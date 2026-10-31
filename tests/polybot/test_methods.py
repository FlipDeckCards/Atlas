"""v1.22: edge and late are single-bet methods; one bet per method per window, never against a side already bet, 20% per window."""
from openjarvis.polybot import directional


def test_edge_needs_six_points_over_the_ask_and_the_80_floor_on_btc():
    # the model says 85% Up, the Up ask is 78c: 7 points over the price, clears the floor
    assert directional.method_pick("edge", 0.85, 0.78, 0.25, asset="btc")[0] == "up"
    # only 5 points over: no edge bet
    assert directional.method_pick("edge", 0.85, 0.80, 0.25, asset="btc") is None
    # model under the 80% floor: nothing, however cheap
    assert directional.method_pick("edge", 0.70, 0.50, 0.55, asset="btc") is None


def test_late_needs_90_percent_and_an_ask_at_95c_or_less():
    assert directional.method_pick("late", 0.93, 0.90, 0.10, asset="btc")[0] == "up"
    assert directional.method_pick("late", 0.89, 0.85, 0.15, asset="btc") is None     # under 90%
    assert directional.method_pick("late", 0.95, 0.96, 0.05, asset="btc") is None     # ask over 95c


def test_lot_follows_the_coin_tier():
    side, cost, method, gap, pct = directional.method_pick("late", 0.93, 0.90, 0.10, asset="btc")
    assert pct == directional.size_pct(0.93, "btc") == 8.0


def test_cushion_and_momentum_still_apply():
    assert directional.method_pick("edge", 0.85, 0.78, 0.25, z=0.1, asset="btc") is None      # index too close to the beat
    assert directional.method_pick("edge", 0.85, 0.78, 0.25, up_ok=False, down_ok=True, asset="btc") is None


def test_no_bet_against_a_side_already_bet():
    bets = {"directional": ("up", 5.0, 10.0)}
    assert directional.may_take(bets, "up", 2.0, 100.0)[0] is True
    ok, why = directional.may_take(bets, "down", 2.0, 100.0)
    assert ok is False and "other side" in why


def test_window_total_is_capped_at_20_percent():
    bets = {"directional": ("up", 10.0, 20.0), "edge": ("up", 8.0, 16.0)}
    assert directional.window_room(bets, 100.0) == 2.0            # 20 of 100 total, 18 used
    assert directional.may_take(bets, "up", 5.0, 100.0)[0] is False
    assert directional.may_take(bets, "up", 2.0, 100.0)[0] is True
    assert directional.window_room({"late": ("down", 25.0, 30.0)}, 100.0) == 0.0


def test_empty_window_allows_a_first_bet():
    assert directional.may_take({}, "down", 4.0, 100.0) == (True, "")
