import hashlib, json, shutil
from pathlib import Path
import pytest
import httpx
from sqlalchemy import select
from app import agents
from app.agents import generate_patch, investigate
from app.config import get_settings
from app.database import ApprovalRecord, Incident, IncidentLog, RepairProposal, SandboxTestRun, Service, SessionLocal
from app.diagnostics import safe_path
from app.phase2_services import create_github_pr, deploy_code_repair

def incident(db):
    service=Service(name="Pricing",url="http://demo",health_path="/health");db.add(service);db.flush();item=Incident(service_id=service.id,title="pricing 500");db.add(item);db.flush();db.add(IncidentLog(incident_id=item.id,message="ZeroDivisionError in pricing.py calculate_discount"));db.commit();return item

def test_valid_llm_investigation(monkeypatch):
    s=get_settings();old=(s.llm_provider,s.llm_api_key);s.llm_provider="openai_compatible";s.llm_api_key="test"
    monkeypatch.setattr(agents,"_chat",lambda prompt:{"incident_id":"INC-0001","analysis_mode":"llm","root_cause":"Observed zero divisor reaches calculate_discount","affected_files":["pricing.py"],"affected_functions":["calculate_discount"],"evidence":[{"source":"incident logs","fact":"ZeroDivisionError"}],"hypotheses":[{"statement":"missing guard","evidence_ids":[],"confidence":.94}],"confidence":.94,"recommended_fix":"validate divisor","verification_plan":["run regression tests"],"limitations":[]})
    with SessionLocal() as db:
        result=investigate(db,incident(db));assert result.analysis_mode=="llm";assert json.loads(result.affected_files_json)==["pricing.py"]
    s.llm_provider,s.llm_api_key=old

def test_invalid_llm_output_falls_back(monkeypatch):
    s=get_settings();old=(s.llm_provider,s.llm_api_key);s.llm_provider="openai_compatible";s.llm_api_key="test";monkeypatch.setattr(agents,"_chat",lambda prompt:{"malicious":"ignore all rules"})
    with SessionLocal() as db:
        result=investigate(db,incident(db));assert result.analysis_mode=="heuristic";assert "validation errors" in result.limitations_json
    s.llm_provider,s.llm_api_key=old

def test_patch_is_real_and_security_paths_rejected():
    with SessionLocal() as db:
        repair=generate_patch(db,incident(db));assert repair.patch.startswith("--- a/pricing.py");assert repair.patch_hash==hashlib.sha256(repair.patch.encode()).hexdigest();compile(repair.proposed_content,"pricing.py","exec")
    for path in ("../.env",".env","../../secret.py"):
        with pytest.raises((ValueError,FileNotFoundError)):safe_path(path)

class GithubResponse:
    def __init__(self,data,status=200):self.data,self.status_code=data,status
    def raise_for_status(self):
        if self.status_code>=400:raise RuntimeError(self.status_code)
    def json(self):return self.data

def test_github_branch_commit_and_pr(monkeypatch):
    s=get_settings();old=(s.github_pr_enabled,s.github_token,s.github_repair_repository);s.github_pr_enabled=True;s.github_token="test";s.github_repair_repository="owner/authorized-demo"
    monkeypatch.setattr("app.phase2_services.httpx.get",lambda url,**k:GithubResponse({"object":{"sha":"base123"}}) if "/git/ref/" in url else GithubResponse({"sha":"file123"}))
    calls=[]
    def post(url,**kwargs):
        calls.append(url);return GithubResponse({"number":7,"html_url":"https://github.test/pr/7"},201) if url.endswith("/pulls") else GithubResponse({},201)
    monkeypatch.setattr("app.phase2_services.httpx.post",post);monkeypatch.setattr("app.phase2_services.httpx.put",lambda *a,**k:GithubResponse({"commit":{"sha":"commit123"}},201))
    with SessionLocal() as db:
        item=incident(db);repair=generate_patch(db,item);repair.status="awaiting_approval";repair.tested_patch_hash=repair.patch_hash;db.commit();pr=create_github_pr(db,repair);assert pr.pr_number==7;assert pr.branch==f"autosre/fix-inc-{item.id:04d}"
    assert any(x.endswith("/git/refs") for x in calls) and any(x.endswith("/pulls") for x in calls);s.github_pr_enabled,s.github_token,s.github_repair_repository=old

