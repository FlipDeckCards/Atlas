"""Crew routes and Helix chat with the crew: owner only, off unless CREW_ENABLED, answers once, start only after a plain yes. Offline."""

from __future__ import annotations

import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from openjarvis.server import atlas_ui, auth_routes, hardening
from openjarvis.server.app import create_app
from openjarvis.server.crew import helix as crew_helix
from openjarvis.server.crew.engine import CrewEngine
from openjarvis.server.crew.store import MemoryCrewStore
from tests.server._media_fakes import (
    API_KEY,
    JWT_SECRET,
    FakeStore,
    completion,
    tool_call,
)

OWNER, OWNER_EMAIL = "owner-1", "owner@x.io"
NOON_LA = dt.datetime(2026, 10, 21, 19, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in ("CREW_ENABLED", "CREW_DAILY_CAP_USD", "MEDIA_ENABLED", "STRIPE_SECRET_KEY", "ETSY_API_KEY", "ETSY_REDIRECT_URI", "RL_CHAT_USER_MAX"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("OPENJARVIS_API_KEY", API_KEY)
    monkeypatch.setenv("ANALYTICS_ADMIN_EMAILS", OWNER_EMAIL)
    monkeypatch.setenv("CREW_ENABLED", "true")
    monkeypatch.setattr(hardening, "LIMITER", hardening.RateLimiter())
    monkeypatch.setattr(hardening, "chat_cost_usd", lambda r, litellm_module=None: 0.001)
    monkeypatch.setattr(crew_helix, "CREW_PENDING", crew_helix.PendingActions())


@pytest.fixture
def world():
    store = MemoryCrewStore(clock=lambda: NOON_LA)
    planner = AsyncMock(return_value=completion(json.dumps({"title": "T", "tasks": [{"title": "Write", "role": "writer", "brief": "b"}]})))
    engine = CrewEngine(store, planner, env={"CREW_DAILY_CAP_USD": "5"}, cost_fn=lambda r: 0.01, now=lambda: NOON_LA,
                        models={"writing": "w", "lookup": "l", "default": "d"})
    app = create_app(MagicMock(), "m", api_key=API_KEY, require_auth=True)
    app.state.session_store, app.state.crew_engine = FakeStore(), engine
    client = TestClient(app, raise_server_exceptions=False)
    return type("W", (), {"client": client, "store": store, "engine": engine, "app": app})()


def owner():
    return {"Authorization": "Bearer " + auth_routes.create_token(OWNER, OWNER_EMAIL)}


def stranger():
    return {"Authorization": "Bearer " + auth_routes.create_token("bob", "bob@x.io")}


async def _question(store, kind="question", options=None, action=None):
    m = await store.create_mission(OWNER, "goal goal goal", 1.0, status="active")
    t = (await store.create_tasks(m["id"], OWNER, [{"idx": 0, "title": "Write", "role": "writer", "brief": "b", "depends_on": [], "budget_usd": 0.5}]))[0]
    await store.update_task(t["id"], status="waiting")
    return await store.add_question(m["id"], t["id"], OWNER, kind, "Which price?", options or ["$5", "$10"], action)


class TestAccess:
    def test_the_station_page_is_served_and_holds_no_data(self, world):
        r = world.client.get("/station")
        assert r.status_code == 200 and "Aibussol Station" in r.text and "/api/crew/state" in r.text

    def test_the_home_address_is_the_station_sign_in_not_the_privacy_page(self, world):
        home, station, privacy = world.client.get("/"), world.client.get("/station"), world.client.get("/privacy")
        assert home.status_code == 200 and home.text == station.text and "Aibussol Station" in home.text
        assert privacy.status_code == 200 and "Privacy Policy" in privacy.text and privacy.text != home.text

    def test_state_needs_a_login_and_the_owner(self, world):
        assert world.client.get("/api/crew/state").status_code == 401
        assert world.client.get("/api/crew/state", headers=stranger()).status_code == 403
        r = world.client.get("/api/crew/state", headers=owner())
        assert r.status_code == 200 and r.json()["enabled"] is True and "stripe" in r.json()["connections"]

    def test_off_unless_enabled(self, world, monkeypatch):
        monkeypatch.delenv("CREW_ENABLED")
        assert world.client.get("/api/crew/state", headers=owner()).json()["enabled"] is False
        assert world.client.post("/api/crew/missions", json={"goal": "Build me a planner product"}, headers=owner()).status_code == 503

    def test_a_stranger_cannot_start_answer_or_download(self, world):
        h = stranger()
        assert world.client.post("/api/crew/missions", json={"goal": "Build me a planner product"}, headers=h).status_code == 403
        assert world.client.post("/api/crew/questions/x/answer", json={"answer": "a"}, headers=h).status_code == 403
        assert world.client.get("/api/crew/outputs/x", headers=h).status_code == 403


class TestActions:
    def test_start_a_mission_from_the_station(self, world):
        r = world.client.post("/api/crew/missions", json={"goal": "Build me a planner product", "budget_usd": 2}, headers=owner())
        assert r.status_code == 201 and r.json()["mission"]["budget_usd"] == 2.0
        assert world.client.post("/api/crew/missions", json={"goal": "short"}, headers=owner()).status_code == 422

    def test_an_answer_is_taken_once(self, world):
        import asyncio
        q = asyncio.run(_question(world.store))
        url = f"/api/crew/questions/{q['id']}/answer"
        assert world.client.post(url, json={"answer": "$10"}, headers=owner()).status_code == 200
        assert world.client.post(url, json={"answer": "$5"}, headers=owner()).status_code == 409
        assert world.client.post("/api/crew/questions/nope/answer", json={"answer": "a"}, headers=owner()).status_code == 404

    def test_a_checkpoint_needs_approve_or_decline(self, world):
        import asyncio
        q = asyncio.run(_question(world.store, "checkpoint", ["Approve", "Decline"], {"platform": "stripe", "action": "create_product", "args": {}}))
        url = f"/api/crew/questions/{q['id']}/answer"
        assert world.client.post(url, json={"answer": "yes"}, headers=owner()).status_code == 400
        r = world.client.post(url, json={"approve": False}, headers=owner())
        assert r.status_code == 200 and "Declined" in r.json()["message"]

    def test_settings_and_downloads(self, world):
        import asyncio
        r = world.client.post("/api/crew/settings", json={"night_shift": True}, headers=owner())
        assert r.json()["settings"] == {"night_shift": True, "paused": False}
        m = asyncio.run(world.store.create_mission(OWNER, "goal goal goal", 1.0, status="active"))
        o = asyncio.run(world.store.add_output(m["id"], "t", OWNER, "plan.md", "# Plan"))
        d = world.client.get(f"/api/crew/outputs/{o['id']}", headers=owner())
        assert d.status_code == 200 and d.text == "# Plan" and "plan.md" in d.headers["content-disposition"]
        assert world.client.get("/api/crew/outputs/nope", headers=owner()).status_code == 404

    def test_etsy_connect_explains_what_is_missing(self, world):
        r = world.client.get("/api/crew/connect/etsy", headers=owner())
        assert r.status_code == 409 and "ETSY_API_KEY" in r.json()["detail"]

    def test_etsy_callback_rejects_an_unknown_state(self, world):
        r = world.client.get("/crew/connect/etsy/callback?code=abc&state=forged")
        assert r.status_code == 400 and "expired" in r.text


class TestHelixChat:
    def test_build_me_is_read_back_then_started_on_yes(self, world, monkeypatch):
        fake = AsyncMock(side_effect=[completion(None, [tool_call("start_mission", {"goal": "Build me a planner product for collectors"})]),
                                      completion("I can set the crew on that.")])
        monkeypatch.setattr(atlas_ui.litellm, "acompletion", fake)
        r = world.client.post("/api/chat", json={"message": "Helix, build me a planner product for collectors"}, headers=owner())
        assert r.status_code == 200 and "Reply 'yes'" in r.json()["reply"]
        names = [t["function"]["name"] for t in fake.await_args_list[0].kwargs["tools"]]
        assert "start_mission" in names
        assert world.store.missions == {}                                   # nothing started yet
        r2 = world.client.post("/api/chat", json={"message": "yes"}, headers=owner())
        assert "Mission started" in r2.json()["reply"] and len(world.store.missions) == 1
        assert fake.await_count == 2                                        # the yes needed no model call

    def test_other_users_do_not_get_crew_tools(self, world, monkeypatch):
        fake = AsyncMock(return_value=completion("hello"))
        monkeypatch.setattr(atlas_ui.litellm, "acompletion", fake)
        world.client.post("/api/chat", json={"message": "build me a business"}, headers=stranger())
        assert "tools" not in fake.await_args_list[0].kwargs
