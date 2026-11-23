"""What the vision model is asked for, and how its answer is checked.

The model transcribes; it does not interpret. Its answer must follow a strict JSON schema
(OpenAI Structured Outputs); `parse()` re-checks it anyway and drops anything inconsistent.
"""

from dataclasses import dataclass, field
from typing import Any

PROMPT_VERSION = "scanner-extract-v1"

PLATFORMS = ("facebook", "instagram", "tiktok", "youtube", "linkedin", "x", "web", "unknown")
VIEWS = ("feed", "post_page", "comments_dialog", "photo_viewer", "group", "profile", "search",
         "other")
ISSUES = ("cropped", "low_resolution", "background_ignored", "not_social_media",
          "unreadable_parts", "several_posts", "no_text")

SYSTEM_PROMPT = """You transcribe screenshots of social networks (mostly Facebook, in Algeria) for a
collection tool. Report only what is visibly written. Never translate, correct, complete,
summarise or judge anything. No opinion, sentiment or topic.

Text rules:
- Copy text exactly as displayed: original script and spelling (Arabic, Algerian Darija in Arabic
  or Latin letters, French, English, mixed), emojis, and line breaks as \\n.
- Arabic is right-to-left: give each line in its logical reading order.
- If text ends with "En voir plus", "Afficher la suite", "Voir plus", "See more", "عرض المزيد"
  or "…", set text_truncated=true and do not include those words.
- A name at the start of a reply that tags someone is part of the reply text.

What to ignore:
- Interface text: buttons and links (J'aime, Répondre, Commenter, Partager, Like, Reply, Share,
  Modifié, Suivre), menus, tabs, the comment box ("Écrivez un commentaire…"), "Voir plus de
  réponses", "Voir 4 réponses", "Plus pertinents", navigation and notification bars, phone status
  bar, ads and suggestions in side columns, "Cette publication vous intéresse ?".
- When a dialog or photo viewer is open, transcribe only the foreground (the dialog, or the photo
  and its comment column). Ignore the dimmed page behind it and add the issue
  "background_ignored".

Structure:
- posts: every publication visible (usually one). author = page or person name as shown;
  author_is_page=true for pages/groups/brands. If a dialog title says "Publication de X", the post
  author is X even when the post body is not visible (then text is ""). image_text = words written
  inside the post's picture or graphic (poster, banner, screenshot), in reading order, "" if none;
  never profile pictures or logos without words.
- comments: every comment and reply, top to bottom. ref = 1, 2, 3… in reading order. A reply
  (indented under a comment) has parent_ref = the ref of the comment it answers; a top-level
  comment has parent_ref = null. post_ref = the ref of the post it belongs to, or null if the post
  is not in the screenshot.
- relative_time: the time label exactly as shown ("9 h", "1 j", "2 sem", "Hier à 14:05",
  "12 mars"), "" if none.
- Counters as integers: "1,2 k" = 1200, "12 commentaires" = 12; null if not shown.
- partially_visible=true when the entry is cut by the top or bottom edge of the screenshot.
- quality.confidence: from 0 to 1, how sure you are that names and texts are copied exactly.
"""

USER_PROMPT = ("Transcribe this screenshot. Capture time (for reference only, do not convert "
               "times): {captured}.")
USER_PROMPT_PARTS = ("These {n} images are consecutive parts of one tall screenshot, top to "
                     "bottom, with some overlap: transcribe them as one screenshot and do not "
                     "repeat entries that appear twice in the overlap. Capture time (for "
                     "reference only): {captured}.")


def _nullable(t: str) -> dict[str, Any]:
    return {"type": [t, "null"]}


_COUNTS = {"reactions_count": _nullable("integer"), "comments_count": _nullable("integer"),
           "shares_count": _nullable("integer")}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["context", "posts", "comments", "quality"],
    "properties": {
        "context": {
            "type": "object", "additionalProperties": False,
            "required": ["platform", "view", "page_or_group", "dialog_title"],
            "properties": {
                "platform": {"type": "string", "enum": list(PLATFORMS)},
                "view": {"type": "string", "enum": list(VIEWS)},
                "page_or_group": {"type": "string"},
                "dialog_title": {"type": "string"},
            },
        },
        "posts": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["ref", "author", "author_is_page", "text", "text_truncated",
                             "image_text", "relative_time", "partially_visible", *_COUNTS],
                "properties": {
                    "ref": {"type": "integer"},
                    "author": {"type": "string"},
                    "author_is_page": {"type": "boolean"},
                    "text": {"type": "string"},
                    "text_truncated": {"type": "boolean"},
                    "image_text": {"type": "string"},
                    "relative_time": {"type": "string"},
                    "partially_visible": {"type": "boolean"},
                    **_COUNTS,
                },
            },
        },
        "comments": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["ref", "parent_ref", "post_ref", "author", "text", "text_truncated",
                             "relative_time", "reactions_count", "partially_visible"],
                "properties": {
                    "ref": {"type": "integer"},
                    "parent_ref": _nullable("integer"),
                    "post_ref": _nullable("integer"),
                    "author": {"type": "string"},
                    "text": {"type": "string"},
                    "text_truncated": {"type": "boolean"},
                    "relative_time": {"type": "string"},
                    "reactions_count": _nullable("integer"),
                    "partially_visible": {"type": "boolean"},
                },
            },
        },
        "quality": {
            "type": "object", "additionalProperties": False,
            "required": ["confidence", "issues"],
            "properties": {
                "confidence": {"type": "number"},
                "issues": {"type": "array", "items": {"type": "string", "enum": list(ISSUES)}},
            },
        },
    },
}


