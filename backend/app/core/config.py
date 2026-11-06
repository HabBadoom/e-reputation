"""Application configuration. All values come from the environment; nothing secret is hardcoded."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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
    email_rate_limit_per_hour: int = 5  # verification resends / reset requests per address

    # Fernet key(s) for encrypting secrets at rest (MFA seeds). Comma-separated: the first
    # encrypts, all decrypt, which allows rotation. Generate with
    # python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
    encryption_keys: SecretStr = Field(default=SecretStr(""))

    # Public URL of the web app, used to build links in emails.
    app_base_url: str = "http://localhost:3000"

    # Email delivery: "brevo" in real environments, "console" logs in dev, "memory" for tests.
    email_provider: Literal["brevo", "console", "memory"] = "console"
    brevo_api_key: SecretStr = Field(default=SecretStr(""))
    brevo_api_url: str = "https://api.brevo.com/v3/smtp/email"
    email_from_address: str = "no-reply@example.com"
    email_from_name: str = "RepIntel"

    email_verify_ttl_seconds: int = 60 * 60 * 48
    password_reset_ttl_seconds: int = 60 * 60
    mfa_challenge_ttl_seconds: int = 5 * 60

    # Plan given to new organizations; empty = the plan flagged is_default in the DB.
    default_plan: str = ""
    trial_days: int = 14

    @field_validator("jwt_secret")
    @classmethod
    def _secret_strength(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("REPINTEL_JWT_SECRET must be set and at least 32 characters")
        return v

    @field_validator("encryption_keys")
    @classmethod
    def _valid_fernet_keys(cls, v: SecretStr) -> SecretStr:
        from cryptography.fernet import Fernet

        keys = [k.strip() for k in v.get_secret_value().split(",") if k.strip()]
        if not keys:
            raise ValueError("REPINTEL_ENCRYPTION_KEYS must contain at least one Fernet key")
        for k in keys:
            try:
                Fernet(k.encode())
            except ValueError as e:
                raise ValueError("REPINTEL_ENCRYPTION_KEYS contains an invalid Fernet key") from e
        return v

    @model_validator(mode="after")
    def _email_config(self) -> "Settings":
        if self.email_provider == "brevo" and not self.brevo_api_key.get_secret_value():
            raise ValueError("REPINTEL_BREVO_API_KEY is required when EMAIL_PROVIDER=brevo")
        if self.env in ("staging", "production") and self.email_provider != "brevo":
            raise ValueError("A real email provider is required in staging/production")
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
