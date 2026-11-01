"""BTC 15m: a fifth desk trading BTC's 15-minute markets, kept apart from the BTC 5m desk."""

import asyncio

from openjarvis.polybot import directional
from openjarvis.polybot.config import ASSETS, assets_from_env, from_env
from openjarvis.polybot.runtime import lock_id
from openjarvis.polybot.state import asset_of, desk_sql, kv_key


def test_btc15_is_its_own_desk_on_btc_15m_markets():
    c = from_env({}, asset="btc15")
    assert (c.asset, c.coin, c.horizon, c.horizon_night) == ("btc15", "btc", "15m", "")
    assert ASSETS.index("btc15") == ASSETS.index("btc") + 1                      # listed right after BTC 5m
    assert "btc15" in assets_from_env({})


def test_shared_horizon_settings_never_turn_btc15_into_a_5m_desk():
    c = from_env({"POLYBOT_HORIZON": "5m", "POLYBOT_HORIZON_NIGHT": "5m"}, asset="btc15")
    assert (c.horizon, c.horizon_night) == ("15m", "")
    assert from_env({"POLYBOT_HORIZON_BTC15": "1h"}, asset="btc15").horizon == "1h"   # only its own setting changes it


def test_btc15_has_its_own_store_lock_and_history():
    assert kv_key("status", "btc15") == "status:btc15" and kv_key("status", "btc") == "status"
    assert len({lock_id("btc"), lock_id("btc15"), lock_id("eth"), lock_id("sol"), lock_id("xrp")}) == 5
    assert asset_of("cpc-btc-updown-15m-2026-10-31-2300z") == "btc15"
    assert asset_of("cpc-btc-updown-5m-2026-10-31-2300z") == "btc"
    assert asset_of("cpcsol-sol-updown-5m-2026-10-31-2300z") == "sol"
    assert "btc15" in desk_sql("f.slug") and "f.slug" in desk_sql("f.slug")


def test_btc15_uses_btc_tiers():
    assert directional.TIERS["btc15"] == directional.TIERS["btc"]
    assert directional.min_conf("btc15") == 0.80


def test_btc_desks_only_cancel_their_own_orders():
    from openjarvis.polybot.desk import Desk

    class C:
        def __init__(self):
            self.cancelled = []

        async def open_orders(self):
            return [{"id": "a", "marketSlug": "cpc-btc-updown-5m-2026-10-31-2300z"},
                    {"id": "b", "marketSlug": "cpc-btc-updown-15m-2026-10-31-2300z"},
                    {"id": "c", "marketSlug": "cpcsol-sol-updown-5m-2026-10-31-2300z"}]

        async def cancel(self, oid, slug):
            self.cancelled.append(oid)

    for asset, want in (("btc", ["a"]), ("btc15", ["b"])):
        c = C()
        d = Desk(from_env({}, asset=asset), c, store=None)
        asyncio.run(d.cancel_own_orders())
        assert c.cancelled == want, (asset, c.cancelled)
