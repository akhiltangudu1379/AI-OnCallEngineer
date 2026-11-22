from app.database import Incident, RepairProposal, SandboxTestRun, Service, SessionLocal
from app.main import recover

def test_approved_recovery_verifies_health(monkeypatch):
    class Response:
        text='{"mode":"healthy"}'
        def raise_for_status(self): pass
    monkeypatch.setattr("app.main.httpx.post",lambda *a,**k:Response())
    monkeypatch.setattr("app.monitoring.httpx.Client.get",lambda *a,**k:type("R",(),{"status_code":200,"text":'{"status":"healthy"}'})())
    with SessionLocal() as db:
        s=Service(name="Demo",url="http://demo",health_path="/health"); db.add(s); db.flush()
        i=Incident(service_id=s.id,title="down",status="repair_proposed"); db.add(i); db.flush()
        r=RepairProposal(incident_id=i.id,status="approved",file_path="demo-app/demo_service.py",patch="safe",test_code="health"); db.add(r); db.flush()
        db.add(SandboxTestRun(repair_id=r.id,passed=True,output="ok",duration_ms=1)); db.commit()
        result=recover(r.id,db)
        assert result["success"] is True and result["incident_status"]=="resolved"

