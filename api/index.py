"""Vercel entry point: exposes the FastAPI app as a serverless function.

The job worker does NOT run here (serverless functions are frozen between
requests). Run `python -m app.worker` on an always-on host with the same env.

If the configuration is incomplete, serve a clear JSON error instead of
crashing (a crash shows Vercel's blank FUNCTION_INVOCATION_FAILED page).

Vercel's build scans this file for a top-level `app = ...`, so it must be
assigned at module level (not only inside try/except).
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import ConfigError, get_config, get_settings


def _not_configured_app(message: str) -> FastAPI:
    fallback = FastAPI(title="contino.ai API (not configured)")

    @fallback.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    def not_configured(path: str):
        return JSONResponse(status_code=503, content={"error": {
            "code": "not_configured",
            "message": message,
            "help": "Set the missing environment variables in Vercel (Settings > Environment Variables), "
                    "then redeploy. See DEPLOY.md.",
        }})

    return fallback


def _create_app() -> FastAPI:
    try:
        get_settings().validate_for_mode()
        get_config()
        from app.main import app as real_app
        return real_app
    except ConfigError as exc:  # pragma: no cover - deployment misconfiguration
        return _not_configured_app(str(exc))


app = _create_app()
