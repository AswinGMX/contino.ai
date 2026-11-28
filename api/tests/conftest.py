from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import ROOT_DIR, AppConfig, Settings, get_config
from app.jobs import handlers  # noqa: F401 - registers job handlers
from app.jobs.queue import Worker
from app.services import planner
from app.services.context import Ctx, set_ctx
from app.services.llm import LLM
from app.storage.factory import set_storage
from app.storage.local import LocalStorage

PLAN = {
    "title": "The Boy Who Cried Wolf",
    "style": "soft watercolour storybook",
    "characters": [
        {"id": "theo", "name": "Theo", "description": "10-year-old shepherd boy, curly brown hair, green tunic"},
        {"id": "elder", "name": "Village Elder", "description": "old man, white beard, brown robe"},
    ],
    "locations": [
        {"id": "hillside-pasture", "name": "Hillside Pasture", "description": "green hill, sheep, oak tree"},
        {"id": "village-square", "name": "Village Square", "description": "stone well, thatched huts"},
    ],
    "scenes": [
        {"id": "S01", "title": "Bored on the hill", "shots": [
            {"id": "S01_SH01", "location_id": "hillside-pasture", "character_ids": ["theo"],
             "action": "Theo yawns among the sheep", "dialogue": "", "framing": "wide shot",
             "reactions": [{"character_id": "theo", "emotion": "bored"}]},
            {"id": "S01_SH02", "location_id": "hillside-pasture", "character_ids": ["theo"],
             "action": "Theo cups his hands and shouts", "dialogue": "Wolf! Wolf!", "framing": "close-up",
             "reactions": [{"character_id": "theo", "emotion": "mischievous"}]},
        ]},
        {"id": "S02", "title": "The village runs", "shots": [
            {"id": "S02_SH01", "location_id": "village-square", "character_ids": ["elder"],
             "action": "The elder looks up from the well", "dialogue": "Did you hear that?",
             "framing": "medium shot", "reactions": [{"character_id": "elder", "emotion": "worried"}]},
        ]},
    ],
}


def png_bytes(color=(120, 160, 200), size=(64, 36)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class FakeOpenAI:
    """Mimics the subset of the OpenAI SDK the app uses."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.review_scores: dict[str, dict] = {}
        self.fail_next: list[Exception] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.images = SimpleNamespace(generate=self._gen, edit=self._edit)

    def _maybe_fail(self):
        if self.fail_next:
            raise self.fail_next.pop(0)

    def _chat(self, **kw):
        self.calls.append(("chat", kw))
        self._maybe_fail()
        system = kw["messages"][0]["content"]
        if "Shot Planner" in system:
            content = json.dumps(PLAN)
        elif "art director" in system:
            content = json.dumps({"score": 88, "issues": [{"problem": "slight blur", "fix": "sharpen the face", "severity": "minor"}],
                                  "notes": "fine", "suggested_fix": "sharpen the face"})
        else:
            text = kw["messages"][1]["content"][0]["text"]
            shot_id = json.loads(text.split("## DATA\n", 1)[1])["shot_data"]["shot_id"]
            scores = self.review_scores.get(shot_id, {"character": 9, "quality": 9, "background": 8, "emotion": 8,
                                                      "continuity": 8, "framing": 9})
            content = json.dumps({"shot_id": shot_id, "scores": scores, "notes": "looks right",
                                  "suggested_fix": ""})
        usage = SimpleNamespace(prompt_tokens=1200, completion_tokens=150,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=200, image_tokens=300))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)

    def _img_resp(self):
        usage = SimpleNamespace(input_tokens=500, output_tokens=1000,
                                input_tokens_details=SimpleNamespace(text_tokens=200, image_tokens=300),
                                output_tokens_details=None)
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes((200, 100, 50), (96, 64))).decode(),
                                                     url=None)], usage=usage)

    def _maybe_stream(self, kw, kind):
        resp = self._img_resp()
        if not kw.get("stream"):
            return resp
        img = resp.data[0].b64_json
        return iter([SimpleNamespace(type=f"{kind}.partial_image", b64_json=img, partial_image_index=0),
                     SimpleNamespace(type=f"{kind}.completed", b64_json=img, usage=resp.usage)])

    def _gen(self, **kw):
        self.calls.append(("generate", kw))
        self._maybe_fail()
        return self._maybe_stream(kw, "image_generation")

    def _edit(self, **kw):
        self.calls.append(("edit", kw))
        self._maybe_fail()
        return self._maybe_stream(kw, "image_edit")


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    settings = Settings(openai_api_key="test", embedded_worker=False,
                        data_dir=tmp_path / "data", output_dir=tmp_path / "output",
                        prompts_dir=ROOT_DIR / "prompts", config_file=ROOT_DIR / "config.yaml", api_token="",
                        session_secret="test-secret")
    raw = get_config().model_dump()
    raw["retries"]["backoff_seconds"] = [0, 0]
    cfg = AppConfig.model_validate(raw)
    storage = LocalStorage(settings.data_dir)
    fake = FakeOpenAI()
    ctx = Ctx(storage=storage, cfg=cfg, settings=settings)
    ctx._llm = LLM(storage, cfg, settings, client=fake)  # type: ignore[arg-type]
    set_ctx(ctx)
    set_storage(storage)
    monkeypatch.setattr(planner, "extract_text", lambda _pdf: "Shot planner text " * 10)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.prompts.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.routes.system.get_settings", lambda: settings)
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/auth/login", json={"email": "Tester@Example.com", "name": "Tester"})
        assert r.status_code == 200, r.text
        yield SimpleNamespace(client=client, ctx=ctx, fake=fake, storage=storage, settings=settings,
                              drain=lambda: drain(storage))
    set_ctx(None)
    set_storage(None)


def drain(storage: LocalStorage, limit: int = 500) -> int:
    """Run queued jobs synchronously until the queue is empty."""
    w = Worker(storage, threads=1, poll_interval=0.01, lease_seconds=60)
    n = 0
    while n < limit:
        raw = storage.claim_job(w.worker_id, 60)
        if raw is None:
            return n
        from app.domain.models import Job
        w._run(Job.model_validate(raw))
        n += 1
    raise RuntimeError("job queue did not drain")
