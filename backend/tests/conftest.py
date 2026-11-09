"""Test fixtures. Tests run against a real, migrated PostgreSQL database.

Required env (CI sets these; see docs/TESTING.md):
  REPINTEL_DATABASE_URL            app role (NOBYPASSRLS) — what the API uses
  REPINTEL_MIGRATION_DATABASE_URL  owner role — used here only to wipe data between tests
"""

import os
import uuid

os.environ.setdefault("REPINTEL_ENV", "test")
os.environ.setdefault("REPINTEL_JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("REPINTEL_COOKIE_SECURE", "false")
os.environ.setdefault("REPINTEL_EMAIL_PROVIDER", "memory")
# Plan limits are tested explicitly (test_entitlements); other tests run unconstrained.
os.environ.setdefault("REPINTEL_DEFAULT_PLAN", "unlimited")
# Test-only key (valid Fernet format). Never used outside the test suite.
os.environ.setdefault("REPINTEL_ENCRYPTION_KEYS", "dGVzdC1vbmx5LWtleS1kby1ub3QtdXNlLTAxMjM0NTY=")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.core.rate_limit import MemoryLimiter, get_limiter  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.notifications.email import MemoryProvider, get_email_provider  # noqa: E402

DATA_TABLES = [
    "usage_events", "plan_change_requests", "invoice_lines", "invoices", "billing_accounts",
    "invoice_counters",
    "approval_requests", "ai_actions", "agent_runs", "ai_tasks",
    "captcha_events", "browser_session_events", "browser_sessions", "browser_missions",
    "browser_workers", "browser_domain_state",
    "reports", "alerts", "alert_rules", "webhooks", "crisis_events", "trend_events",
    "language_evaluation_runs", "language_annotations", "mention_analysis",
    "mentions", "sources", "jobs", "monitoring_runs", "monitoring_projects", "brand_keywords",
    "competitors", "products", "subscriptions", "audit_logs", "brand_aliases", "brands",
    "invitations", "refresh_tokens", "mfa_recovery_codes", "user_mfa", "user_tokens",
    "memberships", "tenants", "users",
]


@pytest.fixture(scope="session")
def owner_engine():
    url = os.environ.get("REPINTEL_MIGRATION_DATABASE_URL")
    if not url:
        pytest.exit("REPINTEL_MIGRATION_DATABASE_URL must be set (see docs/TESTING.md)", 2)
    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean_db(owner_engine):
    with owner_engine.begin() as c:
        c.execute(text(f"TRUNCATE {', '.join(DATA_TABLES)} CASCADE"))
    lim = get_limiter()
    if isinstance(lim, MemoryLimiter):
        lim.reset()
    outbox().clear()
    yield


def outbox() -> MemoryProvider:
    p = get_email_provider()
    assert isinstance(p, MemoryProvider)
    return p


def drain(max_rounds: int = 10) -> int:
    """Process every due job synchronously (what the worker process does in production)."""
    from app.jobs.worker import run_once

    total = 0
    for _ in range(max_rounds):
        n = run_once(schedule=False)
        total += n
        if n == 0:
            break
    return total


def sent_emails():
    drain()
    return list(outbox().outbox)


def token_from_last_email(to: str, path: str) -> str:
    """Extract the token from the most recent email to `to` whose link points at `path`."""
    import re
    for msg in reversed(sent_emails()):
        if msg.to_email == to.lower():
            m = re.search(rf"{path}\?token=([A-Za-z0-9_-]+)", msg.text)
            if m:
                return m.group(1)
    raise AssertionError(f"no email to {to} with a {path} link")


@pytest.fixture(scope="session")
def app():
    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class Api:
    """Bearer-token API client for one user, bound to their current tenant."""

    def __init__(self, client: TestClient, token: dict):
        self.c = client
        self.set_tokens(token)

    def set_tokens(self, token: dict) -> None:
        self.access = token["access_token"]
        self.refresh = token["refresh_token"]
        self.tenant_id = token["tenant_id"]

    def _h(self) -> dict:
        return {"authorization": f"Bearer {self.access}", "x-auth-mode": "token"}

    def get(self, url, **kw):
        return self.c.get(url, headers=self._h(), **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(), **kw)


PASSWORD = "correct horse battery staple"


def register(client: TestClient, *, email: str | None = None, org: str = "Org",
             verify: bool = True) -> Api:
    email = email or f"u{uuid.uuid4().hex[:8]}@example.dz"
    r = client.post("/api/v1/auth/register", headers={"x-auth-mode": "token"},
                    json={"email": email, "password": PASSWORD, "full_name": "Test User",
                          "organization_name": org})
    assert r.status_code == 201, r.text
    api = Api(client, r.json())
    api.email = email.lower()  # type: ignore[attr-defined]
    if verify:
        tok = token_from_last_email(email, "/verify-email")
        assert client.post("/api/v1/auth/email/verify", json={"token": tok}).status_code == 200
    return api


def join(owner: Api, member: Api, role: str) -> Api:
    """Invite `member` into owner's tenant with `role`, accept, and switch member to it."""
    inv = owner.post("/api/v1/invitations", json={"email": member.email, "role": role})
    assert inv.status_code == 201, inv.text
    r = member.post("/api/v1/invitations/accept", json={"token": inv.json()["token"]})
    assert r.status_code == 200, r.text
    member.set_tokens(r.json())
    assert member.tenant_id == owner.tenant_id
    return member


@pytest.fixture
def make_user(client):
    def _make(**kw) -> Api:
        return register(client, **kw)
    return _make


@pytest.fixture
def custom_plan(owner_engine):
    """`custom_plan(api, **limits)` moves the organization to a throw-away plan that copies the
    unlimited plan's entitlements with `limits` overriding them (None = unlimited). Removed
    afterwards, so tests never alter the seeded plans."""
    created: list[str] = []

    def _make(api: Api, *, status: str = "trialing", **limits) -> str:
        key = f"t_{uuid.uuid4().hex[:10]}"
        with owner_engine.begin() as c:
            pid = c.execute(text("INSERT INTO plans (id, key, name, is_active) VALUES "
                                 "(gen_random_uuid(), :k, :k, true) RETURNING id"),
                            {"k": key}).scalar_one()
            c.execute(text("INSERT INTO plan_entitlements (plan_id, key, value) "
                           "SELECT :p, e.key, e.value FROM plan_entitlements e "
                           "JOIN plans p ON p.id = e.plan_id WHERE p.key = 'unlimited'"),
                      {"p": pid})
            for k, v in limits.items():
                c.execute(text("INSERT INTO plan_entitlements (plan_id, key, value) "
                               "VALUES (:p, :k, :v) ON CONFLICT (plan_id, key) "
                               "DO UPDATE SET value = EXCLUDED.value"), {"p": pid, "k": k, "v": v})
            c.execute(text("UPDATE subscriptions SET plan_id = :p, status = :s "
                           "WHERE tenant_id = :t"), {"p": pid, "s": status, "t": api.tenant_id})
        created.append(key)
        return key

    yield _make
    with owner_engine.begin() as c:
        for key in created:
            c.execute(text("TRUNCATE plan_change_requests"))
            c.execute(text("DELETE FROM subscriptions WHERE plan_id = "
                           "(SELECT id FROM plans WHERE key = :k)"), {"k": key})
            c.execute(text("DELETE FROM plans WHERE key = :k"), {"k": key})


@pytest.fixture
def platform_admin(owner_engine, monkeypatch):
    """`platform_admin(api)` flags the user as a platform administrator (an operator action).
    MFA for the console is disabled here; test_billing checks it separately."""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "admin_require_mfa", False)

    def _make(api: Api) -> Api:
        with owner_engine.begin() as c:
            c.execute(text("UPDATE users SET is_platform_admin = true WHERE email = :e"),
                      {"e": api.email})
        return api
    return _make
