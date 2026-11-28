"""Vercel entry point: exposes the FastAPI app as a serverless function.

The job worker does NOT run here (serverless functions are frozen between
requests). Run `python -m app.worker` on an always-on host with the same env.

If the configuration is incomplete, serve a clear JSON error instead of
crashing (a crash shows Vercel's blank FUNCTION_INVOCATION_FAILED page).
"""
from app.config import ConfigError, get_config, get_settings

try:
    get_settings().validate_for_mode()
    get_config()
    from app.main import app  # noqa: F401
except ConfigError as exc:  # pragma: no cover - deployment misconfiguration
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    _message = str(exc)
    app = FastAPI(title="contino.ai API (not configured)")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    def not_configured(path: str):
        return JSONResponse(status_code=503, content={"error": {
            "code": "not_configured",
            "message": _message,
            "help": "Set the missing environment variables in Vercel (Settings > Environment Variables), "
                    "then redeploy. See DEPLOY.md.",
        }})
