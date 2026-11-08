# Browser fleet (Milestone 6)

Headless Chromium (Playwright) workers that open public pages whose text only appears once
JavaScript runs. Separate process: `python -m app.browser.worker` (compose service
`browser-worker`, scale horizontally). Code: `backend/app/browser/`.

## Components (master prompt §8.1)

| Component | Where |
|---|---|
| Fleet Manager / Worker Registry | `browser_workers` + `browser_worker_heartbeat()`; platform admins see it in **Browser fleet → Fleet** |
| Worker Scheduler | `browser_claim_missions()` — free contexts on the worker, priority then age, one-per-domain concurrency and spacing (`browser_domain_policies`), domain cool-down, per-tenant concurrency |
| Mission Orchestrator | `app/browser/missions.py`; the state machine is enforced by a database trigger |
| Context / Session Manager | `app/browser/runtime.py` — one fresh context per mission, `browser_sessions` row per context |
| Resource Monitor | worker takes no new mission below `REPINTEL_BROWSER_MIN_FREE_MEMORY_MB`; metrics in heartbeats |
| Health Monitor | crashed browser relaunched, browser recycled every `REPINTEL_BROWSER_RESTART_AFTER_MISSIONS` visits |
| Cleanup Manager | context closed after every visit and verified (`cleanup_verified`); `browser_recover()` re-queues missions of silent workers, fails them after `max_attempts`, expires late ones and puts their mentions back to an honest status |
| Permission Guard | `app/browser/guard.py` — authorization record at creation **and** again just before navigation |

## Mission lifecycle

```
CREATED → VALIDATING → QUEUED → ASSIGNED → RUNNING → COMPLETED
              │           │         │          ├→ QUEUED (retry with back-off: 2, 4, 8 min)
              └→ FAILED   │         │          ├→ FAILED (blocked address, retries exhausted)
                          │         │          └→ PAUSED → CANCELLED (challenge detected)
                          └→ EXPIRED (deadline, 24 h)   CANCELLED (user) from any non-final state
```
`WAITING_FOR_HUMAN` exists in the schema but nothing routes missions there: the product runs
autonomously (see `CAPTCHA_INTERVENTIONS.md`). Every transition is written to
`browser_session_events` by a trigger, whoever makes it (API, worker, recovery function).

A mission never starts without an authorization record: browser connector approved (blocked in
production while its legal review is pending), public address, domain allowed, robots.txt
allows our user agent, domain not cooling down, daily plan quota (`max_browser_missions_per_day`).

## When missions are created

- A monitoring run fetched a page by HTTP, the brand was not in it, and the page is an
  application shell (almost no text, empty `#root/#app/#__next`, "enable JavaScript" notice)
  → `render_page` mission, mention marked `pending_render` (max `REPINTEL_BROWSER_MISSIONS_PER_RUN`).
- Source set to **Always browser** (`render_mode = browser`) → its search hits are rendered directly;
  **HTML only** never uses the browser.
- **Check with browser** on a website source → `check_source` mission (coverage, feeds).

After rendering, the mention is verified on the rendered text (excerpt, matched terms,
publication date from page metadata) and re-analysed if its text changed.

## Isolation and network

- Fresh context per mission: no stored profile, cookies, local storage, cache or service workers;
  no downloads, no permissions, dialogs dismissed, pop-ups closed; destroyed after the visit.
- No authenticated sessions in this milestone: the fleet never logs in anywhere.
- Every request (document, redirects, scripts, XHR, iframes) passes the same address rules as
  the HTTP fetcher; requests are fetched without following redirects and **each hop is
  re-checked** (Chromium's own redirects bypass request interception otherwise). Images, media
  and fonts are not downloaded.
- Honest user agent (`REPINTEL_CRAWLER_USER_AGENT`), one page at a time per domain, 10 s apart by default.
- WebSockets are not intercepted by Playwright and DNS can change between check and connection:
  **production workers must run on a network whose egress firewall drops private, loopback and
  link-local ranges** (compose comment). The code checks are the first layer.

## Limits

- Throughput per worker = `REPINTEL_BROWSER_WORKER_MAX_CONTEXTS` (default 2). Scale with more workers.
- Pages that need interaction (infinite scroll, "load more") are read as first displayed.
- The detector recognises common challenge families; an unknown one may be read as an almost
  empty page (then `not_found_on_page`), never bypassed.
