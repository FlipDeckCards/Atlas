"""Where to rest orders, given fair value, the book and what the desk already holds in this window.

Prices are always in LONG ("Up") terms, the way Polymarket US quotes them:
  * Up quote   = ORDER_INTENT_BUY_LONG  at long price P: pays P per contract, worth $1 if Up.
  * Down quote = ORDER_INTENT_BUY_SHORT at long price P: pays 1 - P per contract, worth $1 if Down.
    It rests on the long book as an offer at P, so it must sit ABOVE the best bid or it would trade at once.

Rules:
  * Up only at P <= fair - edge; Down only at P >= fair + edge (its cost 1 - P <= (1 - fair) - edge).
  * Never cross: Up < best ask, Down > best bid (orders are post-only anyway; this avoids pointless rejects).
  * Join one tick inside the spread when that is still within the edge, so the desk is first in line at a price it likes.
  * Inventory skew: the more one side is already held in this window, the more edge is demanded to add to it, so the desk
    leans towards holding matched Up + Down pairs (whose combined cost below $1 is locked profit).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from openjarvis.polybot.config import Limits


@dataclass(frozen=True)
class Book:
    best_bid: Optional[float]      # long terms
    best_ask: Optional[float]
    tick: float = 0.01
    min_qty: float = 0.01


@dataclass
class Inventory:
    """What the desk holds in one window (filled), plus what its resting orders could still add."""
    up_qty: float = 0.0
    up_cost: float = 0.0
    down_qty: float = 0.0
    down_cost: float = 0.0

    @property
    def spent(self) -> float:
        return self.up_cost + self.down_cost

    def pnl_if(self, up: bool) -> float:
        return (self.up_qty if up else self.down_qty) - self.spent

    @property
    def worst_case(self) -> float:
        return min(self.pnl_if(True), self.pnl_if(False))


@dataclass(frozen=True)
class Quote:
    side: str          # "up" | "down"
    price: float       # long price
    qty: float

    @property
    def cost_per(self) -> float:
        return self.price if self.side == "up" else round(1.0 - self.price, 6)

    @property
    def cost(self) -> float:
        return self.qty * self.cost_per


def _floor(x: float, tick: float) -> float:
    return round(math.floor(x / tick + 1e-9) * tick, 6)


def _ceil(x: float, tick: float) -> float:
    return round(math.ceil(x / tick - 1e-9) * tick, 6)


def _qty(usd: float, cost_per: float, min_qty: float) -> float:
    if cost_per <= 0 or usd <= 0:
        return 0.0
    q = math.floor(usd / cost_per / min_qty + 1e-9) * min_qty
    return round(q, 6) if q >= min_qty else 0.0


def skew_edge(inv: Inventory, side: str, limits: Limits) -> float:
    """Extra edge demanded to add to a side that is already heavier than the other (dollars per contract)."""
    heavy = (inv.up_qty - inv.down_qty) if side == "up" else (inv.down_qty - inv.up_qty)
    if heavy <= 0 or limits.max_order_usd <= 0:
        return 0.0
    # one extra cent per order's worth of one-sided contracts (at ~50c), at most ten cents
    per = max(limits.max_order_usd / 0.5, 1e-9)
    return min(0.10, 0.01 * heavy / per)


def quotes(fair: float, book: Book, inv: Inventory, limits: Limits, *, budget_left: float, can_up: bool = True,
           can_down: bool = True) -> List[Quote]:
    """The orders the desk wants resting right now in this window (possibly none)."""
    out: List[Quote] = []
    t = book.tick
    window_left = max(0.0, min(limits.max_window_usd - inv.spent, budget_left))
    if window_left < 0.05 or fair is None:
        return out
    lo, hi = max(t, 0.01), min(1.0 - t, 0.99)

    if can_up:
        edge = limits.min_edge + skew_edge(inv, "up", limits)
        p = _floor(fair - edge, t)
        if book.best_bid is not None:
            p = min(p, _floor(book.best_bid + t, t))         # one tick better than the best bid is enough to lead
        if book.best_ask is not None:
            p = min(p, _floor(book.best_ask - t, t))         # never cross
        if lo <= p <= hi:
            q = _qty(min(limits.max_order_usd, window_left), p, book.min_qty)
            if q > 0:
                out.append(Quote("up", p, q))
                window_left -= q * p

    if can_down and window_left >= 0.05:
        edge = limits.min_edge + skew_edge(inv, "down", limits)
        p = _ceil(fair + edge, t)
        if book.best_ask is not None:
            p = max(p, _ceil(book.best_ask - t, t))
        if book.best_bid is not None:
            p = max(p, _ceil(book.best_bid + t, t))
        if lo <= p <= hi:
            q = _qty(min(limits.max_order_usd, window_left), 1.0 - p, book.min_qty)
            if q > 0:
                out.append(Quote("down", p, q))
    return out
