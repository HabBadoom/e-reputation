"""Aynpulse Scanner foundation: separate accounts, token separation from AnyPulse, collection
sessions and isolation between Scanner accounts."""

import uuid

from fastapi.testclient import TestClient

from tests.conftest import PASSWORD, register

TOKEN = {"x-auth-mode": "token"}


class Scanner:
    """Bearer-token client for one Scanner account."""

    def __init__(self, client: TestClient, tokens: dict, email: str):
        self.c, self.email = client, email
        self.access, self.refresh = tokens["access_token"], tokens["refresh_token"]

    def _h(self) -> dict:
        return {"authorization": f"Bearer {self.access}", **TOKEN}

    def get(self, url, **kw):
        return self.c.get(url, headers=self._h(), **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(), **kw)


def scanner_signup(client: TestClient, *, email: str | None = None, **extra) -> Scanner:
    email = email or f"s{uuid.uuid4().hex[:8]}@example.dz"
    body = {"email": email, "password": PASSWORD, "full_name": "Kenza", **extra}
    r = client.post("/api/v1/scanner/auth/register", headers=TOKEN, json=body)
    assert r.status_code == 201, r.text
    return Scanner(client, r.json(), email.lower())


def code(r) -> str:  # noqa: ANN001
    return r.json()["error"]["code"]


# ---- accounts ------------------------------------------------------------------------------
def test_register_and_me(client):
    a = scanner_signup(client, full_name="  Kenza  ")
    r = a.get("/api/v1/scanner/auth/me")
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["email"] == a.email
    assert me["full_name"] == "Kenza"
    assert me["locale"] == "fr"
    # One account type only: there is no individual/professional distinction.
    assert "account_type" not in me and "organization_name" not in me


def test_register_validation(client):
    url = "/api/v1/scanner/auth/register"
    base = {"email": "v@example.dz", "password": PASSWORD, "full_name": "V"}
    r = client.post(url, headers=TOKEN, json={**base, "password": "short"})
    assert r.status_code == 422 and code(r) == "validation_error"
    r = client.post(url, headers=TOKEN, json={**base, "full_name": "   "})
    assert r.status_code == 422, "blank name accepted"


def test_duplicate_email(client):
    scanner_signup(client, email="dup@example.dz")
    r = client.post("/api/v1/scanner/auth/register", headers=TOKEN,
                    json={"email": "DUP@example.dz", "password": PASSWORD, "full_name": "X"})
    assert r.status_code == 409 and code(r) == "email_taken"


def test_login(client):
    a = scanner_signup(client)
    bad = client.post("/api/v1/scanner/auth/login", headers=TOKEN,
                      json={"email": a.email, "password": "wrong password here"})
    assert bad.status_code == 401 and code(bad) == "invalid_credentials"
    ok = client.post("/api/v1/scanner/auth/login", headers=TOKEN,
                     json={"email": a.email.upper(), "password": PASSWORD})
    assert ok.status_code == 200, ok.text
    assert ok.json()["access_token"] and ok.json()["refresh_token"]


# ---- separation from AnyPulse ----------------------------------------------------------------
def test_scanner_and_anypulse_accounts_are_separate(client):
    a = scanner_signup(client, email="same@example.dz")
    # A Scanner account is not an AnyPulse login, even with the same email and password.
    r = client.post("/api/v1/auth/login", headers=TOKEN,
                    json={"email": "same@example.dz", "password": PASSWORD})
    assert r.status_code == 401
    # An AnyPulse user can register in AnyPulse with that email; the two stay independent.
    ap = register(client, email="same@example.dz", verify=False)
    r = client.post("/api/v1/scanner/auth/login", headers=TOKEN,
                    json={"email": "same@example.dz", "password": PASSWORD})
    assert r.status_code == 200  # the Scanner account, not the AnyPulse user
    # Tokens are not interchangeable.
    assert client.get("/api/v1/auth/me", headers={"authorization": f"Bearer {a.access}"}
                      ).status_code == 401
    assert client.get("/api/v1/brands", headers={"authorization": f"Bearer {a.access}"}
                      ).status_code == 401
    assert client.get("/api/v1/scanner/auth/me",
                      headers={"authorization": f"Bearer {ap.access}"}).status_code == 401
    assert client.get("/api/v1/scanner/sessions/current",
                      headers={"authorization": f"Bearer {ap.access}"}).status_code == 401


