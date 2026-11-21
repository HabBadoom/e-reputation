"""Aynpulse Scanner, phase 2: each Scanner account gets its own AnyPulse organization, linked
automatically at sign-up; brands and destination; owner invitation; revocation by platform
administrators only; signed integration calls."""

import time

import httpx
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.integrations.anypulse import client as integration
from app.integrations.anypulse import signing
from tests.conftest import PASSWORD, Api, token_from_last_email
from tests.test_scanner import Scanner, code, scanner_signup

S = "/api/v1/scanner"
INTEG = "/api/v1/integrations/scanner"


def account_id(sc: Scanner) -> str:
    return sc.get(f"{S}/auth/me").json()["account_id"]


def join_anypulse(client: TestClient, email: str) -> Api:
    """Accept the owner invitation e-mailed at Scanner sign-up by creating an AnyPulse login."""
    token = token_from_last_email(email, "/invite")
    r = client.post("/api/v1/auth/register", headers={"x-auth-mode": "token"},
                    json={"email": email, "password": PASSWORD, "full_name": "Kenza",
                          "invitation_token": token})
    assert r.status_code == 201, r.text
    return Api(client, r.json())


# ---- signing (unit) ----------------------------------------------------------------------------
def test_signature_binds_every_part():
    k = b"k" * 32
    parts = {"method": "GET", "path": "/api/v1/x?a=1", "account_id": "A", "link_id": "L",
             "body": b"{}"}
    h = signing.signed_headers(k, **parts)
    sig, ts = h[signing.HEADER_SIGNATURE], h[signing.HEADER_TIMESTAMP]
    assert signing.verify(k, sig, timestamp=ts, **parts)
    for change in ({"path": "/api/v1/x?a=2"}, {"method": "POST"}, {"account_id": "B"},
                   {"link_id": "M"}, {"body": b"{ }"}):
        assert not signing.verify(k, sig, timestamp=ts, **{**parts, **change}), change
    assert not signing.verify(b"z" * 32, sig, timestamp=ts, **parts)
    old = signing.signed_headers(k, now=time.time() - 600, **parts)
    assert not signing.verify(k, old[signing.HEADER_SIGNATURE],
                              timestamp=old[signing.HEADER_TIMESTAMP], **parts)


# ---- automatic organization --------------------------------------------------------------------
def test_signup_creates_and_links_an_organization(client):
    sc = scanner_signup(client, email="kenza@example.dz", full_name="Kenza Benali")
    r = sc.get(f"{S}/anypulse")
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["state"] == "active" and a["organization_name"] == "Kenza Benali"
    assert a["anypulse_url"] == get_settings().app_base_url and a["revoked_at"] is None

    # The account owner is invited by e-mail to open the organization in AnyPulse (separate
    # AnyPulse login), and lands in that organization as owner.
    ap = join_anypulse(client, "kenza@example.dz")
    me = ap.get("/api/v1/auth/me").json()
    assert me["tenant"]["name"] == "Kenza Benali" and me["tenant"]["role"] == "owner"
    audit = [e["action"] for e in ap.get("/api/v1/audit-logs").json()["items"]]
    assert "scanner.organization_created" in audit
    # Once a member, no new invitation is needed.
    r = sc.post(f"{S}/anypulse/invitation")
    assert r.status_code == 409 and code(r) == "already_member"


def test_each_account_gets_its_own_organization(client):
    a = scanner_signup(client, full_name="A")
    b = scanner_signup(client, full_name="B")
    assert a.get(f"{S}/anypulse").json()["organization_name"] == "A"
    assert b.get(f"{S}/anypulse").json()["organization_name"] == "B"
    a.post(f"{S}/brands", json={"name": "Ooredoo"})
    assert [x["name"] for x in a.get(f"{S}/brands").json()] == ["Ooredoo"]
    assert b.get(f"{S}/brands").json() == []   # never another account's organization


def test_provisioning_is_retried_when_anypulse_was_down(client):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    integration.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(down),
                                                      base_url="http://anypulse")
    sc = scanner_signup(client)          # sign-up still succeeds
    assert sc.get(f"{S}/anypulse").json()["state"] == "pending"
    r = sc.get(f"{S}/brands")
    assert r.status_code == 409 and code(r) == "anypulse_pending"

    integration.client_factory = lambda: TestClient(client.app)
    assert sc.get(f"{S}/anypulse").json()["state"] == "active"


def test_provisioning_is_idempotent(client):
    sc = scanner_signup(client)
    me = account_id(sc)
    path = f"{INTEG}/provision"
    h = signing.signed_headers(get_settings().scanner_integration_key, method="POST", path=path,
                               account_id=me, link_id="", body=b"")
    r = client.post(path, headers=h)
    assert r.status_code == 200 and r.json()["created"] is False


def test_resend_invitation(client):
    sc = scanner_signup(client, email="amel@example.dz")
    r = sc.post(f"{S}/anypulse/invitation")
    assert r.status_code == 200 and r.json()["email"] == "amel@example.dz"
    join_anypulse(client, "amel@example.dz")   # the newest invitation works


