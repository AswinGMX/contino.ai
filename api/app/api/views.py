"""Read models for the web app: records + image URLs + which actions the server will allow."""
from __future__ import annotations

from urllib.parse import quote

from ..domain.models import Board, Final, LibraryItem, Panel, Plan, Review, Status
from ..services.budget import BudgetGuard
from ..services.context import Ctx
from ..services.gates import GateError, first_scene_first, library_approved, required_library_ids
from ..services.repo import Repo
from ..services.storyboard import board_status


def img_url(pid: str, key: str | None) -> str | None:
    if not key:
        return None
    prefix = f"projects/{pid}/"
    rest = key[len(prefix):] if key.startswith(prefix) else key
    return f"/api/projects/{quote(pid)}/images/{quote(rest)}"


def _versions(pid: str, versions) -> list[dict]:
    out = []
    for v in versions:
        d = v.model_dump(mode="json")
        d["url"] = img_url(pid, v.image_key)
        if getattr(v, "captioned_key", ""):
            d["captioned_url"] = img_url(pid, v.captioned_key)
        out.append(d)
    return out


def _gate(fn, *args) -> str | None:
    try:
        fn(*args)
        return None
    except GateError as e:
        return e.message


def project_overview(ctx: Ctx, repo: Repo) -> dict:
    p = repo.project()
    pr = repo.plan_record()
    plan = pr.plan if pr else None
    lib = repo.library()
    boards = {b.id: b for b in repo.boards()}
    finals = repo.finals()
    n_shots = sum(len(sc.shots) for sc in plan.scenes) if plan else 0
    required = set(required_library_ids(plan)) if plan and pr.status == Status.approved else set()
    lib_req = [i for i in lib if i.id in required]
    return {
        "project": p.model_dump(mode="json", by_alias=True),
        "plan_status": pr.status if pr else None,
        "counts": {
            "characters": len(plan.characters) if plan else 0,
            "locations": len(plan.locations) if plan else 0,
            "scenes": len(plan.scenes) if plan else 0,
            "shots": n_shots,
            "library_total": len(lib_req),
            "library_approved": sum(1 for i in lib_req if i.status == Status.approved),
            "boards_approved": sum(1 for b in boards.values() if b.status == Status.approved),
            "finals_approved": sum(1 for f in finals if f.status == Status.approved),
        },
        "active_jobs": len(ctx.storage.list_jobs(repo.pid, active_only=True)),
        "cost_usd": round(sum(u.cost_usd for u in repo.usage()), 4),
        "cover_url": _cover(repo, finals, lib),
        "budget": BudgetGuard(ctx.storage, ctx.cfg).status(),
    }


def plan_view(repo: Repo) -> dict:
    pr = repo.must_plan()
    proj = repo.project()
    return {"status": pr.status, "plan": pr.plan.model_dump(mode="json"), "approved_at": pr.approved_at,
            "source_url": img_url(repo.pid, pr.source_key) if pr.source_key else None,
            "parse_status": proj.parse_status, "parse_error": proj.parse_error}


def library_view(ctx: Ctx, repo: Repo) -> dict:
    pr = repo.plan_record()
    plan: Plan | None = pr.plan if pr else None
    items = {i.id: i for i in repo.library()}
    groups: dict[str, list] = {"characters": [], "locations": []}
    if not plan:
        return {**groups, "plan_approved": False}

    def item(i: LibraryItem) -> dict:
        d = i.model_dump(mode="json", by_alias=True, exclude={"versions"})
        cur_v = i.current
        if cur_v and cur_v.source == "uploaded":
            d["check"] = None       # uploaded pictures are the creator's call: never scored
            d["checking"] = False
        elif i.kind == "reaction" and i.check and i.check.basis != "base":
            d["check"] = None       # old check compared the expression with the text; it is judged against its main picture now
        d["versions"] = _versions(repo.pid, i.versions)
        cur = i.current
        d["current_url"] = img_url(repo.pid, cur.image_key) if cur else None
        appr = i.approved()
        d["approved_url"] = img_url(repo.pid, appr.image_key) if appr else None
        return d

    for c in plan.characters:
        base = items.get(f"char:{c.id}")
        reactions = [item(items[f"react:{c.id}:{_slug(e)}"]) for e in c.reactions if f"react:{c.id}:{_slug(e)}" in items]
        groups["characters"].append({
            "character": c.model_dump(mode="json"),
            "base": item(base) if base else None,
            "reactions": reactions,
            "reactions_locked": not (base and base.status == Status.approved),
        })
    for l in plan.locations:
        it = items.get(f"loc:{l.id}")
        groups["locations"].append({"location": l.model_dump(mode="json"), "item": item(it) if it else None})
    return {**groups, "plan_approved": pr.status == Status.approved}


def _review(r: Review | None) -> dict | None:
    return r.model_dump(mode="json", exclude={"rev"}) if r else None


