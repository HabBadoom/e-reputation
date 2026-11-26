"""Aynpulse Scanner, phase 2: every Scanner account (a collection agent) may send collections to
AnyPulse from sign-up on; only platform administrators revoke or restore that access. Agents get
no AnyPulse organization, invitation or brand choice: collections go to a central RepIntel space
and RepIntel's AI attributes brands later (phase 7)."""

from sqlalchemy import text

from tests.conftest import sent_emails
from tests.test_scanner import Scanner, scanner_signup

S = "/api/v1/scanner"
A = "/api/v1/admin/scanner/accounts"


def account_id(sc: Scanner) -> str:
    return sc.get(f"{S}/auth/me").json()["account_id"]


def test_access_granted_at_signup(client):
    sc = scanner_signup(client, email="kenza@example.dz")
    r = sc.get(f"{S}/anypulse")
    assert r.status_code == 200, r.text
    assert r.json() == {"state": "active", "revoked_at": None, "revoke_reason": None}
    # No AnyPulse invitation for collection agents.
    assert not [m for m in sent_emails() if m.to_email == "kenza@example.dz"]


def test_no_organization_brand_or_invitation_endpoints(client):
    sc = scanner_signup(client)
    for method, url in (("GET", f"{S}/brands"), ("POST", f"{S}/brands"),
                        ("POST", f"{S}/anypulse/invitation"),
                        ("POST", "/api/v1/integrations/scanner/provision")):
        assert sc.c.request(method, url, headers=sc._h()).status_code in (404, 405), url
    # A Scanner token reads no AnyPulse data.
    for url in ("/api/v1/mentions", "/api/v1/brands", "/api/v1/reports"):
        assert client.get(url, headers={"authorization": f"Bearer {sc.access}"}
                          ).status_code == 401


def test_platform_admin_revokes_and_restores(client, make_user, platform_admin):
    admin = platform_admin(make_user(org="Plateforme"))
    sc = scanner_signup(client, email="fatma@example.dz", full_name="Fatma")
    sc.get(f"{S}/sessions/current")
    me = account_id(sc)

    row = next(r for r in admin.get(A).json() if r["account_id"] == me)
    assert row["email"] == "fatma@example.dz" and row["full_name"] == "Fatma"
    assert row["anypulse_access"] == "active" and row["collections"] == 1

    r = admin.post(f"{A}/{me}/revoke", json={"reason": "  Départ  "})
    assert r.status_code == 200, r.text
    assert r.json()["anypulse_access"] == "revoked" and r.json()["access_revoke_reason"] == "Départ"
    a = sc.get(f"{S}/anypulse").json()
    assert a["state"] == "revoked" and a["revoke_reason"] == "Départ" and a["revoked_at"]
    assert sc.get(f"{S}/sessions/current").status_code == 200   # Scanner itself still works

    r = admin.post(f"{A}/{me}/restore")
    assert r.status_code == 200 and r.json()["anypulse_access"] == "active"
    assert sc.get(f"{S}/anypulse").json() == {"state": "active", "revoked_at": None,
                                              "revoke_reason": None}
    assert admin.post(f"{A}/00000000-0000-0000-0000-000000000000/revoke", json={}
                      ).status_code == 404


def test_revocation_is_audited_in_the_scanner_trail(client, make_user, platform_admin,
                                                   owner_engine):
    admin = platform_admin(make_user(org="Plateforme"))
    sc = scanner_signup(client)
    me = account_id(sc)
    admin.post(f"{A}/{me}/revoke", json={"reason": "Test"})
    with owner_engine.begin() as c:
        rows = c.execute(text("SELECT action, metadata FROM scanner_audit_logs "
                              "WHERE account_id = :a ORDER BY created_at"), {"a": me}).all()
    assert rows[-1].action == "access.revoked" and rows[-1].metadata["reason"] == "Test"


def test_only_platform_admins_change_access(client, make_user):
    sc = scanner_signup(client)
    me = account_id(sc)
    owner = make_user()
    assert owner.get(A).status_code == 403
    assert owner.post(f"{A}/{me}/revoke", json={}).status_code == 403
    # A Scanner token is not an AnyPulse session either.
    r = client.post(f"{A}/{me}/revoke", json={}, headers={"authorization": f"Bearer {sc.access}"})
    assert r.status_code == 401
    assert sc.get(f"{S}/anypulse").json()["state"] == "active"
