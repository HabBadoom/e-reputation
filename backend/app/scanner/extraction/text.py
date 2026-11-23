"""Text helpers for extracted entries: script detection and normalisation for deduplication.

Normalisation is for comparing only; the stored text stays exactly as transcribed.
"""

import hashlib
import re
import unicodedata

_ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")
_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")  # + tatweel
_ARABIC_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه",
                              "ؤ": "و", "ئ": "ي", "ک": "ك", "ی": "ي",
                              "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
                              "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"})
_NOISE = re.compile(r"[^\w؀-ۿ]+", re.UNICODE)


def script_of(text: str) -> str:
    """'arabic', 'latin', 'mixed' or 'none' (letters only; digits and emojis do not count)."""
    a = len(_ARABIC.findall(text))
    latin = len(_LATIN.findall(text))
    if not a and not latin:
        return "none"
    if a and latin:
        small = min(a, latin) / (a + latin)
        if small >= 0.15:
            return "mixed"
    return "arabic" if a >= latin else "latin"


def normalise(text: str) -> str:
    """Comparison form: NFKC, lower case, Arabic letter variants folded, diacritics, tatweel,
    punctuation, emojis and spacing removed."""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    t = _DIACRITICS.sub("", t).translate(_ARABIC_FOLD)
    return _NOISE.sub(" ", t).strip()


def dedup_key(author: str, text: str) -> str:
    return hashlib.sha256(f"{normalise(author)}\x1f{normalise(text)}".encode()).hexdigest()


def continues(short: str, full: str, *, min_len: int = 15) -> bool:
    """True if `short` (a cut or truncated copy) is the beginning of `full`."""
    a, b = normalise(short), normalise(full)
    return len(a) >= min_len and len(b) > len(a) and b.startswith(a)
