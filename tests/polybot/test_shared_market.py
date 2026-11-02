"""BTC 5m and BTC Late trade the same Polymarket market: each desk cancels only its own orders and counts the other's spend."""
import asyncio

from openjarvis.polybot.brokers import LiveBroker
from openjarvis.polybot.config import SHARED_MARKET


class FakeClient:
    def __init__(self):
        self.market_cancels = 0
        self.cancelled = []

    async def cancel_all(self, slugs=None):
        self.market_cancels += 1
        return []

    async def cancel(self, oid, slug):
        self.cancelled.append(oid)


def test_cancel_all_cancels_only_this_desks_orders():
    c = FakeClient()
    b = LiveBroker(c)
    b.slug = "cpc-btc-updown-5m-1"
    # the broker's own resting orders are the only ones it may cancel
    b.resting = {"up": type("R", (), {"order_id": "mine-1", "quote": None, "left": 1.0})()}
    asyncio.run(b.cancel_all(0.0))
    assert c.market_cancels == 0            # never the market-wide cancel that would hit the other desk
    assert c.cancelled == ["mine-1"]
    assert b.resting == {}


def test_btc_5m_and_late_share_a_market():
    assert SHARED_MARKET["btc"] == ("btclate",)
    assert SHARED_MARKET["btclate"] == ("btc",)
