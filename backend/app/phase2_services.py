import base64, hashlib, json, os, time
from pathlib import Path
import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from .config import get_settings
from .database import ApprovalRecord, AuditEvent, DeploymentAttempt, GithubPullRequest, Incident, RecoveryAction, RepairProposal, SandboxTestRun, utcnow
from .monitoring import run_check

def run_sandbox(db:Session,repair:RepairProposal)->SandboxTestRun:
    if repair.repair_type!="code" or not all((repair.original_content,repair.proposed_content,repair.patch_hash)):raise ValueError("complete code repair required")
    repair.status="sandbox_running"; db.commit(); started=time.perf_counter(); s=get_settings()
    try:
        response=httpx.post(s.sandbox_runner_url.rstrip("/")+"/v1/test",headers={"X-Runner-Token":s.sandbox_runner_token},json={"file_path":repair.file_path,"original_content":repair.original_content,"proposed_content":repair.proposed_content,"unified_diff":repair.patch,"patch_hash":repair.patch_hash},timeout=90); response.raise_for_status(); result=response.json(); passed=bool(result["passed"]); status=result["status"]; output=result["output"]; exit_code=result["exit_code"]; duration=result["duration_ms"]
    except Exception as exc:
        passed=False; status="sandbox_failed"; output=f"Sandbox runner unavailable or rejected request: {type(exc).__name__}: {exc}"; exit_code=None; duration=(time.perf_counter()-started)*1000
    run=SandboxTestRun(repair_id=repair.id,passed=passed,output=output,duration_ms=duration,status=status,exit_code=exit_code,patch_hash=repair.patch_hash); db.add(run); repair.status="awaiting_approval" if passed else "sandbox_failed"; repair.tested_patch_hash=repair.patch_hash if passed else None; db.add(AuditEvent(action=status,resource_type="repair",resource_id=str(repair.id),details=json.dumps({"passed":passed,"patch_hash":repair.patch_hash}))); db.commit(); db.refresh(run); return run

def create_github_pr(db:Session,repair:RepairProposal)->GithubPullRequest:
    s=get_settings()
    if not s.github_pr_enabled:raise ValueError("GitHub PR creation is disabled")
    if not s.github_token or not s.github_repair_repository:raise ValueError("GitHub repair credentials are not configured")
    if repair.status not in {"awaiting_approval","approved"} or repair.tested_patch_hash!=repair.patch_hash:raise ValueError("only the exact sandbox-verified patch may be published")
    existing=db.scalar(select(GithubPullRequest).where(GithubPullRequest.repair_id==repair.id))
    if existing:return existing
    repo=s.github_repair_repository.strip("/"); api=f"https://api.github.com/repos/{repo}"; headers={"Authorization":f"Bearer {s.github_token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}; branch=f"autosre/fix-inc-{repair.incident_id:04d}"
    base=httpx.get(f"{api}/git/ref/heads/{s.github_base_branch}",headers=headers,timeout=20); base.raise_for_status(); sha=base.json()["object"]["sha"]
    created=httpx.post(f"{api}/git/refs",headers=headers,json={"ref":f"refs/heads/{branch}","sha":sha},timeout=20)
    if created.status_code not in (201,422):created.raise_for_status()
    current=httpx.get(f"{api}/contents/{repair.file_path}",headers=headers,params={"ref":s.github_base_branch},timeout=20); current.raise_for_status(); file_sha=current.json()["sha"]
    committed=httpx.put(f"{api}/contents/{repair.file_path}",headers=headers,json={"message":f"fix: repair incident INC-{repair.incident_id:04d}","content":base64.b64encode(repair.proposed_content.encode()).decode(),"sha":file_sha,"branch":branch},timeout=20); committed.raise_for_status(); commit_sha=committed.json()["commit"]["sha"]
    incident=db.get(Incident,repair.incident_id); body=f"## AutoSRE verified repair\n\n- Incident: INC-{incident.id:04d}\n- Root cause: {incident.title}\n- Changed file: `{repair.file_path}`\n- Patch SHA-256: `{repair.patch_hash}`\n- Tests: isolated sandbox passed\n- Security: human approval is still required before demo deployment\n- Rollback: restore the previous artifact recorded at deployment."
    pr=httpx.post(f"{api}/pulls",headers=headers,json={"title":f"fix: prevent pricing division-by-zero [INC-{incident.id:04d}]","head":branch,"base":s.github_base_branch,"body":body},timeout=20); pr.raise_for_status(); pdata=pr.json()
    record=GithubPullRequest(repair_id=repair.id,repository=repo,branch=branch,base_branch=s.github_base_branch,commit_sha=commit_sha,pr_number=pdata["number"],pr_url=pdata["html_url"],status="open"); db.add(record); db.add(AuditEvent(action="github.pr_created",resource_type="repair",resource_id=str(repair.id),details=json.dumps({"url":record.pr_url,"commit":commit_sha}))); db.commit(); db.refresh(record); return record

def deploy_code_repair(db:Session,repair:RepairProposal):
    approval=db.scalar(select(ApprovalRecord).where(ApprovalRecord.repair_id==repair.id,ApprovalRecord.decision=="approved").order_by(desc(ApprovalRecord.id)))
    if not approval or approval.patch_hash!=repair.patch_hash or repair.tested_patch_hash!=repair.patch_hash:raise ValueError("approval does not match the tested patch")
    s=get_settings(); artifact_dir=Path(s.deployment_artifact_dir).resolve(); artifact_dir.mkdir(parents=True,exist_ok=True); target=artifact_dir/"pricing.py"; previous=target.read_text(encoding="utf-8") if target.exists() else repair.original_content; previous_hash=hashlib.sha256(previous.encode()).hexdigest() if previous else None
    attempt=DeploymentAttempt(repair_id=repair.id,artifact_hash=repair.patch_hash,previous_artifact_hash=previous_hash,status="deploying"); db.add(attempt); db.flush(); target.write_text(repair.proposed_content,encoding="utf-8")
    incident=db.get(Incident,repair.incident_id); checked,_=run_check(db,incident.service); success=checked.healthy
    if success:
        attempt.status="recovery_verified"; attempt.details="Patched pricing artifact loaded; observed healthy HTTP response"; repair.status="recovery_verified"; incident.status="resolved"; incident.resolved_at=utcnow()
    else:
        target.write_text(previous,encoding="utf-8"); rollback,_=run_check(db,incident.service); attempt.status="rolled_back"; attempt.details=f"Deployment health failed; previous artifact restored; rollback healthy={rollback.healthy}"; repair.status="rolled_back"
    db.add(RecoveryAction(incident_id=incident.id,action="deploy_code_artifact",success=success,details=attempt.details)); db.add(AuditEvent(action=f"deployment.{attempt.status}",resource_type="repair",resource_id=str(repair.id),details=json.dumps({"patch_hash":repair.patch_hash}))); db.commit(); return {"success":success,"status":attempt.status,"details":attempt.details}

def rollback_repair(db:Session,repair:RepairProposal):
    s=get_settings(); target=Path(s.deployment_artifact_dir).resolve()/"pricing.py"
    if not repair.original_content:raise ValueError("original artifact unavailable")
    target.parent.mkdir(parents=True,exist_ok=True); target.write_text(repair.original_content,encoding="utf-8"); repair.status="rolled_back"; db.add(AuditEvent(action="deployment.rolled_back",resource_type="repair",resource_id=str(repair.id))); db.commit(); return {"status":"rolled_back"}
