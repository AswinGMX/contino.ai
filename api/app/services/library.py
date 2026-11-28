"""Step 2: character references, reactions and location references.

Each item offers Upload (used instead of generating) or Generate (from the planner
description, with an optional comment). The creator approves each one. If an
approved reference changes, everything that used it is marked stale.
"""
from __future__ import annotations

import base64
import json

from pydantic import BaseModel, Field

from ..domain.models import (ImageCheck, ImageVersion, Issue, LibraryItem, Plan, Status, char_item_id, loc_item_id,
                             reaction_item_id, slugify)
from ..jobs.queue import enqueue
from . import imaging, prompts
from .context import Ctx
from .gates import GateError, awaiting_approval, base_reference_approved, not_busy, one_line_comment, plan_approved
from .llm import CallContext
from .repo import Repo
from .stale import mark_dependents_stale


def sync_library(repo: Repo, plan: Plan) -> None:
    """Create items for every character, reaction and location in the plan; keep existing work."""
    wanted: dict[str, LibraryItem] = {}
    for c in plan.characters:
        wanted[char_item_id(c.id)] = LibraryItem(id=char_item_id(c.id), kind="character", subject_id=c.id, name=c.name)
        for e in c.reactions:
            iid = reaction_item_id(c.id, e)
            wanted[iid] = LibraryItem(id=iid, kind="reaction", subject_id=c.id, name=c.name, emotion=e)
    for l in plan.locations:
        wanted[loc_item_id(l.id)] = LibraryItem(id=loc_item_id(l.id), kind="location", subject_id=l.id, name=l.name)

    for iid, fresh in wanted.items():
        repo.mutate_item(iid, lambda cur, fresh=fresh: fresh if cur is None else
                         (cur.model_copy(update={"name": fresh.name}) if cur.name != fresh.name else None))
    for item in repo.library():
        if item.id not in wanted and not item.versions:
            repo.s.delete(repo.pid, "library", item.id)


def _asset_kind(item: LibraryItem) -> str:
    return {"character": "character", "reaction": "expression", "location": "place"}[item.kind]


def _key(repo: Repo, item: LibraryItem, n: int) -> str:
    name = slugify(item.name)
    if item.kind == "character":
        return repo.image_key("library", "characters", f"{name}_v{n}.png")
    if item.kind == "reaction":
        return repo.image_key("library", "reactions", f"{name}_{slugify(item.emotion)}_v{n}.png")
    return repo.image_key("library", "locations", f"{name}_v{n}.png")


# ------------------------------------------------------------------ commands

def request_generate(ctx: Ctx, repo: Repo, item_id: str, comment: str = "", quality: str | None = None,
                     fresh: bool = False) -> LibraryItem:
    plan_approved(repo)
    item = repo.item(item_id)
    not_busy(item.status, item.name)
    if item.kind == "reaction":
        base_reference_approved(repo, item.subject_id)
        _base_not_wrong_subject(repo, item.subject_id)
    comment = one_line_comment(comment, required=False)

    def fn(cur: LibraryItem | None) -> LibraryItem | None:
        assert cur is not None
        not_busy(cur.status, cur.name)
        return cur.model_copy(update={"status": Status.generating, "comment": comment, "error": ""})

    out = repo.mutate_item(item_id, fn)
    enqueue(ctx.storage, repo.pid, "library_generate",
            {"item_id": item_id, "comment": comment, "quality": quality or ctx.cfg.image.final_quality, "fresh": fresh},
            dedupe_key=f"lib:{repo.pid}:{item_id}")
    assert out is not None
    return out


