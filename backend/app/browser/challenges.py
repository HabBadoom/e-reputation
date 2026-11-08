"""Autonomous response to a detected challenge — never a bypass.

    DETECTED → PAUSED (automation stops at once: no click, no retry, no other identity)
             → FALLBACK          the mention keeps the search snippet / feed text, the domain
                                 cools down (1 h, 4 h, 16 h, 72 h) for every tenant
             → SOURCE_RESTRICTED after repeated challenges the source is marked RESTRICTED with
                                 the reason, so coverage limits are visible in the product
The mission, if any, ends CANCELLED (reason `challenge:<kind>`). Nobody has to act.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.browser.detect import DETECTOR_VERSION, Challenge
from app.browser.missions import event, transition
from app.core.config import get_settings
from app.db.models import BrowserMission, CaptchaEvent, Mention, Source

KIND_LABEL = {
    "recaptcha": "reCAPTCHA", "hcaptcha": "hCaptcha", "turnstile": "Cloudflare Turnstile",
    "cloudflare_challenge": "Cloudflare challenge", "datadome": "DataDome",
    "perimeterx": "HUMAN/PerimeterX", "akamai": "Akamai block", "aws_waf": "AWS WAF",
    "generic_captcha": "CAPTCHA", "login_wall": "login required",
    "rate_limited": "rate limited (HTTP 429)", "access_denied": "access denied (HTTP 403)",
}


def _stamp(history: list[dict[str, Any]], state: str, **extra: Any) -> None:
    history.append({"state": state, "at": datetime.now(UTC).isoformat(), **extra})


def handle_challenge(db: Session, tenant_id: uuid.UUID, *, url: str, domain: str, via: str,
                     challenge: Challenge, mission: BrowserMission | None = None,
                     source: Source | None = None, mention: Mention | None = None,
                     worker_id: uuid.UUID | None = None) -> CaptchaEvent:
    now = datetime.now(UTC)
    history: list[dict[str, Any]] = []
    _stamp(history, "DETECTED", kind=challenge.kind, confidence=challenge.confidence)
    _stamp(history, "PAUSED", detail="automation stopped; no interaction with the challenge")

    if mission is not None and mission.status == "RUNNING":
        transition(mission, "PAUSED")
        event(db, mission, "challenge", {"kind": challenge.kind, "signals": challenge.signals,
                                         "confidence": challenge.confidence}, worker_id)
        db.flush()

    cooldown = db.execute(text("SELECT browser_note_challenge(:d, :k)"),
                          {"d": domain, "k": challenge.kind}).scalar_one()
    response: dict[str, Any] = {"cooldown_until": cooldown.isoformat(),
                                "detector_version": DETECTOR_VERSION}

    if source is None and mention is not None and mention.source_id:
        source = db.get(Source, mention.source_id)
    state = "FALLBACK"
    if source is not None:
        source.challenge_count += 1
        source.last_challenge_kind, source.last_challenge_at = challenge.kind, now
        source.consecutive_errors += 1
        source.last_error = f"challenge:{challenge.kind}"
        if source.challenge_count >= get_settings().browser_restricted_after_challenges:
            source.coverage_status = "RESTRICTED"
            source.restrictions = (f"Anti-bot protection ({KIND_LABEL[challenge.kind]}): "
                                   "automated access refused by the site, not bypassed.")
            state = "SOURCE_RESTRICTED"
        has_feed = db.scalar(select(Source.id).where(
            Source.tenant_id == tenant_id, Source.kind == "rss", Source.domain == domain,
            Source.enabled).limit(1)) is not None
        response["fallback"] = "rss_feed" if has_feed else "search_results"
    if mention is not None:
        mention.verification_status = "blocked_by_challenge"
        response["fallback"] = "search_snippet" if mention.collection_method != "rss" \
            else "feed_text"
    response.setdefault("fallback", "none_needed")
    _stamp(history, state, **response)

    ev = CaptchaEvent(id=uuid.uuid4(), tenant_id=tenant_id, domain=domain, url=url[:2000],
                      via=via, kind=challenge.kind, confidence=challenge.confidence,
                      signals=challenge.signals, state=state, history=history,
                      response=response, resolved_at=now,
                      mission_id=mission.id if mission is not None else None,
                      source_id=source.id if source is not None else None,
                      mention_id=mention.id if mention is not None else None)
    db.add(ev)
    if mission is not None and mission.status == "PAUSED":
        transition(mission, "CANCELLED", error=f"challenge:{challenge.kind}")
        mission.result = {**(mission.result or {}), "challenge": challenge.kind,
                          "fallback": response.get("fallback")}
    db.flush()
    return ev


def note_success(db: Session, domain: str, source: Source | None) -> None:
    db.execute(text("SELECT browser_note_success(:d)"), {"d": domain})
    if source is not None:
        source.challenge_count = 0
