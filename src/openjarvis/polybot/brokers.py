"""Two ways to carry out the desk's quotes: PaperBroker (simulated, no orders sent) and LiveBroker (real orders on Polymarket US).

Both keep at most one resting order per side ("up" / "down") in the current window and report fills as Fill records.

Paper fills are deliberately conservative. A resting paper bid for Up at P is filled only when the real market shows that a seller
went through that price after the order was placed: the best ask at or below P (anyone offering there would have hit us first, as
no real bid sat at that price), or a trade printed BELOW P. A trade exactly at P does not count: a real order at P was ahead of us.
Mirror image for Down. Fill size is capped by the size shown at that level. It cannot see trades that happen and vanish between
two looks a second apart, so it misses some fills; it never invents a price better than one that actually traded or was offered.
"""

from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openjarvis.polybot.client import PMError, money
from openjarvis.polybot.strategy import Quote

logger = logging.getLogger("polybot.brokers")


def parse_ts(raw: Any) -> float:
    """RFC 3339 with up to nanoseconds ("2026-10-29T15:36:26.938352977Z") -> unix seconds; 0.0 when missing or unreadable."""
    import datetime as dt

    s = str(raw or "").strip()
    if not s:
        return 0.0
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac = ""
        for ch in rest:                    # only the digits right after the dot (the zone that follows has digits too)
            if not ch.isdigit():
                break
            frac += ch
        tz = rest[len(frac):] or "+00:00"
        s = f"{head}.{frac[:6]}{tz}"
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


@dataclass
class Fill:
    slug: str
    side: str          # up | down
    price: float       # long price of the order
    qty: float
    ts: float
    order_id: str = ""

    @property
    def cost(self) -> float:
        return round(self.qty * (self.price if self.side == "up" else 1.0 - self.price), 6)


@dataclass
class Resting:
    quote: Quote
    order_id: str
    placed_ts: float
    filled: float = 0.0

    @property
    def left(self) -> float:
        return max(0.0, round(self.quote.qty - self.filled, 6))


@dataclass
class BookView:
    """What the brokers need from one look at the order book (long terms)."""
    bids: List[tuple] = field(default_factory=list)      # [(price, qty)] best first
    offers: List[tuple] = field(default_factory=list)
    last_trade_px: Optional[float] = None
    last_trade_qty: Optional[float] = None
    last_trade_ts: float = 0.0
    state: str = ""
    ts: float = 0.0

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.offers[0][0] if self.offers else None

    @classmethod
    def parse(cls, md: Dict[str, Any], now: float) -> "BookView":
        def levels(rows):
            out = []
            for r in rows or []:
                p, q = money(r.get("px")), money(r.get("qty"))
                if p is not None and q:
                    out.append((round(p, 4), q))
            return out
        st = md.get("stats") or {}
        lt_ts = parse_ts(st.get("lastTradeSetTime"))
        bids = sorted(levels(md.get("bids")), key=lambda x: -x[0])
        offers = sorted(levels(md.get("offers")), key=lambda x: x[0])
        return cls(bids=bids, offers=offers, last_trade_px=money(st.get("lastTradePx")), last_trade_qty=money(st.get("lastTradeQty")),
                   last_trade_ts=lt_ts, state=str(md.get("state") or ""), ts=now)


def keep(r: "Resting", q: Optional[Quote], tick: float = 0.01) -> bool:
    """Leave a resting order alone unless it must change: it is now priced past the edge (must move at once), or the better
    price is 2+ ticks away, or it is bigger than wanted. Avoids cancel/replace every second (queue position, exchange throttles)."""
    if q is None or q.side != r.quote.side or q.qty < r.left - 1e-9:
        return False
    if q.side == "up":                      # a bid: the wanted price is the most it may pay
        return r.quote.price <= q.price + 1e-9 and q.price - r.quote.price < 2 * tick - 1e-9
    return r.quote.price >= q.price - 1e-9 and r.quote.price - q.price < 2 * tick - 1e-9


class PaperBroker:
    mode = "paper"

    def __init__(self) -> None:
        self.slug = ""
        self.resting: Dict[str, Resting] = {}
        self._ids = itertools.count(1)

    @property
    def resting_cost(self) -> float:
        return sum(r.left * r.quote.cost_per for r in self.resting.values())

    async def set_quotes(self, slug: str, want: List[Quote], now: float) -> None:
        if slug != self.slug:
            self.resting, self.slug = {}, slug
        sides = {q.side: q for q in want}
        for side in list(self.resting):
            r = self.resting[side]
            q = sides.get(side)
            if not keep(r, q):
                del self.resting[side]                    # cancelled (a new price is a new order at the back of the queue)
        for side, q in sides.items():
            if side not in self.resting:
                self.resting[side] = Resting(q, f"paper-{next(self._ids)}", now)

    async def cancel_all(self, now: float = 0.0) -> None:
        self.resting = {}

    async def sync(self, book: Optional[BookView], now: float) -> List[Fill]:
        fills: List[Fill] = []
        if not book:
            return fills
        for side, r in list(self.resting.items()):
            p, left = r.quote.price, r.left
            if left <= 0:
                continue
            got = 0.0
            if side == "up":
                through = [q for (px, q) in book.offers if px <= p + 1e-9]
                if through:
                    got = sum(through)
                elif book.last_trade_px is not None and book.last_trade_ts > r.placed_ts and book.last_trade_px < p - 1e-9:
                    got = book.last_trade_qty or 0.0
            else:
                through = [q for (px, q) in book.bids if px >= p - 1e-9]
                if through:
                    got = sum(through)
                elif book.last_trade_px is not None and book.last_trade_ts > r.placed_ts and book.last_trade_px > p + 1e-9:
                    got = book.last_trade_qty or 0.0
            q = round(min(left, got), 2)
            if q > 0:
                r.filled = round(r.filled + q, 6)
                fills.append(Fill(self.slug, side, p, q, now, r.order_id))
                if r.left <= 0:
                    del self.resting[side]
        return fills

    async def balance(self) -> Optional[float]:
        return None


