"""Aynpulse Scanner, phase 7: sending collections to AnyPulse — attribution by brand names,
delivery to every organisation following the brand, validated / unvalidated marking, the
administrators' review queue, the optional model, idempotent retries and access rules."""

from sqlalchemy import text

from app.core.config import get_settings
from app.nlp.llm import LLMResult
from tests.conftest import drain
from tests.test_scanner import TOKEN, scanner_signup
from tests.test_scanner_extraction import fake  # noqa: F401
from tests.test_scanner_extraction_unit import comment, out, post
from tests.test_scanner_uploads import current, png, upload

S = "/api/v1/scanner"


def _brand(api, name, aliases=()):
    r = api.post("/api/v1/brands", json={"name": name,
                                         "aliases": [{"alias": a} for a in aliases]})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _mentions(owner_engine, tenant_id):
    with owner_engine.begin() as c:
        return [dict(r._mapping) for r in c.execute(text(
            "SELECT brand_id, title, original_text, publisher, collection_method, connector, "
            "verification_status, raw FROM mentions WHERE tenant_id = :t ORDER BY title"),
            {"t": tenant_id})]


def _read(sc, sid, fake, answer, name):  # noqa: F811
    fake.answers.append(answer)
    r = upload(sc, sid, png(name, text=name)).json()
    drain()
    return r["results"][0]["item_id"]                 # the screenshot just added


def _send(sc, sid):
    return sc.post(f"{S}/sessions/{sid}/send")