def upload(ctx: Ctx, repo: Repo, item_id: str, data: bytes) -> LibraryItem:
    plan_approved(repo)
    item = repo.item(item_id)
    not_busy(item.status, item.name)
    if item.kind == "reaction":
        base_reference_approved(repo, item.subject_id)
    try:
        png = imaging.validate_upload(data)
    except ValueError as exc:
        raise GateError("invalid_image", str(exc)) from exc
    n = (item.current.n + 1) if item.current else 1
    key = _key(repo, item, n)
    repo.put_asset(key, png, kind=_asset_kind(item), item_id=item.id, version=n, source="uploaded")

    def fn(cur: LibraryItem | None) -> LibraryItem | None:
        assert cur is not None
        not_busy(cur.status, cur.name)
        v = ImageVersion(n=n, image_key=key, source="uploaded")
        return cur.model_copy(update={"versions": [*cur.versions, v], "status": Status.needs_approval,
                                      "comment": "", "error": "", "check": None})

    out = repo.mutate_item(item_id, fn)
    assert out is not None
    # The creator's own picture is the reference by definition: it is not scored by the AI check.
    return out


def approve(repo: Repo, item_id: str) -> LibraryItem:
    plan_approved(repo)
    item = repo.item(item_id)
    awaiting_approval(item.status, item.name)
    previous = item.approved_version

    def fn(cur: LibraryItem | None) -> LibraryItem | None:
        assert cur is not None and cur.current is not None
        awaiting_approval(cur.status, cur.name)
        return cur.model_copy(update={"status": Status.approved, "approved_version": cur.current.n, "comment": ""})

    out = repo.mutate_item(item_id, fn)
    assert out is not None
    if previous is not None and previous != out.approved_version:
        mark_dependents_stale(repo, item_id, out.approved_version, f"{out.name} reference changed")
    return out


def reject(ctx: Ctx, repo: Repo, item_id: str, comment: str, quality: str | None = None) -> LibraryItem:
    """Reject with one short comment; the item is regenerated with that comment applied."""
    comment = one_line_comment(comment, required=True)
    item = repo.item(item_id)
    if item.status not in (Status.needs_approval, Status.approved, Status.stale, Status.failed):
        raise GateError("not_rejectable", f"{item.name} is '{item.status}'.")
    repo.mutate_item(item_id, lambda cur: cur.model_copy(update={"status": Status.rejected, "comment": comment})
                     if cur else None)
    return request_generate(ctx, repo, item_id, comment=comment, quality=quality)


# ------------------------------------------------------------------ job body

def run_generate(ctx: Ctx, repo: Repo, item_id: str, comment: str, quality: str, job_id: str = "",
                 fresh: bool = False) -> None:
    plan = repo.must_plan().plan
    item = repo.item(item_id)
    style = plan.style
    refs: list[tuple[str, bytes]] = []
    refs_used: dict[str, int] = {}
    size = ctx.cfg.image.reference_size

    if item.kind == "character":
        c = plan.character(item.subject_id)
        template = "character_ref"
        data = {"name": item.name, "kind": (c.kind if c else "") or "creature",
                "description": c.description if c else "", "style": style}
    elif item.kind == "reaction":
        c = plan.character(item.subject_id)
        base = repo.item(char_item_id(item.subject_id))
        bv = base.approved()
        if bv is None:
            raise RuntimeError("base reference is not approved")
        refs.append((f"{slugify(item.name)}_base.png",
                     imaging.downscale(ctx.storage.get_image(bv.image_key), ctx.cfg.image.ref_max_side_final, fmt="PNG")))
        refs_used[base.id] = bv.n
        template = "reaction_ref"
        data = {"name": item.name, "description": c.description if c else "", "emotion": item.emotion,
                "style": style, "reference_images": ["image 1: approved base reference of this character"]}
    else:
        loc = plan.location(item.subject_id)
        template = "location_ref"
        size = ctx.cfg.image.request_size
        data = {"name": item.name, "description": loc.description if loc else "", "style": style}

    # A rejection comment edits the previous image, unless the creator asked to start over or the
    # AI check said the previous picture shows the wrong subject (editing it would keep the mistake).
    wrong_subject = bool(item.check and item.current and item.current.source != "uploaded"
                         and item.check.version == item.current.n and item.check.score < 50)
    if comment and item.current is not None and not fresh and not wrong_subject:
        refs.append(("previous.png", imaging.downscale(ctx.storage.get_image(item.current.image_key), 768, fmt="PNG")))
        data.setdefault("reference_images", []).append(f"image {len(refs)}: previous attempt to revise")

    img = ctx.llm.image(CallContext(repo.pid, f"LIB:{item.name}{':' + item.emotion if item.emotion else ''}",
                                    f"{item.kind}_ref", job_id),
                        prompt=prompts.compose(template, data, comment), refs=refs, size=size, quality=quality)
    if item.kind == "location":
        img = imaging.crop_to_aspect(img, tuple(ctx.cfg.image.aspect))
    else:
        img = imaging.to_png(imaging.open_image(img))

    # One generate job per item at a time (job dedupe + busy gate), so the version number is safe here
    # and the upload happens outside the record lock.
    fresh = repo.item(item_id)
    n = (fresh.current.n + 1) if fresh.current else 1
    key = _key(repo, fresh, n)
    repo.put_asset(key, img, kind=_asset_kind(fresh), item_id=fresh.id, version=n)

    def fn(cur: LibraryItem | None) -> LibraryItem | None:
        assert cur is not None
        v = ImageVersion(n=n, image_key=key, source="generated", comment=comment, quality=quality,
                         refs_used=refs_used)
        return cur.model_copy(update={"versions": [*cur.versions, v], "status": Status.needs_approval,
                                      "comment": "", "error": "", "check": None})

    repo.mutate_item(item_id, fn)
    request_check(ctx, repo, item_id)


