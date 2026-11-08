"""Challenge detection: real challenges are recognised, ordinary pages are not."""

import pytest

from app.browser.detect import Observation, detect
from tests import browser_pages as P


def obs(html: str, status: int = 200, url: str = "https://site.dz/a", headers=None,
        rendered: bool = True, title: str | None = None, text: int | None = None) -> Observation:
    import re
    found = re.search(r"<title>(.*?)</title>", html, re.S)
    t = title if title is not None else (found.group(1) if found else "")
    visible = re.sub(r"<script.*?</script>|<[^>]+>", " ", html, flags=re.S)
    return Observation(status=status, url=url, html=html, title=t or "",
                       text_length=text if text is not None else len(" ".join(visible.split())),
                       headers=headers or {}, rendered=rendered)


@pytest.mark.parametrize("html,status,headers,kind", [
    (P.CLOUDFLARE, 403, {"cf-mitigated": "challenge"}, "cloudflare_challenge"),
    (P.CLOUDFLARE, 200, {}, "cloudflare_challenge"),
    (P.TURNSTILE, 200, {}, "turnstile"),
    (P.RECAPTCHA_WALL, 200, {}, "recaptcha"),
    (P.DATADOME, 403, {"x-datadome": "protected"}, "datadome"),
    (P.GENERIC, 200, {}, "generic_captcha"),
    ("<html><body>Too many requests</body></html>", 429, {}, "rate_limited"),
    ("<html><body>nope</body></html>", 403, {}, "access_denied"),
    ("<html><head><title>x</title></head><body>blocked</body></html>", 403,
     {"x-amzn-waf-action": "captcha"}, "aws_waf"),
])
def test_challenges_detected(html, status, headers, kind):
    c = detect(obs(html, status, headers=headers))
    assert c is not None and c.kind == kind, c
    assert 0.6 <= c.confidence <= 1 and c.signals


def test_login_wall():
    c = detect(obs(P.LOGIN, url="https://site.dz/login?next=/a"))
    assert c and c.kind == "login_wall"


def test_ordinary_pages_are_not_challenges():
    # Article with an invisible reCAPTCHA v3 and a comment form widget: not a challenge.
    assert detect(obs(P.ARTICLE)) is None
    # Static JS shell embedding a captcha script is short only because JS hasn't run.
    shell = P.JS_SHELL.replace(
        "<head>", '<head><script src="https://www.google.com/recaptcha/api.js"></script>')
    assert detect(obs(shell, rendered=False)) is None
    # An article *about* CAPTCHAs is not a challenge.
    about = "<html><head><title>Les CAPTCHA expliqués</title></head><body>" + \
        "Un CAPTCHA demande de vérifier que vous êtes humain. " * 40 + "</body></html>"
    assert detect(obs(about)) is None
    assert detect(obs("<html><body>" + "x " * 1000 + "</body></html>", status=404)) is None
