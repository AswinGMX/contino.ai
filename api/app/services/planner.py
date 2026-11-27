"""Step 1: upload a Shot Planner PDF, parse it to plan.json, creator checks and approves."""
from __future__ import annotations

import io
import time

from pypdf import PdfReader

from ..domain.models import Plan, PlanRecord, Project, Status, new_id, slugify
from ..jobs.queue import enqueue
from . import prompts
from .context import Ctx
from .gates import GateError
from .library import sync_library
from .llm import CallContext
from .repo import Repo

MAX_PDF_BYTES = 30 * 1024 * 1024


def create_project(ctx: Ctx, filename: str, data: bytes) -> Project:
    if len(data) > MAX_PDF_BYTES:
        raise GateError("file_too_large", "The planner PDF must be 30 MB or smaller.")
    if not data.startswith(b"%PDF"):
        raise GateError("not_pdf", "Upload the Shot Planner as a PDF.")
    stem = filename.rsplit(".", 1)[0]
    pid = f"{slugify(stem)[:40]}-{new_id()[:6]}"
    repo = ctx.repo(pid)
    key = repo.image_key("input", slugify(stem) + ".pdf")
    ctx.storage.put_image(key, data, "application/pdf")
    project = Project(id=pid, title=stem.replace("_", " ").replace("-", " ").strip(), source_filename=filename,
                      parse_status="parsing")
    repo.mutate_project(lambda _c: project)
    repo.mutate_plan(lambda _c: PlanRecord(plan=Plan(title=project.title), status=Status.pending, source_key=key))
    enqueue(ctx.storage, pid, "parse_plan", {"source_key": key}, dedupe_key=f"parse:{pid}")
    return project


def reparse(ctx: Ctx, repo: Repo) -> None:
    pr = repo.must_plan()
    if pr.status == Status.approved:
        raise GateError("plan_locked", "The plan is approved. Edit it instead of re-parsing.")
    repo.mutate_project(lambda p: p.model_copy(update={"parse_status": "parsing", "parse_error": ""}) if p else None)
    enqueue(ctx.storage, repo.pid, "parse_plan", {"source_key": pr.source_key}, dedupe_key=f"parse:{repo.pid}")


def extract_text(pdf: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not read the PDF ({exc}). Re-export it and upload again.") from exc
    pages = []
    for i, page in enumerate(reader.pages, 1):
        txt = (page.extract_text() or "").strip()
        if txt:
            pages.append(f"--- page {i} ---\n{txt}")
    return "\n\n".join(pages)


def run_parse(ctx: Ctx, repo: Repo, source_key: str, job_id: str = "") -> Plan:
    text = extract_text(ctx.storage.get_image(source_key))
    if len(text) < 50:
        raise ValueError("No readable text found in the PDF (is it a scanned image?).")
    plan = ctx.llm.chat_json(
        CallContext(repo.pid, "PLAN", "parse", job_id),
        model=ctx.cfg.models.text,
        system=prompts.load("parse_plan"),
        user=f"## SHOT PLANNER TEXT\n{text}",
        schema=Plan,
    )
    plan = _ensure_ids(plan).normalise()
    if not plan.scenes or not any(sc.shots for sc in plan.scenes):
        raise ValueError("The parser found no shots in this planner.")
    repo.mutate_plan(lambda pr: (pr or PlanRecord(plan=plan)).model_copy(
        update={"plan": plan, "status": Status.needs_approval}))
    repo.mutate_project(lambda p: p.model_copy(update={
        "parse_status": "done", "parse_error": "", "title": plan.title or p.title}) if p else None)
    return plan


def parse_failed(repo: Repo, error: str) -> None:
    repo.mutate_project(lambda p: p.model_copy(update={"parse_status": "failed", "parse_error": error[:500]})
                        if p else None)


def update_plan(repo: Repo, plan: Plan) -> PlanRecord:
    plan = _ensure_ids(plan).normalise()

    def fn(pr: PlanRecord | None) -> PlanRecord:
        if pr is None:
            raise GateError("plan_missing", "No plan to edit.")
        # Editing an approved plan sends it back for approval; the library is re-synced on approval.
        return pr.model_copy(update={"plan": plan, "status": Status.needs_approval, "approved_at": None})

    out = repo.mutate_plan(fn)
    assert out is not None
    return out


def approve_plan(repo: Repo) -> PlanRecord:
    pr = repo.must_plan()
    if pr.status not in (Status.needs_approval, Status.approved):
        raise GateError("plan_not_ready", "The plan is still being parsed.")
    if not pr.plan.scenes:
        raise GateError("plan_empty", "The plan has no scenes.")
    out = repo.mutate_plan(lambda cur: cur.model_copy(update={"status": Status.approved, "approved_at": time.time()})
                           if cur else None)
    sync_library(repo, pr.plan)
    assert out is not None
    return out


def _ensure_ids(plan: Plan) -> Plan:
    """Stable, unique ids for characters and locations, derived from names when missing."""
    seen: set[str] = set()
    for c in plan.characters:
        c.name = c.name.strip()
        c.id = _unique(slugify(c.id or c.name), seen)
    seen = set()
    for l in plan.locations:
        l.name = l.name.strip()
        l.id = _unique(slugify(l.id or l.name), seen)
    for si, sc in enumerate(plan.scenes, 1):
        sc.id = sc.id or f"S{si:02d}"
        for hi, sh in enumerate(sc.shots, 1):
            sh.id = sh.id or f"{sc.id}_SH{hi:02d}"
    return plan


def _unique(base: str, seen: set[str]) -> str:
    out, n = base, 2
    while out in seen:
        out, n = f"{base}-{n}", n + 1
    seen.add(out)
    return out
