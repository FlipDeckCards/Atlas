from openjarvis.server.crew import rh_history as H


def test_horizon_stats_on_a_steady_rise():
    # 5-minute candles rising 1% each: a 10-minute window moves about 2.01% and beats a 1.9% cost every time
    c, px = [], 100.0
    for i in range(50):
        o = px
        px = round(px * 1.01, 6)
        c.append((i * 300, o, px, o, px))
    s = H.horizon_stats(c, 300, 600, 1.9)
    assert s["windows"] == 48 and s["up_share"] == 100.0 and s["beat_cost"] == 100.0
    assert abs(s["median_abs"] - 2.01) < 0.01 and abs(s["max_up_median"] - 2.01) < 0.01 and s["max_down_median"] == 0.0


def test_gaps_are_skipped_and_short_series_give_none():
    c = [(0, 1, 1, 1, 1), (300, 1, 1, 1, 1), (900, 1, 1, 1, 1)]
    assert H.horizon_stats(c, 300, 600) is None                    # the only 10-minute window has a missing candle
    c2 = [(0, 1, 1, 1, 1), (300, 1, 1, 1, 1), (600, 1, 1, 1, 1)]
    assert H.horizon_stats(c2, 300, 600)["windows"] == 1
    assert H.horizon_stats(c[:1], 300, 600) is None


def test_compute_and_csv():
    rows = []
    for i in range(400):
        rows.append(("BTC", 300, i * 300, 100.0, 101.0, 99.0, 100.5))
    st = H._compute(rows, 1.9)
    assert st["coins"]["BTC"] and st["coins"]["BTC"][0]["horizon"] == "5 min"
    assert "median_best_rise_inside_pct" in H.stats_csv(st)
