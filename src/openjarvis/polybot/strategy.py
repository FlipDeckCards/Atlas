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
    taker: bool = False   # v1.12: a hedge that crosses the spread (not post-only) to finish a pair before the close

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


def _unpaired_room(unpaired_usd: float, cost_per: float, side_qty: float, other_qty: float, min_qty: float) -> float:
    """v1.8: how many more contracts this side may buy so that the part NOT matched by the other side costs at most unpaired_usd.
    Pairs (under $1) win whichever way BTC goes; only the unmatched part can lose, so only it is capped. Buying to complete a
    pair is always allowed (it lowers the unmatched part)."""
    if cost_per <= 0:
        return 0.0
    room = (other_qty - side_qty) + unpaired_usd / cost_per
    return round(math.floor(room / min_qty + 1e-9) * min_qty, 6) if room >= min_qty else 0.0


def _contracts_room(cap_usd: float, held: float, min_qty: float) -> float:
    """v1.6: a side may hold at most `cap_usd` contracts. A completed pair costs under $1 a contract, so whatever the first side
    holds, the window's budget can always buy the matching second side (the live losses at 2:05 and 2:15 were pairs that could
    not be completed because the first side had used most of the budget)."""
    room = cap_usd - held
    return round(math.floor(room / min_qty + 1e-9) * min_qty, 6) if room >= min_qty else 0.0


def skew_edge(inv: Inventory, side: str, limits: Limits) -> float:
    """Extra edge demanded to add to a side that is already heavier than the other (dollars per contract)."""
    heavy = (inv.up_qty - inv.down_qty) if side == "up" else (inv.down_qty - inv.up_qty)
    if heavy <= 0 or limits.max_order_usd <= 0:
        return 0.0
    # one extra cent per order's worth of one-sided contracts (at ~50c), at most ten cents
    per = max(limits.max_order_usd / 0.5, 1e-9)
    return min(0.10, 0.01 * heavy / per)


@dataclass(frozen=True)
class Sizing:
    """How much to put on one order (v1.2): quarter-Kelly on the edge, scaled to the account and capped.
    Kelly for a contract that costs c and pays $1 with probability p: stake fraction f = (p - c) / (1 - c).
    The desk stakes kelly_fraction x f x equity, at least min_order_usd, at most the order cap; a window holds at most
    window_pct % of equity (and never more than the hard ceilings in Limits)."""
    equity: float = 50.0
    kelly_fraction: float = 0.25
    window_pct: float = 5.0
    min_order_usd: float = 0.25

    def window_cap(self, limits: Limits) -> float:
        return round(min(limits.max_window_usd, max(0.0, self.equity) * self.window_pct / 100.0), 2)

    def order_usd(self, prob: float, cost: float, limits: Limits) -> float:
        if cost <= 0 or cost >= 1 or prob <= cost:
            return 0.0
        f = (prob - cost) / (1.0 - cost)
        usd = self.kelly_fraction * f * max(0.0, self.equity)
        return round(min(max(usd, self.min_order_usd), limits.max_order_usd, self.window_cap(limits)), 2)


def taker_fee(p: float) -> float:
    """Polymarket US taker fee per contract at price p."""
    return 0.0695 * p * (1.0 - p)


