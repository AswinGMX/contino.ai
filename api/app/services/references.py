"""Approved references attached to every image request for a shot.

A panel request gets each character's base reference and matching reaction, plus
the location reference.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import Plan, Shot, char_item_id, loc_item_id, reaction_item_id, slugify
from . import imaging
from .context import Ctx
from .repo import Repo


@dataclass
class RefSet:
    images: list[tuple[str, bytes]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    used: dict[str, int] = field(default_factory=dict)
    keys: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)   # character | location (for the review labels)
    max_side: int | None = None

    def add(self, ctx: Ctx, name: str, label: str, item_id: str, version: int, key: str, kind: str) -> None:
        data = ctx.storage.get_image(key)
        if self.max_side:
            # Image input is billed by size: a 512 px reference costs a fraction of a 1536 px one.
            data = imaging.downscale(data, self.max_side, fmt="PNG")
        self.images.append((name, data))
        self.labels.append(f"image {len(self.images)}: {label}")
        self.used[item_id] = version
        self.keys.append(key)
        self.kinds.append(kind)


def shot_refs(ctx: Ctx, repo: Repo, plan: Plan, shot: Shot, max_side: int | None = None,
              compact: bool = False) -> RefSet:
    """References for one shot.

    compact=True (draft panels): when a character has an approved reaction image for this shot, send
    only that (it was drawn from the base reference, so it carries the same face and clothes). The base
    is still recorded in `used`, so changing it still marks the panel stale.
    """
    rs = RefSet(max_side=max_side)
    emotions = {r.character_id: r.emotion for r in shot.reactions}
    for cid in shot.character_ids:
        c = plan.character(cid)
        if c is None:
            continue
        base = repo.item_or_none(char_item_id(cid))
        bv = base.approved() if base else None
        emo = emotions.get(cid)
        rx = repo.item_or_none(reaction_item_id(cid, emo)) if emo and emo != "neutral" else None
        rv = rx.approved() if rx else None
        if bv and not (compact and rv):
            rs.add(ctx, f"{slugify(c.name)}.png", f"{c.name} - base reference (face, hair, clothes)",
                   base.id, bv.n, bv.image_key, "character")
        elif bv:
            rs.used[base.id] = bv.n
        if rv:
            label = f"{c.name} - '{emo}' expression" + (" (also the identity reference: same face, hair, clothes)"
                                                       if compact else "")
            rs.add(ctx, f"{slugify(c.name)}_{slugify(emo)}.png", label, rx.id, rv.n, rv.image_key, "character")
    if shot.location_id:
        loc = plan.location(shot.location_id)
        li = repo.item_or_none(loc_item_id(shot.location_id))
        lv = li.approved() if li else None
        if loc and lv:
            rs.add(ctx, f"{slugify(loc.name)}.png", f"location - {loc.name}", li.id, lv.n, lv.image_key, "location")
    return rs


def shot_data(plan: Plan, shot: Shot) -> dict:
    """Only the current shot's data (short prompts = lower cost)."""
    chars = []
    emotions = {r.character_id: r.emotion for r in shot.reactions}
    for cid in shot.character_ids:
        c = plan.character(cid)
        if c:
            chars.append({"name": c.name, "description": c.description, "emotion": emotions.get(cid, "")})
    loc = plan.location(shot.location_id) if shot.location_id else None
    return {
        "style": plan.style,
        "shot_id": shot.id,
        "framing": shot.framing,
        "action": shot.action,
        "dialogue": shot.dialogue,
        "time_of_day": shot.time_of_day,
        "props": shot.props,
        "characters": chars,
        "location": {"name": loc.name, "description": loc.description} if loc else None,
        "notes": shot.notes,
    }
