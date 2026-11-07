"""v1.20 direction plan: per-coin confidence tiers, the no-trade floors, and the cross-desk cap."""

import pytest

from openjarvis.polybot import directional, exposure


@pytest.mark.parametrize("prob,expected", [(0.79, 0.0), (0.80, 5.0), (0.849, 5.0), (0.85, 7.0), (0.899, 7.0),
                                           (0.90, 8.0), (0.949, 8.0), (0.95, 10.0), (0.999, 10.0)])
def test_btc_tiers(prob, expected):
    assert directional.size_pct(prob, "btc") == expected


@pytest.mark.parametrize("coin", ["xrp", "sol", "eth"])
@pytest.mark.parametrize("prob,expected", [(0.80, 0.0), (0.849, 0.0), (0.85, 1.0), (0.879, 1.0), (0.88, 2.0), (0.919, 2.0),
                                           (0.92, 3.0), (0.949, 3.0), (0.95, 5.0), (0.999, 5.0)])
def test_xrp_sol_eth_trade_from_85_at_one_to_five_percent(coin, prob, expected):
    assert directional.size_pct(prob, coin) == expected


def test_unknown_coin_never_trades():
    assert directional.size_pct(0.99, "doge") == 0.0


def test_minimum_confidence_per_coin():
    assert directional.min_conf("btc") == 0.80
    assert directional.min_conf("xrp") == directional.min_conf("eth") == directional.min_conf("sol") == 0.85


def test_btc_trades_at_its_tier_and_eth_does_not_at_the_same_confidence():
    btc = directional.decide(0.96, 0.60, 0.40, (0, 0), (0, 0), asset="btc")
    assert btc is not None and btc[0] == "up" and btc[4] == 10.0
    assert directional.decide(0.84, 0.60, 0.40, (0, 0), (0, 0), asset="eth") is None


def test_exposure_cap_is_20_percent_across_desks():
    exposure.reset()
    assert exposure.headroom(1000.0) == 200.0
    exposure.publish("BTC", 100.0)
    exposure.publish("ETH", 50.0)
    assert exposure.headroom(1000.0) == 50.0
    exposure.reserve("SOL", 60.0)                  # over the cap now: no room left
    assert exposure.headroom(1000.0) == 0.0


def test_publish_overwrites_and_reserve_adds():
    exposure.reset()
    exposure.reserve("BTC", 30.0)
    exposure.reserve("BTC", 20.0)
    assert exposure.total_open() == 50.0
    exposure.publish("BTC", 10.0)                  # the next tick reports the real figure
    assert exposure.total_open() == 10.0
    exposure.reset()


def test_open_sides_follow_the_coin_floor():
    # v1.21: a fresh window opens a leg only where the model clears the coin's floor
    assert directional.open_sides(0.84, "eth") == (False, False)   # v1.32: ETH, SOL and XRP open from 85%
    assert directional.open_sides(0.96, "eth") == (True, False)
    assert directional.open_sides(0.04, "eth") == (False, True)
    assert directional.open_sides(0.82, "btc") == (True, False)
    assert directional.open_sides(0.78, "btc") == (False, False)
    assert directional.open_sides(None, "btc") == (False, False)
    assert directional.open_sides(0.84, "xrp") == (False, False)
    assert directional.open_sides(0.85, "xrp") == (True, False)


def test_small_coins_keep_every_method_in_a_window_under_five_percent():
    # v1.34: directional, edge and late bets on XRP, SOL and ETH share 5% per window; BTC keeps 20%
    assert directional.window_total_pct("btc") == directional.window_total_pct("btc15") == 20.0
    for a in ("xrp", "sol", "eth", "sol15", "eth1h"):
        assert directional.window_total_pct(a) == 5.0, a
    bets = {"directional": ("up", 30.0, 50.0)}
    assert directional.window_room(bets, 1000.0, "xrp") == 20.0 and directional.window_room(bets, 1000.0, "btc") == 170.0
    assert directional.may_take(bets, "up", 25.0, 1000.0, "sol")[0] is False
    assert directional.may_take(bets, "up", 20.0, 1000.0, "sol")[0] is True
