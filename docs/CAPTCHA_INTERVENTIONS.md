# CAPTCHA and anti-bot challenges

**Decision (2026-11-08, confirmed with the founder):** challenges are detected automatically and
answered autonomously; they are **never solved or bypassed** — no solving services, no AI solving,
no rotating identities or fingerprints to evade them, no human-like behaviour simulation. A
challenge is the site refusing automated access; respecting it is a legal requirement (unauthorised
access, terms of use, Algerian Law 18-07) and part of the master prompt (§9 read as "must not",
rule 22.12). Nobody has to act for collection to continue.

## Detection (`app/browser/detect.py`)

Runs on every static page fetch and every rendered page. Recognised: Cloudflare challenge and
Turnstile, reCAPTCHA, hCaptcha, DataDome, HUMAN/PerimeterX, Akamai, AWS WAF, generic
"verify you are human" pages, login walls, HTTP 429 and 403 blocks. Widgets that also appear on
normal pages (invisible reCAPTCHA, contact forms) count only in a blocking context (error status,
challenge title, or an almost empty page once rendered); signatures unique to challenge pages are
enough alone. Each event records kind, confidence and the signals seen.

## Autonomous response (`app/browser/challenges.py`)

| State | What happens |
|---|---|
| `DETECTED` | challenge recognised, event stored |
| `PAUSED` | automation stops immediately: no click, no retry, browser context destroyed |
| `FALLBACK` | the mention keeps its search snippet or feed text (`blocked_by_challenge`); the domain cools down for **all** tenants: 1 h, 4 h, 16 h, then 72 h if it repeats; other sources continue |
| `SOURCE_RESTRICTED` | after `REPINTEL_BROWSER_RESTRICTED_AFTER_CHALLENGES` (3) challenges in a row the source becomes `RESTRICTED` with the reason; coverage limits appear in the UI and reports; a "collection failure" alert rule can notify |

A successful visit resets the counters. The mission ends `CANCELLED` (`challenge:<kind>`).
Manual checks and new missions for a cooling domain are refused at validation.

## Getting access legitimately

For an important site that blocks crawlers: an official API or partner feed, a licensed data
provider, or an agreement with the publisher to allow our user agent. These are added as
connectors with their own compliance record.

## Human intervention

The master prompt's optional human flow (an operator completing the check in a remote view) is
**not built**: it would still be a way around the site's refusal and the founder chose full
autonomy. The `WAITING_FOR_HUMAN` mission state is reserved and unused.
