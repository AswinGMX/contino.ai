"""Cloud mode: PostgreSQL (JSONB records + job queue) and private Cloudflare R2 for images.

- Records: one row per (project, collection, id); read-modify-write runs in a
  transaction with SELECT ... FOR UPDATE, so updates are atomic across processes.
- Jobs: claimed with FOR UPDATE SKIP LOCKED, so any number of worker processes
  can drain the queue concurrently without double-processing.
- Images: R2 via its S3-compatible API. The bucket stays private; the browser
  gets short-lived presigned links.
"""
from __future__ import annotations

import json
import time
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .base import Storage, Updater

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    project_id  TEXT NOT NULL,
    collection  TEXT NOT NULL,
    id          TEXT NOT NULL,
    data        JSONB NOT NULL,
    rev         INTEGER NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, collection, id)
);
CREATE INDEX IF NOT EXISTS records_project_collection ON records (project_id, collection);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL,
    dedupe_key  TEXT NOT NULL DEFAULT '',
    attempts    INTEGER NOT NULL DEFAULT 0,
    lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at  DOUBLE PRECISION NOT NULL,
    data        JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_runnable ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS jobs_project ON jobs (project_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_dedupe_active ON jobs (dedupe_key)
    WHERE dedupe_key <> '' AND status IN ('queued', 'running');
"""


class CloudStorage(Storage):
    def __init__(self, database_url: str, account_id: str, access_key: str, secret_key: str, bucket: str,
                 pool_min: int = 2, pool_max: int = 20):
        self.pool = ConnectionPool(database_url, min_size=pool_min, max_size=pool_max, open=True,
                                   kwargs={"autocommit": False})
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)
        self.bucket = bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"}, max_pool_connections=50),
        )

    # ------------------------------------------------------------ records
    def get(self, project, collection, rid):
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT data FROM records WHERE project_id=%s AND collection=%s AND id=%s",
                (project, collection, rid),
            ).fetchone()
            return row[0] if row else None

    def list(self, project, collection):
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT data FROM records WHERE project_id=%s AND collection=%s ORDER BY id",
                (project, collection),
            ).fetchall()
            return [r[0] for r in rows]

    def update(self, project, collection, rid, fn: Updater):
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT data FROM records WHERE project_id=%s AND collection=%s AND id=%s FOR UPDATE",
                (project, collection, rid),
            ).fetchone()
            if row is None:
                # Serialise concurrent creators of the same key.
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{project}/{collection}/{rid}",))
                row = conn.execute(
                    "SELECT data FROM records WHERE project_id=%s AND collection=%s AND id=%s FOR UPDATE",
                    (project, collection, rid),
                ).fetchone()
            cur = row[0] if row else None
            new = fn(json.loads(json.dumps(cur)) if cur is not None else None)
            if new is None:
                return cur
            new["_rev"] = (cur or {}).get("_rev", 0) + 1
            new["updated_at"] = time.time()
            conn.execute(
                """INSERT INTO records (project_id, collection, id, data, rev, updated_at)
                   VALUES (%s, %s, %s, %s, %s, now())
                   ON CONFLICT (project_id, collection, id)
                   DO UPDATE SET data=EXCLUDED.data, rev=EXCLUDED.rev, updated_at=now()""",
                (project, collection, rid, Jsonb(new), new["_rev"]),
            )
            return new

    def put_many(self, project, collection, records):
        with self.pool.connection() as conn, conn.transaction():
            with conn.cursor() as cur:
                for rid, rec in records.items():
                    rec["updated_at"] = time.time()
                    cur.execute(
                        """INSERT INTO records (project_id, collection, id, data, rev, updated_at)
                           VALUES (%s, %s, %s, %s, 1, now())
                           ON CONFLICT (project_id, collection, id)
                           DO UPDATE SET data = jsonb_set(EXCLUDED.data, '{_rev}', to_jsonb(records.rev + 1)),
                                         rev = records.rev + 1, updated_at = now()""",
                        (project, collection, rid, Jsonb({**rec, "_rev": 1})),
                    )

    def delete(self, project, collection, rid):
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM records WHERE project_id=%s AND collection=%s AND id=%s",
                         (project, collection, rid))

    def list_projects(self):
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT DISTINCT project_id FROM records WHERE collection='project'").fetchall()
            return sorted(r[0] for r in rows)

    def delete_project(self, project):
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM records WHERE project_id=%s", (project,))
            conn.execute("UPDATE jobs SET status='cancelled' WHERE project_id=%s AND status='queued'", (project,))
        prefix = f"projects/{project}/"
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kw)
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")

    # ------------------------------------------------------------ images
    def put_image(self, key, data, content_type="image/png"):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get_image(self, key):
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def image_exists(self, key):
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def image_url(self, key, expires=300):
        return self.s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key},
                                              ExpiresIn=expires)

    # ------------------------------------------------------------ jobs
    def enqueue_job(self, job):
        with self.pool.connection() as conn, conn.transaction():
            if job.get("dedupe_key"):
                row = conn.execute(
                    "SELECT data FROM jobs WHERE dedupe_key=%s AND status IN ('queued','running')",
                    (job["dedupe_key"],),
                ).fetchone()
                if row:
                    return row[0]
            job["_rev"] = 1
            job["updated_at"] = time.time()
            res = conn.execute(
                """INSERT INTO jobs (id, project_id, type, status, dedupe_key, attempts, lease_until, created_at, data)
                   VALUES (%s,%s,%s,%s,%s,0,0,%s,%s) ON CONFLICT DO NOTHING RETURNING data""",
                (job["id"], job["project_id"], job["type"], "queued", job.get("dedupe_key", ""),
                 job["created_at"], Jsonb(job)),
            ).fetchone()
            if res is None:  # lost a dedupe race
                row = conn.execute(
                    "SELECT data FROM jobs WHERE dedupe_key=%s AND status IN ('queued','running')",
                    (job["dedupe_key"],),
                ).fetchone()
                return row[0] if row else job
            return res[0]

    def claim_job(self, worker_id, lease_seconds):
        now = time.time()
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT id, data FROM jobs
                   WHERE status='queued' OR (status='running' AND lease_until < %s)
                   ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""",
                (now,),
            ).fetchone()
            if row is None:
                return None
            jid, data = row
            data.update(status="running", worker_id=worker_id, lease_until=now + lease_seconds,
                        attempts=data.get("attempts", 0) + 1, updated_at=now)
            conn.execute(
                "UPDATE jobs SET status='running', attempts=%s, lease_until=%s, data=%s WHERE id=%s",
                (data["attempts"], data["lease_until"], Jsonb(data), jid),
            )
            return data

    def finish_job(self, job_id, status, error=""):
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute("SELECT data FROM jobs WHERE id=%s FOR UPDATE", (job_id,)).fetchone()
            if not row:
                return
            data = row[0]
            data.update(status=status, error=error[:2000], lease_until=0, updated_at=time.time())
            conn.execute("UPDATE jobs SET status=%s, lease_until=0, data=%s WHERE id=%s",
                         (status, Jsonb(data), job_id))

    def list_jobs(self, project, active_only=True):
        q = "SELECT data FROM jobs WHERE project_id=%s"
        if active_only:
            q += " AND status IN ('queued','running')"
        else:
            q += " AND created_at > %s"
        q += " ORDER BY created_at"
        args: tuple = (project,) if active_only else (project, time.time() - 86400)
        with self.pool.connection() as conn:
            return [r[0] for r in conn.execute(q, args).fetchall()]

    # ------------------------------------------------------------ lifecycle
    def healthcheck(self):
        with self.pool.connection() as conn:
            conn.execute("SELECT 1")
        self.s3.head_bucket(Bucket=self.bucket)

    def close(self):
        self.pool.close()
