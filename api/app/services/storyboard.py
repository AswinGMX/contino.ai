"""Steps 3-5: draft panels, compose boards in code, AI review, human approval, single-panel fixes."""
from __future__ import annotations

import time
import uuid

from pydantic import BaseModel, Field, field_validator

from ..config import AppConfig
from ..domain.models import Board, Final, Issue, Panel, PanelVersion, Plan, Review, Scene, Status
from ..jobs.queue import enqueue
from . import imaging, prompts
from .context import Ctx
from .gates import (GateError, first_scene_first, library_approved, not_busy, one_line_comment, plan_approved)
from .llm import CallContext
from .references import shot_data, shot_refs
from .repo import Repo

CRITERIA = ("character", "quality", "background", "emotion", "continuity", "framing")
_DONE = (Status.needs_approval, Status.approved, Status.in_review, Status.generating)


# ------------------------------------------------------------------ commands

def generate_scene(ctx: Ctx, repo: Repo, scene_id: str, quality: str | None = None) -> list[str]:
    """Queue a draft panel for every shot in the scene that has none (or is stale / failed)."""
    plan = plan_approved(repo)
    library_approved(repo, plan)
    scene = _scene(plan, scene_id)
    first_scene_first(repo, plan, scene_id)
    quality = quality or ctx.cfg.image.draft_quality
    queued: list[str] = []
    for shot in scene.shots:
        p = repo.panel(shot.id)
        if p and p.current and p.status in _DONE:
            continue
        repo.mutate_panel(shot.id, lambda cur, sid=shot.id: (cur or Panel(id=sid, scene_id=scene_id)).model_copy(
            update={"status": Status.generating, "error": "", "comment": ""}))
        enqueue(ctx.storage, repo.pid, "panel_generate",
                {"shot_id": shot.id, "comment": "", "quality": quality}, dedupe_key=f"panel:{repo.pid}:{shot.id}")
        queued.append(shot.id)
    if queued:
        repo.mutate_board(scene_id, lambda cur: (cur or Board(id=scene_id)).model_copy(
            update={"status": Status.generating, "approved_at": None}))
    return queued


def fix_panel(ctx: Ctx, repo: Repo, shot_id: str, comment: str, quality: str | None = None) -> Panel:
    """Regenerate one panel alone with a one-line comment. The board then needs approval again."""
    plan = plan_approved(repo)
    library_approved(repo, plan)
    comment = one_line_comment(comment, required=True)
    scene, _ = plan.shot(shot_id)
    p = repo.panel(shot_id)
    if p is None or p.current is None:
        raise GateError("no_panel", "This shot has no panel yet. Generate the scene first.")
    not_busy(p.status, shot_id)

    def fn(cur: Panel | None) -> Panel | None:
        assert cur is not None
        not_busy(cur.status, shot_id)
        return cur.model_copy(update={"status": Status.generating, "comment": comment, "error": ""})

    base = best_version(repo, p)
    out = repo.mutate_panel(shot_id, fn)
    repo.mutate_board(scene.id, lambda cur: cur.model_copy(update={"status": Status.generating, "approved_at": None})
                      if cur else None)
    _stale_final(repo, shot_id, "panel was fixed")
    enqueue(ctx.storage, repo.pid, "panel_generate",
            {"shot_id": shot_id, "comment": comment, "quality": quality or ctx.cfg.image.draft_quality,
             "base_version": base},
            dedupe_key=f"panel:{repo.pid}:{shot_id}")
    assert out is not None
    return out


def request_review(ctx: Ctx, repo: Repo, shot_id: str) -> None:
    p = repo.panel(shot_id)
    if p is None or p.current is None:
        raise GateError("no_panel", "No panel to review.")
    not_busy(p.status, shot_id)
    # Re-checking an approved shot keeps it approved; only unapproved shots show "checking".
    repo.mutate_panel(shot_id, lambda cur: cur.model_copy(update={"status": Status.in_review})
                      if cur and cur.status != Status.approved else None)
    enqueue(ctx.storage, repo.pid, "panel_review", {"shot_id": shot_id, "recheck_neighbours": False},
            dedupe_key=f"review:{repo.pid}:{shot_id}:v{p.current.n}")


