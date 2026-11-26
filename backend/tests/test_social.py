"""Social sources (0019): Apify (third-party provider, logged out) and the YouTube Data API.

Providers are replaced by fake HTTP servers that answer with the documented output formats.
These tests prove the pipeline (inputs, caps, parsing, attribution, threads, dedup, quotas,
isolation); they do not prove that the live actors still return these formats — every sync
records how many items it could not read for that reason.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.connectors import apify as apify_mod
from app.connectors import youtube as youtube_mod
from app.connectors.apify import build_input, parse
from app.core.config import get_settings
from tests.conftest import drain, join

OLD = (datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
GROUP = "https://www.facebook.com/groups/algerie.telecom.users/"


def _post(pid: str, body: str, *, comments=None, when=OLD, likes=12) -> dict:
    return {"facebookUrl": GROUP, "url": f"{GROUP}posts/{pid}/", "time": when,
            "user": {"id": f"u{pid}", "name": f"Auteur {pid}"}, "text": body, "id": f"Z{pid}",
            "legacyId": pid, "likesCount": likes, "commentsCount": len(comments or []) or 3,
            "sharesCount": 1, "groupTitle": "Algérie Télécom users", "topComments": comments or []}


GROUP_ROWS = [
    _post("101", "Réseau Djezzy coupé à Oran depuis ce matin", comments=[
        {"id": "c1", "commentUrl": f"{GROUP}posts/101/?comment_id=c1", "date": OLD,
         "text": "pareil chez moi", "profileName": "Amine", "profileId": "p1",
         "profileUrl": "https://www.facebook.com/p1", "likesCount": "4"}]),
    _post("102", "Quelqu'un a testé la fibre ?", comments=[
        {"id": "c2", "date": OLD, "text": "Djezzy mlih bezaf", "profileName": "Sara"}]),
    _post("103", "Djezzy FC a gagné hier"),          # excluded by the brand's exclusion term
    {"error": "post not available"},                  # unreadable item: counted, never guessed
]
COMMENT_ROWS = [
    {"facebookUrl": f"{GROUP}posts/101/", "commentUrl": f"{GROUP}posts/101/?comment_id=c9",
     "id": "c9", "text": "toujours en panne", "date": OLD, "profileName": "Karim",
     "profileId": "p9", "likesCount": 2, "threadingDepth": 0,
     "comments": [{"id": "c10", "text": "oui", "date": OLD, "profileName": "Nadia",
                   "threadingDepth": 1}]},
    {"facebookUrl": f"{GROUP}posts/101/", "id": "c1", "text": "pareil chez moi", "date": OLD,
     "profileName": "Amine"},                         # already stored from topComments
]


@pytest.fixture
def apify(monkeypatch):
    state = {"rows": {"apify~facebook-groups-scraper": GROUP_ROWS,
                      "apify~facebook-comments-scraper": COMMENT_ROWS},
             "status": "SUCCEEDED", "start_status": 201, "runs": {}, "calls": []}

    def handler(req: httpx.Request) -> httpx.Response:
        state["calls"].append(req)
        assert req.headers["authorization"] == "Bearer apify-test-token"
        path = req.url.path
        if req.method == "POST" and path.startswith("/v2/acts/") and path.endswith("/runs"):
            if state["start_status"] != 201:
                return httpx.Response(state["start_status"], json={"error": {}})
            actor = path.split("/")[3]
            rid = f"run{len(state['runs']) + 1}"
            state["runs"][rid] = {"actor": actor, "input": json.loads(req.content),
                                  "params": dict(req.url.params)}
            return httpx.Response(201, json={"data": {"id": rid, "status": "READY",
                                                      "defaultDatasetId": f"ds-{rid}"}})
        if req.method == "GET" and path.startswith("/v2/actor-runs/"):
            rid = path.split("/")[3]
            return httpx.Response(200, json={"data": {"id": rid, "status": state["status"],
                                                      "defaultDatasetId": f"ds-{rid}",
                                                      "usageTotalUsd": 0.0123}})
        if req.method == "GET" and path.startswith("/v2/datasets/"):
            rid = path.split("/")[3].removeprefix("ds-")
            run = state["runs"][rid]
            rows = state["rows"].get(run["actor"], [])
            limit = int(run["params"]["maxItems"])
            off = int(req.url.params.get("offset", 0))
            return httpx.Response(200, json=rows[:limit][off:off + int(req.url.params["limit"])])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(apify_mod, "apify_client", lambda: client)
    s = get_settings()
    monkeypatch.setattr(s, "apify_token", SecretStr("apify-test-token"))
    monkeypatch.setattr(s, "apify_poll_seconds", 0)
    return state


def _project(api, *, competitors=False):
    b = api.post("/api/v1/brands", json={"name": "Djezzy", "aliases": [{"alias": "جازي"}]}).json()
    api.post(f"/api/v1/brands/{b['id']}/keywords", json={"expression": "djezzy fc",
                                                         "kind": "exclude"})
    p = api.post("/api/v1/monitoring-projects", json={
        "name": "Veille", "brand_id": b["id"], "languages": ["fr", "ar"],
        "include_competitors": competitors}).json()
    return b["id"], p["id"]


def _add(api, pid, **body):
    r = api.post("/api/v1/social-sources", json={"project_id": pid, **body})
    assert r.status_code == 201, r.text
    return r.json()


def _sync(api, sid):
    r = api.post(f"/api/v1/social-sources/{sid}/sync")
    assert r.status_code == 202, r.text
    drain()
    return api.get(f"/api/v1/social-sources/{sid}/syncs").json()


def _mentions(api):
    return api.get("/api/v1/mentions", params={"limit": 100, "include_reposts": True}
                   ).json()["items"]


# --------------------------------------------------------------------------- sources
def test_social_source_validation_and_listing(make_user, apify):
    api = make_user()
    brand_id, pid = _project(api)
    bad = api.post("/api/v1/social-sources", json={
        "project_id": pid, "kind": "facebook_group", "url": "https://www.facebook.com/djezzy"})
    assert bad.status_code == 422
    assert api.post("/api/v1/social-sources", json={
        "project_id": pid, "kind": "facebook_page",
        "url": "https://evil.example/facebook.com"}).status_code == 422
    g = _add(api, pid, kind="facebook_group", url="https://m.facebook.com/groups/algerie.telecom.users?ref=x")
    assert g["url"] == GROUP and g["provider"] == "apify" and g["platform"] == "facebook"
    dup = api.post("/api/v1/social-sources", json={"project_id": pid, "kind": "facebook_group",
                                                   "url": GROUP})
    assert dup.status_code == 409
    t = _add(api, pid, kind="tiktok_hashtag", query="#OoredooDZ")
    assert t["url"] == "https://www.tiktok.com/tag/OoredooDZ" and t["query"] == "OoredooDZ"
    y = _add(api, pid, kind="youtube_channel", url="https://www.youtube.com/@djezzy",
             match_mode="brand")
    assert y["url"] == "https://www.youtube.com/@djezzy" and y["provider"] == "youtube"
    r = _add(api, pid, kind="reddit_community", query="r/algeria")
    assert r["url"] == "https://www.reddit.com/r/algeria/"
    assert api.post("/api/v1/social-sources", json={
        "project_id": pid, "kind": "linkedin_company",
        "url": "https://www.linkedin.com/in/someone"}).status_code == 422
    # A second project may follow the same group
    p2 = api.post("/api/v1/monitoring-projects", json={"name": "Veille 2",
                                                       "brand_id": brand_id}).json()
    _add(api, p2["id"], kind="facebook_group", url=GROUP)

    overview = api.get("/api/v1/social-sources", params={"project_id": pid}).json()
    assert len(overview["sources"]) == 4
    assert {p["key"] for p in overview["providers"]} == {"apify", "youtube"}
    usage = {u["metric"]: u for u in overview["usage"]}
    assert usage["facebook_posts"]["used"] == 0
    # Social sources are not mixed into the website/RSS registry
    assert api.get("/api/v1/sources").json()["total"] == 0
    assert api.post(f"/api/v1/sources/{g['id']}/check").status_code == 404

    viewer = join(api, make_user(), "viewer")
    assert viewer.post("/api/v1/social-sources", json={
        "project_id": pid, "kind": "facebook_group", "url": GROUP + "x"}).status_code == 403
    other = make_user()
    assert other.post(f"/api/v1/social-sources/{g['id']}/sync").status_code == 404
    assert other.delete(f"/api/v1/social-sources/{g['id']}").status_code == 404
    assert other.get(f"/api/v1/social-sources/{g['id']}/syncs").status_code == 404


# ------------------------------------------------------------------------ Apify flow
def test_facebook_group_sync_posts_comments_and_threads(make_user, apify, owner_engine):
    api = make_user()
    brand_id, pid = _project(api)
    g = _add(api, pid, kind="facebook_group", url=GROUP)
    syncs = _sync(api, g["id"])
    items, comments = [s for s in syncs if s["stage"] == "items"], \
        [s for s in syncs if s["stage"] == "comments"]
    assert len(items) == 1 and len(comments) == 1
    it = items[0]
    assert it["status"] == "succeeded", it
    assert it["items_fetched"] == 4 and it["items_unreadable"] == 1
    assert it["results_billed"] == 4 and it["provider_cost_usd"] == pytest.approx(0.0123)

    # What was sent to Apify: logged out, capped, cost-limited
    run1 = apify["runs"]["run1"]
    assert run1["actor"] == "apify~facebook-groups-scraper"
    assert run1["input"]["startUrls"] == [{"url": GROUP}]
    assert "cookies" not in json.dumps(run1["input"]).lower()
    assert int(run1["params"]["maxItems"]) == run1["input"]["resultsLimit"] <= 100
    assert float(run1["params"]["maxTotalChargeUsd"]) == get_settings().apify_max_run_usd
    assert "onlyPostsNewerThan" not in run1["input"]          # first sync: no date filter

    ms = {m["title"][:20]: m for m in _mentions(api)}
    post = next(m for m in ms.values() if m["content_type"] == "post")
    assert post["original_text"] == "Réseau Djezzy coupé à Oran depuis ce matin"
    assert post["platform"] == "facebook" and post["connector"] == "apify"
    assert post["collection_method"] == "third_party_provider"
    assert post["verification_status"] == "provider_record"
    assert post["publisher"] == "Auteur 101" and post["engagement"]["reactions"] == 12
    assert post["published_at"].startswith(OLD[:16])
    texts = {m["original_text"]: m for m in ms.values()}
    assert "Djezzy FC a gagné hier" not in texts                    # excluded
    assert "Quelqu'un a testé la fibre ?" not in texts              # names no tracked name
    c1 = texts["pareil chez moi"]                                   # comment of a kept post
    assert c1["content_type"] == "comment" and c1["parent_mention_id"] == post["id"]
    c2 = texts["Djezzy mlih bezaf"]                                 # names the brand itself
    assert c2["parent_mention_id"] is None and c2["content_type"] == "comment"

    # Comments run: posts old enough, comments threaded under the post, replies under comments
    crun = apify["runs"]["run2"]
    assert crun["actor"] == "apify~facebook-comments-scraper"
    assert crun["input"]["startUrls"] == [{"url": post["url"]}]
    assert crun["input"]["resultsLimit"] == get_settings().apify_comments_per_post
    assert comments[0]["status"] == "succeeded" and comments[0]["mentions_known"] == 1
    c9, c10 = texts.get("toujours en panne"), None
    c9 = next(m for m in _mentions(api) if m["original_text"] == "toujours en panne")
    c10 = next(m for m in _mentions(api) if m["original_text"] == "oui")
    assert c9["parent_mention_id"] == post["id"]
    assert c10["content_type"] == "reply" and c10["parent_mention_id"] == c9["id"]

    billing = api.get("/api/v1/billing").json()["usage_month"]
    assert billing["facebook_posts"] == 4 and billing["facebook_comments"] == 2
    with owner_engine.begin() as c:
        jobs = c.execute(text("SELECT count(*) FROM jobs WHERE kind = 'nlp.analyze'")).scalar_one()
    assert jobs >= 1

    # Second sync: nothing new is created twice; the date filter starts from the newest post
    before = len(_mentions(api))
    again = _sync(api, g["id"])
    latest = next(s for s in again if s["stage"] == "items")
    assert latest["mentions_new"] == 0 and latest["mentions_known"] >= 1
    assert len(_mentions(api)) == before
    assert apify["runs"]["run3"]["input"]["onlyPostsNewerThan"] == OLD[:10]


def test_monthly_caps_limit_and_stop_runs(make_user, apify, custom_plan):
    api = make_user()
    custom_plan(api, max_facebook_posts_per_month=3, max_facebook_comments_per_month=0)
    _, pid = _project(api)
    g = _add(api, pid, kind="facebook_group", url=GROUP)
    syncs = _sync(api, g["id"])
    assert int(apify["runs"]["run1"]["params"]["maxItems"]) == 3
    assert {s["stage"]: s["status"] for s in syncs} == {"items": "succeeded",
                                                        "comments": "skipped"}
    assert next(s for s in syncs if s["stage"] == "comments")["error_category"] == "quota"
    n_runs = len(apify["runs"])
    syncs = _sync(api, g["id"])
    assert syncs[0]["status"] == "skipped" and syncs[0]["error_category"] == "quota"
    assert len(apify["runs"]) == n_runs                    # Apify not called at all


def test_scheduled_runs_share_the_monthly_cap(make_user, apify, custom_plan):
    from app.db.models import Source
    from app.db.session import session_scope, set_context
    from app.social.sync import run_budget
    api = make_user()
    custom_plan(api, max_facebook_posts_per_month=60)
    _, pid = _project(api)
    a = _add(api, pid, kind="facebook_group", url=GROUP)
    _add(api, pid, kind="facebook_page", url="https://www.facebook.com/djezzy")
    with session_scope() as db:
        set_context(db, tenant_id=uuid.UUID(api.tenant_id), user_id=None)
        src = db.get(Source, uuid.UUID(a["id"]))
        scheduled = run_budget(db, src, "facebook_posts", 100, manual=False)
        manual = run_budget(db, src, "facebook_posts", 100, manual=True)
    # A scheduled run gets at most its share: half (two sources) of what is left, spread over
    # the runs left this month; a manual run may use what is left, up to the per-run maximum.
    assert 1 <= scheduled <= 30
    assert manual == 60


def test_production_needs_the_legal_review(make_user, apify, monkeypatch, owner_engine):
    api = make_user()
    _, pid = _project(api)
    g = _add(api, pid, kind="facebook_group", url=GROUP)
    monkeypatch.setattr(get_settings(), "env", "production")
    syncs = _sync(api, g["id"])
    assert syncs[0]["status"] == "skipped" and syncs[0]["error_category"] == "not_authorized"
    assert apify["runs"] == {}
    src = api.get("/api/v1/social-sources").json()["sources"][0]
    assert src["coverage_status"] == "PENDING_AUTHORIZATION"
    try:
        with owner_engine.begin() as c:
            c.execute(text("UPDATE connector_policies SET review_status = 'approved' "
                           "WHERE key = 'apify'"))
        syncs = _sync(api, g["id"])
        assert syncs[0]["status"] in ("succeeded", "partial")
    finally:
        with owner_engine.begin() as c:
            c.execute(text("UPDATE connector_policies SET review_status = "
                           "'pending_legal_review' WHERE key = 'apify'"))


def test_provider_errors_are_recorded(make_user, apify):
    api = make_user()
    _, pid = _project(api)
    g = _add(api, pid, kind="facebook_group", url=GROUP)
    apify["start_status"] = 401
    s = _sync(api, g["id"])[0]
    assert s["status"] == "failed" and s["error_category"] == "auth"
    assert "apify-test-token" not in (s["error"] or "")
    apify["start_status"] = 201
    apify["status"] = "FAILED"
    s = _sync(api, g["id"])[0]
    assert s["status"] == "failed" and s["error_category"] == "provider"
    apify["status"] = "SUCCEEDED"
    apify["rows"]["apify~facebook-groups-scraper"] = [{"weird": 1}, {"other": 2}]
    s = next(x for x in _sync(api, g["id"]) if x["stage"] == "items")
    assert s["status"] == "partial" and s["error_category"] == "parse"
    src = api.get("/api/v1/social-sources").json()["sources"][0]
    assert src["last_sync"]["id"] == s["id"]


def test_apify_not_configured(make_user, apify, monkeypatch):
    api = make_user()
    _, pid = _project(api)
    g = _add(api, pid, kind="tiktok_hashtag", query="djezzy")
    monkeypatch.setattr(get_settings(), "apify_token", SecretStr(""))
    s = _sync(api, g["id"])[0]
    assert s["status"] == "skipped" and s["error_category"] == "not_configured"


def test_scanner_capture_and_apify_post_are_counted_once(make_user, apify, owner_engine):
    api = make_user()
    brand_id, pid = _project(api)
    post_url = f"{GROUP}posts/101/"
    sid = str(uuid.uuid4())
    with owner_engine.begin() as c:
        c.execute(text("""
          INSERT INTO mentions (id, tenant_id, brand_id, entity_type, entity_name, url,
            canonical_url, domain, title, original_text, collection_method, connector,
            connector_version, verification_status, content_hash, raw)
          VALUES (:id, :t, :b, 'brand', 'Djezzy', :u, :cu, 'facebook.com', 'capture',
            'Réseau Djezzy coupé à Oran depuis ce matin', 'scanner', 'aynpulse_scanner', '1',
            'scanner_validated', repeat('a', 64), '{"scanner": {"kind": "post"}}'::jsonb)"""),
            {"id": sid, "t": api.tenant_id, "b": brand_id, "u": post_url,
             "cu": f"scanner:entry:{uuid.uuid4()}"})
    g = _add(api, pid, kind="facebook_group", url=GROUP)
    _sync(api, g["id"])
    apify_post = next(m for m in _mentions(api) if m["connector"] == "apify"
                      and m["content_type"] == "post")
    assert apify_post["is_repost"] is True
    detail = api.get(f"/api/v1/mentions/{apify_post['id']}").json()
    assert detail["duplicate_of"] == sid
    # Counted once in the default list
    default = api.get("/api/v1/mentions", params={"limit": 100}).json()["items"]
    assert sum(1 for m in default if "Oran" in m["original_text"]) == 1


def test_brand_page_keeps_every_post(make_user, apify):
    api = make_user()
    _, pid = _project(api)
    apify["rows"]["apify~facebook-posts-scraper"] = [{
        "postId": "777", "url": "https://www.facebook.com/djezzy/posts/777", "pageName": "Djezzy",
        "time": OLD, "text": "Nouvelle offre internet pour l'été", "likes": 40, "comments": 0,
        "shares": 3, "user": {"id": "1", "name": "Djezzy"}}]
    p = _add(api, pid, kind="facebook_page", url="https://www.facebook.com/djezzy",
             match_mode="brand")
    syncs = _sync(api, p["id"])
    assert [s["stage"] for s in syncs] == ["items"]    # no comments on this post: no run
    m = _mentions(api)[0]
    assert m["entity_type"] == "brand" and m["original_text"].startswith("Nouvelle offre")
    assert apify["runs"]["run1"]["actor"] == "apify~facebook-posts-scraper"


def test_schedule_enqueues_due_sources_once(make_user, apify, owner_engine):
    from app.jobs.worker import run_once
    api = make_user()
    _, pid = _project(api)
    _add(api, pid, kind="facebook_group", url=GROUP)
    run_once(schedule=True)
    run_once(schedule=True)
    with owner_engine.begin() as c:
        n = c.execute(text("SELECT count(*) FROM jobs WHERE kind = 'social.sync'")).scalar_one()
        nxt = c.execute(text("SELECT next_sync_at FROM sources")).scalar_one()
    assert n == 1 and nxt > datetime.now(UTC) + timedelta(hours=23)


# ---------------------------------------------------------------------- adapters
def test_adapters_read_documented_formats():
    tiktok, bad = parse("tiktok_hashtag", [
        {"id": "73", "text": "Ooredoo 4G top", "createTimeISO": "2026-11-20T10:00:00.000Z",
         "webVideoUrl": "https://www.tiktok.com/@a/video/73", "textLanguage": "fr",
         "authorMeta": {"id": "9", "name": "a", "nickName": "A", "fans": 10,
                        "profileUrl": "https://www.tiktok.com/@a"},
         "diggCount": 5, "shareCount": 1, "playCount": 100, "commentCount": 2},
        {"url": "https://www.tiktok.com/tag/x", "error": "no results", "errorCode": "X"}])
    assert bad == 1 and tiktok[0].engagement == {"likes": 5, "comments": 2, "shares": 1,
                                                 "views": 100}
    assert tiktok[0].author_url == "https://www.tiktok.com/@a"
    reddit, _ = parse("reddit_search", [
        {"id": "t3_abc", "title": "Ooredoo ou Djezzy ?", "body": "avis svp",
         "authorName": "u1", "communityName": "algeria", "score": 7, "commentCount": 3,
         "createdAt": "2026-11-20T10:00:00Z"}])
    assert reddit[0].url == "https://www.reddit.com/r/algeria/comments/abc/"
    assert reddit[0].text == "Ooredoo ou Djezzy ?\navis svp"
    linkedin, _ = parse("linkedin_company", [
        {"id": "715", "linkedinUrl": "https://www.linkedin.com/posts/x-715",
         "content": "Djezzy recrute", "author": {"name": "Djezzy",
                                                 "linkedinUrl": "https://linkedin.com/company/djezzy"},
         "postedAt": {"timestamp": 1763632800000, "date": "2025-11-20"},
         "engagement": {"likes": 10, "comments": 1, "shares": 0}}])
    assert linkedin[0].published_at.year == 2025 and linkedin[0].engagement["likes"] == 10
    # Missing dates stay missing
    nodate, _ = parse("facebook_page", [{"postId": "1", "url": "https://www.facebook.com/p/1",
                                         "text": "x"}])
    assert nodate[0].published_at is None
    assert build_input("tiktok_profile", url="", query="@djezzy_dz", limit=7, since=None)[
        "profiles"] == ["djezzy_dz"]
    assert build_input("reddit_community", url="", query="r/algeria", limit=5, since=None)[
        "communityNames"] == ["algeria"]


# ------------------------------------------------------------------------ YouTube
@pytest.fixture
def youtube(monkeypatch):
    state = {"calls": [], "disabled": set()}
    video = {"id": "vid1", "snippet": {
        "title": "Test du réseau Djezzy 5G", "description": "Débit mesuré à Alger",
        "channelId": "UCx", "channelTitle": "TechDZ", "publishedAt": OLD},
        "statistics": {"viewCount": "1500", "likeCount": "30", "commentCount": "2"}}
    other = {"id": "vid2", "snippet": {"title": "Recette de cuisine", "description": "",
                                       "channelId": "UCy", "channelTitle": "Cuisine",
                                       "publishedAt": OLD}, "statistics": {}}

    def handler(req: httpx.Request) -> httpx.Response:
        state["calls"].append(req)
        assert req.url.params["key"] == "yt-key"
        res = req.url.path.rsplit("/", 1)[-1]
        if res == "search":
            assert req.url.params["regionCode"] == "DZ"
            return httpx.Response(200, json={"items": [{"id": {"videoId": "vid1"}},
                                                       {"id": {"videoId": "vid2"}}]})
        if res == "channels":
            return httpx.Response(200, json={"items": [{"contentDetails": {
                "relatedPlaylists": {"uploads": "UUx"}}, "snippet": {"title": "Djezzy"}}]})
        if res == "playlistItems":
            return httpx.Response(200, json={"items": [
                {"contentDetails": {"videoId": "vid2", "videoPublishedAt": OLD}}]})
        if res == "videos":
            ids = req.url.params["id"].split(",")
            return httpx.Response(200, json={"items": [v for v in (video, other)
                                                       if v["id"] in ids]})
        if res == "commentThreads":
            if req.url.params["videoId"] in state["disabled"]:
                return httpx.Response(403, json={"error": {"errors": [
                    {"reason": "commentsDisabled"}]}})
            return httpx.Response(200, json={"items": [{"snippet": {"topLevelComment": {
                "id": "yc1", "snippet": {"textOriginal": "chez moi 3 Mbps seulement",
                                         "authorDisplayName": "Yacine", "publishedAt": OLD,
                                         "authorChannelUrl": "http://www.youtube.com/@yacine",
                                         "authorChannelId": {"value": "UCz"},
                                         "likeCount": 2}}},
                "replies": {"comments": [{"id": "yc1.r1", "snippet": {
                    "textOriginal": "pareil", "authorDisplayName": "Lina",
                    "publishedAt": OLD}}]}}]})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(youtube_mod, "youtube_client", lambda: client)
    monkeypatch.setattr(get_settings(), "youtube_api_key", SecretStr("yt-key"))
    return state


def test_youtube_search_videos_comments_and_units(make_user, youtube):
    api = make_user()
    _, pid = _project(api)
    y = _add(api, pid, kind="youtube_search", query="djezzy 5g")
    s = _sync(api, y["id"])[0]
    assert s["status"] == "succeeded", s
    assert s["api_units"] == 100 + 1 + 1          # search + videos + one comment thread call
    ms = _mentions(api)
    video = next(m for m in ms if m["content_type"] == "video")
    assert video["url"] == "https://www.youtube.com/watch?v=vid1"
    assert video["connector"] == "youtube" and video["verification_status"] == \
        "platform_api_record" and video["collection_method"] == "platform_api"
    assert video["engagement"] == {"views": 1500, "likes": 30, "comments": 2}
    assert not any("cuisine" in m["original_text"].lower() for m in ms)
    c = next(m for m in ms if m["original_text"] == "chez moi 3 Mbps seulement")
    r = next(m for m in ms if m["original_text"] == "pareil")
    assert c["parent_mention_id"] == video["id"] and r["parent_mention_id"] == c["id"]
    assert c["author_url"] == "http://www.youtube.com/@yacine"
    usage = api.get("/api/v1/billing").json()["usage_month"]
    assert usage["youtube_units"] == 102 and usage["youtube_results"] == 4   # 2 videos + 2


def test_youtube_channel_of_the_brand_and_quota(make_user, youtube, monkeypatch):
    api = make_user()
    _, pid = _project(api)
    ch = _add(api, pid, kind="youtube_channel", url="https://www.youtube.com/@djezzy",
              match_mode="brand")
    youtube["disabled"].add("vid2")
    s = _sync(api, ch["id"])[0]
    assert s["status"] == "succeeded" and s["api_units"] == 1 + 1 + 1 + 1
    assert [m["original_text"] for m in _mentions(api)] == ["Recette de cuisine"]
    src = api.get("/api/v1/social-sources").json()["sources"][0]
    assert src["name"] == "Djezzy"

    monkeypatch.setattr(get_settings(), "youtube_daily_quota_units", 50)
    y = _add(api, pid, kind="youtube_search", query="djezzy")
    s = _sync(api, y["id"])[0]
    assert s["status"] == "failed" and s["error_category"] == "quota"
    assert not [c for c in youtube["calls"] if c.url.path.endswith("/search")]


def test_scanner_delivery_recognises_social_items(make_user, apify):
    """The reverse direction: a Scanner capture of a post or comment already collected from a
    social source is delivered as a duplicate (counted once)."""
    from app.db.session import session_scope, set_context
    from app.social.ingest import social_duplicate_of
    api = make_user()
    brand_id, pid = _project(api)
    g = _add(api, pid, kind="facebook_group", url=GROUP)
    _sync(api, g["id"])
    ms = _mentions(api)
    post = next(m for m in ms if m["content_type"] == "post")
    comment = next(m for m in ms if m["original_text"] == "pareil chez moi")
    t, b = uuid.UUID(api.tenant_id), uuid.UUID(brand_id)
    with session_scope() as db:
        set_context(db, tenant_id=t, user_id=None)
        share_link = "https://www.facebook.com/groups/algerie.telecom.users/posts/101"
        assert social_duplicate_of(db, t, b, url=share_link, kind="post", body="x") == \
            uuid.UUID(post["id"])
        assert social_duplicate_of(db, t, b, url=post["url"], kind="comment",
                                   body="Pareil  chez moi") == uuid.UUID(comment["id"])
        assert social_duplicate_of(db, t, b, url=post["url"], kind="comment",
                                   body="autre chose") is None
        assert social_duplicate_of(db, t, b, url="https://www.facebook.com/x/posts/9",
                                   kind="post", body="") is None
