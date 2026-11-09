import logging
import re
import time
import uuid

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, request_id_var, tenant_id_var
from app.core.rate_limit import RedisLimiter, get_limiter
from app.core.security import constant_time_eq
from app.db.session import session_scope
from app.modules.admin.router import router as admin_router
from app.modules.ai.router import router as ai_router
from app.modules.alerts.router import router as alerts_router
from app.modules.analytics.router import router as analytics_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.billing.router import router as billing_router
from app.modules.brands.router import router as brands_router
from app.modules.browser.router import router as browser_router
from app.modules.catalog.router import router as catalog_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.mentions.router import router as mentions_router
from app.modules.monitoring.router import router as monitoring_router
from app.modules.nlp.router import router as nlp_router
from app.modules.reports.router import router as reports_router
from app.modules.sources.router import router as sources_router
from app.modules.tenancy.router import router as tenancy_router

log = logging.getLogger("repintel.http")
_RID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def render_metrics(m: dict) -> str:
    out = []

    def g(name: str, value: object, labels: str = "") -> None:
        out.append(f"repintel_{name}{{{labels}}} {float(value or 0)}" if labels
                   else f"repintel_{name} {float(value or 0)}")
    for key in ("tenants", "jobs_oldest_pending_seconds", "browser_workers_online",
                "browser_capacity", "ai_tasks_running", "approvals_pending",
                "invoices_outstanding", "invoices_overdue"):
        g(key, m.get(key))
    for group in ("subscriptions", "jobs", "browser_missions"):
        for status, n in (m.get(group) or {}).items():
            g(group, n, f'status="{status}"')
    return "\n".join(out) + "\n"


def _init_error_tracking(dsn: str, env: str) -> None:
    """Optional Sentry error tracking (REPINTEL_SENTRY_DSN). No request bodies or personal
    data are sent."""
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - dependency declared
        log.warning("sentry_sdk_missing")
        return
    sentry_sdk.init(dsn=dsn, environment=env, send_default_pii=False, traces_sample_rate=0.0,
                    max_request_body_size="never")


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
    _init_error_tracking(s.sentry_dsn.get_secret_value(), s.env)
    app = FastAPI(
        title=f"{s.app_name} API",
        version="0.1.0",
        docs_url=None if s.is_production else "/api/docs",
        openapi_url=None if s.is_production else "/api/openapi.json",
        redoc_url=None,
    )
    install_error_handlers(app)

    if s.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["content-type", "x-csrf-token", "x-request-id", "authorization",
                           "x-auth-mode"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # noqa: ANN001, ANN202
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _RID.match(incoming) else uuid.uuid4().hex
        request.state.request_id = rid
        request_id_var.set(rid)
        tenant_id_var.set(None)
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
        response.headers["cache-control"] = response.headers.get("cache-control", "no-store")
        if s.is_production:
            response.headers["strict-transport-security"] = "max-age=63072000; includeSubDomains"
        log.info("request", extra={"method": request.method, "path": request.url.path,
                                   "status": response.status_code,
                                   "ms": round((time.perf_counter() - start) * 1000, 1)})
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        checks: dict[str, bool] = {}
        try:
            with session_scope() as db:
                db.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:  # noqa: BLE001 — readiness must report, not raise
            log.exception("readiness_db_failed")
            checks["database"] = False
        limiter = get_limiter()
        checks["redis"] = limiter.ping() if isinstance(limiter, RedisLimiter) else True
        ok = all(checks.values())
        return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> Response:
        """Prometheus text format. Enabled only when REPINTEL_METRICS_TOKEN is set; scraped
        with `Authorization: Bearer <token>`. Aggregates only, no tenant data."""
        token = s.metrics_token.get_secret_value()
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not token or not constant_time_eq(given, token):
            return Response(status_code=404)
        with session_scope() as db:
            m = db.execute(text("SELECT platform_metrics()")).scalar_one()
        return Response(render_metrics(m), media_type="text/plain; version=0.0.4")

    v1 = APIRouter(prefix="/api/v1")
    for r in (auth_router, tenancy_router, brands_router, catalog_router, monitoring_router,
              mentions_router, sources_router, nlp_router, dashboard_router, audit_router,
              analytics_router, alerts_router, reports_router, browser_router, ai_router,
              billing_router, admin_router):
        v1.include_router(r)
    app.include_router(v1)
    return app


app = create_app()
