from tests.conftest import join


def test_viewer_analyst_admin_permissions(make_user):
    owner = make_user(org="Org")
    viewer = join(owner, make_user(org="v-home"), "viewer")
    analyst = join(owner, make_user(org="a-home"), "analyst")
    brand = owner.post("/api/v1/brands", json={"name": "Cevital"}).json()

    assert viewer.get("/api/v1/brands").status_code == 200
    assert viewer.post("/api/v1/brands", json={"name": "X"}).status_code == 403
    assert viewer.patch(f"/api/v1/brands/{brand['id']}", json={"name": "Y"}).status_code == 403
    assert viewer.get("/api/v1/audit-logs").status_code == 403
    assert viewer.post("/api/v1/invitations", json={"email": "z@example.dz"}).status_code == 403

    assert analyst.post("/api/v1/brands", json={"name": "Ifri"}).status_code == 201
    assert analyst.delete(f"/api/v1/brands/{brand['id']}").status_code == 403
    assert owner.delete(f"/api/v1/brands/{brand['id']}").status_code == 204


def test_role_escalation_rules(make_user):
    owner = make_user(org="Org")
    admin = join(owner, make_user(org="adm-home"), "admin")
    other = join(owner, make_user(org="o-home"), "viewer")
    members = {m["email"]: m["membership_id"] for m in owner.get("/api/v1/members").json()}

    # admin cannot invite or promote anyone to owner
    assert admin.post("/api/v1/invitations",
                      json={"email": "new@example.dz", "role": "owner"}).status_code == 403
    assert admin.patch(f"/api/v1/members/{members[other.email]}",
                       json={"role": "owner"}).status_code == 403
    # admin cannot touch the owner or change their own role
    assert admin.patch(f"/api/v1/members/{members[owner.email]}",
                       json={"role": "viewer"}).status_code == 403
    assert admin.delete(f"/api/v1/members/{members[owner.email]}").status_code == 403
    assert admin.patch(f"/api/v1/members/{members[admin.email]}",
                       json={"role": "owner"}).status_code == 403
    # admin can manage lower roles
    assert admin.patch(f"/api/v1/members/{members[other.email]}",
                       json={"role": "analyst"}).status_code == 200


def test_last_owner_is_protected(make_user):
    owner = make_user(org="Org")
    me = owner.get("/api/v1/members").json()[0]["membership_id"]
    r = owner.delete(f"/api/v1/members/{me}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_owner"


def test_removed_member_loses_access_immediately(make_user):
    owner = make_user(org="Org")
    member = join(owner, make_user(org="m-home"), "analyst")
    assert member.get("/api/v1/brands").status_code == 200
    mid = next(m["membership_id"] for m in owner.get("/api/v1/members").json()
               if m["email"] == member.email)
    assert owner.delete(f"/api/v1/members/{mid}").status_code == 204
    # Access token is still cryptographically valid, but membership is re-checked per request.
    r = member.get("/api/v1/brands")
    assert r.status_code == 401 and r.json()["error"]["code"] == "membership_revoked"


def test_role_change_takes_effect_without_new_token(make_user):
    owner = make_user(org="Org")
    member = join(owner, make_user(org="m-home"), "analyst")
    assert member.post("/api/v1/brands", json={"name": "Before"}).status_code == 201
    mid = next(m["membership_id"] for m in owner.get("/api/v1/members").json()
               if m["email"] == member.email)
    owner.patch(f"/api/v1/members/{mid}", json={"role": "viewer"})
    assert member.post("/api/v1/brands", json={"name": "After"}).status_code == 403
