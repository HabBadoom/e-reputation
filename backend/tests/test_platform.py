"""Milestone 2 platform pieces: job queue, sessions, plans/entitlements, invitations."""

import uuid

import pytest
from sqlalchemy import text

from app.db.session import session_scope
from app.jobs import queue
from app.jobs.handlers import HANDLERS
from app.jobs.worker import run_once
from tests.conftest import PASSWORD, Api, drain, join, register, sent_emails

TOKEN = {"x-auth-mode": "token"}


def _job(owner_engine, job_id):
    with owner_engine.begin() as c:
        return c.execute(text("SELECT * FROM jobs WHERE id = :i"), {"i": job_id}).one()


def _enqueue(kind, payload=None, **kw):
    with session_scope() as db:
        return queue.enqueue(db, kind, payload or {}, **kw)


def _make_due(owner_engine):
    with owner_engine.begin() as c:
        c.execute(text("UPDATE jobs SET run_after = now() WHERE status = 'pending'"))


# ---------------------------------------------------------------- job queue
@pytest.fixture
def flaky_handlers():
    calls = {"flaky": 0}

    def flaky(db, job):
        calls["flaky"] += 1
        if calls["flaky"] == 1:
            raise RuntimeError("temporary outage")

    def broken(db, job):
        raise RuntimeError("always broken")

    HANDLERS["test.flaky"], HANDLERS["test.broken"] = flaky, broken
    yield calls
    HANDLERS.pop("test.flaky")
    HANDLERS.pop("test.broken")


def test_job_retries_with_backoff_then_succeeds(owner_engine, flaky_handlers):
    jid = _enqueue("test.flaky", {"secret": "s"}, sensitive=True)
    run_once(schedule=False)
    j = _job(owner_engine, jid)
    assert j.status == "pending" and j.attempts == 1 and "temporary outage" in j.last_error
    with owner_engine.begin() as c:   # backoff delay is ~30 s
        assert c.execute(text("SELECT run_after > now() + interval '20 seconds' FROM jobs "
                              "WHERE id = :i"), {"i": jid}).scalar_one()
    assert run_once(schedule=False) == 0     # not due yet
    _make_due(owner_engine)
    run_once(schedule=False)
    j = _job(owner_engine, jid)
    assert j.status == "succeeded" and j.attempts == 2
    assert j.payload == {}   # sensitive payload wiped after success


def test_job_dead_letters_after_max_attempts(owner_engine, flaky_handlers):
    jid = _enqueue("test.broken", max_attempts=2)
    run_once(schedule=False)
    _make_due(owner_engine)
    run_once(schedule=False)
    j = _job(owner_engine, jid)
    assert j.status == "dead" and j.attempts == 2 and j.finished_at is not None


def test_unknown_job_kind_is_dead_lettered_immediately(owner_engine):
    jid = _enqueue("test.no_such_handler", max_attempts=5)
    run_once(schedule=False)
    j = _job(owner_engine, jid)
    assert j.status == "dead" and j.attempts == 1 and "no handler" in j.last_error


def test_enqueue_is_transactional(client, owner_engine):
    register(client, email="dup@example.dz")
    with owner_engine.begin() as c:
        before = c.execute(text("SELECT count(*) FROM jobs")).scalar_one()
    r = client.post("/api/v1/auth/register", headers=TOKEN, json={
        "email": "dup@example.dz", "password": PASSWORD, "full_name": "x",
        "organization_name": "Other org"})
    assert r.status_code == 409
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM jobs")).scalar_one() == before


def test_email_delivery_failure_is_retried(client, owner_engine, monkeypatch):
    from app.modules.notifications import email as email_mod
    provider = email_mod.get_email_provider()
    original = provider.send
    state = {"fail": True}

    def flaky_send(msg):
        if state["fail"]:
            raise email_mod.EmailDeliveryError("Brevo returned 503")
        original(msg)

    monkeypatch.setattr(provider, "send", flaky_send)
    register(client, email="retry@example.dz", verify=False)
    drain()
    assert sent_emails() == []
    state["fail"] = False
    _make_due(owner_engine)
    assert [m.to_email for m in sent_emails()] == ["retry@example.dz"]


# ---------------------------------------------------------------- sessions
def test_sessions_list_and_revoke(client):
    first = register(client, email="s@example.dz")
    second = Api(client, client.post("/api/v1/auth/login", headers=TOKEN,
                                     json={"email": "s@example.dz", "password": PASSWORD}).json())
    sessions = first.get("/api/v1/auth/sessions").json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1

    other = next(s["id"] for s in sessions if not s["current"])
    assert first.delete(f"/api/v1/auth/sessions/{other}").status_code == 204
    # The revoked device is cut off immediately, not after its access token expires.
    r = second.get("/api/v1/brands")
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_revoked"
    assert client.post("/api/v1/auth/refresh", headers=TOKEN,
                       json={"refresh_token": second.refresh}).status_code == 401
    assert first.delete(f"/api/v1/auth/sessions/{uuid.uuid4()}").status_code == 404
    assert len(first.get("/api/v1/auth/sessions").json()) == 1


def test_sign_out_everywhere_else_and_logout_is_immediate(client):
    a = register(client, email="e@example.dz")
    others = [Api(client, client.post("/api/v1/auth/login", headers=TOKEN, json={
        "email": "e@example.dz", "password": PASSWORD}).json()) for _ in range(2)]
    assert a.delete("/api/v1/auth/sessions").status_code == 204
    assert all(o.get("/api/v1/brands").status_code == 401 for o in others)
    assert a.get("/api/v1/brands").status_code == 200
    client.post("/api/v1/auth/logout", json={"refresh_token": a.refresh})
    assert a.get("/api/v1/brands").status_code == 401


