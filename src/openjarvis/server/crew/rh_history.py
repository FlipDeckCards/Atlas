"""Robinhood desks' price history and move statistics (owner, 2026-11-20).

How far does each coin typically move over 5 minutes, 10 minutes ... 1 week, against Robinhood's ~1.9% round-trip cost? The server
keeps public candles from Coinbase's exchange (the same coins against USD; Robinhood's mid price tracks them closely):
5-minute candles for the last 120 days and daily candles for the last 4 years, in rh_candles. They also feed backtests.

Statistics per coin and horizon (overlapping windows, every 5 minutes or every day):
  avg_abs / median_abs / p90_abs   how big the move from start to end usually is (percent, either direction)
  up_share                         how often it ended higher
  beat_cost                        how often the move, either way, was bigger than the round-trip cost (default 1.9%)
  max_up_median / max_down_median  the biggest rise / fall seen at any point inside the window (from candle highs and lows),
                                   the typical one: what a trade that sells at the right moment could have caught
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("crew.rh_history")

COINS = ("BTC", "ETH", "SOL", "XRP")
BASE = "https://api.exchange.coinbase.com"
INTRADAY = 300                      # 5-minute candles
DAILY = 86400
INTRADAY_DAYS = 120
DAILY_DAYS = 1460
ROUND_TRIP_COST_PCT = 1.9           # measured on Robinhood, 2026-11-20 (about 0.95% each way)

# (label, seconds); under a day from 5-minute candles, a day and over from daily candles
HORIZONS = [("5 min", 300), ("10 min", 600), ("15 min", 900), ("30 min", 1800), ("1 hour", 3600), ("1.5 hours", 5400),
            ("2 hours", 7200), ("3 hours", 10800), ("5 hours", 18000), ("8 hours", 28800), ("12 hours", 43200),
            ("1 day", 86400), ("2 days", 172800), ("3 days", 259200), ("1 week", 604800), ("2 weeks", 1209600),
            ("1 month", 2592000)]

DDL = """
CREATE TABLE IF NOT EXISTS rh_candles (coin TEXT NOT NULL, gran INTEGER NOT NULL, ts BIGINT NOT NULL, open DOUBLE PRECISION,
  high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION, volume DOUBLE PRECISION, PRIMARY KEY (coin, gran, ts));
