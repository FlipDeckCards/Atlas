"""Sol's tools: the model can ask for media, but the server enforces confirmation, ownership, the caps and the speech-only rule.

Offline: a fake chat model answers with scripted tool calls; storage, catalog and providers are in-memory fakes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from openjarvis.server import atlas_ui, auth_routes, hardening
from openjarvis.server.app import create_app
from openjarvis.server.media import image as image_mod
from openjarvis.server.media import service, sol_tools
from openjarvis.server.media import sound as sound_mod
from openjarvis.server.media.catalog import MemoryCatalog
from openjarvis.server.media.service import MediaServiceError
from openjarvis.server.media.storage import MemoryStorage
from tests.server._media_fakes import (
    API_KEY,
    JWT_SECRET,
    FakeProvider,
    FakeStore,
    completion,
    make_ctx,
    tool_call,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in ("MEDIA_ENABLED", "DAILY_MEDIA_SPEND_CAP_USD", "MEDIA_GLOBAL_DAILY_CAP_USD", "DAILY_SPEND_CAP_USD", "MEDIA_USER_QUOTA_BYTES", "RL_CHAT_USER_MAX"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("OPENJARVIS_API_KEY", API_KEY)
    monkeypatch.setattr(hardening, "LIMITER", hardening.RateLimiter())
    monkeypatch.setattr(sol_tools, "PENDING", sol_tools.PendingActions())
    monkeypatch.setattr(hardening, "chat_cost_usd", lambda r, litellm_module=None: 0.001)


def media_on(monkeypatch, cap="5"):
    monkeypatch.setenv("MEDIA_ENABLED", "1")
    monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", cap)


@pytest.fixture
def providers(monkeypatch):
    img, snd = FakeProvider("image"), FakeProvider("sound")
    monkeypatch.setitem(image_mod.PROVIDERS, image_mod.DEFAULT_PROVIDER, img)
    monkeypatch.setitem(sound_mod.PROVIDERS, sound_mod.DEFAULT_PROVIDER, snd)
    return img, snd


@pytest.fixture
def shared():
    return FakeStore(), MemoryCatalog(), MemoryStorage()


def ctx_for(shared, uid="alice"):
    store, catalog, storage = shared
    return make_ctx(uid, store, catalog, storage)


async def costs(_resp):
    return None


class TestToolDefinitions:
    def test_the_five_tools_exist_and_nothing_else(self):
        assert [t["function"]["name"] for t in sol_tools.TOOLS] == ["generate_image", "generate_speech", "list_library", "open_item", "edit_item"]
        assert sol_tools.PAID_TOOLS == {"generate_image", "generate_speech"}
        for t in sol_tools.TOOLS:                                              # the model is never given a user id to fill in
            assert "user" not in str(t["function"]["parameters"]).lower()

    def test_the_speech_tool_says_it_is_speech_only(self):
        d = next(t for t in sol_tools.TOOLS if t["function"]["name"] == "generate_speech")["function"]["description"]
        assert "speech only" in d and "NOT sound effects" in d


class TestUnknownAndBadTools:
    @pytest.mark.asyncio
    async def test_an_unknown_tool_name_is_refused(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        ctx = ctx_for(shared)
        for name in ("delete_everything", "generate_video", "GENERATE_IMAGE", "", "__import__('os')"):
            out = await sol_tools.execute_tool(ctx, name, {"prompt": "x"}, user_message="hi")
            assert "no tool named" in out["text"] and out["ui"] == []
        assert sol_tools.PENDING.get("alice") is None and providers[0].calls == [] and shared[1].items == {}

    @pytest.mark.asyncio
    async def test_bad_arguments_do_nothing(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        ctx = ctx_for(shared)
        assert "not valid" in (await sol_tools.execute_tool(ctx, "generate_image", None, user_message="hi"))["text"]
        assert "nothing to make" in (await sol_tools.execute_tool(ctx, "generate_image", {"prompt": "  "}, user_message="hi"))["text"]
        assert sol_tools.PENDING.get("alice") is None and providers[0].calls == []


class TestConfirmation:
    @pytest.mark.asyncio
    async def test_a_paid_tool_call_without_confirmation_does_not_run(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        ctx = ctx_for(shared)
        out = await sol_tools.execute_tool(ctx, "generate_image", {"prompt": "a red fox"}, user_message="make me a red fox image")
        assert "NOT RUN YET" in out["text"] and "about $" in out["text"]
        assert providers[0].calls == [] and shared[1].items == {} and shared[0].spend == {} and shared[2].objects == {}
        p = sol_tools.PENDING.get("alice")
        assert p and p.tool == "generate_image" and p.armed is False and p.estimate_usd == 0.01

    @pytest.mark.asyncio
    async def test_the_model_turn_reads_the_action_back_with_the_cost_and_waits_for_a_yes(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        ctx = ctx_for(shared)
        script = [completion(None, [tool_call("generate_image", {"prompt": "a red fox"})]), completion("Sure, I'll make that.")]
        fake = AsyncMock(side_effect=script)
        out = await sol_tools.run_model_with_tools(ctx, [{"role": "user", "content": "make me a red fox image"}], "m", "make me a red fox image",
                                                   acompletion=fake, on_cost=costs, max_tokens=100)
        assert "Ready to make an image" in out["reply"] and "estimated cost about $0.0100" in out["reply"] and "yes" in out["reply"].lower()
        assert providers[0].calls == [] and shared[1].items == {}               # still nothing made
        assert fake.await_args_list[0].kwargs["tools"] == sol_tools.TOOLS
        assert sol_tools.PENDING.get("alice").armed is True

    @pytest.mark.asyncio
    async def test_an_explicit_yes_in_the_next_message_runs_it_once(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        ctx = ctx_for(shared)
        await sol_tools.run_model_with_tools(ctx, [], "m", "make a fox", acompletion=AsyncMock(side_effect=[completion(None, [tool_call("generate_image", {"prompt": "a red fox"})]), completion("ok")]),
                                             on_cost=costs, max_tokens=100)
        done = await sol_tools.pre_turn(ctx, "yes")
        assert done["reply"] == "Saved: a red fox" and done["ui"][0]["type"] == "saved" and done["ui"][0]["item"]["title"] == "a red fox"
        assert len(providers[0].calls) == 1 and len(shared[1].items) == 1 and shared[0].spent("media:alice") == 0.01
        again = await sol_tools.pre_turn(ctx, "yes")                           # a second yes does nothing
        assert again is None and len(providers[0].calls) == 1

    @pytest.mark.asyncio
    async def test_speech_follows_the_same_confirmation(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        ctx = ctx_for(shared)
        await sol_tools.run_model_with_tools(ctx, [], "m", "make a sound that says hello", acompletion=AsyncMock(side_effect=[completion(None, [tool_call("generate_speech", {"text": "hello"})]), completion("ok")]),
                                             on_cost=costs, max_tokens=100)
        assert providers[1].calls == []
        done = await sol_tools.pre_turn(ctx, "Yes.")
        assert done["reply"].startswith("Saved: hello") and providers[1].calls == [{"text": "hello", "lang": "en"}]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", ["yes but make it blue", "no", "maybe", "sure thing, but first tell me the price", "what will it cost?", "yes yes yes please", "make it a cat instead"])
    async def test_anything_but_a_plain_yes_cancels_the_action(self, shared, providers, monkeypatch, answer):
        media_on(monkeypatch)
        ctx = ctx_for(shared)
        await sol_tools.run_model_with_tools(ctx, [], "m", "make a fox", acompletion=AsyncMock(side_effect=[completion(None, [tool_call("generate_image", {"prompt": "fox"})]), completion("ok")]),
                                             on_cost=costs, max_tokens=100)
        out = await sol_tools.pre_turn(ctx, answer)
        assert out is None and providers[0].calls == [] and shared[1].items == {}
        assert sol_tools.PENDING.get("alice") is None                          # used up: a later 'yes' cannot revive it
        assert await sol_tools.pre_turn(ctx, "yes") is None and providers[0].calls == []

    @pytest.mark.asyncio
    async def test_a_yes_cannot_confirm_in_the_same_turn_as_the_request(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        ctx = ctx_for(shared)
        # the model asks for the action while the user's own message already says yes: still only a read-back
        out = await sol_tools.run_model_with_tools(ctx, [], "m", "make a fox image, yes do it",
                                                   acompletion=AsyncMock(side_effect=[completion(None, [tool_call("generate_image", {"prompt": "fox"})]), completion("done!")]),
                                                   on_cost=costs, max_tokens=100)
        assert providers[0].calls == [] and "Reply 'yes'" in out["reply"]
        # and a pending action that has not been read back yet cannot be confirmed
        sol_tools.PENDING.clear("alice")
        sol_tools.PENDING.create("alice", "generate_image", {"prompt": "fox"}, 0.01, "make an image")
        assert await sol_tools.pre_turn(ctx, "yes") is None and providers[0].calls == []

    @pytest.mark.asyncio
    async def test_voice_counts_only_as_an_explicit_yes_after_the_readback(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        ctx = ctx_for(shared)
        for heard in ("yes", "Yes!", "yeah", "go ahead", "Yes, please."):
            assert sol_tools.is_explicit_yes(heard), heard
        for heard in ("yes make it big", "uh yes I guess", "yes and also", "", "mm"):
            assert not sol_tools.is_explicit_yes(heard), heard
        await sol_tools.run_model_with_tools(ctx, [], "m", "x", acompletion=AsyncMock(side_effect=[completion(None, [tool_call("generate_image", {"prompt": "fox"})]), completion("ok")]),
                                             on_cost=costs, max_tokens=100)
        assert (await sol_tools.pre_turn(ctx, "yes, make it big")) is None and providers[0].calls == []

    @pytest.mark.asyncio
    async def test_a_pending_action_expires_and_belongs_to_one_user(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        t = [0.0]
        pend = sol_tools.PendingActions(clock=lambda: t[0])
        monkeypatch.setattr(sol_tools, "PENDING", pend)
        pend.create("alice", "generate_image", {"prompt": "fox"}, 0.01, "make an image")
        pend.arm("alice")
        assert await sol_tools.pre_turn(ctx_for(shared, "bob"), "yes", pend) is None and providers[0].calls == []      # bob cannot confirm alice's action
        assert pend.get("alice") is not None
        t[0] += sol_tools.PENDING_TTL_S + 1
        assert await sol_tools.pre_turn(ctx_for(shared, "alice"), "yes", pend) is None and providers[0].calls == []     # expired


class TestCapsBeforeTheProvider:
    @pytest.mark.asyncio
    async def test_a_confirmed_action_still_stops_at_the_media_cap_before_any_provider_call(self, shared, providers, monkeypatch):
        media_on(monkeypatch, cap="0.015")
        ctx = ctx_for(shared)
        shared[0].spend[("media:alice", hardening.utc_day())] = 0.01
        sol_tools.PENDING.create("alice", "generate_image", {"prompt": "fox"}, 0.01, "make an image")
        sol_tools.PENDING.arm("alice")
        out = await sol_tools.pre_turn(ctx, "yes")
        assert "couldn't" in out["reply"] and "limit" in out["reply"].lower()
        assert providers[0].calls == [] and shared[1].items == {}

    @pytest.mark.asyncio
    async def test_run_paid_raises_429_at_the_cap_and_403_when_there_is_no_budget(self, shared, providers, monkeypatch):
        media_on(monkeypatch, cap="0.005")
        with pytest.raises(MediaServiceError) as e:
            await sol_tools.run_paid(ctx_for(shared), "generate_image", {"prompt": "fox"})
        assert e.value.status == 429 and providers[0].calls == []
        monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "0")
        with pytest.raises(MediaServiceError) as e:
            await sol_tools.run_paid(ctx_for(shared), "generate_image", {"prompt": "fox"})
        assert e.value.status == 403 and providers[0].calls == []

    @pytest.mark.asyncio
    async def test_the_storage_quota_and_global_ceiling_apply_to_tools_too(self, shared, providers, monkeypatch):
        media_on(monkeypatch, cap="5")
        monkeypatch.setenv("MEDIA_GLOBAL_DAILY_CAP_USD", "0.005")
        with pytest.raises(MediaServiceError) as e:
            await sol_tools.run_paid(ctx_for(shared), "generate_speech", {"text": "hello"})
        assert e.value.status == 429 and providers[1].calls == []
        monkeypatch.delenv("MEDIA_GLOBAL_DAILY_CAP_USD")
        monkeypatch.setenv("MEDIA_USER_QUOTA_BYTES", "10")
        with pytest.raises(MediaServiceError) as e:
            await sol_tools.run_paid(ctx_for(shared), "generate_speech", {"text": "hello"})
        assert e.value.status == 413 and shared[1].items == {} and shared[2].objects == {}      # the result did not fit, so nothing was saved

    def test_tools_are_offered_only_when_media_is_fully_on(self, monkeypatch):
        state = type("S", (), {"media_storage": MemoryStorage()})()
        assert sol_tools.tools_available(state) is False
        monkeypatch.setenv("MEDIA_ENABLED", "1")
        assert sol_tools.tools_available(state) is False                       # no budget
        monkeypatch.setenv("DAILY_MEDIA_SPEND_CAP_USD", "1")
        assert sol_tools.tools_available(state) is True
        assert sol_tools.tools_available(type("S", (), {})()) is False          # no storage configured (and none in the environment)


class TestOwnLibraryOnly:
    @pytest.mark.asyncio
    async def test_the_tools_only_touch_the_callers_library(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        a, b = ctx_for(shared, "alice"), ctx_for(shared, "bob")
        a_item = await service.generate_and_save(a, "image", prompt="alice fox")
        await service.generate_and_save(b, "image", prompt="bob cat")
        listed = (await sol_tools.execute_tool(a, "list_library", {"kind": "all"}, user_message="list"))["text"]
        assert "alice fox" in listed and "bob cat" not in listed
        # bob names alice's item: not found, and nothing opens or changes
        opened = await sol_tools.execute_tool(b, "open_item", {"id": a_item.id}, user_message="open")
        assert "not found" in opened["text"] and opened["ui"] == []
        edited = await sol_tools.execute_tool(b, "edit_item", {"id": a_item.id, "operation": "brightness", "params": {"factor": 1.5}}, user_message="edit")
        assert "not found" in edited["text"] and len(shared[1].items) == 2
        # an argument that tries to name another owner is ignored: the caller comes from the request
        spoof = await sol_tools.execute_tool(b, "list_library", {"kind": "all", "user_id": "alice"}, user_message="list")
        assert "alice fox" not in spoof["text"]

    @pytest.mark.asyncio
    async def test_open_and_edit_work_on_the_callers_own_items(self, shared, providers, monkeypatch):
        media_on(monkeypatch)
        a = ctx_for(shared, "alice")
        item = await service.generate_and_save(a, "image", prompt="alice fox")
        opened = await sol_tools.execute_tool(a, "open_item", {"id": item.id}, user_message="open it")
        assert opened["ui"] == [{"type": "open_item", "id": item.id}]
        edited = await sol_tools.execute_tool(a, "edit_item", {"id": item.id, "operation": "brightness", "params": {"factor": 1.5}}, user_message="brighten")
        assert edited["text"].startswith("Saved: ") and edited["ui"][0]["type"] == "saved"
        assert len(shared[1].items) == 2 and any(i.edited_from_id == item.id for i in shared[1].items.values())
        assert len(providers[0].calls) == 1                                    # editing is free: no provider call


class TestSpeechOnly:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("ask", ["make a gunshot sound effect", "generate an explosion sound", "create a sound effect of rain", "can you make thunder", "make me a jingle", "generate some background music"])
    async def test_speech_is_refused_for_sound_effect_requests(self, shared, providers, monkeypatch, ask):
        media_on(monkeypatch)
        ctx = ctx_for(shared)
        assert sol_tools.looks_like_sound_effect(ask)
        out = await sol_tools.execute_tool(ctx, "generate_speech", {"text": "bang"}, user_message=ask)
        assert out["text"].startswith("Refused") and "can't generate sound effects" in out["text"]
        assert sol_tools.PENDING.get("alice") is None and providers[1].calls == []
        pre = await sol_tools.pre_turn(ctx, ask)                              # and the model is never even asked
        assert pre["reply"] == sol_tools.SFX_REPLY and "speech" in pre["reply"] and "library" in pre["reply"]

    def test_ordinary_requests_are_not_mistaken_for_sound_effects(self):
        for ok in ("make a sound that says hello", "make me a red fox image", "tell me about music", "what is a siren?", "read this aloud: thunder is loud", "generate speech that says explosion"):
            assert not sol_tools.looks_like_sound_effect(ok), ok


class TestChatEndpoint:
    @pytest.fixture
    def app_client(self, shared, providers, monkeypatch):
        store, catalog, storage = shared
        app = create_app(MagicMock(), "m", api_key=API_KEY, require_auth=True)
        app.state.session_store, app.state.media_catalog, app.state.media_storage = store, catalog, storage
        return TestClient(app, raise_server_exceptions=False)

    def hdr(self, uid="alice"):
        return {"Authorization": "Bearer " + auth_routes.create_token(uid, uid + "@x.io")}

    def test_with_media_off_chat_is_exactly_as_before_and_gets_no_tools(self, app_client, monkeypatch):
        fake = AsyncMock(return_value=completion("plain answer"))
        monkeypatch.setattr(atlas_ui.litellm, "acompletion", fake)
        r = app_client.post("/api/chat", json={"message": "hello"}, headers=self.hdr())
        assert r.status_code == 200 and r.json()["reply"] == "plain answer" and "ui_actions" not in r.json()
        assert "tools" not in fake.await_args.kwargs

    def test_ask_confirm_save_and_open_through_the_chat(self, app_client, shared, providers, monkeypatch):
        media_on(monkeypatch)
        fake = AsyncMock(side_effect=[completion(None, [tool_call("generate_image", {"prompt": "a red fox"})]), completion("On it.")])
        monkeypatch.setattr(atlas_ui.litellm, "acompletion", fake)
        r1 = app_client.post("/api/chat", json={"message": "make me a red fox image"}, headers=self.hdr())
        assert r1.status_code == 200 and "Reply 'yes'" in r1.json()["reply"] and "$0.0100" in r1.json()["reply"]
        assert fake.await_args_list[0].kwargs["tools"] and providers[0].calls == []
        r2 = app_client.post("/api/chat", json={"message": "yes"}, headers=self.hdr())
        assert r2.json()["reply"] == "Saved: a red fox" and r2.json()["ui_actions"][0]["type"] == "saved"
        assert len(providers[0].calls) == 1 and len(shared[1].items) == 1
        assert fake.await_count == 2                                            # the confirmation itself did not call the model
        item_id = r2.json()["ui_actions"][0]["item"]["id"]
        fake.side_effect = [completion(None, [tool_call("open_item", {"id": item_id})]), completion("Here it is.")]
        r3 = app_client.post("/api/chat", json={"message": "open my fox"}, headers=self.hdr())
        assert r3.json()["ui_actions"] == [{"type": "open_item", "id": item_id}]

    def test_bob_cannot_confirm_alices_action_through_the_chat(self, app_client, shared, providers, monkeypatch):
        media_on(monkeypatch)
        monkeypatch.setattr(atlas_ui.litellm, "acompletion", AsyncMock(side_effect=[completion(None, [tool_call("generate_image", {"prompt": "fox"})]), completion("ok"), completion("hi bob")]))
        app_client.post("/api/chat", json={"message": "make a fox"}, headers=self.hdr("alice"))
        rb = app_client.post("/api/chat", json={"message": "yes"}, headers=self.hdr("bob"))
        assert rb.json()["reply"] == "hi bob" and providers[0].calls == [] and shared[1].items == {}

    def test_a_sound_effect_request_never_reaches_the_model_or_a_provider(self, app_client, providers, monkeypatch):
        media_on(monkeypatch)
        fake = AsyncMock(return_value=completion("x"))
        monkeypatch.setattr(atlas_ui.litellm, "acompletion", fake)
        r = app_client.post("/api/chat", json={"message": "make a gunshot sound effect"}, headers=self.hdr())
        assert r.json()["reply"] == sol_tools.SFX_REPLY and fake.await_count == 0 and providers[1].calls == []

    def test_the_chat_spend_cap_still_applies_before_any_model_call_with_tools_on(self, app_client, shared, monkeypatch):
        media_on(monkeypatch)
        monkeypatch.setenv("DAILY_SPEND_CAP_USD", "0")
        fake = AsyncMock(return_value=completion("x"))
        monkeypatch.setattr(atlas_ui.litellm, "acompletion", fake)
        assert app_client.post("/api/chat", json={"message": "hi"}, headers=self.hdr()).status_code == 429
        assert fake.await_count == 0

    def test_the_slash_shortcuts_still_work_and_save_to_the_library(self, app_client, shared, providers, monkeypatch):
        media_on(monkeypatch)
        r = app_client.post("/api/media/image", json={"prompt": "a red fox"}, headers=self.hdr())
        assert r.status_code == 200 and r.json()["item"]["title"] == "a red fox"
        s = app_client.post("/api/media/sound", json={"text": "hello"}, headers=self.hdr())
        assert s.status_code == 200 and len(shared[1].items) == 2
        from pathlib import Path

        html = Path(atlas_ui.__file__).read_text(encoding="utf-8")
        assert "(image|sound)" in html and "sendMedia(mediaCmd[1]" in html
