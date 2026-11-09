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

    # Search provider (Serper.dev). Empty key = search connector disabled.
    serper_api_key: SecretStr = Field(default=SecretStr(""))
    serper_base_url: str = "https://google.serper.dev"
    search_country: str = "dz"               # Serper `gl`
    search_results_per_query: int = 20       # Serper `num`
    search_endpoints: list[Literal["search", "news"]] = ["news", "search"]
    max_search_queries_per_run: int = 30

    # Fetching third-party pages / feeds.
    crawler_user_agent: str = "RepIntelBot/0.1 (+https://example.com/bot)"
    crawler_robots_token: str = "RepIntelBot"  # noqa: S105 — robots.txt user-agent, not a secret
    fetch_timeout_seconds: float = 10.0
    fetch_max_bytes: int = 2 * 1024 * 1024
    verify_pages: bool = True                # check search hits against the real page
    max_page_fetches_per_run: int = 25
    max_feed_items_per_source: int = 200

    # Language analysis (OpenAI). Empty key or empty model list = AI analysis disabled
    # (the deterministic linguistic layer still runs). Model ids are never hardcoded.
    openai_api_key: SecretStr = Field(default=SecretStr(""))
    openai_base_url: str = "https://api.openai.com/v1"
    nlp_models: list[str] = []          # e.g. '["<model-a>","<model-b>"]': cycled over annotators
    nlp_arbiter_model: str = ""         # default: first of nlp_models
    nlp_annotators: int = Field(default=3, ge=2, le=5)
    nlp_batch_size: int = 25
    nlp_min_macro_f1: float = 0.70      # benchmark thresholds before a task is shown as validated
    nlp_min_coverage: float = 0.60
    nlp_min_gold_items: int = 100
    nlp_benchmark_max_items: int = 500

    # Reputation analytics. Days are counted in this timezone (Algeria: UTC+1, no DST).
    analytics_timezone: str = "Africa/Algiers"
    reputation_min_sample: int = 10
    alert_eval_every_minutes: int = 15
    trend_baseline_days: int = 28        # history used as the "usual" level
    crisis_quiet_hours: int = 48         # a crisis is resolved after this long undetected
    alerts_per_rule_per_run: int = 20    # flood guard
    report_render_timeout_seconds: int = 60
    # Browser fleet (Milestone 6)
    browser_enabled: bool = True            # render JavaScript pages when static HTML is empty
    browser_worker_max_contexts: int = 2    # isolated contexts per worker process
    browser_worker_name: str = ""           # defaults to the hostname
    browser_tenant_concurrency: int = 2     # missions running at once per organization
    browser_missions_per_run: int = 20      # render missions a monitoring run may create
    browser_heartbeat_seconds: int = 10
    browser_stale_seconds: int = 120        # silent worker/mission considered lost
    browser_poll_seconds: float = 2.0
    browser_min_free_memory_mb: int = 300   # below this, the worker takes no new mission
    browser_restart_after_missions: int = 100
    browser_restricted_after_challenges: int = 3
    browser_extra_args: list[str] = []      # extra Chromium flags (e.g. an egress proxy)
    # AI agents (Milestone 7)
    agent_model: str = ""                   # default: first of nlp_models
    agent_step_timeout_seconds: int = 60
    agent_task_timeout_seconds: int = 600
    agent_max_response_drafts: int = 3
    ai_approval_expiry_hours: int = 72
    report_max_bytes: int = 20_000_000

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
