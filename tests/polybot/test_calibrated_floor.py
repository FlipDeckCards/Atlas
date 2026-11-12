from openjarvis.polybot import directional


def test_btc_calibrated_floor_is_80_percent():
    assert directional.min_conf("btc", calibrated=True) == 0.80
    assert directional.min_conf("btc15", calibrated=True) == 0.80
    assert directional.size_pct(0.78, "btc", calibrated=True) == 0.0    # below the floor: no trade
    assert directional.size_pct(0.80, "btc", calibrated=True) == 1.0    # at the floor: the 80-83 lot (owner, 2026-11-11: was 7%)
    assert directional.size_pct(0.85, "btc", calibrated=True) == 4.0    # 84-87 (was 8%)
    assert directional.size_pct(0.90, "btc", calibrated=True) == 8.0    # 88%+ (was 10%)
