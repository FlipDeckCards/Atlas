"""Owner, 2026-11-24: XRP 5m sizes 84-87% = 3%, 88%+ = 5%; no XRP bet or window may pass 5% of the balance, bonus included.
BTC is unchanged (tiers 3 / 5 / 8, 10% window)."""
import pytest

from openjarvis.polybot import directional
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


@pytest.mark.parametrize("prob,expected", [(0.83, 0.0), (0.84, 3.0), (0.85, 3.0), (0.879, 3.0), (0.88, 5.0), (0.94, 5.0), (0.99, 5.0)])
def test_xrp_tiers_are_three_and_five(prob, expected):
    assert directional.size_pct(prob, "xrp") == expected


def test_xrp_window_and_bonus_are_capped_at_five_percent():
    assert directional.window_total_pct("xrp") == 5.0
    assert from_env({}, asset="xrp").window_pct <= 5.0
    assert directional.WIN_BONUS["xrp"] == (3.0, 5.0)


def test_the_nov_24_bet_would_have_been_five_percent_not_nine():
    # 85% confidence (3% tier now), over 3 min left, bought at 68c, 26c edge: every bonus kind applies, the cap holds it at 5%
    d = Desk(from_env({}, asset="xrp"), None, None)
    d.fair = 0.19                                                    # Up fair value: Down worth 81c
    lot, _ = d._win_bonus(directional.size_pct(0.85, "xrp"), "down", 0.68, 236.0)
    assert lot == 5.0
    assert directional.window_room({}, 390.0, "xrp") == 19.5         # at most $19.50 in one window on a $390 account


def test_btc_is_unchanged():
    assert directional.size_pct(0.85, "btc") == 5.0 and directional.size_pct(0.92, "btc") == 8.0
    assert directional.window_total_pct("btc") == 10.0
    assert directional.WIN_BONUS["btc"] == (3.0, 10.0)
