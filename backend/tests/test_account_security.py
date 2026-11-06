"""Milestone 1.1: email verification, password reset/change, TOTP MFA."""

import time

from sqlalchemy import text

from app.core import totp
from tests.conftest import PASSWORD, join, outbox, register, token_from_last_email

TOKEN = {"x-auth-mode": "token"}
NEW_PASSWORD = "a brand new passphrase 2026"


def _login(client, email, password=PASSWORD):
    return client.post("/api/v1/auth/login", headers=TOKEN,
                       json={"email": email, "password": password})


# ---------------------------------------------------------------- email verification
def test_registration_sends_localized_verification_email(client):
    r = client.post("/api/v1/auth/register", headers=TOKEN, json={
        "email": "Amina@Example.dz", "password": PASSWORD, "full_name": "Amina <b>",
        "organization_name": "Org Ar", "locale": "ar"})
    assert r.status_code == 201
    [msg] = outbox().outbox
    assert msg.to_email == "amina@example.dz"
    assert msg.subject == "تأكيد بريدك الإلكتروني"
    assert 'dir="rtl"' in msg.html
    assert "Amina &lt;b&gt;" in msg.html  # user input is escaped in HTML
    assert "/verify-email?token=" in msg.text


def test_verify_email_flow(client):
    api = register(client, email="v@example.dz", verify=False)
    assert api.get("/api/v1/auth/me").json()["email_verified"] is False
    tok = token_from_last_email("v@example.dz", "/verify-email")
    assert client.post("/api/v1/auth/email/verify", json={"token": tok}).status_code == 200
    assert api.get("/api/v1/auth/me").json()["email_verified"] is True
    # single use
    r = client.post("/api/v1/auth/email/verify", json={"token": tok})
    assert r.status_code == 404 and r.json()["error"]["code"] == "invalid_token"
    assert api.post("/api/v1/auth/email/resend").status_code == 409


def test_resend_invalidates_previous_link_and_is_rate_limited(client):
    api = register(client, email="r@example.dz", verify=False)
    first = token_from_last_email("r@example.dz", "/verify-email")
    assert api.post("/api/v1/auth/email/resend").status_code == 202
    second = token_from_last_email("r@example.dz", "/verify-email")
    assert first != second
    assert client.post("/api/v1/auth/email/verify", json={"token": first}).status_code == 404
    codes = [api.post("/api/v1/auth/email/resend").status_code for _ in range(6)]
    assert 429 in codes


def test_expired_verification_link(client, owner_engine):
    register(client, email="x@example.dz", verify=False)
    tok = token_from_last_email("x@example.dz", "/verify-email")
    with owner_engine.begin() as c:
        c.execute(text("UPDATE user_tokens SET expires_at = now() - interval '1 second'"))
    assert client.post("/api/v1/auth/email/verify", json={"token": tok}).status_code == 404


