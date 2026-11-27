"""Server-side gates. Every state-changing endpoint calls these; hiding a button is not enough."""
from __future__ import annotations

from ..domain.models import Plan, Status, char_item_id, loc_item_id, reaction_item_id
from .repo import Repo


class GateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def required_library_ids(plan: Plan, scene_id: str | None = None) -> list[str]:
    """Library items needed by the whole plan, or by one scene."""
    ids: dict[str, None] = {}
    scenes = [plan.scene(scene_id)] if scene_id else plan.scenes
    for sc in scenes:
        for sh in sc.shots:
            for cid in sh.character_ids:
                ids[char_item_id(cid)] = None
            for r in sh.reactions:
                if r.emotion and r.emotion != "neutral":
                    ids[reaction_item_id(r.character_id, r.emotion)] = None
            if sh.location_id:
                ids[loc_item_id(sh.location_id)] = None
    if scene_id is None:
        for c in plan.characters:
            ids[char_item_id(c.id)] = None
            for e in c.reactions:
                ids[reaction_item_id(c.id, e)] = None
        for l in plan.locations:
            ids[loc_item_id(l.id)] = None
    return list(ids)


def plan_approved(repo: Repo) -> Plan:
    pr = repo.plan_record()
    if pr is None:
        raise GateError("plan_missing", "Upload and parse a Shot Planner first.")
    if pr.status != Status.approved:
        raise GateError("plan_not_approved", "Check and approve the parsed plan before building the library.")
    return pr.plan


def library_approved(repo: Repo, plan: Plan) -> None:
    items = {i.id: i for i in repo.library()}
    missing = [iid for iid in required_library_ids(plan)
               if iid not in items or items[iid].status != Status.approved]
    if missing:
        names = ", ".join(items[i].name if i in items else i for i in missing[:6])
        more = f" and {len(missing) - 6} more" if len(missing) > 6 else ""
        raise GateError("library_not_approved",
                        f"Approve every reference and reaction before storyboarding. Waiting on: {names}{more}.")


def first_scene_first(repo: Repo, plan: Plan, scene_id: str) -> None:
    if not plan.scenes:
        raise GateError("no_scenes", "The plan has no scenes.")
    first = plan.scenes[0].id
    if scene_id == first:
        return
    board = repo.board(first)
    if board is None or board.status != Status.approved:
        raise GateError("first_scene_pending",
                        f"Board and approve {first} first; it fixes the look for every other scene.")


def base_reference_approved(repo: Repo, character_id: str) -> None:
    base = repo.item_or_none(char_item_id(character_id))
    if base is None or base.status != Status.approved or base.approved_version is None:
        raise GateError("base_not_approved", "Approve the character's base reference before adding reactions.")


def board_approved(repo: Repo, scene_id: str) -> None:
    b = repo.board(scene_id)
    if b is None or b.status != Status.approved:
        raise GateError("board_not_approved", f"Approve the {scene_id} storyboard before rendering finals.")


def awaiting_approval(status: str, what: str) -> None:
    if status != Status.needs_approval:
        raise GateError("not_awaiting_approval", f"{what} is '{status}', not waiting for approval.")


def not_busy(status: str, what: str) -> None:
    if status in (Status.generating, Status.in_review):
        raise GateError("busy", f"{what} is already {status}.")


def one_line_comment(comment: str, required: bool = True) -> str:
    c = " ".join((comment or "").split())
    if required and not c:
        raise GateError("comment_required", "Give a short one-line comment.")
    if len(c) > 600:  # people type one line; AI-combined fixes can be longer
        raise GateError("comment_too_long", "Keep the comment short (600 characters max).")
    return c
