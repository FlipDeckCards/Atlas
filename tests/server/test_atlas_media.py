"""Media generation (images and speech): switch, media spend cap, rate limits, errors, keys. Video is not offered.

Offline: the database is an in-memory fake and the provider is either a fake or Cloudflare's API mocked with respx.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.server import atlas_ui, auth_routes, hardening
from openjarvis.server.auth_middleware import AuthMiddleware
from openjarvis.server.media import cloudflare as cf
from openjarvis.server.media import image as image_mod
from openjarvis.server.media import sound as sound_mod
from openjarvis.server.media.base import MediaError, MediaResult
from openjarvis.server.media.catalog import MemoryCatalog
from openjarvis.server.media.storage import MemoryStorage
from openjarvis.server.media.routes import router as media_router

API_KEY = "test-system-key-0123456789"
JWT_SECRET = "a-long-random-test-secret-0123456789abcdef"
CF_ACCOUNT = "acct0123456789abcdef"
CF_TOKEN = "cf-token-SECRET-0123456789"
PNG = b"\xff\xd8\xff\xe0fakejpegbytes"
MP3 = b"ID3fakemp3bytes"


class FakeStore:
    def __init__(self) -> None:
        self.spend: dict = {}

    async def get_spend_today(self, user_id, day):
        return self.spend.get((user_id, day), 0.0)

    async def add_spend(self, user_id, day, usd):
        self.spend[(user_id, day)] = self.spend.get((user_id, day), 0.0) + usd

    def spent(self, key):
        return self.spend.get((key, hardening.utc_day()), 0.0)


class FakeProvider:
    def __init__(self, kind, cost=0.01, configured=True, fail=None):
        self.kind, self.id = kind, "fake_" + kind
        self._cost, self._configured, self._fail = cost, configured, fail
        self.calls = []

    def configured(self):
        return self._configured

    def estimate_cost_usd(self, **request):
        return self._cost

    async def generate(self, **request):
        self.calls.append(request)
        if self._fail:
            raise self._fail
        data = PNG if self.kind == "image" else MP3
        return MediaResult(
            self.kind,
            "image/jpeg" if self.kind == "image" else "audio/mpeg",
            data,
            self.id,
        )


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in (
        "MEDIA_ENABLED",
        "DAILY_MEDIA_SPEND_CAP_USD",
        "MEDIA_GLOBAL_DAILY_CAP_USD",
        "DAILY_SPEND_CAP_USD",
        "RL_MEDIA_USER_MAX",
        "RL_MEDIA_IP_MAX",
        "RL_CHAT_USER_MAX",
        "MEDIA_IMAGE_PROVIDER",
        "MEDIA_SOUND_PROVIDER",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("OPENJARVIS_API_KEY", API_KEY)
    monkeypatch.setattr(hardening, "LIMITER", hardening.RateLimiter())


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(media_router)
    app.include_router(atlas_ui.router)
    app.state.session_store = store
    app.state.media_storage = MemoryStorage()
    app.state.media_catalog = MemoryCatalog()
    app.add_middleware(AuthMiddleware, api_key=API_KEY)
    return TestClient(app)


@pytest.fixture
def img(monkeypatch):
    p = FakeProvider("image")
    monkeypatch.setitem(image_mod.PROVIDERS, image_mod.DEFAULT_PROVIDER, p)
    return p


@pytest.fixture
def snd(monkeypatch):
    p = FakeProvider("sound", cost=0.002)
    monkeypatch.setitem(sound_mod.PROVIDERS, sound_mod.DEFAULT_PROVIDER, p)
    return p


def on(monkeypatch, cap="0.05", glob=None):
    monkeypatch.setenv("MEDIA_ENABLED", "1")
    monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", cap)
    if glob is not None:
        monkeypatch.setenv("MEDIA_GLOBAL_DAILY_CAP_USD", glob)


def saved_bytes(client, resp):
    """The bytes stored for the item an answer names (the answer itself carries no file)."""
    item = resp.json()["item"]
    row = client.app.state.media_catalog.items[item["id"]]
    return client.app.state.media_storage.objects[row.storage_key][0]


H = {"Authorization": "Bearer " + API_KEY}


def post_image(client, prompt="a red fox", **extra):
    return client.post("/api/media/image", json=dict(prompt=prompt, **extra), headers=H)


def post_sound(client, text="hello there", **extra):
    return client.post("/api/media/sound", json=dict(text=text, **extra), headers=H)


# ── the switch and the media cap ────────────────────────────────────────────────────────────────


class TestSwitchAndCap:
    def test_flag_off_by_default_blocks_everything_without_calling_a_provider(
        self, client, img, snd, monkeypatch
    ):
        monkeypatch.setenv(
            "DAILY_MEDIA_SPEND_CAP_USD", "5"
        )  # a budget alone is not enough
        for r in (post_image(client), post_sound(client)):
            assert r.status_code == 403 and r.json()["error"] == "media_disabled"
        assert img.calls == [] and snd.calls == []

    def test_cap_zero_or_unset_means_media_is_off_even_with_the_flag_on(
        self, client, img, snd, monkeypatch
    ):
        monkeypatch.setenv("MEDIA_ENABLED", "1")
        for cap in (None, "0", "0.0", "junk", ""):
            if cap is None:
                monkeypatch.delenv("DAILY_MEDIA_SPEND_CAP_USD", raising=False)
            else:
                monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", cap)
            for r in (post_image(client), post_sound(client)):
                assert r.status_code == 403 and r.json()["error"] == "media_disabled", (
                    cap
                )
        assert img.calls == [] and snd.calls == []

    def test_defaults(self):
        from openjarvis.server.media import (
            media_cap_usd,
            media_enabled,
            media_global_cap_usd,
        )

        assert media_enabled({}) is False and media_cap_usd({}) == 0.0
        assert media_global_cap_usd({}) == 1.0
        assert (
            media_enabled({"MEDIA_ENABLED": "yes"}) is True
            and media_enabled({"MEDIA_ENABLED": "0"}) is False
        )

    def test_works_when_on_and_records_the_cost_under_a_media_key(
        self, client, img, snd, store, monkeypatch
    ):
        on(monkeypatch)
        r = post_image(client)
        assert r.status_code == 200
        j = r.json()
        assert j["success"] and j["item"]["kind"] == "image" and j["item"]["mime_type"] == "image/jpeg"
        assert "data_base64" not in j and "storage_key" not in j["item"] and "user_id" not in j["item"]
        assert saved_bytes(client, r) == PNG
        s = post_sound(client)
        assert s.status_code == 200 and saved_bytes(client, s) == MP3 and s.json()["item"]["mime_type"] == "audio/mpeg"
        assert (
            round(store.spent("media:system"), 4) == 0.012
            and round(store.spent("media:__all__"), 4) == 0.012
        )
        assert store.spent("system") == 0.0  # chat's spend is separate

    def test_cap_reached_returns_429_without_calling_the_provider(
        self, client, img, store, monkeypatch
    ):
        on(monkeypatch, cap="0.025")
        assert post_image(client).status_code == 200  # 0.01
        assert post_image(client).status_code == 200  # 0.02
        r = post_image(client)  # 0.02 + 0.01 > 0.025
        assert r.status_code == 429 and r.json()["error"] == "daily_media_cap_reached"
        assert len(img.calls) == 2

    def test_cap_is_per_user_and_there_is_a_global_ceiling(
        self, client, img, store, monkeypatch
    ):
        on(monkeypatch, cap="0.05", glob="0.03")
        alice = {
            "Authorization": "Bearer " + auth_routes.create_token("alice", "a@x.io")
        }
        bob = {"Authorization": "Bearer " + auth_routes.create_token("bob", "b@x.io")}
        assert (
            client.post(
                "/api/media/image", json={"prompt": "x"}, headers=alice
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/media/image", json={"prompt": "x"}, headers=bob
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/media/image", json={"prompt": "x"}, headers=alice
            ).status_code
            == 200
        )
        r = client.post(
            "/api/media/image", json={"prompt": "x"}, headers=bob
        )  # all users together would pass 0.03
        assert r.status_code == 429 and len(img.calls) == 3

    def test_chat_cap_and_media_cap_are_independent(
        self, client, img, store, monkeypatch
    ):
        monkeypatch.setenv("DAILY_SPEND_CAP_USD", "0")  # chat blocked
        on(monkeypatch)
        assert post_image(client).status_code == 200  # media still works
        monkeypatch.delenv("MEDIA_ENABLED")
        monkeypatch.setenv("DAILY_SPEND_CAP_USD", "1")
        assert (
            post_image(client).status_code == 403
        )  # media off does not depend on the chat cap

    def test_not_configured_provider_answers_503_and_is_not_called(
        self, client, monkeypatch
    ):
        on(monkeypatch)
        p = FakeProvider("image", configured=False)
        monkeypatch.setitem(image_mod.PROVIDERS, image_mod.DEFAULT_PROVIDER, p)
        r = post_image(client)
        assert (
            r.status_code == 503
            and r.json()["error"] == "media_not_configured"
            and p.calls == []
        )

    def test_status_reports_what_is_on_and_that_video_is_not_available(
        self, client, img, snd, monkeypatch
    ):
        s = client.get("/api/media/status", headers=H).json()
        assert s["enabled"] is False and s["image"] is False and s["video"] is False
        on(monkeypatch)
        s = client.get("/api/media/status", headers=H).json()
        assert s["enabled"] is True and s["generation"] is True and s["image"] is True and s["sound"] is True
        assert s["video"] is False and s["video_note"] == "Video generation is not available."
        assert s["estimates"]["image"] > 0


# ── protection shared with the chat ─────────────────────────────────────────────────────────────


class TestProtection:
    def test_needs_a_token(self, client, img, monkeypatch):
        on(monkeypatch)
        assert client.post("/api/media/image", json={"prompt": "x"}).status_code == 401
        assert (
            client.post(
                "/api/media/sound",
                json={"text": "x"},
                headers={"Authorization": "Bearer wrong"},
            ).status_code
            == 401
        )
        assert img.calls == []

    def test_per_user_rate_limit(self, client, img, monkeypatch):
        on(monkeypatch, cap="5")
        monkeypatch.setenv("RL_MEDIA_USER_MAX", "2")
        codes = [post_image(client).status_code for _ in range(4)]
        assert codes == [200, 200, 429, 429] and len(img.calls) == 2
        assert post_image(client).headers.get("retry-after")

    def test_input_caps(self, client, img, snd, monkeypatch):
        on(monkeypatch, cap="5")
        assert (
            post_image(
                client, prompt="x" * (image_mod.MAX_PROMPT_CHARS + 1)
            ).status_code
            == 422
        )
        assert post_image(client, prompt="").status_code == 422
        assert (
            post_sound(client, text="x" * (sound_mod.MAX_TEXT_CHARS + 1)).status_code
            == 422
        )
        assert img.calls == [] and snd.calls == []

    def test_steps_and_language_are_kept_in_range(self, client, img, snd, monkeypatch):
        on(monkeypatch, cap="5")
        post_image(client, steps=99)
        post_image(client, steps=-3)
        assert (
            post_image(client, steps="nope").status_code == 422
        )  # not a number: refused
        post_image(client)
        assert [c["steps"] for c in img.calls] == [8, 1, 4]
        post_sound(client, lang="klingon")
        post_sound(client, lang="FR")
        assert [c["lang"] for c in snd.calls] == ["en", "fr"]

    def test_whitespace_in_prompts_is_tidied_and_blank_ones_refused(
        self, client, img, monkeypatch
    ):
        on(monkeypatch, cap="5")
        post_image(client, prompt="  a   red \n fox ")
        assert img.calls[0]["prompt"] == "a red fox"
        assert post_image(client, prompt="   \n ").status_code == 422

    def test_video_without_a_provider_is_refused_and_nothing_is_written_to_disk(
        self, client, monkeypatch
    ):
        on(monkeypatch)
        r = client.post("/api/media/video", json={"prompt": "x"}, headers=H)
        assert r.status_code in (503, 500) and r.status_code != 202        # no provider is selected: nothing is started
        pkg = Path(cf.__file__).parent
        src = "\n".join(p.read_text(encoding="utf-8") for p in pkg.glob("*.py") if p.name != "edits.py")
        for needle in ("open(", "write_bytes", "write_text", "tempfile", "os.makedirs", "shutil"):
            assert needle not in src, needle
        edit_src = (pkg / "edits.py").read_text(encoding="utf-8")        # Image.open reads from memory; nothing is written to disk
        for needle in ("write_bytes", "write_text", "tempfile", "os.makedirs", "NamedTemporaryFile", "open(\"w"):
            assert needle not in edit_src, needle


# ── errors and keys ─────────────────────────────────────────────────────────────────────────────


class TestErrorsAndKeys:
    def test_provider_errors_show_only_a_fixed_sentence(self, client, monkeypatch):
        on(monkeypatch, cap="5")
        monkeypatch.setitem(
            image_mod.PROVIDERS,
            image_mod.DEFAULT_PROVIDER,
            FakeProvider(
                "image",
                fail=MediaError(
                    "The media provider could not make that. Please try again.", 502
                ),
            ),
        )
        r = post_image(client)
        assert r.status_code == 502 and r.json()["error"] == "media_failed"
        monkeypatch.setitem(
            sound_mod.PROVIDERS,
            sound_mod.DEFAULT_PROVIDER,
            FakeProvider(
                "sound", fail=RuntimeError("secret-internal-detail https://x/?key=abc")
            ),
        )
        r2 = post_sound(client)
        assert (
            r2.status_code == 502
            and "secret-internal-detail" not in r2.text
            and "key=abc" not in r2.text
        )

    def test_a_failed_call_costs_nothing(self, client, store, monkeypatch):
        on(monkeypatch, cap="5")
        monkeypatch.setitem(
            image_mod.PROVIDERS,
            image_mod.DEFAULT_PROVIDER,
            FakeProvider("image", fail=RuntimeError("boom")),
        )
        post_image(client)
        assert store.spend == {}

    @respx.mock
    def test_cloudflare_image_call_uses_the_token_only_in_the_header_and_the_key_never_reaches_the_browser_or_logs(
        self, client, store, monkeypatch, caplog
    ):
        on(monkeypatch, cap="5")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", CF_ACCOUNT)
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", CF_TOKEN)
        url = f"{cf.API_BASE}/{CF_ACCOUNT}/ai/run/@cf/black-forest-labs/flux-1-schnell"
        route = respx.post(url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {"image": base64.b64encode(PNG).decode()},
                    "success": True,
                },
            )
        )
        with caplog.at_level(logging.DEBUG):
            r = post_image(client, prompt="a red fox", steps=3)
        assert r.status_code == 200 and saved_bytes(client, r) == PNG
        sent = route.calls[0].request
        assert sent.headers["authorization"] == "Bearer " + CF_TOKEN
        assert CF_TOKEN not in str(sent.url) and CF_TOKEN not in sent.content.decode()
        import json as _json

        assert _json.loads(sent.content) == {"prompt": "a red fox", "steps": 3}
        assert CF_TOKEN not in r.text and CF_TOKEN not in str(dict(r.headers))
        assert CF_TOKEN not in caplog.text and CF_ACCOUNT not in r.text

    @respx.mock
    def test_cloudflare_failures_are_logged_without_the_key_and_answered_with_a_fixed_sentence(
        self, client, store, monkeypatch, caplog
    ):
        on(monkeypatch, cap="5")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", CF_ACCOUNT)
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", CF_TOKEN)
        url = f"{cf.API_BASE}/{CF_ACCOUNT}/ai/run/@cf/black-forest-labs/flux-1-schnell"
        for response, expect in (
            (
                httpx.Response(
                    401, json={"errors": [{"message": f"bad token {CF_TOKEN}"}]}
                ),
                502,
            ),
            (httpx.Response(429, text="quota " + CF_TOKEN), 503),
            (httpx.Response(500, text="oops " + CF_TOKEN), 502),
        ):
            respx.post(url).mock(return_value=response)
            with caplog.at_level(logging.DEBUG):
                r = post_image(client)
            assert r.status_code == expect and CF_TOKEN not in r.text
        respx.post(url).mock(
            side_effect=httpx.ConnectError(f"cannot connect to {url} with {CF_TOKEN}")
        )
        with caplog.at_level(logging.DEBUG):
            r = post_image(client)
        assert (
            r.status_code == 502
            and CF_TOKEN not in r.text
            and CF_TOKEN not in caplog.text
        )
        assert store.spend == {}

    @respx.mock
    def test_cloudflare_speech_accepts_a_json_or_a_binary_answer(
        self, client, monkeypatch
    ):
        on(monkeypatch, cap="5")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", CF_ACCOUNT)
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", CF_TOKEN)
        url = f"{cf.API_BASE}/{CF_ACCOUNT}/ai/run/@cf/myshell-ai/melotts"
        route = respx.post(url).mock(
            return_value=httpx.Response(
                200, json={"result": {"audio": base64.b64encode(MP3).decode()}}
            )
        )
        r = post_sound(client, text="hi", lang="fr")
        assert r.status_code == 200 and saved_bytes(client, r) == MP3
        import json as _json

        assert _json.loads(route.calls[0].request.content) == {
            "prompt": "hi",
            "lang": "fr",
        }
        respx.post(url).mock(
            return_value=httpx.Response(
                200, content=MP3, headers={"content-type": "audio/mpeg"}
            )
        )
        assert saved_bytes(client, post_sound(client)) == MP3
        respx.post(url).mock(return_value=httpx.Response(200, json={"result": {}}))
        assert post_sound(client).status_code == 502

    def test_without_cloudflare_settings_nothing_is_called(self, client, monkeypatch):
        on(monkeypatch)
        with respx.mock(assert_all_called=False) as m:
            r = post_image(client)
        assert r.status_code == 503 and len(m.calls) == 0

    def test_cost_estimates_follow_cloudflare_prices(self):
        assert cf.CloudflareFluxSchnell().estimate_cost_usd(steps=4) == pytest.approx(
            (4 * 9.6 + 4 * 4.8) * 0.011 / 1000, rel=1e-3
        )
        assert cf.CloudflareMeloTTS().estimate_cost_usd(
            text="x" * 900
        ) == pytest.approx(18.63 * 0.011 / 1000, rel=1e-3)

    def test_the_source_never_contains_a_key_or_logs_the_environment(self):
        src = "\n".join(
            p.read_text(encoding="utf-8") for p in Path(cf.__file__).parent.glob("*.py")
        )
        assert "environ" in src and "logger" in src
        assert "os.environ)" not in src and "print(" not in src
        assert (
            "CLOUDFLARE_API_TOKEN" in src
            and "cfut_" not in src
            and "Bearer cf" not in src
        )
        ui = Path(atlas_ui.__file__).read_text(encoding="utf-8")
        assert "/api/media/" in ui and "CLOUDFLARE" not in ui
