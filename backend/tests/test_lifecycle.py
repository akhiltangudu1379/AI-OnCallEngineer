from sqlalchemy import select
from fastapi import HTTPException
from app.agents import investigate, propose_repair
from app.database import Incident, Service, SessionLocal
from app.main import approval, auth, dashboard, sandbox_test
from app.monitoring import run_check
from app.schemas import Approval

class FakeResponse:
    def __init__(self,code,body): self.status_code=code; self.text=body
class FakeClient:
    def __init__(self,responses): self.responses=iter(responses)
    def get(self,url): return next(self.responses)

def test_incident_deduplication_and_investigation():
    with SessionLocal() as db:
        service=Service(name="Checkout",url="http://checkout.local",health_path="/health"); db.add(service); db.commit()
        failure='{"detail":"ZeroDivisionError: division by zero"}'
        fake=FakeClient([FakeResponse(500,failure),FakeResponse(500,failure),FakeResponse(500,failure)])
        assert run_check(db,service,fake)[1] is None
        incident=run_check(db,service,fake)[1]; assert incident is not None
        run_check(db,service,fake)
        assert len(db.scalars(select(Incident)).all())==1
        inv=investigate(db,incident)
        assert inv.error_category=="runtime_exception" and inv.confidence>.9
        repair=propose_repair(db,incident)
        try: approval(repair.id,Approval(approved=True,actor="operator"),db); assert False
        except HTTPException as exc: assert exc.status_code==409
        tested=sandbox_test(repair.id,db); assert tested["passed"] is True
        approved=approval(repair.id,Approval(approved=True,actor="operator"),db)
        assert approved["status"]=="approved"

def test_auth_and_metrics():
    try: auth("wrong"); assert False
    except HTTPException as exc: assert exc.status_code==401
    with SessionLocal() as db: assert dashboard(db)["total_services"]==0

