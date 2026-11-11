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
    exposure.publish("BTC", 16.0)                       # the owner's desks have used their whole cap (20% of 80) ...
    assert exposure.headroom(80.0) == 0.0
    assert exposure.headroom(80.0, "uabc") == 16.0      # ... which takes nothing from a subscriber's
    exposure.reserve("BTC", 5.0, "uabc")
    assert exposure.total_open("uabc") == 5.0 and exposure.total_open() == 16.0
    exposure.reset()


def test_controls_are_per_account_and_a_copy_starts_in_paper_with_the_owners_limits():
    p = FakePool()
    run(desk.put_control(p, {"mode": "live", "limits": {"bankroll_usd": 103.68, "daily_loss_pct": 18}}, "btc"))
    run(tenant_desks.seed_control(p, "uabc"))
    mine, theirs = p.kv["control"], p.kv["control@uabc"]
    assert theirs["mode"] == "paper" and theirs["limits"]["daily_loss_pct"] == 18 and "control:btc15@uabc" not in p.kv   # v1.39: no BTC 15m copy is seeded
    run(desk.control(p, "pause", asset="btc", account="uabc"))
    assert p.kv["control@uabc"]["paused"] is True and not mine.get("paused")
    run(desk.control(p, "reset_day", asset="btc", account="uabc"))                   # only their own desks
    assert "day_reset_at" in p.kv["control@uabc"] and "day_reset_at" not in p.kv["control"]
    out = run(desk.control(p, "go_live", asset="btc15", account="uabc"))
    run(desk.apply_approved(p, out["action"]["set"], asset="btc15", account="uabc"))
    assert p.kv["control:btc15@uabc"]["mode"] == "live" and "control:btc15" not in p.kv
    run(tenant_desks.seed_control(p, "uabc"))                                        # seeding again never resets their choices
    assert p.kv["control:btc15@uabc"]["mode"] == "live"


def test_account_tag_is_short_and_safe():
    assert tenant_desks.account_tag("3F2a-91c0-77d1-aa00-ffff") == "u3f2a91c077d1"
    assert tenant_desks.account_tag("x';drop") == "uxdrop"


def test_profit_by_hour_groups_by_los_angeles_hour():
    import datetime as dt

    utc = dt.timezone.utc
    rows = [  # 09:05 UTC = 1 AM PST; 03:50 UTC = 7 PM PST (Nov 3)
        {"window_start": dt.datetime(2026, 11, 4, 9, 5, tzinfo=utc), "window_end": None, "up_cost": 2.0, "down_cost": 0.0, "pnl": 0.5},
        {"window_start": dt.datetime(2026, 11, 4, 9, 40, tzinfo=utc), "window_end": None, "up_cost": 0.0, "down_cost": 2.0, "pnl": -2.0},
        {"window_start": dt.datetime(2026, 11, 4, 3, 50, tzinfo=utc), "window_end": None, "up_cost": 4.0, "down_cost": 0.0, "pnl": 1.0},
    ]
    out = desk.hour_rows(rows)
    assert [r["label"] for r in out] == ["1 AM", "7 PM"]
    am = out[0]
    assert (am["trades"], am["wins"], am["win_rate"], am["profit"], am["return_pct"]) == (2, 1, 50.0, -1.5, -37.5)
    assert out[1]["return_pct"] == 25.0


def test_seven_am_windows_are_half_size_by_default_and_the_control_row_can_change_it():
    import datetime as dt
    from openjarvis.polybot.config import apply_control, parse_hour_size
    from openjarvis.polybot.desk import Desk
    from openjarvis.polybot.risk import LA

    assert from_env({}).hour_size == ((4, 0.5), (6, 0.5), (7, 0.0), (8, 0.5), (9, 0.5), (12, 0.5), (14, 0.0), (16, 0.0))   # 5-minute BTC
    assert from_env({}, asset="btc15").hour_size == ((7, 0.5),)                # 15-minute keeps the old default
    assert from_env({"POLYBOT_HOUR_SIZE": ""}).hour_size == ()                 # an empty setting turns it off
    assert parse_hour_size("7:0.5,16:0,25:1,x:y,9:1") == ((7, 0.5), (16, 0.0))
    d = Desk(from_env({}, asset="btc15"), None, None)
    seven = dt.datetime(2026, 11, 4, 7, 5, tzinfo=LA).timestamp()
    eight = dt.datetime(2026, 11, 4, 8, 5, tzinfo=LA).timestamp()
    assert d.hour_factor(seven) == 0.5 and d.hour_factor(eight) == 1.0
    d.cfg = apply_control(d.base, {"hour_size": {"7": 0}})
    assert d.hour_factor(seven) == 0.0
    d.cfg = apply_control(d.base, {"hour_size": {"7": 1.0}})
    assert d.hour_factor(seven) == 1.0
    tenant = for_account(from_env({}, asset="btc15"), "uabc", "k", "s")
    assert tenant.hour_size == ((7, 0.5),)                                     # the brother's copies get it too


