"""Owner, 2026-11-24 (later that day): XRP 5m bets a flat 3% from 84% confidence; no XRP bet or window may pass 3% of the balance, bonus
included. BTC is unchanged (tiers 3 / 5 / 8, 10% window). (The file name is from the earlier 3% / 5% step.)"""
import pytest

from openjarvis.polybot import directional
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


@pytest.mark.parametrize("prob,expected", [(0.83, 0.0), (0.84, 0.5), (0.85, 0.5), (0.879, 0.5), (0.88, 0.5), (0.94, 0.5), (0.99, 0.5)])
def test_xrp_is_a_flat_three_percent_from_84(prob, expected):
    assert directional.size_pct(prob, "xrp") == expected
    assert directional.min_conf("xrp") == 0.84


def test_xrp_window_and_bonus_are_capped_at_three_percent():
    assert directional.window_total_pct("xrp") == 0.5
    assert from_env({}, asset="xrp").window_pct == 0.5
    assert "xrp" not in directional.WIN_BONUS


def test_the_nov_24_bet_would_have_been_three_percent_not_nine():
    d = Desk(from_env({}, asset="xrp"), None, None)
    d.fair = 0.19
    lot, _ = d._win_bonus(directional.size_pct(0.85, "xrp"), "down", 0.68, 236.0)
    assert lot == 0.5
    assert directional.window_room({}, 390.0, "xrp") == 1.95        # at most $1.95 in one window on a $390 account (0.5%, owner 2026-11-27)


def test_btc_is_unchanged():
    assert directional.size_pct(0.85, "btc") == 5.0 and directional.size_pct(0.92, "btc") == 8.0
    assert directional.window_total_pct("btc") == 10.0
    assert directional.WIN_BONUS["btc"] == (3.0, 10.0)
