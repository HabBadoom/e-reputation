# CAPTCHA & human intervention — design (Milestone 6, not implemented)

**The platform never solves or bypasses CAPTCHAs or anti-bot protections automatically**, including via third-party solving services. (The master prompt §9 sentence saying the AI "must automatically solve or bypass" contradicts the rest of §9 and rule 22.12; it is treated as "must not".)

## Flow

1. Detector flags a challenge (known challenge DOM/iframe signatures, HTTP 403/429 patterns) → mission `WAITING_FOR_HUMAN`, automation paused, state snapshotted.
2. Intervention created (`DETECTED → HUMAN_REQUIRED`), operators with the `interventions:handle` permission in **that tenant** notified.
3. Operator claims it (`HUMAN_IN_PROGRESS`), opens a short-lived, audited remote view of the isolated context.
4. After the human completes it, the system checks the page is usable (`VERIFIED`) and re-runs the policy check; only then `RESUMED`.
5. Unclaimed/unfinished before expiry → `EXPIRED`; operator can `CANCEL`.

If a site repeatedly challenges, the source's coverage drops to `RESTRICTED` and the team is told, rather than escalating automation. Persistent challenges are often a signal that automated access isn't welcome.
