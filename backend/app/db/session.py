"""Engine, sessions and per-transaction tenant context.

The API connects as a role without BYPASSRLS. Row visibility is decided by PostgreSQL policies
that read `app.tenant_id` / `app.user_id`. Those settings are applied with `set_config(..., true)`
(transaction-local) at the start of *every* transaction on the session, from values stored on
`session.info`. A pooled connection therefore never carries a previous request's context, and a
commit mid-request doesn't drop it.
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


@event.listens_for(Session, "after_begin")
def _apply_context(session: Session, transaction, connection) -> None:  # noqa: ANN001
    tid = session.info.get("tenant_id")
    uid = session.info.get("user_id")
    connection.execute(
        text("SELECT set_config('app.tenant_id', :t, true), set_config('app.user_id', :u, true)"),
        {"t": str(tid) if tid else "", "u": str(uid) if uid else ""},
    )


def set_context(
    session: Session, *, tenant_id: uuid.UUID | None, user_id: uuid.UUID | None
) -> None:
    """Set the context for the current and all future transactions on this session."""
    session.info["tenant_id"] = tenant_id
    session.info["user_id"] = user_id
    if session.in_transaction():
        session.execute(
            text(
                "SELECT set_config('app.tenant_id', :t, true), "
                "set_config('app.user_id', :u, true)"
            ),
            {"t": str(tenant_id) if tenant_id else "", "u": str(user_id) if user_id else ""},
        )


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
