"""Milestone 2: catalog, monitoring projects/runs, scheduler, query plan, isolation."""

from sqlalchemy import text

from app.jobs.worker import run_once
from tests.conftest import drain


def _brand(api, name="Djezzy", aliases=("جازي",)):
    r = api.post("/api/v1/brands", json={"name": name, "aliases": [{"alias": a} for a in aliases]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _project(api, brand_id, **kw):
    r = api.post("/api/v1/monitoring-projects",
                 json={"name": kw.pop("name", "Veille Djezzy"), "brand_id": brand_id, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- catalog
def test_catalog_crud(make_user):
    api = make_user()
    b = _brand(api)
    base = f"/api/v1/brands/{b}"

    p = api.post(f"{base}/products", json={"name": " Hayla  Bezzef ", "category": "forfait",
                                           "aliases": ["هايلة", "hayla", "Hayla"]})
    assert p.status_code == 201 and p.json()["name"] == "Hayla Bezzef"
    assert p.json()["aliases"] == ["هايلة", "hayla"]
    assert api.post(f"{base}/products", json={"name": "hayla bezzef"}).status_code == 409
    pid = p.json()["id"]
    r = api.patch(f"{base}/products/{pid}", json={"aliases": ["hayla 4g"]})
    assert r.json()["aliases"] == ["hayla 4g"] and r.json()["category"] == "forfait"

    c = api.post(f"{base}/competitors", json={"name": "Ooredoo", "website": "https://ooredoo.dz",
                                              "aliases": ["اوريدو"]})
    assert c.status_code == 201
    assert api.post(f"{base}/competitors", json={"name": "x", "website": "not-a-url"}
                    ).status_code == 422

    k = api.post(f"{base}/keywords", json={"expression": "djezzy fc", "kind": "exclude"})
    assert k.status_code == 201
    assert api.post(f"{base}/keywords", json={"expression": "Djezzy FC", "kind": "exclude"}
                    ).status_code == 409
    assert api.post(f"{base}/keywords", json={"expression": "Djezzy FC"}).status_code == 201

    assert len(api.get(f"{base}/products").json()) == 1
    assert len(api.get(f"{base}/keywords").json()) == 2
    assert api.delete(f"{base}/products/{pid}").status_code == 204
    assert api.get(f"{base}/products").json() == []
    # Deleting the brand removes its catalog (cascade)
    assert api.delete(base).status_code == 204
    assert api.get(f"{base}/competitors").status_code == 404


def test_viewer_cannot_edit_catalog_or_run(make_user):
    from tests.conftest import join
    owner = make_user(org="Org One")
    viewer = join(owner, make_user(org="Org Two"), "viewer")
    b = _brand(owner)
    proj = _project(owner, b)
    assert viewer.get(f"/api/v1/brands/{b}/products").status_code == 200
    assert viewer.post(f"/api/v1/brands/{b}/products", json={"name": "x"}).status_code == 403
    assert viewer.get("/api/v1/monitoring-projects").status_code == 200
    assert viewer.post(f"/api/v1/monitoring-projects/{proj['id']}/runs").status_code == 403
    assert viewer.patch(f"/api/v1/monitoring-projects/{proj['id']}",
                        json={"status": "paused"}).status_code == 403


# ---------------------------------------------------------------- isolation
def test_catalog_and_monitoring_are_tenant_isolated(make_user):
    a = make_user(org="Org A")
    b = make_user(org="Org B")
    ba = _brand(a)
    pa = a.post(f"/api/v1/brands/{ba}/products", json={"name": "Prod A"}).json()["id"]
    ca = a.post(f"/api/v1/brands/{ba}/competitors", json={"name": "Comp A"}).json()["id"]
    ka = a.post(f"/api/v1/brands/{ba}/keywords", json={"expression": "kw a"}).json()["id"]
    proj = _project(a, ba)
    a.post(f"/api/v1/monitoring-projects/{proj['id']}/runs")

    base = f"/api/v1/brands/{ba}"
    for path in ("products", "competitors", "keywords"):
        assert b.get(f"{base}/{path}").status_code == 404
    assert b.post(f"{base}/products", json={"name": "x"}).status_code == 404
    assert b.patch(f"{base}/products/{pa}", json={"name": "x"}).status_code == 404
    assert b.delete(f"{base}/competitors/{ca}").status_code == 404
    assert b.delete(f"{base}/keywords/{ka}").status_code == 404

    pid = proj["id"]
    assert b.get("/api/v1/monitoring-projects").json() == []
    for method, path in [("get", ""), ("patch", ""), ("delete", ""), ("post", "/runs"),
                         ("get", "/runs"), ("get", "/query-plan")]:
        kw = {"json": {"name": "x"}} if method == "patch" else {}
        r = getattr(b, method)(f"/api/v1/monitoring-projects/{pid}{path}", **kw)
        assert r.status_code == 404, (method, path, r.status_code)
    # Cannot create a project on someone else's brand
    r = b.post("/api/v1/monitoring-projects", json={"name": "steal", "brand_id": ba})
    assert r.status_code == 404
    # A's data intact
    assert len(a.get(f"{base}/products").json()) == 1
    assert a.get(f"/api/v1/monitoring-projects/{pid}").status_code == 200


# ---------------------------------------------------------------- runs & scheduler
def test_manual_run_records_honest_skipped_result(make_user):
    api = make_user()
    b = _brand(api, aliases=("جازي", "djezy"))
    api.post(f"/api/v1/brands/{b}/products", json={"name": "Hayla", "aliases": ["هايلة"]})
    api.post(f"/api/v1/brands/{b}/competitors", json={"name": "Ooredoo"})
    proj = _project(api, b, status="paused")
    pid = proj["id"]

    r = api.post(f"/api/v1/monitoring-projects/{pid}/runs")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert api.post(f"/api/v1/monitoring-projects/{pid}/runs").status_code == 409
    drain()
    [run] = api.get(f"/api/v1/monitoring-projects/{pid}/runs").json()
    assert run["trigger"] == "manual" and run["status"] == "skipped"
    s = run["summary"]
    assert s["reason"] == "no_active_connectors" and s["mentions_collected"] == 0
    assert s["query_groups"] == 3 and s["search_terms"] == 6  # 3 brand + 2 product + 1
    assert api.get(f"/api/v1/monitoring-projects/{pid}").json()["last_run"]["status"] == "skipped"
    dash = api.get("/api/v1/dashboard/summary").json()
    assert dash["last_run"]["reason"] == "no_active_connectors"
    assert dash["mention_stats"]["total"] == 0        # nothing invented
    assert dash["sentiment"]["available"] is False


def test_scheduler_runs_due_active_projects_only(make_user, owner_engine):
    api = make_user()
    b = _brand(api)
    active = _project(api, b, name="Active", frequency_minutes=60)
    paused = _project(api, b, name="Paused", status="paused")
    assert active["next_run_at"] is not None and paused["next_run_at"] is None

    run_once()   # scheduler tick enqueues; the same call processes the batch
    drain()
    runs = api.get(f"/api/v1/monitoring-projects/{active['id']}/runs").json()
    assert [r["trigger"] for r in runs] == ["schedule"]
    assert api.get(f"/api/v1/monitoring-projects/{paused['id']}/runs").json() == []

    # Next run pushed ~60 min ahead, so another tick does nothing
    run_once()
    drain()
    assert len(api.get(f"/api/v1/monitoring-projects/{active['id']}/runs").json()) == 1

    # Pausing clears the schedule; resuming schedules immediately
    api.patch(f"/api/v1/monitoring-projects/{active['id']}", json={"status": "paused"})
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT next_run_at FROM monitoring_projects WHERE name='Active'")
                         ).scalar_one() is None
    r = api.patch(f"/api/v1/monitoring-projects/{active['id']}", json={"status": "active"})
    assert r.json()["next_run_at"] is not None


def test_query_plan_endpoint(make_user):
    api = make_user()
    b = _brand(api, aliases=("جازي",))
    api.post(f"/api/v1/brands/{b}/keywords", json={"expression": "djezzy fc", "kind": "exclude"})
    api.post(f"/api/v1/brands/{b}/competitors", json={"name": "Ooredoo"})
    with_comp = _project(api, b, name="With")
    without = _project(api, b, name="Without", include_competitors=False)
    g = api.get(f"/api/v1/monitoring-projects/{with_comp['id']}/query-plan").json()["groups"]
    assert [x["entity_type"] for x in g] == ["brand", "competitor"]
    assert g[0]["terms"] == ["Djezzy", "جازي"] and g[0]["exclude"] == ["djezzy fc"]
    assert g[0]["search_string"] == '("Djezzy" OR "جازي") -"djezzy fc"'
    g2 = api.get(f"/api/v1/monitoring-projects/{without['id']}/query-plan").json()["groups"]
    assert [x["entity_type"] for x in g2] == ["brand"]


def test_project_validation(make_user):
    api = make_user()
    b = _brand(api)
    _project(api, b, name="Unique")
    r = api.post("/api/v1/monitoring-projects", json={"name": "unique", "brand_id": b})
    assert r.status_code == 409
    for bad in [{"frequency_minutes": 5}, {"frequency_minutes": 99999}, {"languages": []},
                {"languages": ["xx"]}]:
        r = api.post("/api/v1/monitoring-projects", json={"name": "N", "brand_id": b, **bad})
        assert r.status_code == 422, bad
