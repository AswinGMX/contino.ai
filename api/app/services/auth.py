"""Sign-in by email. The email is the user id (primary key); every project belongs to one user.

Sessions are signed cookies (HMAC-SHA256 with SESSION_SECRET): no session table to query on each request,
so any number of API replicas can verify them. If API_TOKEN is set, it acts as a team access code that is
required to sign in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from pathlib import Path

from ..config import Settings

COOKIE = "contino_session"
SESSION_DAYS = 14
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL.match(e) or len(e) > 254:
        raise ValueError("Enter a valid email address.")
    return e


def _secret(settings: Settings) -> bytes:
    if settings.session_secret:
        return settings.session_secret.encode()
    # Local dev without SESSION_SECRET: keep a random secret in data/ so sessions survive restarts.
    path = Path(settings.data_dir) / ".session_secret"
    try:
        return path.read_bytes()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32).encode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(value)
        return value


def issue(settings: Settings, email: str) -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{email}|{exp}"
    sig = hmac.new(_secret(settings), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()


def verify(settings: Settings, token: str | None) -> str | None:
    """Return the signed-in email, or None."""
    if not token:
        return None
    try:
        email, exp, sig = base64.urlsafe_b64decode(token.encode()).decode().rsplit("|", 2)
    except Exception:  # noqa: BLE001
        return None
    good = hmac.new(_secret(settings), f"{email}|{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good) or int(exp) < time.time():
        return None
    return email
