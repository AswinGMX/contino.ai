"""If an approved reference changes, every reaction, panel and final that used it is marked stale."""
from __future__ import annotations

from ..domain.models import Board, Final, LibraryItem, Panel, Status
from .repo import Repo

_SKIP = (Status.generating, Status.pending)


def mark_dependents_stale(repo: Repo, item_id: str, new_version: int | None, reason: str) -> dict[str, int]:
    counts = {"library": 0, "panels": 0, "finals": 0, "boards": 0}

    for it in repo.library():
        v = it.current
        if v and item_id in v.refs_used and v.refs_used[item_id] != new_version and it.status not in _SKIP:
            def fn(cur: LibraryItem | None) -> LibraryItem | None:
                return cur.model_copy(update={"status": Status.stale, "error": reason}) if cur else None
            repo.mutate_item(it.id, fn)
            counts["library"] += 1

    stale_scenes: set[str] = set()
    for p in repo.panels():
        v = p.current
        if v and item_id in v.refs_used and v.refs_used[item_id] != new_version and p.status not in _SKIP:
            def fp(cur: Panel | None) -> Panel | None:
                return cur.model_copy(update={"status": Status.stale, "stale_reason": reason}) if cur else None
            repo.mutate_panel(p.id, fp)
            stale_scenes.add(p.scene_id)
            counts["panels"] += 1

    for sid in stale_scenes:
        def fb(cur: Board | None) -> Board | None:
            return cur.model_copy(update={"status": Status.stale, "approved_at": None}) if cur else None
        repo.mutate_board(sid, fb)
        counts["boards"] += 1

    for f in repo.finals():
        v = f.current
        if v and item_id in v.refs_used and v.refs_used[item_id] != new_version and f.status not in _SKIP:
            def ff(cur: Final | None) -> Final | None:
                return cur.model_copy(update={"status": Status.stale, "stale_reason": reason}) if cur else None
            repo.mutate_final(f.id, ff)
            counts["finals"] += 1
    return counts
