from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def test_btc_5m_skips_buys_under_75c():
    d = Desk(from_env({}, asset="btc"), None, None)
    assert d._block_rule(150.0, 0.73, 1.0, "up") == "btc 5m under 75c"
    assert d._block_rule(150.0, 0.64, -1.0, "down") == "btc 5m under 75c"
    assert d._block_rule(150.0, 0.80, 1.0, "up") is None
    x = Desk(from_env({}, asset="xrp"), None, None)
    assert x._block_rule(150.0, 0.73, 1.0, "up") != "btc 5m under 75c"
