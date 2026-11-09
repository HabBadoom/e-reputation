"""Amounts in French words, as Algerian invoices state them ("Arrêtée la présente facture à la
somme de …"). French spelling rules: 1990 reform hyphens are NOT used (traditional spelling,
the usual one on Algerian commercial documents)."""

UNITS = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix",
         "onze", "douze", "treize", "quatorze", "quinze", "seize", "dix-sept", "dix-huit",
         "dix-neuf"]
TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}

CURRENCIES = {
    "DZD": ("dinar algérien", "dinars algériens", "centime", "centimes"),
    "EUR": ("euro", "euros", "centime", "centimes"),
    "USD": ("dollar des États-Unis", "dollars des États-Unis", "cent", "cents"),
}


def _below_100(n: int) -> str:
    if n < 20:
        return UNITS[n]
    t, u = divmod(n, 10)
    if t in (7, 9):                       # soixante-dix…, quatre-vingt-dix…
        base = "soixante" if t == 7 else "quatre-vingt"
        if t == 7 and u == 1:
            return "soixante et onze"
        return f"{base}-{UNITS[10 + u]}"
    if t == 8:
        return "quatre-vingts" if u == 0 else f"quatre-vingt-{UNITS[u]}"
    if u == 0:
        return TENS[t]
    if u == 1:
        return f"{TENS[t]} et un"
    return f"{TENS[t]}-{UNITS[u]}"


def _below_1000(n: int, final: bool) -> str:
    """`final`: nothing follows (plural "cents"/"quatre-vingts" only then)."""
    h, r = divmod(n, 100)
    parts = []
    if h:
        if h == 1:
            parts.append("cent")
        else:
            parts.append(f"{UNITS[h]} cents" if r == 0 and final else f"{UNITS[h]} cent")
    if r:
        w = _below_100(r)
        if not final and w == "quatre-vingts":
            w = "quatre-vingt"
        parts.append(w)
    return " ".join(parts)


def number_to_words(n: int) -> str:
    if n < 0:
        raise ValueError("negative amount")
    if n == 0:
        return UNITS[0]
    out = []
    for value, sing, plur in ((10**9, "milliard", "milliards"), (10**6, "million", "millions")):
        q, n = divmod(n, value)
        if q:
            out.append(f"{_below_1000(q, True) if q > 1 else 'un'} {sing if q == 1 else plur}")
    q, n = divmod(n, 1000)
    if q:
        out.append("mille" if q == 1 else f"{_below_1000(q, False)} mille")
    if n:
        out.append(_below_1000(n, True))
    return " ".join(out)


def amount_in_words(amount_minor: int, currency: str) -> str:
    units, minor = divmod(amount_minor, 100)
    sing, plur, msing, mplur = CURRENCIES.get(currency, (currency, currency, "", ""))
    words = f"{number_to_words(units)} {sing if units <= 1 else plur}"
    if minor and msing:
        words += f" et {number_to_words(minor)} {msing if minor == 1 else mplur}"
    return words[0].upper() + words[1:]
