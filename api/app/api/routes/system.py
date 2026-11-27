from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from ...config import get_settings
from ...services.budget import BudgetGuard
from ...services.context import Ctx, get_ctx

router = APIRouter(prefix="/api", tags=["system"])
COOKIE = "s2s_token"


@router.get("/healthz")
def healthz():
    return {"ok": True}


@router.get("/readyz")
def readyz(ctx: Ctx = Depends(get_ctx)):
    ctx.storage.healthcheck()
    return {"ok": True, "storage_mode": ctx.settings.storage_mode}


@router.get("/config")
def public_config(ctx: Ctx = Depends(get_ctx)):
    return {
        "storage_mode": ctx.settings.storage_mode,
        "auth_required": bool(ctx.settings.api_token),
        "models": ctx.cfg.models.model_dump(),
        "image": {"draft_quality": ctx.cfg.image.draft_quality, "final_quality": ctx.cfg.image.final_quality},
        "review": ctx.cfg.review.model_dump(),
    }


@router.get("/budget")
def budget(ctx: Ctx = Depends(get_ctx)):
    return BudgetGuard(ctx.storage, ctx.cfg).status()


class LoginIn(BaseModel):
    token: str


@router.post("/auth/login")
def login(body: LoginIn, response: Response):
    expected = get_settings().api_token
    if expected and not hmac.compare_digest(body.token.encode(), expected.encode()):
        response.status_code = 401
        return {"error": {"code": "unauthorized", "message": "Wrong access token."}}
    cross = get_settings().cookie_cross_site
    # Frontend and API on different domains need SameSite=None; Secure, or the browser drops the cookie.
    response.set_cookie(COOKIE, body.token, httponly=True, max_age=7 * 86400,
                        samesite="none" if cross else "lax", secure=cross)
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response):
    cross = get_settings().cookie_cross_site
    response.delete_cookie(COOKIE, samesite="none" if cross else "lax", secure=cross)
    return {"ok": True}
