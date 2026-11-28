from __future__ import annotations

import threading

from app.config import get_config
from app.services import imaging
from app.services.budget import BudgetExceeded, BudgetGuard, UsageCounts, compute_cost
from app.services.storyboard import score
from app.storage.local import LocalStorage
from tests.conftest import png_bytes


def test_local_storage_concurrent_updates_do_not_lose_writes(tmp_path):
    s = LocalStorage(tmp_path)

    def bump():
        for _ in range(25):
            s.update("p1", "panels", "counter", lambda cur: {"n": (cur or {}).get("n", 0) + 1})

    threads = [threading.Thread(target=bump) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert s.get("p1", "panels", "counter")["n"] == 200
    assert s.get("p1", "panels", "counter")["_rev"] == 200


def test_job_dedupe_and_claim(tmp_path):
    s = LocalStorage(tmp_path)
    j1 = s.enqueue_job({"id": "a", "project_id": "p", "type": "t", "payload": {}, "status": "queued",
                        "dedupe_key": "k", "created_at": 1})
    j2 = s.enqueue_job({"id": "b", "project_id": "p", "type": "t", "payload": {}, "status": "queued",
                        "dedupe_key": "k", "created_at": 2})
    assert j1["id"] == j2["id"] == "a"
    claimed = s.claim_job("w", 60)
    assert claimed["id"] == "a" and s.claim_job("w", 60) is None


def test_scoring_matches_spec():
    cfg = get_config()
    overall, verdict = score(cfg, {"character": 8, "quality": 8, "background": 8, "emotion": 8, "continuity": 8, "framing": 8})
    assert overall == 80 and verdict == "pass"
    overall, verdict = score(cfg, {"character": 6, "quality": 10, "background": 10, "emotion": 10, "continuity": 10, "framing": 10})
    assert overall == 88 and verdict == "fail"  # character below 7
    overall, verdict = score(cfg, {"character": 10, "quality": 5, "background": 10, "emotion": 10, "continuity": 10, "framing": 10})
    assert overall == 90 and verdict == "fail"  # anatomy mistake (quality below 7) fails even at 90


def test_cost_prices_text_image_and_cached_separately():
    cfg = get_config()
    u = UsageCounts(input_text=1000, input_image=2000, cached=500, output_image=4000)
    # 500 text @5 + 500 cached text @1.25 + 2000 image @8 + 4000 image out @30
    expected = (500 * 5 + 500 * 1.25 + 2000 * 8 + 4000 * 30) / 1e6
    assert abs(compute_cost(cfg, "gpt-image-2.6-sunburst", u) - expected) < 1e-9


def test_budget_reservations_are_atomic(tmp_path):
    cfg = get_config()
    g = BudgetGuard(LocalStorage(tmp_path), cfg)
    ok, blocked = [], []

    def try_reserve():
        try:
            ok.append(g.reserve(10.0))
        except BudgetExceeded:
            blocked.append(1)

    threads = [threading.Thread(target=try_reserve) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(ok) == 5 and len(blocked) == 7  # $50 / $10


def test_imaging_crop_caption_board():
    img = imaging.crop_to_aspect(png_bytes(size=(1536, 1024)))
    w, h = imaging.open_image(img).size
    assert abs(w / h - 16 / 9) < 0.01
    cap = imaging.caption_panel(img, "S01_SH01", "Theo yawns " * 20, "Wolf!")
    board = imaging.compose_board("S01", [cap] * 5)
    assert imaging.open_image(board).size[0] > imaging.open_image(cap).size[0] * 2


def test_downscale_shrinks_reference_images():
    big = png_bytes(size=(1536, 1024))
    small = imaging.downscale(big, 512, fmt="PNG")
    assert max(imaging.open_image(small).size) == 512


def test_only_characters_listed_in_the_pdf_are_kept():
    from app.domain.models import Plan
    from app.services.planner import keep_listed_characters
    plan = Plan.model_validate({
        "characters": [{"id": "mira", "name": "Mira"}, {"id": "passers-by", "name": "Passers-by"},
                       {"id": "friend", "name": "Childhood friend"}],
        "locations": [{"id": "street", "name": "Street"}],
        "scenes": [{"id": "S01", "shots": [{"id": "S01_SH01", "location_id": "street", "character_ids": ["mira", "passers-by"]}]}],
    })
    text = ("Characters NAME ROLE LOOK Mira The child, 9 Short dark curls, yellow raincoat "
            "Locations LOCATION DESCRIPTION Street ... S01_SH01 Mira, passers-by Street")
    out = keep_listed_characters(plan, text).normalise()
    assert [c.name for c in out.characters] == ["Mira"]
    sh = out.scenes[0].shots[0]
    assert sh.character_ids == ["mira"] and "Background: passers-by" in sh.notes


def test_objects_get_no_expressions_and_mood_becomes_a_note():
    from app.domain.models import Plan
    plan = Plan.model_validate({
        "characters": [{"id": "mira", "name": "Mira", "kind": "person"},
                       {"id": "boat", "name": "The paper boat", "kind": "object"}],
        "locations": [{"id": "street", "name": "Street"}],
        "scenes": [{"id": "S01", "shots": [{"id": "S01_SH01", "location_id": "street", "character_ids": ["mira", "boat"],
                    "reactions": [{"character_id": "mira", "emotion": "happy"}, {"character_id": "boat", "emotion": "playful"}]}]}],
    }).normalise()
    mira, boat = plan.characters
    assert mira.reactions == ["happy"] and boat.reactions == []
    sh = plan.scenes[0].shots[0]
    assert [r.character_id for r in sh.reactions] == ["mira"]
    assert "The paper boat feels playful" in sh.notes and sh.character_ids == ["mira", "boat"]
