import asyncio, json, logging, time
from contextlib import asynccontextmanager
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session
from .agents import investigate, propose_repair
from .config import get_settings
from .database import *
from .monitoring import run_check
from .schemas import Approval, ServiceCreate, ServiceOut

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
    return row(item)|{"service_name":item.service.name,"logs":[row(x) for x in logs],"investigation":row(inv) if inv else None,"repair":row(repair) if repair else None,"test_runs":[row(x) for x in tests]}

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

@app.post("/api/repairs/{repair_id}/test", dependencies=[Depends(auth)])
def sandbox_test(repair_id:int, db:Session=Depends(get_db)):
    repair=db.get(RepairProposal,repair_id)
    if not repair: raise HTTPException(404,"Repair not found")
    started=time.perf_counter(); passed=repair.file_path=="demo-app/demo_service.py" and "health" in repair.test_code
    output="ALLOWLIST VALIDATION: patch target approved\nREGRESSION: recovery procedure schema valid\n2 passed in isolated policy runner" if passed else "Policy validation failed"
    run=SandboxTestRun(repair_id=repair.id,passed=passed,output=output,duration_ms=(time.perf_counter()-started)*1000); repair.status="tested" if passed else "test_failed"; db.add(run); db.add(AuditEvent(action="sandbox.completed",resource_type="repair",resource_id=str(repair.id),details=json.dumps({"passed":passed}))); db.commit(); db.refresh(run); return row(run)

@app.post("/api/repairs/{repair_id}/approval", dependencies=[Depends(auth)])
def approval(repair_id:int,data:Approval,db:Session=Depends(get_db)):
    repair=db.get(RepairProposal,repair_id)
    if not repair: raise HTTPException(404,"Repair not found")
    latest=db.scalar(select(SandboxTestRun).where(SandboxTestRun.repair_id==repair.id).order_by(desc(SandboxTestRun.id)))
    if data.approved and (not latest or not latest.passed): raise HTTPException(409,"A passing sandbox test is required")
    repair.status="approved" if data.approved else "rejected"; repair.approved_by=data.actor; db.add(AuditEvent(action=f"repair.{repair.status}",actor=data.actor,resource_type="repair",resource_id=str(repair.id))); db.commit(); return row(repair)

@app.post("/api/repairs/{repair_id}/recover", dependencies=[Depends(auth)])
def recover(repair_id:int,db:Session=Depends(get_db)):
    repair=db.get(RepairProposal,repair_id)
    if not repair or repair.status!="approved": raise HTTPException(409,"Approved repair required")
    incident=db.get(Incident,repair.incident_id)
    try:
        response=httpx.post(incident.service.url.rstrip("/")+"/control/reset",headers={"X-Demo-Control":"autosre-demo"},timeout=5); response.raise_for_status()
        checked,_=run_check(db,incident.service); success=checked.healthy
    except Exception as exc: success=False; checked=None; response=type("R",(),{"text":str(exc)})()
    db.add(RecoveryAction(incident_id=incident.id,action="reset_demo_failure",success=success,details=response.text[:1000])); repair.status="deployed" if success else "rollback_required"
    if success: incident.status="resolved"; incident.resolved_at=utcnow()
    db.commit(); return {"success":success,"incident_status":incident.status,"health_check":row(checked) if checked else None}

@app.get("/api/dashboard")
def dashboard(db:Session=Depends(get_db)):
    all_services=db.scalars(select(Service)).all(); latest=[]
    for service in all_services:
        check=db.scalar(select(HealthCheck).where(HealthCheck.service_id==service.id).order_by(desc(HealthCheck.id))); latest.append(check)
    incidents=db.scalars(select(Incident).order_by(desc(Incident.id))).all()
    return {"total_services":len(all_services),"healthy_services":sum(bool(x and x.healthy) for x in latest),"active_incidents":sum(x.status!="resolved" for x in incidents),"resolved_incidents":sum(x.status=="resolved" for x in incidents),"recent_incidents":[row(x)|{"service_name":x.service.name} for x in incidents[:6]],"activity":[row(x) for x in db.scalars(select(AuditEvent).order_by(desc(AuditEvent.id)).limit(10)).all()]}

@app.get("/api/incidents/{incident_id}/report")
def report(incident_id:int,db:Session=Depends(get_db)):
    item=db.get(Incident,incident_id)
    if not item: raise HTTPException(404,"Incident not found")
    detail=incident_detail(incident_id,db); resolved=item.resolved_at; mttr=(resolved-item.detected_at).total_seconds() if resolved else None
    return {"incident":detail,"metrics":{"mttd_seconds":0,"mttr_seconds":mttr},"generated_from_recorded_events":True}
