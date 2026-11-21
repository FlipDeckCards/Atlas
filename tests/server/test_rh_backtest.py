from openjarvis.server.crew import rh_backtest as bt


def _bars(closes, step=86400):
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append((i * step, o, max(o, c) * 1.001, min(o, c) * 0.999, c))
    return out


def test_uptrend_makes_money_after_costs():
    closes = [100 * (1.01 ** i) for i in range(300)]
    for e in bt.ENTRIES:
        for z in bt.SIZINGS:
            r = bt.run(_bars(closes), e, 8.0, z)
            assert r["return_pct"] > 0 and r["trades"] >= 1


def test_crash_is_cut_by_stop():
    closes = [100 * (1.01 ** i) for i in range(120)] + [100 * 1.01 ** 119 * (0.97 ** k) for k in range(1, 60)]
    r = bt.run(_bars(closes), "breakout", 8.0, "single")
    assert r["buy_hold_pct"] > -100 and r["max_drawdown_pct"] < 20
    assert r["trades"] >= 1 and r["time_in_market_pct"] < 100


def test_costs_hurt_choppy_market():
    closes = [100 + (5 if i % 2 else -5) * (i % 7) / 7 for i in range(400)]
    free = bt.run(_bars(closes), "trend", 5.0, "single", cost_pct=0.0)
    paid = bt.run(_bars(closes), "trend", 5.0, "single", cost_pct=1.9)
    if free["trades"]:
        assert paid["return_pct"] <= free["return_pct"]


def test_hourly_aggregation_and_run_all():
    c5 = [(i * 300, 100.0, 101.0, 99.0, 100.0 + i * 0.01) for i in range(12 * 24 * 10)]
    h = bt.to_hourly(c5)
    assert len(h) == 240 and h[0][0] == 0 and h[0][4] == c5[11][4]
    out = bt.run_all({("BTC", 300): c5, ("BTC", 86400): _bars([100 * 1.005 ** i for i in range(200)])})
    assert out["results"] and {r["timeframe"] for r in out["results"]} == {"daily (4 yrs)", "1-hour (120 days)"}
    assert len(bt.backtest_csv(out).splitlines()) == len(out["results"]) + 1
