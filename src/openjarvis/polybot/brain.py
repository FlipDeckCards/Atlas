"""Strategy v1.1: what the desk learned from its first morning, written into the rules.

The first paper session showed one pattern again and again: BTC dropped, sellers dumped "Up" cheaply, the model still priced Up
higher, the desk kept buying, and the window settled Down (9 of 10 windows ended one-sided, mostly Up, 7 of 10 lost). The market
was reacting faster and better than the model alone. So:

  1. Respect the market. Fair value is a blend of the model and the market's own mid price (model_weight, default 0.4). The desk
     only buys when the blend says the price is cheap, i.e. when the model disagrees with the market strongly enough on its own.
  2. Don't catch a falling knife. If the index has moved against a side over the last `momentum_s` seconds by more than
     `momentum_z` standard deviations, that side is not bought (Up while BTC is dropping, Down while it is rising).
  3. One bite per side per window. After a side fills in a window, the desk stops adding to it there (`max_fills_per_side`).
  4. Learn from results. The desk keeps its last 20 settled windows; while their return is negative it demands more edge
     (half the losing return, up to +0.10), and it relaxes back as results turn positive. Shown on the Station as "extra edge".
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from openjarvis.polybot.model import PriceTape

STRATEGY_VERSION = "1.3"           # 1.3: the model expects the last 30 s of BTC's move to carry on (measured, not guessed)


def market_mid(best_bid: Optional[float], best_ask: Optional[float]) -> Optional[float]:
    if best_bid is not None and best_ask is not None and best_ask >= best_bid:
        return (best_bid + best_ask) / 2.0
    return None


def blend(model_p: Optional[float], mid: Optional[float], model_weight: float) -> Optional[float]:
    """The fair value the desk trades on. Without a market price it falls back to the model alone, but only when the weight on
    the model is high; with a thin or one-sided book it does not trust the model by itself."""
    if model_p is None:
        return None
    if mid is None:
        return model_p if model_weight >= 0.99 else None
    w = min(1.0, max(0.0, model_weight))
    return min(0.999, max(0.001, w * model_p + (1.0 - w) * mid))


def momentum_z(tape: PriceTape, now: int, lookback_s: int, sigma_per_s: Optional[float]) -> float:
    """How far the index moved over the lookback, in standard deviations of a normal move over that time (positive = up)."""
    a, b = tape.at(now), tape.at(now - lookback_s, back=10)
    if a is None or b is None or not sigma_per_s or sigma_per_s <= 0:
        return 0.0
    return (a - b) / (sigma_per_s * math.sqrt(lookback_s))


def side_filters(z: float, threshold: float) -> Tuple[bool, bool, str]:
    """(can_up, can_down, why) from momentum."""
    if threshold <= 0:
        return True, True, ""
    if z <= -threshold:
        return False, True, f"BTC is falling ({z:+.1f} sd): not buying Up."
    if z >= threshold:
        return True, False, f"BTC is rising ({z:+.1f} sd): not buying Down."
    return True, True, ""


@dataclass
class Learner:
    """Keeps the last `size` settled windows (P&L, staked) and turns a losing run into extra required edge."""
    size: int = 20
    min_windows: int = 5
    max_extra: float = 0.10
    history: Deque[Tuple[float, float]] = field(default_factory=deque)

    def add(self, pnl: float, staked: float) -> None:
        if staked <= 0:
            return
        self.history.append((float(pnl), float(staked)))
        while len(self.history) > self.size:
            self.history.popleft()

    @property
    def rolling_return(self) -> Optional[float]:
        staked = sum(s for _, s in self.history)
        if len(self.history) < self.min_windows or staked <= 0:
            return None
        return sum(p for p, _ in self.history) / staked

    @property
    def extra_edge(self) -> float:
        r = self.rolling_return
        if r is None or r >= 0:
            return 0.0
        return round(min(self.max_extra, -r * 0.5), 3)

    def dump(self) -> List[List[float]]:
        return [[round(p, 4), round(s, 4)] for p, s in self.history]

    def load(self, rows) -> None:
        self.history.clear()
        for r in rows or []:
            try:
                self.add(float(r[0]), float(r[1]))
            except (TypeError, ValueError, IndexError):
                continue


def can_add(fills: Dict[str, int], side: str, max_fills_per_side: int) -> bool:
    return max_fills_per_side <= 0 or fills.get(side, 0) < max_fills_per_side
