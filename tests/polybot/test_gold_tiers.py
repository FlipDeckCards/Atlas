"""Owner, 2026-11-24: gold trades from 60% confidence in three steps: 60-74% = 3%, 75-89% = 5%, 90%+ = 8%; window limit 8%."""
from openjarvis.polybot import directional
from openjarvis.polybot.config import from_env


def test_gold_tiers_and_floor():
    g = lambda p: directional.size_pct(p, "gold")
    assert g(0.59) == 0.0
    assert g(0.60) == 3.0 and g(0.70) == 3.0 and g(0.749) == 3.0
    assert g(0.75) == 5.0 and g(0.85) == 5.0 and g(0.899) == 5.0
    assert g(0.90) == 8.0 and g(0.97) == 8.0
    assert directional.min_conf("gold") == 0.60


def test_gold_window_limit_is_eight_percent_and_the_rest_unchanged():
    assert from_env({}, asset="gold").window_pct == 8.0 and directional.window_total_pct("gold") == 8.0
    assert directional.window_room({}, 300.0, "gold") == 24.0
    assert directional.size_pct(0.85, "silver") == 8.0 and directional.size_pct(0.74, "silver") == 0.0
    assert directional.size_pct(0.70, "wti") == 0.5 and directional.size_pct(0.70, "natgas") == 0.5 and directional.size_pct(0.85, "xrp") == 0.5
