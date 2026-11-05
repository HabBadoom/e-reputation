import os

from alembic import context
from sqlalchemy import create_engine, pool

from app.db.models import Base

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("REPINTEL_MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("REPINTEL_MIGRATION_DATABASE_URL is not set")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
