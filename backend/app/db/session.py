"""Engine, sessions and per-transaction tenant context.

The API connects as a role without BYPASSRLS. Row visibility is decided by PostgreSQL policies
that read `app.tenant_id` / `app.user_id` (AnyPulse) or `app.scanner_account_id` (Scanner).
Those settings are applied with `set_config(..., true)` (transaction-local) at the start of
*every* transaction on the session, from values stored on `session.info`. A pooled connection
therefore never carries a previous request's context, and a commit mid-request doesn't drop it.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10
        )
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


_CONTEXT_SQL = text(
    "SELECT set_config('app.tenant_id', :t, true), set_config('app.user_id', :u, true), "
    "set_config('app.scanner_account_id', :s, true)"
)


def _context_params(session: Session) -> dict[str, str]:
    tid = session.info.get("tenant_id")
    uid = session.info.get("user_id")
    sid = session.info.get("scanner_account_id")
    return {"t": str(tid) if tid else "", "u": str(uid) if uid else "",
            "s": str(sid) if sid else ""}


@event.listens_for(Session, "after_begin")
def _apply_context(session: Session, transaction, connection) -> None:  # noqa: ANN001
    connection.execute(_CONTEXT_SQL, _context_params(session))


def _switch(session: Session, **info: uuid.UUID | None) -> None:
    """Replace the whole context for the current and all future transactions on this session.

    Pending ORM changes are flushed first, under the context they were created in. Without
    this, rows added before a switch would be written later under the *new* context and be
    rejected by RLS (or, worse, checked against the wrong tenant).
    """
    if session.in_transaction() and (session.new or session.dirty or session.deleted):
        session.flush()
    session.info["tenant_id"] = info.get("tenant_id")
    session.info["user_id"] = info.get("user_id")
    session.info["scanner_account_id"] = info.get("scanner_account_id")
    if session.in_transaction():
        session.execute(_CONTEXT_SQL, _context_params(session))


def set_context(
    session: Session, *, tenant_id: uuid.UUID | None, user_id: uuid.UUID | None
) -> None:
    """AnyPulse context (tenant + user). Clears any Scanner context."""
    _switch(session, tenant_id=tenant_id, user_id=user_id)


def set_scanner_context(session: Session, *, account_id: uuid.UUID | None) -> None:
    """Aynpulse Scanner context (one Scanner account). Clears any AnyPulse context, so a
    Scanner request can never read AnyPulse tenant data and vice versa."""
    _switch(session, scanner_account_id=account_id)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
        s.commit()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    with session_scope() as s:
        yield s
