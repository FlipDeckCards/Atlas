"""Robinhood desks: backtest of the owner's trend plan (2026-11-21).

The plan: buy when a coin is trending up, hold (optionally keep adding) while it rises, sell when it falls back a set
distance from its best price since buying (a trailing stop), then buy again when the trend resumes. Rinse and repeat.

Run on the stored candles (rh_candles): daily bars for ~4 years and 1-hour bars built from 120 days of 5-minute candles.
Every buy and sell pays half the measured round trip (default 1.9% -> 0.95% each way). Long only: Robinhood's API trades
spot crypto (perpetuals aren't available through it, Robinhood support confirmed 2026-11-20), so a downtrend means cash.

Variants (every combination, per coin and timeframe):
  entry    breakout  buy when the close beats the highest high of the previous 20 bars (waits for a new high after a stop)
           trend     buy whenever close > 50-bar average and 20-bar average > 50-bar average (re-buys right away)
  stop     5 / 8 / 12 / 20 percent below the best price since buying, or "atr" = 3 x the 14-bar average range at entry
  sizing   single    all of the coin's money in one buy
           pyramid   a quarter at entry, another quarter each time price rises another half-stop, up to four (the engine's
                     3%-per-order, 12%-exposure shape)
Fills are conservative: entries at the next bar's open; a stop fills at the stop or at the open if the bar gapped through
it; the stop is checked before the bar's high can raise it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from openjarvis.server.crew.rh_history import COINS, DAILY, DDL, INTRADAY, ROUND_TRIP_COST_PCT

ENTRIES = ("breakout", "trend")
STOPS = (5.0, 8.0, 12.0, 20.0, "atr")
SIZINGS = ("single", "pyramid")
HOUR = 3600


def to_hourly(c5: List[tuple]) -> List[tuple]:
    """[(ts, o, h, l, c)] 5-minute -> 1-hour bars (an hour needs at least 10 of its 12 candles)."""
    out, cur, n = [], None, 0
    for ts, o, h, lo, cl in c5:
        hb = ts - ts % HOUR
        if cur and cur[0] == hb:
            cur = [hb, cur[1], max(cur[2], h), min(cur[3], lo), cl]
            n += 1
        else:
            if cur and n >= 10:
                out.append(tuple(cur))
            cur, n = [hb, o, h, lo, cl], 1
    if cur and n >= 10:
        out.append(tuple(cur))
    return out


def _sma(xs: List[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def run(bars: List[tuple], entry: str, stop, sizing: str, cost_pct: float = ROUND_TRIP_COST_PCT,
        bar_secs: int = DAILY) -> Optional[Dict[str, Any]]:
    """bars: [(ts, open, high, low, close)] oldest first. Returns the result on 1.0 of starting money."""
    warm = 51
    if len(bars) <= warm + 5:
        return None
    side = cost_pct / 200.0                                   # each way
    op = [b[1] for b in bars]
    hi = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    cl = [b[4] for b in bars]
    s20, s50 = _sma(cl, 20), _sma(cl, 50)
    tr = [hi[0] - lo[0]] + [max(hi[i], cl[i - 1]) - min(lo[i], cl[i - 1]) for i in range(1, len(bars))]
    atr = _sma(tr, 14)

    cash, qty = 1.0, 0.0
    units, spent, peak, stop_pct, last_add, unit_cash = 0, 0.0, 0.0, 0.0, 0.0, 0.0
    trades: List[float] = []                                   # each closed trade's P&L as % of the money it used
    eq_peak, max_dd, bars_in = 1.0, 0.0, 0
    start_px = op[warm]

    def buy(px: float, money: float) -> None:
        nonlocal cash, qty, spent
        money = min(money, cash)
        if money <= 0:
            return
        cash -= money
        qty += money * (1 - side) / px
        spent += money

    for i in range(warm, len(bars)):
        if qty > 0:
            bars_in += 1
            level = peak * (1 - stop_pct / 100.0)
            if lo[i] <= level:                                 # trailing stop hit
                px = min(op[i], level)
                got = qty * px * (1 - side)
                trades.append(100.0 * (got - spent) / spent)
                cash += got
                qty, units, spent = 0.0, 0, 0.0
            else:
                if sizing == "pyramid" and units < 4:
                    nxt = last_add * (1 + stop_pct / 200.0)
                    if hi[i] >= nxt:
                        buy(max(op[i], nxt), unit_cash)
                        units += 1
                        last_add = max(op[i], nxt)
                peak = max(peak, hi[i])
        else:
            j = i - 1
            if entry == "breakout":
                sig = j >= 21 and cl[j] > max(hi[j - 20:j])
            else:
                sig = s50[j] is not None and cl[j] > s50[j] and s20[j] > s50[j]
            if sig:
                if stop == "atr":
                    if atr[j] is None:
                        continue
                    stop_pct = max(2.0, min(40.0, 300.0 * atr[j] / cl[j]))
                else:
                    stop_pct = float(stop)
                px = op[i]
                if sizing == "single":
                    unit_cash = cash
                else:
                    unit_cash = cash / 4.0
                buy(px, unit_cash)
                units, peak, last_add = 1, max(px, hi[i]), px
                if lo[i] <= px * (1 - stop_pct / 100.0):      # stopped out inside the entry bar
                    level = px * (1 - stop_pct / 100.0)
                    got = qty * level * (1 - side)
                    trades.append(100.0 * (got - spent) / spent)
                    cash += got
                    qty, units, spent = 0.0, 0, 0.0
        eq = cash + qty * cl[i]
        eq_peak = max(eq_peak, eq)
        max_dd = max(max_dd, 100.0 * (eq_peak - eq) / eq_peak)

    final = cash + qty * cl[-1] * (1 - side)                   # mark an open position as if sold
    if qty > 0:
        trades.append(100.0 * (qty * cl[-1] * (1 - side) - spent) / spent)
    months = (bars[-1][0] - bars[warm][0]) / (30.0 * 86400) or 1.0
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    return {"entry": entry, "stop": stop if stop == "atr" else f"{int(stop)}%", "sizing": sizing,
            "return_pct": round(100.0 * (final - 1.0), 1),
            "per_month_pct": round(100.0 * (final ** (1.0 / months) - 1.0), 2) if final > 0 else -100.0,
            "buy_hold_pct": round(100.0 * (cl[-1] / start_px - 1.0), 1),
            "trades": len(trades), "trades_per_month": round(len(trades) / months, 1),
            "win_rate": round(100.0 * len(wins) / len(trades), 1) if trades else None,
            "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else None,
            "max_drawdown_pct": round(max_dd, 1), "time_in_market_pct": round(100.0 * bars_in / (len(bars) - warm), 1),
            "months": round(months, 1)}


def run_all(series: Dict[tuple, List[tuple]], cost_pct: float = ROUND_TRIP_COST_PCT) -> Dict[str, Any]:
    out: Dict[str, Any] = {"cost_pct": cost_pct, "results": []}
    for coin in COINS:
        frames = {"daily (4 yrs)": series.get((coin, DAILY), []),
                  "1-hour (120 days)": to_hourly(series.get((coin, INTRADAY), []))}
        for tf, bars in frames.items():
            for e in ENTRIES:
                for s in STOPS:
                    for z in SIZINGS:
                        r = run(bars, e, s, z, cost_pct)
                        if r:
                            out["results"].append({"coin": coin, "timeframe": tf, **r})
    return out


_CACHE: Dict[str, Any] = {}


async def backtest(pool, cost_pct: float = ROUND_TRIP_COST_PCT) -> Dict[str, Any]:
    hit = _CACHE.get("bt")
    if hit and time.monotonic() - hit[0] < 1800 and hit[1]["cost_pct"] == cost_pct:
        return hit[1]
    async with pool.acquire() as c:
        await c.execute(DDL)
        rows = await c.fetch("SELECT coin, gran, ts, open, high, low, close FROM rh_candles ORDER BY coin, gran, ts")
    series: Dict[tuple, List[tuple]] = {}
    for coin, gran, ts, o, h, lo, cl in rows:
        series.setdefault((coin, gran), []).append((int(ts), o, h, lo, cl))
    out = await asyncio.to_thread(run_all, series, cost_pct)
    _CACHE["bt"] = (time.monotonic(), out)
    return out


COST_LEVELS = (0.1, 0.25, 0.5, 1.0, 1.9)          # round trip %: a cheap exchange ... Robinhood's measured spread


async def backtest_costs(pool) -> Dict[str, Any]:
    """The same backtest at several round-trip costs: is a cheaper exchange worth moving to?"""
    hit = _CACHE.get("bt_costs")
    if hit and time.monotonic() - hit[0] < 1800:
        return hit[1]
    async with pool.acquire() as c:
        await c.execute(DDL)
        rows = await c.fetch("SELECT coin, gran, ts, open, high, low, close FROM rh_candles ORDER BY coin, gran, ts")
    series: Dict[tuple, List[tuple]] = {}
    for coin, gran, ts, o, h, lo, cl in rows:
        series.setdefault((coin, gran), []).append((int(ts), o, h, lo, cl))

    def _all() -> Dict[str, Any]:
        res = []
        for cost in COST_LEVELS:
            res += [{"cost_pct": cost, **r} for r in run_all(series, cost)["results"]]
        return {"cost_pct": "multi", "results": res}

    out = await asyncio.to_thread(_all)
    _CACHE["bt_costs"] = (time.monotonic(), out)
    return out


COLS = ["coin", "timeframe", "entry", "stop", "sizing", "return_pct", "per_month_pct", "buy_hold_pct", "trades",
        "trades_per_month", "win_rate", "avg_win_pct", "avg_loss_pct", "max_drawdown_pct", "time_in_market_pct", "months"]


def backtest_csv(bt: Dict[str, Any]) -> str:
    import csv
    import io

    cols = (["round_trip_cost_pct"] if bt["results"] and "cost_pct" in bt["results"][0] else []) + COLS
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(cols)
    for r in bt["results"]:
        wr.writerow([r.get("cost_pct" if k == "round_trip_cost_pct" else k) for k in cols])
    return buf.getvalue()
