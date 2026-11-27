"""Dedicated worker process:  python -m app.worker

Run as many as you need; in cloud mode they share the Postgres job queue
(FOR UPDATE SKIP LOCKED), in local mode they share the file-locked queue.
"""
from __future__ import annotations

import signal
import threading

from .main import _startup_checks
from .jobs.queue import Worker
from .services.context import get_ctx


def main() -> None:
    _startup_checks()
    ctx = get_ctx()
    ctx.storage.healthcheck()
    worker = Worker(ctx.storage, ctx.cfg.jobs.workers, ctx.cfg.jobs.poll_interval_seconds, ctx.cfg.jobs.lease_seconds)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    worker.start()
    stop.wait()
    worker.stop()
    ctx.storage.close()


if __name__ == "__main__":
    main()
