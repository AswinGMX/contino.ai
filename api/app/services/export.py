"""Export approved outputs + usage log to output/<project>/ on local disk (both modes), and as a zip."""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from ..domain.models import Status, slugify
from .context import Ctx
from .repo import Repo
from .storyboard import CRITERIA
from .usage import by_shot_csv, usage_csv


def export(ctx: Ctx, repo: Repo) -> dict:
    project = repo.project()
    pr = repo.must_plan()
    plan = pr.plan
    root = ctx.settings.output_dir.resolve()
    dest = root / slugify(project.title or project.id)
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".export-", dir=root))
    written: dict[str, int] = {"characters": 0, "reactions": 0, "locations": 0, "storyboards": 0, "keyframes": 0}
    try:
        def put(rel: str, key: str) -> None:
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(ctx.storage.get_image(key))

        for it in repo.library():
            v = it.approved()
            if not v or it.status != Status.approved:
                continue
            if it.kind == "character":
                put(f"library/characters/{slugify(it.name)}.png", v.image_key); written["characters"] += 1
            elif it.kind == "reaction":
                put(f"library/reactions/{slugify(it.name)}_{slugify(it.emotion)}.png", v.image_key)
                written["reactions"] += 1
            else:
                put(f"library/locations/{slugify(it.name)}.png", v.image_key); written["locations"] += 1

        boards = {b.id: b for b in repo.boards()}
        panels = {p.id: p for p in repo.panels()}
        reviews = {r.id: r for r in repo.reviews()}
        finals = {f.id: f for f in repo.finals()}
        all_reviews = []
        for sc in plan.scenes:
            b = boards.get(sc.id)
            if b and b.status == Status.approved and b.image_key:
                put(f"storyboards/{sc.id}_storyboard.png", b.image_key); written["storyboards"] += 1
            lines = [f"# {sc.id} storyboard - AI review notes", "",
                     "| Shot | " + " | ".join(c.capitalize() for c in CRITERIA) + " | Overall | Verdict | Notes | Suggested fix | Human decision |",
                     "|" + "---|" * (len(CRITERIA) + 6)]
            any_review = False
            for sh in sc.shots:
                p = panels.get(sh.id)
                r = reviews.get(f"{sh.id}:v{p.current.n}") if p and p.current else None
                if r:
                    any_review = True
                    decision = "approved (override)" if b and sh.id in b.overrides else (
                        "approved" if p.status == Status.approved else p.status)
                    lines.append(f"| {sh.id} | " + " | ".join(f"{r.scores.get(c, 0):g}" for c in CRITERIA)
                                 + f" | {r.overall:g} | {r.verdict} | {_md(r.notes)} | {_md(r.suggested_fix)} | {decision} |")
                    all_reviews.append({**r.model_dump(mode="json", exclude={"rev", "updated_at"}),
                                        "human_decision": decision})
                f = finals.get(sh.id)
                fv = next((v for v in f.versions if v.n == f.approved_version), None) if f else None
                if fv and f.status == Status.approved:
                    put(f"keyframes/{sh.id}.png", fv.image_key); written["keyframes"] += 1
            if any_review:
                (tmp / "reviews").mkdir(parents=True, exist_ok=True)
                (tmp / "reviews" / f"{sc.id}_review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if all_reviews:
            (tmp / "reviews" / "reviews.json").write_text(json.dumps(all_reviews, indent=2), encoding="utf-8")

        (tmp / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        (tmp / "usage_log.csv").write_text(usage_csv(repo), encoding="utf-8")
        (tmp / "usage_by_shot.csv").write_text(by_shot_csv(ctx, repo), encoding="utf-8")

        # Swap in atomically so a half-written export never replaces a good one.
        if dest.exists():
            old = dest.with_name(f".{dest.name}.old")
            shutil.rmtree(old, ignore_errors=True)
            os.replace(dest, old)
            os.replace(tmp, dest)
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.replace(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"path": str(dest), "written": written}


def zip_export(ctx: Ctx, repo: Repo) -> tuple[str, bytes]:
    info = export(ctx, repo)
    dest = Path(info["path"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(dest.rglob("*")):
            if p.is_file():
                zf.write(p, f"output/{p.relative_to(dest)}")
    return f"{dest.name}-output.zip", buf.getvalue()


def _md(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")