def generate_failed(repo: Repo, item_id: str, error: str) -> None:
    repo.mutate_item(item_id, lambda cur: cur.model_copy(update={"status": Status.failed, "error": error[:500]})
                     if cur else None)


# ------------------------------------------------------------------ expression sheets
# One image call draws every emotion a character needs as a grid; code crops it into one
# reference per emotion. Cuts reaction cost and approval clicks by up to 8x per character.

def _sheet_targets(repo: Repo, character_id: str) -> list[LibraryItem]:
    return sorted((i for i in repo.library() if i.kind == "reaction" and i.subject_id == character_id
                   and i.status != Status.approved and i.status not in (Status.generating, Status.in_review)),
                  key=lambda i: i.emotion)


def request_reaction_sheet(ctx: Ctx, repo: Repo, character_id: str, comment: str = "",
                           quality: str | None = None) -> list[str]:
    plan_approved(repo)
    base_reference_approved(repo, character_id)
    _base_not_wrong_subject(repo, character_id)
    comment = one_line_comment(comment, required=False)
    targets = _sheet_targets(repo, character_id)
    if not targets:
        raise GateError("nothing_to_generate", "Every reaction for this character is approved or in progress.")
    ids = [t.id for t in targets]
    for iid in ids:
        repo.mutate_item(iid, lambda cur: cur.model_copy(update={"status": Status.generating, "error": ""})
                         if cur and cur.status not in (Status.generating, Status.in_review) else None)
    enqueue(ctx.storage, repo.pid, "reaction_sheet",
            {"character_id": character_id, "item_ids": ids, "comment": comment,
             "quality": quality or ctx.cfg.image.final_quality},
            dedupe_key=f"sheet:{repo.pid}:{character_id}")
    return ids


def _base_not_wrong_subject(repo: Repo, character_id: str) -> None:
    """Expressions copy the main picture, so a main picture the AI check flagged as the wrong subject must be fixed first."""
    base = repo.item_or_none(char_item_id(character_id))
    bv = base.approved() if base else None
    c = base.check if base else None
    if bv and bv.source != "uploaded" and c and c.version == bv.n and not c.error and c.score < 50:
        raise GateError("base_wrong_subject",
                        f"The AI check says {base.name}'s main picture shows the wrong subject ({c.score}/100). "
                        "Fix it with Start over first; expressions are drawn from it.")


