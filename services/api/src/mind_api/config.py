from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-insecure-secret-change-me-0123456789abcdef"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), env_prefix="MIND_", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/mind"
    redis_url: str | None = "redis://localhost:6379/0"

    secret_key: str = _DEV_SECRET
    encryption_key: str | None = Field(None, description="Fernet key for provider credentials; derived from secret_key in dev")
    access_token_minutes: int = 30
    refresh_token_days: int = 30
    allow_registration: bool = True

    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    public_api_url: str = "http://localhost:8000"
    preview_public_url: str = "http://localhost:8100"
    cookie_secure: bool = False

    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: Path = Path("data/storage")
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    max_upload_mb: int = 50
    sandbox_enabled: bool = True
    preview_idle_minutes: int = 30
    max_previews_per_org: int = 3

    rate_limit_per_minute: int = 120
    auth_rate_limit_per_minute: int = 10

    worker_poll_seconds: float = 1.0
    job_lease_seconds: int = 300

    @model_validator(mode="after")
    def _prod_guard(self) -> Settings:
        if self.env == "prod":
            if self.secret_key == _DEV_SECRET or len(self.secret_key) < 32:
                raise ValueError("MIND_SECRET_KEY must be set to a random value of at least 32 characters in production")
            if not self.encryption_key:
                raise ValueError("MIND_ENCRYPTION_KEY must be set in production")
            if not self.cookie_secure:
                raise ValueError("MIND_COOKIE_SECURE must be true in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
