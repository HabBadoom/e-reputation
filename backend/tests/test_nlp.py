"""Milestone 4 end to end with a mocked OpenAI API (Chat Completions response format)."""

import hashlib
import json
import uuid

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.core.config import get_settings
from app.db.models import Mention
from app.db.session import session_scope, set_context
from app.nlp import llm
from tests.conftest import drain, join
from tests.test_collection import _run, _setup, internet  # noqa: F401 — fixture reuse


def _annotation(sentiment, about="yes", sarcasm="no", intents=("complaint",)):
    return {"about_target": about, "language": "ar-DZ", "dialect": "algerian",
            "sentiment": sentiment, "sarcasm": sarcasm, "intents": list(intents),
            "emotions": ["frustration"] if sentiment == "negative" else [],
            "topics": ["réseau"], "translation_fr": "traduction", "rationale": "cues",
            "confidence": "high"}


@pytest.fixture
def openai(monkeypatch):
    state = {"status": 200, "by_variant": {}, "arbiter": "negative", "calls": []}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        state["calls"].append({"body": body, "auth": req.headers.get("authorization")})
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": {"message": "nope"}})
        name = body["response_format"]["json_schema"]["name"]
        if name == "arbitration":
            out = {"sentiment": state["arbiter"], "about_target": "yes", "sarcasm": "no",
                   "rationale": "arbitrated"}
        else:
            system, user = body["messages"][0]["content"], body["messages"][1]["content"]
            variant = ("C" if "Be conservative" in system else
                       "B" if "Work carefully" in system else "A")
            sentiment = state["by_variant"].get(variant) or (
                "negative" if ("khayeb" in user or "panne" in user.lower()) else
                "positive" if "mlih" in user else "neutral")
            out = _annotation(sentiment, about="no" if " FC" in user else "yes")
        return httpx.Response(200, json={
            "model": body["model"], "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "choices": [{"message": {"role": "assistant", "content": json.dumps(out),
                                     "refusal": None}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(llm, "openai_client", lambda: client)
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("sk-test"))
    monkeypatch.setattr(get_settings(), "nlp_models", ["test-model"])
    return state


def _brand(api, name="Djezzy"):
    return api.post("/api/v1/brands", json={"name": name}).json()["id"]


def _mention(api, brand_id, title, body, url=None):
    url = url or f"https://news.example.dz/{uuid.uuid4().hex[:8]}"
    with session_scope() as db:
        set_context(db, tenant_id=uuid.UUID(api.tenant_id), user_id=None)
        m = Mention(tenant_id=uuid.UUID(api.tenant_id), brand_id=uuid.UUID(brand_id),
                    entity_type="brand", entity_name="Djezzy", url=url, canonical_url=url,
                    domain="news.example.dz", title=title, original_text=body, snippet=body,
                    collection_method="rss", connector="rss", connector_version="1",
                    verification_status="feed_item",
                    content_hash=hashlib.sha256(url.encode()).hexdigest())
        db.add(m)
        db.flush()
        return str(m.id)


def _analyze(api):
    r = api.post("/api/v1/mentions/analyze")
    assert r.status_code == 202, r.text
    drain()
    return {m["id"]: m for m in api.get("/api/v1/mentions").json()["items"]}


def test_analysis_with_three_annotators(make_user, openai):
    api = make_user()
    b = _brand(api)
    mid = _mention(api, b, "", "wesh khoya, réseau ta3 Djezzy khayeb bzaf lyoum")
    m = _analyze(api)[mid]
    a = m["analysis"]
    assert m["analysed"] is True and a["status"] == "done"
    assert a["sentiment"] == "negative" and a["agreement"] == 1.0 and not a["arbitration_used"]
    assert a["script"] == "arabizi" and a["heuristic_language"] == "ar-DZ"
    assert "bzaf" in a["normalized_text"] and a["intents"] == ["complaint"]

    calls = openai["calls"]
    assert len(calls) == 3                                     # 3 annotators, no arbiter
    body = calls[0]["body"]
    assert calls[0]["auth"] == "Bearer sk-test" and body["model"] == "test-model"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["messages"][1]["content"].startswith("<data>")
    assert '"darija_expressions"' in body["messages"][1]["content"]   # lexicon hints sent

    detail = api.get(f"/api/v1/mentions/{mid}").json()
    assert len(detail["annotators"]) == 3 and detail["models"]["prompt_version"]