def run_reaction_sheet(ctx: Ctx, repo: Repo, character_id: str, item_ids: list[str], comment: str,
                       quality: str, job_id: str = "") -> None:
    plan = repo.must_plan().plan
    c = plan.character(character_id)
    base = repo.item(char_item_id(character_id))
    bv = base.approved()
    if bv is None:
        raise RuntimeError("base reference is not approved")
    base_png = imaging.downscale(ctx.storage.get_image(bv.image_key), ctx.cfg.image.ref_max_side_final, fmt="PNG")
    items = [repo.item(i) for i in item_ids]
    per = max(1, ctx.cfg.image.sheet_max_per_image)

    for start in range(0, len(items), per):
        chunk = items[start:start + per]
        cols, rows = imaging.sheet_layout(len(chunk))
        data = {"name": base.name, "description": c.description if c else "", "style": plan.style,
                "grid": {"columns": cols, "rows": rows},
                "cells": [{"cell": n + 1, "emotion": it.emotion} for n, it in enumerate(chunk)],
                "reference_images": ["image 1: approved base reference of this character"]}
        label = f"LIB:{base.name}:reactions" + (f"#{start // per + 1}" if len(items) > per else "")
        sheet = ctx.llm.image(CallContext(repo.pid, label, "reaction_sheet", job_id),
                              prompt=prompts.compose("reaction_sheet", data, comment),
                              refs=[(f"{slugify(base.name)}_base.png", base_png)],
                              size=ctx.cfg.image.request_size, quality=quality)
        crops = imaging.crop_grid(sheet, cols, rows, len(chunk))
        sheet_key = repo.image_key("library", "sheets", f"{slugify(base.name)}_{job_id or 'sheet'}_{start // per + 1}.png")
        repo.put_asset(sheet_key, imaging.to_png(imaging.open_image(sheet)), kind="sheet", item_id=char_item_id(character_id))
        for it, png in zip(chunk, crops):
            fresh = repo.item(it.id)
            n = (fresh.current.n + 1) if fresh.current else 1
            key = _key(repo, fresh, n)
            repo.put_asset(key, png, kind="expression", item_id=fresh.id, version=n)
            v = ImageVersion(n=n, image_key=key, source="sheet", comment=comment, quality=quality,
                             refs_used={base.id: bv.n})
            repo.mutate_item(it.id, lambda cur, v=v: cur.model_copy(update={
                "versions": [*cur.versions, v], "status": Status.needs_approval, "comment": "", "error": "",
                "check": None}) if cur else None)
            request_check(ctx, repo, it.id)


def reaction_sheet_failed(repo: Repo, item_ids: list[str], error: str) -> None:
    for iid in item_ids:
        repo.mutate_item(iid, lambda cur: cur.model_copy(update={"status": Status.failed, "error": error[:500]})
                         if cur and cur.status == Status.generating else None)


def approve_all_reactions(repo: Repo, character_id: str) -> list[str]:
    """The creator has looked at every reaction for this character and approves them in one click."""
    done = []
    for it in repo.library():
        if it.kind == "reaction" and it.subject_id == character_id and it.status == Status.needs_approval:
            approve(repo, it.id)
            done.append(it.id)
    if not done:
        raise GateError("nothing_to_approve", "No reactions are waiting for approval.")
    return done


# ------------------------------------------------------------------ AI picture check
# A cheap vision call (~1-2 cents) that finds anatomy mistakes, extra limbs, duplicated objects or a wrong
# subject, and suggests a one-line fix. Advisory only: it never regenerates anything by itself.

class _IssueOut(BaseModel):
    problem: str
    fix: str = ""
    severity: str = "minor"


class CheckOut(BaseModel):
    score: float
    issues: list[_IssueOut] = Field(default_factory=list)
    notes: str = ""
    suggested_fix: str = ""


