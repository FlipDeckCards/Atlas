"""Owner, 2026-11-24: silver trades from 75% confidence: 75-84% = 5%, 85%+ = 8%, window limit 8%. Gold stays a flat 5% from 60%."""
from openjarvis.polybot import directional
from openjarvis.polybot.config import from_env


def test_silver_floor_and_tiers():
    s = lambda p: directional.size_pct(p, "silver")
    assert s(0.74) == 0.0 and s(0.749) == 0.0
    assert s(0.75) == 5.0 and s(0.80) == 5.0 and s(0.849) == 5.0
    assert s(0.85) == 8.0 and s(0.97) == 8.0
    assert directional.min_conf("silver") == 0.75


def test_silver_window_limit_is_eight_percent_and_gold_is_unchanged():
    assert from_env({}, asset="silver").window_pct == 8.0 and directional.window_total_pct("silver") == 8.0
    assert directional.window_room({}, 300.0, "silver") == 24.0
    assert directional.size_pct(0.70, "wti") == 1.0 and directional.size_pct(0.70, "natgas") == 1.0


def test_the_losing_silver_fills_would_have_been_blocked():
    assert directional.size_pct(0.719, "silver") == 0.0 and directional.size_pct(0.727, "silver") == 0.0     # Nov 20 and Nov 24 losses
    assert all(directional.size_pct(p, "silver") == 5.0 for p in (0.775, 0.78, 0.799))                       # the three wins under 80%
