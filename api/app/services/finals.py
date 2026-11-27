"""Step 6: one high-quality 16:9 final per approved shot, from the approved panel plus library references."""
from __future__ import annotations

from ..domain.models import Final, ImageVersion, Status
from ..jobs.queue import enqueue
from . import imaging, prompts
from .context import Ctx
from .gates import GateError, awaiting_approval, board_approved, not_busy, one_line_comment, plan_approved
from .llm import CallContext
from .references import shot_data, shot_refs
from .repo import Repo


def render_scene(ctx: Ctx, repo: Repo, scene_id: str, quality: str | None = None,
                 shot_ids: list[str] | None = None) -> list[str]:
    plan = plan_approved(repo)
    board_approved(repo, scene_id)
    scene = plan.scene(scene_id)
    quality = quality or ctx.cfg.image.final_quality
    queued = []
    for shot in scene.shots:
        if shot_ids and shot.id not in shot_ids:
            continue
        panel = repo.panel(shot.id)
        if panel is None or panel.current is None or panel.status != Status.approved:
            continue
        f = repo.final(shot.id)
        if (f and f.current and f.panel_version == panel.current.n
                and f.status in (Status.generating, Status.needs_approval, Status.approved)):
            continue
        _queue(ctx, repo, shot.id, scene_id, "", quality)
        queued.append(shot.id)
    return queued


def reject(ctx: Ctx, repo: Repo, shot_id: str, comment: str, quality: str | None = None) -> Final:
    """Reject with one short comment; the final is regenerated with it applied."""
    comment = one_line_comment(comment, required=True)
    plan = plan_approved(repo)
    scene, _ = plan.shot(shot_id)
    board_approved(repo, scene.id)
    f = repo.final(shot_id)
    if f is None or f.current is None:
        raise GateError("no_final", "No final to reject.")
    if f.status not in (Status.needs_approval, Status.approved, Status.stale, Status.failed):
        raise GateError("not_rejectable", f"Final {shot_id} is '{f.status}'.")
    return _queue(ctx, repo, shot_id, scene.id, comment, quality or ctx.cfg.image.final_quality)


def retry(ctx: Ctx, repo: Repo, shot_id: str, quality: str | None = None) -> Final:
    plan = plan_approved(repo)
    scene, _ = plan.shot(shot_id)
    board_approved(repo, scene.id)
    return _queue(ctx, repo, shot_id, scene.id, "", quality or ctx.cfg.image.final_quality)


def approve(repo: Repo, shot_id: str) -> Final:
    f = repo.final(shot_id)
    if f is None or f.current is None:
        raise GateError("no_final", "No final to approve.")
    awaiting_approval(f.status, f"Final {shot_id}")

    def fn(cur: Final | None) -> Final | None:
        assert cur is not None and cur.current is not None
        awaiting_approval(cur.status, f"Final {shot_id}")
        return cur.model_copy(update={"status": Status.approved, "approved_version": cur.current.n, "comment": ""})

    out = repo.mutate_final(shot_id, fn)
    assert out is not None
    return out


def _queue(ctx: Ctx, repo: Repo, shot_id: str, scene_id: str, comment: str, quality: str) -> Final:
    def fn(cur: Final | None) -> Final:
        cur = cur or Final(id=shot_id, scene_id=scene_id)
        not_busy(cur.status, f"Final {shot_id}")
        return cur.model_copy(update={"status": Status.generating, "comment": comment, "error": "",
                                      "stale_reason": ""})
    out = repo.mutate_final(shot_id, fn)
    enqueue(ctx.storage, repo.pid, "final_generate", {"shot_id": shot_id, "comment": comment, "quality": quality},
            dedupe_key=f"final:{repo.pid}:{shot_id}")
    assert out is not None
    return out


def run_final(ctx: Ctx, repo: Repo, shot_id: str, comment: str, quality: str, job_id: str = "") -> None:
    plan = repo.must_plan().plan
    scene, shot = plan.shot(shot_id)
    panel = repo.panel(shot_id)
    if panel is None or panel.current is None:
        raise RuntimeError("no approved panel")
    final = repo.final(shot_id) or Final(id=shot_id, scene_id=scene.id)
    refs = shot_refs(ctx, repo, plan, shot, max_side=ctx.cfg.image.ref_max_side_final)
    panel_png = imaging.downscale(ctx.storage.get_image(panel.current.image_key), 1024, fmt="PNG")
    images = [("approved_panel.png", panel_png)] + refs.images
    labels = ["image 1: the approved storyboard panel (composition to keep)"] + [
        f"image {i + 2}:{lbl.split(':', 1)[1]}" for i, lbl in enumerate(refs.labels)]
    if comment and final.current is not None:
        images.append(("previous_final.png",
                       imaging.downscale(ctx.storage.get_image(final.current.image_key), 1024, fmt="PNG")))
        labels.append(f"image {len(images)}: previous final to revise")
    data = shot_data(plan, shot)
    data["reference_images"] = labels

    raw = ctx.llm.image(CallContext(repo.pid, shot_id, "final", job_id),
                        prompt=prompts.compose("final", data, comment), refs=images,
                        size=ctx.cfg.image.request_size, quality=quality)
    img = imaging.crop_to_aspect(raw, tuple(ctx.cfg.image.aspect))
    n = (final.current.n + 1) if final.current else 1
    key = repo.image_key("keyframes", f"{shot_id}_v{n}.png")
    ctx.storage.put_image(key, img)
    used = dict(refs.used)

    def fn(cur: Final | None) -> Final:
        cur = cur or Final(id=shot_id, scene_id=scene.id)
        v = ImageVersion(n=n, image_key=key, comment=comment, quality=quality, refs_used=used)
        return cur.model_copy(update={"versions": [*cur.versions, v], "status": Status.needs_approval,
                                      "comment": "", "error": "", "panel_version": panel.current.n})

    repo.mutate_final(shot_id, fn)


def final_failed(repo: Repo, shot_id: str, error: str) -> None:
    repo.mutate_final(shot_id, lambda cur: cur.model_copy(update={"status": Status.failed, "error": error[:500]})
                      if cur else None)
