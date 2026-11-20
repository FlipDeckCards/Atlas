from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def test_btc_5m_skips_buys_under_70c():
    d = Desk(from_env({}, asset="btc"), None, None)
    assert d._block_rule(150.0, 0.69, 1.0, "up") == "btc 5m under 70c"
    assert d._block_rule(150.0, 0.64, -1.0, "down") == "btc 5m under 70c"
    assert d._block_rule(150.0, 0.80, 1.0, "up") is None
    x = Desk(from_env({}, asset="xrp"), None, None)
    assert x._block_rule(150.0, 0.69, 1.0, "up") != "btc 5m under 70c"


def test_btc_5m_allows_up_and_down_at_70_to_79c():
    # owner, 2026-11-18: floor 70c (was 75c); 2026-11-19: Down at 70-79c no longer blocked either
    d = Desk(from_env({}, asset="btc"), None, None)
    assert d._block_rule(150.0, 0.72, 0.5, "up") is None
    assert d._block_rule(150.0, 0.70, 0.5, "up") is None
    assert d._block_rule(150.0, 0.72, -0.5, "down") is None
    assert d._block_rule(150.0, 0.69, -0.5, "down") == "btc 5m under 70c"
