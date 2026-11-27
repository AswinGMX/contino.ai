"""Job type -> service function. Failure hooks hand the item to a person (status=failed)."""
from __future__ import annotations

from ..domain.models import Job
from ..services import finals, library, planner, storyboard
from ..services.context import get_ctx
from .queue import handler


def _repo(job: Job):
    ctx = get_ctx()
    return ctx, ctx.repo(job.project_id)


@handler("parse_plan", on_fail=lambda j, e: planner.parse_failed(_repo(j)[1], e))
def parse_plan(job: Job) -> None:
    ctx, repo = _repo(job)
    planner.run_parse(ctx, repo, job.payload["source_key"], job.id)


@handler("library_generate", on_fail=lambda j, e: library.generate_failed(_repo(j)[1], j.payload["item_id"], e))
def library_generate(job: Job) -> None:
    ctx, repo = _repo(job)
    p = job.payload
    library.run_generate(ctx, repo, p["item_id"], p.get("comment", ""), p["quality"], job.id, p.get("fresh", False))


@handler("panel_generate", on_fail=lambda j, e: storyboard.panel_failed(_repo(j)[1], j.payload["shot_id"], e))
def panel_generate(job: Job) -> None:
    ctx, repo = _repo(job)
    p = job.payload
    storyboard.run_panel(ctx, repo, p["shot_id"], p.get("comment", ""), p["quality"], job.id, p.get("base_version"))


@handler("panel_review", on_fail=lambda j, e: storyboard.review_failed(_repo(j)[1], j.payload["shot_id"], e))
def panel_review(job: Job) -> None:
    ctx, repo = _repo(job)
    storyboard.run_review(ctx, repo, job.payload["shot_id"], job.payload.get("recheck_neighbours", False), job.id)


@handler("final_generate", on_fail=lambda j, e: finals.final_failed(_repo(j)[1], j.payload["shot_id"], e))
def final_generate(job: Job) -> None:
    ctx, repo = _repo(job)
    p = job.payload
    finals.run_final(ctx, repo, p["shot_id"], p.get("comment", ""), p["quality"], job.id)


@handler("reaction_sheet",
         on_fail=lambda j, e: library.reaction_sheet_failed(_repo(j)[1], j.payload["item_ids"], e))
def reaction_sheet(job: Job) -> None:
    ctx, repo = _repo(job)
    p = job.payload
    library.run_reaction_sheet(ctx, repo, p["character_id"], p["item_ids"], p.get("comment", ""),
                               p["quality"], job.id)


@handler("library_check",
         on_fail=lambda j, e: library.check_failed(_repo(j)[1], j.payload["item_id"], j.payload["version"], e))
def library_check(job: Job) -> None:
    ctx, repo = _repo(job)
    library.run_check(ctx, repo, job.payload["item_id"], job.payload["version"], job.id)
