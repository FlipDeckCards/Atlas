def test_entry_rules_by_desk():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    btc = Desk(from_env({}, asset="btc"), None, None)
    btc15 = Desk(from_env({}, asset="btc15"), None, None)
    # 5-minute: 30-70c blocked only in the final 60 seconds
    assert btc._blocked_entry(45, 0.50) and not btc._blocked_entry(120, 0.50)
    assert not btc._blocked_entry(45, 0.80)
    # 15-minute: 70-90c blocked at any time
    assert btc15._blocked_entry(400, 0.75) and btc15._blocked_entry(600, 0.70)
    assert not btc15._blocked_entry(400, 0.90) and not btc15._blocked_entry(400, 0.60)
