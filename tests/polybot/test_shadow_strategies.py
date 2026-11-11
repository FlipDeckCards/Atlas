"""Six shadow strategies, three per timeframe: recorded only, never traded."""

from openjarvis.polybot import shadow


def test_six_shadows_split_by_timeframe():
    assert shadow.NAMES_5M == ("s5_shrink", "s5_floor85", "s5_floor75", "m5_pair", "s5_gate")
    assert shadow.NAMES_15M == ("s15_trend", "s15_distance", "s15_vol", "m15_maker", "s15_gate")
    assert set(shadow.Shadow(window_secs=300).rec) == set(shadow.NAMES)
    assert shadow.Shadow(window_secs=300).names == shadow.NAMES_5M
    assert shadow.Shadow(window_secs=900).names == shadow.NAMES_15M


def test_5m_shrink_trades_the_shrunk_model_not_the_raw_one():
    # fair 0.94 shrinks to 0.83; an ask of 0.75 is 8c under it: buy up. An ask of 0.80 is not 5c under: nothing.
    assert shadow.decide("s5_shrink", 120, 0.94, 0.75, 0.20, None) == [("up", 0.75)]
    assert shadow.decide("s5_shrink", 120, 0.94, 0.80, 0.20, None) == []
    assert shadow.decide("s5_shrink", 300, 0.94, 0.80, 0.20, None) == []                   # outside 30-240 s


def test_5m_floor_shadows_buy_only_at_their_floor_and_under_fair():
    assert shadow.decide("s5_floor85", 120, 0.95, 0.86, 0.10, None) == [("up", 0.86)]
    assert shadow.decide("s5_floor85", 120, 0.95, 0.80, 0.10, None) == []                  # under the 85c floor
    assert shadow.decide("s5_floor75", 120, 0.95, 0.80, 0.10, None) == [("up", 0.80)]
    assert shadow.decide("s5_floor75", 120, 0.90, 0.92, 0.10, None) == []                  # not 3c under fair


def test_15m_trend_follows_momentum_and_needs_an_edge():
    assert shadow.decide("s15_trend", 400, 0.60, 0.50, 0.50, {"momentum": 0.8}) == [("up", 0.50)]
    assert shadow.decide("s15_trend", 400, 0.60, 0.50, 0.50, {"momentum": 0.1}) == []
    assert shadow.decide("s15_trend", 400, 0.40, 0.60, 0.40, {"momentum": -0.8}) == [("down", 0.40)]


def test_15m_distance_needs_80_percent_and_a_7c_edge():
    assert shadow.decide("s15_distance", 200, 0.85, 0.75, 0.20, None) == [("up", 0.75)]
    assert shadow.decide("s15_distance", 200, 0.85, 0.80, 0.20, None) == []               # only 5c edge
    assert shadow.decide("s15_distance", 200, 0.75, 0.70, 0.30, None) == []               # under 80%


def test_15m_vol_needs_both_volatility_views_to_agree():
    assert shadow.decide("s15_vol", 300, 0.75, 0.60, 0.40, {"fair_lo": 0.80, "fair_hi": 0.72}) == [("up", 0.60)]
    assert shadow.decide("s15_vol", 300, 0.75, 0.60, 0.40, {"fair_lo": 0.80, "fair_hi": 0.60}) == []
    assert shadow.decide("s15_vol", 300, 0.75, 0.60, 0.40, None) == []


def test_each_shadow_trades_once_per_window_and_settles():
    s = shadow.Shadow(window_secs=300)
    s.tick("w1", 30, 0.90, 0.85, 0.15, {"book_up": 0.70})
    s.tick("w1", 20, 0.90, 0.85, 0.15, {"book_up": 0.70})                 # same window: not again
    assert s.open["w1"]["s5_floor85"] == [("up", 0.85)]
    s.settle("w1", up=True)
    assert s.rec["s5_floor85"]["windows"] == 1 and s.rec["s5_floor85"]["wins"] == 1 and s.rec["s5_floor85"]["profit"] > 0
    assert "w1" not in s.open


def test_a_5m_shadow_never_fires_on_a_15m_desk():
    s = shadow.Shadow(window_secs=900)
    s.tick("w2", 30, 0.90, 0.85, 0.15, {"book_up": 0.70})
    assert "w2" not in s.open


def test_load_clears_the_old_shadows_and_keeps_calibration():
    s = shadow.Shadow()
    s.calib_add(0.85, None, True)
    old = {"rec": {"edge": {"windows": 9, "wins": 5, "staked": 9, "profit": 1}}, "calib": s.to_dict()["calib"]}
    t = shadow.Shadow()
    t.load(old)
    assert "edge" not in t.rec and t.rec["s5_floor85"]["windows"] == 0
    assert t.bucket_stats(0.85) == (1, 1)


def test_5m_maker_fills_when_the_market_sells_down_to_its_bid():
    s = shadow.Shadow(window_secs=300)
    s.maker_tick("m1", 200, 0.60, 0.62, 0.40)                       # posts bids: up 0.57, down 0.37
    assert s.maker_quotes["m1"] == {"up": 0.57, "down": 0.37}
    s.maker_tick("m1", 150, 0.60, 0.56, 0.40)                       # up ask 56c <= 57c: fills up; down ask 40c > 37c
    assert s.maker_fills["m1"] == {"up": 0.57}
    s.maker_tick("m1", 40, 0.60, 0.30, 0.30)                        # last minute: no new fills
    assert "down" not in s.maker_fills["m1"]
    s.settle("m1", up=True)
    assert s.rec["m5_pair"]["windows"] == 1 and s.rec["m5_pair"]["wins"] == 1 and round(s.rec["m5_pair"]["profit"], 2) == 0.43


def test_15m_maker_posts_one_bid_on_the_favoured_side():
    s = shadow.Shadow(window_secs=900)
    s.maker_tick("m2", 500, 0.70, 0.72, 0.30)                       # up favoured at 70c: bid 64c
    assert s.maker_quotes["m2"] == {"up": 0.64}
    s.maker_tick("m2", 400, 0.70, 0.60, 0.40)                       # ask 60c <= 64c: fills
    s.settle("m2", up=False)                                        # the up side loses
    assert s.rec["m15_maker"]["windows"] == 1 and s.rec["m15_maker"]["wins"] == 0 and round(s.rec["m15_maker"]["profit"], 2) == -0.64


def test_gate_buys_only_under_the_measured_win_rate():
    s = shadow.Shadow(window_secs=300)
    for _ in range(40):                                  # 40 settled 80-89 trades, 34 won: 85% measured
        s.calib_add(0.85, None, True) if _ < 34 else s.calib_add(0.85, None, False)
    s.gate_tick("g1", 120, 0.85, 0.85, 0.15)             # ask 85c, measured 85% - 5c = 80%: no
    assert "g1" not in s.open
    s.gate_tick("g2", 120, 0.85, 0.78, 0.15)             # ask 78c <= 80%: buy up
    assert s.open["g2"]["s5_gate"] == [("up", 0.78)]


def test_gate_needs_a_big_enough_bucket():
    s = shadow.Shadow(window_secs=900)
    for _ in range(10):
        s.calib_add(0.85, None, True)
    s.gate_tick("g3", 300, 0.85, 0.40, 0.60)             # only 10 trades in the bucket: no
    assert "g3" not in s.open
