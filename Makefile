.PHONY: up down migrate test test-sql test-api lint fmt

up:            ## Start the full local stack
	docker compose up --build

down:
	docker compose down

migrate:
	cd backend && uv run alembic upgrade head

test: test-sql test-api   ## All backend tests (needs a migrated DB, see docs/TESTING.md)

test-sql:
	cd backend && psql "$${APP_DB_URL:-postgresql://repintel_app:repintel_app@localhost:5432/repintel}" \
	  -X -v ON_ERROR_STOP=1 -f tests/sql/test_rls_isolation.sql | grep -E "PASS|FAIL|ERROR"
	cd backend && psql "$${APP_DB_URL:-postgresql://repintel_app:repintel_app@localhost:5432/repintel}" \
	  -X -v ON_ERROR_STOP=1 -f tests/sql/test_rls_user_tables.sql | grep -E "PASS|FAIL|ERROR"

test-api:
	cd backend && uv run pytest -v

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run typecheck && npm run lint
