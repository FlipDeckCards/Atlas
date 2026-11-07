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


def test_btc_5m_blocks_flat_price_buys_between_30_and_70c():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    btc = Desk(from_env({}, asset="btc"), None, None)
    # flat price (momentum between -0.3 and 0.3), 30-70c, any time in the window: blocked
    assert btc._blocked_entry(200, 0.50, 0.0) and btc._blocked_entry(200, 0.35, -0.2)
    # price moving, or buy outside 30-70c: not blocked
    assert not btc._blocked_entry(200, 0.50, 1.0) and not btc._blocked_entry(200, 0.80, 0.0)
    # without momentum the flat rule does not apply
    assert not btc._blocked_entry(200, 0.50)
    # the other 5-minute desks keep only the last-minute rule
    eth = Desk(from_env({}, asset="eth"), None, None)
    assert not eth._blocked_entry(200, 0.50, 0.0)
