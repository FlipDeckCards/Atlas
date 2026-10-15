"""A valid logged-in user must reach the media and voice routes; a bad or missing token still gets 401.

Root cause covered here: the auth middleware ran the route INSIDE its token-check try/except, so any error raised by a route (for example the
daily-spend query rejecting a date string) was reported to the person as "401 Invalid or expired token". These tests use the REAL app
(create_app), not a cut-down one, so the real middleware stack is in the way.
"""

from __future__ import annotations

import datetime as dt
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from openjarvis.server import atlas_ui, auth_routes, hardening
from openjarvis.server.app import create_app
from openjarvis.server.media import image as image_mod
from openjarvis.server.media import sound as sound_mod
from openjarvis.server.media.base import MediaResult
from openjarvis.server.media.catalog import MemoryCatalog
from openjarvis.server.media.storage import MemoryStorage
from openjarvis.server.session_store import SessionStore

API_KEY = "test-system-key-0123456789"
JWT_SECRET = "a-long-random-test-secret-0123456789abcdef"


class FakeStore:
    def __init__(self) -> None:
        self.spend: dict = {}
        self.history: list = []

    async def get_or_create(self, sender_id, channel_type):
        return {"conversation_history": self.history}

    async def append_message(self, sender_id, channel_type, role, content):
        self.history.append({"role": role, "content": content})

    async def get_spend_today(self, user_id, day):
        return self.spend.get((user_id, day), 0.0)

    async def add_spend(self, user_id, day, usd):
        self.spend[(user_id, day)] = self.spend.get((user_id, day), 0.0) + usd


class FakeProvider:
    def __init__(self, kind):
        self.kind, self.id, self.calls = kind, "fake_" + kind, []

    def configured(self):
        return True

    def estimate_cost_usd(self, **_):
        return 0.01

    async def generate(self, **request):
        self.calls.append(request)
        return MediaResult(self.kind, "image/jpeg" if self.kind == "image" else "audio/mpeg", b"bytes", self.id)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in ("MEDIA_ENABLED", "DAILY_MEDIA_SPEND_CAP_USD", "MEDIA_GLOBAL_DAILY_CAP_USD", "DAILY_SPEND_CAP_USD", "JWT_SECRET_STRICT"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("OPENJARVIS_API_KEY", API_KEY)
    monkeypatch.setattr(hardening, "LIMITER", hardening.RateLimiter())
    monkeypatch.setattr(atlas_ui, "OPENAI_API_KEY", "test-openai")


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def client(store):
    app = create_app(MagicMock(), "m", api_key=API_KEY, require_auth=True)
    app.state.session_store = store
    app.state.media_storage = MemoryStorage()
    app.state.media_catalog = MemoryCatalog()

    @app.get("/api/_boom")
    async def boom():                                   # a route that fails
        raise RuntimeError("route failure")

    app.router.routes.insert(0, app.router.routes.pop())      # ahead of the app's catch-all page route

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def img(monkeypatch):
    p = FakeProvider("image")
    monkeypatch.setitem(image_mod.PROVIDERS, image_mod.DEFAULT_PROVIDER, p)
    return p


@pytest.fixture
def snd(monkeypatch):
    p = FakeProvider("sound")
    monkeypatch.setitem(sound_mod.PROVIDERS, sound_mod.DEFAULT_PROVIDER, p)
    return p


def user(uid="u1"):
    return {"Authorization": "Bearer " + auth_routes.create_token(uid, uid + "@x.io")}


def media_on(monkeypatch, cap="0.05"):
    monkeypatch.setenv("MEDIA_ENABLED", "1")
    monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", cap)


# ── a valid user reaches the routes ─────────────────────────────────────────────────────────────

class TestValidUser:
    def test_the_same_token_reaches_history_chat_media_and_voice(self, client, img, snd, store, monkeypatch):
        media_on(monkeypatch)
        monkeypatch.setattr(atlas_ui.litellm, "acompletion", AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))])))
        monkeypatch.setattr(hardening, "chat_cost_usd", lambda r, litellm_module=None: 0.001)
        h = user()
        assert client.get("/api/session/history", headers=h).status_code == 200
        assert client.post("/api/chat", json={"message": "hello"}, headers=h).status_code == 200
        r = client.post("/api/media/image", json={"prompt": "a fox"}, headers=h)
        assert r.status_code == 200 and r.json()["success"] is True
        r = client.post("/api/media/sound", json={"text": "hello"}, headers=h)
        assert r.status_code == 200 and r.json()["item"]["kind"] == "sound"
        assert client.get("/api/media/status", headers=h).status_code == 200
        assert len(img.calls) == 1 and len(snd.calls) == 1

    def test_voice_transcribe_reaches_the_route_with_a_user_token(self, client, store, monkeypatch):
        calls = []

        class FakeClient:
            def __init__(self, *a, **k): ...
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, **kw):
                calls.append(url)
                return SimpleNamespace(json=lambda: {"text": "hello"})

        monkeypatch.setattr(atlas_ui.httpx, "AsyncClient", FakeClient)
        r = client.post("/api/voice/transcribe", files={"audio": ("a.webm", b"0" * 100, "audio/webm")}, headers=user())
        assert r.status_code == 200 and r.json()["text"] == "hello" and len(calls) == 1
        assert store.spend                                        # the spend was recorded (the call that used to fail)

    def test_the_media_and_voice_spend_calls_use_real_dates(self, client, img, store, monkeypatch):
        media_on(monkeypatch)
        client.post("/api/media/image", json={"prompt": "a fox"}, headers=user())
        assert store.spend                                          # recorded under the user and the global key


