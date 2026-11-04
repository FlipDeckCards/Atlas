"""A subscriber's own Polymarket Desk: copies of the owner's bots that share nothing with his (keys, rows, controls, caps, locks)."""

from __future__ import annotations

import asyncio

from openjarvis.polybot import exposure
from openjarvis.polybot.config import for_account, from_env, is_live_label, label_for
from openjarvis.polybot.runtime import lock_id
from openjarvis.polybot.state import event_asset, kv_key
from openjarvis.server.crew import desk, tenant_desks
from tests.polybot.test_desk_control import FakePool


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


OWNER_ENV = {"POLYMARKET_KEY_ID": "owner-kid", "POLYMARKET_SECRET_KEY": "owner-secret", "CREW_NOTIFY_URL": "https://ntfy.sh/owner"}


def test_a_subscribers_copy_uses_only_their_keys_and_labels():
    base = from_env(OWNER_ENV, asset="btc15")
    cfg = for_account(base, "uabc", "their-kid", "their-secret", "")
    assert (cfg.key_id, cfg.secret_key, cfg.notify_url) == ("their-kid", "their-secret", "")
    assert cfg.ledger == "paper@uabc" and cfg.live_ledger == "live@uabc"
    assert cfg.horizon == base.horizon and cfg.window_pct == base.window_pct and cfg.methods == base.methods   # the same bot
    assert base.ledger == "paper" and base.live_ledger == "live" and base.account == ""


def test_keys_labels_and_locks_never_collide_with_the_owners():
    assert kv_key("control", "btc") == "control" and kv_key("control", "btc15") == "control:btc15"
    assert kv_key("control", "btc", "uabc") == "control@uabc" and kv_key("status", "btc15", "uabc") == "status:btc15@uabc"
    assert event_asset("btc", "uabc") == "btc@uabc" and event_asset("btc") == "btc"
    assert is_live_label("live") and is_live_label("live@uabc") and is_live_label("live-late")
    assert not is_live_label("paper@uabc") and not is_live_label("paper")
    assert label_for("live", "") == "live"
    owner = {lock_id(a) for a in ("btc", "btc15", "eth", "sol", "xrp", "btclate")}
    theirs = {lock_id(a, "uabc") for a in ("btc", "btc15")}
    assert len(theirs) == 2 and not owner & theirs and lock_id("btc", "uabc") != lock_id("btc", "uxyz")


def test_cross_desk_cap_is_per_account():
    exposure.reset()
    exposure.publish("BTC", 20.0)                       # the owner's desks have used their whole cap ...
    assert exposure.headroom(80.0) == 0.0
    assert exposure.headroom(80.0, "uabc") == 20.0      # ... which takes nothing from a subscriber's
    exposure.reserve("BTC", 5.0, "uabc")
    assert exposure.total_open("uabc") == 5.0 and exposure.total_open() == 20.0
    exposure.reset()


def test_controls_are_per_account_and_a_copy_starts_in_paper_with_the_owners_limits():
    p = FakePool()
    run(desk.put_control(p, {"mode": "live", "limits": {"bankroll_usd": 103.68, "daily_loss_pct": 18}}, "btc"))
    run(tenant_desks.seed_control(p, "uabc"))
    mine, theirs = p.kv["control"], p.kv["control@uabc"]
    assert theirs["mode"] == "paper" and theirs["limits"]["daily_loss_pct"] == 18 and "control:btc15@uabc" in p.kv
    run(desk.control(p, "pause", asset="btc", account="uabc"))
    assert p.kv["control@uabc"]["paused"] is True and not mine.get("paused")
    run(desk.control(p, "reset_day", asset="btc", account="uabc"))                   # only their own desks
    assert "day_reset_at" in p.kv["control:btc15@uabc"] and "day_reset_at" not in p.kv["control"]
    out = run(desk.control(p, "go_live", asset="btc15", account="uabc"))
    run(desk.apply_approved(p, out["action"]["set"], asset="btc15", account="uabc"))
    assert p.kv["control:btc15@uabc"]["mode"] == "live" and "control:btc15" not in p.kv
    run(tenant_desks.seed_control(p, "uabc"))                                        # seeding again never resets their choices
    assert p.kv["control:btc15@uabc"]["mode"] == "live"


def test_account_tag_is_short_and_safe():
    assert tenant_desks.account_tag("3F2a-91c0-77d1-aa00-ffff") == "u3f2a91c077d1"
    assert tenant_desks.account_tag("x';drop") == "uxdrop"
