from types import SimpleNamespace

from openjarvis.polybot.state import Ledger


def test_fill_and_settle_mark_ledger_urgent():
    led = Ledger("btc")
    assert not led.urgent
    led.fill("live", SimpleNamespace(ts=1.0, slug="s", side="up", price=0.8, qty=5, cost=4.0, order_id="o"), 0.85)
    assert led.urgent
    led.take()
    assert not led.urgent
    led.window({"slug": "s", "mode": "live", "pnl": 1.0})
    assert led.urgent
    led.take()
    led.event("info", "x", 1.0)
    led.candidate({"slug": "s"})
    assert not led.urgent                       # ordinary log lines still wait for the minute's flush
