"""2026-11-18 (owner): bets that never filled won ~93% live; the ones that filled ~75%. Taker bets now pay up to 1c through the best
price, and a bet with no fill after 3 s is logged "order missed" and decided again (all rules re-checked), at most 3 tries per method
per window and never more than 2c over the first try's price."""
from types import SimpleNamespace

from openjarvis.polybot import desk as desk_mod
from openjarvis.polybot.brokers import BookView
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def _desk():
    d = Desk(from_env({}, asset="xrp"), None, None)
    d.fair_model, d.fair = 0.92, 0.85
    d.book = BookView(bids=[(0.20, 30)], offers=[(0.78, 20)], last_trade_px=0.78, last_trade_ts=999.0, ts=1000.0)
    d.broker = SimpleNamespace(resting={})
    return d


def _w(slug="w1", end=1200.0):
    return SimpleNamespace(slug=slug, end=end)


def test_taker_limit_pays_one_cent_through():
    d = _desk()
    assert d._taker_limit("up", d.book) == 0.79                  # Up ask 78c -> limit 79c
    assert d._taker_limit("down", d.book) == 0.19                # Up bid 20c -> Down limit 81c (sell Up at 19c)


def test_a_missed_bet_is_logged_and_freed_after_three_seconds():
    d = _desk()
    d._place("w1", "directional", "up", 7.8, 10.0, price=0.78, now=1000.0)
    d._retry_missed(_w(), 1002.0)
    assert d._taken("w1", "directional")                         # too soon
    d._retry_missed(_w(), 1003.5)
    assert not d._taken("w1", "directional")                     # freed for a fresh decision
    rows = d.ledger.take_candidates()
    assert any(r.get("blocked_by", "").startswith("order missed (1 of 3") for r in rows)


def test_a_filled_or_resting_bet_is_not_retried():
    d = _desk()
    d._place("w1", "directional", "up", 7.8, 10.0, price=0.78, now=1000.0)
    d._filled[("w1", "up")] = 4.0
    d._retry_missed(_w(), 1010.0)
    assert d._taken("w1", "directional")
    d2 = _desk()
    d2._place("w1", "directional", "up", 7.8, 10.0, price=0.78, now=1000.0)
    d2.broker.resting = {"up": SimpleNamespace(quote=SimpleNamespace(side="up"))}
    d2._retry_missed(_w(), 1010.0)
    assert d2._taken("w1", "directional")


def test_three_tries_at_most_and_two_cents_over_the_first_price():
    d = _desk()
    for i in range(desk_mod.RETRY_MAX):
        assert d._retry_price_ok("w1", "directional", "up", 0.80)
        d._place("w1", "directional", "up", 7.8, 10.0, price=0.78 if i == 0 else 0.80, now=1000.0 + 10 * i)
        d._retry_missed(_w(), 1005.0 + 10 * i)
    assert d._taken("w1", "directional")                         # third miss: gave up, the window stays taken
    assert not d._retry_price_ok("w1", "directional", "up", 0.81)   # 3c over the first 78c
    assert not d._retry_price_ok("w1", "directional", "down", 0.70) # never flips side


def test_a_miss_is_cross_checked_against_fresh_reads_once_per_window():
    import asyncio

    class C:
        def __init__(self):
            self.calls = []

        async def _req(self, method, path, **kw):
            self.calls.append(path)
            if path.endswith("/book"):
                return {"marketData": {"offers": [{"px": {"value": "0.81"}, "qty": "40"}], "bids": [{"px": {"value": "0.18"}, "qty": "40"}]}}
            return {"marketData": {"marketSlug": "w1", "bestAsk": {"value": "0.90"}, "bestBid": {"value": "0.88"}, "currentPx": {"value": "0.895"}}}
    d = _desk()
    d.c = C()
    logs = []
    d.log = lambda lvl, msg: logs.append(msg)

    async def run():
        d._place("w1", "directional", "up", 7.8, 10.0, price=0.78, now=1000.0)
        d._retry_missed(_w(), 1003.5)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    asyncio.run(run())
    probe = [m for m in logs if "miss check" in m]
    assert probe and "fresh signed book 81c" in probe[0] and "bot book 78c" in probe[0]
    assert any("'bestAsk': '0.90'" in m and "signed bbo" in m for m in probe) and all(len(m) <= 500 for m in probe)   # 2026-11-20


def test_order_probe_logs_polymarkets_record_of_a_missed_order():
    import asyncio
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    d = Desk(from_env({}, asset="gold"), None, None)
    logs = []
    d.log = lambda lvl, msg: logs.append(msg)

    class C:
        async def order(self, oid):
            return {"state": "ORDER_STATE_CANCELED", "cumQuantity": "0", "price": {"value": "0.76"}, "text": "no liquidity"}
    d.c = C()
    d.broker = type("B", (), {"last_order": {("m", "up"): "OID1"}, "rejects": []})()
    asyncio.run(d._order_probe("m", "up", "edge", 1))
    assert "OID1" in logs[-1] and "ORDER_STATE_CANCELED" in logs[-1] and "no liquidity" in logs[-1]
    d.broker = type("B", (), {"last_order": {}, "rejects": ["up 5 @ 0.76: HTTP 400 price band"]})()
    asyncio.run(d._order_probe("m", "up", "edge", 2))
    assert "no order id was sent" in logs[-1] and "price band" in logs[-1]


def test_send_probe_logs_the_book_as_the_order_goes_out():
    import asyncio

    d = _desk()
    logs = []
    d.log = lambda lvl, msg: logs.append(msg)
    d.clock = lambda: 1001.0

    class C:
        async def _req(self, method, path, auth=False, tries=1):
            return {"marketData": {"offers": [{"px": {"value": "0.80"}, "qty": {"value": "12"}}, {"px": {"value": "0.81"}, "qty": {"value": "50"}}],
                                   "bids": [{"px": {"value": "0.20"}, "qty": {"value": "30"}}]}}
    d.c = C()
    asyncio.run(d._send_probe("w1", "up", "edge", 1, 0.78))
    assert "order sent edge up (try 1)" in logs[-1] and "decided at 78c" in logs[-1] and "bot book 78c" in logs[-1]
    assert "signed book at send 80c offers [80c x12, 81c x50]" in logs[-1]
    asyncio.run(d._send_probe("w1", "down", "edge", 2, 0.80))
    assert "signed book at send 80c" in logs[-1]                 # Down ask = 1 - best Up bid (20c)
