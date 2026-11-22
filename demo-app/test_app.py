from fastapi import HTTPException
from demo_service import Fault, health, inject, reset

def test_fault_and_recovery():
    inject(Fault(mode="division_by_zero"),"autosre-demo")
    try: health(); assert False
    except HTTPException as exc: assert exc.status_code==500 and "ZeroDivisionError" in exc.detail
    reset("autosre-demo")
    assert health()["status"]=="healthy"
