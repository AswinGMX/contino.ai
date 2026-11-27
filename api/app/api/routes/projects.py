"""Project-scoped REST API. Every mutating endpoint enforces its gate in the service layer."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from ...domain.models import Plan
from ...services import export as export_svc
from ...services import finals, library, planner, storyboard, usage
from ...services.context import Ctx, get_ctx
from ...services.gates import GateError
from ...services.repo import NotFound, Repo
from ...storage.local import LocalStorage
from .. import views

router = APIRouter(prefix="/api/projects", tags=["projects"])

Quality = Literal["low", "medium", "high"]
MAX_IMAGE_UPLOAD = 20 * 1024 * 1024
MAX_PDF_UPLOAD = 30 * 1024 * 1024


def ctx_dep() -> Ctx:
    return get_ctx()


def repo_dep(project_id: str, ctx: Ctx = Depends(ctx_dep)) -> Repo:
    repo = ctx.repo(project_id)
    try:
        repo.project()
    except (NotFound, ValueError):
        raise NotFound(f"project {project_id}") from None
    return repo


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise GateError("file_too_large", f"File must be {limit // (1024 * 1024)} MB or smaller.")
    return data


class CommentIn(BaseModel):
    comment: str = Field(default="", max_length=1000)
    quality: Quality | None = None
    fresh: bool = False   # start over instead of editing the previous picture


class QualityIn(BaseModel):
    quality: Quality | None = None


class ApproveBoardIn(BaseModel):
    overrides: list[str] = Field(default_factory=list)


class RenderIn(BaseModel):
    quality: Quality | None = None
    shot_ids: list[str] | None = None


# ------------------------------------------------------------------ projects & plan

@router.get("")
def list_projects(ctx: Ctx = Depends(ctx_dep)):
    out = []
    for pid in ctx.storage.list_projects():
        try:
            out.append(views.project_overview(ctx, ctx.repo(pid)))
        except NotFound:
            continue
    return sorted(out, key=lambda o: o["project"]["created_at"], reverse=True)


@router.post("", status_code=201)
async def create_project(file: UploadFile = File(...), ctx: Ctx = Depends(ctx_dep)):
    data = await _read_limited(file, MAX_PDF_UPLOAD)
    project = await run_in_threadpool(planner.create_project, ctx, file.filename or "planner.pdf", data)
    return project.model_dump(mode="json", by_alias=True)


@router.get("/{project_id}")
def overview(repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    return views.project_overview(ctx, repo)


@router.delete("/{project_id}", status_code=204)
def delete_project(repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    """Delete a project's records and images. Money already spent stays counted in the budget."""
    ctx.storage.delete_project(repo.pid)
    return Response(status_code=204)


@router.get("/{project_id}/plan")
def get_plan(repo: Repo = Depends(repo_dep)):
    return views.plan_view(repo)


@router.put("/{project_id}/plan")
def put_plan(plan: Plan, repo: Repo = Depends(repo_dep)):
    planner.update_plan(repo, plan)
    return views.plan_view(repo)


@router.post("/{project_id}/plan/approve")
def approve_plan(repo: Repo = Depends(repo_dep)):
    planner.approve_plan(repo)
    return views.plan_view(repo)


