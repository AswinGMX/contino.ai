"""Prompt templates live in prompts/*.md and are reloaded when the file changes.

Layout rule for cache hits: the template (static instructions) always comes first,
the per-shot data always last.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..config import get_settings

_cache: dict[str, tuple[float, str]] = {}
_guard = threading.Lock()


def load(name: str) -> str:
    path: Path = get_settings().prompts_dir / f"{name}.md"
    mtime = path.stat().st_mtime
    with _guard:
        hit = _cache.get(name)
        if hit and hit[0] == mtime:
            return hit[1]
    text = path.read_text(encoding="utf-8").strip()
    with _guard:
        _cache[name] = (mtime, text)
    return text


def compose(name: str, data: dict[str, Any], comment: str = "") -> str:
    """Static template first, then compact JSON data, then the creator's comment."""
    parts = [load(name), "", "## DATA", json.dumps(data, ensure_ascii=False, separators=(",", ":"))]
    if comment:
        parts += ["", "## CREATOR COMMENT (must be applied)", comment.strip()]
    return "\n".join(parts)
