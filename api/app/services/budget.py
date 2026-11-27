"""Budget guard and cost calculation.

Before each paid call we atomically *reserve* an upper-bound cost; afterwards we
*settle* the reservation to the real cost read from the API response. Because
reservation is a single atomic update, any number of parallel workers (threads or
processes, local or cloud) can never jointly overspend the limit.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from ..config import AppConfig
from ..storage.base import GLOBAL, Storage

RESERVATION_TTL = 1800  # a reservation from a crashed worker is released after 30 min


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class UsageCounts:
    input_text: int = 0
    input_image: int = 0
    cached: int = 0
    output_text: int = 0
    output_image: int = 0

    @property
    def input_total(self) -> int:
        return self.input_text + self.input_image

    @property
    def output_total(self) -> int:
        return self.output_text + self.output_image


def compute_cost(cfg: AppConfig, model: str, u: UsageCounts) -> float:
    """Cost = tokens x rate, with text, image and cached tokens priced separately.

    The API reports cached tokens as a single number; attribute them to text
    input first (static prompt prefixes), then to image input.
    """
    p = cfg.price(model)
    cached_text = min(u.cached, u.input_text)
    cached_image = min(u.cached - cached_text, u.input_image)
    cost = (
        (u.input_text - cached_text) * p.get("text_input", 0)
        + cached_text * p.get("text_input_cached", p.get("text_input", 0))
        + (u.input_image - cached_image) * p.get("image_input", 0)
        + cached_image * p.get("image_input_cached", p.get("image_input", 0))
        + u.output_text * p.get("text_output", 0)
        + u.output_image * p.get("image_output", 0)
    ) / 1_000_000
    return round(cost, 6)


class BudgetGuard:
    def __init__(self, storage: Storage, cfg: AppConfig):
        self.s = storage
        self.cfg = cfg

    def _limit(self) -> float:
        return self.cfg.budget.limit_usd

    def reserve(self, amount: float) -> str:
        rid = uuid.uuid4().hex
        limit = self._limit()

        def fn(cur):
            cur = cur or {"spent": 0.0, "reservations": {}}
            now = time.time()
            res = {k: v for k, v in cur.get("reservations", {}).items() if now - v["at"] < RESERVATION_TTL}
            committed = cur.get("spent", 0.0) + sum(v["amount"] for v in res.values())
            if cur.get("spent", 0.0) >= limit:
                raise BudgetExceeded(f"Budget limit ${limit:.2f} reached; new calls are blocked.")
            if committed + amount > limit:
                raise BudgetExceeded(
                    f"This call could exceed the ${limit:.2f} budget "
                    f"(spent ${cur.get('spent', 0):.2f}, in flight ${committed - cur.get('spent', 0):.2f})."
                )
            res[rid] = {"amount": amount, "at": now}
            cur["reservations"] = res
            return cur

        self.s.update(GLOBAL, "budget", "current", fn)
        return rid

    def settle(self, reservation_id: str, actual: float) -> None:
        def fn(cur):
            cur = cur or {"spent": 0.0, "reservations": {}}
            cur.get("reservations", {}).pop(reservation_id, None)
            cur["spent"] = round(cur.get("spent", 0.0) + actual, 6)
            return cur
        self.s.update(GLOBAL, "budget", "current", fn)

    def status(self) -> dict:
        cur = self.s.get(GLOBAL, "budget", "current") or {"spent": 0.0, "reservations": {}}
        limit = self._limit()
        spent = cur.get("spent", 0.0)
        in_flight = sum(v["amount"] for v in cur.get("reservations", {}).values())
        return {
            "limit_usd": limit,
            "spent_usd": round(spent, 4),
            "in_flight_usd": round(in_flight, 4),
            "remaining_usd": round(max(0.0, limit - spent), 4),
            "warn": spent >= limit * self.cfg.budget.warn_ratio,
            "blocked": spent >= limit,
        }
