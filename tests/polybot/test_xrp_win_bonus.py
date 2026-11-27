"""Owner, 2026-11-27: XRP, ETH, SOL, WTI and natgas bet a flat 0.5% with no win bonus. Only BTC 5m keeps a bonus
(+3 points, under 3 minutes left, capped at 10%)."""
from openjarvis.polybot import directional
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def test_no_bonus_on_the_small_desks():
    assert set(directional.WIN_BONUS) == {"btc"}
    for a in ("xrp", "eth", "sol", "wti", "natgas"):
        d = Desk(from_env({}, asset=a), None, None)
        d.fair = 0.90
        for secs, cost in ((200.0, 0.65), (30.0, 0.78), (100.0, 0.50)):
            assert d._win_bonus(0.5, "up", cost, secs) == (0.5, "")


def test_btc_bonus_is_for_under_three_minutes_left_only():
    d = Desk(from_env({}, asset="btc"), None, None)
    d.fair = 0.80
    assert d._win_bonus(5.0, "up", 0.65, 120.0)[0] == 8.0          # under 3 min left: 5% + 3 points
    assert d._win_bonus(5.0, "up", 0.65, 200.0) == (5.0, "")       # over 3 min left (and 30-70c): no bonus on BTC
    assert d._win_bonus(5.0, "up", 0.72, 200.0) == (5.0, "")       # edge over 10c alone: no bonus on BTC
    assert d._win_bonus(8.0, "up", 0.75, 60.0)[0] == 10.0          # capped at 10%
    assert directional.size_pct(0.92, "btc") == 8.0                # the top tier is unchanged
