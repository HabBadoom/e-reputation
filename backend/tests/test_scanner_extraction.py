"""Aynpulse Scanner, phase 4: extraction through the API and the job worker, with a fake vision
model — statuses, entries and threads, dates, duplicates across overlapping screenshots,
corrections, failures and retries, isolation."""

import pytest

from app.nlp.llm import LLMError
from app.scanner.extraction import vision
from tests.conftest import drain
from tests.test_scanner import TOKEN, scanner_signup
from tests.test_scanner_extraction_unit import comment, out, post
from tests.test_scanner_uploads import current, png, upload

S = "/api/v1/scanner"


class FakeVision:
    def __init__(self):
        self.answers: list = []
        self.calls = 0

    def read(self, images, *, captured):
        self.calls += 1
        assert images and all(i.startswith(b"\x89PNG") for i in images)
        nxt = self.answers.pop(0) if self.answers else out()
        if isinstance(nxt, Exception):
            raise nxt
        return vision.VisionResult(nxt, "fake-vision-1", 1200, 300, len(images))


@pytest.fixture
def fake():
    f = FakeVision()
    vision.set_provider(f)
    yield f
    vision.set_provider(None)


def items(sc, sid):
    return sc.get(f"{S}/sessions/{sid}/items").json()


def entries(sc, item_id):
    r = sc.get(f"{S}/items/{item_id}/entries")
    assert r.status_code == 200, r.text
    return r.json()


def test_without_a_model_screenshots_wait(client):
    sc = scanner_signup(client)
    assert sc.get(f"{S}/extraction").json() == {"enabled": False}
    sid = current(sc)
    upload(sc, sid, png("a.png"))
    assert drain() == 0
    it = items(sc, sid)[0]
    assert it["status"] == "pending" and it["extraction"] is None
    r = sc.post(f"{S}/sessions/{sid}/extract")
    assert r.status_code == 409 and r.json()["error"]["code"] == "extraction_disabled"


def test_screenshot_is_read_into_a_thread(client, fake):
    sc = scanner_signup(client)
    assert sc.get(f"{S}/extraction").json() == {"enabled": True}
    sid = current(sc)
    fake.answers.append(out([post(text="Nouvelle offre 5G 🔥", image_text="-50%")],
                            [comment(1, "مازلت سما عادي\nتشغلت 5g فقط", "H.M"),
                             comment(2, "Nour Chams شكرا", "Nour Chams", parent=1),
                             comment(3, "Rani nchrih 190 qaada", "Boudbi Manel")],
                            issues=["background_ignored"], page_or_group="Ooredoo 5G Algérie"))
    it = upload(sc, sid, png("Capture d'écran 2026-11-23 064458.png")).json()["items"][0]
    assert it["status"] == "processing" and it["extraction"]["status"] == "queued"
    assert drain() >= 1 and fake.calls == 1

    it = items(sc, sid)[0]
    assert it["status"] == "ready" and it["platform"] == "facebook"
    assert it["content_type"] == "post_and_comments"
    ex = it["extraction"]
    assert ex["status"] == "done" and ex["entries"] == 4 and ex["confidence"] == 0.9
    assert ex["context"]["page_or_group"] == "Ooredoo 5G Algérie"
    assert ex["issues"] == ["background_ignored"] and ex["model"] == "fake-vision-1"

    es = entries(sc, it["id"])
    assert [(e["kind"], e["author_name"], e["script"]) for e in es] == [
        ("post", "Ooredoo Algérie", "latin"), ("comment", "H.M", "arabic"),
        ("reply", "Nour Chams", "mixed"), ("comment", "Boudbi Manel", "latin")]
    p, c1, r1, c2 = es
    assert c1["parent_id"] == p["id"] and r1["parent_id"] == c1["id"] and c2["parent_id"] == p["id"]
    assert p["image_text"] == "-50%" and p["reactions_count"] == 12 and p["comments_count"] == 3
    # "9 h" before the capture time read from the file name (06:44:58 in Algiers)
    assert p["published_at"].startswith("2026-11-22T20:44:58")
    assert p["published_precision"] == "hour"
    assert c1["published_precision"] == "day" and c1["text"] == "مازلت سما عادي\nتشغلت 5g فقط"