# ---- brands and destination ---------------------------------------------------------------------
def test_brands_and_destination(client):
    sc = scanner_signup(client, full_name="Kenza")
    other = scanner_signup(client)
    r = sc.post(f"{S}/brands", json={"name": "  Ooredoo  "})
    assert r.status_code == 201, r.text
    b1 = r.json()["id"]
    assert r.json()["name"] == "Ooredoo"
    dup = sc.post(f"{S}/brands", json={"name": "ooredoo"})
    assert dup.status_code == 409 and code(dup) == "duplicate"
    b_other = other.post(f"{S}/brands", json={"name": "Mobilis"}).json()["id"]

    sid = sc.get(f"{S}/sessions/current").json()["id"]
    url = f"{S}/sessions/{sid}"
    r = sc.patch(url, json={"brand_id": b1})
    assert r.status_code == 200, r.text
    assert r.json()["destination"] == {"organization_name": "Kenza", "access_active": True,
                                       "brand_id": b1, "brand_name": "Ooredoo"}
    r = sc.patch(url, json={"title": "Réseau 5G"})   # destination unchanged
    assert r.json()["destination"]["brand_id"] == b1 and r.json()["title"] == "Réseau 5G"
    r = sc.patch(url, json={"brand_id": b_other})
    assert r.status_code == 409 and code(r) == "invalid_destination"
    r = sc.patch(url, json={"brand_id": None})
    assert r.status_code == 200 and r.json()["destination"] is None
    sc.post(f"{url}/cancel")
    r = sc.patch(url, json={"brand_id": b1})
    assert r.status_code == 409 and code(r) == "session_not_open"


# ---- revocation (platform administrators only) -------------------------------------------------
def test_platform_admin_revokes_and_restores(client, make_user, platform_admin):
    admin = platform_admin(make_user(org="Plateforme"))
    sc = scanner_signup(client, email="fatma@example.dz", full_name="Fatma")
    b1 = sc.post(f"{S}/brands", json={"name": "Ooredoo"}).json()["id"]
    sid = sc.get(f"{S}/sessions/current").json()["id"]
    assert sc.patch(f"{S}/sessions/{sid}", json={"brand_id": b1}).status_code == 200
    me = account_id(sc)

    rows = admin.get("/api/v1/admin/scanner/accounts").json()
    row = next(r for r in rows if r["account_id"] == me)
    assert row["email"] == "fatma@example.dz" and row["organization_name"] == "Fatma"
    assert row["link_status"] == "active" and row["collections"] == 1

    r = admin.post(f"/api/v1/admin/scanner/accounts/{me}/revoke", json={"reason": "Départ"})
    assert r.status_code == 200, r.text
    assert r.json()["link_status"] == "revoked" and r.json()["revoke_reason"] == "Départ"

    a = sc.get(f"{S}/anypulse").json()
    assert a["state"] == "revoked" and a["revoke_reason"] == "Départ"
    for call in (lambda: sc.get(f"{S}/brands"), lambda: sc.post(f"{S}/brands", json={"name": "X"}),
                 lambda: sc.post(f"{S}/anypulse/invitation"),
                 lambda: sc.patch(f"{S}/sessions/{sid}", json={"brand_id": b1})):
        r = call()
        assert r.status_code == 403 and code(r) == "access_revoked", r.text
    dest = sc.get(f"{S}/sessions/{sid}").json()["destination"]
    assert dest["access_active"] is False          # shown in Scanner
    assert sc.get(f"{S}/sessions/current").status_code == 200   # Scanner itself still works

    r = admin.post(f"/api/v1/admin/scanner/accounts/{me}/restore")
    assert r.status_code == 200 and r.json()["link_status"] == "active"
    assert sc.get(f"{S}/brands").status_code == 200


def test_only_platform_admins_revoke(client, make_user):
    sc = scanner_signup(client, email="nesrine@example.dz")
    me = account_id(sc)
    owner_of_its_org = join_anypulse(client, "nesrine@example.dz")
    stranger = make_user()
    for who in (owner_of_its_org, stranger):
        assert who.get("/api/v1/admin/scanner/accounts").status_code == 403
        assert who.post(f"/api/v1/admin/scanner/accounts/{me}/revoke", json={}
                        ).status_code == 403
    assert sc.get(f"{S}/anypulse").json()["state"] == "active"


# ---- signed endpoints -------------------------------------------------------------------------
def test_integration_endpoints_refuse_unsigned_or_forged_calls(client, make_user):
    sc = scanner_signup(client)
    other = scanner_signup(client)
    me, them = account_id(sc), account_id(other)
    link = client.post(f"{INTEG}/provision", headers=signing.signed_headers(
        get_settings().scanner_integration_key, method="POST", path=f"{INTEG}/provision",
        account_id=me, link_id="", body=b"")).json()["link_id"]
    path = f"{INTEG}/brands"
    key = get_settings().scanner_integration_key
    owner = make_user()

    assert client.get(path).status_code == 401
    assert client.get(path, headers={"authorization": f"Bearer {owner.access}"}).status_code == 401
    forged = signing.signed_headers(b"wrong-key" * 4, method="GET", path=path, account_id=me,
                                    link_id=link, body=b"")
    r = client.get(path, headers=forged)
    assert r.status_code == 401 and code(r) == "invalid_signature"
    wrong_owner = signing.signed_headers(key, method="GET", path=path, account_id=them,
                                         link_id=link, body=b"")
    assert client.get(path, headers=wrong_owner).status_code == 404
    good = signing.signed_headers(key, method="GET", path=path, account_id=me, link_id=link,
                                  body=b"")
    assert client.get(path, headers=good).status_code == 200
    # A Scanner token gives no access to AnyPulse data.
    assert client.get("/api/v1/mentions", headers={"authorization": f"Bearer {sc.access}"}
                      ).status_code == 401


def test_anypulse_unreachable(client):
    sc = scanner_signup(client)
    assert sc.get(f"{S}/anypulse").json()["state"] == "active"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    integration.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(down),
                                                      base_url="http://anypulse")
    r = sc.get(f"{S}/brands")
    assert r.status_code == 503 and code(r) == "anypulse_unavailable"
