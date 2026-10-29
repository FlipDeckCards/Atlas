"""The desk's loop: once a second it reads the index and the book, books any fills, settles finished windows, checks the risk rules
and sets its resting quotes. Everything here is plain code; nothing is decided by a model."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from dataclasses import replace

from openjarvis.polybot import brain, model, risk, strategy
from openjarvis.polybot.brokers import BookView, Fill, LiveBroker, PaperBroker, parse_ts
from openjarvis.polybot.client import PMError, money
from openjarvis.polybot.config import Config, apply_control
from openjarvis.polybot.state import Ledger

logger = logging.getLogger("polybot.desk")
VERSION = "1.0"


@dataclass
class Window:
    slug: str
    start: float
    end: float
    price_to_beat: Optional[float]
    tick: float = 0.01
    min_qty: float = 0.01
    title: str = ""
    inv: strategy.Inventory = field(default_factory=strategy.Inventory)
    mode: str = "paper"
    settled: bool = False
    fills: Dict[str, int] = field(default_factory=dict)       # fills per side in this window (v1.1: one bite per side)


def window_from_market(m: Dict[str, Any]) -> Optional[Window]:
    apt = m.get("assetPriceTerms") or {}
    start, end = parse_ts(apt.get("windowStart")), parse_ts(apt.get("windowEnd"))
    if not (m.get("slug") and start and end):
        return None
    return Window(slug=m["slug"], start=start, end=end, price_to_beat=money(apt.get("priceToBeat")),
                  tick=float(m.get("orderPriceMinTickSize") or 0.01), min_qty=float(m.get("minimumTradeQty") or 0.01),
                  title=str(m.get("title") or m.get("question") or ""))


def is_series(m: Dict[str, Any], asset: str, horizon: str) -> bool:
    apt = m.get("assetPriceTerms") or {}
    sym = str(((apt.get("asset") or {}).get("symbol")) or "").lower()
    return (apt.get("marketType") == "ASSET_PRICE_MARKET_TYPE_UP_DOWN" and sym == asset and str(apt.get("horizon") or "") == horizon)


class Desk:
    def __init__(self, base: Config, client, store, *, notify: Optional[Callable[[str, str], Awaitable[None]]] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.base, self.cfg = base, base
        self.c, self.store, self.notify_fn = client, store, notify
        self.clock, self.sleep = clock, sleep
        self.tape = model.PriceTape()
        self.rs = risk.RiskState()
        self.ledger = Ledger()
        self.broker = PaperBroker()
        self.window: Optional[Window] = None
        self.open_windows: Dict[str, Window] = {}            # windows holding contracts, not settled yet
        self.recent: Dict[str, Window] = {}                  # the last few windows seen (a late fill on one still finds it)
        self.book: Optional[BookView] = None
        self.fair: Optional[float] = None                    # the fair value traded on (v1.1: model blended with the market)
        self.fair_model: Optional[float] = None
        self.mid: Optional[float] = None
        self.momentum: float = 0.0
        self.learner = brain.Learner()
        self.sigma: Optional[float] = None
        self.reason: str = "Starting."
        self.balance: Optional[float] = None
        self.external_at_risk = 0.0
        self.stats = {"day": "", "fills": 0, "windows": 0, "wins": 0}
        self.hour_pnl = 0.0
        self._t = {"control": 0.0, "flush": 0.0, "markets": 0.0, "markets_fast": 0.0, "market": 0.0, "sigma": 0.0, "positions": 0.0, "hour": 0.0}
        self._stopping = False
        self.instance = ""                                   # set by the runner: which copy wrote the status
        self.version_start: Optional[float] = None           # real account value (live) when this strategy version started

    # ── helpers ───────────────────────────────────────────────────────────────────────
    def log(self, level: str, text: str) -> None:
        (logger.warning if level in ("warn", "error") else logger.info)(text)
        self.ledger.event(level, text, self.clock())

    async def push(self, title: str, text: str) -> None:
        if self.notify_fn is not None:
            try:
                await self.notify_fn(title, text)
            except Exception:
                logger.warning("push failed")

    def _due(self, name: str, every: float, now: float) -> bool:
        if now - self._t[name] >= every:
            self._t[name] = now
            return True
        return False

    # ── control: mode, pause, kill and limits from Aibussol ───────────────────────────
    async def read_control(self, now: float) -> None:
        try:
            ctrl = await self.store.get("control")
        except Exception:
            logger.exception("control row unreadable; keeping the last settings")
            return
        new = apply_control(self.base, ctrl)
        self.rs.paused, self.rs.kill = bool(ctrl.get("paused")), bool(ctrl.get("kill"))
        if str(ctrl.get("mode") or "paper") == "live" and new.mode != "live":
            self.reason = "Live was approved, but the worker is not allowed to trade live (set POLYBOT_ALLOW_LIVE=true and the API keys)."
        if new.mode != self.cfg.mode:
            await self.broker.cancel_all(now)
            self.broker = LiveBroker(self.c) if new.mode == "live" else PaperBroker()
            if new.mode == "live":
                try:
                    await self.c.cancel_all()                       # nothing left over from an earlier run
                except PMError as exc:
                    self.log("warn", f"Could not clear old orders: {exc}")
            self.log("info", f"Mode is now {new.mode.upper()}.")
            self.rs.day_start = None                                  # a new mode starts its own day from its own balance
            self.rs.day_pnl = 0.0
            await self.push("Polymarket Desk", f"Mode switched to {new.mode.upper()}. Bankroll ${new.limits.bankroll_usd:.2f}, "
                                               f"daily stop at {new.limits.daily_loss_pct:g}% of the day's starting balance.")
        if new.limits != self.cfg.limits:
            self.log("info", f"Limits now: bankroll ${new.limits.bankroll_usd:.2f}, per window ${new.limits.max_window_usd:.2f}, "
                             f"per order ${new.limits.max_order_usd:.2f}, daily loss ${new.limits.daily_loss_usd:.2f}, min edge {new.limits.min_edge:.2f}.")
        if (self.rs.kill or self.rs.paused) and (self.broker.resting):
            await self.broker.cancel_all(now)
        self.cfg = new

    # ── data ──────────────────────────────────────────────────────────────────────────
    async def load_index(self, now: float, backfill: bool = False) -> None:
        start = int(now) - (3599 if backfill else 20)
        self.tape.extend(await self.c.index_prices(self.base.asset, start, 0))

    async def find_window(self, now: float) -> None:
        w = self.window
        if w and w.start <= now < w.end:
            if w.price_to_beat is None and self._due("market", 3.0, now):
                fresh = window_from_market(await self.c.market(w.slug))
                if fresh and fresh.price_to_beat:
                    w.price_to_beat = fresh.price_to_beat
            if w.price_to_beat is None and now - w.start > 30:
                k = self.tape.average(int(w.start) - 60, int(w.start) - 1)      # the same statistic, from the index itself
                if k:
                    w.price_to_beat = round(k, 2)
            return
        # the window just ended: look for the next one every 2 seconds instead of waiting 10, and stop using the old one
        ended = w is not None and now >= w.end
        if ended:
            self.window = w = None
        if not self._due("markets", 2.0 if (ended or w is None) else 10.0, now) and w is not None:
            return
        if w is None and not ended and not self._due("markets_fast", 2.0, now):
            return
        ms = await self.c.markets(categories=["crypto"], active=True, closed=False)
        live = [x for x in (window_from_market(m) for m in ms if is_series(m, self.base.asset, self.base.horizon)) if x and x.start <= now < x.end]
        nxt = min(live, key=lambda x: x.end) if live else None
        if nxt and nxt.slug in self.recent:
            nxt = self.recent[nxt.slug]
        self.window = nxt
        if nxt:
            nxt.mode = self.cfg.mode
            self.recent[nxt.slug] = nxt
            for old in sorted(self.recent, key=lambda k: self.recent[k].end)[:-6]:
                self.recent.pop(old, None)

    async def load_book(self, now: float) -> None:
        if not self.window:
            self.book = None
            return
        try:
            self.book = BookView.parse(await self.c.book(self.window.slug), now)
        except PMError as exc:
            if exc.status == 404:                       # the market closed and is gone: not an error, move on to the next window
                self.window, self.book = None, None
                return
            raise

    # ── fills and settlement ──────────────────────────────────────────────────────────
    def _book_fill(self, f: Fill) -> None:
        w = self.open_windows.get(f.slug) or self.recent.get(f.slug)
        if w is None:
            self.log("warn", f"A fill on {f.slug} could not be matched to a window; it is not counted.")
            return
        w.mode = self.cfg.mode
        self.open_windows[w.slug] = w
        w.fills[f.side] = w.fills.get(f.side, 0) + 1
        if f.side == "up":
            w.inv.up_qty += f.qty
            w.inv.up_cost += f.cost
        else:
            w.inv.down_qty += f.qty
            w.inv.down_cost += f.cost
        b = self.book
        cost_per = f.price if f.side == "up" else 1.0 - f.price
        fair_side = None if self.fair is None else (self.fair if f.side == "up" else 1.0 - self.fair)
        self.ledger.fill(self.cfg.mode, f, self.fair, {
            # what the desk saw at the moment of the fill, so results can be cut by condition later
            "model": None if self.fair_model is None else round(self.fair_model, 4),
            "mid": None if self.mid is None else round(self.mid, 4),
            "momentum_z": round(self.momentum, 2),
            "secs_left": round(w.end - f.ts, 1),
            "edge": None if fair_side is None else round(fair_side - cost_per, 4),          # fair value of the side bought minus its cost
            "model_vs_market": None if self.fair_model is None or self.mid is None else round(self.fair_model - self.mid, 4),
            "spread": None if not b or b.best_bid is None or b.best_ask is None else round(b.best_ask - b.best_bid, 4),
            "extra_edge": self.learner.extra_edge if self.cfg.learn else 0.0,
            "sigma": None if self.sigma is None else round(self.sigma, 3),
        })
        self.stats["fills"] += 1

    async def on_fills(self, fills: List[Fill]) -> None:
        for f in fills:
            self._book_fill(f)
            w = self.open_windows.get(f.slug)
            side = "UP (call-like: pays if BTC closes at/above)" if f.side == "up" else "DOWN (put-like: pays if BTC closes below)"
            pay = f.price if f.side == "up" else 1 - f.price
            k = f"${w.price_to_beat:,.2f}" if w and w.price_to_beat else "pending"
            end = time.strftime("%H:%M:%S UTC", time.gmtime(w.end)) if w else "?"
            text = (f"ENTRY {self.cfg.mode.upper()} · BTC {self.base.horizon} Up/Down · {side} · strike (price to beat) {k} · "
                    f"expires {end} · {f.qty:g} contracts at ${pay:.2f} = ${f.cost:.2f} · stop: none, held to settlement (max loss ${f.cost:.2f}) · "
                    f"target: $1.00 per contract (${f.qty:.2f}) · fair value then {self.fair if self.fair is not None else 0:.2f}")
            self.log("trade", text)
            if self.cfg.mode == "live":
                await self.push("Desk entry", text)

    async def settle_due(self, now: float) -> None:
        for slug, w in list(self.open_windows.items()):
            if now < w.end + 3 or not w.price_to_beat:
                continue
            res = model.settle(self.tape, int(w.end), w.price_to_beat)
            if res is None:
                if now > w.end + 120:                                            # index gap: ask the exchange
                    s = await self.c.settlement(slug)
                    if s is None:
                        continue
                    res = (None, s >= 0.5)
                else:
                    continue
            close, up = res
            payout = w.inv.up_qty if up else w.inv.down_qty
            pnl = round(payout - w.inv.spent, 4)
            w.settled = True
            del self.open_windows[slug]
            self.rs.roll(now)
            self.rs.day_pnl += pnl
            self.hour_pnl += pnl
            self.stats["windows"] += 1
            self.stats["wins"] += 1 if pnl > 0 else 0
            if w.mode == self.cfg.mode:
                self.learner.add(pnl, w.inv.spent)
            self.ledger.window({"slug": slug, "mode": w.mode, "window_start": w.start, "window_end": w.end, "price_to_beat": w.price_to_beat,
                                "close_price": close, "up": up, "up_qty": w.inv.up_qty, "up_cost": round(w.inv.up_cost, 4),
                                "down_qty": w.inv.down_qty, "down_cost": round(w.inv.down_cost, 4), "pnl": pnl, "settled_at": now})
            text = (f"EXIT (settled) {w.mode.upper()} · {slug} · BTC {'UP' if up else 'DOWN'} (open ${w.price_to_beat:,.2f}"
                    + (f", close ${close:,.2f}" if close else "") + f") · held Up {w.inv.up_qty:g} / Down {w.inv.down_qty:g}, paid ${w.inv.spent:.2f}, "
                    f"got ${payout:.2f} · P&L {'+' if pnl >= 0 else ''}{pnl:.2f} · today {'+' if self.rs.day_pnl >= 0 else ''}{self.rs.day_pnl:.2f}")
            self.log("trade", text)
            if w.mode == "live":
                await self.push("Desk settled", text)
            dl = risk.day_limit(self.cfg, self.rs)
            if dl > 0 and self.rs.day_pnl <= -dl and self.rs.day_pnl - pnl > -dl:          # only the moment it is crossed
                if self.cfg.mode == "live":
                    msg = f"Daily loss limit hit (${-self.rs.day_pnl:.2f} of ${dl:.2f}). The desk stops quoting until midnight Los Angeles time."
                    self.log("warn", msg)
                    await self.push("Desk stopped for today", msg)
                else:
                    self.log("info", f"Paper: the daily stop (${dl:.2f}) would have ended a live day here; paper keeps trading to collect data.")

    async def refresh_positions(self, now: float) -> None:
        """Live only: contracts held from an earlier run (or bought by hand) still count against the bankroll until they expire."""
        if self.cfg.mode != "live" or not self._due("positions", 300.0, now):
            return
        try:
            pos = await self.c.positions()
        except PMError as exc:
            self.log("warn", f"Positions unreadable: {exc}")
            return
        ext = 0.0
        for slug, p in (pos or {}).items():
            if slug in self.open_windows or p.get("expired"):
                continue
            if f"-{self.base.asset}-updown-" in slug:
                ext += abs(money(p.get("cost")) or 0.0)
        self.external_at_risk = round(ext, 2)

    # ── one second of work ────────────────────────────────────────────────────────────
    async def step(self) -> None:
        now = self.clock()
        self.rs.roll(now)
        if self.stats["day"] != self.rs.day:
            self.stats = {"day": self.rs.day, "fills": 0, "windows": 0, "wins": 0}
        if self._due("control", self.base.control_s, now):
            await self.read_control(now)
        await self.load_index(now)
        if self._due("sigma", 30.0, now) or self.sigma is None:
            self.sigma = self.tape.sigma_per_s(int(now))
        await self.find_window(now)
        await self.load_book(now)
        await self.on_fills(await self.broker.sync(self.book, now))
        await self.settle_due(now)
        await self.refresh_positions(now)
        if self.cfg.mode == "live":
            self.balance = await self.broker.balance()

        self.rs.at_risk = round(sum(w.inv.spent for w in self.open_windows.values()) + self.external_at_risk, 4)
        if self.version_start is None and self.cfg.mode == "live" and self.balance is not None:
            self.version_start = round(self.balance + self.rs.at_risk, 2)
            self.log("info", f"Strategy v{brain.STRATEGY_VERSION} starts with a real account value of ${self.version_start:.2f}.")
        if self.rs.day_start is None:
            if self.cfg.mode == "live":
                if self.balance is not None:
                    self.rs.day_start = round(self.balance + self.rs.at_risk, 2)
                    self.log("info", f"Day starts with ${self.rs.day_start:.2f}; today's loss limit is ${risk.day_limit(self.cfg, self.rs):.2f}.")
            else:
                self.rs.day_start = self.cfg.limits.bankroll_usd
        self.rs.resting = round(self.broker.resting_cost, 4)
        w, b = self.window, self.book
        last = self.tape.last()
        data_age = now - last[0] - 1 if last else 999.0
        if self.tape.moved(int(now), self.base.jump_window_s) >= self.base.jump_usd:
            if now >= self.rs.cooldown_until:
                self.log("info", f"BTC moved ${self.tape.moved(int(now), self.base.jump_window_s):.0f} in {self.base.jump_window_s:.0f}s: quotes pulled.")
            self.rs.cooldown_until = now + self.base.cooldown_s
        self.fair_model = (model.fair_up(self.tape, int(now), int(w.end), w.price_to_beat, self.sigma or 0.0, self.cfg.vol_mult,
                                         drift_beta=self.cfg.drift_beta, drift_lookback_s=int(self.cfg.momentum_s))
                           if w and w.price_to_beat else None)
        self.mid = brain.market_mid(b.best_bid, b.best_ask) if b else None
        self.fair = brain.blend(self.fair_model, self.mid, self.cfg.model_weight)
        self.momentum = brain.momentum_z(self.tape, int(now), int(self.cfg.momentum_s), self.sigma)
        if w is None and not (self.rs.kill or self.rs.paused):
            reason = "Between windows: waiting for the next 5-minute market to open."
        else:
            reason = None
        reason = reason or risk.blocked(self.cfg, self.rs, now=now, data_age_s=data_age, book_age_s=(now - b.ts) if b else 999.0,
                              market_open=bool(b and b.state == "MARKET_STATE_OPEN"), secs_since_open=(now - w.start) if w else 0.0,
                              secs_to_end=(w.end - now) if w else 0.0, balance=self.balance)
        if reason is None and not w.price_to_beat:
            reason = "Waiting for the price to beat."
        if reason is None and self.fair is None:
            reason = "Not enough index history yet to price the window."
        want: List[strategy.Quote] = []
        if reason is None:
            inv = self.open_windows[w.slug].inv if w.slug in self.open_windows else w.inv
            budget = risk.budget_left(self.cfg, self.rs, self.balance) + self.rs.resting      # its own resting orders do not block themselves
            can_up, can_down, why = brain.side_filters(self.momentum, self.cfg.momentum_z)
            fills = (self.open_windows.get(w.slug) or w).fills
            can_up = can_up and brain.can_add(fills, "up", self.cfg.max_fills_per_side)
            can_down = can_down and brain.can_add(fills, "down", self.cfg.max_fills_per_side)
            extra = self.learner.extra_edge if self.cfg.learn else 0.0
            lim = replace(self.cfg.limits, min_edge=round(self.cfg.limits.min_edge + extra, 3))
            equity = self.equity()
            sizing = strategy.Sizing(equity=equity,
                                     kelly_fraction=self.cfg.kelly_fraction, window_pct=self.cfg.window_pct, min_order_usd=self.cfg.min_order_usd)
            want = strategy.quotes(self.fair, strategy.Book(b.best_bid, b.best_ask, w.tick, w.min_qty), inv, lim, budget_left=budget,
                                   can_up=can_up, can_down=can_down, sizing=sizing,
                                   min_cost=self.cfg.min_buy_price, max_cost=self.cfg.max_buy_price) if (can_up or can_down) else []
            if want:
                reason = "Quoting" + (f" (one side only: {why})" if why else ".")
            elif why and not (can_up or can_down):
                reason = why
            elif not (can_up or can_down):
                reason = "Already filled on both sides this window; holding to settlement."
            else:
                reason = (why + " " if why else "") + f"No price is {lim.min_edge:.2f} better than fair value right now."
        if w:
            await self.broker.set_quotes(w.slug, want, now)
        elif self.broker.resting:
            await self.broker.cancel_all(now)
        self.reason = reason
        self.rs.errors_in_row = 0

    def equity(self) -> float:
        """The account's value right now, for sizing: live = buying power + what is in open windows; paper = the simulated
        starting balance plus today's simulated P&L."""
        if self.cfg.mode == "live" and self.balance is not None:
            return round(max(0.0, self.balance + self.rs.at_risk), 2)
        start = self.rs.day_start if self.rs.day_start is not None else self.cfg.limits.bankroll_usd
        return round(max(0.0, start + self.rs.day_pnl), 2)

    def status(self, now: float) -> Dict[str, Any]:
        w, b = self.window, self.book
        last = self.tape.last()
        return {
            "version": VERSION, "heartbeat": now, "instance": self.instance, "stopped": self._stopping, "mode": self.cfg.mode, "allow_live": self.base.allow_live, "has_keys": bool(self.base.key_id and self.base.secret_key),
            "series": f"{self.base.asset.upper()} {self.base.horizon} Up/Down", "reason": self.reason,
            "paused": self.rs.paused, "kill": self.rs.kill, "limits": self.cfg.limits.__dict__,
            "index": {"price": last[1] if last else None, "ts": last[0] if last else None},
            "window": ({"slug": w.slug, "start": w.start, "end": w.end, "price_to_beat": w.price_to_beat, "title": w.title} if w else None),
            "fair_up": round(self.fair, 4) if self.fair is not None else None, "strategy": brain.STRATEGY_VERSION,
            "fair_model": round(self.fair_model, 4) if self.fair_model is not None else None,
            "market_mid": round(self.mid, 4) if self.mid is not None else None, "momentum_z": round(self.momentum, 2),
            "extra_edge": self.learner.extra_edge if self.cfg.learn else 0.0, "learner": self.learner.dump(),
            "rolling_return": round(self.learner.rolling_return, 4) if self.learner.rolling_return is not None else None,
            "book": ({"bid": b.best_bid, "ask": b.best_ask, "state": b.state} if b else None),
            "resting": [{"side": r.quote.side, "price": r.quote.price, "qty": r.left} for r in self.broker.resting.values()],
            "rejects": list(getattr(self.broker, "rejects", []))[-5:],
            "equity": self.equity(), "window_pct": self.cfg.window_pct, "version_start": self.version_start,
            "account_value": round(self.balance + self.rs.at_risk, 2) if self.cfg.mode == "live" and self.balance is not None else None,
            "window_cap": strategy.Sizing(equity=self.equity(), window_pct=self.cfg.window_pct).window_cap(self.cfg.limits),
            "balance": self.balance, "day": self.rs.day, "day_pnl": round(self.rs.day_pnl, 2),
            "day_start": self.rs.day_start, "day_limit": risk.day_limit(self.cfg, self.rs), "at_risk": round(self.rs.at_risk, 2),
            "today": dict(self.stats),
            "open_windows": [{"slug": x.slug, "end": x.end, "up_qty": x.inv.up_qty, "down_qty": x.inv.down_qty, "paid": round(x.inv.spent, 2)}
                             for x in self.open_windows.values()],
        }

    async def flush(self, now: float) -> None:
        try:
            await self.store.flush(self.ledger)
            await self.store.put("status", self.status(now))
        except Exception:
            logger.exception("could not write the desk's state; will retry")

    async def hourly(self, now: float) -> None:
        if not self._due("hour", 3600.0, now):
            return
        if self._t["hour"] and self.cfg.mode == "paper" and self.stats["windows"]:
            await self.push("Desk (paper) hourly", f"Paper desk today: {self.stats['windows']} windows settled, {self.stats['wins']} won, "
                                                    f"{self.stats['fills']} fills, P&L {self.rs.day_pnl:+.2f} (simulated). Last hour {self.hour_pnl:+.2f}.")
        self.hour_pnl = 0.0

    # ── run forever ───────────────────────────────────────────────────────────────────
    async def run(self, max_steps: Optional[int] = None) -> None:
        now = self.clock()
        self._t["hour"] = now
        await self.read_control(now)
        self._t["control"] = now
        try:
            prev = await self.store.get("status")
        except Exception:
            prev = {}
        self.rs.roll(now)
        if prev.get("day") == self.rs.day and prev.get("mode") == self.cfg.mode:
            self.rs.day_pnl = float(prev.get("day_pnl") or 0.0)
            if prev.get("day_start") is not None:
                self.rs.day_start = float(prev["day_start"])
        if prev.get("strategy") == brain.STRATEGY_VERSION and prev.get("mode") == self.cfg.mode:
            self.learner.load(prev.get("learner"))
        if prev.get("strategy") == brain.STRATEGY_VERSION and prev.get("version_start") is not None:
            self.version_start = float(prev["version_start"])
        await self.load_index(now, backfill=True)
        self.log("info", f"Desk started in {self.cfg.mode.upper()} mode on {self.base.asset.upper()} {self.base.horizon} Up/Down.")
        steps = 0
        try:
            while not self._stopping and (max_steps is None or steps < max_steps):
                t0 = self.clock()
                try:
                    await self.step()
                except PMError as exc:
                    self.rs.errors_in_row += 1
                    self.reason = f"API error: {exc}"
                    logger.warning("step: %s", exc)
                    if self.rs.errors_in_row == 5:
                        self.log("error", f"Five API errors in a row; quotes pulled. Last: {exc}")
                        await self.broker.cancel_all(t0)
                        await self.push("Desk problem", f"Five API errors in a row; quotes pulled. Last: {exc}")
                except Exception as exc:
                    self.rs.errors_in_row += 1
                    self.reason = "Internal error; see logs."
                    logger.exception("step failed")
                    if self.rs.errors_in_row == 5:
                        await self.broker.cancel_all(t0)
                        await self.push("Desk problem", f"Internal error, quotes pulled: {exc}")
                if self._due("flush", self.base.flush_s, t0):
                    await self.flush(t0)
                await self.hourly(t0)
                steps += 1
                await self.sleep(max(0.05, self.base.tick_s - (self.clock() - t0)))
        finally:
            try:
                await self.broker.cancel_all(self.clock())
            except Exception:
                logger.exception("final cancel failed")
            self.log("info", "Desk stopped; resting orders cancelled.")
            await self.flush(self.clock())

    def stop(self) -> None:
        self._stopping = True
