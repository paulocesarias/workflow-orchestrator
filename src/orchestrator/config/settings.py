"""Application configuration using pydantic-settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "workflow-orchestrator"
    debug: bool = False
    log_level: str = "INFO"

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Celery
    celery_broker_url: str = Field(default="redis://localhost:6379/0")
    celery_result_backend: str = Field(default="redis://localhost:6379/0")

    # Rate limiting
    rate_limit_requests: int = Field(default=10, description="Max requests per window")
    rate_limit_window_seconds: int = Field(default=60, description="Rate limit window in seconds")

    # Slack
    slack_signing_secret: str = Field(default="", description="Slack app signing secret")
    slack_bot_token: str = Field(default="", description="Slack bot OAuth token")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