def storyboard_view(ctx: Ctx, repo: Repo) -> dict:
    pr = repo.plan_record()
    if not pr:
        return {"scenes": []}
    plan = pr.plan
    panels = {p.id: p for p in repo.panels()}
    reviews = {r.id: r for r in repo.reviews()}
    boards = {b.id: b for b in repo.boards()}
    lib_gate = _gate(library_approved, repo, plan) if pr.status == Status.approved else "Approve the plan first."
    scenes = []
    for sc in plan.scenes:
        b: Board | None = boards.get(sc.id)
        rows = []
        for sh in sc.shots:
            p: Panel | None = panels.get(sh.id)
            cur = p.current if p else None
            r = reviews.get(f"{sh.id}:v{cur.n}") if cur else None
            rows.append({
                "shot": sh.model_dump(mode="json"),
                "status": p.status if p else Status.pending,
                "error": p.error if p else "",
                "stale_reason": p.stale_reason if p else "",
                "version": cur.n if cur else None,
                "url": img_url(repo.pid, cur.image_key) if cur else None,
                "captioned_url": img_url(repo.pid, cur.captioned_key) if cur else None,
                "versions": _versions(repo.pid, p.versions) if p else [],
                "review": _review(r),
                "history": [{"version": v.n, "overall": reviews[f"{sh.id}:v{v.n}"].overall,
                             "verdict": reviews[f"{sh.id}:v{v.n}"].verdict,
                             "url": img_url(repo.pid, v.image_key),
                             "comment": v.comment,
                             "review": _review(reviews[f"{sh.id}:v{v.n}"])}
                            for v in (p.versions if p else []) if f"{sh.id}:v{v.n}" in reviews],
                "override": bool(b and sh.id in b.overrides),
            })
        live_status = board_status(plan, sc.id, panels, reviews, b)
        scenes.append({
            "id": sc.id, "title": sc.title,
            "board": {
                "status": live_status, "version": b.version if b else 0,
                "url": img_url(repo.pid, b.image_key) if b and b.image_key else None,
                "approved_at": b.approved_at if b else None, "overrides": b.overrides if b else [],
            },
            "locked_reason": lib_gate or _gate(first_scene_first, repo, plan, sc.id),
            "panels": rows,
        })
    return {"scenes": scenes, "criteria_weights": ctx.cfg.review.weights,
            "typical_fix_cost_usd": typical_cost(repo, ("panel", "panel_fix")),
            "pass_overall": ctx.cfg.review.pass_overall, "pass_min": ctx.cfg.review.pass_min,
            "draft_quality": ctx.cfg.image.draft_quality, "final_quality": ctx.cfg.image.final_quality}


def finals_view(ctx: Ctx, repo: Repo) -> dict:
    pr = repo.plan_record()
    if not pr:
        return {"scenes": []}
    plan = pr.plan
    finals = {f.id: f for f in repo.finals()}
    panels = {p.id: p for p in repo.panels()}
    boards = {b.id: b for b in repo.boards()}
    scenes = []
    for sc in plan.scenes:
        b = boards.get(sc.id)
        shots = []
        for sh in sc.shots:
            f: Final | None = finals.get(sh.id)
            p = panels.get(sh.id)
            cur = f.current if f else None
            shots.append({
                "shot": sh.model_dump(mode="json"),
                "status": f.status if f else Status.pending,
                "error": f.error if f else "",
                "stale_reason": f.stale_reason if f else "",
                "version": cur.n if cur else None,
                "url": img_url(repo.pid, cur.image_key) if cur else None,
                "panel_url": img_url(repo.pid, p.current.image_key) if p and p.current else None,
                "versions": _versions(repo.pid, f.versions) if f else [],
                "approved_version": f.approved_version if f else None,
            })
        scenes.append({"id": sc.id, "title": sc.title,
                       "board_approved": bool(b and b.status == Status.approved),
                       "shots": shots})
    return {"scenes": scenes, "final_quality": ctx.cfg.image.final_quality,
            "typical_final_cost_usd": typical_cost(repo, ("final",))}


def _slug(e: str) -> str:
    from ..domain.models import slugify
    return slugify(e)


def typical_cost(repo: Repo, steps: tuple[str, ...]) -> float | None:
    """Median cost of successful calls for these steps in this project, for 'costs about 6¢' hints."""
    costs = sorted(u.cost_usd for u in repo.usage() if u.step in steps and u.ok)
    return round(costs[len(costs) // 2], 4) if costs else None


def _cover(repo: Repo, finals: list, lib: list) -> str | None:
    """A representative image for the project card: an approved final, else a panel, else a location."""
    for f in sorted(finals, key=lambda f: f.id):
        v = next((v for v in f.versions if v.n == f.approved_version), None)
        if v:
            return img_url(repo.pid, v.image_key)
    for p in sorted(repo.panels(), key=lambda p: p.id):
        if p.current:
            return img_url(repo.pid, p.current.image_key)
    for i in lib:
        if i.kind == "location" and i.current:
            return img_url(repo.pid, i.current.image_key)
    for i in lib:
        if i.current:
            return img_url(repo.pid, i.current.image_key)
    return None
