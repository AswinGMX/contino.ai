"""Step 7 (cost): tokens and cost per call, per shot, per step, totals, cost per approved final."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime, timezone

from ..domain.models import Status, UsageRecord
from .budget import BudgetGuard
from .context import Ctx
from .repo import Repo

CSV_COLUMNS = ["time", "shot_id", "step", "model", "input_tokens", "output_tokens", "images", "gpu_seconds",
               "cost_usd"]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def summary(ctx: Ctx, repo: Repo) -> dict:
    rows = repo.usage()
    plan_rec = repo.plan_record()
    shot_ids = [sh.id for sc in plan_rec.plan.scenes for sh in sc.shots] if plan_rec else []

    by_step: dict[str, dict] = defaultdict(lambda: {"calls": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0,
                                                    "images": 0, "cost_usd": 0.0})
    by_shot: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                    "images": 0, "cost_usd": 0.0})
    for r in rows:
        for bucket in (by_step[r.step], by_shot[r.shot_id]):
            bucket["calls"] += 1
            bucket["input_tokens"] += r.input_tokens
            bucket["output_tokens"] += r.output_tokens
            bucket["images"] += r.images
            bucket["cost_usd"] += r.cost_usd
        if not r.ok:
            by_step[r.step]["failed"] += 1

    # Library (LIB:*) and plan-parsing (PLAN) calls are shared across every shot in the report.
    shared = sum(v["cost_usd"] for k, v in by_shot.items() if k not in shot_ids)
    per_shot_share = shared / len(shot_ids) if shot_ids else 0.0
    shots = []
    for sid in shot_ids:
        d = by_shot.get(sid, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "images": 0, "cost_usd": 0.0})
        shots.append({"shot_id": sid, **d, "direct_cost_usd": round(d["cost_usd"], 6),
                      "shared_cost_usd": round(per_shot_share, 6),
                      "total_cost_usd": round(d["cost_usd"] + per_shot_share, 6)})
    shared_rows = [{"shot_id": k, **v} for k, v in sorted(by_shot.items()) if k not in shot_ids]

    total = sum(r.cost_usd for r in rows)
    approved = sum(1 for f in repo.finals() if f.status == Status.approved)
    return {
        "totals": {
            "calls": len(rows), "failed_calls": sum(1 for r in rows if not r.ok),
            "input_tokens": sum(r.input_tokens for r in rows), "output_tokens": sum(r.output_tokens for r in rows),
            "cached_tokens": sum(r.cached_tokens for r in rows), "images": sum(r.images for r in rows),
            "cost_usd": round(total, 6), "approved_finals": approved,
            "cost_per_approved_final_usd": round(total / approved, 6) if approved else None,
            "shared_cost_usd": round(shared, 6),
        },
        "by_step": {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in sorted(by_step.items())},
        "by_shot": shots,
        "shared": shared_rows,
        "calls": [_row(r) for r in reversed(rows)],
        "budget": BudgetGuard(ctx.storage, ctx.cfg).status(),
    }


def _row(r: UsageRecord) -> dict:
    return {"id": r.id, "time": _iso(r.time), "shot_id": r.shot_id, "step": r.step, "model": r.model,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "cached_tokens": r.cached_tokens,
            "images": r.images, "gpu_seconds": r.gpu_seconds, "cost_usd": r.cost_usd, "ok": r.ok,
            "attempt": r.attempt, "error": r.error}


def usage_csv(repo: Repo) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in repo.usage():
        w.writerow([_iso(r.time), r.shot_id, r.step, r.model, r.input_tokens, r.output_tokens, r.images,
                    f"{r.gpu_seconds:.2f}", f"{r.cost_usd:.6f}"])
    return buf.getvalue()


def by_shot_csv(ctx: Ctx, repo: Repo) -> str:
    s = summary(ctx, repo)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["shot_id", "calls", "images", "direct_cost_usd", "shared_library_cost_usd", "total_cost_usd"])
    for row in s["by_shot"]:
        w.writerow([row["shot_id"], row["calls"], row["images"], f"{row['direct_cost_usd']:.6f}",
                    f"{row['shared_cost_usd']:.6f}", f"{row['total_cost_usd']:.6f}"])
    t = s["totals"]
    w.writerow(["TOTAL", t["calls"], t["images"], "", f"{t['shared_cost_usd']:.6f}", f"{t['cost_usd']:.6f}"])
    w.writerow(["COST_PER_APPROVED_FINAL", "", "", "", "",
                f"{t['cost_per_approved_final_usd']:.6f}" if t["cost_per_approved_final_usd"] else ""])
    return buf.getvalue()
