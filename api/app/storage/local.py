"""JSON-file storage used by the automated tests (no database needed). Not selectable at runtime:
the app always runs on PostgreSQL + Cloudflare R2 (cloud.py).

Every write goes through one path: take a cross-process file lock, read, apply,
write a temporary file, fsync, then atomically rename. Parallel jobs (threads or
separate worker processes) therefore never corrupt or overwrite each other.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from filelock import FileLock

from .base import GLOBAL, Storage, Updater

# Collection -> file name (spec §4: data/plan.json, data/library.json ...).
# Several collections may share a file; each file holds {collection: {id: record}}.
FILES = {
    "project": "project.json",
    "plan": "plan.json",
    "library": "library.json",
    "panels": "storyboards.json",
    "boards": "storyboards.json",
    "reviews": "reviews.json",
    "finals": "finals.json",
    "usage": "usage_log.json",
    "budget": "budget.json",
    "jobs": "jobs.json",
    "users": "users.json",
    "assets": "assets.json",
}


class LocalStorage(Storage):
    def __init__(self, data_dir: Path, images_dir: Path | None = None):
        self.data_dir = Path(data_dir)
        self.images_dir = Path(images_dir or self.data_dir / "images")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[Path, FileLock] = {}
        self._locks_guard = threading.Lock()
        self._cache: dict[Path, tuple[float, int, dict]] = {}
        self._cache_guard = threading.Lock()

    # ------------------------------------------------------------ internals
    def _path(self, project: str, collection: str) -> Path:
        fname = FILES.get(collection, f"{collection}.json")
        base = self.data_dir / "_global" if project == GLOBAL else self.data_dir / "projects" / _safe(project)
        return base / fname

    def _lock(self, path: Path) -> FileLock:
        with self._locks_guard:
            lk = self._locks.get(path)
            if lk is None:
                path.parent.mkdir(parents=True, exist_ok=True)
                lk = FileLock(str(path) + ".lock", timeout=60)
                self._locks[path] = lk
            return lk

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            st = path.stat()
        except FileNotFoundError:
            return {}
        with self._cache_guard:
            hit = self._cache.get(path)
            if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
                return json.loads(json.dumps(hit[2]))  # defensive copy
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object")
        with self._cache_guard:
            self._cache[path] = (st.st_mtime, st.st_size, data)
        return json.loads(json.dumps(data))

    def _write(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        with self._cache_guard:
            self._cache.pop(path, None)

    # ------------------------------------------------------------ records
    def get(self, project, collection, rid):
        rec = self._read(self._path(project, collection)).get(collection, {}).get(rid)
        return rec

    def list(self, project, collection):
        return list(self._read(self._path(project, collection)).get(collection, {}).values())

    def update(self, project, collection, rid, fn: Updater):
        path = self._path(project, collection)
        with self._lock(path):
            data = self._read(path)
            coll = data.setdefault(collection, {})
            cur = coll.get(rid)
            new = fn(json.loads(json.dumps(cur)) if cur is not None else None)
            if new is None:
                return cur
            new["_rev"] = (cur or {}).get("_rev", 0) + 1
            new["updated_at"] = time.time()
            coll[rid] = new
            self._write(path, data)
            return new

    def put_many(self, project, collection, records):
        path = self._path(project, collection)
        with self._lock(path):
            data = self._read(path)
            coll = data.setdefault(collection, {})
            for rid, rec in records.items():
                rec["_rev"] = coll.get(rid, {}).get("_rev", 0) + 1
                rec["updated_at"] = time.time()
                coll[rid] = rec
            self._write(path, data)

    def delete(self, project, collection, rid):
        path = self._path(project, collection)
        with self._lock(path):
            data = self._read(path)
            if data.get(collection, {}).pop(rid, None) is not None:
                self._write(path, data)

    def list_projects(self, owner_email=None):
        root = self.data_dir / "projects"
        if not root.exists():
            return []
        out = []
        for d in root.iterdir():
            if not d.is_dir():
                continue
            meta = self.get(d.name, "project", "meta")
            if meta is None:
                continue
            if owner_email is not None and meta.get("owner_email", "") != owner_email:
                continue
            out.append((meta.get("created_at", 0), d.name))
        return [pid for _, pid in sorted(out, reverse=True)]

    # ------------------------------------------------------------ users & ownership
    def upsert_user(self, email, name=""):
        def fn(cur):
            now = time.time()
            u = cur or {"email": email, "name": name, "created_at": now}
            if name:
                u["name"] = name
            u["last_login_at"] = now
            return u
        return self.update(GLOBAL, "users", email, fn)

    def get_user(self, email):
        return self.get(GLOBAL, "users", email)

    def register_project(self, project_id, owner_email, title):
        def fn(cur):
            if cur is None:
                return None
            cur["owner_email"], cur["title"] = owner_email, title or cur.get("title", "")
            return cur
        self.update(project_id, "project", "meta", fn)

    def project_owner(self, project_id):
        try:
            meta = self.get(project_id, "project", "meta")
        except ValueError:
            return None
        return None if meta is None else meta.get("owner_email", "")

    def claim_unowned_projects(self, owner_email):
        n = 0
        for pid in self.list_projects():
            if self.project_owner(pid) == "":
                self.update(pid, "project", "meta", lambda cur: {**cur, "owner_email": owner_email} if cur else None)
                n += 1
        return n

    # ------------------------------------------------------------ file catalogue
    def add_asset(self, project, asset):
        self.update(project, "assets", asset["storage_key"], lambda _cur: dict(asset))

    def list_assets(self, project):
        return sorted(self.list(project, "assets"), key=lambda a: a.get("created_at", 0))

    def delete_project(self, project):
        _safe(project)
        jobs_path = self._path(GLOBAL, "jobs")
        with self._lock(jobs_path):
            data = self._read(jobs_path)
            changed = False
            for j in data.get("jobs", {}).values():
                if j.get("project_id") == project and j["status"] == "queued":
                    j.update(status="cancelled", error="project deleted", updated_at=time.time())
                    changed = True
            if changed:
                self._write(jobs_path, data)
        for root in (self.data_dir / "projects" / project, self.images_dir / "projects" / project):
            shutil.rmtree(root, ignore_errors=True)
        with self._cache_guard:
            for path in [p for p in self._cache if str(p).startswith(str(self.data_dir / "projects" / project))]:
                self._cache.pop(path, None)

    # ------------------------------------------------------------ images
    def _img_path(self, key: str) -> Path:
        p = (self.images_dir / key).resolve()
        if not str(p).startswith(str(self.images_dir.resolve()) + os.sep):
            raise ValueError("invalid image key")
        return p

    def put_image(self, key, data, content_type="image/png"):
        p = self._img_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, p)
        return key

    def get_image(self, key):
        return self._img_path(key).read_bytes()

    def image_exists(self, key):
        return self._img_path(key).exists()

    def local_image_path(self, key: str) -> Path:
        return self._img_path(key)

    # ------------------------------------------------------------ jobs
    def enqueue_job(self, job):
        path = self._path(GLOBAL, "jobs")
        with self._lock(path):
            data = self._read(path)
            coll = data.setdefault("jobs", {})
            if job.get("dedupe_key"):
                for j in coll.values():
                    if j.get("dedupe_key") == job["dedupe_key"] and j["status"] in ("queued", "running"):
                        return j
            job["_rev"] = 1
            job["updated_at"] = time.time()
            coll[job["id"]] = job
            _prune_jobs(coll)
            self._write(path, data)
            return job

    def claim_job(self, worker_id, lease_seconds, project=None):
        path = self._path(GLOBAL, "jobs")
        with self._lock(path):
            data = self._read(path)
            coll = data.get("jobs", {})
            now = time.time()
            runnable = [
                j for j in coll.values()
                if (project is None or j["project_id"] == project)
                and (j["status"] == "queued" or (j["status"] == "running" and j.get("lease_until", 0) < now))
            ]
            if not runnable:
                return None
            job = min(runnable, key=lambda j: j["created_at"])
            job.update(status="running", worker_id=worker_id, lease_until=now + lease_seconds,
                       attempts=job.get("attempts", 0) + 1, updated_at=now, _rev=job.get("_rev", 0) + 1)
            self._write(path, data)
            return job

    def finish_job(self, job_id, status, error=""):
        def fn(cur):
            if cur is None:
                return None
            cur.update(status=status, error=error[:2000], lease_until=0)
            return cur
        self.update(GLOBAL, "jobs", job_id, fn)

    def list_jobs(self, project, active_only=True):
        jobs = [j for j in self.list(GLOBAL, "jobs") if j["project_id"] == project]
        if active_only:
            jobs = [j for j in jobs if j["status"] in ("queued", "running")]
        return sorted(jobs, key=lambda j: j["created_at"])


def _prune_jobs(coll: dict[str, dict], keep_finished: int = 2000) -> None:
    finished = sorted((j for j in coll.values() if j["status"] not in ("queued", "running")),
                      key=lambda j: j["updated_at"])
    for j in finished[:-keep_finished] if len(finished) > keep_finished else []:
        coll.pop(j["id"], None)


def _safe(name: str) -> str:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"invalid project id: {name!r}")
    return name