def approve_board(repo: Repo, scene_id: str, overrides: list[str]) -> Board:
    """Creator approves the board. A failed score needs an explicit override."""
    plan = plan_approved(repo)
    scene = _scene(plan, scene_id)
    board = repo.board(scene_id)
    if board is None or not board.image_key:
        raise GateError("no_board", "This scene has no board yet.")
    failed, waiting = [], []
    for shot in scene.shots:
        p = repo.panel(shot.id)
        if p is None or p.current is None or p.status not in (Status.needs_approval, Status.approved):
            waiting.append(shot.id)
            continue
        r = repo.review(shot.id, p.current.n)
        if r is None:
            waiting.append(shot.id)
        elif r.verdict == "fail" and shot.id not in overrides and p.status != Status.approved:
            failed.append(shot.id)
        elif r.verdict == "fail" and shot.id not in overrides:
            overrides = [*overrides, shot.id]  # accepted one by one with "It's fine, approve"
        if board.panel_versions.get(shot.id) != p.current.n:
            waiting.append(shot.id)
    if waiting:
        raise GateError("review_incomplete",
                        f"Every panel must be generated and reviewed first. Waiting on: {', '.join(sorted(set(waiting)))}.")
    if failed:
        raise GateError("override_required",
                        f"These panels failed AI review: {', '.join(failed)}. Fix them or approve with an explicit override.")

    for shot in scene.shots:
        repo.mutate_panel(shot.id, lambda cur: cur.model_copy(update={"status": Status.approved}) if cur else None)
    out = repo.mutate_board(scene_id, lambda cur: cur.model_copy(update={
        "status": Status.approved, "approved_at": time.time(),
        "overrides": [o for o in overrides if o in {s.id for s in scene.shots}]}) if cur else None)
    assert out is not None
    return out


def accept_panel(repo: Repo, shot_id: str) -> Panel:
    """Creator approves one shot ("It's fine, approve"). For a failed score this is the explicit override."""
    plan_approved(repo)
    p = repo.panel(shot_id)
    if p is None or p.current is None:
        raise GateError("no_panel", "This shot has no sketch yet.")
    if p.status != Status.needs_approval:
        raise GateError("not_awaiting_approval", f"{shot_id} is '{p.status}', not waiting for approval.")
    if repo.review(shot_id, p.current.n) is None:
        raise GateError("review_incomplete", "Wait for the AI check to finish first.")
    out = repo.mutate_panel(shot_id, lambda cur: cur.model_copy(update={"status": Status.approved})
                            if cur and cur.status == Status.needs_approval else None)
    assert out is not None
    refresh_board_status(repo, repo.must_plan().plan, out.scene_id)
    return out


def improve_panel(ctx: Ctx, repo: Repo, shot_id: str) -> dict:
    """'Fix with AI': edit the best version using that version's own problems (never another version's)."""
    p = repo.panel(shot_id)
    if p is None or p.current is None:
        raise GateError("no_panel", "This shot has no sketch yet.")
    base = best_version(repo, p)
    r = repo.review(shot_id, base) if base else None
    if r is None:
        raise GateError("review_incomplete", "Wait for the AI check to finish first.")
    targets = fix_targets(r.issues)
    comment = combined_fix(r.suggested_fix, r.issues)
    if not comment:
        raise GateError("nothing_to_fix", "The AI check found nothing to fix in this shot.")
    detail = any(i.category in DETAIL_CATEGORIES for i in targets)
    quality = ctx.cfg.image.fix_quality_detail if detail else ctx.cfg.image.draft_quality
    fix_panel(ctx, repo, shot_id, comment, quality)
    return {"base_version": base, "quality": quality, "fixing": [i.problem for i in targets]}


