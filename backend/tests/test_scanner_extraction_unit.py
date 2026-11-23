"""Aynpulse Scanner, phase 4: pure parts of extraction — model output checking, thread
structure, relative dates, capture time from file names, text normalisation, tall images."""

import io
from datetime import UTC, datetime

from PIL import Image

from app.scanner.extraction import dates, schema, text, vision


def out(posts=(), comments=(), confidence=0.9, issues=(), **ctx):
    return {"context": {"platform": "facebook", "view": "comments_dialog", "page_or_group": "",
                        "dialog_title": "", **ctx},
            "posts": list(posts), "comments": list(comments),
            "quality": {"confidence": confidence, "issues": list(issues)}}


def post(ref=1, author="Ooredoo Algérie", text="Nouvelle offre", **kw):
    return {"ref": ref, "author": author, "author_is_page": True, "text": text,
            "text_truncated": False, "image_text": "", "relative_time": "9 h",
            "partially_visible": False, "reactions_count": 12, "comments_count": 3,
            "shares_count": None, **kw}


def comment(ref, text="salam", author="Nour", parent=None, post_ref=1, **kw):
    return {"ref": ref, "parent_ref": parent, "post_ref": post_ref, "author": author,
            "text": text, "text_truncated": False, "relative_time": "1 j",
            "reactions_count": None, "partially_visible": False, **kw}


def test_parse_builds_post_comment_reply_tree():
    r = schema.parse(out([post()], [comment(1, "مازلت سما عادي"), comment(2, "صحيت", "H.M", 1),
                                    comment(3, "Merci", "Lina")]))
    assert [(e.kind, e.author, e.parent) for e in r.entries] == [
        ("post", "Ooredoo Algérie", None), ("comment", "Nour", 0), ("reply", "H.M", 1),
        ("comment", "Lina", 0)]
    assert r.platform == "facebook" and r.confidence == 0.9 and r.issues == []


def test_parse_drops_what_cannot_be_trusted():
    r = schema.parse(out(
        [post(author="", text=""), post(ref=2)],           # empty post skipped
        [comment(1, ""),                                    # sticker-only, no replies: skipped
         comment(2, "réponse", parent=99),                  # unknown parent → top level
         comment(2, "doublon de ref"),                      # repeated ref: skipped
         comment(3, "ok", parent=3)],                       # its own parent → ignored
        confidence=7, issues=["background_ignored", "made_up"], platform="myspace"))
    assert [(e.kind, e.text, e.parent) for e in r.entries] == [
        ("post", "Nouvelle offre", None), ("comment", "réponse", 0), ("comment", "ok", 0)]
    assert r.confidence == 1.0 and r.issues == ["background_ignored"] and r.platform == "unknown"


def test_parse_marks_screenshots_without_text():
    r = schema.parse(out())
    assert r.entries == [] and "no_text" in r.issues


def test_parse_rejects_non_objects():
    import pytest
    with pytest.raises(schema.BadOutput):
        schema.parse(["not", "an", "object"])


def test_schema_is_strict_everywhere():
    def walk(node):
        if isinstance(node, dict) and node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        for v in (node.values() if isinstance(node, dict) else node if isinstance(node, list)
                  else []):
            walk(v)
    walk(schema.SCHEMA)


NOW = datetime(2026, 11, 23, 6, 0, tzinfo=UTC)      # 07:00 in Algiers


def test_relative_and_absolute_times():
    cases = {
        "9 h": (datetime(2026, 11, 22, 21, 0, tzinfo=UTC), "hour"),
        "1 j": (datetime(2026, 11, 22, 6, 0, tzinfo=UTC), "day"),
        "2 sem.": (datetime(2026, 11, 9, 6, 0, tzinfo=UTC), "week"),
        "3 min": (datetime(2026, 11, 23, 5, 57, tzinfo=UTC), "minute"),
        "À l'instant": (NOW, "minute"),
        "Hier à 14:05": (datetime(2026, 11, 22, 13, 5, tzinfo=UTC), "minute"),
        "٢ ي": (datetime(2026, 11, 21, 6, 0, tzinfo=UTC), "day"),
        "منذ 3 ساعات": (datetime(2026, 11, 23, 3, 0, tzinfo=UTC), "hour"),
        "21 novembre à 14:30": (datetime(2026, 11, 21, 13, 30, tzinfo=UTC), "minute"),
        "12 mars 2024": (datetime(2024, 3, 12, 11, 0, tzinfo=UTC), "day"),
        "2 déc.": (datetime(2025, 12, 2, 11, 0, tzinfo=UTC), "day"),   # not in the future
    }
    for raw, expected in cases.items():
        assert dates.published(raw, NOW) == expected, raw
    assert dates.published("", NOW) is None
    assert dates.published("Modifié", NOW) is None


def test_capture_time_from_file_names():
    upload = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
    tz = "Africa/Algiers"
    assert dates.captured_at("Capture d'écran 2026-11-23 064458.png", upload, tz) == \
        datetime(2026, 11, 23, 5, 44, 58, tzinfo=UTC)
    assert dates.captured_at("WhatsApp Image 2026-11-23 at 08.21.26 (1).jpeg", upload, tz) == \
        datetime(2026, 11, 23, 7, 21, 26, tzinfo=UTC)
    assert dates.captured_at("Screenshot_20261123-081426.png", upload, tz) == \
        datetime(2026, 11, 23, 7, 14, 26, tzinfo=UTC)
    assert dates.captured_at("capture-www-facebook-com-20261123-053000.png", upload, tz) == \
        datetime(2026, 11, 23, 5, 30, tzinfo=UTC)                       # Scanner: UTC
    assert dates.captured_at("content (1).png", upload, tz) == upload
    assert dates.captured_at("Screenshot_20271123-081426.png", upload, tz) == upload  # future


def test_text_helpers():
    assert text.script_of("مازلت سما عادي تشغلت 5g فقط") == "arabic"
    assert text.script_of("Rani nchrih 190 qaada") == "latin"
    assert text.script_of("MobilisCare يخص") == "mixed"
    assert text.script_of("😂😂 123") == "none"
    assert text.normalise("الصلاحيـــة  أقل!!") == text.normalise("الصلاحيه اقل")
    assert text.dedup_key("H.M", "Salam  khouya 😂") == text.dedup_key("h.m", "salam khouya")
    assert text.continues("مكانش تطبيق الا وفيه بنة الاعطال",
                          "مكانش تطبيق الا وفيه بنة الاعطال والمشاكل")
    assert not text.continues("ok", "ok merci")                       # too short to tell


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


def test_tall_screenshots_are_sent_in_overlapping_parts():
    assert len(vision.prepare(_png(1280, 800))) == 1
    assert len(vision.prepare(_png(498, 1080))) == 1                    # ordinary phone shot
    parts = vision.prepare(_png(400, 2400))
    sizes = [Image.open(io.BytesIO(p)).size for p in parts]
    assert len(parts) == 4 and all(s == (400, 720) for s in sizes)      # 1.8 × width each
    assert sum(h for _, h in sizes) > 2400                              # overlap, nothing lost
    long = [Image.open(io.BytesIO(p)).size for p in vision.prepare(_png(300, 30000))]
    assert len(long) == vision.MAX_PARTS and sum(h for _, h in long) > 30000
