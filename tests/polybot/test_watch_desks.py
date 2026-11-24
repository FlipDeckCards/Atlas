"""2026-11-19 (owner): commodity desks (gold, silver, WTI, natgas 15m) are full desks like the crypto ones, on Polymarket's
commodity price feed, betting a flat 1% from 60% with a 1% cap per window. They start in paper; Go live takes them live."""
import asyncio

from openjarvis.polybot import config, directional
from openjarvis.polybot.config import apply_control, from_env
from openjarvis.polybot.desk import Desk


def test_commodity_desks_run_by_default_and_can_be_turned_off():
    assert config.watch_from_env({}) == ["gold", "silver", "wti", "natgas"]
    assert config.watch_from_env({"POLYBOT_WATCH": "off"}) == []
    assert config.assets_from_env({"POLYBOT_ASSETS": "btc,xrp"}) == ["btc", "xrp"]
    assert set(config.COMMODITIES) <= set(config.ASSETS)


def test_commodity_config_15m_one_percent_and_live_only_by_tap():
    env = {"POLYBOT_ALLOW_LIVE": "true", "POLYBOT_KEY_ID": "k", "POLYBOT_SECRET_KEY": "s"}
    for a in config.COMMODITIES:
        c = from_env(env, asset=a)
        assert c.price_kind == "commodity" and c.horizon == "15m" and c.coin == a and not c.watch_only
        pct = {"natgas": 1.0, "gold": 5.0, "silver": 8.0}.get(a, 3.0)    # owner, 2026-11-24: gold 5%, silver window 8%; WTI 3%, natgas 1%
        assert c.window_pct == pct
        assert c.hour_size == () and c.trade_start == "00:00" and c.trade_end == "23:59"     # every hour, full size
        if a == "silver":      # owner, 2026-11-24: silver 75-84% = 5%, 85%+ = 8%, no trade under 75%
            assert directional.size_pct(0.85, a) == 8.0 and directional.size_pct(0.75, a) == 5.0 and directional.size_pct(0.74, a) == 0.0
        else:
            assert directional.size_pct(0.85, a) == pct and directional.size_pct(0.60, a) == pct and directional.size_pct(0.59, a) == 0.0
        assert apply_control(c, {}).mode == "paper"                     # no control row: paper
        from dataclasses import replace
        assert apply_control(replace(c, allow_live=True, key_id="k", secret_key="s"), {"mode": "live"}).mode == "live"   # Go live tap
    assert config.desk_label("wti") == "WTI Oil 15m" and config.desk_label("gold") == "GOLD 15m"


def test_commodity_desk_reads_the_commodity_feed():
    calls = []

    class C:
        async def index_prices(self, asset, start, end=0, bucket="", kind="crypto"):
            calls.append((asset, kind))
            return []
    d = Desk(from_env({}, asset="gold"), C(), None)
    asyncio.run(d.load_index(1000.0, backfill=False))
    assert ("gold", "commodity") in calls


def test_commodity_market_slugs_belong_to_their_desk():
    from openjarvis.polybot.state import asset_of, desk_sql
    assert asset_of("cpausp-gold-updown-15m-2026-11-19-0400z") == "gold"
    assert asset_of("cpwtif-wti-updown-15m-2026-11-19-0400z") == "wti"
    assert asset_of("cpngf-natgas-updown-15m-2026-11-19-0400z") == "natgas"
    assert asset_of("cpceth-eth-updown-15m-2026-11-06-1300z") == "eth15"           # crypto 15m unchanged
    assert "'gold'" in desk_sql("slug")
    assert set(config.COMMODITIES) == {"gold", "silver", "wti", "natgas"}