# ---- refresh, logout ---------------------------------------------------------------------------
def test_refresh_rotation_and_reuse(client):
    a = scanner_signup(client)
    url = "/api/v1/scanner/auth/refresh"
    r1 = client.post(url, headers=TOKEN, json={"refresh_token": a.refresh})
    assert r1.status_code == 200, r1.text
    new = r1.json()
    assert new["refresh_token"] != a.refresh
    # Presenting the rotated-out token again revokes the whole family.
    r2 = client.post(url, headers=TOKEN, json={"refresh_token": a.refresh})
    assert r2.status_code == 401 and code(r2) == "refresh_reuse"
    r3 = client.post(url, headers=TOKEN, json={"refresh_token": new["refresh_token"]})
    assert r3.status_code == 401
    r4 = client.get("/api/v1/scanner/auth/me",
                    headers={"authorization": f"Bearer {new['access_token']}"})
    assert r4.status_code == 401 and code(r4) == "session_revoked"


def test_logout_ends_session_immediately(client):
    a = scanner_signup(client)
    assert a.get("/api/v1/scanner/auth/me").status_code == 200
    r = client.post("/api/v1/scanner/auth/logout", json={"refresh_token": a.refresh})
    assert r.status_code == 204
    r = a.get("/api/v1/scanner/auth/me")
    assert r.status_code == 401 and code(r) == "session_revoked"


def test_cookie_mode_uses_scanner_cookies_and_csrf(client):
    a = scanner_signup(client)
    r = client.post("/api/v1/scanner/auth/login", json={"email": a.email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] is None  # tokens only in httpOnly cookies
    names = set(client.cookies.keys())
    assert {"sc_access", "sc_refresh", "sc_csrf"} <= names
    assert not names & {"ri_access", "ri_refresh", "ri_csrf"}
    s = client.get("/api/v1/scanner/sessions/current")
    assert s.status_code == 200, s.text
    sid = s.json()["id"]
    url = f"/api/v1/scanner/sessions/{sid}"
    no_csrf = client.patch(url, json={"title": "Ooredoo novembre"})
    assert no_csrf.status_code == 403 and code(no_csrf) == "csrf_failed"
    ok = client.patch(url, json={"title": "Ooredoo novembre"},
                      headers={"x-csrf-token": client.cookies["sc_csrf"]})
    assert ok.status_code == 200 and ok.json()["title"] == "Ooredoo novembre"
    # Scanner cookies don't authenticate AnyPulse.
    assert client.get("/api/v1/auth/me").status_code == 401


# ---- collection sessions -----------------------------------------------------------------------
def test_current_session_is_stable(client):
    a = scanner_signup(client)
    r1 = a.get("/api/v1/scanner/sessions/current")
    assert r1.status_code == 200, r1.text
    s = r1.json()
    assert s["status"] == "draft" and s["is_open"] is True
    assert s["counters"] == {"items": 0, "screenshots": 0, "urls": 0, "imports": 0,
                             "needs_review": 0, "ready": 0, "failed": 0}
    assert s["anypulse_collection_id"] is None
    # Coming back to the add-item screen returns the same collection, not a new one.
    assert a.get("/api/v1/scanner/sessions/current").json()["id"] == s["id"]
    page = a.get("/api/v1/scanner/sessions").json()
    assert page["total"] == 1 and page["items"][0]["id"] == s["id"]


def test_cancel_then_new_session(client):
    a = scanner_signup(client)
    sid = a.get("/api/v1/scanner/sessions/current").json()["id"]
    r = a.post(f"/api/v1/scanner/sessions/{sid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert r.json()["is_open"] is False
    again = a.post(f"/api/v1/scanner/sessions/{sid}/cancel")
    assert again.status_code == 409 and code(again) == "session_not_open"
    new = a.get("/api/v1/scanner/sessions/current").json()
    assert new["id"] != sid and new["status"] == "draft"
    assert a.get("/api/v1/scanner/sessions").json()["total"] == 2


def test_sessions_are_isolated_between_accounts(client):
    a = scanner_signup(client)
    b = scanner_signup(client)
    sid = a.get("/api/v1/scanner/sessions/current").json()["id"]
    assert b.get(f"/api/v1/scanner/sessions/{sid}").status_code == 404
    assert b.patch(f"/api/v1/scanner/sessions/{sid}", json={"title": "x"}).status_code == 404
    assert b.post(f"/api/v1/scanner/sessions/{sid}/cancel").status_code == 404
    assert b.get("/api/v1/scanner/sessions").json()["total"] == 0
    assert b.get("/api/v1/scanner/sessions/current").json()["id"] != sid
    assert a.get(f"/api/v1/scanner/sessions/{sid}").json()["status"] == "draft"


def test_requires_authentication(client):
    assert client.get("/api/v1/scanner/sessions/current").status_code == 401
    assert client.get("/api/v1/scanner/auth/me").status_code == 401