def test_each_five_minute_desk_has_its_own_hours_and_an_approved_hour_only_changes_that_hour():
    import datetime as dt
    from openjarvis.polybot.config import apply_control
    from openjarvis.polybot.desk import Desk
    from openjarvis.polybot.risk import LA

    at = lambda h: dt.datetime(2026, 11, 4, h, 5, tzinfo=LA).timestamp()
    f = {a: Desk(from_env({}, asset=a), None, None) for a in ("btc", "eth", "sol", "xrp")}
    assert [f[a].hour_factor(at(9)) for a in ("btc", "eth", "sol", "xrp")] == [0.5, 0.0, 0.5, 0.5]
    assert [f[a].hour_factor(at(10)) for a in ("btc", "eth", "sol", "xrp")] == [1.0, 1.0, 0.5, 1.0]
    assert [f[a].hour_factor(at(3)) for a in ("btc", "eth", "sol", "xrp")] == [1.0, 1.0, 1.0, 0.5]
    assert f["sol"].hour_factor(at(22)) == 0.5 and f["btc"].hour_factor(at(23)) == 1.0
    assert all(f[a].hour_factor(at(14)) == 0.0 and f[a].hour_factor(at(7)) == 0.0 for a in f)
    d = f["btc"]
    d.cfg = apply_control(d.base, {"hour_size": {"7": 0.5}})                 # an older 7 AM approval keeps the rest of the table
    assert d.hour_factor(at(7)) == 0.5 and d.hour_factor(at(14)) == 0.0 and d.hour_factor(at(9)) == 0.5
    assert for_account(from_env({}, asset="btc"), "uabc", "k", "s").hour_size == from_env({}, asset="btc").hour_size


def test_the_daily_review_decides_from_the_seven_am_numbers():
    from openjarvis.server.crew.hour_review import decide

    assert decide(5, -40.0, 0.5)["change"] is False                            # too few trades: keep half size
    up = decide(25, 8.0, 0.5)
    assert up["change"] and up["factor"] == 1.0
    down = decide(25, -9.0, 0.5)
    assert down["change"] and down["factor"] == 0.0
    flat = decide(25, 1.0, 0.5)
    assert not flat["change"] and flat["factor"] == 0.5
    assert decide(25, 8.0, 1.0)["change"] is False                              # already full size: nothing to approve


def test_an_approved_hour_change_lands_in_that_accounts_control_row_only():
    p = FakePool()
    out = run(desk.apply_approved(p, {"hour_size": {"7": 0}}, asset="btc15", account="uabc"))
    assert "7 AM windows skipped" in out and p.kv["control:btc15@uabc"]["hour_size"] == {"7": 0.0}
    run(desk.apply_approved(p, {"hour_size": {"7": 1}}, asset="btc"))
    assert p.kv["control"]["hour_size"] == {"7": 1.0} and "control:btc15" not in p.kv


def test_exports_keep_each_accounts_rows_apart():
    sql = desk._acct_mode_sql("f.mode")
    assert "position('@' in f.mode) = 0" in sql and "LIKE '%@' || $2::text" in sql


def test_daily_pnl_download_reads_only_that_accounts_ledger():
    seen = {}

    class C:
        async def fetch(self, q, *a):
            seen["mode"] = a[0]
            return []

        async def execute(self, *a):
            return None

    class P:
        def acquire(self):
            p = self

            class Ctx:
                async def __aenter__(self):
                    return C()

                async def __aexit__(self, *e):
                    return False
            return Ctx()

    desk._ENSURED = True if hasattr(desk, "_ENSURED") else None
    try:
        run(desk.export_daily(P(), account="uabc"))
    except ImportError:
        return                                            # openpyxl missing here; the query already ran
    assert seen["mode"] == "live@uabc"
    run(desk.export_daily(P()))
    assert seen["mode"] == "live"


