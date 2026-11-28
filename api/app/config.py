"""Settings: secrets from .env, tunables from config.yaml.

All data lives in PostgreSQL and every file in Cloudflare R2. Startup validation is
strict: if any required setting is missing, the process stops with a clear message.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import ClassVar
from pathlib import Path
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

    openai_api_key: str = ""
    api_token: str = ""              # optional team access code required to sign in
    session_secret: str = ""         # signs sign-in cookies (required)
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

    # Deployment
    cookie_cross_site: bool = False   # true when frontend and API are on different domains (e.g. two Vercel projects)
    cors_origin_regex: str = ""       # e.g. https://contino-.*\.vercel\.app to allow Vercel preview URLs
    db_pool_min: int = 4     # connections kept open and ready (each new one costs several round trips)
    db_pool_max: int = 20    # enough for the job workers plus page refreshes, so nobody queues

    data_dir: Path = ROOT_DIR / "data"
    # Serverless platforms only allow writing to /tmp.
    output_dir: Path = Path("/tmp/output") if os.environ.get("VERCEL") else ROOT_DIR / "output"
    prompts_dir: Path = ROOT_DIR / "prompts"
    config_file: Path = ROOT_DIR / "config.yaml"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def serverless(self) -> bool:
        return bool(os.environ.get("VERCEL"))

    @property
    def storage_mode(self) -> str:
        return "cloud"   # PostgreSQL + Cloudflare R2 (the only mode)

    REQUIRED: ClassVar[tuple[str, ...]] = ("openai_api_key", "session_secret", "database_url", "r2_account_id", "r2_access_key_id",
                "r2_secret_access_key", "r2_bucket")

    def missing_settings(self) -> list[str]:
        return [name.upper() for name in self.REQUIRED if not str(getattr(self, name) or "").strip()]

    def validate_for_mode(self) -> None:
        missing = self.missing_settings()
        if missing:
            raise ConfigError(
                f"Missing required settings: {', '.join(missing)}. Set them in .env (see .env.example). "
                "contino.ai stores data in PostgreSQL and files in Cloudflare R2. "
                "Run `python -m app.check` from api/ to test the connections."
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