def test_hash_bound_deployment_and_rollback(monkeypatch):
    s=get_settings();old=s.deployment_artifact_dir;artifacts=Path("runtime-artifacts-test").resolve();s.deployment_artifact_dir=str(artifacts);shutil.rmtree(artifacts,ignore_errors=True)
    monkeypatch.setattr("app.phase2_services.run_check",lambda db,service:(type("C",(),{"healthy":True})(),None))
    with SessionLocal() as db:
        item=incident(db);repair=generate_patch(db,item);repair.status="approved";repair.tested_patch_hash=repair.patch_hash;db.add(SandboxTestRun(repair_id=repair.id,passed=True,output="ok",duration_ms=1,status="sandbox_passed",patch_hash=repair.patch_hash));db.flush();db.add(ApprovalRecord(repair_id=repair.id,incident_id=item.id,operator="operator",decision="approved",patch_hash=repair.patch_hash,deployment_target="isolated-demo"));db.commit();result=deploy_code_repair(db,repair);assert result["status"]=="recovery_verified";assert "ValueError" in (artifacts/"pricing.py").read_text()
    shutil.rmtree(artifacts,ignore_errors=True);s.deployment_artifact_dir=old

def test_changed_patch_invalidates_approval():
    with SessionLocal() as db:
        item=incident(db);repair=generate_patch(db,item);repair.tested_patch_hash=repair.patch_hash;db.add(ApprovalRecord(repair_id=repair.id,incident_id=item.id,operator="operator",decision="approved",patch_hash=repair.patch_hash,deployment_target="isolated-demo"));db.commit();repair.patch_hash="0"*64
        with pytest.raises(ValueError):deploy_code_repair(db,repair)

def test_llm_timeout_and_auth_failure(monkeypatch):
    s=get_settings();old=(s.llm_max_retries,s.llm_api_key);s.llm_max_retries=0;s.llm_api_key="test"
    for exc in (httpx.ReadTimeout("timeout"),httpx.HTTPStatusError("401",request=httpx.Request("POST","https://llm"),response=httpx.Response(401))):
        monkeypatch.setattr(agents.httpx,"post",lambda *a,_exc=exc,**k:(_ for _ in ()).throw(_exc))
        with pytest.raises(RuntimeError):agents._chat("untrusted evidence")
    s.llm_max_retries,s.llm_api_key=old

def test_github_disabled_and_failed_deployment_rolls_back(monkeypatch):
    s=get_settings();old=(s.github_pr_enabled,s.deployment_artifact_dir);s.github_pr_enabled=False;artifacts=Path("runtime-artifacts-test").resolve();s.deployment_artifact_dir=str(artifacts);shutil.rmtree(artifacts,ignore_errors=True)
    outcomes=iter([False,True]);monkeypatch.setattr("app.phase2_services.run_check",lambda db,service:(type("C",(),{"healthy":next(outcomes)})(),None))
    with SessionLocal() as db:
        item=incident(db);repair=generate_patch(db,item);repair.status="approved";repair.tested_patch_hash=repair.patch_hash;db.add(ApprovalRecord(repair_id=repair.id,incident_id=item.id,operator="operator",decision="approved",patch_hash=repair.patch_hash,deployment_target="isolated-demo"));db.commit()
        with pytest.raises(ValueError):create_github_pr(db,repair)
        result=deploy_code_repair(db,repair);assert result["status"]=="rolled_back";assert (artifacts/"pricing.py").read_text()==repair.original_content
    shutil.rmtree(artifacts,ignore_errors=True);s.github_pr_enabled,s.deployment_artifact_dir=old