def test_cannot_see_or_revoke_another_users_sessions(make_user):
    a = make_user(org="Org A")
    b = make_user(org="Org B")
    b_session = b.get("/api/v1/auth/sessions").json()[0]["id"]
    assert all(s["id"] != b_session for s in a.get("/api/v1/auth/sessions").json())
    assert a.delete(f"/api/v1/auth/sessions/{b_session}").status_code == 404
    assert b.get("/api/v1/brands").status_code == 200


# ---------------------------------------------------------------- entitlements
def _set_plan(owner_engine, tenant_id, plan_key):
    with owner_engine.begin() as c:
        c.execute(text("UPDATE subscriptions SET plan_id = (SELECT id FROM plans WHERE key = :k) "
                       "WHERE tenant_id = :t"), {"k": plan_key, "t": tenant_id})


def test_plan_limits_are_enforced(make_user, owner_engine):
    api = make_user(org="Small org")
    _set_plan(owner_engine, api.tenant_id, "trial")   # max 2 brands, 2 projects, 3 members…
    plan = api.get("/api/v1/organization/plan").json()
    assert plan["plan_key"] == "trial" and plan["limits"]["max_brands"] == 2

    b1 = api.post("/api/v1/brands", json={"name": "One"}).json()["id"]
    api.post("/api/v1/brands", json={"name": "Two"})
    r = api.post("/api/v1/brands", json={"name": "Three"})
    assert r.status_code == 402 and r.json()["error"]["code"] == "plan_limit_reached"

    r = api.post("/api/v1/monitoring-projects", json={"name": "Fast", "brand_id": b1,
                                                      "frequency_minutes": 60})
    assert r.status_code == 402   # trial: every 6 h at most
    assert api.post("/api/v1/monitoring-projects", json={
        "name": "Daily", "brand_id": b1, "frequency_minutes": 1440}).status_code == 201

    for i in range(2):   # owner + 2 pending invitations = 3 seats
        assert api.post("/api/v1/invitations",
                        json={"email": f"m{i}@example.dz"}).status_code == 201
    assert api.post("/api/v1/invitations", json={"email": "m9@example.dz"}).status_code == 402
    # Re-inviting someone replaces their pending invite instead of using another seat
    assert api.post("/api/v1/invitations", json={"email": "m0@example.dz"}).status_code == 201

    usage = api.get("/api/v1/organization/plan").json()["usage"]
    assert usage == {"max_brands": 2, "max_monitoring_projects": 1, "max_members": 3,
                     "max_browser_missions_per_day": 0, "max_ai_tasks_per_day": 0,
                     "max_webhooks": 0, "max_mentions_per_month": 0, "max_ai_calls_per_month": 0,
                     "max_reports_per_month": 0}


def test_new_organizations_get_a_subscription(client, owner_engine):
    api = register(client, email="new@example.dz")
    with owner_engine.begin() as c:
        row = c.execute(text("SELECT s.status, p.key FROM subscriptions s JOIN plans p "
                             "ON p.id = s.plan_id WHERE s.tenant_id = :t"),
                        {"t": api.tenant_id}).one()
    assert row.status == "trialing" and row.key == "unlimited"   # test default


# ---------------------------------------------------------------- invitations
def test_invitation_email_and_signup_through_invite(make_user, client):
    owner = make_user(org="Cevital Comms")
    inv = owner.post("/api/v1/invitations", json={"email": "Nadia@Example.dz",
                                                  "role": "analyst"}).json()
    [msg] = [m for m in sent_emails() if m.to_email == "nadia@example.dz"]
    assert "Cevital Comms" in msg.subject or "Cevital Comms" in msg.text
    assert f"/invite?token={inv['token']}" in msg.text

    # Wrong email or bad token: rejected without creating anything
    for body in ({"email": "other@example.dz", "invitation_token": inv["token"]},
                 {"email": "nadia@example.dz", "invitation_token": "x" * 40}):
        r = client.post("/api/v1/auth/register", headers=TOKEN,
                        json={**body, "password": PASSWORD, "full_name": "N"})
        assert r.status_code == 404, r.text
    r = client.post("/api/v1/auth/register", headers=TOKEN,
                    json={"email": "x@example.dz", "password": PASSWORD, "full_name": "N"})
    assert r.status_code == 422   # neither organization nor invitation

    r = client.post("/api/v1/auth/register", headers=TOKEN, json={
        "email": "nadia@example.dz", "password": PASSWORD, "full_name": "Nadia",
        "invitation_token": inv["token"]})
    assert r.status_code == 201, r.text
    nadia = Api(client, r.json())
    me = nadia.get("/api/v1/auth/me").json()
    assert [t["name"] for t in me["tenants"]] == ["Cevital Comms"]   # no extra org created
    assert me["tenant"]["role"] == "analyst"
    assert me["email_verified"] is False   # the inviter also saw the token
    assert owner.get("/api/v1/invitations").json() == []   # consumed
    # Token is single use
    r = client.post("/api/v1/auth/register", headers=TOKEN, json={
        "email": "nadia@example.dz", "password": PASSWORD, "full_name": "Nadia",
        "invitation_token": inv["token"]})
    assert r.status_code == 404


def test_existing_user_still_accepts_via_link(make_user):
    owner = make_user(org="Org One")
    join(owner, make_user(org="Org Two"), "viewer")   # helper asserts the whole flow
    drain()
