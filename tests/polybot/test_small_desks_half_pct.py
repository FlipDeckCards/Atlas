"""2026-11-27 (owner): XRP, SOL, ETH, WTI and natgas bet 0.5% of the balance, no bonuses; BTC 5m, gold and silver are unchanged."""
from openjarvis.polybot import directional
from openjarvis.polybot.config import from_env


def test_small_desks_half_pct():
    for a, p in (("xrp", 0.90), ("eth", 0.90), ("sol", 0.95), ("wti", 0.80), ("natgas", 0.80)):
        assert directional.size_pct(p, a) == 0.5
        assert from_env({}, asset=a).window_pct == 0.5
        assert directional.window_total_pct(a) == 0.5
        assert a not in directional.WIN_BONUS


def test_big_three_unchanged():
    assert directional.size_pct(0.80, "btc") == 3.0 and directional.size_pct(0.90, "btc") == 8.0
    assert directional.WIN_BONUS["btc"] == (3.0, 10.0)
    assert directional.size_pct(0.65, "gold") == 3.0 and directional.size_pct(0.90, "gold") == 8.0
    assert directional.size_pct(0.80, "silver") == 5.0 and directional.size_pct(0.90, "silver") == 8.0
    assert from_env({}, asset="gold").window_pct == 8.0 and from_env({}, asset="silver").window_pct == 8.0
