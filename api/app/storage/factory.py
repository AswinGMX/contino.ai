from __future__ import annotations

import threading

from ..config import Settings, get_settings
from .base import Storage

_storage: Storage | None = None
_guard = threading.Lock()


def build_storage(settings: Settings) -> Storage:
    if settings.storage_mode == "local":
        from .local import LocalStorage
        return LocalStorage(settings.data_dir)
    if settings.storage_mode == "cloud":
        from .cloud import CloudStorage
        return CloudStorage(settings.database_url, settings.r2_account_id, settings.r2_access_key_id,
                            settings.r2_secret_access_key, settings.r2_bucket)
    raise ValueError(f"unknown STORAGE_MODE {settings.storage_mode!r}")


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        with _guard:
            if _storage is None:
                _storage = build_storage(get_settings())
    return _storage


def set_storage(storage: Storage | None) -> None:
    """Test hook."""
    global _storage
    _storage = storage
