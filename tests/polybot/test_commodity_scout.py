"""2026-11-18: the read-only commodity scout builds the right event addresses and finds a price feed name, placing no orders."""
import asyncio

from openjarvis.polybot import scout


def test_event_address_matches_polymarket():
    # the owner's link: https://polymarket.us/event/gold-updown-15m-2026-11-19-0345z (10:45-11:00 PM ET on Nov 18)
    start = scout.window_start(1795059900 + 400)               # 2026-11-19 03:45z + 400 s
    assert scout.event_slug("gold", start) == "gold-updown-15m-2026-11-19-0345z"


class _C:
    def __init__(self):
        self.calls = []

    async def event_markets(self, ev):
        return [{"slug": "cpc" + ev, "title": "x"}]

    async def book(self, slug):
        return {"bids": [{"px": "0.40", "qty": "10"}], "offers": [{"px": "0.42", "qty": "7"}]}

    async def settlement(self, slug):
        return 1.0

    async def _req(self, method, path, params=None, tries=3, **k):
        self.calls.append(params["assets"])
        if params["assets"] == "metal:xau":
            return {"series": [{"candles": [{"tickCount": 3, "close": "4100"}]}]}
        return {"series": []}

    def place(self, *a, **k):
        raise AssertionError("the scout must never trade")


def test_scan_reads_markets_and_finds_a_feed_without_trading(monkeypatch):
    async def nosleep(_):
        return None
    monkeypatch.setattr(scout.asyncio, "sleep", nosleep)
    c = _C()
    s = scout.Scout(c)
    rows = asyncio.run(s.scan(1795059900 + 60))
    gold = [r for r in rows if r["coin"] == "gold"][0]
    assert gold["market"] == "cpcgold-updown-15m-2026-11-19-0345z"
    assert gold["price_feed"] == "metal:xau"
    assert s.price_id["silver"] is None
