import time
import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from .config import get_settings
from .database import AuditEvent, HealthCheck, Incident, IncidentLog, Service, utcnow

def run_check(db: Session, service: Service, client=None) -> tuple[HealthCheck, Incident | None]:
    started = time.perf_counter(); owned = client is None
    client = client or httpx.Client(timeout=5)
    try:
        response = client.get(service.url.rstrip("/") + service.health_path)
        body = response.text[:4000]
        healthy = 200 <= response.status_code < 400
        error = None if healthy else f"HTTP {response.status_code}"
        code = response.status_code
    except Exception as exc:
        body, healthy, code, error = None, False, None, f"{type(exc).__name__}: {exc}"
    finally:
        if owned: client.close()
    check = HealthCheck(service_id=service.id, status_code=code, latency_ms=(time.perf_counter()-started)*1000, healthy=healthy, error=error, response_body=body)
    db.add(check); db.flush()
    active = db.scalar(select(Incident).where(Incident.service_id==service.id, Incident.status!="resolved").order_by(desc(Incident.id)))
    incident = active
    if not healthy:
        recent = db.scalars(select(HealthCheck).where(HealthCheck.service_id==service.id).order_by(desc(HealthCheck.id)).limit(get_settings().failure_threshold)).all()
        if len(recent) >= get_settings().failure_threshold and all(not item.healthy for item in recent) and not active:
            incident = Incident(service_id=service.id, title=f"{service.name} health check failing")
            db.add(incident); db.flush()
            db.add(IncidentLog(incident_id=incident.id, message=f"Health check failed: {error}; response={body or '<none>'}"))
            db.add(AuditEvent(action="incident.created", resource_type="incident", resource_id=str(incident.id)))
    elif active:
        active.status = "resolved"; active.resolved_at = utcnow()
        db.add(AuditEvent(action="incident.auto_resolved", resource_type="incident", resource_id=str(active.id)))
    db.commit(); db.refresh(check)
    return check, incident

