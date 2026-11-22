from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool
from .config import get_settings

def utcnow(): return datetime.now(timezone.utc)

class Base(DeclarativeBase): pass

class Service(Base):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    url: Mapped[str] = mapped_column(String(500))
    health_path: Mapped[str] = mapped_column(String(200), default="/health")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=15)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checks = relationship("HealthCheck", back_populates="service", cascade="all,delete")

class HealthCheck(Base):
    __tablename__ = "health_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    healthy: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    service = relationship("Service", back_populates="checks")

class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(20), default="critical")
    status: Mapped[str] = mapped_column(String(30), default="open")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    service = relationship("Service")

class IncidentLog(Base):
    __tablename__ = "incident_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(20), default="ERROR")
    message: Mapped[str] = mapped_column(Text)

class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), unique=True)
    error_category: Mapped[str] = mapped_column(String(100))
    root_cause: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    proposed_fix: Mapped[str] = mapped_column(Text)
    verification_strategy: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    analysis_mode: Mapped[str] = mapped_column(String(30), default="heuristic")
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    affected_files_json: Mapped[str] = mapped_column(Text, default="[]")
    affected_functions_json: Mapped[str] = mapped_column(Text, default="[]")
    limitations_json: Mapped[str] = mapped_column(Text, default="[]")

class RepairProposal(Base):
    __tablename__ = "repair_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    status: Mapped[str] = mapped_column(String(30), default="proposed")
    file_path: Mapped[str] = mapped_column(String(300))
    patch: Mapped[str] = mapped_column(Text)
    test_code: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    repair_type: Mapped[str] = mapped_column(String(30), default="operational")
    original_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    patch_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tested_patch_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

class SandboxTestRun(Base):
    __tablename__ = "sandbox_test_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    repair_id: Mapped[int] = mapped_column(ForeignKey("repair_proposals.id"))
    passed: Mapped[bool] = mapped_column(Boolean)
    output: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(40), default="sandbox_running")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    patch_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

class RecoveryAction(Base):
    __tablename__ = "recovery_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    action: Mapped[str] = mapped_column(String(100))
    success: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(100))
    actor: Mapped[str] = mapped_column(String(100), default="system")
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str] = mapped_column(String(50))
    details: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class AgentExecution(Base):
    __tablename__="agent_executions"
    id: Mapped[int]=mapped_column(primary_key=True)
    incident_id: Mapped[int]=mapped_column(ForeignKey("incidents.id"))
    execution_type: Mapped[str]=mapped_column(String(50))
    status: Mapped[str]=mapped_column(String(40),default="running")
    provider: Mapped[str | None]=mapped_column(String(100),nullable=True)
    model: Mapped[str | None]=mapped_column(String(100),nullable=True)
    started_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    completed_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    error: Mapped[str | None]=mapped_column(Text,nullable=True)

class ToolCall(Base):
    __tablename__="tool_calls"
    id: Mapped[int]=mapped_column(primary_key=True)
    execution_id: Mapped[int]=mapped_column(ForeignKey("agent_executions.id"))
    tool_name: Mapped[str]=mapped_column(String(100))
    input_json: Mapped[str]=mapped_column(Text,default="{}")
    output_summary: Mapped[str]=mapped_column(Text,default="")
    success: Mapped[bool]=mapped_column(Boolean,default=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)

class EvidenceReference(Base):
    __tablename__="evidence_references"
    id: Mapped[int]=mapped_column(primary_key=True)
    incident_id: Mapped[int]=mapped_column(ForeignKey("incidents.id"))
    evidence_type: Mapped[str]=mapped_column(String(50))
    source: Mapped[str]=mapped_column(String(300))
    content: Mapped[str]=mapped_column(Text)
    content_hash: Mapped[str]=mapped_column(String(64))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)

class InvestigationHypothesis(Base):
    __tablename__="investigation_hypotheses"
    id: Mapped[int]=mapped_column(primary_key=True)
    investigation_id: Mapped[int]=mapped_column(ForeignKey("investigations.id"))
    statement: Mapped[str]=mapped_column(Text)
    evidence_ids_json: Mapped[str]=mapped_column(Text,default="[]")
    confidence: Mapped[float]=mapped_column(Float)

class ApprovalRecord(Base):
    __tablename__="approval_records"
    id: Mapped[int]=mapped_column(primary_key=True)
    repair_id: Mapped[int]=mapped_column(ForeignKey("repair_proposals.id"))
    incident_id: Mapped[int]=mapped_column(ForeignKey("incidents.id"))
    operator: Mapped[str]=mapped_column(String(100))
    decision: Mapped[str]=mapped_column(String(20))
    patch_hash: Mapped[str]=mapped_column(String(64))
    test_run_id: Mapped[int | None]=mapped_column(ForeignKey("sandbox_test_runs.id"),nullable=True)
    deployment_target: Mapped[str]=mapped_column(String(200),default="isolated-demo")
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)

class GithubPullRequest(Base):
    __tablename__="github_pull_requests"
    id: Mapped[int]=mapped_column(primary_key=True)
    repair_id: Mapped[int]=mapped_column(ForeignKey("repair_proposals.id"),unique=True)
    repository: Mapped[str]=mapped_column(String(200))
    branch: Mapped[str]=mapped_column(String(200))
    base_branch: Mapped[str]=mapped_column(String(200))
    commit_sha: Mapped[str | None]=mapped_column(String(64),nullable=True)
    pr_number: Mapped[int | None]=mapped_column(Integer,nullable=True)
    pr_url: Mapped[str | None]=mapped_column(String(500),nullable=True)
    status: Mapped[str]=mapped_column(String(30),default="pending")
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)

class DeploymentAttempt(Base):
    __tablename__="deployment_attempts"
    id: Mapped[int]=mapped_column(primary_key=True)
    repair_id: Mapped[int]=mapped_column(ForeignKey("repair_proposals.id"))
    artifact_hash: Mapped[str]=mapped_column(String(64))
    previous_artifact_hash: Mapped[str | None]=mapped_column(String(64),nullable=True)
    status: Mapped[str]=mapped_column(String(40))
    details: Mapped[str]=mapped_column(Text,default="")
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)

settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine_options = {"poolclass": StaticPool} if settings.database_url == "sqlite://" else {}
engine = create_engine(settings.database_url, connect_args=connect_args, **engine_options)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

def _migrate_sqlite():
    if not settings.database_url.startswith("sqlite"): return
    additions={
      "investigations":{"analysis_mode":"VARCHAR(30) DEFAULT 'heuristic'","provider":"VARCHAR(100)","model":"VARCHAR(100)","duration_ms":"FLOAT","affected_files_json":"TEXT DEFAULT '[]'","affected_functions_json":"TEXT DEFAULT '[]'","limitations_json":"TEXT DEFAULT '[]'"},
      "repair_proposals":{"repair_type":"VARCHAR(30) DEFAULT 'operational'","original_content":"TEXT","proposed_content":"TEXT","patch_hash":"VARCHAR(64)","tested_patch_hash":"VARCHAR(64)","explanation":"TEXT"},
      "sandbox_test_runs":{"status":"VARCHAR(40) DEFAULT 'sandbox_running'","exit_code":"INTEGER","patch_hash":"VARCHAR(64)"}}
    with engine.begin() as conn:
        for table,columns in additions.items():
            existing={x[1] for x in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name,ddl in columns.items():
                if name not in existing: conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
def init_db():
    Base.metadata.create_all(engine); _migrate_sqlite()
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

