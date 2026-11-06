"""Seed lexicon of common Algerian Darija expressions.

Status: SEED, NOT VALIDATED. These entries are hints for normalisation and for the AI
annotators; they never decide a label on their own (master prompt §10.4: no keyword-only
classification). Polarity is the usual one *out of context*; negation, sarcasm and context
can flip it. Entries are reviewed through the NLP admin screen before being marked validated.

Each entry: (canonical Latin form, Arabic-script form, Latin variants, meaning_fr, polarity)
polarity ∈ positive | negative | none
"""

SEED: list[tuple[str, str, list[str], str, str]] = [
    ("bzaf", "بزاف", ["bzaf", "bzzaf", "bezaf", "bezzaf", "bzef"], "beaucoup, très", "none"),
    ("mlih", "مليح", ["mlih", "mli7", "mleh", "mli"], "bien, bon", "positive"),
    ("khayeb", "خايب", ["khayeb", "khayb", "5ayeb", "5ayb"], "mauvais, laid", "negative"),
    ("ghali", "غالي", ["ghali", "ghaly"], "cher (prix)", "negative"),
    ("rkhis", "رخيص", ["rkhis", "r5is", "rkhiss"], "pas cher (peut être péjoratif)", "none"),
    ("kifach", "كيفاش", ["kifach", "kifech", "kifash", "kifeh"], "comment", "none"),
    ("wesh", "واش", ["wesh", "wech", "wach", "wash"], "quoi ; salut", "none"),
    ("rani", "راني", ["rani"], "je suis", "none"),
    ("ma3lich", "معليش", ["ma3lich", "maalich", "ma3lish", "m3lich"], "ce n'est pas grave", "none"),
    ("ya3tik saha", "يعطيك الصحة", ["ya3tik saha", "ya3tik sa7a", "yatik saha", "y3tik saha"],
     "merci, bravo", "positive"),
    ("hogra", "حقرة", ["hogra", "hagra", "7ogra", "7agra"], "mépris, injustice subie", "negative"),
    ("3ayi", "عيي", ["3ayi", "3yit", "3ayyi"], "fatigué, à bout", "negative"),
    ("top", "توب", ["top"], "génial", "positive"),
    ("chbab", "شباب", ["chbab", "shbab"], "joli, super", "positive"),
    ("walou", "والو", ["walou", "walo", "walu", "oualou"], "rien", "none"),
    ("dima", "ديما", ["dima", "dayman"], "toujours", "none"),
    ("makach", "ماكاش", ["makach", "makache", "makech", "makanch"], "il n'y a pas", "none"),
    ("7chouma", "حشومة", ["7chouma", "hchouma", "hshouma"], "honte", "negative"),
    ("mrigel", "مريقل", ["mrigel", "mriguel"], "réglé, ça va bien", "positive"),
    ("nichan", "نيشان", ["nichan", "nishan"], "correct, droit", "positive"),
    ("3jebni", "عجبني", ["3jebni", "3ejbni", "ajebni", "3jbni"], "ça m'a plu", "positive"),
    ("ma 3jebnich", "ما عجبنيش", ["ma 3jebnich", "ma3jebnich", "majebnich", "ma3jbnich"],
     "ça ne m'a pas plu", "negative"),
    ("nta3", "نتاع", ["nta3", "ntaa", "nta3a"], "de (possession)", "none"),
    ("bark", "برك", ["bark", "berk"], "seulement ; assez", "none"),
    ("kima", "كيما", ["kima", "kima"], "comme", "none"),
    ("sahbi", "صاحبي", ["sahbi", "sa7bi", "sahbi"], "mon ami", "none"),
    ("khoya", "خويا", ["khoya", "5oya", "khouya"], "mon frère", "none"),
    ("drahem", "دراهم", ["drahem", "dra7em", "drahm"], "argent", "none"),
    ("zwin", "زوين", ["zwin", "zouin"], "beau (usage ouest)", "positive"),
    ("tbahdil", "تبهديل", ["tbahdil", "tbehdil"], "humiliation, pagaille", "negative"),
]

# Function words typical of Darija (not polarity-bearing): detection hints only.
DARIJA_MARKERS_LATIN = {
    "rak", "rah", "rahi", "rahom", "nta", "nti", "howa", "hiya", "hna", "3la", "3lach", "wla",
    "bach", "ki", "ida", "3ndi", "3andek", "kayen", "kayna", "mazal", "chhal", "win", "hadi",
    "hada", "hadak", "dork", "drok", "ghadwa", "lyoum", "baraka", "brk", "chwiya", "chwia",
    "w9t", "wa9t", "bla", "fih", "fiha", "ndir", "dir", "ndiro", "rana", "rakom",
}
DARIJA_MARKERS_ARABIC = {
    "راني", "راك", "راه", "راهي", "واش", "كيفاش", "بزاف", "ماكانش", "ماكاش", "نتاع", "ديما",
    "برك", "كيما", "هاذ", "هادي", "علاش", "وين", "شحال", "مازال", "درك", "دروك", "شوية",
    "باش", "كاين", "كاينة", "والو", "خويا", "صاحبي", "مليح", "خايب", "عجبني",
}
MSA_MARKERS = {
    "الذي", "التي", "الذين", "هذا", "هذه", "لقد", "إن", "أن", "سوف", "ليس", "ليست", "قد",
    "لكن", "حيث", "كما", "إلى", "عن", "على", "منذ", "خلال", "بين", "عبر", "وفق", "أيضا",
}
FRENCH_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "est", "sont", "pas", "ne", "je",
    "tu", "il", "elle", "nous", "vous", "ils", "elles", "que", "qui", "pour", "dans", "sur",
    "avec", "mais", "ou", "donc", "car", "ce", "cette", "ces", "mon", "ma", "mes", "son", "sa",
    "ses", "leur", "très", "trop", "plus", "moins", "même", "aussi", "au", "aux", "c'est",
    "j'ai", "n'est", "y", "a", "été", "être", "avoir", "fait", "tout", "rien", "jamais",
}
ENGLISH_STOPWORDS = {
    "the", "and", "is", "are", "was", "were", "not", "this", "that", "with", "for", "you",
    "your", "they", "have", "has", "but", "very", "what", "why", "how", "of", "to", "in", "it",
    "be", "been", "will", "would", "can", "could", "should", "my", "our", "their",
}


def variant_map() -> dict[str, str]:
    """Latin spelling variant → canonical form (used by the normaliser)."""
    out: dict[str, str] = {}
    for canonical, _ar, variants, _fr, _pol in SEED:
        for v in variants:
            out[v.lower()] = canonical
    return out
