"""Durable job queue + worker pool.

Jobs are persisted through the storage interface, so they survive restarts.
- local mode: claimed under a file lock (one or more processes on one host)
- cloud mode: claimed with FOR UPDATE SKIP LOCKED (any number of worker hosts)

A job whose worker dies is re-claimed after its lease expires. A job is never
claimed more than MAX_JOB_ATTEMPTS times. Model-call retries happen inside the
LLM gateway, not here, so a failed job is never silently re-run (no paid
regeneration without a person's click).
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from typing import Any, Callable

from ..domain.models import Job
from ..logging_setup import correlation_id, log
from ..storage.base import Storage

logger = logging.getLogger(__name__)
MAX_JOB_ATTEMPTS = 3

Handler = Callable[[Job], None]
_HANDLERS: dict[str, Handler] = {}
_FAIL_HOOKS: dict[str, Callable[[Job, str], None]] = {}


def handler(job_type: str, on_fail: Callable[[Job, str], None] | None = None):
    def deco(fn: Handler) -> Handler:
        _HANDLERS[job_type] = fn
        if on_fail:
            _FAIL_HOOKS[job_type] = on_fail
        return fn
    return deco


def enqueue(storage: Storage, project_id: str, job_type: str, payload: dict[str, Any],
            dedupe_key: str = "") -> Job:
    job = Job(project_id=project_id, type=job_type, payload=payload, dedupe_key=dedupe_key)
    return Job.model_validate(storage.enqueue_job(job.dump()))


class Worker:
    def __init__(self, storage: Storage, threads: int, poll_interval: float, lease_seconds: int):
        self.s = storage
        self.threads = threads
        self.poll = poll_interval
        self.lease = lease_seconds
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._stop = threading.Event()
        self._pool: list[threading.Thread] = []

    def start(self) -> None:
        from . import handlers  # noqa: F401  (registers handlers)
        for i in range(self.threads):
            t = threading.Thread(target=self._loop, name=f"job-worker-{i}", daemon=True)
            t.start()
            self._pool.append(t)
        log(logger, logging.INFO, "worker started", worker_id=self.worker_id, threads=self.threads)

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        for t in self._pool:
            t.join(timeout=timeout / max(1, len(self._pool)))

    def _loop(self) -> None:
        idle = self.poll
        while not self._stop.is_set():
            try:
                raw = self.s.claim_job(self.worker_id, self.lease)
            except Exception:  # noqa: BLE001
                logger.exception("claim failed")
                self._stop.wait(2)
                continue
            if raw is None:
                self._stop.wait(idle)
                idle = min(idle * 1.5, 3.0)  # back off while idle
                continue
            idle = self.poll
            self._run(Job.model_validate(raw))

    def _run(self, job: Job) -> None:
        token = correlation_id.set(job.id)
        t0 = time.monotonic()
        try:
            fn = _HANDLERS.get(job.type)
            if fn is None:
                raise RuntimeError(f"no handler for job type {job.type}")
            if job.attempts > MAX_JOB_ATTEMPTS:
                raise RuntimeError("job abandoned too many times; handing to a person")
            fn(job)
            self.s.finish_job(job.id, "succeeded")
            log(logger, logging.INFO, "job done", type=job.type, seconds=round(time.monotonic() - t0, 2))
        except Exception as exc:  # noqa: BLE001
            msg = str(exc) or type(exc).__name__
            log(logger, logging.WARNING, "job failed", type=job.type, error=msg[:500])
            hook = _FAIL_HOOKS.get(job.type)
            if hook:
                try:
                    hook(job, msg)
                except Exception:  # noqa: BLE001
                    logger.exception("fail hook crashed")
            self.s.finish_job(job.id, "failed", msg)
        finally:
            correlation_id.reset(token)
