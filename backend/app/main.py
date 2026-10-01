import logging
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.logging import setup_logging
from app.db.session import warm_pool
from app.services import storage

log = logging.getLogger("app")


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"success": False, "data": None, "error": {"code": code, "message": message, "details": details}},
    )


def _warm_analytics() -> None:
    """Build the cached analytics cube in the background so the first dashboard load is fast."""
    try:
        from app.db.session import SessionLocal
        from app.services import cube

        with SessionLocal() as db:
            cube.get_cube(db)
    except Exception:  # noqa: BLE001 - purely an optimisation
        log.exception("Could not pre-build the analytics cube")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    if s.env != "test":
        try:
            warm_pool()
        except Exception:  # noqa: BLE001
            log.exception("Could not pre-open database connections (see /health)")
    if s.env != "test" and s.storage_configured:
        try:  # create the private buckets on first start; a failure must not stop the API
            storage.ensure_buckets()
        except Exception:  # noqa: BLE001
            log.exception("Could not verify Supabase Storage buckets (see /health)")
    if s.env != "test":
        threading.Thread(target=_warm_analytics, daemon=True, name="warm-analytics").start()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="Smart Governance Intelligence & Decision Support System",
        version="0.1.0",
        description="SG-DSS API. All responses use the envelope `{success, data, error}`.",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def access_log(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - start) * 1000
        log.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code, ms)
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return _error(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error", exc_info=exc)
        return _error(500, "internal_error", "Internal server error")

    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
