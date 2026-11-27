"""Every OpenAI call goes through here.

Responsibilities, in order, for every attempt:
  1. reserve budget (blocks new calls at the limit),
  2. call the API,
  3. read token usage from the response (never estimated when returned),
  4. log one usage record - including failures and invalid-output retries,
  5. settle the budget reservation to the real cost.

Automatic retries cover failed calls and invalid output only: at most
`retries.max_attempts - 1` (2) retries, then the error is raised so the item is
handed to a person.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

import httpx
import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from ..config import AppConfig, Settings
from ..domain.models import UsageRecord
from ..logging_setup import log
from ..storage.base import Storage
from .budget import BudgetExceeded, BudgetGuard, UsageCounts, compute_cost

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

RETRYABLE = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)


class InvalidOutput(ValueError):
    """Model returned output that failed parsing / validation (retryable)."""


class LLMError(RuntimeError):
    """Retries exhausted or non-retryable failure; hand the item to a person."""


@dataclass
class CallContext:
    project_id: str
    shot_id: str          # S01_SH01, or LIB:<name> for library calls, or PLAN
    step: str             # parse | character_ref | reaction_ref | location_ref | panel | panel_fix | review | final
    job_id: str = ""


class LLM:
    def __init__(self, storage: Storage, cfg: AppConfig, settings: Settings,
                 client: OpenAI | None = None):
        self.s = storage
        self.cfg = cfg
        self.budget = BudgetGuard(storage, cfg)
        self.client = client or OpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=300)
        self._image_sem = threading.BoundedSemaphore(cfg.jobs.image_concurrency)
        self._no_fidelity = False

    # ------------------------------------------------------------------ text / vision
    def chat_json(self, ctx: CallContext, *, model: str, system: str, user: list[dict] | str,
                  schema: type[T], reserve_key: str = "text", max_tokens: int = 16000) -> T:
        content = user if isinstance(user, list) else [{"type": "text", "text": user}]
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]

        def call() -> tuple[T, UsageCounts]:
            resp = self.client.chat.completions.create(
                model=model, messages=messages, response_format={"type": "json_object"},
                max_completion_tokens=max_tokens,
                prompt_cache_key=f"{ctx.project_id}:{ctx.step}",
            )
            counts = _chat_usage(resp)
            text = (resp.choices[0].message.content or "").strip()
            try:
                return schema.model_validate(json.loads(text)), counts
            except (json.JSONDecodeError, ValidationError) as exc:
                raise _WithUsage(InvalidOutput(f"invalid JSON from model: {exc}"[:500]), counts) from exc

        return self._run(ctx, model, reserve_key, images=0, fn=call)

    # ------------------------------------------------------------------ images
    def image(self, ctx: CallContext, *, prompt: str, refs: list[tuple[str, bytes]], size: str,
              quality: str) -> bytes:
        """Generate one image. With refs, uses the edit endpoint so references are attached."""
        model = self.cfg.models.image
        reserve_key = "image_high" if quality in ("high", "xhigh", "max") else "image_low"

        def call() -> tuple[bytes, UsageCounts]:
            kwargs: dict[str, Any] = dict(model=model, prompt=prompt, size=size, quality=quality, n=1,
                                          output_format=self.cfg.image.output_format)
            if self.cfg.image.stream:
                kwargs.update(stream=True, partial_images=self.cfg.image.partial_images)
            with self._image_sem:
                if refs:
                    if self.cfg.image.input_fidelity and not self._no_fidelity:
                        kwargs["input_fidelity"] = self.cfg.image.input_fidelity
                    files = [(name, data, "image/png") for name, data in refs]
                    try:
                        resp = self.client.images.edit(image=files, **kwargs)
                    except openai.BadRequestError as exc:
                        if "input_fidelity" not in kwargs or "input_fidelity" not in str(exc):
                            raise
                        # Model does not support it: remember, drop it, resend once (a 400 is not billed).
                        self._no_fidelity = True
                        kwargs.pop("input_fidelity")
                        log(logger, logging.WARNING, "image model rejects input_fidelity; disabled", model=model)
                        resp = self.client.images.edit(image=files, **kwargs)
                else:
                    resp = self.client.images.generate(**kwargs)
            if self.cfg.image.stream:
                return _consume_image_stream(resp)
            counts = _image_usage(resp)
            try:
                item = resp.data[0]
                if getattr(item, "b64_json", None):
                    return base64.b64decode(item.b64_json), counts
                if getattr(item, "url", None):
                    r = httpx.get(item.url, timeout=60)
                    r.raise_for_status()
                    return r.content, counts
            except (IndexError, TypeError, httpx.HTTPError) as exc:
                raise _WithUsage(InvalidOutput(f"no image in response: {exc}"), counts) from exc
            raise _WithUsage(InvalidOutput("no image in response"), counts)

        return self._run(ctx, model, reserve_key, images=1, fn=call)

    # ------------------------------------------------------------------ core loop
    def _run(self, ctx: CallContext, model: str, reserve_key: str, images: int,
             fn: Callable[[], tuple[Any, UsageCounts]]) -> Any:
        max_attempts = max(1, self.cfg.retries.max_attempts)
        backoff = self.cfg.retries.backoff_seconds
        reserve = self.cfg.budget.reserve_usd.get(reserve_key, 0.5)
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            rid = self.budget.reserve(reserve)  # raises BudgetExceeded -> not retried
            t0 = time.monotonic()
            counts = UsageCounts()
            err = ""
            try:
                result, counts = fn()
                cost = compute_cost(self.cfg, model, counts)
                self._log(ctx, model, counts, images, cost, attempt, ok=True, seconds=time.monotonic() - t0)
                self.budget.settle(rid, cost)
                return result
            except _WithUsage as wu:
                counts, last_exc = wu.counts, wu.inner
                err = str(wu.inner)
            except RETRYABLE as exc:
                last_exc, err = exc, f"{type(exc).__name__}: {exc}"
            except BudgetExceeded:
                self.budget.settle(rid, 0.0)
                raise
            except openai.APIStatusError as exc:  # 4xx etc: not retryable
                cost = 0.0
                self._log(ctx, model, counts, 0, cost, attempt, ok=False, seconds=time.monotonic() - t0,
                          error=f"{type(exc).__name__}: {exc}")
                self.budget.settle(rid, cost)
                raise LLMError(_short(exc)) from exc
            except Exception as exc:  # noqa: BLE001 - unexpected: log, release, stop
                self._log(ctx, model, counts, 0, 0.0, attempt, ok=False, seconds=time.monotonic() - t0,
                          error=f"{type(exc).__name__}: {exc}")
                self.budget.settle(rid, 0.0)
                raise LLMError(_short(exc)) from exc

            cost = compute_cost(self.cfg, model, counts)
            self._log(ctx, model, counts, 0, cost, attempt, ok=False, seconds=time.monotonic() - t0, error=err)
            self.budget.settle(rid, cost)
            log(logger, logging.WARNING, "llm attempt failed", step=ctx.step, shot=ctx.shot_id,
                attempt=attempt, error=err[:300])
            if attempt < max_attempts:
                time.sleep(backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 1)

        raise LLMError(f"failed after {max_attempts} attempts: {_short(last_exc)}")

    def _log(self, ctx: CallContext, model: str, c: UsageCounts, images: int, cost: float, attempt: int,
             ok: bool, seconds: float, error: str = "") -> None:
        rec = UsageRecord(
            project_id=ctx.project_id, shot_id=ctx.shot_id, step=ctx.step, model=model,
            input_tokens=c.input_total, output_tokens=c.output_total,
            input_text_tokens=c.input_text, input_image_tokens=c.input_image, cached_tokens=c.cached,
            output_text_tokens=c.output_text, output_image_tokens=c.output_image,
            images=images if ok else 0, gpu_seconds=0.0, cost_usd=cost, ok=ok, attempt=attempt,
            error=error[:1000], job_id=ctx.job_id,
        )
        self.s.update(ctx.project_id, "usage", rec.id, lambda _c: rec.dump())
        log(logger, logging.INFO, "llm call", step=ctx.step, shot=ctx.shot_id, model=model, ok=ok,
            attempt=attempt, cost_usd=cost, in_tokens=c.input_total, out_tokens=c.output_total,
            seconds=round(seconds, 2))


def _consume_image_stream(stream: Any) -> tuple[bytes, UsageCounts]:
    """Read an image event stream; the completed event carries the final image and usage."""
    final = None
    for event in stream:
        if str(getattr(event, "type", "")).endswith(".completed"):
            final = event
    if final is None or not getattr(final, "b64_json", None):
        counts = _image_usage(final) if final is not None else UsageCounts()
        raise _WithUsage(InvalidOutput("image stream ended without a completed image"), counts)
    return base64.b64decode(final.b64_json), _image_usage(final)


class _WithUsage(Exception):
    def __init__(self, inner: Exception, counts: UsageCounts):
        super().__init__(str(inner))
        self.inner, self.counts = inner, counts


def _g(obj: Any, name: str, default: int = 0) -> int:
    if obj is None:
        return default
    v = getattr(obj, name, None)
    if v is None and isinstance(obj, dict):
        v = obj.get(name)
    return int(v or 0)


def _chat_usage(resp: Any) -> UsageCounts:
    u = getattr(resp, "usage", None)
    if u is None:
        return UsageCounts()
    d = getattr(u, "prompt_tokens_details", None)
    prompt = _g(u, "prompt_tokens")
    img = _g(d, "image_tokens")
    return UsageCounts(input_text=prompt - img, input_image=img, cached=_g(d, "cached_tokens"),
                       output_text=_g(u, "completion_tokens"))


def _image_usage(resp: Any) -> UsageCounts:
    u = getattr(resp, "usage", None)
    if u is None:
        return UsageCounts()
    di = getattr(u, "input_tokens_details", None)
    do = getattr(u, "output_tokens_details", None)
    total_in = _g(u, "input_tokens")
    text_in = _g(di, "text_tokens")
    img_in = _g(di, "image_tokens")
    if di is None or (text_in + img_in) == 0:
        text_in, img_in = total_in, 0
    total_out = _g(u, "output_tokens")
    out_img = _g(do, "image_tokens") if do is not None else total_out
    out_txt = _g(do, "text_tokens") if do is not None else 0
    if do is not None and out_img + out_txt == 0:
        out_img = total_out
    return UsageCounts(input_text=text_in, input_image=img_in, cached=_g(di, "cached_tokens"),
                       output_text=out_txt, output_image=out_img)


def _short(exc: Exception | None) -> str:
    if exc is None:
        return "unknown error"
    msg = getattr(exc, "message", None) or str(exc)
    return f"{type(exc).__name__}: {msg}"[:500]
