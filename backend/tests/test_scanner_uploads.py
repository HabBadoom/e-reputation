"""Aynpulse Scanner, phase 3: screenshot uploads — validation, sanitisation, duplicates, order,
removal, private read-back, isolation, failure handling."""

import contextlib
import io
import os
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import get_settings
from app.scanner import storage as storage_mod
from app.scanner.storage import S3Storage, StorageError
from tests.conftest import PASSWORD
from tests.test_scanner import TOKEN, code, scanner_signup
from tests.test_scanner_images import screenshot, with_gps_exif

S = "/api/v1/scanner"


def upload(sc, sid: str, *files: tuple[str, bytes, str]):
    return sc.c.post(f"{S}/sessions/{sid}/screenshots",
                     headers={"authorization": f"Bearer {sc.access}", **TOKEN},
                     files=[("files", f) for f in files])


def png(name="capture.png", **kw):
    return (name, screenshot("PNG", **kw), "image/png")


def current(sc) -> str:
    return sc.get(f"{S}/sessions/current").json()["id"]


def test_upload_mixed_batch(client):
    sc = scanner_signup(client)
    sid = current(sc)
    r = upload(sc, sid, png("a.png"), ("photo.jpg", with_gps_exif("JPEG"), "image/jpeg"),
               ("rapport.pdf", b"%PDF-1.7", "image/png"),          # lies about its type
               png("a-copy.png"))                                   # exact duplicate of a.png
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(x["filename"], x["status"], x["code"]) for x in body["results"]] == [
        ("a.png", "added", None), ("photo.jpg", "added", None),
        ("rapport.pdf", "rejected", "unsupported_type"),
        ("a-copy.png", "duplicate", "duplicate_image")]
    assert body["results"][3]["item_id"] == body["results"][0]["item_id"]
    items = body["items"]
    assert [i["file"]["original_filename"] for i in items] == ["a.png", "photo.jpg"]
    assert [i["position"] for i in items] == [0, 1]
    assert all(i["source_type"] == "screenshot" and i["status"] == "pending" for i in items)
    sess = body["session"]
    assert sess["status"] == "collecting" and sess["counters"]["screenshots"] == 2
    # Coming back to the add-item screen shows the same collection with its items.
    assert sc.get(f"{S}/sessions/current").json()["id"] == sid
    assert len(sc.get(f"{S}/sessions/{sid}/items").json()) == 2


def test_stored_file_is_sanitised_and_private(client):
    sc = scanner_signup(client)
    other = scanner_signup(client)
    sid = current(sc)
    item = upload(sc, sid, ("gps.jpg", with_gps_exif("JPEG"), "image/jpeg")).json()["items"][0]
    url = item["file"]["url"]
    r = sc.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"].startswith("private")
    im = Image.open(io.BytesIO(r.content))
    assert not im.getexif() and (im.width, im.height) == (844, 390)   # no GPS, upright
    assert other.get(url).status_code == 404
    assert client.get(url).status_code == 401
    # Stored under an application-made key, not the uploaded name.
    root = Path(get_settings().storage_filesystem_root)
    stored = list(root.rglob(f"{item['file']['id']}.jpg"))
    assert len(stored) == 1 and "gps" not in str(stored[0])


def test_near_duplicate_is_flagged_not_merged(client):
    sc = scanner_signup(client)
    sid = current(sc)
    first = upload(sc, sid, png("a.png")).json()["items"][0]
    r = upload(sc, sid, ("a.jpg", screenshot("JPEG", quality=70), "image/jpeg"))
    second = r.json()["items"][1]
    assert r.json()["results"][0]["status"] == "added"
    assert second["possible_duplicate_of"] == first["id"]
    third = upload(sc, sid, png("b.png", size=(844, 390), text="Mobilis")).json()["items"][2]
    assert third["possible_duplicate_of"] is None
    # The same image is accepted in another collection.
    sc.post(f"{S}/sessions/{sid}/cancel")
    sid2 = current(sc)
    assert upload(sc, sid2, png("a.png")).json()["results"][0]["status"] == "added"


def test_reorder_and_remove(client):
    sc = scanner_signup(client)
    sid = current(sc)
    items = upload(sc, sid, png("1.png", text="un"), png("2.png", size=(800, 400), text="deux"),
                   png("3.png", size=(500, 900), text="trois")).json()["items"]
    ids = [i["id"] for i in items]
    r = sc.c.put(f"{S}/sessions/{sid}/items/order", json={"item_ids": ids[::-1]},
                 headers=sc._h())
    assert r.status_code == 200 and [i["id"] for i in r.json()] == ids[::-1]
    bad = sc.c.put(f"{S}/sessions/{sid}/items/order", json={"item_ids": ids[:2]},
                   headers=sc._h())
    assert bad.status_code == 409 and code(bad) == "invalid_order"

    url = items[0]["file"]["url"]
    r = sc.c.delete(f"{S}/items/{ids[0]}", headers=sc._h())
    assert r.status_code == 200 and r.json()["counters"]["screenshots"] == 2
    assert sc.get(url).status_code == 404
    root = Path(get_settings().storage_filesystem_root)
    assert not list(root.rglob(f"{items[0]['file']['id']}.*"))     # object deleted too
    for i in ids[1:]:
        sc.c.delete(f"{S}/items/{i}", headers=sc._h())
    assert sc.get(f"{S}/sessions/{sid}").json()["status"] == "draft"


