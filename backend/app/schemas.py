from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

class ServiceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    url: HttpUrl
    health_path: str = Field(default="/health", pattern=r"^/")
    interval_seconds: int = Field(default=15, ge=5, le=3600)

class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int; name: str; url: str; health_path: str; enabled: bool; interval_seconds: int; created_at: datetime

class Approval(BaseModel):
    approved: bool
    actor: str = Field(min_length=2, max_length=100)

class EvidenceItem(BaseModel):
    evidence_id: int | None = None
    source: str
    fact: str

class HypothesisItem(BaseModel):
    statement: str
    evidence_ids: list[int] = []
    confidence: float = Field(ge=0,le=1)

class InvestigationResult(BaseModel):
    model_config=ConfigDict(extra="forbid")
    incident_id: str
    analysis_mode: str
    root_cause: str
    affected_files: list[str]
    affected_functions: list[str]
    evidence: list[EvidenceItem]
    hypotheses: list[HypothesisItem]
    confidence: float = Field(ge=0,le=1)
    recommended_fix: str
    verification_plan: list[str]
    limitations: list[str]

class PatchResult(BaseModel):
    model_config=ConfigDict(extra="forbid")
    file_path: str
    original_content: str
    proposed_content: str
    unified_diff: str
    explanation: str
    regression_test: str