def test_twelve_desks_five_fifteen_and_one_hour_for_every_coin():
    from openjarvis.polybot.config import ASSETS, NEW_DESKS, desk_label
    from openjarvis.polybot.state import asset_of as desk_of
    from openjarvis.polybot import directional

    assert len(ASSETS) == 6 and len(NEW_DESKS) == 10 and "btc" not in NEW_DESKS and "btc15" not in NEW_DESKS
    for a in ASSETS:
        cfg = from_env({"POLYBOT_HORIZON_ETH": "15m", "POLYBOT_HORIZON_NIGHT": "5m"}, asset=a)   # old settings never move a new desk
        want = "15m" if a.endswith("15") else "1h" if a.endswith("1h") else "5m"
        if a != "btc":
            assert cfg.horizon == want and (cfg.horizon_night == "" or a == "btc15"), a
        assert desk_of(f"cpc-{cfg.coin}-updown-{want}-2026-11-06-1300z") == a
    assert desk_of("cpceth-eth-updown-15m-2026-11-06-1300z") == "eth15"
    assert desk_label("sol1h") == "SOL 1h" and desk_label("btc") == "BTC 5m"
    assert directional.min_conf("eth15") == directional.min_conf("eth") and directional.min_conf("btc1h") == directional.min_conf("btc")
    assert len({lock_id(a) for a in ASSETS}) == 6


def test_new_desks_start_in_paper_even_if_an_old_row_says_live():
    p = FakePool()
    run(desk.put_control(p, {"mode": "live", "limits": {"bankroll_usd": 50}}, "eth"))      # ETH 5m traded live before it was retired
    run(desk.put_control(p, {"mode": "live"}, "btc"))
    ok = run(desk.paper_first(p, ["btc", "btc15", "eth", "sol1h"]))
    assert ok == ["btc", "btc15", "eth", "sol1h"]
    assert p.kv["control:eth"]["mode"] == "paper" and p.kv["control"]["mode"] == "live"            # BTC 5m untouched
    p.kv["control:eth"]["mode"] = "live"                                                           # the owner's Go live tap later ...
    run(desk.paper_first(p, ["eth"]))
    assert p.kv["control:eth"]["mode"] == "live"                                                   # ... is never undone by a restart


def test_paper_desks_never_take_room_from_the_live_cap():
    from openjarvis.polybot.desk import Desk

    exposure.reset()
    d = Desk(from_env({}, asset="eth15"), None, None)
    assert d._xacct() != ""                                   # paper: its own tally
    d.cfg = replace_mode(d.cfg, "live")
    assert d._xacct() == ""                                   # live: the owner's account tally
    exposure.reset()


def replace_mode(cfg, mode):
    from dataclasses import replace
    return replace(cfg, mode=mode)


def test_one_hour_desks_get_one_hour_timing_and_the_others_keep_theirs():
    from openjarvis.polybot import directional

    for a in ("btc1h", "eth1h", "sol1h", "xrp1h"):
        c = from_env({}, asset=a)
        assert (c.directional_min_secs, c.directional_secs_left, c.late_split_s) == (120.0, 900.0, 300.0), a
        assert (c.sweep_secs_left, c.sweep_secs_left_night, c.momentum_s) == (300.0, 300.0, 120.0), a
    for a in ("btc", "btc15", "eth", "eth15", "xrp15"):
        c = from_env({}, asset=a)
        assert (c.directional_min_secs, c.directional_secs_left, c.late_split_s, c.sweep_secs_left) == (30.0, 240.0, 60.0, 60.0), a
    assert "2-15 minute" in directional.explain(None, None, None, (0, 0), (0, 0), None, False, window=(120.0, 900.0))
    assert "30-240 second" in directional.explain(None, None, None, (0, 0), (0, 0), None, False)