def sweep(prob_up: float, mid: Optional[float], book: Book, inv: Inventory, *, cap_usd: float, budget_left: float,
          min_price: float = 0.75, max_price: float = 0.97, min_edge: float = 0.03, mid_agree: float = 0.65,
          gap_bp: Optional[float] = None, min_gap_bp: float = 0.0) -> Optional[Quote]:
    """v1.13 closing sweep: near the close, buy the side that is likely to win at its asking price, but only when the model's
    probability beats the price by at least min_edge after the taker fee, the market leans the same way, and the price is in
    [min_price, max_price]. Size: the exposure left on that side (beyond what pairs cover) at most cap_usd."""
    for side, prob, ask in (("up", prob_up, book.best_ask), ("down", 1.0 - prob_up, None if book.best_bid is None else 1.0 - book.best_bid)):
        if ask is None or not (min_price - 1e-9 <= ask <= max_price + 1e-9):
            continue
        if mid is None or (mid if side == "up" else 1.0 - mid) < mid_agree:
            continue
        # v1.14: the index must already be clearly on this side of the price to beat (min_gap_bp basis points). A sweep pays ~20c
        # and loses ~80c, so near-ties are not worth it; and a tie settles Up, so Down needs the index strictly below.
        if min_gap_bp > 0:
            if gap_bp is None or (gap_bp if side == "up" else -gap_bp) < min_gap_bp:
                continue
        if prob - ask - taker_fee(ask) < min_edge:
            continue
        held, other = (inv.up_qty, inv.down_qty) if side == "up" else (inv.down_qty, inv.up_qty)
        room_usd = min(cap_usd - max(0.0, held - other) * ask, budget_left)
        q = _qty(room_usd, ask, book.min_qty)
        if q > 0:
            return Quote(side, round(ask if side == "up" else 1.0 - ask, 6), q, taker=True)
    return None


def quotes(fair: float, book: Book, inv: Inventory, limits: Limits, *, budget_left: float, can_up: bool = True,
           can_down: bool = True, sizing: Optional[Sizing] = None, pair_margin: float = 0.03,
           min_cost: float = 0.0, max_cost: float = 1.0, unpaired_usd: Optional[float] = None,
           open_min: float = 0.0, open_max: float = 1.0, hedge_loss: Optional[float] = None) -> List[Quote]:
    """The orders the desk wants resting right now in this window (possibly none)."""
    out: List[Quote] = []
    t = book.tick
    cap = sizing.window_cap(limits) if sizing else limits.max_window_usd
    window_left = max(0.0, min(cap - inv.spent, budget_left))
    if window_left < 0.05 or fair is None:
        return out
    lo, hi = max(t, 0.01), min(1.0 - t, 0.99)

    # v1.12 hedge: a leg left unpaired too long (or close to the close) is finished now, crossing the spread if it must, as long as
    # the pair costs at most $1 + hedge_loss. That swaps a coin flip of the whole leg (-$1 to -$2.30 tonight) for a few cents at most,
    # and when the leg is already winning the other side is cheap, so finishing the pair locks the profit in.
    if hedge_loss is not None and window_left >= 0.05:
        if inv.down_qty > inv.up_qty + book.min_qty / 2:
            p = _floor(1.0 - inv.down_cost / inv.down_qty + hedge_loss, t)
            if book.best_ask is not None:
                p = min(p, book.best_ask)
            q = min(round(inv.down_qty - inv.up_qty, 2), _qty(window_left, p, book.min_qty))
            if lo <= p <= hi and q > 0:
                return [Quote("up", p, q, taker=book.best_ask is not None and p >= book.best_ask - 1e-9)]
        elif inv.up_qty > inv.down_qty + book.min_qty / 2:
            p = _ceil(inv.up_cost / inv.up_qty - hedge_loss, t)
            if book.best_bid is not None:
                p = max(p, book.best_bid)
            q = min(round(inv.up_qty - inv.down_qty, 2), _qty(window_left, 1.0 - p, book.min_qty))
            if lo <= p <= hi and q > 0:
                return [Quote("down", p, q, taker=book.best_bid is not None and p <= book.best_bid + 1e-9)]

    if can_up:
        edge = limits.min_edge + skew_edge(inv, "up", limits)
        p = _floor(fair - edge, t)
        if book.best_bid is not None:
            p = min(p, _floor(book.best_bid + t, t))         # one tick better than the best bid is enough to lead
        if book.best_ask is not None:
            p = min(p, _floor(book.best_ask - t, t))         # never cross
        if inv.down_qty > 0 and pair_margin is not None:      # v1.4: Up + the Down already held must cost under $1 - margin
            p = min(p, _floor(1.0 - inv.down_cost / inv.down_qty - pair_margin, t))
        completing = inv.down_qty > inv.up_qty + 1e-9
        band = (0.0, 1.0) if completing else (max(min_cost, open_min), min(max_cost, open_max))   # v1.9: open only near 50/50
        if lo <= p <= hi and band[0] - 1e-9 <= p <= band[1] + 1e-9:          # v1.5: no long shots, no expensive favourites
            usd = sizing.order_usd(fair, p, limits) if sizing else limits.max_order_usd
            q = _qty(min(usd, window_left), p, book.min_qty)
            if inv.down_qty > inv.up_qty:                                     # v1.5: complete the pair exactly, no leftover
                q = min(round(inv.down_qty - inv.up_qty, 2), _qty(window_left, p, book.min_qty))
            q = min(q, _contracts_room(cap, inv.up_qty, book.min_qty))         # v1.6: never more contracts a side than the window's dollars
            if unpaired_usd is not None:                                      # v1.8: at most unpaired_usd riding on Up alone
                q = min(q, _unpaired_room(unpaired_usd, p, inv.up_qty, inv.down_qty, book.min_qty))
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
        if inv.up_qty > 0 and pair_margin is not None:        # v1.4: Down (costs 1 - p) + the Up already held under $1 - margin
            p = max(p, _ceil(inv.up_cost / inv.up_qty + pair_margin, t))
        completing = inv.up_qty > inv.down_qty + 1e-9
        band = (0.0, 1.0) if completing else (max(min_cost, open_min), min(max_cost, open_max))
        if lo <= p <= hi and band[0] - 1e-9 <= 1.0 - p <= band[1] + 1e-9:
            usd = sizing.order_usd(1.0 - fair, 1.0 - p, limits) if sizing else limits.max_order_usd
            q = _qty(min(usd, window_left), 1.0 - p, book.min_qty)
            if inv.up_qty > inv.down_qty:
                q = min(round(inv.up_qty - inv.down_qty, 2), _qty(window_left, 1.0 - p, book.min_qty))
            q = min(q, _contracts_room(cap, inv.down_qty, book.min_qty))
            if unpaired_usd is not None:
                q = min(q, _unpaired_room(unpaired_usd, 1.0 - p, inv.down_qty, inv.up_qty, book.min_qty))
            if q > 0:
                out.append(Quote("down", p, q))
    return out


