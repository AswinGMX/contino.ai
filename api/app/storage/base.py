"""The single storage interface. No other code knows which mode is active.

Records are JSON dicts addressed by (project, collection, id) and carry a `_rev`
counter for optimistic concurrency. `update()` is the atomic read-modify-write
primitive every service uses, so parallel workers never lose writes.

Images are bytes addressed by a key such as
`projects/<pid>/library/characters/theo_v2.png` - identical in both modes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

GLOBAL = "_global"  # pseudo-project for cross-project records (budget, job queue)

Updater = Callable[[dict[str, Any] | None], dict[str, Any] | None]


class ConflictError(RuntimeError):
    """Expected revision did not match the stored record."""


class NotFoundError(KeyError):
    pass


class Storage(ABC):
    # ------------------------------------------------------------ records
    @abstractmethod
    def get(self, project: str, collection: str, rid: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list(self, project: str, collection: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def update(self, project: str, collection: str, rid: str, fn: Updater) -> dict[str, Any] | None:
        """Atomically apply fn(current) and store the result.

        fn receives the current record (or None) and returns the new record, or
        None to leave the record unchanged. `_rev` is incremented on every write.
        """

    @abstractmethod
    def put_many(self, project: str, collection: str, records: dict[str, dict[str, Any]]) -> None:
        """Bulk upsert in a single atomic write."""

    @abstractmethod
    def delete(self, project: str, collection: str, rid: str) -> None: ...

    @abstractmethod
    def list_projects(self) -> list[str]: ...

    @abstractmethod
    def delete_project(self, project: str) -> None:
        """Remove every record and image of a project and cancel its queued jobs."""

    def put(self, project: str, collection: str, rid: str, record: dict[str, Any],
            expected_rev: int | None = None) -> dict[str, Any]:
        def fn(cur: dict[str, Any] | None) -> dict[str, Any]:
            cur_rev = (cur or {}).get("_rev", 0)
            if expected_rev is not None and cur_rev != expected_rev:
                raise ConflictError(f"{collection}/{rid}: expected rev {expected_rev}, found {cur_rev}")
            return record
        out = self.update(project, collection, rid, fn)
        assert out is not None
        return out

    # ------------------------------------------------------------ images
    @abstractmethod
    def put_image(self, key: str, data: bytes, content_type: str = "image/png") -> str: ...

    @abstractmethod
    def get_image(self, key: str) -> bytes: ...

    @abstractmethod
    def image_exists(self, key: str) -> bool: ...

    def image_url(self, key: str, expires: int = 300) -> str | None:
        """A short-lived direct URL, or None when the API must stream the bytes."""
        return None

    # ------------------------------------------------------------ jobs
    @abstractmethod
    def enqueue_job(self, job: dict[str, Any]) -> dict[str, Any]:
        """Insert a job; if a queued/running job with the same dedupe_key exists, return it instead."""

    @abstractmethod
    def claim_job(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        """Atomically claim the oldest runnable job (queued, or running with an expired lease)."""

    @abstractmethod
    def finish_job(self, job_id: str, status: str, error: str = "") -> None: ...

    @abstractmethod
    def list_jobs(self, project: str, active_only: bool = True) -> list[dict[str, Any]]: ...

    # ------------------------------------------------------------ lifecycle
    def healthcheck(self) -> None:
        """Raise if the backend is unreachable."""

    def close(self) -> None:
        pass
