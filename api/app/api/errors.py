from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..services.budget import BudgetExceeded
from ..services.gates import GateError
from ..services.repo import NotFound
from ..storage.base import ConflictError

logger = logging.getLogger(__name__)


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def install(app: FastAPI) -> None:
    @app.exception_handler(GateError)
    async def gate(_: Request, exc: GateError):
        return _err(409, exc.code, exc.message)

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound):
        return _err(404, "not_found", str(exc))

    @app.exception_handler(KeyError)
    async def key_error(_: Request, exc: KeyError):
        return _err(404, "not_found", f"{exc.args[0] if exc.args else 'item'} not found")

    @app.exception_handler(BudgetExceeded)
    async def budget(_: Request, exc: BudgetExceeded):
        return _err(402, "budget_exceeded", str(exc))

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError):
        return _err(409, "conflict", str(exc))

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):
        return _err(400, "bad_request", str(exc))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        logger.exception("unhandled error")
        return _err(500, "internal_error", "Something went wrong. Check the server logs.")
