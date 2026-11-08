"""Synthetic pages imitating the *structure* of common challenge pages (no third-party code),
served by a local HTTP server in the browser tests and used directly in detector tests."""

ARTICLE = """<!doctype html><html lang="fr"><head><title>Réseau mobile : les abonnés de Djezzy
se plaignent</title><meta property="article:published_time" content="2026-11-05T09:30:00+01:00">
<script src="https://www.google.com/recaptcha/api.js?render=sitekey"></script></head>
<body><article><h1>Réseau mobile</h1><p>""" + ("Les abonnés de Djezzy à Oran signalent des coupures "
"depuis le début de la semaine. " * 30) + """</p><form><div class="g-recaptcha"></div>
<textarea name="comment"></textarea></form></article></body></html>"""

# Static HTML is an empty shell; the article appears only once JavaScript runs.
JS_SHELL = """<!doctype html><html><head><title>Actualité</title></head><body><div id="app"></div>
<noscript>Activez JavaScript pour lire cet article.</noscript>
<script>
document.getElementById('app').innerHTML = '<article><h1>Coupure internet</h1><p>' +
  'Le service client de Djezzy a reconnu une panne réseau dans la wilaya de Blida. '.repeat(20) +
  '</p></article>';
fetch('http://169.254.169.254/latest/meta-data/').catch(() => {});
fetch('http://10.0.0.1/admin').catch(() => {});
</script></body></html>"""

CLOUDFLARE = """<!doctype html><html><head><title>Just a moment...</title></head><body>
<div id="challenge-stage"></div><script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>
</body></html>"""

TURNSTILE = """<!doctype html><html><head><title>Vérification</title>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script></head>
<body><div class="cf-turnstile" data-sitekey="x"></div></body></html>"""

RECAPTCHA_WALL = """<!doctype html><html><head><title>Security check</title>
<script src="https://www.google.com/recaptcha/api.js"></script></head>
<body><form><div class="g-recaptcha" data-sitekey="x"></div><button>Continue</button></form></body></html>"""

DATADOME = """<!doctype html><html><head><title>site.dz</title></head><body>
<iframe src="https://geo.captcha-delivery.com/captcha/?initialCid=x"></iframe></body></html>"""

LOGIN = """<!doctype html><html><head><title>Connexion</title></head><body>
<form action="/login" method="post"><input name="email"><input type="password" name="pw">
<button>Se connecter</button></form></body></html>"""

GENERIC = """<!doctype html><html><head><title>site</title></head><body>
<p>Merci de vérifier que vous n'êtes pas un robot.</p><img src="/captcha.png"><input name="captcha_code">
</body></html>"""

ROBOTS = "User-agent: *\nAllow: /\n"