def pair_takes(best_bid: Optional[float], best_ask: Optional[float], bid_size: float, ask_size: float, budget_usd: float,
               window_cap_usd: float, min_qty: float, max_cost: float = 0.97) -> Tuple[List["Quote"], str]:
    """v1.16 back to basics: open a pair ONLY by buying both sides at the same moment at the current asks, when the two together
    cost at most max_cost and the book has size on both sides for the whole pair. Nothing rests, so no one-sided leg is opened on
    a hope the other side will fill. Returns (quotes, why)."""
    if best_bid is None or best_ask is None:
        return [], "Waiting for both sides of the book."
    up = round(best_ask, 6)
    down = round(1.0 - best_bid, 6)
    pair = up + down
    if pair > max_cost + 1e-9:
        return [], f"Both sides cost {pair * 100:.0f}c together (limit {max_cost * 100:.0f}c): no pair this window."
    usd = min(budget_usd, window_cap_usd)
    q = _qty(usd, pair, min_qty)
    q = min(q, math.floor(ask_size / min_qty + 1e-9) * min_qty, math.floor(bid_size / min_qty + 1e-9) * min_qty)
    q = round(q, 6)
    if q < min_qty:
        return [], "Not enough size on both sides at once for a pair."
    return ([Quote("up", up, q, taker=True), Quote("down", round(best_bid, 6), q, taker=True)],
            f"Opening a pair: buying both sides now for {pair * 100:.0f}c.")