def restore_version(ctx: Ctx, repo: Repo, shot_id: str, version: int) -> Panel:
    """Make an earlier version current again (free: no image call). Its AI check is reused."""
    plan = plan_approved(repo)
    p = repo.panel(shot_id)
    if p is None or p.current is None:
        raise GateError("no_panel", "This shot has no sketch yet.")
    not_busy(p.status, shot_id)
    src = next((v for v in p.versions if v.n == version), None)
    if src is None:
        raise GateError("unknown_version", f"Version {version} does not exist.")
    if src.n == p.current.n:
        raise GateError("already_current", "That version is already the current one.")
    n = p.current.n + 1
    old_review = repo.review(shot_id, src.n)

    def fn(cur: Panel | None) -> Panel | None:
        assert cur is not None
        v = PanelVersion(n=n, image_key=src.image_key, captioned_key=src.captioned_key, comment=f"restored v{src.n}",
                         quality=src.quality, refs_used=src.refs_used, restored_from=src.n)
        status = Status.needs_approval if old_review else Status.in_review
        return cur.model_copy(update={"versions": [*cur.versions, v], "status": status, "error": ""})

    out = repo.mutate_panel(shot_id, fn)
    if old_review:
        repo.save_review(old_review.model_copy(update={"id": f"{shot_id}:v{n}", "panel_version": n}))
    else:
        enqueue(ctx.storage, repo.pid, "panel_review", {"shot_id": shot_id, "recheck_neighbours": False},
                dedupe_key=f"review:{repo.pid}:{shot_id}:v{n}")
    _stale_final(repo, shot_id, "panel changed")
    compose_board(ctx, repo, plan, out.scene_id)
    assert out is not None
    return out


def best_version(repo: Repo, panel: Panel) -> int | None:
    """Highest-scoring reviewed version (ties go to the newest)."""
    scored = [(r.overall, v.n) for v in panel.versions if (r := repo.review(panel.id, v.n))]
    return max(scored)[1] if scored else (panel.current.n if panel.current else None)


def fix_context(repo: Repo, panel: Panel, base_n: int) -> dict:
    """What the fix prompt must keep, and which earlier problems must not come back."""
    base_review = repo.review(panel.id, base_n)
    keep = [GOOD_TEXT[c] for c in CRITERIA if base_review and base_review.scores.get(c, 0) >= 8]
    earlier: list[str] = []
    for v in panel.versions:
        r = repo.review(panel.id, v.n)
        if r and v.n != base_n:
            earlier += [i.problem for i in r.issues if i.severity == "major"]
    targets = fix_targets(base_review.issues) if base_review else []
    composition = any(i.category == "composition" for i in targets)
    return {
        "planned_shot": "Keep the shot as planned: DATA.framing, DATA.action.",
        "composition_change_allowed": composition,
        "keep_unchanged": keep,
        "do_not_reintroduce": list(dict.fromkeys(earlier))[-6:],
    }


GOOD_TEXT = {
    "character": "the characters' identity, faces, colours and outfits",
    "quality": "the correct anatomy and props",
    "background": "the location, lighting and time of day",
    "emotion": "the characters' expressions",
    "continuity": "consistency with the neighbouring shots",
    "framing": "the camera angle, framing and composition",
}


# ------------------------------------------------------------------ job bodies

def run_panel(ctx: Ctx, repo: Repo, shot_id: str, comment: str, quality: str, job_id: str = "",
              base_version: int | None = None) -> None:
    plan = repo.must_plan().plan
    scene, shot = plan.shot(shot_id)
    panel = repo.panel(shot_id) or Panel(id=shot_id, scene_id=scene.id)
    im = ctx.cfg.image
    draft = quality in ("low", "medium")
    refs = shot_refs(ctx, repo, plan, shot,
                     max_side=im.ref_max_side_draft if draft else im.ref_max_side_final,
                     compact=draft and im.compact_draft_refs)
    data = shot_data(plan, shot)
    images = list(refs.images)
    labels = list(refs.labels)

    base = None
    if comment and panel.current is not None:
        template, step = "panel_fix", "panel_fix"
        base = next((v for v in panel.versions if v.n == base_version), None) or panel.current
        images.insert(0, ("panel_to_revise.png",
                          imaging.downscale(ctx.storage.get_image(base.image_key), 1024, fmt="PNG")))
        labels = ["image 1: the panel to revise (the best version so far)"] + [
            f"image {i + 2}:{lbl.split(':', 1)[1]}" for i, lbl in enumerate(labels)]
        data.update(fix_context(repo, panel, base.n))
    else:
        template, step = "panel", "panel"
    data["reference_images"] = labels

    raw = ctx.llm.image(CallContext(repo.pid, shot_id, step, job_id),
                        prompt=prompts.compose(template, data, comment), refs=images,
                        size=ctx.cfg.image.request_size, quality=quality)
    img = imaging.crop_to_aspect(raw, tuple(ctx.cfg.image.aspect))
    captioned = imaging.caption_panel(img, shot.id, shot.action, shot.dialogue)

    n = (panel.current.n + 1) if panel.current else 1
    key = repo.image_key("storyboards", "panels", f"{shot_id}_v{n}.png")
    ckey = repo.image_key("storyboards", "panels", f"{shot_id}_v{n}_captioned.png")
    repo.put_asset(key, img, kind="panel", item_id=shot_id, version=n)
    repo.put_asset(ckey, captioned, kind="panel_captioned", item_id=shot_id, version=n, source="system")

    def fn(cur: Panel | None) -> Panel:
        cur = cur or Panel(id=shot_id, scene_id=scene.id)
        v = PanelVersion(n=n, image_key=key, captioned_key=ckey, comment=comment, quality=quality,
                         refs_used=refs.used, base_version=base.n if base else None)
        return cur.model_copy(update={"versions": [*cur.versions, v], "status": Status.in_review,
                                      "comment": "", "error": "", "stale_reason": ""})

    repo.mutate_panel(shot_id, fn)
    compose_board(ctx, repo, plan, scene.id)
    enqueue(ctx.storage, repo.pid, "panel_review",
            {"shot_id": shot_id, "recheck_neighbours": bool(comment)},
            dedupe_key=f"review:{repo.pid}:{shot_id}:v{n}")


