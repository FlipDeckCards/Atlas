"""2026-11-18 diagnostic: a decided, allowed bet that sends no order says which limit stopped it (once per window, side, reason)."""
from types import SimpleNamespace

from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def _d():
    d = Desk(from_env({}, asset="xrp"), None, None)
    d.fair = 0.85
    return d


W = SimpleNamespace(slug="w1", end=1200.0, min_qty=1.0)


def test_names_the_tightest_limit_when_the_bet_is_too_small():
    d = _d()
    d._no_order(W, 1000.0, "up", 0.78, budget_usd=0.40, lot_usd=7.5, headroom=30.0, room=15.0, usd=0.40, size_here=50.0, ok_win=True)
    rows = d.ledger.take_candidates()
    assert rows[-1]["blocked_by"] == "no order: bet too small (day budget or hour size)"


def test_nothing_on_offer_and_window_rule_and_dedup():
    d = _d()
    d._no_order(W, 1000.0, "up", 0.78, 10.0, 7.5, 30.0, 15.0, 7.5, 0.5, True)
    d._no_order(W, 1001.0, "up", 0.78, 10.0, 7.5, 30.0, 15.0, 7.5, 0.5, True)       # same window, side, reason: once
    d._no_order(W, 1002.0, "down", 0.70, 10.0, 7.5, 30.0, 15.0, 7.5, 50.0, False, "a bet on the other side is open")
    reasons = [r["blocked_by"] for r in d.ledger.take_candidates()]
    assert reasons == ["no order: nothing on offer", "no order: window rule"]
