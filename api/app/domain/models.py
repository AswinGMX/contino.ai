"""Domain models. Every persisted record is validated through these on load and save."""
from __future__ import annotations

import re
import time
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now() -> float:
    return time.time()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "item"


class Status(StrEnum):
    pending = "pending"
    generating = "generating"
    in_review = "in_review"
    needs_approval = "needs_approval"
    approved = "approved"
    rejected = "rejected"
    stale = "stale"
    failed = "failed"  # retries exhausted; handed to a person


class Record(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True, populate_by_name=True)
    rev: int = Field(default=0, alias="_rev")
    updated_at: float = Field(default_factory=now)

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


# ---------------------------------------------------------------- plan

class ShotReaction(BaseModel):
    character_id: str
    emotion: str

    @field_validator("emotion")
    @classmethod
    def _norm(cls, v: str) -> str:
        return v.strip().lower()


class Shot(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    location_id: str = ""
    character_ids: list[str] = Field(default_factory=list)
    action: str = ""
    dialogue: str = ""
    reactions: list[ShotReaction] = Field(default_factory=list)
    framing: str = ""
    time_of_day: str = ""
    props: list[str] = Field(default_factory=list)
    notes: str = ""


class Scene(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str = ""
    shots: list[Shot] = Field(default_factory=list)


CharacterKind = Literal["person", "animal", "creature", "group", "effect"]
_GROUP_WORDS = ("hundreds", "dozens", "crowd", "flock", "swarm", "herd", "group of", "many", "villagers", "pack of")
_EFFECT_WORDS = ("points of light", "glow", "sparkle", "mist", "fog", "light that", "lights", "magic")


_BEING_WORDS = ("insect", "animal", "creature", "bird", "wings", "fox", "owl", "dog", "cat", "wolf", "sheep",
                "boy", "girl", "man", "woman", "child", "eyes", "legs")


def _has(text: str, words: tuple[str, ...]) -> bool:
    """Whole-word / whole-phrase match (so "shepherd" doesn't match "herd")."""
    return any(re.search(rf"\b{re.escape(w.strip())}s?\b", text) for w in words)


def infer_kind(name: str, description: str) -> str:
    text = f"{name} {description}".lower()
    if _has(text, _GROUP_WORDS):
        return "group"
    if _has(text, _EFFECT_WORDS) and not _has(text, _BEING_WORDS):
        return "effect"
    if _has(text, ("boy", "girl", "man", "woman", "child", "elder", "person", "year-old", "villager")):
        return "person"
    return "creature"


class Character(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    description: str = ""
    reactions: list[str] = Field(default_factory=list)
    kind: str = ""   # person | animal | creature | group | effect ("" = infer from the description)


class Location(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    description: str = ""


class Plan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = ""
    style: str = ""
    characters: list[Character] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)

    def shot(self, shot_id: str) -> tuple[Scene, Shot]:
        for sc in self.scenes:
            for sh in sc.shots:
                if sh.id == shot_id:
                    return sc, sh
        raise KeyError(shot_id)

    def scene(self, scene_id: str) -> Scene:
        for sc in self.scenes:
            if sc.id == scene_id:
                return sc
        raise KeyError(scene_id)

    def character(self, cid: str) -> Character | None:
        return next((c for c in self.characters if c.id == cid), None)

    def location(self, lid: str) -> Location | None:
        return next((l for l in self.locations if l.id == lid), None)

    def normalise(self) -> "Plan":
        """Derive each character's reactions from the shots, dedupe, drop dangling ids."""
        char_ids = {c.id for c in self.characters}
        loc_ids = {l.id for l in self.locations}
        needed: dict[str, set[str]] = {c.id: set(c.reactions) for c in self.characters}
        for sc in self.scenes:
            for sh in sc.shots:
                sh.character_ids = [c for c in dict.fromkeys(sh.character_ids) if c in char_ids]
                sh.reactions = [r for r in sh.reactions if r.character_id in char_ids]
                if sh.location_id not in loc_ids:
                    sh.location_id = ""
                for r in sh.reactions:
                    if r.character_id not in sh.character_ids:
                        sh.character_ids.append(r.character_id)
                    needed[r.character_id].add(r.emotion)
        for c in self.characters:
            c.reactions = sorted(e for e in needed[c.id] if e and e != "neutral")
            if c.kind not in ("person", "animal", "creature", "group", "effect"):
                c.kind = infer_kind(c.name, c.description)
            if c.kind == "effect":
                c.reactions = []   # lights or weather have no faces, so no expression pictures
        return self


class PlanRecord(Record):
    plan: Plan
    status: Status = Status.needs_approval  # creator checks the parsed list
    source_key: str = ""
    approved_at: float | None = None


# ---------------------------------------------------------------- project

class Project(Record):
    id: str
    title: str = ""
    created_at: float = Field(default_factory=now)
    source_filename: str = ""
    parse_status: Literal["pending", "parsing", "done", "failed"] = "pending"
    parse_error: str = ""


# ---------------------------------------------------------------- library

LibraryKind = Literal["character", "reaction", "location"]


class ImageVersion(BaseModel):
    n: int
    image_key: str
    source: Literal["generated", "uploaded", "sheet"] = "generated"
    comment: str = ""
    quality: str = ""
    refs_used: dict[str, int] = Field(default_factory=dict)  # library item id -> approved version
    created_at: float = Field(default_factory=now)


IssueCategory = Literal["anatomy", "object", "character", "place", "lighting", "composition", "other"]


class Issue(BaseModel):
    problem: str
    fix: str = ""
    severity: Literal["major", "minor"] = "minor"
    category: IssueCategory = "other"


class ImageCheck(BaseModel):
    """AI quality check of a library picture (anatomy, artifacts, match to description)."""
    version: int
    score: int                      # 0-100
    issues: list[Issue] = Field(default_factory=list)
    suggested_fix: str = ""
    notes: str = ""
    model: str = ""
    error: str = ""
    basis: str = "description"   # "base" = an expression judged only against its approved main picture


class LibraryItem(Record):
    id: str                       # char:<cid> | react:<cid>:<emotion> | loc:<lid>
    kind: LibraryKind
    subject_id: str               # character or location id
    name: str
    emotion: str = ""
    status: Status = Status.pending
    comment: str = ""             # rejection comment waiting to be applied
    versions: list[ImageVersion] = Field(default_factory=list)
    approved_version: int | None = None
    error: str = ""
    check: ImageCheck | None = None   # AI check of the current version
    checking: bool = False

    @property
    def current(self) -> ImageVersion | None:
        return self.versions[-1] if self.versions else None

    def approved(self) -> ImageVersion | None:
        return next((v for v in self.versions if v.n == self.approved_version), None)


def char_item_id(cid: str) -> str:
    return f"char:{cid}"


def reaction_item_id(cid: str, emotion: str) -> str:
    return f"react:{cid}:{slugify(emotion)}"


def loc_item_id(lid: str) -> str:
    return f"loc:{lid}"


# ---------------------------------------------------------------- storyboard

class PanelVersion(ImageVersion):
    captioned_key: str = ""
    base_version: int | None = None   # the version a fix was drawn from
    restored_from: int | None = None  # a free copy of an earlier version


class Panel(Record):
    id: str                       # shot id
    scene_id: str
    status: Status = Status.pending
    versions: list[PanelVersion] = Field(default_factory=list)
    comment: str = ""
    error: str = ""
    stale_reason: str = ""

    @property
    def current(self) -> PanelVersion | None:
        return self.versions[-1] if self.versions else None


class Board(Record):
    id: str                       # scene id
    status: Status = Status.pending
    version: int = 0
    image_key: str = ""
    panel_versions: dict[str, int] = Field(default_factory=dict)
    approved_at: float | None = None
    overrides: list[str] = Field(default_factory=list)  # shot ids approved despite a failed score


class Review(Record):
    id: str                       # <shot_id>:v<n>
    shot_id: str
    panel_version: int
    scores: dict[str, float]
    overall: float
    verdict: Literal["pass", "fail"]
    notes: str = ""
    suggested_fix: str = ""
    issues: list["Issue"] = Field(default_factory=list)
    neighbour_versions: dict[str, int] = Field(default_factory=dict)
    model: str = ""


class Final(Record):
    id: str                       # shot id
    scene_id: str
    status: Status = Status.pending
    versions: list[ImageVersion] = Field(default_factory=list)
    approved_version: int | None = None
    comment: str = ""
    error: str = ""
    stale_reason: str = ""
    panel_version: int | None = None

    @property
    def current(self) -> ImageVersion | None:
        return self.versions[-1] if self.versions else None


# ---------------------------------------------------------------- usage & jobs

class UsageRecord(Record):
    id: str = Field(default_factory=lambda: new_id("u_"))
    time: float = Field(default_factory=now)
    project_id: str = ""
    shot_id: str
    step: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    input_text_tokens: int = 0
    input_image_tokens: int = 0
    cached_tokens: int = 0
    output_image_tokens: int = 0
    output_text_tokens: int = 0
    images: int = 0
    gpu_seconds: float = 0.0
    cost_usd: float = 0.0
    ok: bool = True
    attempt: int = 1
    error: str = ""
    job_id: str = ""


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class Job(Record):
    id: str = Field(default_factory=lambda: new_id("j_"))
    project_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = "queued"
    attempts: int = 0
    dedupe_key: str = ""
    lease_until: float = 0.0
    worker_id: str = ""
    error: str = ""
    created_at: float = Field(default_factory=now)
