from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./autosre.db"
    autosre_api_key: str = "change-me"
    demo_app_url: str = "http://localhost:9000"
    monitor_interval_seconds: int = 15
    failure_threshold: int = 2
    llm_provider: str = "heuristic"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 60
    llm_max_tool_calls: int = 12
    llm_max_retries: int = 2
    github_token: str = ""
    github_repair_repository: str = ""
    github_base_branch: str = "main"
    github_pr_enabled: bool = False
    allowed_repository_root: str = "./repair-target"
    sandbox_runner_url: str = "http://localhost:9100"
    sandbox_runner_token: str = "change-sandbox-token"
    deployment_artifact_dir: str = "./runtime-artifacts"

@lru_cache
def get_settings() -> Settings:
    return Settings()

