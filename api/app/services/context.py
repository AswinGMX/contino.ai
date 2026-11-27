"""Process-wide service container (storage, config, LLM gateway)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..config import AppConfig, Settings, get_config, get_settings
from ..storage.base import Storage
from ..storage.factory import get_storage
from .llm import LLM
from .repo import Repo


@dataclass
class Ctx:
    storage: Storage
    cfg: AppConfig
    settings: Settings
    _llm: LLM | None = None
    _guard: threading.Lock = field(default_factory=threading.Lock)

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            with self._guard:
                if self._llm is None:
                    self._llm = LLM(self.storage, self.cfg, self.settings)
        return self._llm

    def repo(self, project_id: str) -> Repo:
        return Repo(self.storage, project_id)


_ctx: Ctx | None = None
_lock = threading.Lock()


def get_ctx() -> Ctx:
    global _ctx
    if _ctx is None:
        with _lock:
            if _ctx is None:
                _ctx = Ctx(storage=get_storage(), cfg=get_config(), settings=get_settings())
    return _ctx


def set_ctx(ctx: Ctx | None) -> None:
    """Test hook."""
    global _ctx
    _ctx = ctx
