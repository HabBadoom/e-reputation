"""Aynpulse Scanner, phase 2: linking a Scanner account to an AnyPulse organization (consent by
owner/admin), signed integration calls, collection destination, revocation and isolation."""

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.integrations.anypulse import client as integration
from app.integrations.anypulse import signing
from tests.conftest import Api, join, register
from tests.test_scanner import Scanner, code, scanner_signup

S = "/api/v1/scanner"
INTEG = "/api/v1/integrations/scanner"


@pytest.fixture(autouse=True)
def in_process_anypulse(app):
    """Scanner's signed client talks to this same app in-process instead of over the network."""
    integration.client_factory = lambda: TestClient(app)
    yield
    integration.client_factory = None


def request_token(sc: Scanner) -> str:
    r = sc.post(f"{S}/links/start")
    assert r.status_code == 201, r.text
    url = r.json()["authorize_url"]
    assert url.startswith(get_settings().app_base_url.rstrip("/") + "/connect/scanner?request=")
    return parse_qs(urlparse(url).query)["request"][0]


def linked(sc: Scanner, owner: Api) -> str:
    r = owner.post(f"{INTEG}/link-requests/approve", json={"token": request_token(sc)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def brand(api: Api, name: str) -> str:
    r = api.post("/api/v1/brands", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


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


# ---- consent ------------------------------------------------------------------------------------
def test_owner_approves_and_scanner_sees_brands(client):
    owner = register(client, org="Ooredoo Algérie")
    other = register(client, org="Mobilis")
    b1 = brand(owner, "Ooredoo")
    brand(other, "Mobilis")
    sc = scanner_signup(client)
    token = request_token(sc)

    r = owner.post(f"{INTEG}/link-requests/inspect", json={"token": token})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["account_email"] == sc.email and info["status"] == "pending"
    assert info["organization_name"] == "Ooredoo Algérie" and info["can_decide"] is True

    r = owner.post(f"{INTEG}/link-requests/approve", json={"token": token})
    assert r.status_code == 200, r.text
    link_id = r.json()["id"]
    assert owner.post(f"{INTEG}/link-requests/approve", json={"token": token}).status_code == 404
    assert owner.post(f"{INTEG}/link-requests/inspect", json={"token": token}
                      ).json()["status"] == "approved"

    mine = sc.get(f"{S}/links").json()
    assert [(lk["id"], lk["organization_name"], lk["status"]) for lk in mine] == \
        [(link_id, "Ooredoo Algérie", "active")]
    r = sc.get(f"{S}/links/{link_id}/brands")
    assert r.status_code == 200, r.text
    assert r.json() == [{"id": b1, "name": "Ooredoo"}]   # never the other organization's

    ap = owner.get(f"{INTEG}/links").json()
    assert ap[0]["account_email"] == sc.email and ap[0]["status"] == "active"
    audit = owner.get("/api/v1/audit-logs").json()["items"]
    assert any(a["action"] == "scanner.link_approved" for a in audit)


def test_only_owner_or_admin_decides(client):
    owner = register(client)
    viewer = join(owner, register(client), "viewer")
    analyst = join(owner, register(client), "analyst")
    sc = scanner_signup(client)
    token = request_token(sc)
    assert viewer.post(f"{INTEG}/link-requests/inspect", json={"token": token}
                       ).json()["can_decide"] is False
    for who in (viewer, analyst):
        r = who.post(f"{INTEG}/link-requests/approve", json={"token": token})
        assert r.status_code == 403
        assert who.post(f"{INTEG}/link-requests/decline", json={"token": token}).status_code == 403
    admin = join(owner, register(client), "admin")
    assert admin.post(f"{INTEG}/link-requests/approve", json={"token": token}).status_code == 200


def test_decline_and_unknown_tokens(client):
    owner = register(client)
    sc = scanner_signup(client)
    token = request_token(sc)
    assert owner.post(f"{INTEG}/link-requests/decline", json={"token": token}).status_code == 204
    assert owner.post(f"{INTEG}/link-requests/approve", json={"token": token}).status_code == 404
    assert sc.get(f"{S}/links").json() == []
    r = owner.post(f"{INTEG}/link-requests/inspect", json={"token": "x" * 40})
    assert r.status_code == 404 and code(r) == "invalid_link_request"


def test_consent_needs_an_anypulse_session(client):
    sc = scanner_signup(client)
    token = request_token(sc)
    # Not signed in to AnyPulse, or signed in with a Scanner token: refused.
    assert client.post(f"{INTEG}/link-requests/inspect", json={"token": token}).status_code == 401
    r = client.post(f"{INTEG}/link-requests/approve", json={"token": token},
                    headers={"authorization": f"Bearer {sc.access}"})
    assert r.status_code == 401


# ---- collection destination -------------------------------------------------------------------
def test_destination(client):
    owner = register(client, org="Ooredoo Algérie")
    other = register(client)
    b1 = brand(owner, "Ooredoo")
    b2 = brand(other, "Mobilis")
    sc = scanner_signup(client)
    link_id = linked(sc, owner)
    sid = sc.get(f"{S}/sessions/current").json()["id"]
    url = f"{S}/sessions/{sid}"

    r = sc.patch(url, json={"link_id": link_id, "brand_id": b1})
    assert r.status_code == 200, r.text
    assert r.json()["destination"] == {"link_id": link_id, "organization_name": "Ooredoo Algérie",
                                       "link_active": True, "brand_id": b1,
                                       "brand_name": "Ooredoo"}
    # A title change leaves the destination alone.
    r = sc.patch(url, json={"title": "Réseau 5G"})
    assert r.json()["destination"]["brand_id"] == b1 and r.json()["title"] == "Réseau 5G"

    r = sc.patch(url, json={"link_id": link_id, "brand_id": b2})
    assert r.status_code == 409 and code(r) == "invalid_destination"
    r = sc.patch(url, json={"link_id": None, "brand_id": b1})
    assert r.status_code == 409 and code(r) == "invalid_destination"
    r = sc.patch(url, json={"link_id": None})
    assert r.status_code == 200 and r.json()["destination"] is None

    # Discarded collections keep their history but cannot change destination.
    sc.post(f"{url}/cancel")
    r = sc.patch(url, json={"link_id": link_id})
    assert r.status_code == 409 and code(r) == "session_not_open"


# ---- revocation -------------------------------------------------------------------------------
def test_revoked_by_anypulse(client):
    owner = register(client, org="Org A")
    b1 = brand(owner, "Ooredoo")
    sc = scanner_signup(client)
    link_id = linked(sc, owner)
    sid = sc.get(f"{S}/sessions/current").json()["id"]
    assert sc.patch(f"{S}/sessions/{sid}", json={"link_id": link_id, "brand_id": b1}
                    ).status_code == 200

    r = owner.post(f"{INTEG}/links/{link_id}/revoke")
    assert r.status_code == 200 and r.json()["revoked_by"] == "anypulse"
    assert owner.post(f"{INTEG}/links/{link_id}/revoke").status_code == 409

    assert sc.get(f"{S}/links").json()[0]["status"] == "revoked"
    r = sc.get(f"{S}/links/{link_id}/brands")
    assert r.status_code == 409 and code(r) == "link_revoked"
    dest = sc.get(f"{S}/sessions/{sid}").json()["destination"]
    assert dest["link_active"] is False   # shown, so the user picks another destination
    r = sc.patch(f"{S}/sessions/{sid}", json={"link_id": link_id})
    assert r.status_code == 409 and code(r) == "link_revoked"
    # A new request can link the same pair again (new link, the old one stays revoked).
    assert linked(sc, owner) != link_id


def test_revoked_by_scanner(client):
    owner = register(client)
    sc = scanner_signup(client)
    link_id = linked(sc, owner)
    r = sc.post(f"{S}/links/{link_id}/revoke")
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    assert sc.post(f"{S}/links/{link_id}/revoke").status_code == 409
    assert owner.get(f"{INTEG}/links").json()[0]["revoked_by"] == "scanner"


# ---- signed endpoint and isolation -----------------------------------------------------------
def test_signed_endpoint_refuses_unsigned_or_forged_calls(client):
    owner = register(client)
    brand(owner, "Ooredoo")
    sc = scanner_signup(client)
    other = scanner_signup(client)
    link_id = linked(sc, owner)
    me = sc.get(f"{S}/auth/me").json()["account_id"]
    them = other.get(f"{S}/auth/me").json()["account_id"]
    path = f"{INTEG}/links/{link_id}/brands"
    key = get_settings().scanner_integration_key

    assert client.get(path).status_code == 401
    assert client.get(path, headers={"authorization": f"Bearer {owner.access}"}).status_code == 401
    forged = signing.signed_headers(b"wrong-key" * 4, method="GET", path=path, account_id=me,
                                    link_id=link_id, body=b"")
    r = client.get(path, headers=forged)
    assert r.status_code == 401 and code(r) == "invalid_signature"
    # Correctly signed, but for an account that does not own the link.
    wrong_owner = signing.signed_headers(key, method="GET", path=path, account_id=them,
                                         link_id=link_id, body=b"")
    assert client.get(path, headers=wrong_owner).status_code == 404
    good = signing.signed_headers(key, method="GET", path=path, account_id=me, link_id=link_id,
                                  body=b"")
    assert client.get(path, headers=good).status_code == 200


def test_links_are_isolated(client):
    owner = register(client)
    stranger_org = register(client)
    brand(owner, "Ooredoo")
    a = scanner_signup(client)
    b = scanner_signup(client)
    link_id = linked(a, owner)
    assert b.get(f"{S}/links").json() == []
    assert b.get(f"{S}/links/{link_id}/brands").status_code == 404
    assert b.post(f"{S}/links/{link_id}/revoke").status_code == 404
    sid = b.get(f"{S}/sessions/current").json()["id"]
    r = b.patch(f"{S}/sessions/{sid}", json={"link_id": link_id})
    assert r.status_code == 404 and code(r) == "link_not_found"
    assert stranger_org.get(f"{INTEG}/links").json() == []
    assert stranger_org.post(f"{INTEG}/links/{link_id}/revoke").status_code == 404
    # A link gives the Scanner account no access to AnyPulse data.
    assert client.get("/api/v1/mentions", headers={"authorization": f"Bearer {a.access}"}
                      ).status_code == 401


def test_anypulse_unreachable(client):
    owner = register(client)
    sc = scanner_signup(client)
    link_id = linked(sc, owner)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    integration.client_factory = lambda: httpx.Client(
        transport=httpx.MockTransport(down), base_url="http://anypulse")
    r = sc.get(f"{S}/links/{link_id}/brands")
    assert r.status_code == 503 and code(r) == "anypulse_unavailable"
