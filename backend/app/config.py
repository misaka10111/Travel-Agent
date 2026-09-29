from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "TravelAgent API"
    app_version: str = "0.1.0"
    debug: bool = False
    api_prefix: str = "/api"
    database_url: str = "sqlite:///./travelagent.db"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    amap_web_service_key: SecretStr | None = None
    google_maps_api_key: SecretStr | None = None
    map_request_timeout_seconds: float = Field(default=15, gt=0, le=60)
    map_probe_max_calls: int = Field(default=12, ge=1, le=30)
    planning_api_key: SecretStr | None = None
    planning_base_url: str = "https://api.deepseek.com"
    planning_model: str = "deepseek-flash"
    planning_max_output_tokens: int = Field(default=4096, ge=512, le=8192)
    planning_thinking_mode: Literal["enabled", "disabled"] = "disabled"
    planning_model_timeout_seconds: float = Field(default=45, gt=0, le=90)
    planning_model_call_limit: int = Field(default=20, ge=1, le=50)
    planning_map_call_limit: int = Field(default=12, ge=1, le=30)
    planning_step_limit: int = Field(default=12, ge=1, le=30)
    planning_run_timeout_seconds: float = Field(default=180, ge=1, le=600)


@lru_cache
def get_settings() -> Settings:
    return Settings()

