from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def test_last_60s_blocks_every_price_up_to_70c_on_5m_desks():
    for a in ("btc", "eth", "sol", "xrp"):
        d = Desk(from_env({}, asset=a), None, None)
        assert d._block_rule(45.0, 0.19, 0.5, "down") is not None, a      # Nov 20: XRP Down at 19c, 45 s left
    x = Desk(from_env({}, asset="xrp"), None, None)
    assert x._block_rule(45.0, 0.19, 0.5, "down") in ("xrp 5m under 70c", "last 60s under 70c")
    s = Desk(from_env({}, asset="sol"), None, None)
    assert s._block_rule(45.0, 0.20, 0.5, "up") == "sol 5m under 75c"
    b = Desk(from_env({}, asset="eth"), None, None)
    assert b._block_rule(45.0, 0.90, 0.5, "up") is None                 # 90c in the last minute still allowed


def test_xrp_floor_70c():
    x = Desk(from_env({}, asset="xrp"), None, None)
    assert x._block_rule(200.0, 0.60, 0.5, "down") == "xrp 5m under 70c"     # Nov 20 05:43 Down at 60c
    assert x._block_rule(200.0, 0.72, 0.5, "down") is None