def test_disagreement_uses_arbiter(make_user, openai):
    openai["by_variant"] = {"A": "positive", "B": "negative", "C": "neutral"}
    openai["arbiter"] = "negative"
    api = make_user()
    mid = _mention(api, _brand(api), "", "Djezzy, merci pour la 4G… bravo vraiment 🙃")
    a = _analyze(api)[mid]["analysis"]
    assert a["arbitration_used"] and a["sentiment"] == "negative"
    assert len(openai["calls"]) == 4


def test_not_configured_keeps_heuristics_and_invents_nothing(make_user):
    api = make_user()
    mid = _mention(api, _brand(api), "", "واش راك خويا، الريزو خايب بزاف")
    m = _analyze(api)[mid]
    assert m["analysed"] is False
    a = m["analysis"]
    assert a["status"] == "not_configured" and a["sentiment"] is None
    assert a["script"] == "arabic" and a["heuristic_language"] == "ar-DZ"
    dash = api.get("/api/v1/dashboard/summary").json()
    assert dash["sentiment"]["available"] is False


def test_production_requires_ai_provider_approval(make_user, openai, monkeypatch, owner_engine):
    monkeypatch.setattr(get_settings(), "env", "production")
    api = make_user()
    mid = _mention(api, _brand(api), "", "Djezzy khayeb")
    assert _analyze(api)[mid]["analysis"]["status"] == "blocked_by_policy"
    assert openai["calls"] == []


def test_auth_failure_stops_the_batch(make_user, openai):
    openai["status"] = 401
    api = make_user()
    b = _brand(api)
    ids = [_mention(api, b, "", f"Djezzy khayeb {i}") for i in range(3)]
    ms = _analyze(api)
    statuses = sorted(ms[i]["analysis"]["status"] for i in ids)
    assert statuses == ["failed", "pending", "pending"]   # one call, then stop
    assert len(openai["calls"]) == 1


def test_corrections_filters_and_permissions(make_user, openai):
    owner = make_user(org="Org One")
    b = _brand(owner)
    neg = _mention(owner, b, "", "Djezzy khayeb bzaf")
    homonym = _mention(owner, b, "", "Le club Djezzy FC gagne le match, mlih")
    _analyze(owner)
    assert owner.get("/api/v1/mentions", params={"sentiment": "negative"}).json()["total"] == 1
    assert owner.get("/api/v1/mentions", params={"about_target": "no"}).json()["items"][0][
        "id"] == homonym

    r = owner.patch(f"/api/v1/mentions/{neg}/analysis",
                    json={"sentiment": "mixed", "intents": ["complaint", "question"]})
    assert r.status_code == 200
    d = r.json()
    assert d["analysis"]["sentiment"] == "mixed" and d["analysis"]["corrected"] is True
    assert all(x.get("sentiment") == "negative" for x in d["annotators"])   # AI output kept
    assert owner.patch(f"/api/v1/mentions/{neg}/analysis",
                       json={"intents": ["hack"]}).status_code == 422
    assert owner.patch(f"/api/v1/mentions/{neg}/analysis",
                       json={"sentiment": "UNCERTAIN"}).status_code == 422

    viewer = join(owner, make_user(org="Org Two"), "viewer")
    assert viewer.patch(f"/api/v1/mentions/{neg}/analysis",
                        json={"sentiment": "positive"}).status_code == 403
    other = make_user(org="Org Three")
    assert other.patch(f"/api/v1/mentions/{neg}/analysis",
                       json={"sentiment": "positive"}).status_code == 404

    stats = owner.get("/api/v1/dashboard/summary").json()["sentiment_stats"]
    assert stats["analysed"] == 2 and stats["about_brand"] == 1     # homonym excluded
    assert stats["distribution"] == [{"key": "mixed", "count": 1}]
    assert stats["validated"] is False                               # no benchmark yet
    csv_text = owner.get("/api/v1/mentions/export.csv").text
    assert ",mixed," in csv_text


def _corpus_count(owner_engine, where=""):
    with owner_engine.begin() as c:
        return c.execute(text(f"SELECT count(*) FROM language_annotations {where}")).scalar_one()


