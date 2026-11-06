"""Darija/Arabizi normalisation. The original text is never modified; the normalised form is
stored beside it, with the list of substitutions so it can be traced back (§10.3)."""

import re
import unicodedata

from app.nlp.lexicon import variant_map

_VARIANTS = variant_map()
_TOKEN = re.compile(r"[\w'’]+|[^\w\s]+|\s+", re.UNICODE)
_ELONGATION = re.compile(r"(\w)\1{2,}", re.UNICODE)     # "bezzzzaf" → "bezzaf"
_AR_DIACRITICS = re.compile(r"[ً-ْـ]")    # harakat + tatweel
# Multi-word canonical forms (matched before single tokens).
_PHRASES = sorted({v for v in _VARIANTS if " " in v}, key=len, reverse=True)


def normalize(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (normalised text, [(original token, replacement), …])."""
    s = unicodedata.normalize("NFKC", text or "")
    changes: list[tuple[str, str]] = []
    if _AR_DIACRITICS.search(s):        # harakat split words for \w: strip them first
        stripped = _AR_DIACRITICS.sub("", s)
        changes.append(("[arabic diacritics/tatweel]", "removed"))
        s = stripped
    low = s.lower()
    for p in _PHRASES:
        for m in re.finditer(rf"(?<!\w){re.escape(p)}(?!\w)", low):
            changes.append((s[m.start():m.end()], _VARIANTS[p]))
        low = re.sub(rf"(?<!\w){re.escape(p)}(?!\w)", _VARIANTS[p], low)
    out = []
    for tok in _TOKEN.findall(low):
        if not tok.strip() or not tok[0].isalnum():
            out.append(tok)
            continue
        t = _AR_DIACRITICS.sub("", tok)
        t = _ELONGATION.sub(r"\1\1", t)
        canon = _VARIANTS.get(t, _VARIANTS.get(_ELONGATION.sub(r"\1", t)))
        if canon and canon != tok:
            changes.append((tok, canon))
            t = canon
        elif t != tok:
            changes.append((tok, t))
        out.append(t)
    return re.sub(r"\s+", " ", "".join(out)).strip(), changes
