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

@lru_cache
def get_settings() -> Settings:
    return Settings()