@dataclass
class Entry:
    kind: str                     # post | comment | reply
    ref: int
    parent: int | None            # index in Result.entries
    author: str
    author_is_page: bool
    text: str
    image_text: str
    text_truncated: bool
    partially_visible: bool
    relative_time: str
    reactions_count: int | None = None
    comments_count: int | None = None
    shares_count: int | None = None


@dataclass
class Result:
    platform: str
    context: dict[str, str]
    entries: list[Entry] = field(default_factory=list)
    confidence: float = 0.0
    issues: list[str] = field(default_factory=list)


def _s(v: Any, limit: int) -> str:
    return (v if isinstance(v, str) else "").strip()[:limit]


def _n(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2**31 else None


class BadOutput(Exception):
    pass


def parse(data: Any) -> Result:
    """Model JSON → ordered entries (posts, then each post's comments and replies in reading
    order). Unknown references are dropped, never guessed; empty entries are skipped."""
    if not isinstance(data, dict):
        raise BadOutput("not an object")
    ctx = data.get("context") if isinstance(data.get("context"), dict) else {}
    platform = ctx.get("platform") if ctx.get("platform") in PLATFORMS else "unknown"
    quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
    conf = quality.get("confidence")
    conf = min(1.0, max(0.0, float(conf))) if isinstance(conf, int | float) else 0.0
    issues = [i for i in quality.get("issues") or [] if i in ISSUES]
    result = Result(platform=platform, confidence=round(conf, 2), issues=sorted(set(issues)),
                    context={"view": ctx.get("view") if ctx.get("view") in VIEWS else "other",
                             "page_or_group": _s(ctx.get("page_or_group"), 200),
                             "dialog_title": _s(ctx.get("dialog_title"), 200)})

    post_index: dict[int, int] = {}
    for p in data.get("posts") or []:
        if not isinstance(p, dict):
            continue
        e = Entry("post", _n(p.get("ref")) or len(post_index) + 1, None,
                  _s(p.get("author"), 200), bool(p.get("author_is_page")),
                  _s(p.get("text"), 20000), _s(p.get("image_text"), 5000),
                  bool(p.get("text_truncated")), bool(p.get("partially_visible")),
                  _s(p.get("relative_time"), 60), _n(p.get("reactions_count")),
                  _n(p.get("comments_count")), _n(p.get("shares_count")))
        if not (e.author or e.text or e.image_text) or e.ref in post_index:
            continue
        post_index[e.ref] = len(result.entries)
        result.entries.append(e)

    raw = [c for c in data.get("comments") or [] if isinstance(c, dict)]
    by_ref = {c["ref"]: c for c in raw if _n(c.get("ref")) is not None}
    has_children = {c.get("parent_ref") for c in raw}
    comment_index: dict[int, int] = {}
    for c in raw:
        ref = _n(c.get("ref"))
        if ref is None or ref in comment_index:
            continue
        text, author = _s(c.get("text"), 20000), _s(c.get("author"), 200)
        if not text and ref not in has_children:
            continue                       # sticker/GIF-only comment: nothing to collect
        parent_ref = _n(c.get("parent_ref"))
        if parent_ref == ref or (parent_ref is not None and parent_ref not in by_ref):
            parent_ref = None
        if parent_ref is not None and parent_ref in comment_index:
            parent, kind = comment_index[parent_ref], "reply"
        elif parent_ref is not None:
            parent, kind = None, "reply"   # answers a comment that was skipped or comes later
        else:
            post_ref = _n(c.get("post_ref"))
            parent, kind = post_index.get(post_ref) if post_ref is not None else None, "comment"
            if parent is None and len(post_index) == 1:
                parent = next(iter(post_index.values()))
        comment_index[ref] = len(result.entries)
        result.entries.append(Entry(kind, ref, parent, author, False, text, "",
                                    bool(c.get("text_truncated")),
                                    bool(c.get("partially_visible")),
                                    _s(c.get("relative_time"), 60), _n(c.get("reactions_count"))))
    if not result.entries and "no_text" not in result.issues:
        result.issues.append("no_text")
    return result