def test_cannot_send_empty_or_while_reading(client, fake):  # noqa: F811
    sc = scanner_signup(client)
    sid = current(sc)
    r = _send(sc, sid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "nothing_to_send"
    fake.answers.append(out([post()]))
    upload(sc, sid, png("a.png"))                 # queued, not read yet
    r = _send(sc, sid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "still_reading"


def test_delivered_to_every_organisation_following_the_brand(client, fake, make_user,  # noqa: F811
                                                              owner_engine, platform_admin):
    ooredoo, competitor, djezzy = make_user(), make_user(), make_user()
    b1 = _brand(ooredoo, "Ooredoo", ["أوريدو"])
    b2 = _brand(competitor, "Ooredoo")
    _brand(djezzy, "Djezzy")

    sc = scanner_signup(client)
    sid = current(sc)
    named = _read(sc, sid, fake, out(
        [post(author="Ooredoo Algérie", text="Nouvelle offre 5G 🔥")],
        [comment(1, "أوريدو مازلت سما عادي", "H.M"), comment(2, "merci", "Lina", parent=1)]),
        "a.png")
    unnamed = _read(sc, sid, fake, out([post(author="Page X", text="Le réseau est coupé")],
                                       [comment(1, "ndir chkoun", "Nour")]), "b.png")
    assert sc.post(f"{S}/items/{named}/validate").status_code == 200

    r = _send(sc, sid)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued" and r.json()["is_open"] is False
    assert sc.get(f"{S}/sessions/current").json()["id"] != sid      # a new collection starts
    drain()
    hist = sc.get(f"{S}/sessions/{sid}").json()
    assert hist["status"] == "transmitted" and hist["transmitted_at"]

    for api, brand in ((ooredoo, b1), (competitor, b2)):
        ms = _mentions(owner_engine, api.tenant_id)
        assert len(ms) == 3 and {str(m["brand_id"]) for m in ms} == {brand}
        assert {m["verification_status"] for m in ms} == {"scanner_validated"}
        assert {m["collection_method"] for m in ms} == {"scanner"}
        kinds = sorted(m["raw"]["scanner"]["kind"] for m in ms)
        assert kinds == ["comment", "post", "reply"]
        assert {m["publisher"] for m in ms} == {"Ooredoo Algérie", "H.M", "Lina"}
    assert _mentions(owner_engine, djezzy.tenant_id) == []
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM jobs WHERE kind = 'nlp.analyze' "
                              "AND tenant_id IN (:a, :b)"),
                         {"a": ooredoo.tenant_id, "b": competitor.tenant_id}).scalar() >= 2
        att = {str(r.item_id): r for r in c.execute(text(
            "SELECT item_id, status, method, brand_names, organisations "
            "FROM scanner_attributions"))}
        assert c.execute(text("SELECT count(*) FROM scanner_uploaded_files WHERE session_id = :s"
                              " AND retention_until IS NULL"), {"s": sid}).scalar() == 0
    assert att[named].status == "delivered" and att[named].method == "keywords"
    assert att[named].organisations == 2 and att[named].brand_names == ["Ooredoo"]
    assert att[unnamed].status == "to_review"

    # The administrators' queue: only platform administrators.
    assert ooredoo.get("/api/v1/admin/scanner/review").status_code == 403
    admin = platform_admin(make_user())
    q = admin.get("/api/v1/admin/scanner/review").json()
    assert q["total"] == 1 and q["items"][0]["item_id"] == unnamed
    assert q["items"][0]["entries"][0]["text"] == "Le réseau est coupé"
    assert q["items"][0]["validated"] is False
    img = admin.get(f"/api/v1/admin/scanner/items/{unnamed}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    brands = admin.get("/api/v1/admin/scanner/brands").json()
    names = {b["name"]: b["organisations"] for b in brands}
    assert names["Ooredoo"] == 2 and names["Djezzy"] == 1

    r = admin.post(f"/api/v1/admin/scanner/review/{unnamed}", json={"brands": ["Djezzy"]})
    assert r.status_code == 200, r.text
    assert r.json()["mentions_created"] == 2 and r.json()["organisations"] == 1
    ms = _mentions(owner_engine, djezzy.tenant_id)
    assert len(ms) == 2 and {m["verification_status"] for m in ms} == {"scanner_unvalidated"}
    r = admin.post(f"/api/v1/admin/scanner/review/{unnamed}", json={"discard": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_handled"
    assert admin.get("/api/v1/admin/scanner/review").json()["total"] == 0

    # A retried job never delivers twice.
    with owner_engine.begin() as c:
        c.execute(text("UPDATE scanner_collection_sessions SET status = 'retrying' WHERE id = :s"),
                  {"s": sid})
    from app.db.session import session_scope
    from app.jobs import queue
    with session_scope() as db:
        queue.enqueue(db, "scanner.transmit", {"account_id": _account(sc), "session_id": sid})
    drain()
    assert len(_mentions(owner_engine, ooredoo.tenant_id)) == 3


def _account(sc):
    return sc.get(f"{S}/auth/me").json()["account_id"]


def test_discard_and_model_routing(client, fake, make_user, owner_engine,  # noqa: F811
                                   platform_admin, monkeypatch):
    djezzy = make_user()
    _brand(djezzy, "Djezzy")
    answers = [{"brands": ["Djezzy"], "confidence": 0.92, "reason": "network of the page"},
               {"brands": ["Djezzy"], "confidence": 0.4, "reason": "unsure"}]

    class FakeLLM:
        name = "fake"

        def complete_json(self, **kw):
            assert "Djezzy" in kw["user"]
            return LLMResult(answers.pop(0), "fake-router", 100, 20)

    monkeypatch.setattr("app.nlp.llm.get_provider", lambda: FakeLLM())
    monkeypatch.setattr(get_settings(), "scanner_attribution_model", "fake-router")
    sc = scanner_signup(client)
    sid = current(sc)
    sure = _read(sc, sid, fake, out([post(author="Page 1", text="réseau coupé à Oran")]), "1.png")
    unsure = _read(sc, sid, fake, out([post(author="Page 2", text="ça marche pas")]), "2.png")
    _send(sc, sid)
    drain()
    with owner_engine.begin() as c:
        att = {str(r.item_id): r for r in c.execute(text(
            "SELECT item_id, status, method, confidence, brand_names FROM scanner_attributions"))}
    assert att[sure].status == "delivered" and att[sure].method == "ai"
    assert att[unsure].status == "to_review" and att[unsure].brand_names == ["Djezzy"]
    assert len(_mentions(owner_engine, djezzy.tenant_id)) == 1

    admin = platform_admin(make_user())
    assert admin.get("/api/v1/admin/scanner/review").json()["items"][0]["suggested"] == ["Djezzy"]
    r = admin.post(f"/api/v1/admin/scanner/review/{unsure}", json={"discard": True})
    assert r.status_code == 200 and r.json()["status"] == "discarded"
    assert len(_mentions(owner_engine, djezzy.tenant_id)) == 1


def test_revoked_agent_cannot_send(client, fake, make_user, platform_admin):  # noqa: F811
    sc = scanner_signup(client)
    sid = current(sc)
    _read(sc, sid, fake, out([post()]), "a.png")
    admin = platform_admin(make_user())
    assert admin.post(f"/api/v1/admin/scanner/accounts/{_account(sc)}/revoke",
                      json={"reason": "test"}).status_code == 200
    r = _send(sc, sid)
    assert r.status_code == 403 and r.json()["error"]["code"] == "anypulse_access_revoked"


def test_other_agents_cannot_send_or_resend_my_collection(client, fake):  # noqa: F811
    a, b = scanner_signup(client), scanner_signup(client)
    sid = current(a)
    _read(a, sid, fake, out([post()]), "a.png")
    assert b.post(f"{S}/sessions/{sid}/send").status_code == 404
    r = a.post(f"{S}/sessions/{sid}/resend")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_failed"
    h = {"authorization": f"Bearer {a.access}", **TOKEN}
    assert a.c.post(f"{S}/sessions/{sid}/send", headers=h).status_code == 200


def test_scanner_connector_policy_exists(owner_engine):
    with owner_engine.begin() as c:
        row = c.execute(text("SELECT review_status FROM connector_policies "
                             "WHERE key = 'aynpulse_scanner'")).one()
    assert row.review_status == "pending_legal_review"
