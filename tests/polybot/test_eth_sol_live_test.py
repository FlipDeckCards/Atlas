from openjarvis.polybot import directional
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def test_eth_flat_3_percent_from_85_and_sol_from_90():
    # owner: SOL floor 90% calibrated since 2026-11-17; ETH put back to 85% on 2026-11-22 (90% left it with no trades)
    for a, floor in (("eth", 0.85), ("sol", 0.90)):
        assert [directional.size_pct(p, a) for p in (0.84, 0.85, 0.899, 0.90, 0.99)] == [0.0 if p < floor else 0.5 for p in (0.84, 0.85, 0.899, 0.90, 0.99)]
        assert directional.min_conf(a, calibrated=True) == floor
        assert directional.window_total_pct(a) == 0.5
        assert from_env({"POLYBOT_WINDOW_PCT": "20"}, asset=a).window_pct == 0.5


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
    assert x._block_rule(200, 0.90, 1.5, "up") is None          # 3-4 min left (1-3 min at 70c+ has its own XRP rule)


def test_a_raw_89_reading_no_longer_clears_the_sol_floor():
    # 2026-11-17 11:18 AM: SOL bought Up on a raw 89% reading (about 85.5% calibrated) and lost
    from openjarvis.polybot import shadow
    d = Desk(from_env({}, asset="sol"), None, None)
    d.shadow.live_seed(shadow.LIVE_SEED["sol"])
    assert directional.size_pct(d._calib(0.89), "sol") == 0.0
    assert directional.size_pct(d._calib(0.95), "sol") == 0.5
