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
