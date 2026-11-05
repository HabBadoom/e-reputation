import pytest

from app.modules.brands.scripts import guess_script


def test_brand_crud_with_multiscript_aliases(make_user):
    api = make_user()
    r = api.post("/api/v1/brands", json={
        "name": "  Mobilis  ", "website": "https://www.mobilis.dz", "aliases": [
            {"alias": "موبيليس"}, {"alias": "mobilis"}, {"alias": "MOBILIS"},  # dup (case)
            {"alias": "mobiliss", "kind": "misspelling"}, {"alias": "ⵎⵓⴱⵉⵍⵉⵙ"},
        ]})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["name"] == "Mobilis"
    scripts = {a["alias"]: a["script"] for a in b["aliases"]}
    assert scripts == {"موبيليس": "arabic", "mobilis": "latin", "mobiliss": "latin",
                       "ⵎⵓⴱⵉⵍⵉⵙ": "tifinagh"}

    assert api.post("/api/v1/brands", json={"name": "mobilis"}).status_code == 409
    assert api.post(f"/api/v1/brands/{b['id']}/aliases",
                    json={"alias": "Mobilis"}).status_code == 409

    found = api.get("/api/v1/brands", params={"q": "موبي"}).json()
    assert found["total"] == 1
    assert api.get("/api/v1/brands", params={"q": "%"}).json()["total"] == 0  # LIKE escaped

    r = api.patch(f"/api/v1/brands/{b['id']}", json={"status": "paused"})
    assert r.json()["status"] == "paused"
    assert api.get("/api/v1/brands", params={"status": "active"}).json()["total"] == 0
    assert api.delete(f"/api/v1/brands/{b['id']}").status_code == 204
    assert api.get(f"/api/v1/brands/{b['id']}").status_code == 404

    actions = [e["action"] for e in api.get("/api/v1/audit-logs").json()["items"]]
    assert {"brand.created", "brand.updated", "brand.deleted"} <= set(actions)


def test_pagination(make_user):
    api = make_user()
    for i in range(7):
        api.post("/api/v1/brands", json={"name": f"Brand {i}"})
    p = api.get("/api/v1/brands", params={"limit": 3, "offset": 6}).json()
    assert p["total"] == 7 and len(p["items"]) == 1
    assert api.get("/api/v1/brands", params={"limit": 1000}).status_code == 422


@pytest.mark.parametrize("text,script", [
    ("Djezzy", "latin"), ("جازي", "arabic"), ("3la khater", "arabizi"),
    ("sa7a", "arabizi"), ("w9t", "arabizi"), ("ⴰⵣⵓⵍ", "tifinagh"), ("Café", "latin"),
    ("Mobilis 3G", "latin"), ("Studio 7a", "latin"), ("bzaf", "latin"), ("Привет", "other"),
])
def test_guess_script(text, script):
    assert guess_script(text) == script
