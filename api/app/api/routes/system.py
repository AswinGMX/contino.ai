from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from ...config import get_settings
from ...services import auth
from ...services.budget import BudgetGuard
from ...services.context import Ctx, get_ctx

router = APIRouter(prefix="/api", tags=["system"])


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
        "access_code_required": bool(ctx.settings.api_token),
        "models": ctx.cfg.models.model_dump(),
        "image": {"draft_quality": ctx.cfg.image.draft_quality, "final_quality": ctx.cfg.image.final_quality},
        "review": ctx.cfg.review.model_dump(),
    }


@router.get("/budget")
def budget(ctx: Ctx = Depends(get_ctx)):
    return BudgetGuard(ctx.storage, ctx.cfg).status()


class LoginIn(BaseModel):
    email: str
    name: str = ""
    access_code: str = ""


def _cookie_args() -> dict:
    cross = get_settings().cookie_cross_site
    # Frontend and API on different domains need SameSite=None; Secure, or the browser drops the cookie.
    return {"httponly": True, "samesite": "none" if cross else "lax", "secure": cross}


@router.post("/auth/login")
def login(body: LoginIn, response: Response, ctx: Ctx = Depends(get_ctx)):
    settings = get_settings()
    try:
        email = auth.normalize_email(body.email)
    except ValueError as exc:
        response.status_code = 400
        return {"error": {"code": "bad_email", "message": str(exc)}}
    if settings.api_token and not hmac.compare_digest(body.access_code.encode(), settings.api_token.encode()):
        response.status_code = 401
        return {"error": {"code": "bad_access_code", "message": "That team access code is not right."}}
    user = ctx.storage.upsert_user(email, body.name.strip()[:80])
    claimed = ctx.storage.claim_unowned_projects(email)   # projects made before sign-in existed
    response.set_cookie(auth.COOKIE, auth.issue(settings, email), max_age=auth.SESSION_DAYS * 86400, **_cookie_args())
    return {"user": {"email": user["email"], "name": user.get("name", "")}, "claimed_projects": claimed}


@router.get("/auth/me")
def me(request: Request, response: Response, ctx: Ctx = Depends(get_ctx)):
    email = auth.verify(get_settings(), request.cookies.get(auth.COOKIE))
    if not email:
        response.status_code = 401
        return {"error": {"code": "signed_out", "message": "Sign in with your email."}}
    user = ctx.storage.get_user(email) or {"email": email, "name": ""}
    return {"user": {"email": user["email"], "name": user.get("name", "")}}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, **{k: v for k, v in _cookie_args().items() if k != "httponly"})
    return {"ok": True}