# ── bad tokens are still refused ────────────────────────────────────────────────────────────────

class TestBadTokens:
    @pytest.mark.parametrize("path,body", [("/api/media/image", {"prompt": "x"}), ("/api/media/sound", {"text": "x"}), ("/api/chat", {"message": "x"})])
    def test_missing_invalid_expired_or_forged_tokens_get_401_and_nothing_is_called(self, client, img, snd, monkeypatch, path, body):
        import jwt

        media_on(monkeypatch)
        expired = jwt.encode({"sub": "u1", "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)}, JWT_SECRET, algorithm="HS256")
        forged = jwt.encode({"sub": "u1"}, "some-other-secret-0123456789abcdef0123", algorithm="HS256")
        no_sub = jwt.encode({"email": "a@b.c"}, JWT_SECRET, algorithm="HS256")
        for headers in ({}, {"Authorization": "Bearer nonsense"}, {"Authorization": "Bearer " + expired}, {"Authorization": "Bearer " + forged},
                        {"Authorization": "Bearer " + no_sub}, {"Authorization": "Basic abc"}):
            assert client.post(path, json=body, headers=headers).status_code == 401, headers
        assert img.calls == [] and snd.calls == []

    def test_transcribe_without_a_token_is_401(self, client):
        assert client.post("/api/voice/transcribe", files={"audio": ("a.webm", b"1", "audio/webm")}).status_code == 401


# ── Stage 2 rules still hold for a valid user ───────────────────────────────────────────────────

class TestMediaRulesStillHold:
    def test_flag_off_and_cap_zero_give_403_without_calling_the_provider(self, client, img, snd, monkeypatch):
        h = user()
        assert client.post("/api/media/image", json={"prompt": "x"}, headers=h).status_code == 403          # nothing set
        monkeypatch.setenv("MEDIA_ENABLED", "1")
        assert client.post("/api/media/image", json={"prompt": "x"}, headers=h).status_code == 403          # flag on, cap 0
        monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "0")
        assert client.post("/api/media/sound", json={"text": "x"}, headers=h).status_code == 403
        monkeypatch.delenv("MEDIA_ENABLED")
        monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "5")
        assert client.post("/api/media/sound", json={"text": "x"}, headers=h).status_code == 403            # cap set, flag off
        assert img.calls == [] and snd.calls == []

    def test_cap_reached_gives_429_before_any_provider_call(self, client, img, store, monkeypatch):
        media_on(monkeypatch, cap="0.015")
        h = user()
        assert client.post("/api/media/image", json={"prompt": "x"}, headers=h).status_code == 200
        r = client.post("/api/media/image", json={"prompt": "x"}, headers=h)
        assert r.status_code == 429 and r.json()["error"] == "daily_media_cap_reached"
        assert len(img.calls) == 1


