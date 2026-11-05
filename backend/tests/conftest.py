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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.core.rate_limit import MemoryLimiter, get_limiter  # noqa: E402
from app.main import create_app  # noqa: E402

DATA_TABLES = ["audit_logs", "brand_aliases", "brands", "invitations", "refresh_tokens",
               "memberships", "tenants", "users"]


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
    yield


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


def register(client: TestClient, *, email: str | None = None, org: str = "Org") -> Api:
    email = email or f"u{uuid.uuid4().hex[:8]}@example.dz"
    r = client.post("/api/v1/auth/register", headers={"x-auth-mode": "token"},
                    json={"email": email, "password": PASSWORD, "full_name": "Test User",
                          "organization_name": org})
    assert r.status_code == 201, r.text
    api = Api(client, r.json())
    api.email = email  # type: ignore[attr-defined]
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
