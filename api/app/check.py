"""Check that every setting is present and every service is reachable.

    cd api && source .venv/bin/activate && python -m app.check

Checks: required settings in .env, PostgreSQL (connect + create tables), Cloudflare R2
(write, read back and delete a small test file), and that the OpenAI key can use the
configured models. Secrets are never printed. Exit code 0 = everything OK.
"""
from __future__ import annotations

import sys
import time
import uuid

from .config import ConfigError, get_config, get_settings

OK, BAD, WARN = "✓", "✗", "!"


def line(mark: str, title: str, detail: str = "") -> None:
    print(f"  {mark} {title}" + (f"  ({detail})" if detail else ""))


def masked(value: str) -> str:
    v = value.strip()
    return "set" if not v else f"set, {len(v)} characters"


def main() -> int:
    s = get_settings()
    failures = 0

    print("\n1. Settings in .env")
    labels = {
        "OPENAI_API_KEY": "OpenAI key", "SESSION_SECRET": "sign-in cookie secret", "DATABASE_URL": "PostgreSQL",
        "R2_ACCOUNT_ID": "R2 account", "R2_ACCESS_KEY_ID": "R2 access key", "R2_SECRET_ACCESS_KEY": "R2 secret key",
        "R2_BUCKET": "R2 bucket",
    }
    missing = s.missing_settings()
    for name, label in labels.items():
        if name in missing:
            line(BAD, f"{name} is empty", label)
            failures += 1
        else:
            line(OK, name, masked(str(getattr(s, name.lower()))))
    try:
        cfg = get_config()
        line(OK, "config.yaml", f"models: image={cfg.models.image}, text={cfg.models.text}, review={cfg.models.vision}")
    except ConfigError as exc:
        line(BAD, "config.yaml", str(exc))
        return 1
    if missing:
        print(f"\n{BAD} Fill in the empty settings above, then run this again.\n")
        return 1

    print("\n2. PostgreSQL")
    storage = None
    try:
        import psycopg
        t0 = time.monotonic()
        with psycopg.connect(s.database_url, connect_timeout=10) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
        line(OK, "connected", f"PostgreSQL {version}, {round((time.monotonic() - t0) * 1000)} ms")
        from .storage.cloud import CloudStorage
        storage = CloudStorage(s.database_url, s.r2_account_id, s.r2_access_key_id, s.r2_secret_access_key,
                               s.r2_bucket, pool_min=0, pool_max=2)
        with storage.pool.connection() as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY 1").fetchall()]
            counts = {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                      for t in ("users", "projects", "assets") if t in tables}
        need = {"users", "projects", "assets", "records", "jobs"}
        if need <= set(tables):
            line(OK, "tables ready", ", ".join(sorted(need)) + f"  |  {counts['users']} users, "
                 f"{counts['projects']} projects, {counts['assets']} files")
        else:
            line(BAD, "tables missing", ", ".join(sorted(need - set(tables))))
            failures += 1
    except Exception as exc:  # noqa: BLE001
        line(BAD, "cannot use the database", f"{type(exc).__name__}: {str(exc)[:200]}")
        failures += 1

    print("\n3. Cloudflare R2")
    try:
        import boto3
        from botocore.config import Config as BotoConfig
        s3 = boto3.client("s3", endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
                          aws_access_key_id=s.r2_access_key_id, aws_secret_access_key=s.r2_secret_access_key,
                          region_name="auto", config=BotoConfig(retries={"max_attempts": 2}, connect_timeout=10))
        s3.head_bucket(Bucket=s.r2_bucket)
        line(OK, f"bucket '{s.r2_bucket}' reachable")
        key = f"_healthcheck/{uuid.uuid4().hex}.txt"
        body = b"contino.ai storage check"
        s3.put_object(Bucket=s.r2_bucket, Key=key, Body=body, ContentType="text/plain")
        got = s3.get_object(Bucket=s.r2_bucket, Key=key)["Body"].read()
        s3.delete_object(Bucket=s.r2_bucket, Key=key)
        if got == body:
            line(OK, "write, read and delete a test file")
        else:
            line(BAD, "read back a different file")
            failures += 1
        url = s3.generate_presigned_url("get_object", Params={"Bucket": s.r2_bucket, "Key": "x"}, ExpiresIn=60)
        line(OK, "short-lived image links", url.split("?")[0].rsplit("/", 1)[0] + "/…")
    except Exception as exc:  # noqa: BLE001
        line(BAD, "cannot use the R2 bucket", f"{type(exc).__name__}: {str(exc)[:200]}")
        failures += 1

    print("\n4. OpenAI")
    try:
        from openai import OpenAI
        available = {m.id for m in OpenAI(api_key=s.openai_api_key, timeout=20).models.list()}
        for role, model in (("image", cfg.models.image), ("text", cfg.models.text), ("review", cfg.models.vision)):
            if model in available:
                line(OK, f"{role} model {model}")
            else:
                line(BAD, f"{role} model {model} is not available to this key",
                     "available: " + ", ".join(sorted(available)[:8]))
                failures += 1
    except Exception as exc:  # noqa: BLE001
        line(BAD, "cannot reach OpenAI with this key", f"{type(exc).__name__}: {str(exc)[:200]}")
        failures += 1

    if storage is not None:
        storage.close()
    print(f"\n{OK + ' Everything is ready.' if not failures else BAD + f' {failures} problem(s) found (see above).'}\n")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
