"""Sound effects and video (asynchronous jobs). Offline: in-memory storage, catalog and jobs, and FAKE providers. No paid API is ever called."""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openjarvis.server import auth_routes, hardening
from openjarvis.server.app import create_app
from openjarvis.server.media import sfx as sfx_mod
from openjarvis.server.media import video as video_mod
from openjarvis.server.media import service
from openjarvis.server.media.base import MediaError, MediaResult
from openjarvis.server.media.catalog import MemoryCatalog
from openjarvis.server.media.jobs import MemoryJobs
from openjarvis.server.media.storage import MemoryStorage
from openjarvis.server.media.video import JobState
from tests.server._media_fakes import API_KEY, JWT_SECRET, FakeProvider, FakeStore, tiny_mp3

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class FakeSfx(FakeProvider):
    def __init__(self, cost: float = 0.02, configured: bool = True) -> None:
        super().__init__("sound", data=tiny_mp3(1.0), cost=cost, configured=configured)
        self.id = "fake_sfx"


class FakeVideo:
    """A video provider that never calls anything: the test decides what each poll says."""

    kind, id = "video", "fake_video"

    def __init__(self, cost: float = 0.5, configured: bool = True) -> None:
        self._cost, self._configured = cost, configured
        self.started: List[dict] = []
        self.state = JobState("running")
        self.fetch_calls = 0
        self.status_error: Optional[Exception] = None
        self.start_error: Optional[Exception] = None
        self.data = MP4

    def configured(self) -> bool:
        return self._configured

    def estimate_cost_usd(self, **_: Any) -> float:
        return self._cost

    async def start(self, *, prompt: str, seconds: int, height: int) -> str:
        if self.start_error:
            raise self.start_error
        self.started.append({"prompt": prompt, "seconds": seconds, "height": height})
        return f"prov-{len(self.started)}"

    async def status(self, provider_job_id: str) -> JobState:
        if self.status_error:
            raise self.status_error
        return self.state

    async def fetch(self, provider_job_id: str) -> MediaResult:
        self.fetch_calls += 1
        await asyncio.sleep(0)
        return MediaResult("video", "video/mp4", self.data, self.id)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in ("MEDIA_ENABLED", "DAILY_MEDIA_SPEND_CAP_USD", "MEDIA_GLOBAL_DAILY_CAP_USD", "MEDIA_USER_QUOTA_BYTES", "R2_BUCKET", "MEDIA_SFX_PROVIDER",
              "MEDIA_VIDEO_PROVIDER", "VIDEO_MAX_SECONDS", "VIDEO_MAX_HEIGHT", "MEDIA_SFX_MAX_SECONDS", "RL_MEDIA_USER_MAX", "RL_MEDIA_READ_USER_MAX"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("OPENJARVIS_API_KEY", API_KEY)
    monkeypatch.setattr(hardening, "LIMITER", hardening.RateLimiter())


def on(monkeypatch, cap="50", glob="50"):
    monkeypatch.setenv("MEDIA_ENABLED", "1")
    monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", cap)
    monkeypatch.setenv("MEDIA_GLOBAL_DAILY_CAP_USD", glob)


def hdr(uid):
    return {"Authorization": "Bearer " + auth_routes.create_token(uid, uid + "@x.io")}


@pytest.fixture
def world(monkeypatch):
    store, storage, catalog, jobs = FakeStore(), MemoryStorage(), MemoryCatalog(), MemoryJobs()
    app = create_app(MagicMock(), "m", api_key=API_KEY, require_auth=True)
    app.state.session_store, app.state.media_storage, app.state.media_catalog, app.state.media_jobs = store, storage, catalog, jobs
    sfx, vid = FakeSfx(), FakeVideo()
    monkeypatch.setitem(sfx_mod.PROVIDERS, "fake_sfx", sfx)
    monkeypatch.setitem(video_mod.PROVIDERS, "fake_video", vid)
    monkeypatch.setenv("MEDIA_SFX_PROVIDER", "fake_sfx")
    monkeypatch.setenv("MEDIA_VIDEO_PROVIDER", "fake_video")
    client = TestClient(app, raise_server_exceptions=False)
    return type("W", (), {"client": client, "store": store, "storage": storage, "catalog": catalog, "jobs": jobs, "sfx": sfx, "vid": vid})()


def post_sfx(w, uid="alice", **body):
    return w.client.post("/api/media/sfx", json={"prompt": "a door slamming", **body}, headers=hdr(uid))


def post_video(w, uid="alice", **body):
    return w.client.post("/api/media/video", json={"prompt": "a fox running", **body}, headers=hdr(uid))


def poll(w, job_id, uid="alice"):
    return w.client.get(f"/api/media/jobs/{job_id}", headers=hdr(uid))


# ── sound effects ───────────────────────────────────────────────────────────

class TestSoundEffects:
    def test_a_sound_effect_is_made_saved_and_counted(self, world, monkeypatch):
        on(monkeypatch)
        r = post_sfx(world, seconds=4)
        assert r.status_code == 200, r.text
        item = r.json()["item"]
        assert item["kind"] == "sound" and item["provider"] == "sfx:fake_sfx" and item["prompt"] == "a door slamming"
        assert world.sfx.calls == [{"prompt": "a door slamming", "seconds": 4}]
        assert world.store.spent("media:alice") == pytest.approx(0.02)
        row = world.catalog.items[item["id"]]
        assert row.user_id == "alice" and row.storage_key.endswith(".mp3")
        assert '"user_id"' not in r.text and "storage_key" not in r.text

    def test_it_needs_a_login_and_the_media_switch_and_a_budget(self, world, monkeypatch):
        assert world.client.post("/api/media/sfx", json={"prompt": "x"}).status_code == 401
        assert post_sfx(world).status_code == 403                                      # MEDIA_ENABLED is off
        monkeypatch.setenv("MEDIA_ENABLED", "1")
        assert post_sfx(world).status_code == 403                                      # no budget
        assert world.sfx.calls == []

    def test_with_no_provider_selected_it_answers_503_and_calls_nothing(self, world, monkeypatch):
        on(monkeypatch)
        monkeypatch.delenv("MEDIA_SFX_PROVIDER")
        r = post_sfx(world)
        assert r.status_code == 503 and r.json()["error"] == "media_not_configured"
        assert world.sfx.calls == []

    def test_the_length_limit_is_hard_and_shown(self, world, monkeypatch):
        on(monkeypatch)
        r = post_sfx(world, seconds=11)
        assert r.status_code == 422 and r.json()["error"] == "sfx_limit" and r.json()["max_seconds"] == 10
        assert post_sfx(world, seconds=0).status_code == 422
        monkeypatch.setenv("MEDIA_SFX_MAX_SECONDS", "3")
        assert post_sfx(world, seconds=4).status_code == 422
        assert post_sfx(world, seconds=3).status_code == 200
        monkeypatch.setenv("MEDIA_SFX_MAX_SECONDS", "999")                             # the configuration cannot lift the ceiling
        assert sfx_mod.max_seconds() == sfx_mod.HARD_MAX_SECONDS
        assert len(world.sfx.calls) == 1

    def test_an_empty_or_long_description_is_refused(self, world, monkeypatch):
        on(monkeypatch)
        assert post_sfx(world, prompt="   ").status_code == 422
        assert post_sfx(world, prompt="x" * (sfx_mod.MAX_PROMPT_CHARS + 1)).status_code == 422
        assert world.sfx.calls == []

    def test_the_daily_cap_stops_the_provider_call(self, world, monkeypatch):
        on(monkeypatch, cap="0.03")
        assert post_sfx(world).status_code == 200                                      # 0.02 of 0.03
        r = post_sfx(world)
        assert r.status_code == 429 and r.json()["error"] == "daily_media_cap_reached"
        assert len(world.sfx.calls) == 1

    def test_a_provider_failure_costs_nothing_and_saves_nothing(self, world, monkeypatch):
        on(monkeypatch)

        async def boom(**_):
            raise MediaError("The sound could not be made.", 502)

        monkeypatch.setattr(world.sfx, "generate", boom)
        r = post_sfx(world)
        assert r.status_code == 502 and world.catalog.items == {} and world.store.spent("media:alice") == 0

    def test_regenerating_a_sound_effect_uses_the_effects_provider(self, world, monkeypatch):
        on(monkeypatch)
        item = post_sfx(world).json()["item"]
        r = world.client.post(f"/api/media/items/{item['id']}/regenerate", json={"prompt": "a heavier door"}, headers=hdr("alice"))
        assert r.status_code == 200, r.text
        assert world.sfx.calls[-1]["prompt"] == "a heavier door" and r.json()["item"]["provider"] == "sfx:fake_sfx"

    def test_status_reports_availability_limits_and_estimates(self, world, monkeypatch):
        on(monkeypatch)
        s = world.client.get("/api/media/status", headers=hdr("alice")).json()
        assert s["sfx"] is True and s["limits"]["sfx"]["max_seconds"] == 10 and s["estimates"]["sfx"] == pytest.approx(0.02)
        monkeypatch.delenv("MEDIA_SFX_PROVIDER")
        s = world.client.get("/api/media/status", headers=hdr("alice")).json()
        assert s["sfx"] is False and "not available" in s["sfx_note"]


# ── video ───────────────────────────────────────────────────────────────

class TestVideoJobs:
    def test_creating_a_video_returns_a_job_id_at_once_and_makes_nothing_yet(self, world, monkeypatch):
        on(monkeypatch)
        r = post_video(world, seconds=4, height=480)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["job"]["status"] == "running" and body["job"]["job_id"] and body["limits"]["max_seconds"] == 5
        assert world.vid.started == [{"prompt": "a fox running", "seconds": 4, "height": 480}]
        assert world.catalog.items == {}
        assert world.store.spent("media:alice") == pytest.approx(0.5)               # the estimated cost counts when the provider accepts the job
        assert "provider_job_id" not in r.text and "prov-1" not in r.text and '"user_id"' not in r.text

    def test_polling_advances_the_job_and_a_finished_video_is_saved_once(self, world, monkeypatch):
        on(monkeypatch)
        job_id = post_video(world).json()["job"]["job_id"]
        first = poll(world, job_id).json()
        assert first["job"]["status"] == "running" and "item" not in first and world.catalog.items == {}
        world.vid.state = JobState("succeeded")
        done = poll(world, job_id).json()
        assert done["job"]["status"] == "succeeded" and done["item"]["kind"] == "video" and done["item"]["mime_type"] == "video/mp4"
        assert done["item"]["url"].startswith("memory://") and done["item"]["duration_seconds"] == 5.0
        again = poll(world, job_id).json()
        assert again["item"]["id"] == done["item"]["id"]
        assert len(world.catalog.items) == 1 and world.vid.fetch_calls == 1
        lib = world.client.get("/api/media/library?kind=video", headers=hdr("alice")).json()
        assert [i["id"] for i in lib["items"]] == [done["item"]["id"]]

    def test_two_polls_at_the_same_moment_save_one_file(self, world, monkeypatch):
        on(monkeypatch)
        job_id = post_video(world).json()["job"]["job_id"]
        world.vid.state = JobState("succeeded")

        async def both():
            from openjarvis.server.media.service import Ctx

            ctx = Ctx("alice", "10.0.0.1", world.store, world.catalog, world.storage, world.jobs)
            return await asyncio.gather(service.get_job(ctx, job_id), service.get_job(ctx, job_id))

        a, b = asyncio.run(both())
        assert a["item"]["id"] == b["item"]["id"] and len(world.catalog.items) == 1 and world.vid.fetch_calls == 1

    def test_a_failed_job_reports_a_safe_message(self, world, monkeypatch):
        on(monkeypatch)
        job_id = post_video(world).json()["job"]["job_id"]
        world.vid.state = JobState("failed", "The video was blocked by the provider.")
        j = poll(world, job_id).json()["job"]
        assert j["status"] == "failed" and j["error"] == "The video was blocked by the provider." and world.catalog.items == {}

    def test_the_length_and_resolution_limits_are_hard(self, world, monkeypatch):
        on(monkeypatch)
        r = post_video(world, seconds=6)
        assert r.status_code == 422 and r.json()["error"] == "video_limit" and r.json()["max_seconds"] == 5 and r.json()["max_height"] == 720
        assert post_video(world, height=1080).status_code == 422
        assert post_video(world, seconds=0).status_code == 422
        assert world.vid.started == [] and world.store.spent("media:alice") == 0          # refused BEFORE any provider call or spend
        assert post_video(world, seconds=5, height=720).status_code == 202

    def test_the_limits_come_from_the_configuration_and_cannot_pass_the_ceiling(self, world, monkeypatch):
        on(monkeypatch)
        monkeypatch.setenv("VIDEO_MAX_SECONDS", "3")
        monkeypatch.setenv("VIDEO_MAX_HEIGHT", "480")
        assert post_video(world, seconds=4).status_code == 422 and post_video(world, height=720).status_code == 422
        assert post_video(world, seconds=3, height=480).status_code == 202
        monkeypatch.setenv("VIDEO_MAX_SECONDS", "999")
        monkeypatch.setenv("VIDEO_MAX_HEIGHT", "9999")
        assert video_mod.max_seconds() == video_mod.HARD_MAX_SECONDS == 10 and video_mod.max_height() == video_mod.HARD_MAX_HEIGHT == 1080
        s = world.client.get("/api/media/status", headers=hdr("alice")).json()
        assert s["limits"]["video"]["max_seconds"] == 10 and s["limits"]["video"]["max_height"] == 1080

    def test_a_user_can_have_only_a_few_videos_running(self, world, monkeypatch):
        on(monkeypatch)
        for _ in range(video_mod.MAX_ACTIVE_JOBS_PER_USER):
            assert post_video(world).status_code == 202
        r = post_video(world)
        assert r.status_code == 429 and r.json()["error"] == "too_many_jobs"
        assert len(world.vid.started) == video_mod.MAX_ACTIVE_JOBS_PER_USER
        assert post_video(world, uid="bob").status_code == 202                             # someone else is not affected

    def test_the_daily_cap_stops_a_video_before_the_provider_is_called(self, world, monkeypatch):
        on(monkeypatch, cap="0.6")
        assert post_video(world).status_code == 202                                       # 0.5 of 0.6
        r = post_video(world)
        assert r.status_code == 429 and r.json()["error"] == "daily_media_cap_reached"
        assert len(world.vid.started) == 1

    def test_the_global_ceiling_applies_to_everyone_together(self, world, monkeypatch):
        on(monkeypatch, cap="50", glob="0.6")
        assert post_video(world, uid="alice").status_code == 202
        assert post_video(world, uid="bob").status_code == 429
        assert len(world.vid.started) == 1

    def test_a_full_library_stops_a_video_before_it_is_paid_for(self, world, monkeypatch):
        on(monkeypatch)
        monkeypatch.setenv("MEDIA_USER_QUOTA_BYTES", "1")
        world.catalog.items["x"] = MagicMock(user_id="alice", size_bytes=10)
        r = post_video(world)
        assert r.status_code == 413 and world.vid.started == []

    def test_a_video_that_cannot_be_saved_fails_the_job_with_the_reason(self, world, monkeypatch):
        on(monkeypatch)
        job_id = post_video(world).json()["job"]["job_id"]
        monkeypatch.setenv("MEDIA_USER_QUOTA_BYTES", "10")                                # room for nothing once the video arrives
        world.vid.state = JobState("succeeded")
        j = poll(world, job_id).json()["job"]
        assert j["status"] == "failed" and "full" in j["error"].lower() and world.catalog.items == {}

    def test_without_a_provider_it_answers_503_and_starts_nothing(self, world, monkeypatch):
        on(monkeypatch)
        monkeypatch.delenv("MEDIA_VIDEO_PROVIDER")
        r = post_video(world)
        assert r.status_code == 503 and r.json()["error"] == "media_not_configured" and world.jobs.jobs == {}
        monkeypatch.setenv("MEDIA_VIDEO_PROVIDER", "fake_video")
        world.vid._configured = False
        assert post_video(world).status_code == 503

    def test_a_provider_that_will_not_start_creates_no_job_and_no_spend(self, world, monkeypatch):
        on(monkeypatch)
        world.vid.start_error = MediaError("The provider is busy.", 502)
        r = post_video(world)
        assert r.status_code == 502 and world.jobs.jobs == {} and world.store.spent("media:alice") == 0

    def test_an_unreachable_provider_leaves_the_job_running_until_it_times_out(self, world, monkeypatch):
        on(monkeypatch)
        job_id = post_video(world).json()["job"]["job_id"]
        world.vid.status_error = MediaError("down", 502)
        assert poll(world, job_id).json()["job"]["status"] == "running"
        monkeypatch.setattr(service, "JOB_TIMEOUT_S", -1)
        j = poll(world, job_id).json()["job"]
        assert j["status"] == "failed" and "too long" in j["error"]

    def test_jobs_belong_to_one_user(self, world, monkeypatch):
        on(monkeypatch)
        job_id = post_video(world, uid="alice").json()["job"]["job_id"]
        assert poll(world, job_id, uid="bob").status_code == 404
        assert poll(world, "not-a-uuid", uid="alice").status_code == 404
        assert poll(world, "0a1b2c3d-0000-4000-8000-0000000000ff", uid="alice").status_code == 404

    def test_everything_needs_a_login_and_the_media_switch(self, world, monkeypatch):
        assert world.client.post("/api/media/video", json={"prompt": "x"}).status_code == 401
        assert world.client.get("/api/media/jobs/0a1b2c3d-0000-4000-8000-0000000000ff").status_code == 401
        assert post_video(world).status_code == 403
        assert poll(world, "0a1b2c3d-0000-4000-8000-0000000000ff").status_code == 403
        assert world.vid.started == []

    def test_video_items_cannot_be_edited_or_regenerated(self, world, monkeypatch):
        on(monkeypatch)
        job_id = post_video(world).json()["job"]["job_id"]
        world.vid.state = JobState("succeeded")
        item = poll(world, job_id).json()["item"]
        assert world.client.post(f"/api/media/items/{item['id']}/edit", json={"operation": "trim", "params": {}}, headers=hdr("alice")).status_code == 400
        assert world.client.post(f"/api/media/items/{item['id']}/regenerate", json={"prompt": "again"}, headers=hdr("alice")).status_code == 400
        assert world.client.delete(f"/api/media/items/{item['id']}", headers=hdr("alice")).status_code == 200            # it can be deleted

    def test_status_reports_video_limits_and_availability(self, world, monkeypatch):
        on(monkeypatch)
        s = world.client.get("/api/media/status", headers=hdr("alice")).json()
        assert s["video"] is True and s["limits"]["video"] == video_mod.limits() and s["estimates"]["video"] == pytest.approx(0.5)
        monkeypatch.delenv("MEDIA_VIDEO_PROVIDER")
        s = world.client.get("/api/media/status", headers=hdr("alice")).json()
        assert s["video"] is False and s["video_note"] == "Video generation is not available."


class TestNoRealProviders:
    def test_the_default_providers_are_placeholders_that_are_never_configured(self):
        assert sfx_mod.DEFAULT_PROVIDER == "none" and not sfx_mod.get_provider({}).configured()
        assert video_mod.DEFAULT_PROVIDER == "none" and not video_mod.get_provider({}).configured()
        assert set(sfx_mod.PROVIDERS) >= {"none"} and set(video_mod.PROVIDERS) >= {"none"}

    def test_no_media_module_makes_a_network_call_for_sfx_or_video(self):
        from pathlib import Path

        pkg = Path(sfx_mod.__file__).parent
        for name in ("sfx.py", "video.py", "jobs.py"):
            src = (pkg / name).read_text(encoding="utf-8")
            import re

            assert not re.search(r"^\s*(import|from)\s+(httpx|requests|urllib|aiohttp|socket)", src, re.M), f"{name} imports a network library"


# ── the web page ───────────────────────────────────────────────────────────

class TestUi:
    def _html(self, world):
        r = world.client.get("/")
        assert r.status_code == 200, r.status_code
        return r.text

    def test_the_page_has_the_shortcuts_the_limits_line_and_the_video_follow_up(self, world):
        html = self._html(world)
        assert 'id="media-limits"' in html and "function limitsText" in html and "Sound FX: up to" in html
        assert "(sfx|video)" in html and "async function followVideoJob" in html and "/api/media/jobs/" in html
        assert "(image|sound)" in html and ">Video<" in html                             # the old shortcuts and the Video filter are still there
        assert "createElement(it.kind === 'image' ? 'img' : it.kind === 'video' ? 'video' : 'audio')" in html
        send = html[html.index("async function sendMedia"):html.index("// CHANGE 3")]
        assert "data:" not in send and "createElement('img')" not in send                  # the file is never put in the chat

    def test_the_pages_scripts_still_parse(self, world, tmp_path):
        import re
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            pytest.skip("node is not installed")
        html = self._html(world)
        scripts = [m for m in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S) if m.strip()]
        assert scripts
        for i, code in enumerate(scripts):
            f = tmp_path / f"s{i}.js"
            f.write_text(code, encoding="utf-8")
            r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
            if r.returncode != 0 and "import" in code[:200]:
                continue                                                                  # a module script: node --check wants .mjs
            assert r.returncode == 0, r.stderr[:400]
