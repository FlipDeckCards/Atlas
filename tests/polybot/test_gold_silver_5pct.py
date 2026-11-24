"""Owner, 2026-11-24: gold and silver bet a flat 5% per window (was 3%); WTI stays 3%, natgas 1%; BTC and XRP are unchanged."""
from openjarvis.polybot import directional
from openjarvis.polybot.config import from_env


def test_gold_and_silver_are_five_percent_and_the_rest_unchanged():
    for a in ("gold",):      # silver moved to 75% / 5% / 8% tiers the same day (tests/polybot/test_silver_tiers.py)
        assert from_env({}, asset=a).window_pct == 5.0, a
        assert directional.window_total_pct(a) == 5.0, a
        assert directional.size_pct(0.60, a) == 5.0 and directional.size_pct(0.59, a) == 0.0, a      # same 60% floor
    assert from_env({}, asset="wti").window_pct == 3.0 and directional.size_pct(0.70, "wti") == 3.0
    assert from_env({}, asset="natgas").window_pct == 1.0 and directional.size_pct(0.70, "natgas") == 1.0
    assert directional.window_total_pct("xrp") == 3.0 and directional.window_total_pct("btc") == 10.0


def test_a_five_percent_window_on_a_three_hundred_dollar_account():
    assert directional.window_room({}, 300.0, "gold") == 15.0
