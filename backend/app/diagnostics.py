import hashlib, json, re
from pathlib import Path
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from .config import get_settings
from .database import AuditEvent, EvidenceReference, HealthCheck, Incident, IncidentLog, Service, ToolCall

SECRET=re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,]+")
ALLOWED_SUFFIXES={".py",".md",".txt",".json",".toml",".yaml",".yml"}
DENIED_NAMES={".env","id_rsa","id_ed25519","credentials","secrets.json"}

class ToolContext(BaseModel):
    incident_id:int
    execution_id:int

class RepositoryPath(BaseModel):
    path:str=Field(min_length=1,max_length=300,pattern=r"^[A-Za-z0-9_./-]+$")

def root_path()->Path: return Path(get_settings().allowed_repository_root).resolve()
def safe_path(relative:str)->Path:
    if ".." in Path(relative).parts or Path(relative).is_absolute(): raise ValueError("path traversal rejected")
    target=(root_path()/relative).resolve()
    if root_path() not in target.parents and target!=root_path(): raise ValueError("path outside authorized repository")
    if target.is_symlink() or target.name.lower() in DENIED_NAMES or target.suffix.lower() not in ALLOWED_SUFFIXES: raise ValueError("file is not authorized")
    return target

class DiagnosticTools:
    def __init__(self,db:Session,context:ToolContext): self.db,self.context=db,context
    def record(self,name:str,inputs:dict,output:str,success=True):
        clean=SECRET.sub(r"\1=[REDACTED]",output)[:4000]
        self.db.add(ToolCall(execution_id=self.context.execution_id,tool_name=name,input_json=json.dumps(inputs),output_summary=clean,success=success)); self.db.add(AuditEvent(action=f"tool.{name}",resource_type="incident",resource_id=str(self.context.incident_id),details=json.dumps({"success":success}))); self.db.flush(); return clean
    def get_incident_details(self):
        item=self.db.get(Incident,self.context.incident_id)
        if not item: raise ValueError("incident not found")
        out=json.dumps({"id":item.id,"title":item.title,"status":item.status,"service":item.service.name,"detected_at":item.detected_at.isoformat()}); self.record("get_incident_details",{},out); return out
    def get_service_health(self,limit=10):
        item=self.db.get(Incident,self.context.incident_id); limit=max(1,min(limit,50))
        rows=self.db.scalars(select(HealthCheck).where(HealthCheck.service_id==item.service_id).order_by(desc(HealthCheck.id)).limit(limit)).all()
        out=json.dumps([{"status_code":x.status_code,"healthy":x.healthy,"latency_ms":x.latency_ms,"error":x.error,"body":x.response_body} for x in rows]); self.record("get_service_health",{"limit":limit},out); return out
    def get_recent_logs(self,limit=20):
        limit=max(1,min(limit,100)); rows=self.db.scalars(select(IncidentLog).where(IncidentLog.incident_id==self.context.incident_id).order_by(desc(IncidentLog.id)).limit(limit)).all()
        out="\n".join(SECRET.sub(r"\1=[REDACTED]",x.message) for x in rows)[:12000]; self.record("get_recent_logs",{"limit":limit},out); return out
    def get_error_traceback(self):
        logs=self.get_recent_logs(); lines=[x for x in logs.splitlines() if any(k in x for k in ("Error","Exception","Traceback","division"))]; out="\n".join(lines)[:8000]; self.record("get_error_traceback",{},out); return out
    def list_repository_files(self,limit=100):
        files=[str(x.relative_to(root_path())).replace("\\","/") for x in root_path().rglob("*") if x.is_file() and not x.is_symlink() and x.suffix.lower() in ALLOWED_SUFFIXES and x.name.lower() not in DENIED_NAMES][:max(1,min(limit,200))]
        out=json.dumps(files); self.record("list_repository_files",{"limit":limit},out); return files
    def read_repository_file(self,path:str):
        target=safe_path(RepositoryPath(path=path).path); out=SECRET.sub(r"\1=[REDACTED]",target.read_text(encoding="utf-8"))[:20000]; self.record("read_repository_file",{"path":path},out); return out
    def search_repository_code(self,query:str,limit=20):
        if not re.fullmatch(r"[A-Za-z0-9_ .()/-]{2,100}",query): raise ValueError("invalid search query")
        found=[]
        for file in self.list_repository_files():
            for number,line in enumerate(self.read_repository_file(file).splitlines(),1):
                if query.lower() in line.lower(): found.append({"file":file,"line":number,"content":line[:300]})
                if len(found)>=min(limit,50): break
            if len(found)>=min(limit,50): break
        out=json.dumps(found); self.record("search_repository_code",{"query":query,"limit":limit},out); return found
    def get_recent_commits(self,limit=10):
        s=get_settings()
        if not s.github_token or not s.github_repair_repository: out="GitHub integration not configured"
        else:
            try:
                response=httpx.get(f"https://api.github.com/repos/{s.github_repair_repository}/commits",headers={"Authorization":f"Bearer {s.github_token}","Accept":"application/vnd.github+json"},params={"per_page":min(limit,20)},timeout=10); response.raise_for_status(); out=json.dumps([{"sha":x["sha"],"message":x["commit"]["message"][:300]} for x in response.json()])
            except Exception as exc:out=f"GitHub commits unavailable: {type(exc).__name__}"
        self.record("get_recent_commits",{"limit":limit},out); return out
    def get_related_incidents(self,limit=5):
        current=self.db.get(Incident,self.context.incident_id); rows=self.db.scalars(select(Incident).where(Incident.service_id==current.service_id,Incident.id!=current.id).order_by(desc(Incident.id)).limit(min(limit,20))).all(); out=json.dumps([{"id":x.id,"title":x.title,"status":x.status} for x in rows]); self.record("get_related_incidents",{"limit":limit},out); return out
    def persist_evidence(self,kind:str,source:str,content:str)->EvidenceReference:
        clean=SECRET.sub(r"\1=[REDACTED]",content)[:20000]; ref=EvidenceReference(incident_id=self.context.incident_id,evidence_type=kind,source=source,content=clean,content_hash=hashlib.sha256(clean.encode()).hexdigest()); self.db.add(ref); self.db.flush(); return ref
