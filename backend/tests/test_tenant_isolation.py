"""Mandatory multi-tenant test (master prompt §21), at the API layer.

The same guarantees are tested at the database layer in tests/sql/test_rls_isolation.sql.
Mentions do not exist until Milestone 3; their isolation test is added with that table.
"""

import jwt

from tests.conftest import join


def _brand(api, name, aliases=()):
    r = api.post("/api/v1/brands", json={"name": name, "aliases": [{"alias": a} for a in aliases]})
    assert r.status_code == 201, r.text
    return r.json()


def test_cross_tenant_access_is_denied_everywhere(make_user):
    a = make_user(org="Tenant A")
    b = make_user(org="Tenant B")
    a_brand = _brand(a, "Djezzy", ["جازي", "djezy"])
    b_brand = _brand(b, "Ooredoo", ["اوريدو"])
    bid = b_brand["id"]

    # Lists only show own data
    assert [x["name"] for x in a.get("/api/v1/brands").json()["items"]] == ["Djezzy"]
    assert [x["name"] for x in b.get("/api/v1/brands").json()["items"]] == ["Ooredoo"]
    # Search can't reach the other tenant either (incl. via alias text)
    assert a.get("/api/v1/brands", params={"q": "اوريدو"}).json()["total"] == 0

    # Direct object access by ID → 404 (indistinguishable from "doesn't exist")
    assert a.get(f"/api/v1/brands/{bid}").status_code == 404
    assert a.patch(f"/api/v1/brands/{bid}", json={"name": "pwned"}).status_code == 404
    assert a.delete(f"/api/v1/brands/{bid}").status_code == 404
    assert a.post(f"/api/v1/brands/{bid}/aliases", json={"alias": "x"}).status_code == 404
    alias_id = b_brand["aliases"][0]["id"]
    assert a.delete(f"/api/v1/brands/{bid}/aliases/{alias_id}").status_code == 404
    # A's brand id combined with B's alias id
    assert a.delete(f"/api/v1/brands/{a_brand['id']}/aliases/{alias_id}").status_code == 404

    # B's data is untouched
    assert b.get(f"/api/v1/brands/{bid}").json()["name"] == "Ooredoo"
    assert len(b.get(f"/api/v1/brands/{bid}").json()["aliases"]) == 1

    # Org, members, audit, dashboard: only own tenant
    assert a.get("/api/v1/organization").json()["name"] == "Tenant A"
    assert [m["email"] for m in a.get("/api/v1/members").json()] == [a.email]
    audit = a.get("/api/v1/audit-logs").json()["items"]
    assert audit and all(e["actor_email"] in (a.email, None) for e in audit)
    assert a.get("/api/v1/dashboard/summary").json()["brands_total"] == 1

    # Cannot switch into a tenant you don't belong to
    r = a.post("/api/v1/auth/switch-tenant", json={"tenant_id": b.tenant_id})
    assert r.status_code == 401

    # Cannot log in "into" someone else's tenant
    r = a.c.post("/api/v1/auth/login", headers={"x-auth-mode": "token"},
                 json={"email": a.email, "password": "correct horse battery staple",
                       "tenant_id": b.tenant_id})
    assert r.status_code == 401


def test_cannot_manage_other_tenant_members_or_invitations(make_user):
    a = make_user(org="Org A")
    b = make_user(org="Org B")
    b_member = b.get("/api/v1/members").json()[0]["membership_id"]
    assert a.patch(f"/api/v1/members/{b_member}", json={"role": "viewer"}).status_code == 404
    assert a.delete(f"/api/v1/members/{b_member}").status_code == 404

    inv = b.post("/api/v1/invitations", json={"email": "x@example.dz", "role": "viewer"}).json()
    assert a.delete(f"/api/v1/invitations/{inv['id']}").status_code == 404
    assert a.get("/api/v1/invitations").json() == []


def test_invitation_token_only_works_for_the_invited_email(make_user):
    owner = make_user(org="Owner org")
    intruder = make_user(org="Intruder org")
    inv = owner.post("/api/v1/invitations",
                     json={"email": "someone.else@example.dz", "role": "admin"}).json()
    r = intruder.post("/api/v1/invitations/accept", json={"token": inv["token"]})
    assert r.status_code == 404
    assert [m["email"] for m in owner.get("/api/v1/members").json()] == [owner.email]


def test_forged_tenant_claim_is_rejected(make_user):
    a = make_user(org="Org A")
    b = make_user(org="Org B")
    claims = jwt.decode(a.access, options={"verify_signature": False})
    claims["tid"] = b.tenant_id
    forged = jwt.encode(claims, "not-the-server-secret-" + "y" * 30, algorithm="HS256")
    r = a.c.get("/api/v1/brands", headers={"authorization": f"Bearer {forged}"})
    assert r.status_code == 401
    unsigned = jwt.encode(claims, key="", algorithm="none")
    r = a.c.get("/api/v1/brands", headers={"authorization": f"Bearer {unsigned}"})
    assert r.status_code == 401


def test_multi_tenant_user_sees_only_current_tenant(make_user):
    a = make_user(org="Org A")
    b = make_user(org="Org B")
    _brand(a, "Brand of A")
    _brand(b, "Brand of B")
    join(b, a, "viewer")  # a now belongs to both, currently switched into B
    assert [x["name"] for x in a.get("/api/v1/brands").json()["items"]] == ["Brand of B"]
    me = a.get("/api/v1/auth/me").json()
    assert {t["name"] for t in me["tenants"]} == {"Org A", "Org B"}
    assert me["tenant"]["name"] == "Org B"
    # Members list in B must not include A's other memberships
    assert {m["email"] for m in a.get("/api/v1/members").json()} == {a.email, b.email}

    tenant_a = next(t["id"] for t in me["tenants"] if t["name"] == "Org A")
    r = a.post("/api/v1/auth/switch-tenant", json={"tenant_id": tenant_a})
    assert r.status_code == 200
    a.set_tokens(r.json())
    assert [x["name"] for x in a.get("/api/v1/brands").json()["items"]] == ["Brand of A"]