def test_overlapping_screenshots_do_not_double_count(client, fake):
    sc = scanner_signup(client)
    sid = current(sc)
    full = "مكانش تطبيق الا وفيه بنة الاعطال والمشاكل"
    fake.answers += [
        out([], [comment(1, "زيد تاع موبيليس", "Imad Wan", post_ref=None),
                 comment(2, "مكانش تطبيق الا وفيه بنة", "علي ابوستين", post_ref=None,
                         partially_visible=True)]),                     # cut at the bottom
        out([], [comment(1, full, "علي ابوستين", post_ref=None),        # now in full
                 comment(2, "زيد تاع  موبيليس!", "imad wan", post_ref=None),   # same comment
                 comment(3, "أسوأ تطبيقات في دنيا", "فوزي فوزي", post_ref=None)]),
    ]
    upload(sc, sid, png("1.png", text="haut"))
    drain()
    upload(sc, sid, png("2.png", text="bas"))
    drain()
    first, second = items(sc, sid)
    assert first["extraction"]["entries"] == 1          # the cut copy now points to the full one
    assert second["extraction"]["entries"] == 2         # Imad Wan's repeat is not counted
    e1, e2 = entries(sc, first["id"]), entries(sc, second["id"])
    assert e1[1]["duplicate_of"] == e2[0]["id"] and e1[0]["duplicate_of"] is None
    assert e2[1]["duplicate_of"] == e1[0]["id"] and e2[2]["duplicate_of"] is None


def test_low_confidence_or_nothing_readable_needs_review(client, fake):
    sc = scanner_signup(client)
    sid = current(sc)
    fake.answers += [out([post()], confidence=0.4), out(issues=["not_social_media"])]
    upload(sc, sid, png("a.png", text="un"))
    drain()                                   # one at a time: job order is not guaranteed
    upload(sc, sid, png("b.png", text="deux"))
    drain()
    a, b = items(sc, sid)
    assert a["status"] == "needs_review" and b["status"] == "needs_review"
    assert b["extraction"]["entries"] == 0 and "no_text" in b["extraction"]["issues"]


def test_agent_corrects_and_removes_entries(client, fake):
    sc, other = scanner_signup(client), scanner_signup(client)
    sid = current(sc)
    fake.answers.append(out([post()], [comment(1, "salam alikoum", "Nour"),
                                       comment(2, "Répondre", "Partager")]))
    item = upload(sc, sid, png("a.png")).json()["items"][0]
    drain()
    es = entries(sc, item["id"])
    h = {"authorization": f"Bearer {sc.access}", **TOKEN}
    r = sc.c.patch(f"{S}/entries/{es[1]['id']}", headers=h,
                   json={"text": "salam alikoum khouya", "author_name": " Nour Chams "})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "salam alikoum khouya" and r.json()["author_name"] == "Nour Chams"
    assert r.json()["edited_by_agent"] is True
    assert sc.c.delete(f"{S}/entries/{es[2]['id']}", headers=h).status_code == 204
    assert [e["author_name"] for e in entries(sc, item["id"])] == ["Ooredoo Algérie", "Nour Chams"]
    # Another agent sees nothing and changes nothing.
    ho = {"authorization": f"Bearer {other.access}", **TOKEN}
    assert other.get(f"{S}/items/{item['id']}/entries").status_code == 404
    assert other.c.patch(f"{S}/entries/{es[1]['id']}", headers=ho,
                         json={"text": "x"}).status_code == 404
    assert other.c.delete(f"{S}/entries/{es[0]['id']}", headers=ho).status_code == 404
    assert entries(sc, item["id"])[1]["text"] == "salam alikoum khouya"


