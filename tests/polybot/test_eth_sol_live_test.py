from openjarvis.polybot import directional
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def test_eth_sol_flat_3_percent_from_85():
    for a in ("eth", "sol"):
        assert [directional.size_pct(p, a) for p in (0.84, 0.85, 0.90, 0.99)] == [0.0, 3.0, 3.0, 3.0]
        assert directional.min_conf(a, calibrated=True) == 0.85
        assert directional.window_total_pct(a) == 3.0
        assert from_env({"POLYBOT_WINDOW_PCT": "20"}, asset=a).window_pct == 3.0


def test_eth_sol_enter_only_in_the_last_3_minutes():
    for a in ("eth", "sol"):
        assert from_env({}, asset=a).directional_secs_left == 180.0
        assert from_env({"POLYBOT_DIRECTIONAL_SECONDS": "240"}, asset=a).directional_secs_left == 180.0
    assert from_env({}, asset="btc").directional_secs_left == 240.0 and from_env({}, asset="xrp").directional_secs_left == 240.0


def test_no_chasing_on_eth_and_sol_only():
    for a in ("eth", "sol"):
        d = Desk(from_env({}, asset=a), None, None)
        assert d._block_rule(120, 0.90, 1.5, "up") == "chasing momentum"
        assert d._block_rule(120, 0.90, -1.5, "down") == "chasing momentum"
        assert d._block_rule(120, 0.90, -1.5, "up") is None          # moving against the bet: allowed
        assert d._block_rule(120, 0.90, 0.5, "down") is None
    x = Desk(from_env({}, asset="xrp"), None, None)
    assert x._block_rule(120, 0.90, 1.5, "up") is None
