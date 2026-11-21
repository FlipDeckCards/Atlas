"""Robinhood crypto engine (owner, 2026-11-20): everything a strategy needs to trade BTC, ETH, SOL and XRP on Robinhood, built
before the strategy exists, so going live later is a switch, not a build.

  - One loop every LOOP_S seconds: reads the owner's control row, prices (best bid/ask, spread included), the account and the
    holdings, asks the selected strategy for signals, sizes them with the owner's limits (robinhood_bot.size_order: 3% of the
    account per order, 12% at risk across the coins), and carries them out on paper or live.
  - The strategy slot is empty: with no strategy the engine only watches and records prices; it never invents a trade.
  - Paper: fills at the real ask (buy) or bid (sell), spread included, on a simulated cash balance. Nothing reaches Robinhood.
  - Live needs all of: Robinhood connected, a strategy selected, the owner's Go live tap. While "approve each order" is on (the
    default, the owner's rule), every live order waits on the Station for the owner's Approve; unapproved ones lapse after
    APPROVAL_TTL_S.
  - Pause stops new orders; the kill switch cancels every open Robinhood order the engine placed (never the owner's own), pauses, and puts the engine back on paper.
  - Every order, fill and decision is recorded (rh_orders, rh_fills, rh_events); entries and exits push to the phone.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from openjarvis.server.crew import robinhood as rh
from openjarvis.server.crew import robinhood_bot as bot

logger = logging.getLogger("crew.rh_engine")

LOOP_S = 15.0
ACCOUNT_EVERY_S = 60.0
APPROVAL_TTL_S = 300.0
ORDER_WAIT_S = 30.0                 # how long a live order is followed before it is cancelled as stale
PAPER_START_CASH = 1000.0
SYMBOL = {c: f"{c}-USD" for c in bot.COINS}

DDL = """
CREATE TABLE IF NOT EXISTS rh_orders (
  id TEXT PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(), mode TEXT NOT NULL, coin TEXT NOT NULL, side TEXT NOT NULL,
  type TEXT NOT NULL, notional DOUBLE PRECISION, qty DOUBLE PRECISION, limit_price DOUBLE PRECISION, state TEXT NOT NULL,
  filled_qty DOUBLE PRECISION DEFAULT 0, avg_price DOUBLE PRECISION, exchange_id TEXT, strategy TEXT, reason TEXT,
  detail TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS rh_orders_ts ON rh_orders (ts);
CREATE TABLE IF NOT EXISTS rh_fills (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(), mode TEXT NOT NULL, order_id TEXT NOT NULL, coin TEXT NOT NULL,
  side TEXT NOT NULL, qty DOUBLE PRECISION NOT NULL, price DOUBLE PRECISION NOT NULL, notional DOUBLE PRECISION NOT NULL,
  realized_pnl DOUBLE PRECISION, strategy TEXT);
CREATE INDEX IF NOT EXISTS rh_fills_ts ON rh_fills (ts);
CREATE TABLE IF NOT EXISTS rh_events (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(), level TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rh_quotes (ts TIMESTAMPTZ NOT NULL DEFAULT now(), coin TEXT NOT NULL, bid DOUBLE PRECISION NOT NULL,
  ask DOUBLE PRECISION NOT NULL, mid DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS rh_quotes_ts ON rh_quotes (ts);
ALTER TABLE rh_events ADD COLUMN IF NOT EXISTS coin TEXT;
ALTER TABLE rh_fills ADD COLUMN IF NOT EXISTS features JSONB;
CREATE TABLE IF NOT EXISTS rh_kv (key TEXT PRIMARY KEY, value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""

DEFAULT_CONTROL = {"mode": "paper", "paused": False, "strategy": "", "approve_each": True,
                   # 2026-11-20 (owner): one desk per coin. Each can be paused on its own; the daily stop (percent of the day's
                   # starting value) stops new buys for the rest of the Los Angeles day once the day's loss reaches it.
                   "coins": {c: {"paused": False} for c in bot.COINS}, "daily_stop_pct": 8.0}   # 8%: owner, 2026-11-20


# ── the strategy slot ─────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    """What a strategy wants: buy or sell one coin. notional None = size by the owner's limits (buys) or the whole position
    (sells). limit_price None = a market order."""
    coin: str
    side: str
    reason: str
    notional: Optional[float] = None
    limit_price: Optional[float] = None


@dataclass
class Snapshot:
    """What a strategy sees each loop."""
    now: float
    prices: Dict[str, Dict[str, float]]                 # coin -> {bid, ask, mid}
    history: Dict[str, List[tuple]]                     # coin -> [(unix second, mid)], the last few hours, oldest first
    positions: Dict[str, Dict[str, float]]              # coin -> {qty, avg_cost}
    cash: float
    account_value: float


# name -> function(Snapshot) -> List[Signal]. Empty until the owner picks a strategy; adding one is a single entry here.
STRATEGIES: Dict[str, Callable[[Snapshot], List[Signal]]] = {}


# ── the engine ────────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Position:
    qty: float = 0.0
    avg_cost: float = 0.0
    opened: float = 0.0              # unix time the position was opened (hold time on exit)

    def buy(self, qty: float, price: float, ts: float = 0.0) -> None:
        if self.qty <= 0:
            self.opened = ts
        total = self.qty + qty
        self.avg_cost = (self.avg_cost * self.qty + price * qty) / total if total > 0 else 0.0
        self.qty = total

    def sell(self, qty: float, price: float) -> float:
        """Realized P&L of selling qty at price (average-cost basis)."""
        q = min(qty, self.qty)
        pnl = (price - self.avg_cost) * q
        self.qty = round(self.qty - q, 10)
        if self.qty <= 1e-12:
            self.qty, self.avg_cost, self.opened = 0.0, 0.0, 0.0
        return pnl


@dataclass
class Book:
    """Positions and cash for one mode (paper or live), rebuilt from rh_fills at startup."""
    cash: float = 0.0
    pos: Dict[str, Position] = field(default_factory=lambda: {c: Position() for c in bot.COINS})

    def exposure(self, prices: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        return {c: round(p.qty * prices.get(c, {}).get("bid", p.avg_cost), 2) for c, p in self.pos.items()}

    def value(self, prices: Dict[str, Dict[str, float]]) -> float:
        return round(self.cash + sum(self.exposure(prices).values()), 2)


class Engine:
    def __init__(self, pool: Any, client: Optional[rh.RHClient] = None, notify: Optional[Callable] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.pool, self.c, self.notify, self.clock = pool, client or rh.RHClient(), notify, clock
        self.control: Dict[str, Any] = dict(DEFAULT_CONTROL)
        self.paper = Book(cash=PAPER_START_CASH)
        self.live = Book()
        self.prices: Dict[str, Dict[str, float]] = {}
        self.history: Dict[str, List[tuple]] = {c: [] for c in bot.COINS}
        self.pairs: Dict[str, Dict[str, Any]] = {}
        self.account: Dict[str, Any] = {}
        self.holdings: Dict[str, float] = {}                 # live: what Robinhood says is available to sell, per coin
        self.last_error = ""
        self._t: Dict[str, float] = {}
        self._stop = asyncio.Event()
        self.ready = False
        self.day: Dict[str, Any] = {}
        self._reasons: Dict[str, str] = {}                  # order id -> the strategy's reason (recorded with the fill)

    # small helpers
    def _due(self, name: str, every: float, now: float) -> bool:
        if now - self._t.get(name, -1e18) >= every:
            self._t[name] = now
            return True
        return False

    async def log(self, level: str, text: str, coin: Optional[str] = None) -> None:
        logger.info("RH %s %s: %s", coin or "-", level, text)
        try:
            async with self.pool.acquire() as c:
                await c.execute("INSERT INTO rh_events (level, text, coin) VALUES ($1, $2, $3)", level, text[:1000], coin)
        except Exception:
            logger.warning("rh event not saved")

    async def push(self, title: str, text: str) -> None:
        if self.notify is not None:
            try:
                await self.notify(f"Robinhood · {title}", text)
            except Exception:
                logger.warning("rh push failed")

    async def kv_get(self, key: str) -> Dict[str, Any]:
        async with self.pool.acquire() as c:
            v = await c.fetchval("SELECT value FROM rh_kv WHERE key = $1", key)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                v = {}
        return v if isinstance(v, dict) else {}

    async def kv_put(self, key: str, value: Dict[str, Any]) -> None:
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO rh_kv (key, value, updated_at) VALUES ($1, $2::jsonb, now()) ON CONFLICT (key) DO UPDATE "
                            "SET value = EXCLUDED.value, updated_at = now()", key, json.dumps(value))

    # ── startup ──
    async def setup(self) -> None:
        async with self.pool.acquire() as c:
            await c.execute(DDL)
            rows = await c.fetch("SELECT ts, mode, coin, side, qty, price FROM rh_fills ORDER BY id")
        paper_cash = PAPER_START_CASH
        for r in rows:
            book = self.paper if r["mode"] == "paper" else self.live
            p = book.pos.setdefault(r["coin"], Position())
            if r["side"] == "buy":
                p.buy(float(r["qty"]), float(r["price"]), r["ts"].timestamp())
                if r["mode"] == "paper":
                    paper_cash -= float(r["qty"]) * float(r["price"])
            else:
                p.sell(float(r["qty"]), float(r["price"]))
                if r["mode"] == "paper":
                    paper_cash += float(r["qty"]) * float(r["price"])
        self.paper.cash = round(paper_cash, 2)
        self.control = _merge_control(await self.kv_get("control"))
        self.day = await self.kv_get("day")
        self.ready = True

    # ── the loop ──
    async def forever(self) -> None:
        try:
            await self.setup()
        except Exception:
            logger.exception("Robinhood engine could not start; nothing trades")
            return
        await self.log("info", "Robinhood engine started (paper unless the owner took it live; no strategy runs until one is picked).")
        while not self._stop.is_set():
            try:
                await self.step()
            except Exception as exc:                          # one bad loop never stops the engine
                self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                logger.exception("Robinhood engine step failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=LOOP_S)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def step(self) -> None:
        now = self.clock()
        self.control = _merge_control(await self.kv_get("control"))
        if self.control.get("kill"):
            await self.kill()
        if not rh.configured():
            await self.write_status(now, "Robinhood is not connected; the engine waits.")
            return
        await self.read_market(now)
        if self.control.get("mode") == "live" and self._due("account", ACCOUNT_EVERY_S, now):
            await self.read_account()
        await self.roll_day(now)
        await self.run_approved(now)
        why = await self.run_strategy(now)
        await self.write_status(now, why)

    async def read_market(self, now: float) -> None:
        q = await self.c.best_bid_ask([SYMBOL[c] for c in bot.COINS])
        self.prices = {c: q[SYMBOL[c]] for c in bot.COINS if SYMBOL[c] in q}
        for c, p in self.prices.items():
            h = self.history.setdefault(c, [])
            h.append((int(now), p["mid"]))
            del h[:-1500]                                   # about 6 hours at one look every 15 s
        # 2026-11-20 (owner): one quote a minute per coin is kept, to measure Robinhood's real spread and to build price history
        if self.prices and self._due("quotes", 60.0, now):
            try:
                async with self.pool.acquire() as c:
                    await c.executemany("INSERT INTO rh_quotes (coin, bid, ask, mid) VALUES ($1,$2,$3,$4)",
                                        [(k, v["bid"], v["ask"], v["mid"]) for k, v in self.prices.items()])
                    if self._due("quotes_trim", 86400.0, now):
                        await c.execute("DELETE FROM rh_quotes WHERE ts < now() - interval '120 days'")
            except Exception:
                logger.warning("rh quotes not saved")
        if not self.pairs or self._due("pairs", 3600.0, now):
            try:
                self.pairs = await self.c.trading_pairs([SYMBOL[c] for c in bot.COINS])
            except Exception as exc:
                self.last_error = f"trading pairs: {exc}"

    async def read_account(self) -> None:
        self.account = await self.c.account()
        try:
            self.live.cash = float(self.account.get("buying_power") or 0.0)
        except (TypeError, ValueError):
            pass
        # The engine trades only what it bought itself (rh_fills). Coins the owner holds by hand are shown, never sold, and a sell
        # can never exceed what Robinhood says is available.
        self.holdings = {}
        for h in await self.c.holdings():
            coin = str(h.get("asset_code") or "")
            if coin in bot.COINS:
                try:
                    self.holdings[coin] = float(h.get("quantity_available_for_trading") or h.get("total_quantity") or 0.0)
                except (TypeError, ValueError):
                    continue

    async def roll_day(self, now: float) -> None:
        """The day's starting value (Los Angeles date) per mode, for the daily stop and the scoreboard's 'today'."""
        if not self.prices:
            return
        today = dt.datetime.fromtimestamp(now, _la()).date().isoformat()
        mode = self.control.get("mode") or "paper"
        d = getattr(self, "day", {}) or {}
        if d.get("date") != today or mode not in (d.get("start") or {}):
            start = dict(d.get("start") or {}) if d.get("date") == today else {}
            start[mode] = self.book().value(self.prices)
            self.day = {"date": today, "start": start, "stopped": {} if d.get("date") != today else d.get("stopped", {})}
            await self.kv_put("day", self.day)

    def day_loss_pct(self) -> Optional[float]:
        mode = self.control.get("mode") or "paper"
        st = ((getattr(self, "day", {}) or {}).get("start") or {}).get(mode)
        if not st or not self.prices:
            return None
        return round(100.0 * (self.book().value(self.prices) - st) / st, 2)

    def stopped_today(self) -> bool:
        pct = self.day_loss_pct()
        lim = float(self.control.get("daily_stop_pct") or 0)
        return bool(lim > 0 and pct is not None and pct <= -lim)

    def book(self) -> Book:
        return self.live if self.control.get("mode") == "live" else self.paper

    def can_go_live(self) -> Optional[str]:
        """Why live is not possible right now, or None."""
        if not rh.configured():
            return "Robinhood is not connected"
        name = self.control.get("strategy") or ""
        if not name or name not in STRATEGIES:
            return "no strategy is selected"
        return None

    # ── strategy -> orders ──
    async def run_strategy(self, now: float) -> str:
        name = self.control.get("strategy") or ""
        fn = STRATEGIES.get(name)
        if fn is None:
            return "No strategy selected: watching prices only." if not name else f"Strategy '{name}' is not installed: watching only."
        if self.control.get("paused"):
            return "Paused: no new orders."
        if not self.prices:
            return "No prices yet."
        mode = self.control.get("mode")
        if mode == "live" and self.can_go_live():
            return f"Live is blocked: {self.can_go_live()}."
        book = self.book()
        snap = Snapshot(now, dict(self.prices), {c: list(h) for c, h in self.history.items()},
                        {c: {"qty": p.qty, "avg_cost": p.avg_cost} for c, p in book.pos.items()}, book.cash, book.value(self.prices))
        try:
            signals = list(fn(snap) or [])
        except Exception as exc:
            await self.log("warn", f"Strategy '{name}' failed: {exc}")
            return f"Strategy '{name}' failed this loop."
        stopped = self.stopped_today()
        if stopped and not (self.day or {}).get("stopped", {}).get(mode):
            self.day.setdefault("stopped", {})[mode] = True
            await self.kv_put("day", self.day)
            msg = (f"Daily stop: the {mode} account is down {-self.day_loss_pct():.1f}% today (limit "
                   f"{float(self.control.get('daily_stop_pct') or 0):g}%). No new buys until midnight Los Angeles time; exits still run.")
            await self.log("warn", msg)
            await self.push("Daily stop", msg)
        for s in signals:
            if (self.control.get("coins") or {}).get(s.coin, {}).get("paused"):
                await self.log("info", f"{s.coin} desk is paused: {s.side} skipped ({s.reason}).", s.coin)
                continue
            if stopped and s.side == "buy":
                continue
            await self.act(s, name, now)
        return f"Strategy '{name}': {len(signals)} signal(s) this loop." + (" Daily stop: buys off." if stopped else "")

    def size(self, s: Signal) -> tuple:
        """(dollars, coin quantity, why) for a signal, inside the owner's limits."""
        book = self.book()
        px = self.prices.get(s.coin) or {}
        if s.side == "buy":
            price = s.limit_price or px.get("ask")
            if not price:
                return 0.0, 0.0, "no price"
            sz = bot.size_order(book.value(self.prices), book.exposure(self.prices), s.coin)
            usd = sz.notional if s.notional is None else min(float(s.notional), sz.notional)
            usd = min(usd, max(0.0, book.cash))
            qty = self._round_qty(s.coin, usd / price)
            return round(qty * price, 2), qty, sz.reason
        held = book.pos.get(s.coin, Position()).qty
        if book is self.live:
            held = min(held, getattr(self, "holdings", {}).get(s.coin, 0.0))
        price = s.limit_price or px.get("bid")
        if not price or held <= 0:
            return 0.0, 0.0, "nothing to sell"
        qty = held if s.notional is None else min(held, float(s.notional) / price)
        qty = self._round_qty(s.coin, qty)
        return round(qty * price, 2), qty, "position"

    def _round_qty(self, coin: str, qty: float) -> float:
        pair = self.pairs.get(SYMBOL[coin]) or {}
        try:
            inc = float(pair.get("asset_increment") or 0) or 1e-8
            mn = float(pair.get("min_order_size") or 0)
        except (TypeError, ValueError):
            inc, mn = 1e-8, 0.0
        q = (int(qty / inc + 1e-9)) * inc
        q = round(q, 10)
        return q if q >= mn and q > 0 else 0.0

    async def act(self, s: Signal, strategy: str, now: float) -> None:
        if s.coin not in bot.COINS or s.side not in ("buy", "sell"):
            await self.log("warn", f"Ignored a bad signal: {s}")
            return
        usd, qty, why = self.size(s)
        if qty <= 0:
            await self.log("info", f"{strategy}: {s.side} {s.coin} skipped ({why}).", s.coin)
            return
        oid = str(uuid.uuid4())
        mode = self.control.get("mode") or "paper"
        typ = "limit" if s.limit_price else "market"
        state = "pending_approval" if (mode == "live" and self.control.get("approve_each", True)) else "new"
        await self.record_order(oid, mode, s.coin, s.side, typ, usd, qty, s.limit_price, state, strategy, s.reason)
        self._reasons[oid] = s.reason
        if state == "pending_approval":
            await self.log("info", f"Waiting for your approval: {s.side} {qty:g} {s.coin} (~${usd:.2f}). {s.reason}", s.coin)
            await self.push("Approve order?", f"{s.side.upper()} {qty:g} {s.coin} (~${usd:.2f}) · {s.reason} · approve on the Station within 5 minutes")
            return
        await self.execute(oid, mode, s.coin, s.side, typ, qty, s.limit_price, strategy, s.reason)

    async def run_approved(self, now: float) -> None:
        async with self.pool.acquire() as c:
            rows = await c.fetch("SELECT id, ts, mode, coin, side, type, qty, limit_price, strategy, reason, state FROM rh_orders "
                                 "WHERE state IN ('pending_approval', 'approved') ORDER BY ts")
        for r in rows:
            age = now - r["ts"].timestamp()
            if r["state"] == "pending_approval":
                if age > APPROVAL_TTL_S:
                    await self.set_state(r["id"], "expired", "not approved within 5 minutes")
                continue
            if self.control.get("mode") != "live" or self.control.get("paused") or self.can_go_live():
                await self.set_state(r["id"], "expired", "approved, but live trading was off, paused or blocked by then")
                continue
            await self.execute(r["id"], "live", r["coin"], r["side"], r["type"], float(r["qty"]), r["limit_price"],
                               r["strategy"] or "", r["reason"] or "")

    async def execute(self, oid: str, mode: str, coin: str, side: str, typ: str, qty: float, limit_price: Optional[float],
                      strategy: str, reason: str) -> None:
        px = self.prices.get(coin) or {}
        if reason:
            self._reasons.setdefault(oid, reason)
        if mode != "live":                                   # paper: fill now at the real ask or bid, or not at all
            price = px.get("ask") if side == "buy" else px.get("bid")
            if not price or (limit_price and ((side == "buy" and price > limit_price) or (side == "sell" and price < limit_price))):
                await self.set_state(oid, "canceled", "paper: the limit was not marketable")
                return
            await self.record_fill(oid, "paper", coin, side, qty, float(price), strategy)
            await self.set_state(oid, "filled", "", qty, float(price))
            return
        why = self.can_go_live()
        if why:
            await self.set_state(oid, "failed", f"live blocked: {why}")
            return
        try:
            o = await self.c.place_order(SYMBOL[coin], side, client_order_id=oid, asset_quantity=qty, limit_price=limit_price)
        except Exception as exc:
            await self.set_state(oid, "failed", str(exc)[:300])
            await self.log("warn", f"Robinhood refused {side} {qty:g} {coin}: {exc}", coin)
            await self.push("Order refused", f"{side.upper()} {qty:g} {coin}: {str(exc)[:200]}")
            return
        xid = str(o.get("id") or "")
        await self.set_state(oid, str(o.get("state") or "open"), "", exchange_id=xid)
        await self.follow(oid, xid, coin, side, strategy)

    async def follow(self, oid: str, xid: str, coin: str, side: str, strategy: str) -> None:
        """Read the order back until it is done; book what filled; cancel what is still open after ORDER_WAIT_S."""
        start, booked = self.clock(), 0.0
        o: Dict[str, Any] = {}
        while True:
            try:
                o = await self.c.get_order(xid)
            except Exception as exc:
                self.last_error = f"order read: {exc}"
                o = {}
            booked = await self._book_executions(oid, o, coin, side, strategy, booked)
            st = str(o.get("state") or "")
            if st in rh.ORDER_STATES_DONE:
                break
            if self.clock() - start > ORDER_WAIT_S:
                try:
                    await self.c.cancel_order(xid)
                except Exception as exc:
                    self.last_error = f"cancel: {exc}"
                await asyncio.sleep(1.0)
                try:
                    o = await self.c.get_order(xid)
                    booked = await self._book_executions(oid, o, coin, side, strategy, booked)
                except Exception:
                    pass
                st = str(o.get("state") or "canceled")
                break
            await asyncio.sleep(2.0)
        avg = o.get("average_price")
        await self.set_state(oid, st or "unknown", "", booked, float(avg) if avg else None)

    async def _book_executions(self, oid: str, o: Dict[str, Any], coin: str, side: str, strategy: str, booked: float) -> float:
        try:
            filled = float(o.get("filled_asset_quantity") or 0.0)
        except (TypeError, ValueError):
            return booked
        if filled <= booked + 1e-12:
            return booked
        try:
            price = float(o.get("average_price") or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            ex = o.get("executions") or []
            q = sum(float(e.get("quantity") or 0) for e in ex)
            price = sum(float(e.get("quantity") or 0) * float(e.get("effective_price") or 0) for e in ex) / q if q else 0.0
        if price <= 0:
            return booked
        await self.record_fill(oid, "live", coin, side, filled - booked, price, strategy)
        return filled

    # ── records ──
    async def record_order(self, oid, mode, coin, side, typ, usd, qty, limit_price, state, strategy, reason) -> None:
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO rh_orders (id, mode, coin, side, type, notional, qty, limit_price, state, strategy, reason) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)", oid, mode, coin, side, typ, usd, qty, limit_price, state,
                            strategy, reason)

    async def set_state(self, oid: str, state: str, detail: str = "", filled: Optional[float] = None, avg: Optional[float] = None,
                        exchange_id: Optional[str] = None) -> None:
        async with self.pool.acquire() as c:
            await c.execute("UPDATE rh_orders SET state = $2, detail = coalesce(nullif($3, ''), detail), "
                            "filled_qty = coalesce($4, filled_qty), avg_price = coalesce($5, avg_price), "
                            "exchange_id = coalesce($6, exchange_id), updated_at = now() WHERE id = $1",
                            oid, state, detail, filled, avg, exchange_id)

    async def record_fill(self, oid: str, mode: str, coin: str, side: str, qty: float, price: float, strategy: str) -> None:
        book = self.live if mode == "live" else self.paper
        p = book.pos.setdefault(coin, Position())
        realized = None
        now = self.clock()
        px = self.prices.get(coin) or {}
        feats = {"reason": self._reasons.pop(oid, ""), "mid": px.get("mid"), "bid": px.get("bid"), "ask": px.get("ask"),
                 "spread_pct": round(100 * (px["ask"] - px["bid"]) / px["mid"], 4) if px.get("mid") else None,
                 "paid_vs_mid_pct": round(100 * (price - px["mid"]) / px["mid"], 4) if px.get("mid") else None,
                 "hour_la": dt.datetime.fromtimestamp(now, _la()).hour, "weekday": dt.datetime.fromtimestamp(now, _la()).weekday(),
                 "entry_price": round(p.avg_cost, 8) if side == "sell" else None,
                 "hold_s": round(now - p.opened) if side == "sell" and p.opened else None,
                 "account_value": self.book().value(self.prices) if self.prices else None,
                 "day_pct": self.day_loss_pct()}
        if side == "buy":
            p.buy(qty, price, now)
            if mode == "paper":
                book.cash = round(book.cash - qty * price, 2)
        else:
            realized = round(p.sell(qty, price), 4)
            if mode == "paper":
                book.cash = round(book.cash + qty * price, 2)
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO rh_fills (mode, order_id, coin, side, qty, price, notional, realized_pnl, strategy, features) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)", mode, oid, coin, side, qty, price, round(qty * price, 2),
                            realized, strategy, json.dumps(feats))
        text = (f"{'ENTRY' if side == 'buy' else 'EXIT'} {mode.upper()} · {side.upper()} {qty:g} {coin} at ${price:,.4f} = ${qty * price:,.2f}"
                + ("" if realized is None else f" · P&L {'+' if realized >= 0 else ''}{realized:.2f}") + (f" · {strategy}" if strategy else ""))
        await self.log("trade", text, coin)
        if mode == "live":
            await self.push(f"{coin} {'entry' if side == 'buy' else 'exit'}", text)

    async def kill(self) -> None:
        """Cancel every open Robinhood order this engine placed (never the owner's own), pause, back to paper, clear the kill flag."""
        n = 0
        if rh.configured():
            try:
                async with self.pool.acquire() as c:
                    mine = {r["id"] for r in await c.fetch("SELECT id FROM rh_orders WHERE mode = 'live'")}
                for o in await self.c.open_orders():
                    if str(o.get("client_order_id") or "") not in mine:
                        continue                                 # only the engine's own orders: never one the owner placed by hand
                    try:
                        await self.c.cancel_order(str(o.get("id")))
                        n += 1
                    except Exception as exc:
                        self.last_error = f"kill cancel: {exc}"
            except Exception as exc:
                self.last_error = f"kill: {exc}"
        async with self.pool.acquire() as c:
            await c.execute("UPDATE rh_orders SET state = 'canceled', detail = 'kill switch', updated_at = now() "
                            "WHERE state IN ('pending_approval', 'approved', 'new')")
        self.control.update(mode="paper", paused=True, kill=False)
        await self.kv_put("control", self.control)
        await self.log("warn", f"Kill switch: {n} open Robinhood order(s) cancelled; paused and back on paper.")
        await self.push("Kill switch", f"{n} open order(s) cancelled; Robinhood engine paused and on paper.")

    async def write_status(self, now: float, why: str) -> None:
        book = self.book()
        prices = self.prices
        pos = []
        for coin, p in book.pos.items():
            if p.qty > 0:
                bid = prices.get(coin, {}).get("bid")
                pos.append({"coin": coin, "qty": p.qty, "avg_cost": round(p.avg_cost, 6),
                            "value": round(p.qty * bid, 2) if bid else None,
                            "unrealized": round((bid - p.avg_cost) * p.qty, 2) if bid else None})
        status = {"heartbeat": now, "mode": self.control.get("mode"), "paused": bool(self.control.get("paused")),
                  "strategy": self.control.get("strategy") or "", "approve_each": bool(self.control.get("approve_each", True)),
                  "connected": rh.configured(), "strategies": sorted(STRATEGIES), "why": why,
                  "live_blocked": self.can_go_live(), "cash": round(book.cash, 2), "account_value": book.value(prices) if prices else None,
                  "buying_power": self.account.get("buying_power"), "prices": prices, "positions": pos,
                  "holdings": self.holdings,
                  "exposure_pct": round(100 * sum(book.exposure(prices).values()) / book.value(prices), 1) if prices and book.value(prices) else None,
                  "limits": {"per_order_pct": bot.PER_ORDER_PCT, "max_exposure_pct": bot.MAX_EXPOSURE_PCT,
                             "daily_stop_pct": float(self.control.get("daily_stop_pct") or 0)},
                  "day_pct": self.day_loss_pct(), "stopped_today": self.stopped_today(),
                  "last_error": self.last_error}
        await self.kv_put("status", status)


# ── read side for the Station ────────────────────────────────────────────────────────────────────────────────────────────

async def ensure(pool) -> None:
    async with pool.acquire() as c:
        await c.execute(DDL)


async def state(pool) -> Dict[str, Any]:
    await ensure(pool)
    async with pool.acquire() as c:
        st = await c.fetchval("SELECT value FROM rh_kv WHERE key = 'status'")
        ctl = await c.fetchval("SELECT value FROM rh_kv WHERE key = 'control'")
        orders = await c.fetch("SELECT id, ts, mode, coin, side, type, notional, qty, limit_price, state, filled_qty, avg_price, "
                               "strategy, reason, detail FROM rh_orders ORDER BY ts DESC LIMIT 30")
        events = await c.fetch("SELECT ts, level, text, coin FROM rh_events ORDER BY id DESC LIMIT 30")
        coin_events = await c.fetch("SELECT * FROM (SELECT ts, level, text, coin, row_number() OVER (PARTITION BY coin ORDER BY id DESC) AS rn "
                                    "FROM rh_events WHERE coin IS NOT NULL) x WHERE rn <= 8 ORDER BY ts DESC")
        spread = await c.fetch("SELECT coin, count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY (ask - bid) / mid) AS med, "
                               "percentile_cont(0.9) WITHIN GROUP (ORDER BY (ask - bid) / mid) AS p90, min(ts) AS since "
                               "FROM rh_quotes WHERE ts > now() - interval '7 days' GROUP BY coin ORDER BY coin")
        today = dt.datetime.now(dt.timezone.utc).astimezone(_la()).replace(hour=0, minute=0, second=0, microsecond=0)
        score = await c.fetch("SELECT mode, count(*) FILTER (WHERE side = 'sell') AS exits, "
                              "count(*) FILTER (WHERE side = 'sell' AND realized_pnl > 0) AS wins, "
                              "coalesce(sum(realized_pnl), 0) AS pnl, coalesce(sum(realized_pnl) FILTER (WHERE ts >= $1), 0) AS today "
                              "FROM rh_fills GROUP BY mode", today)
        coin_score = await c.fetch("SELECT mode, coin, count(*) FILTER (WHERE side = 'sell') AS exits, "
                                   "count(*) FILTER (WHERE side = 'sell' AND realized_pnl > 0) AS wins, "
                                   "coalesce(sum(realized_pnl), 0) AS pnl, coalesce(sum(realized_pnl) FILTER (WHERE ts >= $1), 0) AS today, "
                                   "coalesce(sum(notional) FILTER (WHERE side = 'buy'), 0) AS bought, max(ts) AS last "
                                   "FROM rh_fills GROUP BY mode, coin", today)

    def j(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except ValueError:
                return {}
        return v or {}
    return {"status": j(st), "control": _merge_control(j(ctl)),
            "orders": [{**dict(r), "ts": r["ts"].isoformat()} for r in orders],
            "events": [{"ts": r["ts"].isoformat(), "level": r["level"], "text": r["text"], "coin": r["coin"]} for r in events],
            "coins": {cn: {"events": [{"ts": r["ts"].isoformat(), "level": r["level"], "text": r["text"]} for r in coin_events if r["coin"] == cn],
                           "scoreboard": {r["mode"]: {"exits": int(r["exits"]), "wins": int(r["wins"]), "pnl": round(float(r["pnl"]), 2),
                                                      "today": round(float(r["today"]), 2), "bought": round(float(r["bought"]), 2),
                                                      "last": r["last"].isoformat() if r["last"] else None}
                                          for r in coin_score if r["coin"] == cn}} for cn in bot.COINS},
            "spread": [{"coin": r["coin"], "samples": int(r["n"]), "median_pct": round(100 * float(r["med"]), 3),
                        "p90_pct": round(100 * float(r["p90"]), 3), "since": r["since"].isoformat()} for r in spread],
            "scoreboard": {r["mode"]: {"exits": int(r["exits"]), "wins": int(r["wins"]), "pnl": round(float(r["pnl"]), 2),
                                       "today": round(float(r["today"]), 2)} for r in score}}


def _merge_control(saved: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The saved control row over the defaults, the per-coin part merged coin by coin."""
    saved = dict(saved or {})
    ctl = {**DEFAULT_CONTROL, **saved}
    coins = {c: {"paused": False} for c in bot.COINS}
    for c, v in (saved.get("coins") or {}).items():
        if c in coins and isinstance(v, dict):
            coins[c].update(v)
    ctl["coins"] = coins
    return ctl


def _la():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/Los_Angeles")


async def control(pool, action: str, value: Any = None) -> Dict[str, Any]:
    """The owner's taps. Returns the new control row, or raises ValueError with the reason a tap is refused."""
    await ensure(pool)
    async with pool.acquire() as c:
        cur = await c.fetchval("SELECT value FROM rh_kv WHERE key = 'control'")
    ctl = _merge_control(json.loads(cur) if isinstance(cur, str) else (cur or {}))
    if action == "pause":
        ctl["paused"] = True
    elif action == "resume":
        ctl["paused"] = False
    elif action == "paper":
        ctl["mode"] = "paper"
    elif action == "live":
        if not rh.configured():
            raise ValueError("Robinhood is not connected.")
        if not ctl.get("strategy") or ctl["strategy"] not in STRATEGIES:
            raise ValueError("Pick a strategy first: there is none to run live.")
        ctl["mode"] = "live"
    elif action == "kill":
        ctl["kill"] = True
    elif action == "strategy":
        name = str(value or "")
        if name and name not in STRATEGIES:
            raise ValueError(f"No strategy called '{name}'.")
        ctl["strategy"] = name
        if not name:
            ctl["mode"] = "paper"
    elif action == "approve_each":
        ctl["approve_each"] = bool(value)
    elif action == "coin_pause":
        v = value if isinstance(value, dict) else {}
        coin = str(v.get("coin") or "").upper()
        if coin not in bot.COINS:
            raise ValueError(f"No {coin or 'such'} desk.")
        ctl["coins"][coin]["paused"] = bool(v.get("paused"))
    elif action == "daily_stop":
        try:
            pct = float(value)
        except (TypeError, ValueError):
            raise ValueError("The daily stop is a percent, e.g. 6.")
        if not 0 <= pct <= 50:
            raise ValueError("The daily stop must be between 0 (off) and 50%.")
        ctl["daily_stop_pct"] = pct
    else:
        raise ValueError(f"Unknown action '{action}'.")
    async with pool.acquire() as c:
        await c.execute("INSERT INTO rh_kv (key, value, updated_at) VALUES ('control', $1::jsonb, now()) ON CONFLICT (key) DO UPDATE "
                        "SET value = EXCLUDED.value, updated_at = now()", json.dumps(ctl))
        await c.execute("INSERT INTO rh_events (level, text) VALUES ('info', $1)", f"Owner: {action}{'' if value is None else ' ' + str(value)}.")
    return ctl


async def approve(pool, order_id: str, ok: bool) -> str:
    await ensure(pool)
    async with pool.acquire() as c:
        st = await c.fetchval("SELECT state FROM rh_orders WHERE id = $1", order_id)
        if st != "pending_approval":
            raise ValueError("That order is not waiting for approval (it may have lapsed).")
        new = "approved" if ok else "rejected"
        await c.execute("UPDATE rh_orders SET state = $2, detail = $3, updated_at = now() WHERE id = $1", order_id, new,
                        "approved by the owner" if ok else "rejected by the owner")
    return new


async def export_orders(pool) -> str:
    import csv
    import io

    await ensure(pool)
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT o.ts, o.mode, o.coin, o.side, o.type, o.notional, o.qty, o.limit_price, o.state, o.filled_qty, "
                             "o.avg_price, o.strategy, o.reason, o.detail, coalesce(sum(f.realized_pnl), 0) AS realized_pnl "
                             "FROM rh_orders o LEFT JOIN rh_fills f ON f.order_id = o.id GROUP BY o.id ORDER BY o.ts")
    buf = io.StringIO()
    wr = csv.writer(buf)
    cols = ["time_utc", "mode", "coin", "side", "type", "dollars", "qty", "limit_price", "state", "filled_qty", "avg_price",
            "strategy", "reason", "detail", "realized_pnl"]
    wr.writerow(cols)
    for r in rows:
        wr.writerow([r["ts"].isoformat()] + [r[k] for k in ("mode", "coin", "side", "type", "notional", "qty", "limit_price", "state",
                                                               "filled_qty", "avg_price", "strategy", "reason", "detail", "realized_pnl")])
    return buf.getvalue()


async def export_quotes(pool) -> str:
    """Every saved quote (one a minute per coin): bid and ask include Robinhood's spread; spread_pct is the full buy-sell gap."""
    import csv
    import io

    await ensure(pool)
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT ts, coin, bid, ask, mid FROM rh_quotes ORDER BY ts")
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["time_utc", "coin", "bid", "ask", "mid", "spread_pct"])
    for r in rows:
        wr.writerow([r["ts"].isoformat(), r["coin"], r["bid"], r["ask"], r["mid"],
                     round(100 * (r["ask"] - r["bid"]) / r["mid"], 4) if r["mid"] else ""])
    return buf.getvalue()


async def export_trades(pool, coin: str = "") -> str:
    """Every Robinhood fill, all four desks combined (or one), with what the engine saw: one row per fill, a desk column."""
    import csv
    import io

    await ensure(pool)
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT ts, mode, coin, side, qty, price, notional, realized_pnl, strategy, features, order_id "
                             "FROM rh_fills WHERE ($1 = '' OR coin = $1) ORDER BY ts", coin.upper())
    cols = ["reason", "mid", "bid", "ask", "spread_pct", "paid_vs_mid_pct", "hour_la", "weekday", "entry_price", "hold_s",
            "account_value", "day_pct"]
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["time_utc", "desk", "mode", "side", "qty", "price", "dollars", "realized_pnl", "strategy", *cols, "order_id"])
    for r in rows:
        f = r["features"]
        f = json.loads(f) if isinstance(f, str) else (f or {})
        wr.writerow([r["ts"].isoformat(), f"{r['coin']} (Robinhood)", r["mode"], r["side"], r["qty"], r["price"], r["notional"],
                     r["realized_pnl"], r["strategy"], *[f.get(k) for k in cols], r["order_id"]])
    return buf.getvalue()


async def export_log(pool, coin: str = "") -> str:
    """The Robinhood desk log, all desks combined (or one): time, desk (blank for engine-wide lines), level, text."""
    import csv
    import io

    await ensure(pool)
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT ts, coin, level, text FROM rh_events WHERE ($1 = '' OR coin = $1) ORDER BY id", coin.upper())
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["time_utc", "desk", "level", "text"])
    for r in rows:
        wr.writerow([r["ts"].isoformat(), r["coin"] or "ENGINE", r["level"], r["text"]])
    return buf.getvalue()


async def daily_rows(pool) -> list:
    """(Los Angeles date, mode, coin, realized P&L, exits) for the daily P&L workbook's Robinhood sheet."""
    await ensure(pool)
    async with pool.acquire() as c:
        return await c.fetch("SELECT (ts AT TIME ZONE 'America/Los_Angeles')::date AS day, mode, coin, coalesce(sum(realized_pnl), 0) AS pnl, "
                             "count(*) FILTER (WHERE side = 'sell') AS exits FROM rh_fills GROUP BY 1, 2, 3 ORDER BY 1 DESC")