def panel_failed(repo: Repo, shot_id: str, error: str) -> None:
    out = repo.mutate_panel(shot_id, lambda cur: cur.model_copy(update={"status": Status.failed, "error": error[:500]})
                            if cur else None)
    if out:
        refresh_board_status(repo, repo.must_plan().plan, out.scene_id)


class IssueOut(BaseModel):
    problem: str
    fix: str = ""
    severity: str = "minor"
    category: str = "other"


class ReviewOut(BaseModel):
    shot_id: str = ""
    scores: dict[str, float]
    notes: str = ""
    suggested_fix: str = ""
    issues: list[IssueOut] = Field(default_factory=list)

    @field_validator("scores")
    @classmethod
    def _scores(cls, v: dict[str, float]) -> dict[str, float]:
        missing = [c for c in CRITERIA if c not in v]
        if missing:
            raise ValueError(f"missing scores: {missing}")
        return {c: max(0.0, min(10.0, float(v[c]))) for c in CRITERIA}


def score(cfg: AppConfig, scores: dict[str, float]) -> tuple[float, str]:
    """The app, not the model, computes overall (0-100) and verdict."""
    w = cfg.review.weights
    overall = round(sum(scores.get(k, 0) / 10 * w[k] for k in w), 1)
    ok = overall >= cfg.review.pass_overall and all(scores.get(k, 0) >= m for k, m in cfg.review.pass_min.items())
    return overall, "pass" if ok else "fail"


