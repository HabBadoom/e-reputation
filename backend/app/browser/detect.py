"""Automatic detection of anti-bot challenges (CAPTCHA, JavaScript challenges, WAF blocks,
login walls, rate limiting) in an HTTP response or a rendered page.

Detection only. When a challenge is found the visit stops; nothing here tries to solve, evade
or bypass it (docs/CAPTCHA_INTERVENTIONS.md).

A CAPTCHA widget alone is not a challenge (contact forms and invisible reCAPTCHA v3 are common):
provider fingerprints count only in a *blocking context* — an error status, a challenge title,
or an almost empty page once JavaScript has run.
Headers and paths that exist only on challenge pages (Cloudflare `cf-mitigated`, the
`/cdn-cgi/challenge-platform/` script, DataDome's captcha host…) are enough by themselves.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DETECTOR_VERSION = "1.0"
THRESHOLD = 0.6
SHORT_PAGE = 800          # visible characters below which a page is "almost empty"

_TITLE_CHALLENGE = re.compile(
    r"just a moment|attention required|un instant|un moment|access denied|accès refusé|"
    r"are you a robot|verify you are human|security check|vérification de sécurité|"
    r"êtes-vous un robot|pardon our interruption|request blocked|captcha|"
    r"لحظة|التحقق من أنك|تحقق أمني", re.I)
_HUMAN_TEXT = re.compile(
    r"verify (that )?you are (a )?human|i'?m not a robot|are you a robot|"
    r"prove you are human|complete the security check|"
    r"vérifie[rz]? que vous (n'êtes pas un robot|êtes (un )?humain)|je ne suis pas un robot|"
    r"تحقق من أنك (لست روبوت|إنسان)|أنا لست روبوت", re.I)
_LOGIN_PATH = re.compile(r"/(login|log-in|signin|sign-in|connexion|se-connecter|auth|"
                         r"account/login|accounts/login|users/sign_in)\b", re.I)
_LOGIN_TITLE = re.compile(r"log ?in|sign ?in|connexion|se connecter|identifiez-vous|"
                          r"تسجيل الدخول", re.I)
_PASSWORD = re.compile(r"<input[^>]+type=[\"']?password", re.I)
_CAPTCHA_INPUT = re.compile(r"<(input|img)[^>]+(name|id|src)=[\"'][^\"']*captcha", re.I)


@dataclass
class Observation:
    status: int
    url: str
    html: str
    title: str = ""
    text_length: int = 0
    headers: dict[str, str] = field(default_factory=dict)   # lower-case names
    frame_urls: list[str] = field(default_factory=list)
    rendered: bool = False     # True for a page rendered by the browser (JavaScript ran)


@dataclass
class Challenge:
    kind: str
    confidence: float
    signals: list[str]


def _has(hay: str, *needles: str) -> list[str]:
    return [n for n in needles if n in hay]


def detect(o: Observation) -> Challenge | None:
    html = o.html[:400_000].lower()
    frames = " ".join(o.frame_urls).lower()
    hay = html + " " + frames
    h = {k.lower(): v.lower() for k, v in o.headers.items()}
    title = o.title.strip()
    short = o.text_length < SHORT_PAGE
    blocking_status = o.status in (401, 403, 429, 503)
    challenge_title = bool(_TITLE_CHALLENGE.search(title))
    blocking = blocking_status or short or challenge_title
    ctx = [s for s, on in ((f"status:{o.status}", blocking_status),
                           ("short_page", short), ("challenge_title", challenge_title)) if on]
    found: list[Challenge] = []

    def add(kind: str, conf: float, signals: list[str]) -> None:
        found.append(Challenge(kind, conf, signals))

    # --- fingerprints that only exist on challenge pages
    if h.get("cf-mitigated") == "challenge":
        add("cloudflare_challenge", 0.99, ["header:cf-mitigated=challenge"])
    if s := _has(html, "/cdn-cgi/challenge-platform/", "_cf_chl_opt", "cf-chl-widget",
                 "cf_chl_prog"):
        add("cloudflare_challenge", 0.95, s)
    if s := _has(hay, "captcha-delivery.com", "ct.captcha-delivery.com"):
        add("datadome", 0.95, s)
    if "x-datadome" in h and blocking_status:
        add("datadome", 0.9, ["header:x-datadome", *ctx])
    if s := _has(hay, "px-captcha", "_pxcaptcha", "captcha.px-cdn.net", "/px/captcha"):
        add("perimeterx", 0.9 if blocking else 0.65, s + ctx)
    if h.get("x-amzn-waf-action") in ("captcha", "challenge"):
        add("aws_waf", 0.97, ["header:x-amzn-waf-action"])
    if s := _has(html, "awswafcaptcha", "aws-waf-token", "captcha.awswaf.com"):
        add("aws_waf", 0.9 if blocking else 0.6, s + ctx)

    # --- widgets that also appear on normal pages: need a blocking context
    widgets = (
        ("turnstile", ("challenges.cloudflare.com/turnstile", "cf-turnstile")),
        ("hcaptcha", ("hcaptcha.com/1/api.js", "class=\"h-captcha\"", "newassets.hcaptcha.com")),
        ("recaptcha", ("google.com/recaptcha/api", "recaptcha/api2", "class=\"g-recaptcha\"",
                       "recaptcha/enterprise.js")),
    )
    # A static HTML shell is short before JavaScript runs: only trust "short" once rendered.
    widget_context = blocking_status or challenge_title or (short and o.rendered)
    for kind, needles in widgets:
        if (s := _has(hay, *needles)) and widget_context:
            add(kind, 0.9 if (blocking_status or challenge_title) else 0.75, s + ctx)

    # --- WAF block pages
    if o.status == 403 and "reference #" in html and (
            "akamai" in h.get("server", "") or title.lower() == "access denied"):
        add("akamai", 0.85, ["status:403", "akamai_reference"])

    # --- generic human verification
    if (short or blocking_status) and (_HUMAN_TEXT.search(html) or _CAPTCHA_INPUT.search(html)):
        add("generic_captcha", 0.75, ["human_verification_text_or_input", *ctx])

    # --- login walls (we never log in)
    path = urlsplit(o.url).path
    if _PASSWORD.search(html) and (short or o.status in (401, 403)) and (
            _LOGIN_PATH.search(path) or _LOGIN_TITLE.search(title)):
        add("login_wall", 0.8, ["password_field", "login_path_or_title", *ctx])
    elif o.status == 401:
        add("login_wall", 0.7, ["status:401"])

    if o.status == 429:
        add("rate_limited", 0.9, ["status:429"])
    if o.status == 403 and short and not found:
        add("access_denied", 0.6, ["status:403", "short_page"])

    best = max(found, key=lambda c: c.confidence, default=None)
    return best if best and best.confidence >= THRESHOLD else None
