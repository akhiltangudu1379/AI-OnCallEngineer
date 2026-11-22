"""Intentionally breakable service used only by the AutoSRE demo."""
import json, logging, time
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format='{"time":"%(asctime)s","level":"%(levelname)s","message":"%(message)s"}')
log=logging.getLogger("demo")
app=FastAPI(title="Breakable Commerce Demo")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173"],allow_methods=["*"],allow_headers=["*"])
state={"mode":"healthy","changed_at":time.time()}
MODES={"healthy","division_by_zero","missing_config","database_failure","http_500","latency"}

def error_event(message:str):
    print(json.dumps({"time":time.time(),"level":"ERROR","message":message}),flush=True)

class Fault(BaseModel): mode:str

def diagnose():
    mode=state["mode"]
    if mode=="division_by_zero":
        try: _=10/0
        except ZeroDivisionError:
            error_event("division-by-zero in pricing calculation; ZeroDivisionError at pricing.py:calculate_discount")
            return False,"ZeroDivisionError: division by zero in pricing.py:calculate_discount"
    if mode=="missing_config": return False,"MissingConfiguration: PAYMENT_API_KEY is not configured"
    if mode=="database_failure": return False,"DatabaseConnectionError: connection refused at demo-db:5432"
    if mode=="http_500": return False,"InternalServerError: injected HTTP 500 response"
    return True,"ready"

@app.get("/health")
def health():
    if state["mode"]=="latency":
        error_event("artificial latency fault active: 3 seconds"); time.sleep(3)
    ok,detail=diagnose()
    if not ok:
        error_event(f"health check failed: {detail}"); raise HTTPException(500,detail)
    return {"status":"healthy","mode":state["mode"],"uptime_seconds":round(time.time()-state["changed_at"],2)}

@app.get("/api/price")
def price():
    ok,detail=diagnose()
    if not ok: raise HTTPException(500,detail)
    return {"subtotal":100,"discount":10,"total":90}

@app.get("/control/status")
def control_status(): return state

@app.post("/control/fault")
def inject(payload:Fault,x_demo_control:str=Header(default="")):
    if x_demo_control!="autosre-demo": raise HTTPException(401,"control token required")
    if payload.mode not in MODES: raise HTTPException(422,f"mode must be one of {sorted(MODES)}")
    state.update(mode=payload.mode,changed_at=time.time()); log.warning("fault mode changed to %s",payload.mode); return state

@app.post("/control/reset")
def reset(x_demo_control:str=Header(default="")):
    if x_demo_control!="autosre-demo": raise HTTPException(401,"control token required")
    state.update(mode="healthy",changed_at=time.time()); log.info("service restored to healthy mode"); return state

