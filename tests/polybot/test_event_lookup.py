import asyncio
from openjarvis.polybot.client import PMError
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


class _Client:
    def __init__(self, start):
        self.start = start
        self.asked = []

    async def market(self, slug):
        self.asked.append(("market", slug))
        raise PMError("unable to process", status=404)

    async def event_markets(self, slug):
        self.asked.append(("event", slug))
        import datetime as dt
        iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        return [{"slug": "aec-btc-up-5m-real", "title": "BTC Up or Down 5m",
                 "assetPriceTerms": {"windowStart": iso(self.start), "windowEnd": iso(self.start + 300), "priceToBeat": "74000.5"}}]


def test_event_address_resolves_to_the_market_address():
    start = 1762897500                       # a 5-minute boundary
    d = Desk(from_env({}, asset="btc"), None, None)
    d.c = _Client(start)
    w = asyncio.run(d.window_by_slug(start + 20))
    assert w is not None and w.slug == "aec-btc-up-5m-real" and w.price_to_beat == 74000.5
    assert ("event", d.slug_for(start)) in d.c.asked
