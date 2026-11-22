"""Owner, 2026-11-18: XRP 5m - confidence sets the tier; a bet that is also one of the three "what wins" kinds (over 3 minutes left,
bought 30-70c, edge over 10c) gets +3% on top, capped at 10%. Otherwise the tier stands. Other desks are unchanged."""
from openjarvis.polybot import directional
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def _xrp(fair=0.80):
    d = Desk(from_env({}, asset="xrp"), None, None)
    d.fair = fair
    return d


def test_one_winning_kind_adds_three_points():
    d = _xrp(fair=0.80)
    assert d._win_bonus(5.0, "up", 0.78, 200.0)[0] == 8.0            # over 3 min left
    assert d._win_bonus(3.0, "up", 0.65, 100.0)[0] == 6.0            # bought 30-70c (fair 80 - 65 = 15c edge too: still +3 once)
    d.fair = 0.90
    assert d._win_bonus(5.0, "up", 0.78, 30.0)[0] == 8.0             # edge over 10c (fair 90c vs 78c), last minute


def test_capped_at_ten_and_no_bonus_without_a_kind():
    d = _xrp(fair=0.80)
    assert d._win_bonus(8.0, "up", 0.78, 200.0)[0] == 10.0           # 8 + 3 capped at 10
    assert d._win_bonus(5.0, "up", 0.78, 30.0) == (5.0, "")          # last minute, 78c, 2c edge: tier stands
    assert d._win_bonus(0.0, "up", 0.50, 200.0) == (0.0, "")         # under the floor: never sized up from nothing


def test_down_side_edge_uses_the_down_fair_value():
    d = _xrp(fair=0.10)                                              # Down worth 90c
    assert d._win_bonus(3.0, "down", 0.78, 30.0)[0] == 6.0


def test_other_desks_get_no_bonus():
    assert set(directional.WIN_BONUS) == {"xrp", "btc"}
    for a in ("eth", "sol"):
        d = Desk(from_env({}, asset=a), None, None)
        d.fair = 0.80
        assert d._win_bonus(5.0, "up", 0.65, 200.0) == (5.0, "")


def test_btc_bonus_is_for_under_three_minutes_left_only():
    d = Desk(from_env({}, asset="btc"), None, None)
    d.fair = 0.80
    assert d._win_bonus(5.0, "up", 0.65, 120.0)[0] == 8.0          # under 3 min left: 5% + 3 points
    assert d._win_bonus(5.0, "up", 0.65, 200.0) == (5.0, "")       # over 3 min left (and 30-70c): no bonus on BTC
    assert d._win_bonus(5.0, "up", 0.72, 200.0) == (5.0, "")       # edge over 10c alone: no bonus on BTC
    assert d._win_bonus(8.0, "up", 0.75, 60.0)[0] == 10.0          # capped at 10%
    assert directional.size_pct(0.92, "btc") == 8.0                # the top tier is unchanged