def test_unverified_user_cannot_accept_invitation(make_user, client):
    owner = make_user(org="Owner org")
    newbie = register(client, email="newbie@example.dz", org="Newbie org", verify=False)
    inv = owner.post("/api/v1/invitations", json={"email": "newbie@example.dz"}).json()
    r = newbie.post("/api/v1/invitations/accept", json={"token": inv["token"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "email_unverified"


def test_verification_token_cannot_reset_password(client):
    register(client, email="p@example.dz", verify=False)
    tok = token_from_last_email("p@example.dz", "/verify-email")
    r = client.post("/api/v1/auth/password/reset", json={"token": tok, "password": NEW_PASSWORD})
    assert r.status_code == 404


# ---------------------------------------------------------------- password reset
def test_password_reset_flow(client):
    api = register(client, email="reset@example.dz")
    outbox().clear()
    assert client.post("/api/v1/auth/password/forgot",
                       json={"email": "RESET@example.dz"}).status_code == 202
    tok = token_from_last_email("reset@example.dz", "/reset-password")

    r = client.post("/api/v1/auth/password/reset", json={"token": tok, "password": "short"})
    assert r.status_code == 422
    r = client.post("/api/v1/auth/password/reset", json={"token": tok, "password": NEW_PASSWORD})
    assert r.status_code == 204
    # single use
    assert client.post("/api/v1/auth/password/reset",
                       json={"token": tok, "password": NEW_PASSWORD}).status_code == 404
    # all sessions revoked, old password dead, new one works
    assert client.post("/api/v1/auth/refresh", headers=TOKEN,
                       json={"refresh_token": api.refresh}).status_code == 401
    assert _login(client, "reset@example.dz").status_code == 401
    assert _login(client, "reset@example.dz", NEW_PASSWORD).status_code == 200
    # The (short-lived) access token still works; the org audit trail records the reset.
    actions = [e["action"] for e in api.get("/api/v1/audit-logs").json()["items"]]
    assert "auth.password_reset" in actions


def test_forgot_password_does_not_reveal_accounts(client):
    register(client, email="known@example.dz")
    outbox().clear()
    a = client.post("/api/v1/auth/password/forgot", json={"email": "known@example.dz"})
    b = client.post("/api/v1/auth/password/forgot", json={"email": "unknown@example.dz"})
    assert a.status_code == b.status_code == 202
    assert a.json() == b.json()
    assert [m.to_email for m in outbox().outbox] == ["known@example.dz"]


def test_change_password_requires_current_and_revokes_other_sessions(client):
    api = register(client, email="chg@example.dz")
    other = _login(client, "chg@example.dz").json()
    r = api.post("/api/v1/auth/password/change",
                 json={"current_password": "wrong-wrong-wrong", "new_password": NEW_PASSWORD})
    assert r.status_code == 401
    r = api.post("/api/v1/auth/password/change",
                 json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert r.status_code == 200 and r.json()["refresh_token"]
    assert client.post("/api/v1/auth/refresh", headers=TOKEN,
                       json={"refresh_token": other["refresh_token"]}).status_code == 401
    assert client.post("/api/v1/auth/refresh", headers=TOKEN,
                       json={"refresh_token": r.json()["refresh_token"]}).status_code == 200


# ---------------------------------------------------------------- MFA
def _enable_mfa(api):
    setup = api.post("/api/v1/auth/mfa/setup").json()
    secret = setup["secret"]
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    r = api.post("/api/v1/auth/mfa/enable", json={"code": "000000"})
    assert r.status_code == 401
    r = api.post("/api/v1/auth/mfa/enable", json={"code": totp.code_at(secret)})
    assert r.status_code == 200, r.text
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    return secret, codes


def _next_code(secret):
    """Code for the next 30 s step: valid now (drift window) and newer than the code that
    enabled MFA, which the replay guard has already consumed."""
    return totp.code_at(secret, now=time.time() + totp.PERIOD)


def test_mfa_login_flow(client):
    api = register(client, email="mfa@example.dz")
    secret, codes = _enable_mfa(api)
    assert api.get("/api/v1/auth/mfa").json() == {"enabled": True, "recovery_codes_remaining": 10}
    assert api.get("/api/v1/auth/me").json()["mfa_enabled"] is True

    r = _login(client, "mfa@example.dz")
    body = r.json()
    assert r.status_code == 200 and body["mfa_required"] is True
    assert "access_token" not in body and "ri_access" not in r.cookies

    bad = client.post("/api/v1/auth/login/mfa", json={"mfa_token": body["mfa_token"],
                                                      "code": "123456"})
    assert bad.status_code == 401
    # The code used to enable MFA was consumed by the replay guard: use the next step's.
    code = _next_code(secret)
    ok = client.post("/api/v1/auth/login/mfa", headers=TOKEN,
                     json={"mfa_token": body["mfa_token"], "code": code})
    assert ok.status_code == 200, ok.text
    assert ok.json()["access_token"]
    # Challenge is single use
    again = client.post("/api/v1/auth/login/mfa", json={"mfa_token": body["mfa_token"],
                                                        "code": codes[0]})
    assert again.status_code == 404

    # Replay of the same TOTP code on a fresh challenge fails …
    ch = _login(client, "mfa@example.dz").json()["mfa_token"]
    r = client.post("/api/v1/auth/login/mfa", json={"mfa_token": ch, "code": code})
    assert r.status_code == 401
    # … but a recovery code works, exactly once.
    r = client.post("/api/v1/auth/login/mfa", headers=TOKEN,
                    json={"mfa_token": ch, "code": codes[0].upper()})
    assert r.status_code == 200
    ch = _login(client, "mfa@example.dz").json()["mfa_token"]
    assert client.post("/api/v1/auth/login/mfa",
                       json={"mfa_token": ch, "code": codes[0]}).status_code == 401
    assert api.get("/api/v1/auth/mfa").json()["recovery_codes_remaining"] == 9


def test_mfa_challenge_attempts_are_limited(client):
    api = register(client, email="brute@example.dz")
    secret, _ = _enable_mfa(api)
    ch = _login(client, "brute@example.dz").json()["mfa_token"]
    codes = [client.post("/api/v1/auth/login/mfa",
                         json={"mfa_token": ch, "code": f"{i:06d}"}).status_code
             for i in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_mfa_disable_needs_password_and_code(client):
    api = register(client, email="dis@example.dz")
    secret, codes = _enable_mfa(api)
    assert api.post("/api/v1/auth/mfa/disable",
                    json={"password": "wrong-wrong", "code": codes[0]}).status_code == 401
    assert api.post("/api/v1/auth/mfa/disable",
                    json={"password": PASSWORD, "code": "000000"}).status_code == 401
    assert api.post("/api/v1/auth/mfa/disable",
                    json={"password": PASSWORD, "code": codes[1]}).status_code == 204
    assert api.get("/api/v1/auth/mfa").json() == {"enabled": False,
                                                  "recovery_codes_remaining": 0}
    r = _login(client, "dis@example.dz")
    assert r.status_code == 200 and r.json().get("access_token")


def test_mfa_secret_is_encrypted_at_rest(client, owner_engine):
    api = register(client, email="enc@example.dz")
    secret, _ = _enable_mfa(api)
    with owner_engine.begin() as c:
        stored = c.execute(text("SELECT secret_enc FROM user_mfa")).scalar_one()
    assert secret not in stored and stored.startswith("gAAAA")  # Fernet token


def test_mfa_state_is_per_user_not_per_tenant(make_user):
    owner = make_user(org="Org One")
    member = join(owner, make_user(org="Org Two"), "analyst")
    _enable_mfa(member)
    assert owner.get("/api/v1/auth/mfa").json()["enabled"] is False
    members = owner.get("/api/v1/members").json()
    assert all("secret" not in str(m) for m in members)


def test_removed_member_cannot_finish_mfa_login(make_user, client):
    owner = make_user(org="Org One")
    member = join(owner, make_user(org="Org Two"), "viewer")
    secret, codes = _enable_mfa(member)
    ch = client.post("/api/v1/auth/login", headers=TOKEN,
                     json={"email": member.email, "password": PASSWORD,
                           "tenant_id": owner.tenant_id}).json()["mfa_token"]
    mid = next(m["membership_id"] for m in owner.get("/api/v1/members").json()
               if m["email"] == member.email)
    assert owner.delete(f"/api/v1/members/{mid}").status_code == 204
    r = client.post("/api/v1/auth/login/mfa", json={"mfa_token": ch, "code": codes[0]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "membership_revoked"