def run_review(ctx: Ctx, repo: Repo, shot_id: str, recheck_neighbours: bool, job_id: str = "") -> None:
    plan = repo.must_plan().plan
    scene, shot = plan.shot(shot_id)
    panel = repo.panel(shot_id)
    if panel is None or panel.current is None:
        raise RuntimeError("panel has no image")
    version = panel.current.n
    max_side = ctx.cfg.image.review_max_side
    content: list[dict] = []

    def add_img(label: str, key: str, detail: str) -> None:
        content.append({"type": "text", "text": label})
        content.append({"type": "image_url", "image_url": {
            "url": _data_url(imaging.downscale(ctx.storage.get_image(key), max_side)), "detail": detail}})

    refs = shot_refs(ctx, repo, plan, shot)
    prev_s, next_s = _neighbours(scene, shot_id)
    neighbour_versions: dict[str, int] = {}
    data = {"shot_data": shot_data(plan, shot),
            "previous_shot_data": shot_data(plan, prev_s) if prev_s else None,
            "next_shot_data": shot_data(plan, next_s) if next_s else None}
    content.append({"type": "text", "text": "## DATA\n" + _json(data)})
    prev_n = panel.current.base_version or panel.current.restored_from
    prev = repo.review(shot_id, prev_n) if prev_n else None
    if prev:
        content.append({"type": "text", "text": "## PREVIOUS CHECK of the version this panel was revised from\n" + _json({
            "score": prev.overall, "scores": prev.scores,
            "problems": [i.problem for i in prev.issues],
            "instructions": "Check each previous problem first and drop it if fixed. Do not lower a criterion that "
                            "the revision did not touch. Only add a NEW problem if it is a real defect (anatomy, "
                            "duplicated or wrong objects, wrong character or place), never a style preference."})})
    add_img(f"panel (under review, {shot_id}):", panel.current.image_key, "high")
    for label, key, kind in zip(refs.labels, refs.keys, refs.kinds):
        name = "character_references" if kind == "character" else "location_reference"
        add_img(f"{name} ({label.split(':', 1)[1].strip()}):", key, "low")
    if not any(k == "location" for k in refs.kinds):
        content.append({"type": "text", "text": "location_reference: none provided"})
    for tag, nb in (("previous_panel", prev_s), ("next_panel", next_s)):
        if nb:
            np_ = repo.panel(nb.id)
            if np_ and np_.current:
                add_img(f"{tag} ({nb.id}):", np_.current.image_key, "low")
                neighbour_versions[nb.id] = np_.current.n
        else:
            content.append({"type": "text", "text": f"{tag}: none (no neighbour)"})

    out = ctx.llm.chat_json(CallContext(repo.pid, shot_id, "review", job_id), model=ctx.cfg.models.vision,
                            system=prompts.load("review"), user=content, schema=ReviewOut,
                            reserve_key="vision", max_tokens=4000)
    overall, verdict = score(ctx.cfg, out.scores)
    issues = [Issue(problem=i.problem[:240], fix=i.fix[:240], severity="major" if i.severity == "major" else "minor",
                    category=_cat(i.category)) for i in out.issues if i.problem.strip()][:8]
    repo.save_review(Review(id=f"{shot_id}:v{version}", shot_id=shot_id, panel_version=version, scores=out.scores,
                            overall=overall, verdict=verdict, notes=out.notes[:600],
                            suggested_fix=combined_fix(out.suggested_fix, issues), issues=issues,
                            neighbour_versions=neighbour_versions, model=ctx.cfg.models.vision))

    def fn(cur: Panel | None) -> Panel | None:
        if cur is None or cur.current is None or cur.current.n != version or cur.status != Status.in_review:
            return None  # panel moved on while we reviewed
        return cur.model_copy(update={"status": Status.needs_approval})

    repo.mutate_panel(shot_id, fn)

    # If neighbours change after a fix, re-check their continuity.
    if recheck_neighbours:
        for nb in (prev_s, next_s):
            if nb:
                np_ = repo.panel(nb.id)
                if np_ and np_.current and np_.status in (Status.needs_approval, Status.approved):
                    repo.mutate_panel(nb.id, lambda cur: cur.model_copy(update={"status": Status.in_review})
                                      if cur and cur.status in (Status.needs_approval, Status.approved) else None)
                    enqueue(ctx.storage, repo.pid, "panel_review", {"shot_id": nb.id, "recheck_neighbours": False},
                            dedupe_key=f"review:{repo.pid}:{nb.id}:v{np_.current.n}:after:{shot_id}:v{version}")
    refresh_board_status(repo, plan, scene.id)


def review_failed(repo: Repo, shot_id: str, error: str) -> None:
    # The panel image exists; leave it reviewable by hand with the error visible.
    out = repo.mutate_panel(shot_id, lambda cur: cur.model_copy(update={"status": Status.failed,
                                                                          "error": f"review: {error[:480]}"})
                            if cur else None)
    if out:
        refresh_board_status(repo, repo.must_plan().plan, out.scene_id)


# ------------------------------------------------------------------ board

def compose_board(ctx: Ctx, repo: Repo, plan: Plan, scene_id: str) -> Board | None:
    """Recompose the scene board in code from the current captioned panels."""
    scene = plan.scene(scene_id)
    panels = {p.id: p for p in repo.panels() if p.scene_id == scene_id}
    if any(sh.id not in panels or panels[sh.id].current is None for sh in scene.shots):
        refresh_board_status(repo, plan, scene_id)
        return None
    versions = {sh.id: panels[sh.id].current.n for sh in scene.shots}
    imgs = [ctx.storage.get_image(panels[sh.id].current.captioned_key) for sh in scene.shots]
    title = f"{scene.id}{' - ' + scene.title if scene.title else ''}"
    png = imaging.compose_board(title, imgs)
    tag = "-".join(str(versions[s.id]) for s in scene.shots)
    key = repo.image_key("storyboards", f"{scene_id}_storyboard_{tag}_{uuid.uuid4().hex[:6]}.png")
    repo.put_asset(key, png, kind="board", item_id=scene_id, source="system")

    def fn(cur: Board | None) -> Board | None:
        cur = cur or Board(id=scene_id)
        latest = {p.id: p.current.n for p in repo.panels() if p.scene_id == scene_id and p.current}
        if latest != versions or cur.panel_versions == versions and cur.image_key:
            return None  # stale compose (the job that changed the panels writes a newer one) or no change
        return cur.model_copy(update={"version": cur.version + 1, "image_key": key, "panel_versions": versions,
                                      "approved_at": None, "status": Status.in_review, "overrides": []})

    repo.mutate_board(scene_id, fn)
    return refresh_board_status(repo, plan, scene_id)