def test_failure_then_read_again(client, fake):
    sc = scanner_signup(client)
    sid = current(sc)
    fake.answers += [LLMError("quota_exceeded", "OpenAI 429", fatal=True),
                     out([post()])]
    item = upload(sc, sid, png("a.png")).json()["items"][0]
    drain()
    it = items(sc, sid)[0]
    assert it["status"] == "failed" and it["error_code"] == "quota_exceeded"
    assert it["extraction"]["status"] == "failed"
    r = sc.post(f"{S}/items/{item['id']}/extract")
    assert r.status_code == 200, r.text
    assert r.json()[0]["status"] == "processing"
    drain()
    it = items(sc, sid)[0]
    assert it["status"] == "ready" and it["error_code"] is None
    assert it["extraction"]["entries"] == 1 and fake.calls == 2


def test_temporary_errors_are_retried(client, fake, monkeypatch):
    from app.jobs import queue
    monkeypatch.setattr(queue, "BACKOFF", [0])          # retry at once in the test
    sc = scanner_signup(client)
    sid = current(sc)
    fake.answers += [LLMError("network_error", "ConnectError"), out([post()])]
    upload(sc, sid, png("a.png"))
    drain()
    assert items(sc, sid)[0]["status"] == "ready" and fake.calls == 2


def test_screenshots_added_before_configuration_can_be_queued(client, fake):
    sc = scanner_signup(client)
    sid = current(sc)
    vision.set_provider(None)
    upload(sc, sid, png("a.png", text="un"), png("b.png", text="deux"))
    vision.set_provider(fake)
    fake.answers += [out([post()]), out([post(author="Djezzy", text="Autre")])]
    r = sc.post(f"{S}/sessions/{sid}/extract")
    assert r.status_code == 200 and r.json() == {"queued": 2}
    drain()
    assert [i["status"] for i in items(sc, sid)] == ["ready", "ready"]
    assert sc.post(f"{S}/sessions/{sid}/extract").json() == {"queued": 0}


def test_removing_a_screenshot_removes_its_entries(client, fake):
    sc = scanner_signup(client)
    sid = current(sc)
    fake.answers.append(out([post()], [comment(1, "salam")]))
    item = upload(sc, sid, png("a.png")).json()["items"][0]
    drain()
    h = {"authorization": f"Bearer {sc.access}", **TOKEN}
    assert sc.c.delete(f"{S}/items/{item['id']}", headers=h).status_code == 200
    assert sc.get(f"{S}/items/{item['id']}/entries").status_code == 404


def test_agent_validates_a_reading(client, fake):
    sc = scanner_signup(client)
    sid = current(sc)
    fake.answers += [out([post()], confidence=0.4), out([post()])]
    item = upload(sc, sid, png("a.png")).json()["items"][0]
    r = sc.post(f"{S}/items/{item['id']}/validate")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_extracted"   # not read yet
    drain()
    assert items(sc, sid)[0]["status"] == "needs_review"

    r = sc.post(f"{S}/items/{item['id']}/validate")
    assert r.status_code == 200, r.text
    it = r.json()[0]
    assert it["status"] == "ready" and it["extraction"]["validated_at"] is not None
    first = it["extraction"]["validated_at"]
    assert sc.post(f"{S}/items/{item['id']}/validate").json()[0]["extraction"][
        "validated_at"] == first                                          # idempotent

    r = sc.post(f"{S}/items/{item['id']}/validate", json={"validated": False})
    assert r.json()[0]["extraction"]["validated_at"] is None

    sc.post(f"{S}/items/{item['id']}/validate")
    sc.post(f"{S}/items/{item['id']}/extract")                          # read again
    drain()
    assert items(sc, sid)[0]["extraction"]["validated_at"] is None       # to check again

    other = scanner_signup(client)
    assert other.post(f"{S}/items/{item['id']}/validate").status_code == 404