class LiveBroker:
    """Real post-only orders. Fills are read back from the exchange (open orders, then each order's final state)."""

    mode = "live"
    SYNC_EVERY_S = 2.0

    def __init__(self, client) -> None:
        self.c = client
        self.slug = ""
        self.resting: Dict[str, Resting] = {}
        self.closing: Dict[str, Resting] = {}          # cancelled orders whose final fill is not read back yet (order id -> record)
        self._last_sync = 0.0
        self._bal: Optional[float] = None
        self.rejects: List[str] = []
        self._bal_ts = 0.0

    @property
    def resting_cost(self) -> float:
        return sum(r.left * r.quote.cost_per for r in self.resting.values())

    async def _cancel(self, r: Resting) -> None:
        try:
            await self.c.cancel(r.order_id, self.slug)
        except PMError as exc:
            logger.warning("cancel %s: %s", r.order_id, exc)
        self.closing[r.order_id] = r

    async def set_quotes(self, slug: str, want: List[Quote], now: float) -> None:
        if slug != self.slug and self.resting:
            for r in list(self.resting.values()):
                await self._cancel(r)
            self.resting = {}
        self.slug = slug
        sides = {q.side: q for q in want}
        for side in list(self.resting):
            r = self.resting[side]
            q = sides.get(side)
            if not keep(r, q):
                await self._cancel(r)
                del self.resting[side]
        for side, q in sides.items():
            if side in self.resting:
                continue
            try:
                oid = await self.c.place(slug, side, q.price, q.qty)
            except PMError as exc:
                if exc.status and 400 <= exc.status < 500 and exc.status != 401:   # e.g. post-only would cross, size or tick: skip this quote
                    self.rejects.append(f"{side} {q.qty:g} @ {q.price:.2f}: {exc}")
                    self.rejects = self.rejects[-20:]
                    logger.warning("order rejected: %s", exc)
                    continue
                raise
            self.resting[side] = Resting(q, oid, now)

    async def cancel_all(self, now: float = 0.0) -> None:
        try:
            await self.c.cancel_all([self.slug] if self.slug else None)
        except PMError as exc:
            logger.warning("cancel_all: %s", exc)
            for r in list(self.resting.values()):
                await self._cancel(r)
        for r in self.resting.values():
            self.closing[r.order_id] = r
        self.resting = {}

    async def sync(self, book: Optional[BookView], now: float) -> List[Fill]:
        if now - self._last_sync < self.SYNC_EVERY_S or not (self.resting or self.closing):
            return []
        self._last_sync = now
        fills: List[Fill] = []
        open_by_id = {str(o.get("id")): o for o in await self.c.open_orders([self.slug] if self.slug else None)}
        tracked = [(side, r, False) for side, r in self.resting.items()] + [(r.quote.side, r, True) for r in self.closing.values()]
        for side, r, closing in tracked:
            o = open_by_id.get(r.order_id)
            final = o is None
            if final:
                try:
                    o = await self.c.order(r.order_id)
                except PMError as exc:
                    logger.warning("order %s: %s", r.order_id, exc)
                    continue
            cum = money(o.get("cumQuantity")) or 0.0
            if cum > r.filled + 1e-9:
                px = money(o.get("avgPx")) or r.quote.price
                fills.append(Fill(self.slug, side, px, round(cum - r.filled, 6), now, r.order_id))
                r.filled = cum
            state = str(o.get("state") or "")
            done = final or state in ("ORDER_STATE_FILLED", "ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED", "ORDER_STATE_EXPIRED")
            if done:
                if closing:
                    self.closing.pop(r.order_id, None)
                elif self.resting.get(side) is r:
                    del self.resting[side]
                if state == "ORDER_STATE_REJECTED":
                    logger.warning("order rejected: %s", o.get("text") or o)
        return fills

    async def balance(self) -> Optional[float]:
        if time.time() - self._bal_ts > 30:
            try:
                self._bal = await self.c.buying_power()
                self._bal_ts = time.time()
            except PMError as exc:
                logger.warning("balance: %s", exc)
        return self._bal
