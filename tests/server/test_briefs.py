"""Brief feed: token-protected ingest, size limits, latest per period, the Listen cap, the 503 path, the favicon/static fix. Offline."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openjarvis.server import atlas_ui, auth_routes, hardening
from openjarvis.server.app import create_app
from openjarvis.server.briefs import BRIEFS_DDL, BriefStore, MemoryBriefStore
from tests.server._media_fakes import API_KEY, JWT_SECRET, FakeStore

INGEST = "brief-ingest-token-SECRET-0123456789"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in ("BRIEF_INGEST_TOKEN", "BRIEF_LISTEN_DAILY_LIMIT", "DAILY_SPEND_CAP_USD", "RL_BRIEF_IP_MAX"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("OPENJARVIS_API_KEY", API_KEY)
    monkeypatch.setenv("BRIEF_INGEST_TOKEN", INGEST)
    monkeypatch.setattr(hardening, "LIMITER", hardening.RateLimiter())
    monkeypatch.setattr(atlas_ui, "ELEVENLABS_API_KEY", "test-eleven")


@pytest.fixture
def world():
    clock = [dt.datetime(2026, 10, 15, 6, 0, tzinfo=dt.timezone.utc)]
    store, session = MemoryBriefStore(clock=lambda: clock[0]), FakeStore()
    app = create_app(MagicMock(), "m", api_key=API_KEY, require_auth=True)
    app.state.brief_store, app.state.session_store = store, session
    client = TestClient(app, raise_server_exceptions=False)
    return type("W", (), {"client": client, "store": store, "session": session, "clock": clock})()


def user(uid="alice"):
    return {"Authorization": "Bearer " + auth_routes.create_token(uid, uid + "@x.io")}


def ingest_h(token=INGEST):
    return {"Authorization": "Bearer " + token}


def brief(**over):
    b = {"product": "Marnyx and FlipDeck", "period": "morning", "body_markdown": "# Morning\n- all quiet", "spoken_summary": "All quiet this morning."}
    b.update(over)
    return b


def post(w, body=None, headers=None):
    return w.client.post("/api/briefs", json=brief() if body is None else body, headers=ingest_h() if headers is None else headers)


# ── ingest: the token ───────────────────────────────────────────────────────────────────────────

class TestIngestAuth:
    def test_401_without_a_token_with_a_wrong_token_or_the_wrong_scheme(self, world):
        for h in ({}, ingest_h("wrong"), {"Authorization": "Bearer "}, {"Authorization": "Basic " + INGEST}, {"Authorization": INGEST}, {"Authorization": "Bearer " + INGEST + "x"}):
            assert post(world, headers=h).status_code == 401, h
        assert world.store.rows == []

    def test_401_when_the_variable_is_unset_even_if_the_caller_sends_something(self, world, monkeypatch):
        monkeypatch.delenv("BRIEF_INGEST_TOKEN")
        for h in (ingest_h(INGEST), ingest_h(""), ingest_h("anything"), {}):
            assert post(world, headers=h).status_code == 401
        monkeypatch.setenv("BRIEF_INGEST_TOKEN", "   ")
        assert post(world, headers=ingest_h("   ")).status_code == 401
        assert world.store.rows == []

    def test_the_system_key_and_a_user_login_do_not_work_for_ingest(self, world):
        assert post(world, headers=ingest_h(API_KEY)).status_code == 401
        assert post(world, headers=user()).status_code == 401

    def test_the_token_check_comes_before_the_body_is_looked_at(self, world):
        r = world.client.post("/api/briefs", json={"junk": True}, headers={})
        assert r.status_code == 401 and "errors" not in r.json()
        assert world.client.post("/api/briefs", content=b"not json", headers={}).status_code == 401

    def test_the_ingest_token_opens_nothing_else(self, world):
        h = ingest_h()
        assert world.client.get("/api/briefs/latest", headers=h).status_code == 401
        assert world.client.post("/api/chat", json={"message": "hi"}, headers=h).status_code == 401
        assert world.client.get("/api/session/history", headers=h).status_code == 401
        assert world.client.post("/api/briefs/00000000-0000-4000-8000-000000000000/listen", headers=h).status_code == 401
        assert world.client.get("/api/briefs", headers={}).status_code == 401              # only POST /api/briefs is outside the login

    def test_a_good_post_is_stored(self, world):
        r = post(world)
        assert r.status_code == 201 and r.json()["success"] is True and r.json()["id"]
        assert len(world.store.rows) == 1 and world.store.rows[0]["product"] == "Marnyx and FlipDeck" and world.store.rows[0]["period"] == "morning"

    def test_attempts_are_rate_limited_per_ip_which_also_slows_guessing(self, world, monkeypatch):
        monkeypatch.setenv("RL_BRIEF_IP_MAX", "3")
        codes = [post(world, headers=ingest_h("guess%d" % i)).status_code for i in range(5)]
        assert codes == [401, 401, 401, 429, 429]

    def test_the_token_never_appears_in_answers_or_logs(self, world, caplog):
        with caplog.at_level(logging.DEBUG):
            ok = post(world)
            bad = post(world, headers=ingest_h(INGEST + "x"))
            invalid = post(world, body={"period": "noon"})
        for r in (ok, bad, invalid):
            assert INGEST not in r.text and INGEST not in str(dict(r.headers))
        assert INGEST not in caplog.text


# ── ingest: limits ──────────────────────────────────────────────────────────────────────────────

class TestLimits:
    def test_the_size_limits(self, world):
        assert post(world, brief(body_markdown="x" * 20_000)).status_code == 201
        assert post(world, brief(body_markdown="x" * 20_001)).status_code == 422
        assert post(world, brief(spoken_summary="y" * 600)).status_code == 201
        assert post(world, brief(spoken_summary="y" * 601)).status_code == 422
        assert post(world, brief(product="p" * 101)).status_code == 422
        assert len(world.store.rows) == 2                                               # only the two valid ones were stored

    def test_the_period_must_be_one_of_the_three(self, world):
        for p in ("morning", "afternoon", "evening"):
            assert post(world, brief(period=p)).status_code == 201
        for bad in ("noon", "Morning", "", None, 1):
            assert post(world, brief(period=bad)).status_code == 422

    def test_missing_or_empty_fields_are_refused_and_the_error_does_not_echo_the_text(self, world):
        for field in ("product", "period", "body_markdown", "spoken_summary"):
            b = brief()
            del b[field]
            assert post(world, b).status_code == 422
        assert post(world, brief(body_markdown="")).status_code == 422
        r = post(world, brief(spoken_summary="z" * 601))
        assert r.status_code == 422 and "zzzz" not in r.text and r.json()["errors"][0]["field"] == "spoken_summary"
        assert world.client.post("/api/briefs", content=b"not json", headers=ingest_h()).status_code == 400

    def test_the_table_has_the_requested_columns(self):
        for col in ("id", "created_at", "product", "period", "body", "spoken_summary"):
            assert col in BRIEFS_DDL
        assert "CHECK (period IN ('morning', 'afternoon', 'evening'))" in BRIEFS_DDL and "IF NOT EXISTS" in BRIEFS_DDL and "DROP" not in BRIEFS_DDL.upper()
        import openjarvis.server.session_store as ss

        assert "BRIEFS_DDL" in Path(ss.__file__).read_text(encoding="utf-8")


# ── latest per period ───────────────────────────────────────────────────────────────────────────

class TestLatest:
    def test_it_needs_a_logged_in_session(self, world):
        assert world.client.get("/api/briefs/latest").status_code == 401
        assert world.client.get("/api/briefs/latest", headers={"Authorization": "Bearer nonsense"}).status_code == 401
        assert world.client.get("/api/briefs/latest", headers=user()).status_code == 200

    def test_the_newest_brief_of_each_period_wins_and_missing_periods_are_null(self, world):
        def add(period, body, minutes):
            world.clock[0] = dt.datetime(2026, 10, 15, 6, 0, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=minutes)
            assert post(world, brief(period=period, body_markdown=body)).status_code == 201

        add("morning", "old morning", 0)
        add("evening", "only evening", 5)
        add("morning", "new morning", 10)
        add("morning", "oldest-looking but newest period?", -30)          # an older timestamp posted later does not win
        d = world.client.get("/api/briefs/latest", headers=user()).json()
        assert d["briefs"]["morning"]["body"] == "new morning" and d["briefs"]["evening"]["body"] == "only evening" and d["briefs"]["afternoon"] is None
        assert [i["period"] for i in d["items"]] == ["morning", "evening"]                  # newest first
        assert d["items"][0]["created_at"] > d["items"][1]["created_at"]
        assert set(d["briefs"]["morning"]) == {"id", "created_at", "product", "period", "body", "spoken_summary"}

    def test_it_is_empty_before_anything_is_posted(self, world):
        d = world.client.get("/api/briefs/latest", headers=user()).json()
        assert d["briefs"] == {"morning": None, "afternoon": None, "evening": None} and d["items"] == []

    @pytest.mark.asyncio
    async def test_the_postgres_queries(self):
        seen = []

        class Conn:
            async def fetchrow(self, sql, *a):
                seen.append(sql)
                return {"id": "0a1b2c3d-0000-4000-8000-000000000000", "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), "product": "p", "period": "morning", "body": "b", "spoken_summary": "s"}

            async def fetch(self, sql, *a):
                seen.append(sql)
                return []

        class Pool:
            def acquire(self):
                class C:
                    async def __aenter__(s): return Conn()
                    async def __aexit__(s, *a): return False
                return C()

        s = BriefStore(Pool())
        await s.insert("p", "morning", "b", "s")
        await s.latest_per_period()
        assert "INSERT INTO briefs" in seen[0] and "DISTINCT ON (period)" in seen[1] and "ORDER BY period, created_at DESC" in seen[1]
        assert await BriefStore(Pool()).get("not-a-uuid") is None


# ── Listen ──────────────────────────────────────────────────────────────────────────────────────

def add_brief(w, **over):
    r = post(w, brief(**over))
    assert r.status_code == 201
    return r.json()["id"]


class TestListen:
    def test_it_needs_a_logged_in_session(self, world):
        bid = add_brief(world)
        assert world.client.post(f"/api/briefs/{bid}/listen").status_code == 401

    def test_it_returns_the_spoken_summary_only_never_the_full_text(self, world):
        bid = add_brief(world, body_markdown="THE FULL LONG TEXT " * 50, spoken_summary="Short spoken version.")
        r = world.client.post(f"/api/briefs/{bid}/listen", headers=user())
        assert r.status_code == 200 and r.json()["text"] == "Short spoken version." and "FULL LONG" not in r.text
        assert r.json()["estimated_cost_usd"] == hardening.tts_cost_usd(len("Short spoken version."))

    def test_five_listens_a_day_per_user_then_a_clear_refusal(self, world):
        bid = add_brief(world)
        remaining = []
        for _ in range(5):
            r = world.client.post(f"/api/briefs/{bid}/listen", headers=user("alice"))
            assert r.status_code == 200
            remaining.append(r.json()["remaining"])
        assert remaining == [4, 3, 2, 1, 0]
        six = world.client.post(f"/api/briefs/{bid}/listen", headers=user("alice"))
        assert six.status_code == 429 and six.json()["error"] == "listen_limit_reached" and "5 briefing listens" in six.json()["detail"] and "text" not in six.json()
        assert world.session.spent("brief_listen:alice") == 5.0                          # the existing spend tracker holds the count
        assert world.client.post(f"/api/briefs/{bid}/listen", headers=user("bob")).status_code == 200       # per user

    def test_the_count_resets_the_next_day(self, world, monkeypatch):
        bid = add_brief(world)
        for _ in range(5):
            world.client.post(f"/api/briefs/{bid}/listen", headers=user())
        assert world.client.post(f"/api/briefs/{bid}/listen", headers=user()).status_code == 429
        monkeypatch.setattr(hardening, "utc_day", lambda: "2099-01-01")
        assert world.client.post(f"/api/briefs/{bid}/listen", headers=user()).status_code == 200

    def test_the_limit_can_be_changed_with_a_variable(self, world, monkeypatch):
        monkeypatch.setenv("BRIEF_LISTEN_DAILY_LIMIT", "2")
        bid = add_brief(world)
        codes = [world.client.post(f"/api/briefs/{bid}/listen", headers=user()).status_code for _ in range(3)]
        assert codes == [200, 200, 429]

    def test_the_daily_spend_cap_applies_to_the_speech_and_a_refusal_costs_no_listen(self, world, monkeypatch):
        bid = add_brief(world, spoken_summary="x" * 600)                               # about $0.18 of speech
        monkeypatch.setenv("DAILY_SPEND_CAP_USD", "0.05")
        r = world.client.post(f"/api/briefs/{bid}/listen", headers=user())
        assert r.status_code == 429 and r.json()["error"] == "daily_spend_cap_reached"
        assert world.session.spent("brief_listen:alice") == 0.0

    def test_503_when_elevenlabs_is_not_configured_and_nothing_is_counted(self, world, monkeypatch):
        bid = add_brief(world)
        monkeypatch.setattr(atlas_ui, "ELEVENLABS_API_KEY", "")
        r = world.client.post(f"/api/briefs/{bid}/listen", headers=user())
        assert r.status_code == 503 and r.json()["detail"] == "Text-to-speech is not configured"
        assert world.session.spend == {}

    def test_unknown_or_malformed_ids_are_404(self, world):
        for bad in ("00000000-0000-4000-8000-000000000000", "nope", "1" * 40):
            assert world.client.post(f"/api/briefs/{bad}/listen", headers=user()).status_code == 404


# ── the page: favicon, static fix, chat message ─────────────────────────────────────────────────

class TestFaviconAndStatic:
    def test_favicon_is_served_without_a_login_and_is_not_an_error(self, world):
        r = world.client.get("/favicon.ico")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and "<svg" in r.text

    def test_the_minimal_page_exists_and_is_not_git_ignored(self):
        import openjarvis.server.app as appmod

        index = Path(appmod.__file__).parent / "static" / "index.html"
        assert index.is_file() and "<title>Aibussol</title>" in index.read_text(encoding="utf-8")
        ignore = (Path(appmod.__file__).parents[3] / ".gitignore").read_text(encoding="utf-8")
        assert "src/openjarvis/server/static/*" in ignore and "!src/openjarvis/server/static/index.html" in ignore

    def test_a_missing_page_is_a_404_not_a_500(self, world, monkeypatch):
        orig = Path.is_file
        monkeypatch.setattr(Path, "is_file", lambda self: False if self.name == "index.html" else orig(self))
        assert world.client.get("/some/page").status_code == 404
        assert world.client.get("/favicon.ico").status_code == 200

    def test_unknown_pages_still_get_the_page(self, world):
        r = world.client.get("/anything")
        assert r.status_code == 200 and "Aibussol" in r.text


class TestChatMessage:
    @pytest.fixture
    def page(self):
        return Path(atlas_ui.__file__).read_text(encoding="utf-8")

    def test_each_new_brief_is_a_message_with_the_full_text_and_a_listen_button(self, page):
        assert "function addBriefMessage(b)" in page and "mk('div', null, b.body)" in page and "mk('button', 'v-btn', 'Listen')" in page
        assert "BRIEF" in page and "pre-wrap" in page
        assert "'/api/briefs/latest'" in page and "setInterval(checkBriefs, 60000)" in page and "checkBriefs();" in page
        assert "markBriefSeen(b.id)" in page and "seenBriefs().includes(b.id)" in page                   # each brief is shown once per browser

    def test_listen_calls_speakreply_with_the_spoken_summary_the_server_returns_and_nothing_else(self, page):
        block = page[page.index("function addBriefMessage(b)"):page.index("async function checkBriefs")]
        assert "speakReply(r.data.text, wrap)" in block and "speakReply(b.body" not in block and "speakReply(b.spoken" not in block
        assert "/listen'" in block and "r.data.detail" in block                                          # the refusal message is shown
        assert block.count("speakReply(") == 1

    def test_the_brief_text_is_set_as_text_not_html(self, page):
        block = page[page.index("function addBriefMessage(b)"):page.index("async function checkBriefs")]
        assert "innerHTML" not in block


# ── chat: "read me the brief" ───────────────────────────────────────────────────────────────────

from openjarvis.server import briefs as briefs_mod  # noqa: E402


class TestWantsBrief:
    @pytest.mark.parametrize("msg,want", [
        ("Read me the Daily Brief.", "latest"),
        ("read me the daily brief", "latest"),
        ("What's in my brief?", "latest"),
        ("play the morning brief", "morning"),
        ("I didn't get the am brief", "morning"),
        ("give me the afternoon briefing", "afternoon"),
        ("read tonight's brief", "evening"),
        ("any new briefs?", "latest"),
    ])
    def test_requests_for_the_brief(self, msg, want):
        assert briefs_mod.wants_brief(msg) == want

    @pytest.mark.parametrize("msg", [
        "give me a brief summary of the French revolution",
        "write me a brief email to my landlord",
        "can you draft the brief for the client",
        "keep it brief",
        "generate me an image of a bear in the woods",
        "",
        "read me the brief " + "x" * 200,
    ])
    def test_other_messages_are_not_brief_requests(self, msg):
        assert briefs_mod.wants_brief(msg) is None


class TestChatReadsTheBrief:
    def _chat(self, w, msg, uid="alice"):
        return w.client.post("/api/chat", json={"message": msg}, headers=user(uid))

    def test_the_latest_brief_is_read_from_the_feed_without_a_model_call_or_spend(self, world, monkeypatch):
        called = []
        monkeypatch.setattr(atlas_ui.litellm, "acompletion", lambda *a, **k: called.append(1))
        post(world, brief(body_markdown="# Status\n**Reconnect** the Meta token first.", spoken_summary="Short."))
        r = self._chat(world, "Read me the Daily Brief.")
        assert r.status_code == 200
        assert r.json()["reply"] == "Morning brief for Marnyx and FlipDeck.\n\nStatus\nReconnect the Meta token first."
        assert "Short." not in r.json()["reply"]
        assert called == [] and world.session.spent("alice") == 0

    def test_the_newest_period_wins_for_latest_and_a_named_period_is_honoured(self, world):
        post(world, brief(body_markdown="Morning summary."))
        world.clock[0] = world.clock[0] + dt.timedelta(hours=6)
        post(world, brief(period="afternoon", body_markdown="Afternoon summary."))
        assert self._chat(world, "read me the brief").json()["reply"].endswith("Afternoon summary.")
        assert self._chat(world, "read me the morning brief").json()["reply"].endswith("Morning summary.")

    def test_a_clear_answer_when_there_is_no_brief(self, world):
        assert self._chat(world, "read me the evening brief").json()["reply"] == "There's no evening brief in the feed yet."
        assert self._chat(world, "read me the brief").json()["reply"] == "There's no brief in the feed yet."

    def test_the_exchange_is_kept_in_the_conversation(self, world):
        post(world)
        self._chat(world, "read me the brief")
        hist = world.session.history["alice"]
        assert hist[-2] == {"role": "user", "content": "read me the brief"}
        assert hist[-1]["role"] == "assistant" and hist[-1]["content"].startswith("Morning brief for")

    def test_a_follow_up_gets_the_full_brief_as_context(self, world, monkeypatch):
        seen = {}

        async def fake(model, messages, **kw):
            seen["system"] = messages[0]["content"]
            msg = MagicMock(); msg.content = "Meta and TikTok are pending."
            resp = MagicMock(); resp.choices = [MagicMock(message=msg)]
            return resp

        monkeypatch.setattr(atlas_ui.litellm, "acompletion", fake)
        monkeypatch.setattr(atlas_ui.helix_tools, "tools_available", lambda state: False)
        monkeypatch.setattr(hardening, "chat_cost_usd", lambda resp, litellm_module=None: 0.001)
        post(world, brief(body_markdown="# Morning\n- Pending: Meta review, TikTok review"))
        self._chat(world, "read me the brief")
        r = self._chat(world, "what's pending?")
        assert r.status_code == 200
        assert "<brief>" in seen["system"] and "Pending: Meta review" in seen["system"]

    def test_unrelated_chat_gets_no_brief_context(self, world, monkeypatch):
        seen = {}

        async def fake(model, messages, **kw):
            seen["system"] = messages[0]["content"]
            msg = MagicMock(); msg.content = "Hi."
            resp = MagicMock(); resp.choices = [MagicMock(message=msg)]
            return resp

        monkeypatch.setattr(atlas_ui.litellm, "acompletion", fake)
        monkeypatch.setattr(atlas_ui.helix_tools, "tools_available", lambda state: False)
        monkeypatch.setattr(hardening, "chat_cost_usd", lambda resp, litellm_module=None: 0.001)
        post(world)
        self._chat(world, "hello there")
        assert "<brief>" not in seen["system"]


class TestVoiceFailureIsShown:
    @pytest.fixture
    def page(self):
        return Path(atlas_ui.__file__).read_text(encoding="utf-8")

    def test_a_failed_spoken_reply_shows_a_note_under_the_message(self, page):
        block = page[page.index("async function speakReply(text, el)"):]
        block = block[:block.index("const arrayBuffer")]
        assert "voiceNote(el, res, data)" in block
        assert "'Voice unavailable: '" in page and "daily_spend_cap_reached" in page
        assert "speakReply(reply, replyEl)" in page

    def test_the_note_is_set_as_text_not_html(self, page):
        block = page[page.index("function voiceNote("):page.index("async function speakReply(")]
        assert "innerHTML" not in block and "textContent" in block


class TestOwnerExemption:
    def test_only_the_listed_emails_are_exempt(self):
        assert hardening.spend_exempt("michael.dano1523@gmail.com")
        assert hardening.spend_exempt("Michael.Dano1523@Gmail.com ")
        assert not hardening.spend_exempt("someone@else.com")
        assert not hardening.spend_exempt("")
        assert hardening.spend_exempt("a@x.io", env={"SPEND_CAP_EXEMPT_EMAILS": "a@x.io, b@x.io"})
        assert not hardening.spend_exempt("michael.dano1523@gmail.com", env={"SPEND_CAP_EXEMPT_EMAILS": "a@x.io"})

    @pytest.mark.asyncio
    async def test_an_exempt_user_is_not_blocked_by_the_cap(self):
        store = FakeStore()
        store.spend[("m", hardening.utc_day())] = 99.0
        assert await hardening.spend_blocked(store, "m", 1.0, cap=1.0) is not None
        assert await hardening.spend_blocked(store, "m", 1.0, cap=1.0, exempt=True) is None
