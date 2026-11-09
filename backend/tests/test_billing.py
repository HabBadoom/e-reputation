"""Milestone 8: plans, usage metering and quotas, billing details, manual invoices, admin console,
daily maintenance (trial expiry, retention) and the metrics endpoint."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.billing.maintenance import run_daily
from app.core.config import get_settings
from tests.conftest import drain, join, outbox, sent_emails
from tests.test_collection import _run, _setup, internet  # noqa: F401 — fixture reuse
from tests.test_intelligence import hooks  # noqa: F401 — fixture reuse
from tests.test_nlp import _analyze, _brand, _mention, openai  # noqa: F401 — fixture reuse

ACCOUNT = {"legal_name": "SPA Exemple", "address": "Route de Ouled Fayet", "city": "Alger",
           "rc": "16/00-0000000B00", "nif": "000016000000000", "nis": "000016000000000",
           "article_imposition": "16000000000", "email": "compta@exemple.dz"}
LINES = [{"description": "Abonnement", "quantity": 1, "unit_minor": 4_500_000},
         {"description": "Formation", "quantity": 2, "unit_minor": 1_500_000}]


@pytest.fixture
def seller(monkeypatch):
    s = get_settings()
    for k, v in {"billing_seller_name": "RepIntel SARL", "billing_seller_address": "Alger",
                 "billing_seller_rc": "16/00-1", "billing_seller_nif": "0012",
                 "billing_seller_nis": "0013", "billing_seller_article_imposition": "16",
                 "billing_seller_bank": "RIB 004"}.items():
        monkeypatch.setattr(s, k, v)


def _admin_and_customer(make_user, platform_admin):
    admin = platform_admin(make_user(org="Platform"))
    customer = make_user(org="Client SPA")
    return admin, customer


# ------------------------------------------------------------------------- access
def test_admin_console_is_for_platform_admins_with_mfa(make_user, owner_engine, monkeypatch):
    user = make_user()
    assert user.get("/api/v1/admin/stats").status_code == 403
    with owner_engine.begin() as c:
        c.execute(text("UPDATE users SET is_platform_admin = true WHERE email = :e"),
                  {"e": user.email})
    monkeypatch.setattr(get_settings(), "admin_require_mfa", True)
    r = user.get("/api/v1/admin/stats")
    assert r.status_code == 403 and r.json()["error"]["code"] == "mfa_required"
    monkeypatch.setattr(get_settings(), "admin_require_mfa", False)
    stats = user.get("/api/v1/admin/stats")
    assert stats.status_code == 200 and stats.json()["tenants"] >= 1


def test_billing_permissions(make_user, client):
    owner = make_user()
    viewer = join(owner, make_user(), "viewer")
    analyst = join(owner, make_user(), "analyst")
    assert viewer.get("/api/v1/billing").status_code == 403
    assert analyst.get("/api/v1/billing").status_code == 403
    assert analyst.put("/api/v1/billing/account", json=ACCOUNT).status_code == 403
    r = owner.get("/api/v1/billing")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "manual" and body["plan"]["active"] is True
    assert set(body["missing_fields"]) == {"legal_name", "address", "rc", "nif", "nis",
                                           "article_imposition"}
    assert body["usage_month"]["mentions_collected"] == 0 and body["invoices"] == []


# ----------------------------------------------------------------------- invoices
def test_invoice_lifecycle(make_user, platform_admin, seller, owner_engine):
    pytest.importorskip("playwright")
    admin, customer = _admin_and_customer(make_user, platform_admin)
    other = make_user(org="Autre")
    base = f"/api/v1/admin/tenants/{customer.tenant_id}/invoices"

    draft = admin.post(base, json={"lines": LINES}).json()
    assert draft["status"] == "draft" and draft["number"] is None
    assert draft["subtotal_minor"] == 7_500_000 and draft["tax_minor"] == 1_425_000
    assert draft["total_minor"] == 8_925_000
    # Drafts are invisible to the customer
    assert customer.get("/api/v1/billing").json()["invoices"] == []
    assert customer.get(f"/api/v1/billing/invoices/{draft['id']}/pdf").status_code == 404

    preview = admin.get(f"{base}/{draft['id']}/pdf")          # admins can preview drafts
    assert preview.status_code == 200 and preview.content[:5] == b"%PDF-"

    # Issuing needs the customer's legal identifiers
    r = admin.post(f"{base}/{draft['id']}/issue")
    assert r.status_code == 422 and r.json()["error"]["code"] == "billing_account_incomplete"
    acct = customer.put("/api/v1/billing/account", json=ACCOUNT)
    assert acct.status_code == 200 and acct.json()["missing_fields"] == []

    edited = admin.put(f"{base}/{draft['id']}", json={"lines": LINES[:1]}).json()
    assert edited["total_minor"] == 5_355_000
    issued = admin.post(f"{base}/{draft['id']}/issue").json()
    year = datetime.now(UTC).year
    assert issued["status"] == "issued" and issued["number"] == f"FAC-{year}-00001"
    assert issued["amount_in_words"].startswith("Cinquante-trois mille")
    assert issued["due_date"] > issued["issue_date"]

    # Issued invoices are immutable, through the API and in the database
    r = admin.put(f"{base}/{draft['id']}", json={"lines": LINES})
    assert r.status_code == 422
    with pytest.raises(Exception, match="immutable"), owner_engine.begin() as c:
        c.execute(text("UPDATE invoices SET total_minor = 1 WHERE id = :i"), {"i": draft["id"]})

    second = admin.post(base, json={"lines": LINES}).json()
    assert admin.post(f"{base}/{second['id']}/issue").json()["number"] == f"FAC-{year}-00002"

    # A deleted draft does not consume a number
    third = admin.post(base, json={"lines": LINES}).json()
    assert admin.post(f"{base}/{third['id']}/void", json={"reason": "erreur"}
                      ).json()["status"] == "deleted"

    paid = admin.post(f"{base}/{draft['id']}/paid", json={"method": "ccp", "reference": "V-1"})
    assert paid.json()["status"] == "paid" and paid.json()["payment_method"] == "ccp"
    assert admin.post(f"{base}/{draft['id']}/paid", json={"method": "ccp"}).status_code == 422
    voided = admin.post(f"{base}/{second['id']}/void", json={"reason": "annulée"}).json()
    assert voided["status"] == "void" and voided["number"] == f"FAC-{year}-00002"

    listed = customer.get("/api/v1/billing").json()["invoices"]
    assert [i["number"] for i in sorted(listed, key=lambda i: i["number"])] == [
        f"FAC-{year}-00001", f"FAC-{year}-00002"]
    pdf = customer.get(f"/api/v1/billing/invoices/{draft['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    assert other.get(f"/api/v1/billing/invoices/{draft['id']}/pdf").status_code == 404

    # Everything the platform did is in the customer's own audit log
    with owner_engine.begin() as c:
        actions = set(c.execute(text("SELECT action FROM audit_logs WHERE tenant_id = :t"),
                                {"t": customer.tenant_id}).scalars())
    assert {"platform.invoice_drafted", "platform.invoice_issued", "platform.invoice_paid",
            "platform.invoice_voided", "platform.invoice_deleted"} <= actions


def test_customers_cannot_write_invoices_or_subscriptions(make_user, client):
    from app.db.session import session_scope, set_context
    api = make_user()
    tid = uuid.UUID(api.tenant_id)
    with session_scope() as db:   # the application's role, as the tenant
        set_context(db, tenant_id=tid, user_id=None)
        with pytest.raises(Exception, match="platform administrators"):
            db.execute(text("INSERT INTO invoices (id, tenant_id, status, currency) VALUES "
                            "(gen_random_uuid(), :t, 'draft', 'DZD')"), {"t": tid})
    with session_scope() as db:
        set_context(db, tenant_id=tid, user_id=None)
        with pytest.raises(Exception, match="platform administrators"):
            db.execute(text("UPDATE subscriptions SET status = 'active' WHERE tenant_id = :t"),
                       {"t": tid})


def test_draft_from_subscription_uses_the_plan_price(make_user, platform_admin, custom_plan):
    admin, customer = _admin_and_customer(make_user, platform_admin)
    key = custom_plan(customer, status="active")
    base = f"/api/v1/admin/tenants/{customer.tenant_id}/invoices"
    r = admin.post(base, json={"from_subscription": True})
    assert r.status_code == 422 and "No active month price" in r.json()["error"]["message"]

    admin.post(f"/api/v1/admin/plans/{key}/prices",
               json={"currency": "DZD", "billing_interval": "month", "amount_minor": 1_000_000})
    plan = admin.post(f"/api/v1/admin/plans/{key}/prices", json={
        "currency": "DZD", "billing_interval": "month", "amount_minor": 2_000_000}).json()
    assert [p["amount_minor"] for p in plan["prices"]] == [2_000_000]   # replaced, not added
    inv = admin.post(base, json={"from_subscription": True}).json()
    assert inv["subtotal_minor"] == 2_000_000 and inv["total_minor"] == 2_380_000
    assert inv["lines"][0]["description"].startswith(f"Abonnement {key} (mensuel)")


# --------------------------------------------------------------- plans & requests
def test_plan_catalogue_requests_and_subscription_change(make_user, platform_admin,
                                                         custom_plan, monkeypatch, owner_engine):
    monkeypatch.setattr(get_settings(), "platform_notify_emails", ["ops@repintel.example"])
    admin, customer = _admin_and_customer(make_user, platform_admin)
    current = custom_plan(customer)
    target = custom_plan(make_user(org="Placeholder"))
    assert customer.post("/api/v1/billing/plan-request",
                         json={"plan_key": target}).status_code == 404     # not public yet

    p = admin.patch(f"/api/v1/admin/plans/{target}", json={
        "is_public": True, "description": "Pour les équipes"}).json()
    assert p["is_public"] is True
    r = admin.put(f"/api/v1/admin/plans/{target}/entitlements", json={"max_bogus": 3})
    assert r.status_code == 422
    assert admin.put(f"/api/v1/admin/plans/{target}/entitlements",
                     json={"max_brands": -1}).status_code == 422
    admin.put(f"/api/v1/admin/plans/{target}/entitlements", json={"max_brands": 7})
    catalogue = {x["key"]: x for x in customer.get("/api/v1/billing").json()["plans"]}
    assert catalogue[target]["limits"]["max_brands"] == 7
    assert catalogue[target]["description"] == "Pour les équipes"
    assert current not in catalogue

    outbox().clear()
    r = customer.post("/api/v1/billing/plan-request",
                      json={"plan_key": target, "billing_interval": "year", "message": "Merci"})
    assert r.status_code == 201
    assert customer.post("/api/v1/billing/plan-request",
                         json={"plan_key": target}).status_code == 409
    mails = [m for m in sent_emails() if m.to_email == "ops@repintel.example"]
    assert len(mails) == 1 and "Client SPA" in mails[0].text
    assert customer.delete("/api/v1/billing/plan-request").status_code == 204
    assert customer.delete("/api/v1/billing/plan-request").status_code == 404
    customer.post("/api/v1/billing/plan-request", json={"plan_key": target})

    tenants = {t["tenant_id"]: t for t in admin.get("/api/v1/admin/tenants").json()}
    assert tenants[customer.tenant_id]["pending_request"] is True
    sub = admin.patch(f"/api/v1/admin/tenants/{customer.tenant_id}/subscription", json={
        "plan_key": target, "status": "active", "billing_interval": "year", "currency": "DZD"})
    assert sub.status_code == 200 and sub.json()["plan_key"] == target
    detail = admin.get(f"/api/v1/admin/tenants/{customer.tenant_id}").json()
    assert [r["status"] for r in detail["requests"]][:1] == ["done"]
    billing = customer.get("/api/v1/billing").json()
    assert billing["plan"]["key"] == target and billing["pending_request"] is None
    assert billing["plan"]["limits"]["max_brands"] == 7
    with owner_engine.begin() as c:
        meta = c.execute(text("SELECT meta FROM audit_logs WHERE tenant_id = :t AND action = "
                              "'platform.subscription_changed'"),
                         {"t": customer.tenant_id}).scalar_one()
    assert meta["before"]["plan_key"] == current and meta["after"]["plan_key"] == target


def test_connector_review_is_recorded(make_user, platform_admin, owner_engine):
    admin = platform_admin(make_user())
    before = owner_engine.connect().execute(text(
        "SELECT review_status, review_notes FROM connector_policies WHERE key = 'rss'")).one()
    try:
        r = admin.patch("/api/v1/admin/connectors/rss", json={
            "review_status": "approved", "review_notes": "Avis juridique du 01/11 (loi 18-07)"})
        assert r.status_code == 200
        assert r.json()["reviewed_by"] == admin.email and r.json()["review_status"] == "approved"
        assert admin.patch("/api/v1/admin/connectors/nope", json={
            "review_status": "approved"}).status_code == 404
    finally:
        with owner_engine.begin() as c:
            c.execute(text("UPDATE connector_policies SET review_status = :s, review_notes = :n, "
                           "reviewed_by = NULL, reviewed_at = NULL WHERE key = 'rss'"),
                      {"s": before.review_status, "n": before.review_notes})


# ---------------------------------------------------------------- quotas & usage
def test_expired_trial_stops_new_work_but_keeps_data(make_user, custom_plan, owner_engine):
    api = make_user(org="Essai")
    b = _brand(api)
    custom_plan(api)
    with owner_engine.begin() as c:
        c.execute(text("UPDATE subscriptions SET trial_ends_at = now() - interval '1 day' "
                       "WHERE tenant_id = :t"), {"t": api.tenant_id})
    outbox().clear()
    stats = run_daily()
    assert stats["trials_expired"] == 1
    mails = [m for m in sent_emails() if m.to_email == api.email]
    assert any("trial_expired" in m.tags for m in mails)

    plan = api.get("/api/v1/billing").json()["plan"]
    assert plan["status"] == "expired" and plan["active"] is False
    r = api.post("/api/v1/brands", json={"name": "Ooredoo"})
    assert r.status_code == 402 and r.json()["error"]["code"] == "subscription_inactive"
    assert api.get(f"/api/v1/brands/{b}").status_code == 200       # data stays readable
    assert run_daily()["trials_expired"] == 0                      # idempotent


def test_monthly_report_quota(make_user, custom_plan):
    api = make_user()
    custom_plan(api, max_reports_per_month=1)
    r = api.post("/api/v1/reports", json={"kind": "monthly", "format": "csv"})
    assert r.status_code in (201, 202), r.text
    drain()
    r = api.post("/api/v1/reports", json={"kind": "monthly", "format": "csv"})
    assert r.status_code == 402 and r.json()["error"]["code"] == "plan_limit_reached"
    usage = api.get("/api/v1/billing").json()
    assert usage["usage"]["max_reports_per_month"] == 1
    assert usage["usage_month"]["reports_generated"] == 1


def test_webhook_limit(make_user, custom_plan, hooks):  # noqa: F811
    api = make_user()
    custom_plan(api, max_webhooks=1)
    assert api.post("/api/v1/webhooks", json={"name": "a", "url": "https://hooks.example.dz/a"}
                    ).status_code == 201
    r = api.post("/api/v1/webhooks", json={"name": "b", "url": "https://hooks.example.dz/b"})
    assert r.status_code == 402


def test_ai_quota_stops_analysis_without_inventing(make_user, custom_plan, openai):  # noqa: F811
    api = make_user()
    # A mention needs room for 3 annotators + a possible arbiter (4 calls) before it starts.
    custom_plan(api, max_ai_calls_per_month=5)
    b = _brand(api)
    first = _mention(api, b, "", "réseau Djezzy en panne")
    second = _mention(api, b, "", "Djezzy mlih")
    out = _analyze(api)
    states = sorted(m["analysis"]["status"] for m in (out[first], out[second]))
    assert states == ["done", "pending"]
    pending = next(m for m in (out[first], out[second]) if m["analysis"]["status"] == "pending")
    assert pending["analysis"]["sentiment"] is None and pending["analysed"] is False
    assert len(openai["calls"]) == 3
    month = api.get("/api/v1/billing").json()["usage_month"]
    assert month["ai_calls"] == 3 and month["ai_tokens"] == 45


def test_mention_quota_caps_collection(make_user, custom_plan, internet):  # noqa: F811
    api = make_user()
    custom_plan(api, max_mentions_per_month=2)
    _, pid = _setup(api)
    run = _run(api, pid)
    assert run["status"] == "completed", run
    assert run["summary"]["mention_quota_reached"] is True
    assert run["summary"]["mentions_collected"] == 2
    month = api.get("/api/v1/billing").json()["usage_month"]
    assert month["mentions_collected"] == 2 and month["search_queries"] > 0
    assert _run(api, pid)["summary"]["mentions_collected"] == 0


def test_retention_purge_follows_the_plan(make_user, custom_plan, owner_engine):
    api = make_user()
    other = make_user(org="Autre")
    custom_plan(api, max_retention_days=30)
    b, ob = _brand(api), _brand(other)
    old, recent = _mention(api, b, "", "ancien"), _mention(api, b, "", "récent")
    other_old = _mention(other, ob, "", "ancien ailleurs")
    with owner_engine.begin() as c:
        c.execute(text("UPDATE mentions SET collected_at = now() - interval '45 days' "
                       "WHERE id IN (:a, :b)"), {"a": old, "b": other_old})
    stats = run_daily()
    assert stats["purged"][api.tenant_id]["mentions"] == 1
    assert stats["purged"][api.tenant_id]["retention_days"] == 30
    ids = {m["id"] for m in api.get("/api/v1/mentions").json()["items"]}
    assert ids == {recent}
    # The unlimited plan keeps data up to REPINTEL_RETENTION_MAX_DAYS (730 by default)
    assert other_old in {m["id"] for m in other.get("/api/v1/mentions").json()["items"]}


def test_daily_maintenance_is_scheduled_once(owner_engine):
    from app.jobs.worker import run_once
    run_once(schedule=True)
    run_once(schedule=True)
    with owner_engine.begin() as c:
        n = c.execute(text("SELECT count(*) FROM jobs WHERE kind = 'platform.daily'")).scalar_one()
    assert n == 1


# ----------------------------------------------------------------------- metrics
def test_metrics_endpoint_needs_its_token(client, make_user, monkeypatch):
    make_user()
    assert client.get("/metrics").status_code == 404           # disabled by default
    monkeypatch.setattr(get_settings(), "metrics_token", SecretStr("m" * 32))
    assert client.get("/metrics").status_code == 404
    assert client.get("/metrics", headers={"authorization": "Bearer wrong"}).status_code == 404
    r = client.get("/metrics", headers={"authorization": "Bearer " + "m" * 32})
    assert r.status_code == 200 and "repintel_tenants 1.0" in r.text
    assert 'repintel_subscriptions{status="trialing"}' in r.text
    assert "@" not in r.text                                   # aggregates only


def test_period_dates_are_not_invented(make_user, platform_admin):
    admin, customer = _admin_and_customer(make_user, platform_admin)
    inv = admin.post(f"/api/v1/admin/tenants/{customer.tenant_id}/invoices",
                     json={"lines": LINES}).json()
    assert inv["period_start"] is None and inv["issue_date"] is None
    assert inv["amount_in_words"] in ("", None)
