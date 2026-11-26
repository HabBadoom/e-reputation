"""Encrypted built-in browser state per Scanner account (cookies + local storage, no passwords)."""

import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.db.models import ScannerBrowserProfile

log = logging.getLogger("repintel.scanner.browser")


def load(db: Session, account_id: uuid.UUID) -> dict | None:
    row = db.get(ScannerBrowserProfile, account_id)   # RLS: own row only
    if row is None:
        return None
    try:
        state = json.loads(decrypt(row.state_encrypted))
    except (ValueError, json.JSONDecodeError):
        log.warning("scanner_browser_profile_unreadable")   # rotated-out key: start signed out
        return None
    return state if isinstance(state, dict) else None


def _fit(state: dict) -> str:
    """Serialise, keeping the size bounded: local storage goes first, cookies are kept."""
    limit = get_settings().scanner_browser_max_state_bytes
    raw = json.dumps(state, separators=(",", ":"))
    if len(raw) > limit:
        raw = json.dumps({"cookies": state.get("cookies", []), "origins": []},
                         separators=(",", ":"))
    return raw if len(raw) <= limit else json.dumps({"cookies": [], "origins": []})


def save(db: Session, account_id: uuid.UUID, state: dict) -> None:
    blob = encrypt(_fit(state))
    row = db.get(ScannerBrowserProfile, account_id)
    if row is None:
        db.add(ScannerBrowserProfile(account_id=account_id, state_encrypted=blob))
    else:
        row.state_encrypted = blob
        row.updated_at = datetime.now(UTC)


def forget(db: Session, account_id: uuid.UUID) -> bool:
    return bool(db.execute(delete(ScannerBrowserProfile)
                           .where(ScannerBrowserProfile.account_id == account_id)).rowcount)
