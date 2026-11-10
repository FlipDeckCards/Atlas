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


def test_btc_5m_blocks_flat_price_buys_at_any_price():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    btc = Desk(from_env({}, asset="btc"), None, None)
    # flat price (momentum between -0.3 and 0.3), any price, any time in the window: blocked
    assert btc._blocked_entry(200, 0.50, 0.0) and btc._blocked_entry(200, 0.35, -0.2)
    assert btc._blocked_entry(200, 0.85, 0.0) and btc._blocked_entry(200, 0.20, 0.3)
    # price moving: not blocked by this rule
    assert not btc._blocked_entry(200, 0.50, 1.0) and not btc._blocked_entry(200, 0.80, -0.5)
    # without momentum the flat rule does not apply
    assert not btc._blocked_entry(200, 0.50)
    # the other 5-minute desks keep only the last-minute rule
    eth = Desk(from_env({}, asset="eth"), None, None)
    assert not eth._blocked_entry(200, 0.50, 0.0)


def test_sol_5m_blocks_buys_when_price_is_already_moving_with_the_bet():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    sol = Desk(from_env({}, asset="sol"), None, None)
    assert sol._blocked_entry(200, 0.85, 0.5)            # moving with the bet: blocked at any price
    assert not sol._blocked_entry(200, 0.85, 0.1)        # flat or against: allowed
    btc = Desk(from_env({}, asset="btc"), None, None)
    assert not btc._blocked_entry(200, 0.85, 0.5)        # BTC's "with" trades are profitable: unchanged


def test_btc_15m_skips_down_bets_and_keeps_up_bets():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    b15 = Desk(from_env({}, asset="btc15"), None, None)
    assert b15._blocked_entry(400, 0.40, 0.0, "down")
    assert not b15._blocked_entry(400, 0.40, 0.0, "up")
    b5 = Desk(from_env({}, asset="btc"), None, None)
    assert not b5._blocked_entry(400, 0.40, None, "down")   # BTC 5m unchanged


def test_btc_5m_blocks_down_buys_at_70_to_79c_and_up_buys_under_40c():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    btc = Desk(from_env({}, asset="btc"), None, None)
    assert btc._blocked_entry(200, 0.75, 0.5, "down") and btc._blocked_entry(200, 0.70, 0.5, "down")
    assert not btc._blocked_entry(200, 0.80, 0.5, "down") and not btc._blocked_entry(200, 0.69, 0.5, "down")
    assert btc._blocked_entry(200, 0.35, 0.5, "up") and not btc._blocked_entry(200, 0.40, 0.5, "up")
    # Up at 70-79c and Down under 40c stay allowed (both profitable in the sample)
    assert not btc._blocked_entry(200, 0.75, 0.5, "up") and not btc._blocked_entry(200, 0.30, 0.5, "down")
    eth = Desk(from_env({}, asset="eth"), None, None)
    assert not eth._blocked_entry(200, 0.75, 0.5, "down")
