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
CREATE TABLE IF NOT EXISTS users (
    email          TEXT PRIMARY KEY,                 -- the user id
    name           TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS projects (
    id           TEXT PRIMARY KEY,
    owner_email  TEXT REFERENCES users(email) ON DELETE CASCADE,   -- NULL only for projects made before users
    title        TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS projects_owner ON projects (owner_email, created_at DESC);

-- Every stored file: input PDF, character/expression/place pictures, sketch versions, boards, finals, exports.
-- The bytes live in Cloudflare R2 under storage_key; this table is the catalogue.
CREATE TABLE IF NOT EXISTS assets (
    id            BIGSERIAL PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,       -- input | character | expression | place | sheet | panel | panel_captioned | board | final | export
    item_id       TEXT NOT NULL DEFAULT '',   -- e.g. char:theo, S01_SH02
    version       INTEGER,
    storage_key   TEXT NOT NULL UNIQUE,
    content_type  TEXT NOT NULL DEFAULT 'image/png',
    bytes         BIGINT NOT NULL DEFAULT 0,
    sha256        TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'generated',   -- generated | uploaded | system
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS assets_project ON assets (project_id, kind, item_id, version);

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
        # autocommit: a plain read is ONE network round trip (no BEGIN/COMMIT around it). Every write
        # still runs inside an explicit `conn.transaction()`, so atomicity and row locks are unchanged.
        self.pool = ConnectionPool(database_url, min_size=pool_min, max_size=pool_max, open=True,
                                   kwargs={"autocommit": True, "prepare_threshold": None})
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

    def list_projects(self, owner_email=None):
        with self.pool.connection() as conn:
            if owner_email is None:
                rows = conn.execute("SELECT id FROM projects ORDER BY created_at DESC").fetchall()
            else:
                rows = conn.execute("SELECT id FROM projects WHERE owner_email=%s ORDER BY created_at DESC",
                                    (owner_email,)).fetchall()
            return [r[0] for r in rows]

    # ------------------------------------------------------------ users & ownership
    def upsert_user(self, email, name=""):
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO users (email, name) VALUES (%s, %s)
                   ON CONFLICT (email) DO UPDATE
                     SET last_login_at = now(), name = CASE WHEN EXCLUDED.name <> '' THEN EXCLUDED.name ELSE users.name END
                   RETURNING email, name, extract(epoch FROM created_at), extract(epoch FROM last_login_at)""",
                (email, name),
            ).fetchone()
            return {"email": row[0], "name": row[1], "created_at": float(row[2]), "last_login_at": float(row[3])}

    def get_user(self, email):
        with self.pool.connection() as conn:
            row = conn.execute("SELECT email, name FROM users WHERE email=%s", (email,)).fetchone()
            return {"email": row[0], "name": row[1]} if row else None

    def register_project(self, project_id, owner_email, title):
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO projects (id, owner_email, title) VALUES (%s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET owner_email = EXCLUDED.owner_email,
                       title = CASE WHEN EXCLUDED.title <> '' THEN EXCLUDED.title ELSE projects.title END,
                       updated_at = now()""",
                (project_id, owner_email or None, title or ""),
            )

    def project_owner(self, project_id):
        with self.pool.connection() as conn:
            row = conn.execute("SELECT owner_email FROM projects WHERE id=%s", (project_id,)).fetchone()
            if row is None:
                return None
            return row[0] or ""

    def claim_unowned_projects(self, owner_email):
        with self.pool.connection() as conn, conn.transaction():
            # projects stored before the projects table existed
            ins = conn.execute(
                """INSERT INTO projects (id, owner_email, title)
                   SELECT project_id, %s, coalesce(data->>'title', '') FROM records
                   WHERE collection = 'project' AND id = 'meta'
                     AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.id = records.project_id)
                   ON CONFLICT DO NOTHING""",
                (owner_email,),
            )
            upd = conn.execute("UPDATE projects SET owner_email=%s WHERE owner_email IS NULL", (owner_email,))
            return (ins.rowcount or 0) + (upd.rowcount or 0)

    # ------------------------------------------------------------ file catalogue
    def add_asset(self, project, asset):
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO assets (project_id, kind, item_id, version, storage_key, content_type, bytes, sha256, source)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (storage_key) DO UPDATE SET bytes=EXCLUDED.bytes, sha256=EXCLUDED.sha256""",
                (project, asset["kind"], asset.get("item_id", ""), asset.get("version"), asset["storage_key"],
                 asset.get("content_type", "image/png"), asset.get("bytes", 0), asset.get("sha256", ""),
                 asset.get("source", "generated")),
            )

    def list_assets(self, project):
        with self.pool.connection() as conn:
            rows = conn.execute(
                """SELECT kind, item_id, version, storage_key, content_type, bytes, sha256, source,
                          extract(epoch FROM created_at) FROM assets WHERE project_id=%s ORDER BY id""", (project,),
            ).fetchall()
            keys = ("kind", "item_id", "version", "storage_key", "content_type", "bytes", "sha256", "source", "created_at")
            return [dict(zip(keys, r)) for r in rows]

    def delete_project(self, project):
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM records WHERE project_id=%s", (project,))
            conn.execute("DELETE FROM projects WHERE id=%s", (project,))   # assets rows cascade
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
