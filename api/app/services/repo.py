"""Typed, project-scoped access to storage. Validates every record on load and save."""
from __future__ import annotations

from typing import Callable, TypeVar

from pydantic import BaseModel

from ..domain.models import Board, Final, LibraryItem, Panel, PlanRecord, Project, Review, UsageRecord
from ..storage.base import Storage

M = TypeVar("M", bound=BaseModel)


class NotFound(KeyError):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what

    def __str__(self) -> str:
        return f"{self.what} not found"


class Repo:
    def __init__(self, storage: Storage, project_id: str):
        self.s = storage
        self.pid = project_id

    # -------------------------------------------------------------- generic
    def _get(self, model: type[M], coll: str, rid: str) -> M | None:
        raw = self.s.get(self.pid, coll, rid)
        return model.model_validate(raw) if raw is not None else None

    def _must(self, model: type[M], coll: str, rid: str) -> M:
        obj = self._get(model, coll, rid)
        if obj is None:
            raise NotFound(f"{coll}/{rid}")
        return obj

    def _list(self, model: type[M], coll: str) -> list[M]:
        return [model.model_validate(r) for r in self.s.list(self.pid, coll)]

    def _mutate(self, model: type[M], coll: str, rid: str, fn: Callable[[M | None], M | None]) -> M | None:
        def raw_fn(cur):
            obj = model.model_validate(cur) if cur is not None else None
            out = fn(obj)
            return out.dump() if out is not None else None  # type: ignore[attr-defined]
        raw = self.s.update(self.pid, coll, rid, raw_fn)
        return model.model_validate(raw) if raw is not None else None

    def _save(self, obj) -> None:
        coll = _COLL[type(obj)]
        self.s.update(self.pid, coll, obj.id if hasattr(obj, "id") else "current", lambda _cur: obj.dump())

    # -------------------------------------------------------------- project / plan
    def project(self) -> Project:
        return self._must(Project, "project", "meta")

    def mutate_project(self, fn: Callable[[Project | None], Project | None]) -> Project | None:
        return self._mutate(Project, "project", "meta", fn)

    def plan_record(self) -> PlanRecord | None:
        pr = self._get(PlanRecord, "plan", "current")
        if pr is not None:
            for c in pr.plan.characters:
                if not c.kind:
                    from ..domain.models import infer_kind
                    c.kind = infer_kind(c.name, c.description)
        return pr

    def must_plan(self) -> PlanRecord:
        pr = self.plan_record()
        if pr is None:
            raise NotFound("plan")
        return pr

    def mutate_plan(self, fn: Callable[[PlanRecord | None], PlanRecord | None]) -> PlanRecord | None:
        return self._mutate(PlanRecord, "plan", "current", fn)

    # -------------------------------------------------------------- library
    def library(self) -> list[LibraryItem]:
        return self._list(LibraryItem, "library")

    def item(self, iid: str) -> LibraryItem:
        return self._must(LibraryItem, "library", iid)

    def item_or_none(self, iid: str) -> LibraryItem | None:
        return self._get(LibraryItem, "library", iid)

    def mutate_item(self, iid: str, fn: Callable[[LibraryItem | None], LibraryItem | None]) -> LibraryItem | None:
        return self._mutate(LibraryItem, "library", iid, fn)

    # -------------------------------------------------------------- storyboard
    def panels(self) -> list[Panel]:
        return self._list(Panel, "panels")

    def panel(self, shot_id: str) -> Panel | None:
        return self._get(Panel, "panels", shot_id)

    def mutate_panel(self, shot_id: str, fn: Callable[[Panel | None], Panel | None]) -> Panel | None:
        return self._mutate(Panel, "panels", shot_id, fn)

    def boards(self) -> list[Board]:
        return self._list(Board, "boards")

    def board(self, scene_id: str) -> Board | None:
        return self._get(Board, "boards", scene_id)

    def mutate_board(self, scene_id: str, fn: Callable[[Board | None], Board | None]) -> Board | None:
        return self._mutate(Board, "boards", scene_id, fn)

    def reviews(self) -> list[Review]:
        return self._list(Review, "reviews")

    def review(self, shot_id: str, version: int) -> Review | None:
        return self._get(Review, "reviews", f"{shot_id}:v{version}")

    def save_review(self, review: Review) -> None:
        self.s.update(self.pid, "reviews", review.id, lambda _c: review.dump())

    # -------------------------------------------------------------- finals
    def finals(self) -> list[Final]:
        return self._list(Final, "finals")

    def final(self, shot_id: str) -> Final | None:
        return self._get(Final, "finals", shot_id)

    def mutate_final(self, shot_id: str, fn: Callable[[Final | None], Final | None]) -> Final | None:
        return self._mutate(Final, "finals", shot_id, fn)

    # -------------------------------------------------------------- usage
    def usage(self) -> list[UsageRecord]:
        return sorted(self._list(UsageRecord, "usage"), key=lambda u: u.time)

    # -------------------------------------------------------------- images
    def image_key(self, *parts: str) -> str:
        return "/".join(("projects", self.pid, *parts))


_COLL = {Project: "project", PlanRecord: "plan", LibraryItem: "library", Panel: "panels", Board: "boards",
         Review: "reviews", Final: "finals", UsageRecord: "usage"}
