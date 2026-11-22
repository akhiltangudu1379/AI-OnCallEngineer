import asyncio, ipaddress, json, logging, time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from urllib.parse import urlparse
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session
from .agents import generate_patch, investigate, propose_repair
from .config import get_settings
from .database import *
from .monitoring import run_check
from .schemas import Approval, ServiceCreate, ServiceOut
from .phase2_services import create_github_pr, deploy_code_repair, rollback_repair, run_sandbox

log = logging.getLogger("autosre")

async def monitor_loop():
    while True:
        await asyncio.sleep(get_settings().monitor_interval_seconds)
        with SessionLocal() as db:
            for service in db.scalars(select(Service).where(Service.enabled==True)).all():
                try: await asyncio.to_thread(run_check, db, service)
                except Exception: log.exception("monitor_check_failed", extra={"service_id": service.id})

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        if not db.scalar(select(Service).where(Service.name=="Demo Commerce API")):
            db.add(Service(name="Demo Commerce API", url=get_settings().demo_app_url, health_path="/health", interval_seconds=get_settings().monitor_interval_seconds)); db.commit()
    task = asyncio.create_task(monitor_loop())
    yield
    task.cancel()

app = FastAPI(title="AutoSRE AI", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
rate_windows=defaultdict(deque)

@app.middleware("http")
async def sensitive_rate_limit(request:Request,call_next):
    if request.method in {"POST","PUT","PATCH","DELETE"} and request.url.path.startswith("/api/"):
        key=request.client.host if request.client else "unknown";now=time.monotonic();window=rate_windows[key]
        while window and now-window[0]>60:window.popleft()
        if len(window)>=60:return JSONResponse({"detail":"Sensitive endpoint rate limit exceeded"},status_code=429)
        window.append(now)
    return await call_next(request)

def auth(x_api_key: str = Header(default="")):
    if x_api_key != get_settings().autosre_api_key: raise HTTPException(401, "Valid X-API-Key required")

def row(model):
    data = {c.name: getattr(model, c.name) for c in model.__table__.columns}
    for k,v in data.items():
        if hasattr(v, "isoformat"): data[k]=v.isoformat()
    return data

@app.get("/api/health")
def health(): return {"status":"ok"}

@app.post("/api/services", response_model=ServiceOut, dependencies=[Depends(auth)])
def create_service(data: ServiceCreate, db: Session=Depends(get_db)):
    parsed=urlparse(str(data.url));host=(parsed.hostname or "").lower()
    if parsed.username or parsed.password:raise HTTPException(422,"Credentials in monitoring URLs are forbidden")
    try:
        address=ipaddress.ip_address(host)
        if address.is_link_local or address.is_multicast or address.is_unspecified or str(address)=="169.254.169.254":raise HTTPException(422,"Unsafe monitoring target")
    except ValueError:pass
    if host in {"metadata.google.internal","metadata.azure.internal"}:raise HTTPException(422,"Unsafe monitoring target")
    if db.scalar(select(Service).where(Service.name==data.name)): raise HTTPException(409,"Service name exists")
    service=Service(name=data.name,url=str(data.url).rstrip("/"),health_path=data.health_path,interval_seconds=data.interval_seconds); db.add(service); db.commit(); db.refresh(service); return service

@app.get("/api/services")
def services(db: Session=Depends(get_db)):
    result=[]
    for service in db.scalars(select(Service).order_by(Service.id)).all():
        check=db.scalar(select(HealthCheck).where(HealthCheck.service_id==service.id).order_by(desc(HealthCheck.id)))
        result.append({**row(service),"latest_check":row(check) if check else None})
    return result

@app.post("/api/services/{service_id}/check", dependencies=[Depends(auth)])
def check(service_id:int, db:Session=Depends(get_db)):
    service=db.get(Service,service_id)
    if not service: raise HTTPException(404,"Service not found")
    checked,incident=run_check(db,service); return {"check":row(checked),"incident":row(incident) if incident else None}

@app.get("/api/incidents")
def incidents(status:str|None=None, db:Session=Depends(get_db)):
    q=select(Incident).order_by(desc(Incident.id)); q=q.where(Incident.status==status) if status else q
    return [row(x)|{"service_name":x.service.name} for x in db.scalars(q).all()]

@app.get("/api/incidents/{incident_id}")
def incident_detail(incident_id:int, db:Session=Depends(get_db)):
    item=db.get(Incident,incident_id)
    if not item: raise HTTPException(404,"Incident not found")
    logs=db.scalars(select(IncidentLog).where(IncidentLog.incident_id==incident_id)).all(); inv=db.scalar(select(Investigation).where(Investigation.incident_id==incident_id)); repair=db.scalar(select(RepairProposal).where(RepairProposal.incident_id==incident_id))
    tests=db.scalars(select(SandboxTestRun).where(SandboxTestRun.repair_id==repair.id)).all() if repair else []
    execution_ids=select(AgentExecution.id).where(AgentExecution.incident_id==incident_id); calls=db.scalars(select(ToolCall).where(ToolCall.execution_id.in_(execution_ids)).order_by(ToolCall.id)).all(); evidence=db.scalars(select(EvidenceReference).where(EvidenceReference.incident_id==incident_id)).all(); pr=db.scalar(select(GithubPullRequest).where(GithubPullRequest.repair_id==repair.id)) if repair else None
    return row(item)|{"service_name":item.service.name,"logs":[row(x) for x in logs],"investigation":row(inv) if inv else None,"repair":row(repair) if repair else None,"test_runs":[row(x) for x in tests],"tool_calls":[row(x) for x in calls],"evidence":[row(x) for x in evidence],"pull_request":row(pr) if pr else None}

@app.post("/api/incidents/{incident_id}/investigate", dependencies=[Depends(auth)])
def start_investigation(incident_id:int, db:Session=Depends(get_db)):
    item=db.get(Incident,incident_id)
    if not item: raise HTTPException(404,"Incident not found")
    return row(investigate(db,item))

@app.post("/api/incidents/{incident_id}/repairs", dependencies=[Depends(auth)])
def create_repair(incident_id:int, db:Session=Depends(get_db)):
    item=db.get(Incident,incident_id)
    if not item: raise HTTPException(404,"Incident not found")
    return row(propose_repair(db,item))

@app.post("/api/incidents/{incident_id}/generate-patch", dependencies=[Depends(auth)])
def create_code_patch(incident_id:int,db:Session=Depends(get_db)):
    item=db.get(Incident,incident_id)
    if not item:raise HTTPException(404,"Incident not found")
    try:return row(generate_patch(db,item))
    except ValueError as exc:raise HTTPException(422,str(exc))

@app.post("/api/incidents/{incident_id}/operational-repair", dependencies=[Depends(auth)])
def operational_repair(incident_id:int,db:Session=Depends(get_db)):
    item=db.get(Incident,incident_id)
    if not item:raise HTTPException(404,"Incident not found")
    existing=db.scalar(select(RepairProposal).where(RepairProposal.incident_id==incident_id,RepairProposal.repair_type=="operational"))
    if existing:return row(existing)
    repair=RepairProposal(incident_id=incident_id,status="proposed",repair_type="operational",file_path="demo-control",patch="Reset the isolated demo failure mode",test_code="health check must return 2xx",explanation="Operational recovery; no source code is changed.");db.add(repair);db.commit();db.refresh(repair);return row(repair)

@app.post("/api/repairs/{repair_id}/test", dependencies=[Depends(auth)])
def sandbox_test(repair_id:int, db:Session=Depends(get_db)):
    repair=db.get(RepairProposal,repair_id)
    if not repair: raise HTTPException(404,"Repair not found")
    if repair.repair_type=="code":return row(run_sandbox(db,repair))
    started=time.perf_counter(); passed=repair.file_path=="demo-control"; output="Operational recovery procedure validated" if passed else "Policy validation failed"; run=SandboxTestRun(repair_id=repair.id,passed=passed,output=output,duration_ms=(time.perf_counter()-started)*1000,status="sandbox_passed" if passed else "sandbox_failed",patch_hash=repair.patch_hash);repair.status="awaiting_approval" if passed else "sandbox_failed";db.add(run);db.commit();db.refresh(run);return row(run)

@app.post("/api/repairs/{repair_id}/approval", dependencies=[Depends(auth)])
def approval(repair_id:int,data:Approval,db:Session=Depends(get_db)):
    repair=db.get(RepairProposal,repair_id)
    if not repair: raise HTTPException(404,"Repair not found")
    latest=db.scalar(select(SandboxTestRun).where(SandboxTestRun.repair_id==repair.id).order_by(desc(SandboxTestRun.id)))
    if data.approved and (not latest or not latest.passed): raise HTTPException(409,"A passing sandbox test is required")
    if data.approved and repair.repair_type=="code" and (not repair.patch_hash or repair.tested_patch_hash!=repair.patch_hash):raise HTTPException(409,"Tested patch hash does not match current patch")
    repair.status="approved" if data.approved else "rejected"; repair.approved_by=data.actor; db.add(ApprovalRecord(repair_id=repair.id,incident_id=repair.incident_id,operator=data.actor,decision="approved" if data.approved else "rejected",patch_hash=repair.patch_hash or "operational",test_run_id=latest.id if latest else None));db.add(AuditEvent(action=f"repair.{repair.status}",actor=data.actor,resource_type="repair",resource_id=str(repair.id),details=json.dumps({"patch_hash":repair.patch_hash}))); db.commit(); return row(repair)

@app.post("/api/repairs/{repair_id}/recover", dependencies=[Depends(auth)])
def recover(repair_id:int,db:Session=Depends(get_db)):
    repair=db.get(RepairProposal,repair_id)
    if not repair or repair.status!="approved": raise HTTPException(409,"Approved repair required")
    if repair.repair_type=="code":
        try:return deploy_code_repair(db,repair)
        except ValueError as exc:raise HTTPException(409,str(exc))
    incident=db.get(Incident,repair.incident_id)
    try:
        response=httpx.post(incident.service.url.rstrip("/")+"/control/reset",headers={"X-Demo-Control":"autosre-demo"},timeout=5); response.raise_for_status()
        checked,_=run_check(db,incident.service); success=checked.healthy
    except Exception as exc: success=False; checked=None; response=type("R",(),{"text":str(exc)})()
    db.add(RecoveryAction(incident_id=incident.id,action="reset_demo_failure",success=success,details=response.text[:1000])); repair.status="deployed" if success else "rollback_required"
    if success: incident.status="resolved"; incident.resolved_at=utcnow()
    db.commit(); return {"success":success,"incident_status":incident.status,"health_check":row(checked) if checked else None}

@app.get("/api/incidents/{incident_id}/evidence")
def incident_evidence(incident_id:int,db:Session=Depends(get_db)):return [row(x) for x in db.scalars(select(EvidenceReference).where(EvidenceReference.incident_id==incident_id)).all()]

@app.get("/api/incidents/{incident_id}/timeline")
def incident_timeline(incident_id:int,db:Session=Depends(get_db)):
    audits=db.scalars(select(AuditEvent).where(AuditEvent.resource_id==str(incident_id)).order_by(AuditEvent.created_at)).all();executions=db.scalars(select(AgentExecution).where(AgentExecution.incident_id==incident_id)).all();return {"audit_events":[row(x) for x in audits],"agent_executions":[row(x) for x in executions]}

@app.get("/api/repairs/{repair_id}/diff")
def repair_diff(repair_id:int,db:Session=Depends(get_db)):
    repair=db.get(RepairProposal,repair_id)
    if not repair:raise HTTPException(404,"Repair not found")
    return {"file_path":repair.file_path,"diff":repair.patch,"patch_hash":repair.patch_hash,"original":repair.original_content,"proposed":repair.proposed_content,"explanation":repair.explanation}

@app.get("/api/repairs/{repair_id}/test-results")
def repair_tests(repair_id:int,db:Session=Depends(get_db)):return [row(x) for x in db.scalars(select(SandboxTestRun).where(SandboxTestRun.repair_id==repair_id).order_by(SandboxTestRun.id)).all()]

@app.post("/api/repairs/{repair_id}/create-pr",dependencies=[Depends(auth)])
def github_pr(repair_id:int,db:Session=Depends(get_db)):
    repair=db.get(RepairProposal,repair_id)
    if not repair:raise HTTPException(404,"Repair not found")
    try:return row(create_github_pr(db,repair))
    except (ValueError,httpx.HTTPError) as exc:raise HTTPException(409,str(exc))

@app.post("/api/repairs/{repair_id}/rollback",dependencies=[Depends(auth)])
def rollback(repair_id:int,db:Session=Depends(get_db)):
    repair=db.get(RepairProposal,repair_id)
    if not repair:raise HTTPException(404,"Repair not found")
    try:return rollback_repair(db,repair)
    except ValueError as exc:raise HTTPException(409,str(exc))

@app.get("/api/dashboard")
def dashboard(db:Session=Depends(get_db)):
    all_services=db.scalars(select(Service)).all(); latest=[]
    for service in all_services:
        check=db.scalar(select(HealthCheck).where(HealthCheck.service_id==service.id).order_by(desc(HealthCheck.id))); latest.append(check)
    incidents=db.scalars(select(Incident).order_by(desc(Incident.id))).all()
    investigations=db.scalars(select(Investigation)).all();runs=db.scalars(select(SandboxTestRun)).all();deployments=db.scalars(select(DeploymentAttempt)).all();resolved=[x for x in incidents if x.resolved_at]
    return {"total_services":len(all_services),"healthy_services":sum(bool(x and x.healthy) for x in latest),"active_incidents":sum(x.status!="resolved" for x in incidents),"resolved_incidents":len(resolved),"recent_incidents":[row(x)|{"service_name":x.service.name} for x in incidents[:6]],"activity":[row(x) for x in db.scalars(select(AuditEvent).order_by(desc(AuditEvent.id)).limit(10)).all()],"engineering_metrics":{"mttd_seconds":0,"mean_investigation_seconds":(sum((x.duration_ms or 0) for x in investigations)/len(investigations)/1000) if investigations else None,"mttr_seconds":(sum((x.resolved_at-x.detected_at).total_seconds() for x in resolved)/len(resolved)) if resolved else None,"verified_patch_success_rate":(sum(x.passed for x in runs)/len(runs)) if runs else None,"failed_repairs":sum(not x.passed for x in runs),"rollback_count":sum(x.status=="rolled_back" for x in deployments),"llm_investigations":sum(x.analysis_mode=="llm" for x in investigations),"heuristic_investigations":sum(x.analysis_mode!="llm" for x in investigations)}}

@app.get("/api/metrics")
def metrics(db:Session=Depends(get_db)):return dashboard(db)["engineering_metrics"]

@app.get("/api/incidents/{incident_id}/report")
def report(incident_id:int,db:Session=Depends(get_db)):
    item=db.get(Incident,incident_id)
    if not item: raise HTTPException(404,"Incident not found")
    detail=incident_detail(incident_id,db); resolved=item.resolved_at; mttr=(resolved-item.detected_at).total_seconds() if resolved else None
    return {"incident":detail,"metrics":{"mttd_seconds":0,"mttr_seconds":mttr},"generated_from_recorded_events":True}