def test_corpus_only_with_opt_in_and_purged_on_opt_out(make_user, openai, owner_engine):
    api = make_user()
    b = _brand(api)
    _mention(api, b, "", "Djezzy khayeb bzaf")
    _analyze(api)
    assert _corpus_count(owner_engine) == 0                          # not opted in

    assert api.patch("/api/v1/organization", json={"nlp_corpus_opt_in": True}).json()[
        "nlp_corpus_opt_in"] is True
    mid = _mention(api, b, "", "Djezzy mlih bzaf")
    _analyze(api)
    # only the new mention is analysed now (the first was already done before the opt-in)
    assert _corpus_count(owner_engine) == 1
    api.patch(f"/api/v1/mentions/{mid}/analysis", json={"sentiment": "neutral"})
    assert _corpus_count(owner_engine, "WHERE status='tenant_corrected' AND split='train'") == 1

    api.patch("/api/v1/organization", json={"nlp_corpus_opt_in": False})
    assert _corpus_count(owner_engine) == 0


def test_gold_review_and_benchmark(make_user, openai, owner_engine, monkeypatch):
    admin = make_user(org="Platform")
    user = make_user(org="Customer")
    assert admin.get("/api/v1/nlp/corpus/stats").status_code == 403
    with owner_engine.begin() as c:
        c.execute(text("UPDATE users SET is_platform_admin = true WHERE email = :e"),
                  {"e": admin.email})
    assert admin.get("/api/v1/auth/me").json()["is_platform_admin"] is True
    assert user.get("/api/v1/nlp/corpus/queue").status_code == 403

    admin.patch("/api/v1/organization", json={"nlp_corpus_opt_in": True})
    b = _brand(admin)
    for t in ("Djezzy khayeb bzaf", "Djezzy mlih", "Djezzy annonce un communiqué",
              "réseau Djezzy en panne"):
        _mention(admin, b, "", t)
    _analyze(admin)
    assert admin.post("/api/v1/nlp/corpus/sample", json={"n": 4}).json()["assigned"] == 4
    queue = admin.get("/api/v1/nlp/corpus/queue").json()
    assert len(queue) == 4 and queue[0]["auto_labels"]["target"] == "Djezzy"

    for item in queue:
        auto = item["auto_labels"]
        labels = {k: auto[k] for k in ("sentiment", "sarcasm", "about_target", "language")}
        labels["intents"] = auto["intents"]
        if "communiqué" in item["text"]:
            labels["sentiment"] = "positive"      # founder disagrees with the AI here
        r = admin.post(f"/api/v1/nlp/corpus/{item['id']}/verify", json={"labels": labels})
        assert r.status_code == 200
        assert r.json()["status"] == ("human_corrected" if "communiqué" in item["text"]
                                      else "human_verified")
    assert admin.get("/api/v1/nlp/corpus/queue").json() == []

    run = admin.post("/api/v1/nlp/benchmark", json={}).json()
    drain()
    runs = admin.get("/api/v1/nlp/benchmark").json()
    done = next(r for r in runs if r["id"] == run["id"])
    assert done["status"] == "completed", done
    assert done["n_items"] == 4
    sent = done["metrics"]["sentiment"]
    assert sent["accuracy"] == 0.75 and sent["confusion"]["positive"] == {"positive": 1,
                                                                         "neutral": 1}
    assert "language_heuristic_baseline" in done["metrics"]

    status = user.get("/api/v1/nlp/status").json()
    assert status["tasks"]["sentiment"]["validated"] is False      # 4 < 100 gold items
    monkeypatch.setattr(get_settings(), "nlp_min_gold_items", 1)
    monkeypatch.setattr(get_settings(), "nlp_min_macro_f1", 0.5)
    assert user.get("/api/v1/nlp/status").json()["tasks"]["about_target"]["validated"] is True


def test_collection_run_triggers_analysis(make_user, internet, openai):  # noqa: F811
    api = make_user()
    _, pid = _setup(api)
    _run(api, pid)
    items = api.get("/api/v1/mentions").json()["items"]
    assert items and all(m["analysis"]["status"] == "done" for m in items)
    assert any(m["analysis"]["sentiment"] == "negative" for m in items)   # "panne" articles


def test_expression_dictionary_is_readable(make_user):
    api = make_user()
    exprs = {e["canonical"]: e for e in api.get("/api/v1/nlp/expressions").json()}
    assert exprs["bzaf"]["arabic"] == "بزاف" and "bezzaf" in exprs["bzaf"]["variants"]
    assert exprs["hogra"]["polarity"] == "negative"
    assert all(e["status"] == "seed_unvalidated" for e in exprs.values())
