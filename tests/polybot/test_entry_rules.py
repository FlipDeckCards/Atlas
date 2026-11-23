def test_entry_rules_by_desk():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    btc = Desk(from_env({}, asset="btc"), None, None)
    btc15 = Desk(from_env({}, asset="btc15"), None, None)
    # 5-minute: 30-70c blocked only in the final 60 seconds
    xrp = Desk(from_env({}, asset="xrp"), None, None)
    assert xrp._blocked_entry(45, 0.50) and xrp._blocked_entry(120, 0.50)          # 2026-11-20: XRP 5m floored at 70c
    assert not btc._blocked_entry(45, 0.80)
    assert btc._block_rule(120, 0.50) == "btc 5m under 70c"            # 2026-11-11: BTC 5m's 75c floor
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
    assert not btc._blocked_entry(200, 0.80, 1.0) and not btc._blocked_entry(200, 0.80, -0.5)
    # without momentum the flat rule does not apply
    assert not btc._blocked_entry(200, 0.80)
    # the other 5-minute desks keep only the last-minute rule
    eth = Desk(from_env({}, asset="eth"), None, None)
    assert eth._block_rule(200, 0.50, 0.0) == "eth 5m under 85c"


def test_sol_5m_blocks_buys_when_price_is_already_moving_with_the_bet():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    sol = Desk(from_env({}, asset="sol"), None, None)
    # 2026-11-17: side-aware "chasing momentum" (more than 1 sd toward the bet) replaces v1.35's any-side 0.3 sd block
    assert sol._blocked_entry(200, 0.85, 1.2, "up") and sol._blocked_entry(200, 0.85, -1.2, "down")
    assert not sol._blocked_entry(200, 0.85, 0.5, "up")   # mild move with the bet: allowed now
    assert not sol._blocked_entry(200, 0.85, 1.2, "down")  # moving against the bet: allowed
    btc = Desk(from_env({}, asset="btc"), None, None)
    assert not btc._blocked_entry(200, 0.85, 0.5)        # BTC's "with" trades are profitable: unchanged


def test_btc_15m_skips_down_bets_and_keeps_up_bets():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    b15 = Desk(from_env({}, asset="btc15"), None, None)
    assert b15._blocked_entry(400, 0.40, 0.0, "down")
    assert not b15._blocked_entry(400, 0.40, 0.0, "up")
    b5 = Desk(from_env({}, asset="btc"), None, None)
    assert not b5._blocked_entry(400, 0.85, None, "down")   # BTC 5m: no Down rule at 85c (its 75c floor blocks under 75c)


def test_btc_5m_blocks_down_buys_at_70_to_79c_only():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    btc = Desk(from_env({}, asset="btc"), None, None)
    # owner, 2026-11-19: Down at 70-79c is no longer blocked on BTC 5m
    assert not btc._blocked_entry(200, 0.75, 0.5, "down") and not btc._blocked_entry(200, 0.70, 0.5, "down")
    assert not btc._blocked_entry(200, 0.80, 0.5, "down")
    assert btc._block_rule(200, 0.69, 0.5, "down") == "btc 5m under 70c"    # 2026-11-11: under 75c the floor applies
    # Up at 75-79c stays allowed; anything under 75c is now stopped by the floor, not an Up/Down rule
    assert not btc._blocked_entry(200, 0.75, 0.5, "up")
    assert btc._block_rule(200, 0.35, 0.5, "up") == "btc 5m under 70c" and btc._block_rule(200, 0.30, 0.5, "down") == "btc 5m under 70c"
    eth = Desk(from_env({}, asset="eth"), None, None)
    assert eth._block_rule(200, 0.75, 0.5, "down") == "eth 5m under 85c"   # v1.41: ETH 5m's floor is 85c, so 75c is blocked


def test_eth_5m_and_sol_5m_entry_price_floors():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    eth = Desk(from_env({}, asset="eth"), None, None)
    sol = Desk(from_env({}, asset="sol"), None, None)
    btc = Desk(from_env({}, asset="btc"), None, None)
    xrp = Desk(from_env({}, asset="xrp"), None, None)
    # ETH 5m: buys under 85c are blocked; 85c and up allowed
    assert eth._block_rule(200, 0.84, 0.0) == "eth 5m under 85c" and eth._blocked_entry(200, 0.60)
    assert eth._block_rule(200, 0.85, 0.0) is None and eth._block_rule(200, 0.95, 0.0) is None
    # SOL 5m: buys under 85c are blocked (owner, 2026-11-21; was 75c); 85c and up allowed (momentum rule still applies separately)
    assert sol._block_rule(200, 0.84, 0.0) == "sol 5m under 85c" and sol._block_rule(200, 0.75, 0.0) == "sol 5m under 85c"
    assert sol._block_rule(200, 0.85, 0.0) is None
    # BTC 5m is floored at 70c; XRP 5m too since 2026-11-20
    assert btc._block_rule(200, 0.40, 1.0) == "btc 5m under 70c" and btc._block_rule(200, 0.75, 1.0) is None
    assert xrp._block_rule(200, 0.50, 1.0) == "xrp 5m under 70c" and xrp._block_rule(200, 0.75, 1.0) is None


def test_natgas_floor_is_85c_and_the_other_commodities_are_unchanged():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    ng = Desk(from_env({}, asset="natgas"), None, None)
    gold = Desk(from_env({}, asset="gold"), None, None)
    assert ng._block_rule(200, 0.84, 0.5, "up") == "natgas under 85c" and ng._block_rule(200, 0.72, 0.5, "up") == "natgas under 85c"
    assert ng._block_rule(200, 0.85, 0.5, "up") is None and ng._block_rule(200, 0.91, 0.5, "up") is None
    assert gold._block_rule(200, 0.72, 0.5, "up") is None
