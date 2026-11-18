"""Owner, 2026-11-18: XRP 5m skips buys at 70c or more with 1-3 minutes left (they lost live); earlier or cheaper buys are allowed."""
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def test_xrp_skips_70c_plus_with_1_to_3_minutes_left():
    x = Desk(from_env({}, asset="xrp"), None, None)
    assert x._block_rule(150.0, 0.75, 0.5, "up") == "xrp 1-3 min at 70c+"
    assert x._block_rule(61.0, 0.90, 0.5, "down") == "xrp 1-3 min at 70c+"
    assert x._block_rule(180.0, 0.70, 0.5, "up") == "xrp 1-3 min at 70c+"


def test_xrp_still_buys_early_or_cheap():
    x = Desk(from_env({}, asset="xrp"), None, None)
    assert x._block_rule(200.0, 0.78, 0.5, "up") is None      # 3-4 minutes left
    assert x._block_rule(150.0, 0.65, 0.5, "up") is None      # under 70c
    assert x._block_rule(45.0, 0.85, 0.5, "up") is None       # last minute, outside 30-70c


def test_other_desks_are_untouched():
    for a in ("btc", "sol", "eth"):
        d = Desk(from_env({}, asset=a), None, None)
        assert d._block_rule(150.0, 0.95, 0.0, "up") != "xrp 1-3 min at 70c+"