@router.post("/{project_id}/plan/reparse", status_code=202)
def reparse(repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    planner.reparse(ctx, repo)
    return {"queued": True}


# ------------------------------------------------------------------ library

@router.get("/{project_id}/library")
def get_library(repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    return views.library_view(ctx, repo)


@router.post("/{project_id}/characters/{character_id}/reactions/generate", status_code=202)
def generate_reaction_sheet(character_id: str, body: CommentIn, repo: Repo = Depends(repo_dep),
                            ctx: Ctx = Depends(ctx_dep)):
    """One expression-sheet image for all of a character's pending reactions, cropped per emotion."""
    return {"queued": library.request_reaction_sheet(ctx, repo, character_id, body.comment, body.quality)}


@router.post("/{project_id}/characters/{character_id}/reactions/approve")
def approve_reactions(character_id: str, repo: Repo = Depends(repo_dep)):
    return {"approved": library.approve_all_reactions(repo, character_id)}


@router.post("/{project_id}/library/{item_id}/generate", status_code=202)
def generate_item(item_id: str, body: CommentIn, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    library.request_generate(ctx, repo, item_id, body.comment, body.quality, fresh=body.fresh)
    return {"queued": True}


@router.post("/{project_id}/library/{item_id}/upload")
async def upload_item(item_id: str, file: UploadFile = File(...), repo: Repo = Depends(repo_dep),
                      ctx: Ctx = Depends(ctx_dep)):
    data = await _read_limited(file, MAX_IMAGE_UPLOAD)
    await run_in_threadpool(library.upload, ctx, repo, item_id, data)
    return {"ok": True}


@router.post("/{project_id}/library/{item_id}/check", status_code=202)
def check_item(item_id: str, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    library.request_check(ctx, repo, item_id)
    return {"queued": True}


@router.post("/{project_id}/library/{item_id}/approve")
def approve_item(item_id: str, repo: Repo = Depends(repo_dep)):
    library.approve(repo, item_id)
    return {"ok": True}


@router.post("/{project_id}/library/{item_id}/reject", status_code=202)
def reject_item(item_id: str, body: CommentIn, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    library.reject(ctx, repo, item_id, body.comment, body.quality)
    return {"queued": True}


# ------------------------------------------------------------------ storyboard

@router.get("/{project_id}/storyboard")
def get_storyboard(repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    return views.storyboard_view(ctx, repo)


@router.post("/{project_id}/scenes/{scene_id}/generate", status_code=202)
def generate_scene(scene_id: str, body: QualityIn, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    return {"queued": storyboard.generate_scene(ctx, repo, scene_id, body.quality)}


@router.post("/{project_id}/panels/{shot_id}/fix", status_code=202)
def fix_panel(shot_id: str, body: CommentIn, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    storyboard.fix_panel(ctx, repo, shot_id, body.comment, body.quality)
    return {"queued": True}


@router.post("/{project_id}/panels/{shot_id}/improve", status_code=202)
def improve_panel(shot_id: str, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    """Fix the best version using that version's own AI-check problems."""
    return storyboard.improve_panel(ctx, repo, shot_id)


class RestoreIn(BaseModel):
    version: int


@router.post("/{project_id}/panels/{shot_id}/restore")
def restore_panel(shot_id: str, body: RestoreIn, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    storyboard.restore_version(ctx, repo, shot_id, body.version)
    return {"ok": True}


@router.post("/{project_id}/panels/{shot_id}/accept")
def accept_panel(shot_id: str, repo: Repo = Depends(repo_dep)):
    storyboard.accept_panel(repo, shot_id)
    return {"ok": True}


@router.post("/{project_id}/panels/{shot_id}/review", status_code=202)
def review_panel(shot_id: str, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    storyboard.request_review(ctx, repo, shot_id)
    return {"queued": True}


@router.post("/{project_id}/scenes/{scene_id}/approve")
def approve_board(scene_id: str, body: ApproveBoardIn, repo: Repo = Depends(repo_dep)):
    storyboard.approve_board(repo, scene_id, body.overrides)
    return {"ok": True}


# ------------------------------------------------------------------ finals

@router.get("/{project_id}/finals")
def get_finals(repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    return views.finals_view(ctx, repo)


@router.post("/{project_id}/scenes/{scene_id}/finals", status_code=202)
def render_finals(scene_id: str, body: RenderIn, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    return {"queued": finals.render_scene(ctx, repo, scene_id, body.quality, body.shot_ids)}


@router.post("/{project_id}/finals/{shot_id}/approve")
def approve_final(shot_id: str, repo: Repo = Depends(repo_dep)):
    finals.approve(repo, shot_id)
    return {"ok": True}


@router.post("/{project_id}/finals/{shot_id}/reject", status_code=202)
def reject_final(shot_id: str, body: CommentIn, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    finals.reject(ctx, repo, shot_id, body.comment, body.quality)
    return {"queued": True}


@router.post("/{project_id}/finals/{shot_id}/retry", status_code=202)
def retry_final(shot_id: str, body: QualityIn, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    finals.retry(ctx, repo, shot_id, body.quality)
    return {"queued": True}


# ------------------------------------------------------------------ usage, jobs, export

@router.get("/{project_id}/usage")
def get_usage(repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    return usage.summary(ctx, repo)


@router.get("/{project_id}/usage.csv")
def get_usage_csv(repo: Repo = Depends(repo_dep)):
    return Response(usage.usage_csv(repo), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="usage_log.csv"'})


@router.get("/{project_id}/jobs")
def get_jobs(repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep), active: bool = True):
    jobs = ctx.storage.list_jobs(repo.pid, active_only=active)
    return [{k: j.get(k) for k in ("id", "type", "status", "payload", "attempts", "error", "created_at")}
            for j in jobs]


@router.post("/{project_id}/export")
def export(repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    return export_svc.export(ctx, repo)


@router.get("/{project_id}/export.zip")
def export_zip(repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    name, data = export_svc.zip_export(ctx, repo)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ images

@router.get("/{project_id}/images/{rest:path}")
def get_image(rest: str, request: Request, repo: Repo = Depends(repo_dep), ctx: Ctx = Depends(ctx_dep)):
    if ".." in rest.split("/"):
        raise NotFound("image")
    key = f"projects/{repo.pid}/{rest}"
    # Keys are versioned and never overwritten, so responses are immutable and cacheable.
    cache = {"Cache-Control": "private, max-age=31536000, immutable"}
    url = ctx.storage.image_url(key, expires=300)
    if url:
        return RedirectResponse(url, status_code=302, headers={"Cache-Control": "private, max-age=240"})
    if isinstance(ctx.storage, LocalStorage):
        path = ctx.storage.local_image_path(key)
        if not path.exists():
            raise NotFound("image")
        return FileResponse(path, headers=cache)
    return Response(ctx.storage.get_image(key), media_type="image/png", headers=cache)
