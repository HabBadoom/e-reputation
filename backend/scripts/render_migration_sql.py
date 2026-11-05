"""Render a migration's upgrade() or downgrade() to plain SQL without Alembic installed.

Used by tests/sql to exercise the real schema + RLS policies with psql, and handy for DBAs
reviewing a migration. Only supports migrations that use op.execute() (ours do).

Usage: python scripts/render_migration_sql.py migrations/versions/0001_foundation.py [downgrade]
"""

import importlib.util
import sys
import types

statements: list[str] = []
fake_alembic = types.ModuleType("alembic")
fake_alembic.op = types.SimpleNamespace(execute=lambda sql: statements.append(str(sql)))  # type: ignore[attr-defined]
sys.modules["alembic"] = fake_alembic

path = sys.argv[1]
direction = sys.argv[2] if len(sys.argv) > 2 else "upgrade"
spec = importlib.util.spec_from_file_location("migration", path)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
getattr(mod, direction)()

print("BEGIN;")
for s in statements:
    s = s.strip()
    print(s if s.endswith(";") else s + ";")
print("COMMIT;")
