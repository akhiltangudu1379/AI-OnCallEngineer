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