"""


async def _fetch(http, coin: str, gran: int, start: int, end: int) -> List[list]:
    iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{BASE}/products/{coin}-USD/candles?granularity={gran}&start={iso(start)}&end={iso(end)}"
    for attempt in range(4):
        r = await http.get(url, headers={"User-Agent": "aibussol-desk"})
        if r.status_code == 429:
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Coinbase candles HTTP {r.status_code} for {coin}")
        d = r.json()
        return d if isinstance(d, list) else []
    return []


async def backfill(pool, http=None, now: Optional[float] = None) -> Dict[str, int]:
    """Fill in rh_candles up to now: only the gaps since the newest stored candle (the first run takes a couple of minutes)."""
    import httpx

    now = int(now or time.time())
    own = http is None
    http = http or httpx.AsyncClient(timeout=30)
    added: Dict[str, int] = {}
    try:
        async with pool.acquire() as c:
            await c.execute(DDL)
        for coin in COINS:
            for gran, days in ((INTRADAY, INTRADAY_DAYS), (DAILY, DAILY_DAYS)):
                async with pool.acquire() as c:
                    last = await c.fetchval("SELECT max(ts) FROM rh_candles WHERE coin = $1 AND gran = $2", coin, gran)
                start = max(int(last or 0) + gran, now - days * 86400)
                step = gran * 300                         # Coinbase returns at most 300 candles a request
                n = 0
                t = start
                while t < now:
                    rows = await _fetch(http, coin, gran, t, min(now, t + step))
                    if rows:
                        async with pool.acquire() as c:
                            await c.executemany("INSERT INTO rh_candles (coin, gran, ts, low, high, open, close, volume) "
                                                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING",
                                                [(coin, gran, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                                                 for r in rows])
                        n += len(rows)
                    t += step
                    await asyncio.sleep(0.25)             # well under Coinbase's public limit
                added[f"{coin} {gran}"] = n
            async with pool.acquire() as c:
                await c.execute("DELETE FROM rh_candles WHERE coin = $1 AND gran = $2 AND ts < $3", coin, INTRADAY,
                                now - INTRADAY_DAYS * 86400)
    finally:
        if own:
            await http.aclose()
    return added


def _pct(a: float, b: float) -> float:
    return 100.0 * (b - a) / a


def _q(xs: Sequence[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def horizon_stats(candles: List[tuple], gran: int, seconds: int, cost_pct: float = ROUND_TRIP_COST_PCT) -> Optional[Dict[str, Any]]:
    """candles: [(ts, open, high, low, close)] oldest first, evenly spaced by gran (gaps skipped)."""
    k = seconds // gran
    if k < 1 or len(candles) <= k:
        return None
    from collections import deque

    n = len(candles)
    hi_max, lo_min = [0.0] * n, [0.0] * n            # max high / min low over candles[i:i+k], by sliding window
    dq_hi: deque = deque()
    dq_lo: deque = deque()
    for j in range(n - 1, -1, -1):                   # walk backwards so the window is candles[j:j+k]
        while dq_hi and candles[dq_hi[-1]][2] <= candles[j][2]:
            dq_hi.pop()
        dq_hi.append(j)
        while dq_lo and candles[dq_lo[-1]][3] >= candles[j][3]:
            dq_lo.pop()
        dq_lo.append(j)
        while dq_hi[0] >= j + k:
            dq_hi.popleft()
        while dq_lo[0] >= j + k:
            dq_lo.popleft()
        hi_max[j], lo_min[j] = candles[dq_hi[0]][2], candles[dq_lo[0]][3]
    moves, ups, downs = [], [], []
    for i in range(n - k):
        t0, o0 = candles[i][0], candles[i][1]
        if candles[i + k][0] - t0 != seconds or not o0:   # a gap in the data: skip this window
            continue
        moves.append(_pct(o0, candles[i + k - 1][4]))
        ups.append(_pct(o0, hi_max[i]))
        downs.append(_pct(o0, lo_min[i]))
    if not moves:
        return None
    ab = [abs(m) for m in moves]
    return {"windows": len(moves), "avg_abs": round(sum(ab) / len(ab), 3), "median_abs": round(_q(ab, 0.5), 3),
            "p90_abs": round(_q(ab, 0.9), 3), "up_share": round(100.0 * sum(1 for m in moves if m > 0) / len(moves), 1),
            "beat_cost": round(100.0 * sum(1 for a in ab if a > cost_pct) / len(ab), 1),
            "max_up_median": round(_q(ups, 0.5), 3), "max_down_median": round(_q(downs, 0.5), 3)}


_CACHE: Dict[str, Any] = {}


async def stats(pool, cost_pct: float = ROUND_TRIP_COST_PCT) -> Dict[str, Any]:
    """Cached for 30 minutes (the candles change every 6 hours); the arithmetic runs off the event loop."""
    hit = _CACHE.get("stats")
    if hit and time.monotonic() - hit[0] < 1800 and hit[1]["cost_pct"] == cost_pct:
        return hit[1]
    async with pool.acquire() as c:
        await c.execute(DDL)
        rows = await c.fetch("SELECT coin, gran, ts, open, high, low, close FROM rh_candles ORDER BY coin, gran, ts")
    out = await asyncio.to_thread(_compute, [tuple(r) for r in rows], cost_pct)
    _CACHE["stats"] = (time.monotonic(), out)
    return out


def _compute(rows: List[tuple], cost_pct: float) -> Dict[str, Any]:
    series: Dict[tuple, List[tuple]] = {}
    for coin, gran, ts, o, h, lo, cl in rows:
        series.setdefault((coin, gran), []).append((int(ts), o, h, lo, cl))
    out: Dict[str, Any] = {"cost_pct": cost_pct, "coins": {}, "span": {}}
    for coin in COINS:
        intr, day = series.get((coin, INTRADAY), []), series.get((coin, DAILY), [])
        out["span"][coin] = {"intraday_days": round((intr[-1][0] - intr[0][0]) / 86400, 1) if intr else 0,
                             "daily_days": round((day[-1][0] - day[0][0]) / 86400) if day else 0}
        per = []
        for label, secs in HORIZONS:
            s = horizon_stats(intr, INTRADAY, secs, cost_pct) if secs < DAILY else horizon_stats(day, DAILY, secs, cost_pct)
            if s:
                per.append({"horizon": label, **s})
        out["coins"][coin] = per
    return out


def stats_csv(st: Dict[str, Any]) -> str:
    import csv
    import io

    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["coin", "horizon", "windows", "avg_move_pct", "median_move_pct", "p90_move_pct", "ended_up_pct",
                 f"move_bigger_than_{st['cost_pct']}pct_cost_pct", "median_best_rise_inside_pct", "median_worst_fall_inside_pct"])
    for coin, per in st["coins"].items():
        for h in per:
            wr.writerow([coin, h["horizon"], h["windows"], h["avg_abs"], h["median_abs"], h["p90_abs"], h["up_share"], h["beat_cost"],
                         h["max_up_median"], h["max_down_median"]])
    return buf.getvalue()


async def loop(pool) -> None:
    """Keep the history current: once at startup, then every 6 hours."""
    while True:
        try:
            added = await backfill(pool)
            logger.warning("Robinhood price history updated: %s", added)
        except Exception:
            logger.exception("Robinhood price history update failed; will try again")
        await asyncio.sleep(6 * 3600)
