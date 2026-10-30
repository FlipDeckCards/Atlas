"""Fair value of "Up" for a BTC Up/Down window, from the settlement index itself.

Settlement (Polymarket US crypto schema): the open (price to beat) and the close are each the simple average of the 60 one-second
BRTI prints in [T - 60 s, T). Up pays $1 when close >= open.

Model: the index is a random walk with per-second volatility sigma (in dollars). Let E be the window end and F = E - 60 the first
second of the closing average.
  * before F, the close is the average of a walk that starts at today's price S: mean S, variance sigma^2 * ((F - t) + 60 / 3)
    (the time still to wait before the averaging starts, plus the variance of a 60-second average of a walk, which is T/3);
  * inside the averaging minute, k seconds are already known (their average A) and m = E - t are still to come: mean
    (k*A + m*S) / 60, variance sigma^2 * m^3 / (3 * 3600).
P(Up) = Phi((mean - K) / sd).

Volatility comes from one-minute returns of the last hour, NOT one-second returns: the index is smoothed second to second, so
one-second returns understate real movement many times over (measured: per-minute vol was ~6x what 1 s returns imply).
`vol_mult` widens it further for fat tails.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, Optional, Tuple

AVG_S = 60


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class PriceTape:
    """One-second index prints, keyed by unix second. Keeps about two hours."""

    KEEP_S = 7200

    def __init__(self) -> None:
        self.px: Dict[int, float] = {}
        self.last_ts: int = 0

    def add(self, ts: int, price: float) -> None:
        if price and price > 0:
            self.px[int(ts)] = float(price)
            if ts > self.last_ts:
                self.last_ts = int(ts)
        if len(self.px) > self.KEEP_S + 600:
            cut = self.last_ts - self.KEEP_S
            for k in [k for k in self.px if k < cut]:
                del self.px[k]

    def extend(self, rows: Iterable[Tuple[int, float]]) -> None:
        for ts, p in rows:
            self.add(ts, p)

    def at(self, ts: int, back: int = 5) -> Optional[float]:
        """The print at `ts`, or the latest one within `back` seconds before it."""
        for k in range(back + 1):
            v = self.px.get(int(ts) - k)
            if v is not None:
                return v
        return None

    def last(self) -> Optional[Tuple[int, float]]:
        return (self.last_ts, self.px[self.last_ts]) if self.last_ts in self.px else None

    def average(self, start: int, end_incl: int) -> Optional[float]:
        vals = [self.px[t] for t in range(int(start), int(end_incl) + 1) if t in self.px]
        return sum(vals) / len(vals) if vals else None

    def coverage(self, start: int, end_incl: int) -> float:
        n = int(end_incl) - int(start) + 1
        return sum(1 for t in range(int(start), int(end_incl) + 1) if t in self.px) / n if n > 0 else 0.0

    def sigma_per_s(self, now: int, lookback_s: int = 3600) -> Optional[float]:
        """Volatility in dollars per sqrt(second), from one-minute log returns over the lookback."""
        s, n = 0.0, 0
        for t in range(int(now) - lookback_s + 60, int(now) + 1, 60):
            a, b = self.at(t), self.at(t - 60)
            if a and b:
                s += math.log(a / b) ** 2
                n += 1
        last = self.at(now)
        if n < 15 or not last:
            return None
        return last * math.sqrt(s / n / 60.0)

    def moved(self, now: int, window_s: float) -> float:
        """The largest absolute move of the index within the last `window_s` seconds."""
        vals = [self.px[t] for t in range(int(now - window_s), int(now) + 1) if t in self.px]
        return (max(vals) - min(vals)) if len(vals) >= 2 else 0.0


def fair_up(tape: PriceTape, now: int, window_end: int, price_to_beat: float, sigma: float, vol_mult: float = 1.0,
            drift_beta: float = 0.0, drift_lookback_s: int = 30) -> Optional[float]:
    """Probability the window settles Up. None when there is not enough data to say.

    drift_beta (v1.3): BTC's index keeps going the way it just went. Measured over 117 five-minute windows (10 hours of BRTI),
    a move over the last 30 seconds continued to the close 61-66% of the time, by roughly as much again (regression slope
    0.6-1.3). So the expected close is shifted by drift_beta x (the last 30 seconds' move); for the part of the closing average
    already known, only the seconds still to come carry it."""
    s = tape.at(now)
    if s is None or not sigma or sigma <= 0 or not price_to_beat:
        return None
    drift = 0.0
    if drift_beta:
        s0 = tape.at(now - drift_lookback_s, back=5)
        if s0 is not None:
            drift = drift_beta * (s - s0)
    sd1 = sigma * vol_mult
    first = window_end - AVG_S
    if now < first:
        mean = s + drift
        var = sd1 * sd1 * ((first - now) + AVG_S / 3.0)
    elif now < window_end:
        k = now - first
        known = (tape.average(first, now - 1) or s) * k if k > 0 else 0.0
        m = window_end - now
        mean = (known + m * (s + drift)) / AVG_S
        var = sd1 * sd1 * (m ** 3) / (3.0 * AVG_S * AVG_S)
    else:
        close = tape.average(first, window_end - 1)
        if close is None:
            return None
        return 1.0 if close >= price_to_beat else 0.0
    sd = math.sqrt(max(var, 1e-12))
    p = phi((mean - price_to_beat) / sd)
    return min(0.999, max(0.001, p))


def settle(tape: PriceTape, window_end: int, price_to_beat: float) -> Optional[Tuple[float, bool]]:
    """(closing average, settled Up?) once the closing minute is fully in, else None."""
    first = window_end - AVG_S
    if tape.coverage(first, window_end - 1) < 0.9:
        return None
    close = tape.average(first, window_end - 1)
    # v1.14: compare at full precision. Rounding both to the cent made nearly every XRP window (~$1.23) a "tie", and ties settle
    # Up, so the desk booked Up for windows the exchange settled Down (the XRP scoreboard did not match the account).
    return (round(close, 6), round(close, 6) >= round(price_to_beat, 6)) if close is not None else None
