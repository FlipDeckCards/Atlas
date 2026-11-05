"""Six shadow strategies, three per timeframe: recorded only, never traded."""

from openjarvis.polybot import shadow


def test_six_shadows_split_by_timeframe():
    assert shadow.NAMES_5M == ("s5_late", "s5_cheap", "s5_book")
    assert shadow.NAMES_15M == ("s15_trend", "s15_distance", "s15_vol")
    assert set(shadow.Shadow(window_secs=300).rec) == set(shadow.NAMES)
    assert shadow.Shadow(window_secs=300).names == shadow.NAMES_5M
    assert shadow.Shadow(window_secs=900).names == shadow.NAMES_15M


def test_5m_late_needs_model_and_book_and_a_cheap_enough_ask():
    assert shadow.decide("s5_late", 30, 0.90, 0.85, 0.15, {"book_up": 0.70}) == [("up", 0.85)]
    assert shadow.decide("s5_late", 30, 0.90, 0.95, 0.15, {"book_up": 0.70}) == []        # ask over 90c
    assert shadow.decide("s5_late", 30, 0.90, 0.85, 0.15, {"book_up": 0.50}) == []        # book not agreeing
    assert shadow.decide("s5_late", 120, 0.90, 0.85, 0.15, {"book_up": 0.70}) == []       # outside 10-60 s


def test_5m_cheap_buys_a_cheap_side_the_model_likes_more():
    assert shadow.decide("s5_cheap", 150, 0.40, 0.15, 0.60, None) == [("up", 0.15)]       # fair 40c vs ask 15c: not enough
    assert shadow.decide("s5_cheap", 150, 0.30, 0.15, 0.85, None) == [("up", 0.15)]       # fair 30c vs ask 15c: 15c edge
    assert shadow.decide("s5_cheap", 150, 0.70, 0.75, 0.10, None) == [("down", 0.10)]     # down fair 30c vs ask 10c
    assert shadow.decide("s5_cheap", 150, 0.70, 0.75, 0.30, None) == []                   # no cheap side


def test_5m_book_needs_book_and_model_to_agree():
    assert shadow.decide("s5_book", 120, 0.70, 0.55, 0.45, {"book_up": 0.70}) == [("up", 0.55)]
    assert shadow.decide("s5_book", 120, 0.70, 0.55, 0.45, {"book_up": 0.50}) == []
    assert shadow.decide("s5_book", 120, 0.50, 0.55, 0.45, {"book_up": 0.70}) == []       # model not 60%+


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
    assert s.open["w1"]["s5_late"] == [("up", 0.85)]
    s.settle("w1", up=True)
    assert s.rec["s5_late"]["windows"] == 1 and s.rec["s5_late"]["wins"] == 1 and s.rec["s5_late"]["profit"] > 0
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
    assert "edge" not in t.rec and t.rec["s5_late"]["windows"] == 0
    assert t.bucket_stats(0.85) == (1, 1)