def board_status(plan: Plan, scene_id: str, panels: dict[str, Panel], reviews: dict[str, Review],
                 board: Board | None) -> str:
    scene = plan.scene(scene_id)
    if board and board.status == Status.approved:
        unchanged = all(panels.get(s.id) and panels[s.id].current and
                        board.panel_versions.get(s.id) == panels[s.id].current.n and
                        panels[s.id].status == Status.approved for s in scene.shots)
        if unchanged:
            return Status.approved
    sts = []
    for s in scene.shots:
        p = panels.get(s.id)
        if p is None or p.current is None:
            sts.append(Status.generating if p and p.status == Status.generating else Status.pending)
        else:
            sts.append(p.status)
    if Status.generating in sts:
        return Status.generating
    if Status.failed in sts:
        return Status.failed
    if Status.stale in sts:
        return Status.stale
    if Status.pending in sts:
        return Status.pending
    if Status.in_review in sts:
        return Status.in_review
    for s in scene.shots:
        p = panels[s.id]
        if f"{s.id}:v{p.current.n}" not in reviews:
            return Status.in_review
    return Status.needs_approval


def refresh_board_status(repo: Repo, plan: Plan, scene_id: str) -> Board | None:
    def fn(cur: Board | None) -> Board | None:
        cur = cur or Board(id=scene_id)
        panels = {p.id: p for p in repo.panels() if p.scene_id == scene_id}
        reviews = {r.id: r for r in repo.reviews() if r.shot_id in panels}
        st = board_status(plan, scene_id, panels, reviews, cur)
        if st == cur.status:
            return None
        upd: dict = {"status": st}
        if st != Status.approved:
            upd["approved_at"] = None
        return cur.model_copy(update=upd)
    return repo.mutate_board(scene_id, fn)


# ------------------------------------------------------------------ helpers

def _scene(plan: Plan, scene_id: str) -> Scene:
    try:
        return plan.scene(scene_id)
    except KeyError:
        raise GateError("unknown_scene", f"Scene {scene_id} is not in the plan.") from None


def _neighbours(scene: Scene, shot_id: str):
    ids = [s.id for s in scene.shots]
    i = ids.index(shot_id)
    return (scene.shots[i - 1] if i > 0 else None, scene.shots[i + 1] if i + 1 < len(ids) else None)


def _stale_final(repo: Repo, shot_id: str, reason: str) -> None:
    def fn(cur: Final | None) -> Final | None:
        if cur is None or cur.current is None or cur.status == Status.generating:
            return None
        return cur.model_copy(update={"status": Status.stale, "stale_reason": reason})
    repo.mutate_final(shot_id, fn)


def _data_url(jpeg: bytes) -> str:
    import base64
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def _json(d: dict) -> str:
    import json
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


def combined_fix(suggested: str, issues: list[Issue], limit: int = 600) -> str:
    """Numbered, concrete changes for the image model (serious problems only, unless none are left)."""
    chosen = fix_targets(issues)
    fixes = [i.fix.strip().rstrip(".") for i in chosen if i.fix.strip()]
    if not fixes:
        return suggested.strip()[:limit]
    text = " ".join(f"{n}. {f}." for n, f in enumerate(dict.fromkeys(fixes), 1))
    return text[:limit]


def fix_targets(issues: list[Issue]) -> list[Issue]:
    major = [i for i in issues if i.severity == "major"]
    return major or issues[:2]


DETAIL_CATEGORIES = {"anatomy", "object", "character"}


def _cat(c: str) -> str:
    c = (c or "").strip().lower()
    return c if c in {"anatomy", "object", "character", "place", "lighting", "composition"} else "other"
