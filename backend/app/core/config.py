"""Application configuration. All values come from the environment; nothing secret is hardcoded."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPINTEL_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "RepIntel"
    log_level: str = "INFO"

    # Runtime DB URL uses the restricted application role (NOBYPASSRLS).
    database_url: str = "postgresql+psycopg://repintel_app:repintel_app@localhost:5432/repintel"
    # Migration DB URL uses the owner role. Only Alembic uses it.
    migration_database_url: str = (
        "postgresql+psycopg://repintel_owner:repintel_owner@localhost:5432/repintel"
    )
    redis_url: str = "redis://localhost:6379/0"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    jwt_secret: SecretStr = Field(default=SecretStr(""))
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14

    cookie_secure: bool = True
    cookie_domain: str | None = None
    cors_origins: list[str] = []

    login_rate_limit_per_minute: int = 10
    register_rate_limit_per_hour: int = 20

    @field_validator("jwt_secret")
    @classmethod
    def _secret_strength(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("REPINTEL_JWT_SECRET must be set and at least 32 characters")
        return v

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
