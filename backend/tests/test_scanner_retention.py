"""Aynpulse Scanner, phase 6: retention (D9) — unsent collections deleted after 30 days without
activity (warning from 7 days before), images of sent collections deleted 30 days after sending,
objects removed from storage, audited, other agents untouched."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.db.session import session_scope, set_scanner_context
from app.scanner import retention
from app.scanner.storage import StorageError, get_storage
from tests.test_scanner import scanner_signup
from tests.test_scanner_uploads import current, png, upload

S = "/api/v1/scanner"


def _age(owner_engine, sid: str, days: int, status: str | None = None) -> None:
    with owner_engine.begin() as c:
        c.execute(text("UPDATE scanner_collection_sessions SET updated_at = now() - "
                       "make_interval(days => :d), status = coalesce(:st, status) WHERE id = :id"),
                  {"d": days, "id": sid, "st": status})


def _keys(owner_engine, sid: str) -> list[str]:
    with owner_engine.begin() as c:
        return [r[0] for r in c.execute(text(
            "SELECT storage_key FROM scanner_uploaded_files WHERE session_id = :id"), {"id": sid})]


def _gone(key: str) -> bool:
    try:
        get_storage().get(key)
    except (StorageError, FileNotFoundError, OSError):
        return True
    return False


def test_abandoned_collection_is_deleted_after_30_days(client, owner_engine):
    old, recent = scanner_signup(client), scanner_signup(client)
    sid_old, sid_recent = current(old), current(recent)
    upload(old, sid_old, png("a.png"))
    upload(recent, sid_recent, png("b.png"))
    keys_old, keys_recent = _keys(owner_engine, sid_old), _keys(owner_engine, sid_recent)
    _age(owner_engine, sid_old, 31)
    _age(owner_engine, sid_recent, 29)

    stats = retention.run()
    assert stats["collections"] == 1 and stats["accounts"] == 1

    assert all(_gone(k) for k in keys_old)
    assert not any(_gone(k) for k in keys_recent)
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM scanner_collection_sessions WHERE id = :i"),
                         {"i": sid_old}).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM scanner_collection_items WHERE session_id"
                              " = :i"), {"i": sid_old}).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM scanner_audit_logs WHERE action = "
                              "'collection.expired'")).scalar() == 1
    # The agent simply gets a new collection.
    assert current(old) != sid_old
    assert current(recent) == sid_recent


def test_cancelled_collections_follow_the_same_rule(client, owner_engine):
    sc = scanner_signup(client)
    sid = current(sc)
    upload(sc, sid, png("a.png"))
    _age(owner_engine, sid, 31, status="cancelled")
    assert retention.run()["collections"] == 1


def test_agent_is_warned_before_deletion(client, owner_engine):
    sc = scanner_signup(client)
    sid = current(sc)
    s = sc.get(f"{S}/sessions/current").json()
    assert s["expires_soon"] is False and s["expires_at"] is not None
    _age(owner_engine, sid, 25)
    s = sc.get(f"{S}/sessions/current").json()
    assert s["expires_soon"] is True
    expires = datetime.fromisoformat(s["expires_at"].replace("Z", "+00:00"))
    assert timedelta(days=4) < expires - datetime.now(UTC) < timedelta(days=6)
    upload(sc, sid, png("a.png"))                       # activity pushes the date back
    assert sc.get(f"{S}/sessions/current").json()["expires_soon"] is False


def test_images_of_a_sent_collection_go_30_days_after_sending(client, owner_engine):
    sc = scanner_signup(client)
    sid = current(sc)
    upload(sc, sid, png("a.png"), png("b.png", text="deux"))
    keys = _keys(owner_engine, sid)
    _age(owner_engine, sid, 60, status="transmitted")   # sent collections never "expire"

    account_id = sc.get(f"{S}/auth/me").json()["account_id"]
    with session_scope() as db:
        set_scanner_context(db, account_id=account_id)
        sent_at = datetime.now(UTC) - timedelta(days=29)
        assert retention.set_after_sending(db, sid, sent_at) == 2
    assert retention.run() == {"accounts": 0, "files": 0, "collections": 0}

    with owner_engine.begin() as c:
        c.execute(text("UPDATE scanner_uploaded_files SET retention_until = now() - "
                       "interval '1 minute' WHERE session_id = :i"), {"i": sid})
    stats = retention.run()
    assert stats["files"] == 2 and stats["collections"] == 0
    assert all(_gone(k) for k in keys)
    items = sc.get(f"{S}/sessions/{sid}/items").json()
    assert len(items) == 2 and all(i["file"] is None for i in items)   # items and text stay


def test_storage_failure_keeps_the_rows(client, owner_engine, monkeypatch):
    sc = scanner_signup(client)
    sid = current(sc)
    upload(sc, sid, png("a.png"))
    _age(owner_engine, sid, 31)

    def broken(_key):
        raise StorageError("down")
    monkeypatch.setattr(type(get_storage()), "delete", lambda self, k: broken(k))
    assert retention.run()["collections"] == 0
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM scanner_collection_sessions WHERE id = :i"),
                         {"i": sid}).scalar() == 1


def test_only_the_api_role_may_list_accounts_for_retention(owner_engine):
    with owner_engine.begin() as c:
        acl = c.execute(text("SELECT proacl::text FROM pg_proc WHERE proname = "
                             "'scanner_retention_accounts'")).scalar()
        assert c.execute(text("SELECT has_function_privilege('repintel_app', "
                              "'scanner_retention_accounts(integer)', 'EXECUTE')")).scalar()
    entries = acl.strip("{}").split(",")
    assert not any(e.startswith("=") for e in entries), acl          # nothing for PUBLIC
