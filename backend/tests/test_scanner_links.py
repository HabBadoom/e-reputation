"""Aynpulse Scanner, phase 2: each Scanner account gets its own AnyPulse organization, linked
automatically at sign-up; revocation by platform administrators only; signed integration calls.
Scanner users are collection agents: no AnyPulse invitation, no brand choice."""

import time

import httpx
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.integrations.anypulse import client as integration
from app.integrations.anypulse import signing
from tests.conftest import sent_emails
from tests.test_scanner import Scanner, code, scanner_signup

S = "/api/v1/scanner"
INTEG = "/api/v1/integrations/scanner"


def account_id(sc: Scanner) -> str:
    return sc.get(f"{S}/auth/me").json()["account_id"]


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
def test_signup_creates_and_links_an_organization(client, make_user, platform_admin):
    admin = platform_admin(make_user(org="Plateforme"))
    sc = scanner_signup(client, email="kenza@example.dz", full_name="Kenza Benali")
    r = sc.get(f"{S}/anypulse")
    assert r.status_code == 200, r.text
    assert r.json() == {"state": "active", "revoked_at": None, "revoke_reason": None}
    row = next(x for x in admin.get("/api/v1/admin/scanner/accounts").json()
               if x["account_id"] == account_id(sc))
    assert row["organization_name"] == "Kenza Benali" and row["link_status"] == "active"
    # Collection agents are not invited to AnyPulse: no e-mail to the Scanner address.
    assert not [m for m in sent_emails() if m.to_email == "kenza@example.dz"]


def test_each_account_gets_its_own_organization(client, make_user, platform_admin):
    admin = platform_admin(make_user(org="Plateforme"))
    a = scanner_signup(client, full_name="A")
    b = scanner_signup(client, full_name="B")
    rows = {x["account_id"]: x for x in admin.get("/api/v1/admin/scanner/accounts").json()}
    ra, rb = rows[account_id(a)], rows[account_id(b)]
    assert ra["tenant_id"] and rb["tenant_id"] and ra["tenant_id"] != rb["tenant_id"]
    assert (ra["organization_name"], rb["organization_name"]) == ("A", "B")


def test_provisioning_is_retried_when_anypulse_was_down(client):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    integration.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(down),
                                                      base_url="http://anypulse")
    sc = scanner_signup(client)          # sign-up still succeeds
    assert sc.get(f"{S}/anypulse").json()["state"] == "pending"
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


def test_no_brand_or_invitation_endpoints(client):
    sc = scanner_signup(client)
    for method, url in (("GET", f"{S}/brands"), ("POST", f"{S}/brands"),
                        ("POST", f"{S}/anypulse/invitation")):
        assert sc.c.request(method, url, headers=sc._h()).status_code in (404, 405), url
    sid = sc.get(f"{S}/sessions/current").json()["id"]
    r = sc.patch(f"{S}/sessions/{sid}", json={"title": "Réseau 5G"})
    assert r.status_code == 200 and "destination" not in r.json()


# ---- revocation (platform administrators only) -------------------------------------------------
def test_platform_admin_revokes_and_restores(client, make_user, platform_admin):
    admin = platform_admin(make_user(org="Plateforme"))
    sc = scanner_signup(client, email="fatma@example.dz", full_name="Fatma")
    sc.get(f"{S}/sessions/current")
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
    assert sc.get(f"{S}/sessions/current").status_code == 200   # Scanner itself still works
    audit = admin.get(f"/api/v1/admin/tenants/{row['tenant_id']}")
    assert audit.status_code == 200

    r = admin.post(f"/api/v1/admin/scanner/accounts/{me}/restore")
    assert r.status_code == 200 and r.json()["link_status"] == "active"
    assert sc.get(f"{S}/anypulse").json()["state"] == "active"


def test_only_platform_admins_revoke(client, make_user):
    sc = scanner_signup(client)
    me = account_id(sc)
    stranger = make_user()
    assert stranger.get("/api/v1/admin/scanner/accounts").status_code == 403
    assert stranger.post(f"/api/v1/admin/scanner/accounts/{me}/revoke", json={}
                         ).status_code == 403
    assert sc.get(f"{S}/anypulse").json()["state"] == "active"


# ---- signed endpoints -------------------------------------------------------------------------
def test_integration_endpoint_refuses_unsigned_or_forged_calls(client, make_user):
    sc = scanner_signup(client)
    me = account_id(sc)
    path = f"{INTEG}/provision"
    key = get_settings().scanner_integration_key
    owner = make_user()

    assert client.post(path).status_code == 401
    assert client.post(path, headers={"authorization": f"Bearer {owner.access}"}
                       ).status_code == 401
    forged = signing.signed_headers(b"wrong-key" * 4, method="POST", path=path, account_id=me,
                                    link_id="", body=b"")
    r = client.post(path, headers=forged)
    assert r.status_code == 401 and code(r) == "invalid_signature"
    unknown = signing.signed_headers(key, method="POST", path=path,
                                     account_id="00000000-0000-0000-0000-000000000000",
                                     link_id="", body=b"")
    assert client.post(path, headers=unknown).status_code == 404
    # A Scanner token gives no access to AnyPulse data.
    for url in ("/api/v1/mentions", "/api/v1/brands", "/api/v1/reports"):
        assert client.get(url, headers={"authorization": f"Bearer {sc.access}"}
                          ).status_code == 401
