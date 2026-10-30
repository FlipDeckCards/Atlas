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
            self.kv[args[1] if len(args) > 1 else "control"] = json.loads(args[0])

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
    assert "needs_approval" in out and out["action"] == {"type": "desk_control", "asset": "btc", "set": {"mode": "live"}}
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
    assert "PAPER scoreboard (strategy v" in t and "10 windows traded, win rate 60.0%, total profit +3.20" in t
    assert "LIVE scoreboard: no settled trades yet." in t


def test_analysis_cuts_profit_by_condition():
    rows = [
        # an Up bought cheap while BTC was falling (lost), and one with BTC rising (won)
        {"side": "up", "price": 0.30, "qty": 2.0, "cost": 0.6, "features": json.dumps({"edge": 0.05, "momentum_z": -1.0, "secs_left": 200}), "up": False},
        {"side": "up", "price": 0.40, "qty": 1.0, "cost": 0.4, "features": json.dumps({"edge": 0.08, "momentum_z": 0.9, "secs_left": 100}), "up": True},
        # a Down at long price 0.70 (costs 0.30) while BTC was falling: moving with the bet (won)
        {"side": "down", "price": 0.70, "qty": 1.0, "cost": 0.3, "features": json.dumps({"edge": 0.12, "momentum_z": -0.8, "secs_left": 40}), "up": False},
    ] * 10

    class C(FakeConn):
        async def fetch(self, sql, *a):
            return rows

    class P(FakePool):
        def acquire(self):
            class Ctx:
                async def __aenter__(s):
                    return C(self.kv)

                async def __aexit__(s, *a):
                    return False
            return Ctx()

    desk._READY.clear()
    a = run(desk.analysis(P(), "paper"))
    mom = {r["condition"]: r for r in a["groups"]["momentum_with"]}
    assert mom["price moving against the bet"]["win_rate"] == 0.0 and mom["price moving against the bet"]["per_contract"] == pytest.approx(-0.30)
    assert mom["price moving with the bet"]["win_rate"] == 100.0
    assert "30 settled fills" in desk.analysis_text(a)


def test_each_desk_has_its_own_control_row():
    pool = FakePool()
    run(desk.control(pool, "pause", asset="eth"))
    assert pool.kv["control:eth"]["paused"] is True and "control" not in pool.kv
    out = run(desk.control(pool, "go_live", asset="sol"))
    assert out["action"]["asset"] == "sol" and "SOL" in out["needs_approval"]
    run(desk.apply_approved(pool, {"mode": "live"}, "sol"))
    assert pool.kv["control:sol"]["mode"] == "live" and "control" not in pool.kv


def test_reset_daily_stop_marks_every_desk():
    pool = FakePool()
    out = run(desk.control(pool, "reset_day", asset="btc"))
    assert "reset" in out["done"]
    assert all(pool.kv[k].get("day_reset_at") for k in ("control", "control:eth", "control:sol", "control:xrp"))
