"""End-to-end flow through the HTTP API with a fake OpenAI client."""
from __future__ import annotations

import csv
import io
from urllib.parse import quote

import httpx
import openai

from app.storage.base import GLOBAL


def _err(r):
    return r.json()["error"]["code"]


def setup_project(env) -> str:
    c = env.client
    r = c.post("/api/projects", files={"file": ("Shot-Planner_Test.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    env.drain()
    plan = c.get(f"/api/projects/{pid}/plan").json()
    assert plan["status"] == "needs_approval"
    assert len(plan["plan"]["scenes"]) == 2
    # Reactions are derived from the shots.
    theo = next(ch for ch in plan["plan"]["characters"] if ch["id"] == "theo")
    assert theo["reactions"] == ["bored", "mischievous"]
    return pid


def approve_library(env, pid):
    c = env.client
    lib = c.get(f"/api/projects/{pid}/library").json()
    bases = [g["base"]["id"] for g in lib["characters"]] + [g["item"]["id"] for g in lib["locations"]]
    for iid in bases:
        assert c.post(f"/api/projects/{pid}/library/{quote(iid)}/generate", json={}).status_code == 202
    env.drain()
    for iid in bases:
        r = c.post(f"/api/projects/{pid}/library/{quote(iid)}/approve")
        assert r.status_code == 200, r.text
    lib = c.get(f"/api/projects/{pid}/library").json()
    reactions = [rx["id"] for g in lib["characters"] for rx in g["reactions"]]
    assert len(reactions) == 3
    for iid in reactions:
        assert c.post(f"/api/projects/{pid}/library/{quote(iid)}/generate", json={}).status_code == 202
    env.drain()
    for iid in reactions:
        assert c.post(f"/api/projects/{pid}/library/{quote(iid)}/approve").status_code == 200


def test_full_flow(env):
    c = env.client
    pid = setup_project(env)

    # Gate: library needs an approved plan.
    r = c.post(f"/api/projects/{pid}/library/char:theo/generate", json={})
    assert r.status_code == 409 and _err(r) in ("plan_not_approved", "not_found")
    assert c.post(f"/api/projects/{pid}/plan/approve").status_code == 200

    # Gate: reactions need an approved base reference.
    r = c.post(f"/api/projects/{pid}/library/{quote('react:theo:bored')}/generate", json={})
    assert r.status_code == 409 and _err(r) == "base_not_approved"

    # Gate: storyboard needs the whole library approved.
    r = c.post(f"/api/projects/{pid}/scenes/S01/generate", json={})
    assert r.status_code == 409 and _err(r) == "library_not_approved"

    approve_library(env, pid)

    # Gate: scene 1 first.
    r = c.post(f"/api/projects/{pid}/scenes/S02/generate", json={})
    assert r.status_code == 409 and _err(r) == "first_scene_pending"

    # Make S01_SH02 fail review on character.
    env.fake.review_scores["S01_SH02"] = {"character": 5, "quality": 9, "background": 8, "emotion": 8, "continuity": 8,
                                          "framing": 8}
    r = c.post(f"/api/projects/{pid}/scenes/S01/generate", json={})
    assert r.status_code == 202 and r.json()["queued"] == ["S01_SH01", "S01_SH02"]
    env.drain()

    sb = c.get(f"/api/projects/{pid}/storyboard").json()
    s1 = sb["scenes"][0]
    assert s1["board"]["status"] == "needs_approval" and s1["board"]["url"]
    verdicts = {p["shot"]["id"]: p["review"]["verdict"] for p in s1["panels"]}
    assert verdicts == {"S01_SH01": "pass", "S01_SH02": "fail"}

    # Draft panels send the compact set: reaction (carries identity) + location, not the base as well.
    panel_calls = [kw for kind, kw in env.fake.calls if kind == "edit" and "Storyboard panel" in kw["prompt"]]
    assert panel_calls and all(len(kw["image"]) == 2 for kw in panel_calls)
    assert all("also the identity reference" in kw["prompt"] for kw in panel_calls)
    assert all(kw["quality"] == "low" for kw in panel_calls)

    # Approving with a failed score needs an explicit override.
    r = c.post(f"/api/projects/{pid}/scenes/S01/approve", json={"overrides": []})
    assert r.status_code == 409 and _err(r) == "override_required"

    # Fix one panel instead: only that panel regenerates, board needs approval again.
    env.fake.review_scores.pop("S01_SH02")
    edits_before = sum(1 for k, _ in env.fake.calls if k == "edit")
    r = c.post(f"/api/projects/{pid}/panels/S01_SH02/fix", json={"comment": "Make Theo's tunic green"})
    assert r.status_code == 202
    env.drain()
    edits_after = sum(1 for k, _ in env.fake.calls if k == "edit")
    assert edits_after - edits_before == 1  # one image call: the single panel
    sb = c.get(f"/api/projects/{pid}/storyboard").json()
    s1 = sb["scenes"][0]
    sh2 = next(p for p in s1["panels"] if p["shot"]["id"] == "S01_SH02")
    assert sh2["version"] == 2 and sh2["review"]["verdict"] == "pass"
    assert s1["board"]["status"] == "needs_approval"
    fix_call = [kw for k, kw in env.fake.calls if k == "edit" and "Revise a storyboard panel" in kw["prompt"]][-1]
    assert "Make Theo's tunic green" in fix_call["prompt"] and len(fix_call["image"]) == 3

    assert c.post(f"/api/projects/{pid}/scenes/S01/approve", json={"overrides": []}).status_code == 200

    # Finals: gate on board approval for S02.
    r = c.post(f"/api/projects/{pid}/scenes/S02/finals", json={})
    assert r.status_code == 409 and _err(r) == "board_not_approved"
    r = c.post(f"/api/projects/{pid}/scenes/S01/finals", json={})
    assert r.status_code == 202 and len(r.json()["queued"]) == 2
    env.drain()
    fin = c.get(f"/api/projects/{pid}/finals").json()
    assert all(s["status"] == "needs_approval" for s in fin["scenes"][0]["shots"])
    final_calls = [kw for k, kw in env.fake.calls if k == "edit" and "Final keyframe" in kw["prompt"]]
    assert all(kw["quality"] == "high" for kw in final_calls)
    assert all(len(kw["image"]) == 4 for kw in final_calls)  # finals keep the full set: panel + base + reaction + location
    # Review inputs use the names in prompts/review.md.
    review_msg = next(kw for k, kw in env.fake.calls if k == "chat" and "storyboard supervisor" in kw["messages"][0]["content"])
    texts = " ".join(p["text"] for p in review_msg["messages"][1]["content"] if p["type"] == "text")
    assert '"shot_data"' in texts and "panel (under review" in texts and "character_references" in texts
    assert "location_reference" in texts

    # Reject one final with a comment: regenerated with the comment applied.
    r = c.post(f"/api/projects/{pid}/finals/S01_SH01/reject", json={"comment": "warmer light"})
    assert r.status_code == 202
    env.drain()
    for sid in ("S01_SH01", "S01_SH02"):
        assert c.post(f"/api/projects/{pid}/finals/{sid}/approve").status_code == 200

    # Scene 2 is now unlocked.
    assert c.post(f"/api/projects/{pid}/scenes/S02/generate", json={}).status_code == 202
    env.drain()

    # Usage: every call logged; CSV has the spec columns.
    u = c.get(f"/api/projects/{pid}/usage").json()
    assert u["totals"]["calls"] == len(env.fake.calls)
    lib_now = c.get(f"/api/projects/{pid}/library").json()
    assert lib_now["characters"][0]["base"]["check"]["score"] == 88  # library pictures get an AI check
    assert u["totals"]["approved_finals"] == 2 and u["totals"]["cost_per_approved_final_usd"] > 0
    rows = list(csv.reader(io.StringIO(c.get(f"/api/projects/{pid}/usage.csv").text)))
    assert rows[0] == ["time", "shot_id", "step", "model", "input_tokens", "output_tokens", "images",
                       "gpu_seconds", "cost_usd"]
    assert any(r[1].startswith("LIB:") for r in rows[1:])

    # Export.
    r = c.post(f"/api/projects/{pid}/export")
    assert r.status_code == 200, r.text
    out = env.settings.output_dir / "the-boy-who-cried-wolf"
    assert (out / "library/characters/theo.png").exists()
    assert (out / "library/locations/hillside-pasture.png").exists()
    assert (out / "storyboards/S01_storyboard.png").exists()
    assert (out / "keyframes/S01_SH01.png").exists()
    assert (out / "reviews/S01_review.md").exists()
    assert (out / "usage_log.csv").exists() and (out / "usage_by_shot.csv").exists()
    assert c.get(f"/api/projects/{pid}/export.zip").status_code == 200

    # Changing an approved reference marks dependent panels, boards and finals stale.
    r = c.post(f"/api/projects/{pid}/library/char:theo/reject", json={"comment": "shorter hair"})
    assert r.status_code == 202
    env.drain()
    assert c.post(f"/api/projects/{pid}/library/char:theo/approve").status_code == 200
    sb = c.get(f"/api/projects/{pid}/storyboard").json()
    assert sb["scenes"][0]["board"]["status"] == "stale"
    assert all(p["status"] == "stale" for p in sb["scenes"][0]["panels"])
    fin = c.get(f"/api/projects/{pid}/finals").json()
    assert all(s["status"] == "stale" for s in fin["scenes"][0]["shots"])
    lib = c.get(f"/api/projects/{pid}/library").json()
    theo = next(g for g in lib["characters"] if g["character"]["id"] == "theo")
    assert all(rx["status"] == "stale" for rx in theo["reactions"])

    # Images are served.
    img = sb["scenes"][0]["panels"][0]["url"]
    assert c.get(img).status_code == 200


def test_retries_capped_and_logged(env):
    c = env.client
    pid = setup_project(env)
    c.post(f"/api/projects/{pid}/plan/approve")
    req = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
    rate = lambda: openai.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None)  # noqa: E731

    # Two failures then success: 3 calls logged, item ready.
    env.fake.fail_next = [rate(), rate()]
    c.post(f"/api/projects/{pid}/library/char:theo/generate", json={})
    env.drain()
    item = next(g["base"] for g in c.get(f"/api/projects/{pid}/library").json()["characters"]
                if g["character"]["id"] == "theo")
    assert item["status"] == "needs_approval"
    usage = c.get(f"/api/projects/{pid}/usage").json()["calls"]
    lib_calls = [u for u in usage if u["shot_id"] == "LIB:Theo" and u["step"] == "character_ref"]
    assert [u["ok"] for u in sorted(lib_calls, key=lambda u: u["attempt"])] == [False, False, True]

    # Three failures: handed to a person (failed), no fourth call.
    env.fake.fail_next = [rate(), rate(), rate()]
    n0 = len(env.fake.calls)
    c.post(f"/api/projects/{pid}/library/char:elder/generate", json={})
    env.drain()
    assert len([c for c in env.fake.calls[n0:] if c[0] in ("edit", "generate")]) == 3
    elder = next(g["base"] for g in c.get(f"/api/projects/{pid}/library").json()["characters"]
                 if g["character"]["id"] == "elder")
    assert elder["status"] == "failed" and elder["error"]


def test_budget_blocks_calls(env):
    c = env.client
    pid = setup_project(env)
    c.post(f"/api/projects/{pid}/plan/approve")
    env.storage.update(GLOBAL, "budget", "current", lambda cur: {"spent": 50.0, "reservations": {}})
    assert c.get("/api/budget").json()["blocked"] is True
    n0 = len(env.fake.calls)
    c.post(f"/api/projects/{pid}/library/char:theo/generate", json={})
    env.drain()
    assert len(env.fake.calls) == n0  # no paid call was made
    theo = next(g["base"] for g in c.get(f"/api/projects/{pid}/library").json()["characters"]
                if g["character"]["id"] == "theo")
    assert theo["status"] == "failed" and "Budget" in theo["error"]


def test_upload_reference_and_validation(env):
    from tests.conftest import png_bytes
    c = env.client
    pid = setup_project(env)
    c.post(f"/api/projects/{pid}/plan/approve")
    r = c.post(f"/api/projects/{pid}/library/loc:hillside-pasture/upload",
               files={"file": ("hill.png", png_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/projects/{pid}/library/loc:hillside-pasture/upload",
               files={"file": ("bad.png", b"not an image", "image/png")})
    assert r.status_code == 409 and _err(r) == "invalid_image"
    # Reject requires a comment.
    r = c.post(f"/api/projects/{pid}/library/loc:hillside-pasture/reject", json={"comment": "  "})
    assert r.status_code == 409 and _err(r) == "comment_required"


def test_rejects_non_pdf(env):
    r = env.client.post("/api/projects", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 409 and _err(r) == "not_pdf"


def test_reaction_sheet_one_call_for_all_emotions(env):
    c = env.client
    pid = setup_project(env)
    c.post(f"/api/projects/{pid}/plan/approve")
    # Sheet needs the base reference approved first.
    r = c.post(f"/api/projects/{pid}/characters/theo/reactions/generate", json={})
    assert r.status_code == 409 and _err(r) == "base_not_approved"
    c.post(f"/api/projects/{pid}/library/char:theo/generate", json={})
    env.drain()
    c.post(f"/api/projects/{pid}/library/char:theo/approve")

    n0 = len(env.fake.calls)
    r = c.post(f"/api/projects/{pid}/characters/theo/reactions/generate", json={})
    assert r.status_code == 202 and len(r.json()["queued"]) == 2  # bored + mischievous
    env.drain()
    calls = [c for c in env.fake.calls[n0:] if c[0] in ("edit", "generate")]
    assert len(calls) == 1 and calls[0][0] == "edit"  # ONE image call for both emotions
    assert "Expression sheet" in calls[0][1]["prompt"] and len(calls[0][1]["image"]) == 1

    theo = next(g for g in c.get(f"/api/projects/{pid}/library").json()["characters"] if g["character"]["id"] == "theo")
    assert all(rx["status"] == "needs_approval" and rx["versions"][-1]["source"] == "sheet" for rx in theo["reactions"])
    assert c.get(theo["reactions"][0]["current_url"]).status_code == 200

    r = c.post(f"/api/projects/{pid}/characters/theo/reactions/approve")
    assert r.status_code == 200 and len(r.json()["approved"]) == 2
    usage = c.get(f"/api/projects/{pid}/usage").json()
    assert any(s["shot_id"] == "LIB:Theo:reactions" for s in usage["shared"])


def test_input_fidelity_dropped_when_model_rejects_it(env):
    c = env.client
    env.ctx.cfg.image.input_fidelity = "high"
    req = httpx.Request("POST", "https://api.openai.com/v1/images/edits")
    orig_edit = env.fake._edit

    def picky_edit(**kw):
        if "input_fidelity" in kw:
            env.fake.calls.append(("edit_rejected", kw))
            raise openai.BadRequestError("does not support the 'input_fidelity' parameter",
                                         response=httpx.Response(400, request=req), body=None)
        return orig_edit(**kw)

    env.fake.images.edit = picky_edit
    pid = setup_project(env)
    c.post(f"/api/projects/{pid}/plan/approve")
    c.post(f"/api/projects/{pid}/library/char:theo/generate", json={})
    env.drain()
    c.post(f"/api/projects/{pid}/library/char:theo/approve")
    c.post(f"/api/projects/{pid}/library/{quote('react:theo:bored')}/generate", json={})
    env.drain()
    theo = next(g for g in c.get(f"/api/projects/{pid}/library").json()["characters"] if g["character"]["id"] == "theo")
    bored = next(r for r in theo["reactions"] if r["emotion"] == "bored")
    assert bored["status"] == "needs_approval", bored["error"]


def test_accept_single_failed_panel_then_approve_scene(env):
    c = env.client
    pid = setup_project(env)
    c.post(f"/api/projects/{pid}/plan/approve")
    approve_library(env, pid)
    env.fake.review_scores["S01_SH02"] = {"character": 4, "quality": 9, "background": 8, "emotion": 8, "continuity": 8, "framing": 8}
    c.post(f"/api/projects/{pid}/scenes/S01/generate", json={})
    env.drain()
    r = c.post(f"/api/projects/{pid}/scenes/S01/approve", json={"overrides": []})
    assert r.status_code == 409 and _err(r) == "override_required"
    assert c.post(f"/api/projects/{pid}/panels/S01_SH02/accept").status_code == 200  # "It's fine, approve"
    assert c.post(f"/api/projects/{pid}/scenes/S01/approve", json={"overrides": []}).status_code == 200
    sb = c.get(f"/api/projects/{pid}/storyboard").json()["scenes"][0]
    assert sb["board"]["status"] == "approved" and "S01_SH02" in sb["board"]["overrides"]
    ov = c.get(f"/api/projects/{pid}").json()
    assert ov["cover_url"] and ov["cost_usd"] > 0
    assert c.get(f"/api/projects/{pid}/plan").json()["source_url"]


def test_delete_project(env):
    c = env.client
    pid = setup_project(env)
    assert c.delete(f"/api/projects/{pid}").status_code == 204
    assert c.get(f"/api/projects/{pid}").status_code == 404
    assert all(o["project"]["id"] != pid for o in c.get("/api/projects").json())
    assert c.delete(f"/api/projects/{pid}").status_code == 404


def test_fix_starts_from_best_version_and_restore_is_free(env):
    c = env.client
    pid = setup_project(env)
    c.post(f"/api/projects/{pid}/plan/approve")
    approve_library(env, pid)
    c.post(f"/api/projects/{pid}/scenes/S01/generate", json={})
    env.drain()                                                       # v1 scores 87 (default fake)
    env.fake.review_scores["S01_SH01"] = {"character": 6, "quality": 5, "background": 8, "emotion": 8, "continuity": 8, "framing": 8}
    c.post(f"/api/projects/{pid}/panels/S01_SH01/fix", json={"comment": "brighter sky"})
    env.drain()                                                       # v2 is worse
    jobs = env.storage.list_jobs(pid, active_only=False)
    r2 = c.post(f"/api/projects/{pid}/panels/S01_SH01/fix", json={"comment": "fix the hands"})
    assert r2.status_code == 202, r2.text
    fix_job = [j for j in env.storage.list_jobs(pid, active_only=True) if j["type"] == "panel_generate"][0]
    assert fix_job["payload"]["base_version"] == 1                    # the next fix starts from the best version
    env.drain()
    fix_prompt = [kw for k, kw in env.fake.calls if k == "edit" and "Apply ONLY the numbered changes" in kw["prompt"]][-1]["prompt"]
    assert "do_not_reintroduce" in fix_prompt and "keep_unchanged" in fix_prompt and "composition_change_allowed" in fix_prompt
    n_images = sum(1 for k, _ in env.fake.calls if k in ("edit", "generate"))
    r = c.post(f"/api/projects/{pid}/panels/S01_SH01/restore", json={"version": 1})
    assert r.status_code == 200
    assert sum(1 for k, _ in env.fake.calls if k in ("edit", "generate")) == n_images  # no image call
    sb = c.get(f"/api/projects/{pid}/storyboard").json()["scenes"][0]
    sh = next(p for p in sb["panels"] if p["shot"]["id"] == "S01_SH01")
    assert sh["version"] == 4 and sh["status"] == "needs_approval" and sh["review"]["overall"] > 80
    del jobs



def test_improve_uses_the_best_versions_own_problems(env):
    c = env.client
    pid = setup_project(env)
    c.post(f"/api/projects/{pid}/plan/approve")
    approve_library(env, pid)
    c.post(f"/api/projects/{pid}/scenes/S01/generate", json={})
    env.drain()
    # give v1 a specific anatomy problem in its review
    rid = "S01_SH01:v1"
    env.storage.update(pid, "reviews", rid, lambda cur: {**cur, "issues": [
        {"problem": "two pebbles", "fix": "Show exactly one pebble in Theo's right hand", "severity": "major", "category": "object"}]})
    r = c.post(f"/api/projects/{pid}/panels/S01_SH01/improve")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["base_version"] == 1 and body["quality"] == "medium"   # detail fix -> medium quality
    job = [j for j in env.storage.list_jobs(pid, active_only=True) if j["type"] == "panel_generate"][0]
    assert job["payload"]["comment"] == "1. Show exactly one pebble in Theo's right hand."


def test_character_kind_and_start_over(env):
    c = env.client
    pid = setup_project(env)
    plan = c.get(f"/api/projects/{pid}/plan").json()["plan"]
    plan["characters"].append({"id": "fireflies", "name": "Fireflies",
                               "description": "Hundreds of tiny insects that appear as golden points of light"})
    c.put(f"/api/projects/{pid}/plan", json=plan)
    kinds = {ch["id"]: ch["kind"] for ch in c.get(f"/api/projects/{pid}/plan").json()["plan"]["characters"]}
    assert kinds == {"theo": "person", "elder": "person", "fireflies": "group"}
    c.post(f"/api/projects/{pid}/plan/approve")
    c.post(f"/api/projects/{pid}/library/char:fireflies/generate", json={})
    env.drain()
    first = [kw for k, kw in env.fake.calls if k in ("generate", "edit") and '"kind":"group"' in kw["prompt"]]
    assert first, "the picture prompt carries the character kind"
    # start over: a fresh picture, the previous one is NOT attached for editing
    c.post(f"/api/projects/{pid}/library/char:fireflies/generate", json={"comment": "only golden points of light", "fresh": True})
    env.drain()
    last = [kw for k, kw in env.fake.calls if k in ("generate", "edit") and "only golden points of light" in kw["prompt"]][-1]
    assert "image" not in last   # no reference image -> generated from scratch


def test_uploaded_pictures_are_not_scored(env):
    from tests.conftest import png_bytes
    c = env.client
    pid = setup_project(env)
    c.post(f"/api/projects/{pid}/plan/approve")
    n_chat = sum(1 for k, _ in env.fake.calls if k == "chat")
    r = c.post(f"/api/projects/{pid}/library/char:theo/upload", files={"file": ("theo.png", png_bytes(), "image/png")})
    assert r.status_code == 200
    env.drain()
    assert sum(1 for k, _ in env.fake.calls if k == "chat") == n_chat     # no AI check call
    theo = next(g["base"] for g in c.get(f"/api/projects/{pid}/library").json()["characters"] if g["character"]["id"] == "theo")
    assert theo["check"] is None and theo["status"] == "needs_approval"
    r = c.post(f"/api/projects/{pid}/library/char:theo/check")
    assert r.status_code == 409 and _err(r) == "uploaded_not_scored"



def test_users_own_their_projects(env):
    from fastapi.testclient import TestClient
    c = env.client
    pid = setup_project(env)
    me = c.get("/api/auth/me").json()["user"]
    assert me["email"] == "tester@example.com"                       # email is normalised and is the user id
    assert env.storage.project_owner(pid) == "tester@example.com"
    # signed out: everything under /api needs a user
    anon = TestClient(c.app)
    assert anon.get("/api/projects").status_code == 401
    # another user sees nothing of tester's work
    other = TestClient(c.app)
    other.post("/api/auth/login", json={"email": "someone.else@example.com"})
    assert other.get("/api/projects").json() == []
    assert other.get(f"/api/projects/{pid}").status_code == 404
    assert other.get(f"/api/projects/{pid}/plan").status_code == 404
    assert other.delete(f"/api/projects/{pid}").status_code == 404
    assert [o["project"]["id"] for o in c.get("/api/projects").json()] == [pid]


def test_every_file_is_in_the_catalogue(env):
    c = env.client
    pid = setup_project(env)
    c.post(f"/api/projects/{pid}/plan/approve")
    approve_library(env, pid)
    c.post(f"/api/projects/{pid}/scenes/S01/generate", json={})
    env.drain()
    assets = c.get(f"/api/projects/{pid}/assets").json()
    kinds = {a["kind"] for a in assets}
    assert {"input", "character", "place", "expression", "panel", "panel_captioned", "board"} <= kinds
    theo = [a for a in assets if a["item_id"] == "char:theo"]
    assert theo and theo[0]["version"] == 1 and len(theo[0]["sha256"]) == 64 and theo[0]["bytes"] > 0


def test_browser_runs_jobs_without_worker(env):
    """Serverless setup: no worker; the open tab calls /jobs/run and each call runs one job."""
    c = env.client
    env.settings.request_jobs = True
    assert c.get("/api/config").json()["browser_runs_jobs"] is True
    r = c.post("/api/projects", files={"file": ("Shot-Planner_Test.pdf", b"%PDF-1.4 fake", "application/pdf")})
    pid = r.json()["id"]
    out = c.post(f"/api/projects/{pid}/jobs/run").json()
    assert out["ran"] == "parse_plan"
    assert c.get(f"/api/projects/{pid}/plan").json()["status"] == "needs_approval"
    assert c.post(f"/api/projects/{pid}/jobs/run").json()["ran"] is None  # queue empty

    # Another user's run call cannot touch this project (owner check: 404).
    c.post("/api/auth/logout")
    c.post("/api/auth/login", json={"email": "someone@else.com", "name": "Else"})
    assert c.post(f"/api/projects/{pid}/jobs/run").status_code == 404


def test_run_job_disabled_when_worker_mode(env):
    env.settings.request_jobs = False
    c = env.client
    pid = c.post("/api/projects", files={"file": ("Shot-Planner_Test.pdf", b"%PDF-1.4 fake", "application/pdf")}).json()["id"]
    assert c.post(f"/api/projects/{pid}/jobs/run").json() == {"ran": None, "disabled": True}
    assert len(c.get(f"/api/projects/{pid}/jobs").json()) == 1  # still queued for the worker
