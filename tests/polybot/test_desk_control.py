"""Aibussol's switches for the desk: what happens at once, and what only ever becomes an Approve card."""

from __future__ import annotations

import asyncio
import json

import pytest

from openjarvis.server.crew import desk


class FakeConn:
    def __init__(self, kv):
        self.kv = kv

    async def execute(self, sql, *args):
        if "INSERT INTO polybot_kv" in sql:
            self.kv["control"] = json.loads(args[0])

    async def fetchval(self, sql, *args):
        v = self.kv.get(args[0])
        return json.dumps(v) if v is not None else None


class FakePool:
    def __init__(self):
        self.kv = {}

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                return FakeConn(pool.kv)

            async def __aexit__(self, *a):
                return False
        return Ctx()


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def test_pause_resume_kill_are_immediate():
    p = FakePool()
    assert "paused" in run(desk.control(p, "pause"))["done"]
    assert p.kv["control"]["paused"] is True
    run(desk.control(p, "flatten"))
    assert p.kv["control"]["kill"] is True
    run(desk.control(p, "resume"))
    assert p.kv["control"]["paused"] is False and p.kv["control"]["kill"] is False


def test_go_live_is_only_ever_an_approve_card():
    p = FakePool()
    out = run(desk.control(p, "go_live"))
    assert "needs_approval" in out and out["action"] == {"type": "desk_control", "set": {"mode": "live"}}
    assert p.kv.get("control", {}).get("mode") != "live"
    run(desk.apply_approved(p, out["action"]["set"]))          # what the Approve tap runs
    assert p.kv["control"]["mode"] == "live"
    run(desk.control(p, "paper"))
    assert p.kv["control"]["mode"] == "paper"


def test_tighter_limits_apply_now_looser_need_approval():
    p = FakePool()
    out = run(desk.control(p, "set_limits", {"daily_loss_usd": 5}))
    assert "done" in out and p.kv["control"]["limits"]["daily_loss_usd"] == 5
    out = run(desk.control(p, "set_limits", {"bankroll_usd": 500}))
    assert "needs_approval" in out and p.kv["control"]["limits"]["bankroll_usd"] == 50
    run(desk.apply_approved(p, out["action"]["set"]))
    assert p.kv["control"]["limits"]["bankroll_usd"] == 500


def test_unknown_action_is_refused():
    with pytest.raises(ValueError):
        run(desk.control(FakePool(), "buy_everything"))


def test_status_text_keeps_paper_and_live_apart():
    s = {"status": {"mode": "paper", "reason": "Quoting.", "heartbeat": 1, "at_risk": 1.0, "day_pnl": 0.5},
         "control": {}, "running": True, "heartbeat_age_s": 3, "limits": desk.current_limits({}).__dict__, "events": [],
         "score": {"paper": {"all": {"trades": 10, "win_rate": 60.0, "profit": 3.2, "staked": 30.0, "return_pct": 10.7, "best": 1, "worst": -1},
                             "d7": {"profit": 3.2, "trades": 10}, "today": {"profit": 0.5, "trades": 2}, "fills": 20},
                   "live": {"all": {"trades": 0}, "d7": {}, "today": {}, "fills": 0}}}
    t = desk.status_text(s)
    assert "PAPER scoreboard: 10 windows traded, win rate 60.0%, total profit +3.20" in t
    assert "LIVE scoreboard: no settled trades yet." in t
