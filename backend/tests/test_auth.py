from tests.conftest import PASSWORD, register

TOKEN = {"x-auth-mode": "token"}


def test_register_login_me(client):
    api = register(client, email="Founder@Example.dz", org="Sonatrach Comms")
    me = api.get("/api/v1/auth/me").json()
    assert me["email"] == "founder@example.dz"
    assert me["tenant"]["role"] == "owner"
    assert "brands:write" in me["permissions"]
    r = client.post("/api/v1/auth/login", headers=TOKEN,
                    json={"email": "FOUNDER@example.dz", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == api.tenant_id


def test_duplicate_email_and_weak_password(client):
    register(client, email="dup@example.dz")
    r = client.post("/api/v1/auth/register", headers=TOKEN,
                    json={"email": "DUP@example.dz", "password": PASSWORD, "full_name": "x",
                          "organization_name": "Other"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"
    r = client.post("/api/v1/auth/register", headers=TOKEN,
                    json={"email": "weak@example.dz", "password": "short", "full_name": "x",
                          "organization_name": "Org"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_bad_credentials_are_indistinguishable(client):
    register(client, email="real@example.dz")
    wrong_pw = client.post("/api/v1/auth/login", headers=TOKEN,
                           json={"email": "real@example.dz", "password": "nope-nope-nope"})
    no_user = client.post("/api/v1/auth/login", headers=TOKEN,
                          json={"email": "ghost@example.dz", "password": "nope-nope-nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["error"]["message"] == no_user.json()["error"]["message"]


def test_refresh_rotation_and_reuse_detection(client):
    api = register(client)
    first = api.refresh
    r = client.post("/api/v1/auth/refresh", headers=TOKEN, json={"refresh_token": first})
    assert r.status_code == 200
    second = r.json()["refresh_token"]
    assert second != first
    # Replaying the rotated-out token is treated as theft: family revoked.
    r = client.post("/api/v1/auth/refresh", headers=TOKEN, json={"refresh_token": first})
    assert r.status_code == 401 and r.json()["error"]["code"] == "refresh_reuse"
    r = client.post("/api/v1/auth/refresh", headers=TOKEN, json={"refresh_token": second})
    assert r.status_code == 401


def test_logout_revokes_refresh(client):
    api = register(client)
    assert client.post("/api/v1/auth/logout", json={"refresh_token": api.refresh}).status_code == 204
    r = client.post("/api/v1/auth/refresh", headers=TOKEN, json={"refresh_token": api.refresh})
    assert r.status_code == 401


def test_cookie_flow_requires_csrf(client):
    r = client.post("/api/v1/auth/register",
                    json={"email": "cookie@example.dz", "password": PASSWORD, "full_name": "C",
                          "organization_name": "Cookie Org"})
    assert r.status_code == 201
    assert r.json()["access_token"] is None  # browsers never see tokens
    assert "ri_access" in client.cookies and "ri_csrf" in client.cookies

    assert client.get("/api/v1/auth/me").status_code == 200  # safe method: no CSRF needed
    r = client.post("/api/v1/brands", json={"name": "NoCsrf"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = client.post("/api/v1/brands", json={"name": "WithCsrf"},
                    headers={"x-csrf-token": "wrong"})
    assert r.status_code == 403
    r = client.post("/api/v1/brands", json={"name": "WithCsrf"},
                    headers={"x-csrf-token": client.cookies["ri_csrf"]})
    assert r.status_code == 201


def test_unauthenticated_and_garbage_tokens(client):
    assert client.get("/api/v1/brands").status_code == 401
    r = client.get("/api/v1/brands", headers={"authorization": "Bearer garbage"})
    assert r.status_code == 401
    assert r.json()["error"]["request_id"]


def test_login_rate_limit(client):
    register(client, email="rl@example.dz")
    codes = [client.post("/api/v1/auth/login", headers=TOKEN,
                         json={"email": "rl@example.dz", "password": "wrong-wrong-wrong"}
                         ).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10
    assert 429 in codes[10:]


def test_disabled_user_loses_access(client, owner_engine):
    from sqlalchemy import text
    api = register(client, email="disable@example.dz")
    with owner_engine.begin() as c:
        c.execute(text("UPDATE users SET status='disabled' WHERE email='disable@example.dz'"))
    assert api.get("/api/v1/brands").status_code == 401  # existing access token stops working
    r = client.post("/api/v1/auth/login", headers=TOKEN,
                    json={"email": "disable@example.dz", "password": PASSWORD})
    assert r.status_code == 401
