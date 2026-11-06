"""Matching helpers. Normalisation here is ONLY used to decide whether a text mentions a
term; the original text is always stored unchanged (master prompt §10.3)."""

import hashlib
import re
import unicodedata

_AR_DIACRITICS = re.compile(r"[ً-ْٰـ]")   # harakat, superscript alef, tatweel
_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و",
                         "ئ": "ي"})
_SPACES = re.compile(r"\s+")


def _norm_chars(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    s = _AR_DIACRITICS.sub("", s).translate(_AR_MAP)
    return "".join(ch for ch in unicodedata.normalize("NFKD", s)
                   if not (unicodedata.combining(ch) and ord(ch) < 0x0600))   # é→e, keep Arabic


def normalize_for_match(s: str) -> str:
    return _SPACES.sub(" ", _norm_chars(s or "")).strip()


def _pattern(term: str) -> re.Pattern[str]:
    t = re.escape(normalize_for_match(term)).replace(r"\ ", r"\s+")
    # Not preceded/followed by a letter or digit (works for Latin and Arabic scripts).
    return re.compile(rf"(?<![^\W_]){t}(?![^\W_])")


def find_terms(text: str, terms: list[str]) -> list[str]:
    """Terms (as given) that occur in `text` as whole words, in input order."""
    norm = normalize_for_match(text)
    return [t for t in terms if t.strip() and _pattern(t).search(norm)]


def excerpt_around(text: str, terms: list[str], width: int = 300) -> str:
    """A window of the ORIGINAL text around the first matching term (or its start)."""
    clean = _SPACES.sub(" ", text or "").strip()
    norm: list[str] = []
    index: list[int] = []          # position in `clean` for each normalised character
    for i, c in enumerate(clean):
        for ch in _norm_chars(c):
            norm.append(ch)
            index.append(i)
    joined = "".join(norm)
    for t in terms:
        if not t.strip():
            continue
        m = _pattern(t).search(joined)
        if m:
            start = index[m.start()]
            a = max(0, start - width // 2)
            b = min(len(clean), start + width // 2)
            return ("…" if a else "") + clean[a:b].strip() + ("…" if b < len(clean) else "")
    return clean[:width] + ("…" if len(clean) > width else "")


def content_hash(title: str, text: str) -> str:
    """Hash of the normalised content, to spot the same article republished elsewhere."""
    return hashlib.sha256(normalize_for_match(f"{title}\n{text}").encode()).hexdigest()
