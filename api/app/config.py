"""Settings: secrets from .env, tunables from config.yaml.

Startup validation is strict: if the chosen STORAGE_MODE is missing settings,
the process stops with a clear message. Modes are never switched silently.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid. Fatal at startup."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    storage_mode: Literal["local", "cloud"] = "local"
    openai_api_key: str = ""
    api_token: str = ""
    embedded_worker: bool = True
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"

    # Model overrides. When set, they win over `models:` in config.yaml.
    image_model: str = ""     # reference, panel and final images
    text_model: str = ""      # planner parsing
    review_model: str = ""    # vision model that scores panels

    database_url: str = ""
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    data_dir: Path = ROOT_DIR / "data"
    output_dir: Path = ROOT_DIR / "output"
    prompts_dir: Path = ROOT_DIR / "prompts"
    config_file: Path = ROOT_DIR / "config.yaml"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_for_mode(self) -> None:
        missing: list[str] = []
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if self.storage_mode == "cloud":
            for name in ("database_url", "r2_account_id", "r2_access_key_id", "r2_secret_access_key", "r2_bucket"):
                if not getattr(self, name):
                    missing.append(name.upper())
        if missing:
            raise ConfigError(
                f"STORAGE_MODE={self.storage_mode} is missing required settings: {', '.join(missing)}. "
                "Set them in .env (see .env.example). The app will not switch modes on its own."
            )


class ReviewConfig(BaseModel):
    weights: dict[str, int]
    pass_overall: int = 75
    pass_min: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _weights_sum(self) -> "ReviewConfig":
        if sum(self.weights.values()) != 100:
            raise ValueError("review.weights must sum to 100")
        return self


class ImageConfig(BaseModel):
    request_size: str = "1536x1024"
    aspect: tuple[int, int] = (16, 9)
    reference_size: str = "1024x1024"
    draft_quality: str = "low"
    final_quality: str = "high"
    output_format: str = "png"
    input_fidelity: str | None = "high"
    review_max_side: int = 768
    sheet_max_per_image: int = 8
    stream: bool = True
    ref_max_side_draft: int = 512
    ref_max_side_final: int = 768
    compact_draft_refs: bool = True
    fix_quality_detail: str = "medium"
    partial_images: int = 1


class BudgetConfig(BaseModel):
    limit_usd: float = 50.0
    warn_ratio: float = 0.8
    reserve_usd: dict[str, float]


class RetryConfig(BaseModel):
    max_attempts: int = 3
    backoff_seconds: list[float] = Field(default_factory=lambda: [2, 6])


class JobsConfig(BaseModel):
    workers: int = 6
    image_concurrency: int = 4
    poll_interval_seconds: float = 0.5
    lease_seconds: int = 600


class ModelsConfig(BaseModel):
    image: str
    text: str
    vision: str


class AppConfig(BaseModel):
    models: ModelsConfig
    image: ImageConfig
    review: ReviewConfig
    budget: BudgetConfig
    pricing: dict[str, dict[str, float]]
    retries: RetryConfig
    jobs: JobsConfig

    def price(self, model: str) -> dict[str, float]:
        return self.pricing.get(model) or self.pricing["default"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_config() -> AppConfig:
    settings = get_settings()
    path = settings.config_file
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
        models = raw.setdefault("models", {})
        for env_value, key in ((settings.image_model, "image"), (settings.text_model, "text"),
                               (settings.review_model, "vision")):
            if env_value.strip():
                models[key] = env_value.strip()
        return AppConfig.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"invalid {path.name}: {exc}") from exc