def request_check(ctx: Ctx, repo: Repo, item_id: str) -> None:
    item = repo.item(item_id)
    if item.current is None:
        raise GateError("no_picture", "There is no picture to check yet.")
    if item.current.source == "uploaded":
        raise GateError("uploaded_not_scored", "Your own uploaded pictures are not scored by the AI check.")
    n = item.current.n
    repo.mutate_item(item_id, lambda cur: cur.model_copy(update={"checking": True}) if cur else None)
    enqueue(ctx.storage, repo.pid, "library_check", {"item_id": item_id, "version": n},
            dedupe_key=f"libcheck:{repo.pid}:{item_id}:v{n}")


def run_check(ctx: Ctx, repo: Repo, item_id: str, version: int, job_id: str = "") -> None:
    plan = repo.must_plan().plan
    item = repo.item(item_id)
    v = next((x for x in item.versions if x.n == version), None)
    if v is None or v.source == "uploaded":
        repo.mutate_item(item_id, lambda cur: cur.model_copy(update={"checking": False}) if cur else None)
        return
    max_side = ctx.cfg.image.review_max_side

    def img(key: str) -> dict:
        jpeg = imaging.downscale(ctx.storage.get_image(key), max_side)
        return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}

    if item.kind == "location":
        loc = plan.location(item.subject_id)
        data = {"kind": "place", "name": item.name, "description": loc.description if loc else "", "style": plan.style}
    elif item.kind == "reaction":
        # The approved main picture IS the character (it may be the creator's own upload): judge the
        # expression against it, never against the plan's text description.
        data = {"kind": "expression", "name": item.name, "emotion": item.emotion, "style": plan.style,
                "rule": "The APPROVED BASE REFERENCE defines exactly who this character is and how many people or "
                        "creatures it shows. Do not compare the picture with any text description. Check only: same "
                        "subject as the base, same face/colours/clothes, the emotion reads clearly, correct anatomy, "
                        "clean background."}
    else:
        c = plan.character(item.subject_id)
        data = {"kind": "character", "name": item.name, "type": (c.kind if c else "") or "creature",
                "description": c.description if c else "", "style": plan.style}
    content: list[dict] = [{"type": "text", "text": "## DATA\n" + json.dumps(data, ensure_ascii=False)},
                           {"type": "text", "text": "PICTURE:"}, img(v.image_key)]
    if item.kind == "reaction":
        base = repo.item_or_none(char_item_id(item.subject_id))
        bv = base.approved() if base else None
        if bv:
            content += [{"type": "text", "text": "APPROVED BASE REFERENCE:"}, img(bv.image_key)]

    label = f"LIB:{item.name}{':' + item.emotion if item.emotion else ''}"
    out = ctx.llm.chat_json(CallContext(repo.pid, label, "ref_check", job_id), model=ctx.cfg.models.vision,
                            system=prompts.load("ref_check"), user=content, schema=CheckOut,
                            reserve_key="vision", max_tokens=3000)
    issues = [Issue(problem=i.problem[:240], fix=i.fix[:240], severity="major" if i.severity == "major" else "minor")
              for i in out.issues if i.problem.strip()][:8]
    fixes = [i.fix.strip().rstrip(".") for i in issues if i.fix.strip()]
    check = ImageCheck(version=version, score=max(0, min(100, round(out.score))), issues=issues,
                       suggested_fix=("; ".join(dict.fromkeys(fixes)) + ".") if fixes else out.suggested_fix[:400],
                       notes=out.notes[:300], model=ctx.cfg.models.vision,
                       basis="base" if item.kind == "reaction" else "description")
    repo.mutate_item(item_id, lambda cur: cur.model_copy(update={"check": check, "checking": False})
                     if cur and cur.current and cur.current.n == version else
                     (cur.model_copy(update={"checking": False}) if cur else None))


def check_failed(repo: Repo, item_id: str, version: int, error: str) -> None:
    repo.mutate_item(item_id, lambda cur: cur.model_copy(update={
        "checking": False, "check": ImageCheck(version=version, score=0, error=error[:300])}) if cur else None)
