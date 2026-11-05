import logging
import re
import time
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, request_id_var, tenant_id_var
from app.core.rate_limit import RedisLimiter, get_limiter
from app.db.session import session_scope
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.brands.router import router as brands_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.tenancy.router import router as tenancy_router

log = logging.getLogger("repintel.http")
_RID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
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

    v1 = APIRouter(prefix="/api/v1")
    for r in (auth_router, tenancy_router, brands_router, dashboard_router, audit_router):
        v1.include_router(r)
    app.include_router(v1)
    return app


app = create_app()