def test_closed_collection_and_isolation(client):
    a = scanner_signup(client)
    b = scanner_signup(client)
    sid = current(a)
    item = upload(a, sid, png()).json()["items"][0]
    assert upload(b, sid, png("x.png", text="x")).status_code == 404
    assert b.get(f"{S}/sessions/{sid}/items").status_code == 404
    assert b.c.delete(f"{S}/items/{item['id']}", headers=b._h()).status_code == 404
    assert b.c.put(f"{S}/sessions/{sid}/items/order", json={"item_ids": [item["id"]]},
                   headers=b._h()).status_code == 404
    a.post(f"{S}/sessions/{sid}/cancel")
    r = upload(a, sid, png("late.png", text="late"))
    assert r.status_code == 409 and code(r) == "session_not_open"
    r = a.c.delete(f"{S}/items/{item['id']}", headers=a._h())
    assert r.status_code == 409 and code(r) == "session_not_open"


def test_cookie_upload_needs_csrf(client):
    sc = scanner_signup(client)
    client.post(f"{S}/auth/login", json={"email": sc.email, "password": PASSWORD})
    sid = client.get(f"{S}/sessions/current").json()["id"]
    f = [("files", png())]
    assert client.post(f"{S}/sessions/{sid}/screenshots", files=f).status_code == 403
    r = client.post(f"{S}/sessions/{sid}/screenshots", files=f,
                    headers={"x-csrf-token": client.cookies["sc_csrf"]})
    assert r.status_code == 200, r.text


def test_limits(client, monkeypatch):
    sc = scanner_signup(client)
    sid = current(sc)
    cfg = get_settings()
    n = cfg.scanner_upload_max_files_per_request + 1
    many = [png(f"{i}.png", text=str(i)) for i in range(n)]
    r = upload(sc, sid, *many)
    assert r.status_code == 413 and code(r) == "too_many_files"
    monkeypatch.setattr(cfg, "scanner_max_items_per_session", 1)
    r = upload(sc, sid, png("1.png", text="1"), png("2.png", size=(800, 400), text="2"))
    assert [x["status"] for x in r.json()["results"]] == ["added", "rejected"]
    assert r.json()["results"][1]["code"] == "collection_full"
    monkeypatch.setattr(cfg, "scanner_upload_max_bytes", 2000)
    monkeypatch.setattr(cfg, "scanner_max_items_per_session", 10)
    r = upload(sc, sid, png("big.png", size=(1200, 2400), text="big"))
    assert r.json()["results"][0]["code"] == "file_too_large"


def test_storage_outage_keeps_nothing_half_done(client, monkeypatch):
    sc = scanner_signup(client)
    sid = current(sc)

    class Down:
        def put(self, *a, **k):
            raise StorageError("down")

        def get(self, *a, **k):
            raise StorageError("down")

        def delete(self, *a, **k):
            raise StorageError("down")

    monkeypatch.setattr("app.scanner.uploads.get_storage", lambda: Down())
    r = upload(sc, sid, png())
    assert r.status_code == 503 and code(r) == "storage_unavailable"
    assert sc.get(f"{S}/sessions/{sid}/items").json() == []


# ---- S3-compatible backend (MinIO in CI) --------------------------------------------------------
@pytest.mark.skipif(not os.environ.get("REPINTEL_TEST_S3_ENDPOINT"),
                    reason="needs an S3-compatible endpoint (MinIO in CI)")
def test_s3_storage_roundtrip_and_private_bucket():
    import httpx

    endpoint = os.environ["REPINTEL_TEST_S3_ENDPOINT"]
    st = S3Storage(endpoint_url=endpoint, region="us-east-1", bucket="repintel-scanner-test",
                   access_key=os.environ["REPINTEL_TEST_S3_ACCESS_KEY"],
                   secret_key=os.environ["REPINTEL_TEST_S3_SECRET_KEY"])
    with contextlib.suppress(st.client.exceptions.BucketAlreadyOwnedByYou):
        st.client.create_bucket(Bucket="repintel-scanner-test")
    key = "scanner/acc/sess/file.png"
    data = screenshot("PNG")
    st.put(key, data, "image/png")
    assert st.get(key) == data
    anonymous = httpx.get(f"{endpoint}/repintel-scanner-test/{key}")
    assert anonymous.status_code == 403            # never public
    st.delete(key)
    with pytest.raises(StorageError):
        st.get(key)
    with pytest.raises(StorageError):
        st.put("../escape", b"x", "image/png")
    assert storage_mod.get_storage   # factory importable
