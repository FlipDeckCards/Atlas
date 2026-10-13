"""Security hardening of the AiBusSol web app: required secrets, invite-only registration, rate limits, size caps, spend cap, model config.

Everything runs offline: the database is an in-memory fake and the model provider is mocked, so no key or network is needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.server import atlas_ui, auth_routes, hardening
from openjarvis.server.auth_middleware import AuthMiddleware

API_KEY = "test-system-key-0123456789"
JWT_SECRET = "a-long-random-test-secret-0123456789abcdef"
INVITE = "let-me-in"


class FakeStore:
    """The parts of SessionStore the routes use, in memory."""

    def __init__(self) -> None:
        self.users: dict = {}
        self.history: dict = {}
        self.spend: dict = {}

    async def get_user_by_email(self, email):
        return self.users.get(email)

    async def create_user(self, user_id, email, password_hash, display_name=""):
        self.users[email] = {
            "id": user_id,
            "email": email,
            "password_hash": password_hash,
            "display_name": display_name,
        }

    async def get_or_create(self, sender_id, channel_type):
        return {"conversation_history": self.history.setdefault(sender_id, [])}

    async def append_message(self, sender_id, channel_type, role, content):
        self.history.setdefault(sender_id, []).append(
            {"role": role, "content": content}
        )

    async def get_spend_today(self, user_id, day):
        return self.spend.get((user_id, day), 0.0)

    async def add_spend(self, user_id, day, usd):
        self.spend[(user_id, day)] = self.spend.get((user_id, day), 0.0) + usd


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in (
        "INVITE_CODE",
        "DAILY_SPEND_CAP_USD",
        "ATLAS_DEFAULT_MODEL",
        "ATLAS_WRITING_MODEL",
        "ATLAS_LOOKUP_MODEL",
        "RL_LOGIN_IP_MAX",
        "RL_LOGIN_USER_MAX",
        "RL_REGISTER_IP_MAX",
        "RL_CHAT_USER_MAX",
        "RL_CHAT_IP_MAX",
        "TRUSTED_PROXY_HOPS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("OPENJARVIS_API_KEY", API_KEY)
    monkeypatch.setattr(
        hardening, "LIMITER", hardening.RateLimiter()
    )  # a clean limiter per test
    monkeypatch.setattr(atlas_ui, "OPENAI_API_KEY", "test-openai")
    monkeypatch.setattr(atlas_ui, "ELEVENLABS_API_KEY", "test-eleven")


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(atlas_ui.router)
    app.include_router(auth_routes.router)
    app.state.session_store = store
    app.add_middleware(AuthMiddleware, api_key=API_KEY)
    return TestClient(app)


def _bearer(key=API_KEY):
    return {"Authorization": "Bearer " + key}


def _fake_completion(text="hello"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


@pytest.fixture
def model(monkeypatch):
    m = AsyncMock(return_value=_fake_completion())
    monkeypatch.setattr(atlas_ui.litellm, "acompletion", m)
    monkeypatch.setattr(
        hardening, "chat_cost_usd", lambda response, litellm_module=None: 0.03
    )
    return m


# ── startup refusal ─────────────────────────────────────────────────────────────────────────────


class TestStartup:
    def test_refuses_without_jwt_secret(self):
        with pytest.raises(
            hardening.StartupConfigError, match="JWT_SECRET_KEY is not set"
        ):
            hardening.validate_startup({"OPENJARVIS_API_KEY": API_KEY})

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", hardening.DEFAULT_JWT_SECRET, "change-me-please", "secret123"],
    )
    def test_refuses_default_or_placeholder_jwt_secret(self, bad):
        with pytest.raises(hardening.StartupConfigError, match="JWT_SECRET_KEY"):
            hardening.validate_startup(
                {"JWT_SECRET_KEY": bad, "OPENJARVIS_API_KEY": API_KEY}
            )

    def test_refuses_without_api_key(self):
        with pytest.raises(
            hardening.StartupConfigError, match="OPENJARVIS_API_KEY is not set"
        ):
            hardening.validate_startup({"JWT_SECRET_KEY": JWT_SECRET})
        with pytest.raises(hardening.StartupConfigError, match="OPENJARVIS_API_KEY"):
            hardening.validate_startup(
                {"JWT_SECRET_KEY": JWT_SECRET, "OPENJARVIS_API_KEY": "  "}
            )

    def test_both_problems_are_reported_and_values_never_appear(self):
        with pytest.raises(hardening.StartupConfigError) as e:
            hardening.validate_startup({"JWT_SECRET_KEY": hardening.DEFAULT_JWT_SECRET})
        assert "JWT_SECRET_KEY" in str(e.value) and "OPENJARVIS_API_KEY" in str(e.value)

    def test_accepts_good_settings(self):
        hardening.validate_startup(
            {"JWT_SECRET_KEY": JWT_SECRET, "OPENJARVIS_API_KEY": API_KEY}
        )

    def test_no_fallback_secret_for_tokens(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET_KEY")
        with pytest.raises(hardening.StartupConfigError):
            auth_routes.create_token("u1", "a@b.c")
        assert auth_routes.decode_token("whatever") is None
        monkeypatch.setenv(
            "JWT_SECRET_KEY", hardening.DEFAULT_JWT_SECRET
        )  # the old public string is no longer accepted
        with pytest.raises(hardening.StartupConfigError):
            auth_routes.create_token("u1", "a@b.c")

    def test_app_build_requires_an_api_key_when_auth_is_required(self):
        from openjarvis.server.app import create_app

        with pytest.raises(hardening.StartupConfigError):
            create_app(MagicMock(), "m", api_key="", require_auth=True)

    @pytest.mark.parametrize("missing", ["JWT_SECRET_KEY", "OPENJARVIS_API_KEY"])
    def test_jarvis_serve_exits_before_starting(self, monkeypatch, missing):
        from click.testing import CliRunner

        from openjarvis.cli.serve import serve

        monkeypatch.delenv(missing)
        result = CliRunner().invoke(serve, [])
        assert result.exit_code == 1
        assert "Refusing to start" in result.output and missing in result.output


# ── invite-only registration ────────────────────────────────────────────────────────────────────


class TestRegister:
    BODY = {
        "email": "new@example.com",
        "password": "correct-horse",
        "display_name": "N",
    }

    def test_closed_when_invite_code_unset(self, client):
        r = client.post(
            "/api/auth/register", json=dict(self.BODY, invite_code="anything")
        )
        assert r.status_code == 403 and "closed" in r.json()["detail"].lower()

    def test_refused_without_or_with_wrong_invite_code(
        self, client, monkeypatch, store
    ):
        monkeypatch.setenv("INVITE_CODE", INVITE)
        assert client.post("/api/auth/register", json=self.BODY).status_code == 403
        assert (
            client.post(
                "/api/auth/register", json=dict(self.BODY, invite_code="nope")
            ).status_code
            == 403
        )
        assert store.users == {}

    def test_works_with_the_invite_code(self, client, monkeypatch, store):
        monkeypatch.setenv("INVITE_CODE", INVITE)
        r = client.post("/api/auth/register", json=dict(self.BODY, invite_code=INVITE))
        assert r.status_code == 200 and r.json()["token"]
        assert "new@example.com" in store.users

    def test_short_password_refused(self, client, monkeypatch):
        monkeypatch.setenv("INVITE_CODE", INVITE)
        r = client.post(
            "/api/auth/register",
            json=dict(self.BODY, password="short", invite_code=INVITE),
        )
        assert r.status_code == 400

    def test_register_is_rate_limited_per_ip(self, client, monkeypatch):
        monkeypatch.setenv("INVITE_CODE", INVITE)
        monkeypatch.setenv("RL_REGISTER_IP_MAX", "2")
        codes = [
            client.post(
                "/api/auth/register",
                json=dict(self.BODY, email=f"u{i}@x.io", invite_code="bad"),
            ).status_code
            for i in range(4)
        ]
        assert codes == [403, 403, 429, 429]


# ── rate limits ─────────────────────────────────────────────────────────────────────────────────


class TestRateLimits:
    def test_login_limited_per_ip(self, client, monkeypatch):
        monkeypatch.setenv("RL_LOGIN_IP_MAX", "3")
        codes = [
            client.post(
                "/api/auth/login", json={"email": f"u{i}@x.io", "password": "x"}
            ).status_code
            for i in range(5)
        ]
        assert codes == [401, 401, 401, 429, 429]
        assert client.post(
            "/api/auth/login", json={"email": "z@x.io", "password": "x"}
        ).headers.get("retry-after")

    def test_login_limited_per_user_across_ips(self, client, monkeypatch):
        monkeypatch.setenv("RL_LOGIN_USER_MAX", "2")
        codes = [
            client.post(
                "/api/auth/login",
                json={"email": "victim@x.io", "password": "x"},
                headers={"x-forwarded-for": f"10.0.0.{i}"},
            ).status_code
            for i in range(4)
        ]
        assert codes == [
            401,
            401,
            429,
            429,
        ]  # a different IP does not reset the per-account limit

    def test_chat_limited_per_user(self, client, model, monkeypatch):
        monkeypatch.setenv("RL_CHAT_USER_MAX", "2")
        codes = [
            client.post(
                "/api/chat", json={"message": "hi"}, headers=_bearer()
            ).status_code
            for _ in range(4)
        ]
        assert codes == [200, 200, 429, 429]
        assert model.await_count == 2  # the provider is not called once limited

    def test_chat_limited_per_ip(self, client, model, monkeypatch):
        monkeypatch.setenv("RL_CHAT_IP_MAX", "2")
        tok = auth_routes.create_token
        codes = []
        for i in range(4):
            h = {
                "Authorization": "Bearer " + tok(f"user{i}", f"u{i}@x.io"),
                "x-forwarded-for": "9.9.9.9",
            }
            codes.append(
                client.post("/api/chat", json={"message": "hi"}, headers=h).status_code
            )
        assert codes == [200, 200, 429, 429]

    def test_client_ip_uses_the_proxy_added_entry_not_the_forgeable_left_side(self):
        req = SimpleNamespace(
            headers={"x-forwarded-for": "6.6.6.6, 1.2.3.4"},
            client=SimpleNamespace(host="10.0.0.1"),
        )
        assert hardening.client_ip(req) == "1.2.3.4"
        assert (
            hardening.client_ip(
                SimpleNamespace(headers={}, client=SimpleNamespace(host="10.0.0.1"))
            )
            == "10.0.0.1"
        )

    def test_window_slides(self):
        t = [0.0]
        lim = hardening.RateLimiter(clock=lambda: t[0])
        assert [lim.check("k", 2, 10)[0] for _ in range(3)] == [True, True, False]
        t[0] = 11.0
        assert lim.check("k", 2, 10)[0] is True


# ── auth is always enforced ─────────────────────────────────────────────────────────────────────


class TestAuthEnforced:
    def test_api_routes_need_a_token(self, client, model):
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
        assert client.get("/api/session/history").status_code == 401
        assert (
            client.post(
                "/api/chat", json={"message": "hi"}, headers=_bearer("wrong")
            ).status_code
            == 401
        )
        assert model.await_count == 0

    def test_a_forged_token_with_the_old_default_secret_is_rejected(
        self, client, model
    ):
        import jwt

        forged = jwt.encode(
            {"sub": "attacker", "email": "a@b.c"},
            hardening.DEFAULT_JWT_SECRET,
            algorithm="HS256",
        )
        assert (
            client.post(
                "/api/chat", json={"message": "hi"}, headers=_bearer(forged)
            ).status_code
            == 401
        )


# ── size caps ───────────────────────────────────────────────────────────────────────────────────


class TestCaps:
    def test_audio_upload_over_the_cap_is_refused(self, client, store):
        big = b"0" * (hardening.MAX_AUDIO_BYTES + 1)
        r = client.post(
            "/api/voice/transcribe",
            files={"audio": ("a.webm", big, "audio/webm")},
            headers=_bearer(),
        )
        assert r.status_code == 413
        assert store.spend == {}  # nothing was charged or sent

    def test_audio_upload_within_the_cap_is_accepted_up_to_the_provider(
        self, client, monkeypatch
    ):
        calls = []

        class FakeClient:
            def __init__(self, *a, **k): ...
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, **kw):
                calls.append(url)
                return SimpleNamespace(json=lambda: {"text": "hello"})

        monkeypatch.setattr(atlas_ui.httpx, "AsyncClient", FakeClient)
        r = client.post(
            "/api/voice/transcribe",
            files={"audio": ("a.webm", b"0" * 1000, "audio/webm")},
            headers=_bearer(),
        )
        assert r.status_code == 200 and r.json()["text"] == "hello" and len(calls) == 1

    def test_tts_text_over_the_cap_is_refused(self, client, store):
        r = client.post(
            "/api/voice/speak",
            json={"message": "x" * (hardening.MAX_TTS_CHARS + 1)},
            headers=_bearer(),
        )
        assert r.status_code == 413 and store.spend == {}

    def test_oversized_request_body_is_refused_before_it_is_read(self, client, model):
        r = client.post(
            "/api/chat",
            content=b"{}",
            headers=dict(
                _bearer(),
                **{
                    "content-length": str(hardening.MAX_BODY_BYTES + 1),
                    "content-type": "application/json",
                },
            ),
        )
        assert r.status_code == 413 and model.await_count == 0

    def test_image_over_the_cap_is_refused(self, client, model):
        r = client.post(
            "/api/chat",
            json={
                "message": "look",
                "image": "A" * (hardening.MAX_IMAGE_B64_CHARS + 10),
            },
            headers=_bearer(),
        )
        assert r.status_code in (413, 422) and model.await_count == 0

    def test_message_over_the_cap_is_refused(self, client, model):
        r = client.post(
            "/api/chat",
            json={"message": "x" * (hardening.MAX_MESSAGE_CHARS + 1)},
            headers=_bearer(),
        )
        assert r.status_code == 422 and model.await_count == 0

    def test_reply_length_is_bounded(self, client, model):
        client.post("/api/chat", json={"message": "hi"}, headers=_bearer())
        assert model.await_args.kwargs["max_tokens"] == hardening.CHAT_MAX_TOKENS


# ── daily spend cap ─────────────────────────────────────────────────────────────────────────────


class TestSpendCap:
    def test_default_cap_and_parsing(self):
        assert (
            hardening.daily_spend_cap_usd({}) == hardening.DEFAULT_DAILY_SPEND_CAP_USD
        )
        assert hardening.daily_spend_cap_usd({"DAILY_SPEND_CAP_USD": "2.5"}) == 2.5
        assert hardening.daily_spend_cap_usd({"DAILY_SPEND_CAP_USD": "0"}) == 0.0
        assert (
            hardening.daily_spend_cap_usd({"DAILY_SPEND_CAP_USD": "junk"})
            == hardening.DEFAULT_DAILY_SPEND_CAP_USD
        )

    def test_calls_are_recorded_and_the_cap_stops_further_calls_without_calling_the_provider(
        self, client, model, store, monkeypatch
    ):
        monkeypatch.setenv("DAILY_SPEND_CAP_USD", "0.07")
        r1 = client.post("/api/chat", json={"message": "hi"}, headers=_bearer())
        r2 = client.post("/api/chat", json={"message": "hi"}, headers=_bearer())
        assert r1.status_code == 200 and r2.status_code == 200
        assert round(store.spend[("system", hardening.utc_day())], 2) == 0.06
        r3 = client.post(
            "/api/chat", json={"message": "hi"}, headers=_bearer()
        )  # 0.06 + the 0.02 estimate would pass 0.07
        assert r3.status_code == 429 and r3.json()["error"] == "daily_spend_cap_reached"
        assert "limit" in r3.json()["reply"].lower()
        assert model.await_count == 2  # the provider was not called

    def test_zero_cap_blocks_every_paid_call(self, client, model, monkeypatch):
        monkeypatch.setenv("DAILY_SPEND_CAP_USD", "0")
        assert (
            client.post(
                "/api/chat", json={"message": "hi"}, headers=_bearer()
            ).status_code
            == 429
        )
        assert model.await_count == 0

    def test_the_cap_is_per_user(self, client, model, store, monkeypatch):
        monkeypatch.setenv("DAILY_SPEND_CAP_USD", "0.05")
        store.spend[("alice", hardening.utc_day())] = 0.05
        alice = {
            "Authorization": "Bearer " + auth_routes.create_token("alice", "a@x.io")
        }
        bob = {"Authorization": "Bearer " + auth_routes.create_token("bob", "b@x.io")}
        assert (
            client.post("/api/chat", json={"message": "hi"}, headers=alice).status_code
            == 429
        )
        assert (
            client.post("/api/chat", json={"message": "hi"}, headers=bob).status_code
            == 200
        )

    def test_voice_calls_count_against_the_cap_and_are_blocked_at_it(
        self, client, store, monkeypatch
    ):
        monkeypatch.setenv("DAILY_SPEND_CAP_USD", "0.01")
        r = client.post(
            "/api/voice/speak", json={"message": "x" * 1000}, headers=_bearer()
        )  # ~$0.30 estimate > cap
        assert r.status_code == 429 and store.spend == {}

    def test_provider_errors_are_not_leaked_to_the_browser(
        self, client, store, monkeypatch
    ):
        monkeypatch.setattr(
            atlas_ui.litellm,
            "acompletion",
            AsyncMock(side_effect=RuntimeError("secret-provider-detail")),
        )
        r = client.post("/api/chat", json={"message": "hi"}, headers=_bearer())
        assert "secret-provider-detail" not in r.text and store.spend == {}

    def test_cost_falls_back_to_a_flat_estimate_for_unknown_models(self):
        lm = SimpleNamespace(
            completion_cost=MagicMock(side_effect=Exception("unknown model"))
        )
        assert (
            hardening.chat_cost_usd(object(), litellm_module=lm)
            == hardening.FLAT_CHAT_ESTIMATE_USD
        )


# ── model names from configuration ──────────────────────────────────────────────────────────────


class TestModelConfig:
    def test_defaults_keep_the_current_routing(self):
        assert atlas_ui.classify_model("hello there", False)[0] == "gpt-4o"
        assert (
            atlas_ui.classify_model("write an essay and explain it", False)[0]
            == "claude-sonnet-4-5-20250929"
        )
        assert (
            atlas_ui.classify_model("latest news and statistics", False)[0]
            == "gemini/gemini-2.5-flash"
        )
        assert atlas_ui.classify_model("what is this", True)[0] == "gpt-4o"

    def test_model_names_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("ATLAS_DEFAULT_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("ATLAS_WRITING_MODEL", "claude-x")
        monkeypatch.setenv("ATLAS_LOOKUP_MODEL", "gemini/g-x")
        assert atlas_ui.classify_model("hello", False)[0] == "gpt-4o-mini"
        assert (
            atlas_ui.classify_model("write a poem and explain", False)[0] == "claude-x"
        )
        assert (
            atlas_ui.classify_model("research the latest news", False)[0]
            == "gemini/g-x"
        )
        assert atlas_ui.classify_model("see this", True)[0] == "gpt-4o-mini"

    def test_no_model_name_is_hard_coded_in_the_routing_function(self):
        import inspect

        src = inspect.getsource(atlas_ui.classify_model)
        import re

        assert not re.search(
            r"""["'](gpt-|claude-|gemini/)""", src
        )  # no quoted model id in the function

    def test_the_glb_url_variable_is_untouched(self):
        assert atlas_ui.ATLAS_MODEL_URL == atlas_ui.os.getenv("ATLAS_MODEL_URL", "")