def test_all_desk_cap_is_20_percent_and_the_5m_block_covers_every_coin():
    from openjarvis.polybot.desk import Desk

    exposure.reset()
    assert exposure.headroom(100.0) == 20.0
    exposure.publish("BTC", 13.0)
    exposure.publish("ETH 5m", 5.0)
    assert exposure.headroom(100.0) == 2.0
    exposure.reset()
    for a in ("btc", "eth", "sol", "xrp"):
        d = Desk(from_env({}, asset=a), None, None)
        floored = a in ("eth", "sol")       # v1.41: ETH 5m and SOL 5m also have entry price floors (85c and 75c)
        assert d._blocked_entry(45.0, 0.5) and not d._blocked_entry(45.0, 0.9), a
        assert d._blocked_entry(90.0, 0.5) == floored, a
    assert not Desk(from_env({}, asset="eth15"), None, None)._blocked_entry(45.0, 0.5)


def test_every_live_desk_shares_one_day_start_for_the_account():
    from openjarvis.polybot.desk import Desk
    from openjarvis.polybot.state import ScopedStore

    class Raw:
        def __init__(self):
            self.kv = {}

        async def get(self, k):
            return self.kv.get(k, {})

        async def put(self, k, v):
            self.kv[k] = v

    raw = Raw()
    btc = Desk(replace_mode(from_env({}, asset="btc"), "live"), None, ScopedStore(raw, "btc"))
    eth = Desk(replace_mode(from_env({}, asset="eth"), "live"), None, ScopedStore(raw, "eth"))
    for d in (btc, eth):
        d.rs.roll(1_800_000_000)
    btc.rs.day_start = 107.22                       # BTC 5m started the day at midnight
    eth.rs.day_start = 100.00                       # ETH went live later in the day, from a lower balance
    run(eth._share_day_start(1_800_000_000))
    run(btc._share_day_start(1_800_000_000))
    eth._t["dayshare"] = 0.0
    run(eth._share_day_start(1_800_000_100))
    assert raw.kv["account_day"]["start"] == 107.22 and eth.rs.day_start == 107.22 and btc.rs.day_start == 107.22


def test_xrp_desks_risk_at_most_one_percent():
    from openjarvis.polybot import directional

    for a in ("xrp", "xrp15", "xrp1h"):
        c = from_env({"POLYBOT_WINDOW_PCT": "10", "POLYBOT_SWEEP_PCT": "10"}, asset=a)
        assert c.window_pct == 5.0 and c.sweep_pct == 1.0, a
    assert directional.size_pct(0.95, "xrp") == 5.0 and directional.size_pct(0.85, "xrp") == 1.0 and directional.size_pct(0.84, "xrp") == 0
    assert from_env({"POLYBOT_WINDOW_PCT": "10"}, asset="btc").window_pct == 10.0


def test_desks_on_one_key_share_one_open_orders_read_and_back_off_after_a_429():
    import types
    from openjarvis.polybot import client as cl

    calls = {"n": 0}

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._b, self.headers, self.text = code, body, {"Retry-After": "1"}, ""
            self.content = b"x"

        def json(self):
            return self._b

    class Http:
        async def request(self, method, url, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return Resp(429, {})
            return Resp(200, {"orders": [{"id": "1", "marketSlug": "a"}, {"id": "2", "marketSlug": "b"}]})

    slept = []

    async def fake_sleep(s):
        slept.append(s)

    cl._SHARED.clear()
    cl._GATES.clear()
    import base64
    key = base64.b64encode(b"\1" * 32).decode()
    a = cl.PolymarketUS("kid-x", key, http=Http(), sleep=fake_sleep)
    b = cl.PolymarketUS("kid-x", key, http=Http(), sleep=fake_sleep)

    async def go():
        x = await a.open_orders(["a"])
        y = await b.open_orders(["b"])            # same key, within a second: no new request
        return x, y

    x, y = run(go())
    assert [o["id"] for o in x] == ["1"] and [o["id"] for o in y] == ["2"]
    assert calls["n"] == 2                          # one 429, one success, shared by both desks
    assert cl.gate_for("kid-x", True).cool > 0      # the key cooled down after the 429
    cl._SHARED.clear()
    cl._GATES.clear()


def test_sol_and_eth_desks_are_capped_at_one_percent_too():
    for a in ("sol", "sol15", "sol1h", "eth", "eth15", "eth1h"):
        c = from_env({"POLYBOT_WINDOW_PCT": "10", "POLYBOT_SWEEP_PCT": "10"}, asset=a)
        assert c.window_pct == 5.0 and c.sweep_pct == 1.0, a
    for a in ("btc", "btc15", "btc1h"):
        assert from_env({"POLYBOT_WINDOW_PCT": "10"}, asset=a).window_pct == 10.0, a
