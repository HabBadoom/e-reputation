"""ORM models. The authoritative schema (incl. RLS policies and grants) is in migrations/."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _user_fk() -> ForeignKey:
    return ForeignKey("users.id", ondelete="SET NULL")


def _created() -> Mapped[datetime]:
    # Python-side default too, so values are known after flush without a re-select.
    return mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now(),
        nullable=False,
    )


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    default_locale: Mapped[str] = mapped_column(String(5), default="fr")
    nlp_corpus_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(String(200))
    locale: Mapped[str] = mapped_column(String(5), default="fr")
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()


class UserToken(Base):
    """Single-use tokens sent by email (verify, reset) or issued for the MFA login step."""

    __tablename__ = "user_tokens"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class UserMfa(Base):
    __tablename__ = "user_mfa"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    secret_enc: Mapped[str | None] = mapped_column(Text)
    pending_secret_enc: Mapped[str | None] = mapped_column(Text)
    last_step: Mapped[int] = mapped_column(BigInteger, default=0)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _created()


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    rank: Mapped[int] = mapped_column()
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.id"), primary_key=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = _created()


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = _created()


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(_user_fk())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(_user_fk())
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(INET)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str] = mapped_column(String(2), default="DZ")
    primary_language: Mapped[str] = mapped_column(String(10), default="fr")
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(_user_fk())
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class BrandAlias(Base):
    __tablename__ = "brand_aliases"
    __table_args__ = (
        # Composite FK: an alias can only point at a brand in the same tenant.
        ForeignKeyConstraint(
            ["tenant_id", "brand_id"], ["brands.tenant_id", "brands.id"], ondelete="CASCADE"
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    alias: Mapped[str] = mapped_column(String(200))
    script: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20), default="alias")
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------- Milestone 2
class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class PlanEntitlement(Base):
    __tablename__ = "plan_entitlements"
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[int | None] = mapped_column()


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"))
    status: Mapped[str] = mapped_column(String(20), default="trialing")
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


def _brand_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["tenant_id", "brand_id"], ["brands.tenant_id", "brands.id"],
                                ondelete="CASCADE")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (_brand_fk(),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class Competitor(Base):
    __tablename__ = "competitors"
    __table_args__ = (_brand_fk(),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(500))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class BrandKeyword(Base):
    __tablename__ = "brand_keywords"
    __table_args__ = (_brand_fk(),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    expression: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10), default="include")
    language: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = _created()


class MonitoringProject(Base):
    __tablename__ = "monitoring_projects"
    __table_args__ = (_brand_fk(), UniqueConstraint("tenant_id", "id"))
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")
    frequency_minutes: Mapped[int] = mapped_column()
    include_competitors: Mapped[bool] = mapped_column(Boolean, default=True)
    languages: Mapped[list[str]] = mapped_column(ARRAY(Text), default=lambda: ["fr", "ar"])
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(_user_fk())
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class MonitoringRun(Base):
    __tablename__ = "monitoring_runs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "project_id"],
                             ["monitoring_projects.tenant_id", "monitoring_projects.id"],
                             ondelete="CASCADE"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    trigger: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    queued_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- Milestone 3
class ConnectorPolicy(Base):
    __tablename__ = "connector_policies"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    access_method: Mapped[str] = mapped_column(String(40))
    legal_basis: Mapped[str] = mapped_column(Text)
    data_categories: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    restrictions: Mapped[str] = mapped_column(Text, default="")
    retention_days: Mapped[int] = mapped_column()
    review_status: Mapped[str] = mapped_column(String(30))
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _created()


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    url: Mapped[str] = mapped_column(String(1000))
    domain: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(40), default="unknown")
    country: Mapped[str | None] = mapped_column(String(2))
    language: Mapped[str | None] = mapped_column(String(10))
    connector: Mapped[str] = mapped_column(ForeignKey("connector_policies.key"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    discovered_via: Mapped[str] = mapped_column(String(20), default="manual")
    coverage_status: Mapped[str] = mapped_column(String(30), default="PARTIALLY_AVAILABLE")
    restrictions: Mapped[str] = mapped_column(Text, default="")
    mentions_count: Mapped[int] = mapped_column(default=0)
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_errors: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class Mention(Base):
    __tablename__ = "mentions"
    __table_args__ = (
        _brand_fk(),
        ForeignKeyConstraint(["tenant_id", "project_id"],
                             ["monitoring_projects.tenant_id", "monitoring_projects.id"]),
        ForeignKeyConstraint(["tenant_id", "source_id"], ["sources.tenant_id", "sources.id"]),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    first_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(2000))
    canonical_url: Mapped[str] = mapped_column(String(2000))
    domain: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    original_text: Mapped[str] = mapped_column(Text, default="")
    normalized_text: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(10))
    matched_terms: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    publisher: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at_source: Mapped[str | None] = mapped_column(String(30))
    collected_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = _created()
    seen_count: Mapped[int] = mapped_column(default=1)
    collection_method: Mapped[str] = mapped_column(String(30))
    connector: Mapped[str] = mapped_column(String(40))
    connector_version: Mapped[str] = mapped_column(String(20))
    verification_status: Mapped[str] = mapped_column(String(30))
    content_hash: Mapped[str] = mapped_column(String(64))
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mentions.id", ondelete="SET NULL"))
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------- Milestone 4
class MentionAnalysis(Base):
    __tablename__ = "mention_analysis"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "mention_id"], ["mentions.tenant_id", "mentions.id"],
                             ondelete="CASCADE"),
    )
    mention_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    script: Mapped[str | None] = mapped_column(String(20))
    heuristic_language: Mapped[str | None] = mapped_column(String(10))
    code_switching: Mapped[bool | None] = mapped_column(Boolean)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(10))
    dialect: Mapped[str | None] = mapped_column(String(30))
    sentiment: Mapped[str | None] = mapped_column(String(20))
    sarcasm: Mapped[str | None] = mapped_column(String(10))
    about_target: Mapped[str | None] = mapped_column(String(10))
    intents: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    emotions: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    topics: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    translation_fr: Mapped[str | None] = mapped_column(Text)
    agreement: Mapped[float | None] = mapped_column()
    arbitration_used: Mapped[bool] = mapped_column(Boolean, default=False)
    annotators: Mapped[list] = mapped_column(JSONB, default=list)
    models: Mapped[dict] = mapped_column(JSONB, default=dict)
    prompt_version: Mapped[str | None] = mapped_column(String(30))
    analysis_version: Mapped[str | None] = mapped_column(String(30))
    analysed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    corrected_sentiment: Mapped[str | None] = mapped_column(String(20))
    corrected_sarcasm: Mapped[str | None] = mapped_column(String(10))
    corrected_about_target: Mapped[str | None] = mapped_column(String(10))
    corrected_intents: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    corrected_by: Mapped[uuid.UUID | None] = mapped_column(_user_fk())
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LanguageAnnotation(Base):
    __tablename__ = "language_annotations"
    id: Mapped[uuid.UUID] = _pk()
    origin_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"))
    origin_mention_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    script: Mapped[str | None] = mapped_column(String(20))
    auto_labels: Mapped[dict] = mapped_column(JSONB, default=dict)
    auto_status: Mapped[str] = mapped_column(String(20), default="manual")
    labels: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="auto")
    split: Mapped[str] = mapped_column(String(20), default="unassigned")
    verified_by: Mapped[uuid.UUID | None] = mapped_column(_user_fk())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class LanguageEvaluationRun(Base):
    __tablename__ = "language_evaluation_runs"
    id: Mapped[uuid.UUID] = _pk()
    status: Mapped[str] = mapped_column(String(20), default="queued")
    split: Mapped[str] = mapped_column(String(20), default="gold_test")
    n_items: Mapped[int] = mapped_column(default=0)
    prompt_version: Mapped[str | None] = mapped_column(String(30))
    analysis_version: Mapped[str | None] = mapped_column(String(30))
    models: Mapped[dict] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(_user_fk())
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DialectExpression(Base):
    __tablename__ = "dialect_expressions"
    id: Mapped[uuid.UUID] = _pk()
    canonical: Mapped[str] = mapped_column(String(100))
    arabic: Mapped[str | None] = mapped_column(String(100))
    variants: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    meaning_fr: Mapped[str] = mapped_column(Text, default="")
    polarity: Mapped[str] = mapped_column(String(10), default="none")
    status: Mapped[str] = mapped_column(String(20), default="seed_unvalidated")
    created_at: Mapped[datetime] = _created()