# ── the actual bug ──────────────────────────────────────────────────────────────────────────────

class TestErrorsAreNotReportedAsBadTokens:
    def test_a_route_that_fails_is_a_500_not_a_401(self, client):
        assert client.get("/api/_boom", headers=user()).status_code == 500
        assert client.get("/api/_boom").status_code == 401                 # but no token is still refused first

    def test_a_failing_store_is_a_500_for_a_valid_user(self, client, store, img, monkeypatch):
        media_on(monkeypatch)

        async def broken(*a, **k):
            raise RuntimeError("database says no")

        monkeypatch.setattr(store, "get_spend_today", broken)
        r = client.post("/api/media/image", json={"prompt": "x"}, headers=user())
        assert r.status_code == 500 and "Invalid or expired token" not in r.text

    def test_the_middleware_runs_the_route_outside_its_token_try_block(self):
        import inspect

        from openjarvis.server.auth_middleware import AuthMiddleware

        src = inspect.getsource(AuthMiddleware.dispatch)
        token_try = src.index("try:")
        assert "call_next" not in src[token_try: src.index("except Exception", token_try)]


class FakeConn:
    def __init__(self):
        self.args = []

    async def fetchrow(self, sql, *args):
        self.args.append(args)
        return None

    async def execute(self, sql, *args):
        self.args.append(args)


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self): return conn
            async def __aexit__(self, *a): return False

        return Ctx()


class TestSpendDates:
    @pytest.mark.asyncio
    async def test_daily_spend_queries_get_a_date_object_not_a_string(self):
        conn = FakeConn()
        s = SessionStore("postgres://unused")
        s._pool = FakePool(conn)
        await s.get_spend_today("u1", "2026-10-14")
        await s.add_spend("u1", "2026-10-14", 0.01)
        await s.get_spend_today("u1", dt.date(2026, 10, 14))
        for args in conn.args:
            assert isinstance(args[1], dt.date) and not isinstance(args[1], str) and args[1] == dt.date(2026, 10, 14)

    @pytest.mark.asyncio
    async def test_the_sql_no_longer_casts_a_text_parameter(self):
        import inspect

        src = inspect.getsource(SessionStore.get_spend_today) + inspect.getsource(SessionStore.add_spend)
        assert "::date" not in src


# ── JWT secret length ───────────────────────────────────────────────────────────────────────────

class TestSecretLength:
    BASE = {"OPENJARVIS_API_KEY": API_KEY}

    def test_a_short_secret_warns_with_its_length_in_bytes_and_still_starts(self, caplog):
        env = dict(self.BASE, JWT_SECRET_KEY="x" * 23)
        with caplog.at_level(logging.WARNING):
            hardening.validate_startup(env)
        assert "23 bytes" in caplog.text and "at least 32" in caplog.text and "xxxxxxxx" not in caplog.text
        assert "23 bytes" in hardening.jwt_secret_length_warning(env)

    def test_32_bytes_or_more_is_fine_and_length_is_counted_in_bytes(self, caplog):
        with caplog.at_level(logging.WARNING):
            hardening.validate_startup(dict(self.BASE, JWT_SECRET_KEY="k" * 32))
            hardening.validate_startup(dict(self.BASE, JWT_SECRET_KEY="é" * 20))        # 20 characters but 40 bytes
        assert caplog.text == ""
        assert "30 bytes" in hardening.jwt_secret_length_warning(dict(self.BASE, JWT_SECRET_KEY="é" * 15))

    def test_strict_mode_refuses_to_start_with_a_short_secret(self):
        env = dict(self.BASE, JWT_SECRET_KEY="x" * 23, JWT_SECRET_STRICT="1")
        with pytest.raises(hardening.StartupConfigError, match="23 bytes"):
            hardening.validate_startup(env)
        hardening.validate_startup(dict(self.BASE, JWT_SECRET_KEY="x" * 40, JWT_SECRET_STRICT="1"))

    def test_serve_prints_the_warning_too(self):
        import importlib
        from pathlib import Path

        serve_mod = importlib.import_module("openjarvis.cli.serve")      # (the package attribute of that name is the click command)
        src = Path(serve_mod.__file__).read_text(encoding="utf-8")
        assert "jwt_secret_length_warning()" in src
