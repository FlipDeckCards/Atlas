"""v1.20 direction plan: per-coin confidence tiers, the no-trade floors, and the cross-desk cap."""

import pytest

from openjarvis.polybot import directional, exposure


@pytest.mark.parametrize("prob,expected", [(0.79, 0.0), (0.80, 5.0), (0.849, 5.0), (0.85, 7.0), (0.899, 7.0),
                                           (0.90, 8.0), (0.949, 8.0), (0.95, 10.0), (0.999, 10.0)])
def test_btc_tiers(prob, expected):
    assert directional.size_pct(prob, "btc") == expected


@pytest.mark.parametrize("prob,expected", [(0.80, 0.0), (0.85, 0.0), (0.88, 0.0), (0.889, 0.0), (0.89, 7.0), (0.899, 7.0),
                                           (0.90, 8.0), (0.949, 8.0), (0.95, 10.0), (0.999, 10.0)])
def test_xrp_tiers_floor_at_89(prob, expected):
    assert directional.size_pct(prob, "xrp") == expected


@pytest.mark.parametrize("prob,expected", [(0.94, 0.0), (0.95, 1.0), (0.99, 1.0), (0.80, 0.0)])
def test_eth_and_sol_only_trade_at_95_and_size_flat(prob, expected):
    assert directional.size_pct(prob, "eth") == expected
    assert directional.size_pct(prob, "sol") == expected


def test_unknown_coin_never_trades():
    assert directional.size_pct(0.99, "doge") == 0.0


def test_minimum_confidence_per_coin():
    assert directional.min_conf("btc") == 0.80
    assert directional.min_conf("xrp") == 0.89
    assert directional.min_conf("eth") == 0.95
    assert directional.min_conf("sol") == 0.95


def test_btc_trades_at_its_tier_and_eth_does_not_at_the_same_confidence():
    btc = directional.decide(0.96, 0.60, 0.40, (0, 0), (0, 0), asset="btc")
    assert btc is not None and btc[0] == "up" and btc[4] == 10.0
    assert directional.decide(0.90, 0.60, 0.40, (0, 0), (0, 0), asset="eth") is None


def test_exposure_cap_is_25_percent_across_desks():
    exposure.reset()
    assert exposure.headroom(1000.0) == 250.0
    exposure.publish("BTC", 100.0)
    exposure.publish("ETH", 100.0)
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
    assert directional.open_sides(0.93, "eth") == (False, False)   # the 93% ETH case: no open
    assert directional.open_sides(0.96, "eth") == (True, False)
    assert directional.open_sides(0.04, "eth") == (False, True)
    assert directional.open_sides(0.82, "btc") == (True, False)
    assert directional.open_sides(0.78, "btc") == (False, False)
    assert directional.open_sides(None, "btc") == (False, False)
    assert directional.open_sides(0.88, "xrp") == (False, False)   # the 88% XRP loss would not open now
    assert directional.open_sides(0.89, "xrp") == (True, False)
