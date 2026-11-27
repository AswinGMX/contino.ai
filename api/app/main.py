"""FastAPI application.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8000
Scale: run N API replicas with EMBEDDED_WORKER=false behind a load balancer, plus
M `python -m app.worker` processes. All state lives in storage (cloud mode:
Postgres + R2), so every process is stateless and horizontally scalable.
"""
from __future__ import annotations

import hmac
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .api import errors
from .api.routes import projects, system
from .config import ROOT_DIR, ConfigError, get_config, get_settings
from .jobs.queue import Worker
from .logging_setup import correlation_id, log, setup_logging
from .services.context import get_ctx

logger = logging.getLogger("app")
PUBLIC_PATHS = ("/api/healthz", "/api/readyz", "/api/auth/", "/api/config")


def _startup_checks() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    try:
        settings.validate_for_mode()
        get_config()
    except ConfigError as exc:
        logger.critical(str(exc))
        print(f"\nFATAL: {exc}\n", file=sys.stderr)
        raise SystemExit(2) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    _startup_checks()
    ctx = get_ctx()
    ctx.storage.healthcheck()
    worker = None
    # A serverless function is frozen between requests: jobs run on a separate always-on worker instead.
    if ctx.settings.embedded_worker and not ctx.settings.serverless:
        worker = Worker(ctx.storage, ctx.cfg.jobs.workers, ctx.cfg.jobs.poll_interval_seconds,
                        ctx.cfg.jobs.lease_seconds)
        worker.start()
    m = ctx.cfg.models
    log(logger, logging.INFO, "api started", storage_mode=ctx.settings.storage_mode,
        embedded_worker=ctx.settings.embedded_worker, image_model=m.image, text_model=m.text,
        review_model=m.vision)
    for name in {m.image, m.text, m.vision} - set(ctx.cfg.pricing):
        log(logger, logging.WARNING, "no pricing for model in config.yaml; using 'default' rates", model=name)
    try:
        yield
    finally:
        if worker:
            worker.stop()
        ctx.storage.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="contino.ai API", version="1.0.0", lifespan=lifespan)
    errors.install(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = correlation_id.set(rid)
        t0 = time.monotonic()
        try:
            if settings.api_token and request.url.path.startswith("/api/") and \
                    not request.url.path.startswith(PUBLIC_PATHS):
                supplied = request.cookies.get(system.COOKIE) or \
                    request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                if not supplied or not hmac.compare_digest(supplied.encode(), settings.api_token.encode()):
                    return JSONResponse(status_code=401, content={
                        "error": {"code": "unauthorized", "message": "Sign in with the access token."}})
            response = await call_next(request)
            response.headers["x-request-id"] = rid
            if "/images/" not in request.url.path:  # image hits are high-volume and cacheable
                log(logger, logging.INFO, "request", method=request.method, path=request.url.path,
                    status=response.status_code, ms=round((time.monotonic() - t0) * 1000, 1))
            return response
        finally:
            correlation_id.reset(token)

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                       allow_origin_regex=settings.cors_origin_regex or None, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition", "x-request-id"])
    app.include_router(system.router)
    app.include_router(projects.router)

    # Single-container deploys: serve the built web app if present.
    dist = ROOT_DIR / "web" / "dist"
    if dist.exists():
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "No such endpoint."}})
            target = (dist / path).resolve()
            if path and target.is_file() and str(target).startswith(str(dist.resolve())):
                return FileResponse(target)
            return FileResponse(Path(dist) / "index.html")

    return app


app = create_app()
