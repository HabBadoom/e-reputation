"""Writing-system heuristic for brand aliases.

Only used to pre-fill the `script` field; the user can override it. This is not language
identification and makes no NLP accuracy claim (that's Milestone 4 and its benchmark).
"""

import re
import unicodedata

_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_TIFINAGH = re.compile(r"[\u2D30-\u2D7F]")
# Arabizi uses digits for Arabic sounds: 2 (ء), 3 (ع), 7 (ح), 9 (ق), adjacent to letters.
# Requires a word with >= 2 letters, so "3G" or "Studio 7a" are not mistaken for Arabizi.
_ARABIZI = re.compile(
    r"\b(?=[a-z0-9]*[a-z][a-z0-9]*[a-z])[a-z0-9]*(?:[a-z][2379]|[2379][a-z])[a-z0-9]*\b",
    re.IGNORECASE,
)


def _is_latin(text: str) -> bool:
    return all(not ch.isalpha() or unicodedata.name(ch, "").startswith("LATIN") for ch in text)


def guess_script(text: str) -> str:
    if _TIFINAGH.search(text):
        return "tifinagh"
    if _ARABIC.search(text):
        return "arabic"
    if _is_latin(text):
        return "arabizi" if _ARABIZI.search(text) else "latin"
    return "other"
