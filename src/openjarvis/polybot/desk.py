"""The desk's loop: once a second it reads the index and the book, books any fills, settles finished windows, checks the risk rules
and sets its resting quotes. Everything here is plain code; nothing is decided by a model."""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from dataclasses import replace

from openjarvis.polybot import brain, directional, exposure, model, research, risk, setups, shadow, strategy
from openjarvis.polybot.config import SHARED_MARKET, is_live_label
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
    unpaired_since: Optional[float] = None                    # v1.12: when this window last became one-sided


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
    h = str(apt.get("horizon") or "").lower().replace("horizon_", "")
    same = h == horizon or (horizon == "1h" and h in ("1h", "60m", "1hr", "hourly", "hour"))
    return apt.get("marketType") == "ASSET_PRICE_MARKET_TYPE_UP_DOWN" and sym == asset and same


# v1.37: how much of the live correction is applied (1.0 = full, 0.5 = half).
CORRECTION_STRENGTH = 0.5


class Desk:
    def __init__(self, base: Config, client, store, *, notify: Optional[Callable[[str, str], Awaitable[None]]] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.base, self.cfg = base, base
        self.c, self.store, self.notify_fn = client, store, notify
        self.clock, self.sleep = clock, sleep
        self.tape = model.PriceTape()
        self.rs = risk.RiskState()
        from openjarvis.polybot.state import event_asset
        self.ledger = Ledger(event_asset(base.asset, base.account))
        from openjarvis.polybot.config import desk_label
        self.name = "BTC" if base.asset == "btc" else desk_label(base.asset)   # BTC 5m keeps its name in the log
        from openjarvis.polybot.config import HORIZON_S
        self._prefixes: Dict[str, str] = {}                  # learned market address pattern per window length
        self.broker = PaperBroker()
        self.window: Optional[Window] = None
        self.open_windows: Dict[str, Window] = {}            # windows holding contracts, not settled yet
        self.recent: Dict[str, Window] = {}                  # the last few windows seen (a late fill on one still finds it)
        self.book: Optional[BookView] = None
        self.fair: Optional[float] = None                    # the fair value traded on (v1.1: model blended with the market)
        self.fair_model: Optional[float] = None
        self.mid: Optional[float] = None
        self.momentum: float = 0.0
        self.trend: float = 0.0
        self._missing = ""
        self._lookup_fail = ""
        self._event_seen = ""
        self._book404 = ""
        self._day_reset_seen: Optional[str] = None          # the owner's last "reset daily stop" this desk has applied
        self._ready = False
        self._sweeps: set = set()                            # (window, side) a closing sweep is buying
        self.learner = brain.Learner()
        self._dir_pending: set = set()                       # v1.18: windows where a directional trade was placed
        self._bets: Dict[str, Dict[str, Tuple[str, float, float]]] = {}   # v1.22: window -> {method: (side, usd, qty)}, one bet per method
        self._fill_conf: Dict[str, List[Tuple[str, Optional[float], Optional[float]]]] = {}   # per window: (side, model conf, book conf)
        # live calibration: a side's raw model confidence -> the win rate its bucket has actually shown (shrunk toward the raw value)
        self._calib = lambda p: self._calibrated(p)
        self._entry_sig: Dict[str, Tuple[Any, ...]] = {}   # per window: (sigma, model conf, calibrated conf, calibrated lot) at the first entry
        self.shadow = shadow.Shadow(window_secs=900 if str(self.cfg.asset).endswith("15") else 300)   # six hidden shadow strategies, three per timeframe (recorded, never traded or shown)
        self.setups = setups.SetupBook()                     # v1.14 learned pair sizing (shadow): record per pair setup
        self._cand_seen: set = set()                              # v1.38: candidates already queued for the log (window, side, reason)
        self._pair_fills: Dict[str, List[Dict[str, Any]]] = {}   # non-sweep fills per open window, to file the pair under its setup
        self.sigma: Optional[float] = None
        self.reason: str = "Starting."
        self.balance: Optional[float] = None
        self.external_at_risk = 0.0
        self.stats = {"day": "", "fills": 0, "windows": 0, "wins": 0}
        self.hour_pnl = 0.0
        self._t = {"markets_scan": 0.0, "control": 0.0, "flush": 0.0, "markets": 0.0, "markets_fast": 0.0, "market": 0.0, "sigma": 0.0, "positions": 0.0, "hour": 0.0}
        self._stopping = False
        self.instance = ""                                   # set by the runner: which copy wrote the status
        self.version_start: Optional[float] = None           # real account value (live) when this strategy version started
        self.deposits_start: Optional[float] = None         # v1.21: owner's total deposits when this version started (to net them out)
        self.guard: Optional[str] = None                     # v1.8: set when Polymarket holds more than the desk recorded; blocks trading
        self._mismatch: Dict[str, int] = {}                  # consecutive mismatched checks per window (a fill may be mid-sync once)

    # ── helpers ───────────────────────────────────────────────────────────────────────
    def log(self, level: str, text: str) -> None:
        (logger.warning if level in ("warn", "error") else logger.info)(text)
        self.ledger.event(level, text, self.clock())

    async def push(self, title: str, text: str) -> None:
        if self.notify_fn is not None:
            try:
                await self.notify_fn(f"{self.name} · {title}", text)
            except Exception:
                logger.warning("push failed")

    def _due(self, name: str, every: float, now: float) -> bool:
        if now - self._t.get(name, 0.0) >= every:
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
        was_paused, was_kill = self.rs.paused, self.rs.kill
        self.rs.paused, self.rs.kill = bool(ctrl.get("paused")), bool(ctrl.get("kill"))
        rt = ctrl.get("day_reset_at")
        if self._ready and rt and rt != self._day_reset_seen:          # the owner reset the daily stop: today starts again from now
            self._day_reset_seen = rt
            self.rs.day_start, self.rs.day_pnl, self.rs.real_pnl = None, 0.0, None
            self.log("info", "Daily stop reset by the owner: today's loss limit now counts from the current account value.")
        if (was_paused, was_kill) != (self.rs.paused, self.rs.kill):   # a pause/resume tap: report it on the next tick, not a minute later
            self._t["flush"] = 0.0
            self.log("info", f"{self.name} {'paused' if self.rs.paused or self.rs.kill else 'resumed'} from Aibussol.")
        if self.guard and was_paused and not self.rs.paused:          # the owner looked, paused and resumed: trade again
            self.log("info", "Resumed by the owner after the position check; trading again.")
            self.guard = None
        if str(ctrl.get("mode") or "paper") == "live" and new.mode != "live":
            self.reason = "Live was approved, but the worker is not allowed to trade live (set POLYBOT_ALLOW_LIVE=true and the API keys)."
        if new.mode != self.cfg.mode:
            await self.broker.cancel_all(now)
            self.broker = LiveBroker(self.c) if new.mode == "live" else PaperBroker()
            if new.mode == "live":
                try:
                    await self.cancel_own_orders()                  # nothing left over from an earlier run (this desk's market only)
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
        self.tape.extend(await self.c.index_prices(self.base.coin, start, 0))

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
                    w.price_to_beat = round(k, 6)                               # v1.14: full precision (XRP is ~$1.23)
            return
        # the window just ended: look for the next one every 2 seconds instead of waiting 10, and stop using the old one
        ended = w is not None and now >= w.end
        if ended:
            self.window = w = None
        if not self._due("markets", 2.0 if (ended or w is None) else 10.0, now) and w is not None:
            return
        if w is None and not ended and not self._due("markets_fast", 2.0, now):
            return
        nxt = await self.window_by_slug(now)
        if nxt is None and self._due("markets_scan", 10.0, now):       # the slow full scan: at most every 10 s (three desks hammering it
            ms = await self.c.markets(categories=["crypto"], active=True, closed=False)
            if getattr(self, "_scan_sampled", None) != int(now // self.span):      # diagnostic: once per window, what the market list holds
                self._scan_sampled = int(now // self.span)
                names = [str(m.get("slug") or m.get("marketSlug") or "?") for m in ms[:12]]
                self.log("warn", f"Market list sample ({len(ms)} crypto markets): {names}; first record keys: {sorted(ms[0])[:25] if ms else []}")          # every 2 s got rate-limited)
            live = [x for x in (window_from_market(m) for m in ms if is_series(m, self.base.coin, self.horizon)) if x and x.start <= now < x.end
                    and not x.slug.startswith("cpc")]          # the retired cpc- addresses are still listed but have no order book
            nxt = min(live, key=lambda x: x.end) if live else None
        if nxt is not None:
            if nxt.slug == self.slug_for(int(nxt.start)):   # only learn from a window found at its own address, not an event's market
                self.learn_prefix(nxt.slug)
        if nxt and nxt.slug in self.recent:
            nxt = self.recent[nxt.slug]
        self.window = nxt
        if nxt:
            nxt.mode = self.lmode()
            self.recent[nxt.slug] = nxt
            for old in sorted(self.recent, key=lambda k: self.recent[k].end)[:-6]:
                self.recent.pop(old, None)

    async def window_by_slug(self, now: float) -> Optional[Window]:
        """The current window looked up by its slug (cpc-btc-updown-5m-YYYY-MM-DD-HHMMz, the window's UTC start; 15m the same).
        Scanning the whole market list missed windows when the list was long, and the desk sat 'between windows' mid-window."""
        if self.horizon not in ("5m", "15m", "1h"):
            return None
        H = self.span
        start = int(now // H) * H
        slug = self.slug_for(start)
        if slug in self.recent and self.recent[slug].start <= now < self.recent[slug].end:
            return self.recent[slug]
        why = ""
        try:
            m = await self.c.market(slug)
            w = window_from_market(m) if m else None
            if w and w.start <= now < w.end:
                return w
            why = "no market data" if not m else f"market data without a usable window (keys: {', '.join(sorted(m)[:8])})"
        except PMError as exc:
            why = str(exc)[:160]
        # the window's address is an EVENT address: look the event up and trade its market at the market's own address
        try:
            for em in await self.c.event_markets(slug):
                w = window_from_market(em)
                if w and w.start <= now < w.end:
                    if self._event_seen != slug:
                        self._event_seen = slug
                        self.log("warn", f"{self.name}: event {slug} trades as market {w.slug}.")
                    return w
            why += "; event lookup had no market for this window"
        except PMError as exc:
            why += f"; event lookup failed ({str(exc)[:120]})"
        # the market lookup failed, but every 5-minute window has a known address and time: trade it from its order book; the
        # price to beat comes from the market when it answers, or from the index itself 30 s in (the same 60-second average).
        # Only once the address pattern is known from a real market (ETH's is cpceth-eth-..., not cpc-eth-...): otherwise scan.
        if not self.slug_prefix:
            return None
        if self._lookup_fail != slug:
            self._lookup_fail = slug
            self.log("warn", f"Market lookup for {slug} failed ({why}); trading it from its order book instead.")
        return Window(slug=slug, start=float(start), end=float(start + H), price_to_beat=None)

    @property
    def horizon(self) -> str:
        """Window length now: the day setting in trading hours, the night one outside them (BTC: 15m by day, 5m at night)."""
        night = self.base.horizon_night
        if night and not risk.in_hours(self.clock(), self.base.trade_start, self.base.trade_end):
            return night
        return self.base.horizon

    @property
    def span(self) -> int:
        from openjarvis.polybot.config import HORIZON_S
        return HORIZON_S.get(self.horizon, 300)

    @property
    def slug_prefix(self) -> str:
        # Polymarket's current address form (btc-updown-5m-...) until a live market teaches a different one
        return self._prefixes.get(self.horizon) or f"{self.base.coin}-updown-{self.horizon}-"

    @slug_prefix.setter
    def slug_prefix(self, v: str) -> None:
        self._prefixes[self.horizon] = v

    def slug_for(self, start: int) -> str:
        prefix = self.slug_prefix or f"{self.base.coin}-updown-{self.horizon}-"     # Polymarket's current form (no cpc- prefix)
        return f"{prefix}{time.strftime('%Y-%m-%d-%H%M', time.gmtime(start))}z"

    def learn_prefix(self, slug: str) -> None:
        """cpceth-eth-updown-5m-2026-10-30-0225z -> cpceth-eth-updown-5m- (the part before the date)."""
        if slug.startswith("cpc"):                  # retired address form: never learn from it
            return
        parts = slug.split("-")
        if len(parts) > 4 and parts[-1].endswith("z"):
            p = "-".join(parts[:-4]) + "-"
            h = parts[-5] if len(parts) > 5 else self.horizon            # the window length is in the address (...-updown-5m-...)
            if p != self._prefixes.get(h):
                self._prefixes[h] = p
                self.log("info", f"{self.name} market addresses look like {p}YYYY-MM-DD-HHMMz.")

    async def load_book(self, now: float) -> None:
        if not self.window:
            self.book = None
            return
        try:
            self.book = BookView.parse(await self.c.book(self.window.slug), now)
            self.book_from_bbo = False
            if self.book is not None and getattr(self, "_book_open_seen", None) != self.window.slug:
                self._book_open_seen = self.window.slug          # diagnostic: say once per window when the order book opens, and its prices
                bb, ba = self.book.best_bid, self.book.best_ask
                self.log("info", f"Order book open for {self.window.slug} {now - self.window.start:.0f}s into the window: "
                                 f"Up ask {ba if ba is not None else 'none'}, Down ask {round(1 - bb, 4) if bb is not None else 'none'}.")
        except PMError as exc:
            if exc.status == 404:                       # the market closed and is gone: not an error, move on to the next window
                if self.window.start <= now < self.window.end - 5:
                    bbo = await self.bbo_book(now)       # the full book opens 1-2 min in; best bid/offer often has prices sooner
                    if bbo is not None:
                        self.book, self.book_from_bbo = bbo, True
                        return
                if self.window.start <= now < self.window.end - 5 and self._book404 != self.window.slug:
                    self._book404 = self.window.slug     # the market is listed but has no order book: say so once (that is the cause)
                    self.log("warn", f"{self.window.slug} is listed but Polymarket has no order book for it yet ({str(exc)[:120]}).")
                self.window, self.book = None, None
                return
            raise

    async def bbo_book(self, now: float) -> Optional[BookView]:
        """Prices from the best bid/offer endpoint while the full book is missing. It has no sizes (qty 0), so the desk sees the
        market and explains its decision but places no order until the full book shows how many contracts are there."""
        slug = self.window.slug
        prev = getattr(self, "_bbo_last", None)
        if not self._due("bbo", 4.0, now):                         # at most every 4 s per desk; reuse a fresh answer in between
            return prev if prev is not None and getattr(self, "_bbo_slug", "") == slug and now - prev.ts < 8 else None
        try:
            md = (await self.c.bbo(slug)).get("marketData") or {}
        except PMError:
            return None
        bid, ask = money(md.get("bestBid")), money(md.get("bestAsk"))
        if bid is None and ask is None:
            return None
        bv = BookView(bids=[(round(bid, 4), 0.0)] if bid is not None else [], offers=[(round(ask, 4), 0.0)] if ask is not None else [],
                      last_trade_px=money(md.get("lastTradePx")), state="MARKET_STATE_OPEN", ts=now)
        self._bbo_last, self._bbo_slug = bv, slug
        if getattr(self, "_bbo_seen", None) != slug:
            self._bbo_seen = slug
            self.log("info", f"Prices for {slug} from best bid/offer {now - self.window.start:.0f}s in (Up ask {ask}, Down ask "
                             f"{round(1 - bid, 4) if bid is not None else 'none'}); no order until the full book shows the size.")
        return bv

    # ── fills and settlement ──────────────────────────────────────────────────────────
    def _book_fill(self, f: Fill) -> None:
        w = self.open_windows.get(f.slug) or self.recent.get(f.slug)
        if w is None:
            self.log("warn", f"A fill on {f.slug} could not be matched to a window; it is not counted.")
            return
        w.mode = self.lmode()
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
        feats = {
            # what the desk saw at the moment of the fill, so results can be cut by condition later
            "model": None if self.fair_model is None else round(self.fair_model, 4),
            "mid": None if self.mid is None else round(self.mid, 4),
            "momentum_z": round(self.momentum, 2), "trend_z": round(self.trend, 2),
            "secs_left": round(w.end - f.ts, 1),
            "edge": None if fair_side is None else round(fair_side - cost_per, 4),          # fair value of the side bought minus its cost
            "model_vs_market": None if self.fair_model is None or self.mid is None else round(self.fair_model - self.mid, 4),
            "spread": None if not b or b.best_bid is None or b.best_ask is None else round(b.best_ask - b.best_bid, 4),
            "extra_edge": self.learner.extra_edge if self.cfg.learn else 0.0,
            "sigma": None if self.sigma is None else round(self.sigma, 3),
            "sweep": (f.slug, f.side) in self._sweeps,                                      # v1.13: bought by a closing sweep
        }
        bu = shadow.book_up(getattr(self.book, "bids", None), getattr(self.book, "offers", None))
        bconf = None if bu is None else (bu if f.side == "up" else round(1.0 - bu, 4))
        mconf = None if self.fair_model is None else (self.fair_model if f.side == "up" else 1.0 - self.fair_model)
        feats.update({"book_up": bu})                                        # v1.15: the order book's own view, recorded (not traded)
        self._fill_conf.setdefault(w.slug, []).append((f.side, mconf, bconf))
        if not feats["sweep"]:
            cal_c = self.shadow.calibrated_conf(mconf)
            cal_lot = directional.size_pct(cal_c, self.cfg.asset, calibrated=True) if cal_c is not None else 0.0
            self._entry_sig.setdefault(w.slug, (feats["sigma"], mconf, cal_c, cal_lot))   # the window's first entry
        if not feats["sweep"]:
            # v1.14 shadow sizing: the window's pair is filed under the setup its FIRST leg opened in; record what size that setup
            # has earned next to the size actually used (trading still uses the normal size)
            pf = self._pair_fills.setdefault(w.slug, [])
            first = pf[0] if pf else {"side": f.side, "features": feats}
            key = setups.pair_setup(first["side"], first["features"])
            learned_pct, tier = self.setups.size(key, self.cfg.window_pct)
            feats.update({"setup": key, "shadow_pct": learned_pct, "shadow_tier": tier, "actual_pct": self.cfg.window_pct})
            pf.append({"side": f.side, "qty": f.qty, "cost": f.cost, "features": feats})
        self.ledger.fill(self.lmode(), f, self.fair, feats)
        self.stats["fills"] += 1

    async def on_fills(self, fills: List[Fill]) -> None:
        for f in fills:
            self._book_fill(f)
            w = self.open_windows.get(f.slug)
            side = f"UP (call-like: pays if {self.base.coin.upper()} closes at/above)" if f.side == "up" else f"DOWN (put-like: pays if {self.base.coin.upper()} closes below)"
            pay = f.price if f.side == "up" else 1 - f.price
            k = f"${w.price_to_beat:,.2f}" if w and w.price_to_beat else "pending"
            end = time.strftime("%H:%M:%S UTC", time.gmtime(w.end)) if w else "?"
            text = (f"ENTRY {self.cfg.mode.upper()}{' [SWEEP]' if (f.slug, f.side) in self._sweeps else ''} · {self.base.coin.upper()} {self.horizon} Up/Down · {side} · strike (price to beat) {k} · "
                    f"expires {end} · {f.qty:g} contracts at ${pay:.2f} = ${f.cost:.2f} · stop: none, held to settlement (max loss ${f.cost:.2f}) · "
                    f"target: $1.00 per contract (${f.qty:.2f}) · fair value then {self.fair if self.fair is not None else 0:.2f}"
                    + ("" if self.fair_model is None else
                       f" · confidence {(self.fair_model if f.side == 'up' else 1 - self.fair_model):.0%} (model's odds this side wins)"))
            self.log("trade", text)
            if self.cfg.mode == "live":
                await self.push("Desk entry", text)

    async def settle_due(self, now: float) -> None:
        for slug, w in list(self.open_windows.items()):
            if now < w.end + 3 or not w.price_to_beat:
                continue
            # v1.14: the exchange's own settlement is the truth for live money; the desk's index replay is only the fallback
            # (and the paper desk's referee). Ask every 10 s; use the index if the exchange has not answered 5 minutes after the close.
            res = None
            if is_live_label(w.mode):
                # v1.22: live money is booked only from the venue's own final answer. The index's 60-second average can land on
                # the other side of the strike from the venue's close (9:15 PM Nov 2: average above the target, venue settled Down),
                # so a live window with no final answer stays open instead of being booked from the index.
                if now >= w.end + 20 and self._due(f"settle:{slug}", 10.0, now):
                    s = await self.c.settlement(slug)
                    if s is not None and (s >= 0.99 or s <= 0.01):                 # only a final $1 / $0 answer counts
                        mine = model.settle(self.tape, int(w.end), w.price_to_beat)
                        res = (mine[0] if mine else None), s >= 0.5
                        if mine and mine[1] != res[1]:
                            self.log("warn", f"{slug}: the index average said {'UP' if mine[1] else 'DOWN'} but the venue settled "
                                             f"{'UP' if res[1] else 'DOWN'}; booking the venue's result.")
                if res is None:
                    if now > w.end + 600 and self._due(f"unsettled:{slug}", 300.0, now):
                        self.log("warn", f"{slug}: no final result from Polymarket 10 minutes after close; holding the window open.")
                    continue
            else:
                res = model.settle(self.tape, int(w.end), w.price_to_beat)
                if res is None and now > w.end + 120:                            # paper: index gap, ask the exchange
                    s = await self.c.settlement(slug)
                    if s is not None:
                        res = (None, s >= 0.5)
            if res is None:
                continue
            close, up = res
            self.ledger.result(slug, bool(up), close)               # v1.42: every settled window, traded or not
            self.shadow.settle(slug, up)
            for method, (side, usd, qty) in self._bets.pop(slug, {}).items():
                if method != "directional":
                    self.shadow.add_result(method, round((qty if (side == "up") == bool(up) else 0.0) - usd, 4), usd)
            if slug in self._dir_pending:
                self._dir_pending.discard(slug)
                if w.inv.spent > 0:
                    self.shadow.add_result("directional", (w.inv.up_qty if up else w.inv.down_qty) - w.inv.spent, w.inv.spent)
            for side, mc, bc in self._fill_conf.pop(slug, []):
                self.shadow.calib_add(mc, bc, (side == "up") == bool(up))
                if is_live_label(w.mode):                              # v1.33: live fills feed the live-only tally (XRP's calibration)
                    self.shadow.live_add(mc, (side == "up") == bool(up))
            try:
                await self.store.put("shadow2", self.shadow.to_dict())
            except Exception:
                logger.warning("shadow record not saved")
            po = setups.pair_outcome(self._pair_fills.pop(slug, []), up)
            if po is not None and is_live_label(w.mode):
                self.setups.add(*po)
            payout = w.inv.up_qty if up else w.inv.down_qty
            pnl = round(payout - w.inv.spent, 4)
            ent = self._entry_sig.pop(slug, None)
            if ent is not None and w.inv.spent > 0:
                self.shadow.add_sizing(slug, ent[0], ent[1], directional.size_pct(ent[1] or 0.0, self.cfg.asset), ent[2], ent[3],
                                       pnl / w.inv.spent)
                try:
                    await self.store.put("shadow2", self.shadow.to_dict())
                except Exception:
                    logger.warning("shadow sizing not saved")
            w.settled = True
            del self.open_windows[slug]
            await self._shared().put(f"openspend:{slug}:{self.cfg.asset}{self._acct_sfx()}", 0.0)      # settled: nothing left booked here
            self._t.pop(f"settle:{slug}", None)
            self.rs.roll(now)
            self.rs.day_pnl += pnl
            self.hour_pnl += pnl
            self.stats["windows"] += 1
            self.stats["wins"] += 1 if pnl > 0 else 0
            if w.mode == self.lmode():
                self.learner.add(pnl, w.inv.spent)
            self.ledger.window({"slug": slug, "mode": w.mode, "window_start": w.start, "window_end": w.end, "price_to_beat": w.price_to_beat,
                                "close_price": close, "up": up, "up_qty": w.inv.up_qty, "up_cost": round(w.inv.up_cost, 4),
                                "down_qty": w.inv.down_qty, "down_cost": round(w.inv.down_cost, 4), "pnl": pnl, "settled_at": now})
            text = (f"EXIT (settled) {w.mode.upper()} · {slug} · {self.name} {'UP' if up else 'DOWN'} (open ${w.price_to_beat:,.2f}"
                    + (f", close ${close:,.2f}" if close else "") + f") · held Up {w.inv.up_qty:g} / Down {w.inv.down_qty:g}, paid ${w.inv.spent:.2f}, "
                    f"got ${payout:.2f} · P&L {'+' if pnl >= 0 else ''}{pnl:.2f} · today {'+' if self.rs.day_pnl >= 0 else ''}{self.rs.day_pnl:.2f}")
            self.log("trade", text)
            if is_live_label(w.mode):
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
        if self.cfg.mode != "live" or not self._due("positions", 15.0, now):
            return
        try:
            pos = await self.c.positions()
        except PMError as exc:
            self.log("warn", f"Positions unreadable: {exc}")
            return
        await self.publish_open_spend()
        await self.check_positions(pos or {}, now)
        await self.flag_untracked(pos or {})
        ext = 0.0
        for slug, p in (pos or {}).items():
            if slug in self.open_windows or p.get("expired"):
                continue
            if "-updown-" in slug:                       # every desk's open contracts count: they all share one account
                ext += abs(money(p.get("cost")) or 0.0)
        self.external_at_risk = round(ext, 2)

    def _calibrated(self, p):
        """v1.36: every desk's confidence is corrected toward its LIVE fills only (paper ran far better and hid the real losses).
        v1.37 (owner, 2026-11-08): the correction is half strength: half of the gap between the raw model and the live rate is applied."""
        c = self.shadow.live_calibrated_conf(p)
        if c is None:
            return p
        return round(p + CORRECTION_STRENGTH * (c - p), 4)

    def _calib_for(self, w):
        """The confidence the bot trades on for this window: the live-calibrated confidence in every hour (v1.36: no raw hours)."""
        return self._calib

    def hour_factor(self, ts: float) -> float:
        """v1.25: the size factor for a window opening at ts (Los Angeles hour): 1 normally, 0.5 at 7 AM, 0 to skip the hour."""
        h = dt.datetime.fromtimestamp(ts, risk.LA).hour
        return dict(self.cfg.hour_size).get(h, 1.0)

    def _xacct(self) -> str:
        """Whose cross-desk tally this desk's bets count in: live desks share their account's; a paper desk has its own, so
        simulated bets never take room from real-money desks."""
        return self.base.account if self.cfg.mode == "live" else f"{self.base.account}~paper~{self.base.asset}"

    def _acct_sfx(self) -> str:
        return f"@{self.base.account}" if self.base.account else ""

    def _shared(self):
        """The shared store under this desk's scope: the keys that every desk can read (open spend per market)."""
        return getattr(self.store, "store", self.store)

    async def publish_open_spend(self) -> None:
        """Share this desk's booked spend per open window, so the other desks on the same market can account for it."""
        for slug, w in self.open_windows.items():
            await self._shared().put(f"openspend:{slug}:{self.cfg.asset}{self._acct_sfx()}", round(w.inv.spent, 4))

    async def others_spent(self, slug: str) -> float:
        """What the other desks on this market have booked in this window (they share one Polymarket position)."""
        total = 0.0
        for other in SHARED_MARKET.get(self.cfg.asset, ()):
            total += float(await self._shared().get(f"openspend:{slug}:{other}{self._acct_sfx()}") or 0.0)
        return total

    async def flag_untracked(self, pos: Dict[str, Any]) -> None:
        """v1.29: money in one of THIS desk's markets that the desk never recorded (an order that filled while Polymarket was
        rate-limiting the desk). It is not in the scoreboard, so say so loudly, once per window, with what Polymarket reports."""
        mark = f"-{self.base.coin}-updown-{self.horizon}-"
        seen = self.__dict__.setdefault("_untracked_seen", set())
        for slug, p in pos.items():
            if mark not in slug or slug in self.open_windows or slug in seen or (p or {}).get("expired"):
                continue
            cost = abs(money((p or {}).get("cost")) or 0.0)
            if cost < 0.05:
                continue
            seen.add(slug)
            msg = (f"Polymarket holds ${cost:.2f} in {slug} that this desk did not record (likely filled while the API was "
                   f"rate-limited). It is NOT in the scoreboard. Polymarket says: {str(p)[:300]}")
            self.log("warn", msg)
            await self.push("Unrecorded trade", msg[:240])
        if len(seen) > 200:
            self.__dict__["_untracked_seen"] = set(list(seen)[-50:])

    async def check_positions(self, pos: Dict[str, Any], now: float) -> None:
        """v1.8 safety check: what Polymarket says the desk paid in each open window must match what the desk recorded.
        Earlier versions sometimes held far more than they knew (orders that filled after a cancel), which made windows of
        +$16 and -$4 on a $7.50 budget. If that happens again the desk stops trading, cancels its orders and alerts the owner."""
        if self.guard:
            return
        for slug, w in list(self.open_windows.items()):
            p = pos.get(slug)
            if not p or p.get("expired"):
                continue
            real = abs(money(p.get("cost")) or 0.0)
            booked = w.inv.spent + await self.others_spent(slug)
            if real <= booked + max(1.0, 0.25 * booked):
                self._mismatch.pop(slug, None)
                continue
            self._mismatch[slug] = self._mismatch.get(slug, 0) + 1
            if self._mismatch[slug] >= 2:
                self.guard = (f"Stopped: Polymarket shows ${real:.2f} in this window but the desk recorded ${booked:.2f}. "
                              "Tap Pause, then Resume, to trade again after checking.")
                self.log("warn", self.guard)
                await self.broker.cancel_all(now)
                await self.push("Desk stopped: position mismatch", self.guard)
                return

    # ── one second of work ────────────────────────────────────────────────────────────
    def lmode(self) -> str:
        """v1.23: the ledger label for this desk's rows: live, or the desk's own paper label (btclate: paper-late)."""
        return self.cfg.live_ledger if self.cfg.mode == "live" else self.cfg.ledger

    def _blocked_entry(self, secs_left: float, price: float, momentum: Optional[float] = None, side: Optional[str] = None) -> bool:
        return self._block_rule(secs_left, price, momentum, side) is not None

    def _block_rule(self, secs_left: float, price: float, momentum: Optional[float] = None, side: Optional[str] = None) -> Optional[str]:
        """The name of the entry rule that rules this buy out, or None when it is allowed. The rules themselves are the same as
        before v1.38 (the name is the only addition).
        Entries the current review rules out (both on 1.21):
        5-minute BTC: no buy priced 30-70c in the final 60 seconds, and none at any price while the price is flat (momentum between -0.3 and 0.3).
        15-minute BTC: no buy priced 70-90c at any time (about 80c needs an 80% win rate; this bot won 75%)."""
        if self.cfg.asset == "btc15" and side == "down":
            return "btc15 down"                                     # v1.36: BTC 15m's Down bets: 49 live fills, 71% won, -$24.79 (Up: +$15.93)
        if self.cfg.asset == "sol" and momentum is not None and momentum > 0.3:
            return "sol momentum"                                   # v1.35: SOL 5m, price already moving with the bet: 17 live fills, 12 won, -$4.40
        if self.cfg.asset == "btc" and side == "down" and 0.70 <= price < 0.80:
            return "btc down 70-79c"                                # BTC 5m Down buys at 70-79c: 75 live fills, 67% won, -$37 (break-even needs ~75%)
        # v1.39: the BTC 5m Up-under-40c block is removed. Oct 29-Nov 9 live fills: 46 fills, 30% won, +$19.44 (the old block had
        # cited 44 fills, 25% won, -$27 from an earlier sample that the current fills do not reproduce).
        if self.cfg.asset == "btc" and momentum is not None and -0.3 <= momentum <= 0.3:
            return "btc flat momentum"                              # v1.34, widened: BTC 5m's flat-price buys at any price. v1.34 blocked 30-70c only;
                                                                    # Oct 29-Nov 8 live CSV: flat-price buys 57% won, -$50 on 190 fills (30-70c -$26, outside 30-70c -$24)
        # v1.41 (owner, 2026-11-10): entry price floors on v1.21 fills. ETH 5m's 75-84c buys lost money (58 fills, 79% won, -$2.34);
        # its 85c+ buys made +$1.19. SOL 5m's 60-74c buys lost money (15 fills, 60% won, -$2.15); its 75c+ buys made +$9.64 (a test).
        if self.cfg.asset == "eth" and price < 0.85:
            return "eth 5m under 85c"
        if self.cfg.asset == "sol" and price < 0.75:
            return "sol 5m under 75c"
        if self.cfg.asset in ("btc", "eth", "sol", "xrp"):          # v1.28: BTC 5m's block, copied to the other 5-minute desks
            return "last 60s 30-70c" if (secs_left <= 60.0 and 0.30 <= price <= 0.70) else None
        if self.cfg.asset == "btc15" and 0.70 <= price < 0.90:
            return "btc15 70-90c"
        return None

    def _entry_check(self, now: float, w, price: float, momentum: Optional[float], side: str) -> Optional[str]:
        """v1.38: the one place an entry is checked. Returns why it is stopped (the event blackout, when switched on, or a rule), or None.
        Each distinct candidate (window, side, reason) is queued once for polybot_candidates, so the logs show what the bot passed up.
        Logging never changes the decision."""
        reason = None
        if research.BLACKOUT_ON:
            ev = research.in_blackout(now, research.shipped_events())
            if ev:
                reason = f"blackout: {ev}"
        if reason is None:
            reason = self._block_rule(w.end - now, price, momentum, side)
        key = (w.slug, side, reason or "")
        if key not in self._cand_seen:
            if len(self._cand_seen) > 5000:
                self._cand_seen.clear()
            self._cand_seen.add(key)
            sigma = self.sigma
            vol = research.vol_bucket(sigma) if self.cfg.asset == "btc" else None
            row = research.candidate_row(w.slug, now, w.end - now, side, price, self.fair, momentum, sigma, vol, reason)
            self.ledger.candidate({**row, "ts_unix": now})
        return reason

    async def _share_day_start(self, now: float) -> None:
        """v1.29: the daily stop is for the ACCOUNT. Every live desk measures today's loss from the same starting value: the account
        value when the first live desk started the day (midnight Los Angeles, or the owner's reset). A desk that goes live mid-day no
        longer starts its own fresh 18% from a lower balance. Read every 30 s; a desk whose shared value cannot be read keeps its own."""
        if now - self._t.get("dayshare", 0.0) < 30.0 and self.rs.day_start is not None:
            return
        self._t["dayshare"] = now
        raw = getattr(self.store, "store", None)                   # the unscoped store (one key for the whole account)
        if raw is None:
            return
        key = "account_day" + (f"@{self.base.account}" if self.base.account else "")
        try:
            cur = await raw.get(key) or {}
            reset = getattr(self, "_day_reset_seen", None) or ""
            mine_first = (self.cfg.asset == "btc" and cur.get("by") != "btc" and self.rs.day_start is not None)  # BTC 5m (live all day) wins
            if cur.get("day") == self.rs.day and cur.get("reset", "") == reset and cur.get("start") is not None and not mine_first:
                self.rs.day_start = float(cur["start"])
            else:
                start = self.rs.day_start if self.rs.day_start is not None else round(self.account_value(), 2)
                await raw.put(key, {"day": self.rs.day, "start": start, "reset": reset, "by": self.cfg.asset})
                # the day's starting balance, kept per day for the Daily P&L workbook (one key per day and account)
                await raw.put(f"day_start:{self.rs.day}" + (f"@{self.base.account}" if self.base.account else ""), {"start": start})
                self.rs.day_start = start
        except Exception:
            logger.exception("shared day start unavailable; this desk keeps its own")

    def _taken(self, slug: str, method: str) -> bool:
        return method in self._bets.get(slug, {})

    def _place(self, slug: str, method: str, side: str, usd: float, qty: float) -> None:
        """v1.22: record a bet the method just placed in this window (one per method), and count it against the windows' cap."""
        self._bets.setdefault(slug, {})[method] = (side, round(usd, 4), qty)
        self._dir_pending.add(slug)
        exposure.reserve(self.name, usd, self._xacct())

    async def step(self) -> None:
        now = self.clock()
        try:
            self.c.low_priority = self.cfg.mode != "live"      # v1.30: paper desks use the small lane, live desks never wait behind them
        except Exception:
            pass
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
        exposure.publish(self.name, account=self._xacct(), usd=sum(w.inv.spent for w in self.open_windows.values() if w.slug in self._dir_pending))   # v1.20 cross-desk cap
        if self.version_start is None and self.cfg.mode == "live" and self.balance is not None:
            self.version_start = round(self.account_value(), 2)
            try:
                import os as _os
                self.deposits_start = (None if self.base.account else        # the owner's deposits figure is his account's, never a subscriber's
                                       float((_os.environ.get("POLYBOT_DEPOSITS_USD") or "").strip() or 0) or None)
            except ValueError:
                self.deposits_start = None
            self.log("info", f"Strategy v{brain.STRATEGY_VERSION} starts with a real account value of ${self.version_start:.2f}.")
        if self.rs.day_start is None:
            if self.cfg.mode == "live":
                if self.balance is not None:
                    self.rs.day_start = round(self.account_value(), 2)
                    self.log("info", f"Day starts with ${self.rs.day_start:.2f}; today's loss limit is ${risk.day_limit(self.cfg, self.rs):.2f}.")
            else:
                self.rs.day_start = self.cfg.limits.bankroll_usd
        if self.cfg.mode == "live" and self.balance is not None:
            await self._share_day_start(now)                  # v1.29: one day start for the whole account, every live desk
        if self.cfg.mode == "live" and self.balance is not None and self.rs.day_start is not None:
            self.rs.real_pnl = round(self.account_value() - self.rs.day_start, 2)
        self.rs.resting = round(self.broker.resting_cost, 4)
        w, b = self.window, self.book
        last = self.tape.last()
        data_age = now - last[0] - 1 if last else 999.0
        jump = self.base.jump_usd * (last[1] / 78000.0 if last else 1.0)          # $25 at BTC $78k; the same % move for ETH and SOL
        if self.sigma:                    # ... but never tighter than a 3.5-sd move for this coin (SOL and ETH were pulling quotes on every tick)
            jump = max(jump, 3.5 * self.sigma * math.sqrt(self.base.jump_window_s))
        if self.tape.moved(int(now), self.base.jump_window_s) >= jump:
            if now >= self.rs.cooldown_until:
                self.log("info", f"{self.name} moved ${self.tape.moved(int(now), self.base.jump_window_s):.0f} in {self.base.jump_window_s:.0f}s: quotes pulled.")
            self.rs.cooldown_until = now + self.base.cooldown_s
        self.fair_model = (model.fair_up(self.tape, int(now), int(w.end), w.price_to_beat, self.sigma or 0.0, self.cfg.vol_mult,
                                         drift_beta=self.cfg.drift_beta, drift_lookback_s=int(self.cfg.momentum_s))
                           if w and w.price_to_beat else None)
        self.mid = brain.market_mid(b.best_bid, b.best_ask) if b else None
        self.fair = brain.blend(self.fair_model, self.mid, self.cfg.model_weight)
        self.momentum = brain.momentum_z(self.tape, int(now), int(self.cfg.momentum_s), self.sigma)
        if w is not None and w.price_to_beat and b is not None:
            # v1.21 shadows: the book's own view, and the model with a tighter and a wider volatility
            book_view = shadow.book_up(getattr(b, "bids", None), getattr(b, "offers", None))
            fair_lo = model.fair_up(self.tape, int(now), int(w.end), w.price_to_beat, self.sigma or 0.0, self.cfg.vol_mult * 0.7,
                                    drift_beta=self.cfg.drift_beta, drift_lookback_s=int(self.cfg.momentum_s)) if self.sigma else None
            fair_hi = model.fair_up(self.tape, int(now), int(w.end), w.price_to_beat, self.sigma or 0.0, self.cfg.vol_mult * 1.4,
                                    drift_beta=self.cfg.drift_beta, drift_lookback_s=int(self.cfg.momentum_s)) if self.sigma else None
            self.shadow.tick(w.slug, w.end - now, self.fair_model, b.best_ask,
                             None if b.best_bid is None else round(1.0 - b.best_bid, 6),
                             {"book_up": book_view, "fair_lo": fair_lo, "fair_hi": fair_hi, "momentum": self.momentum})
            self.shadow.maker_tick(w.slug, w.end - now, self.fair_model, b.best_ask,
                                   None if b.best_bid is None else round(1.0 - b.best_bid, 6))
            self.shadow.gate_tick(w.slug, w.end - now, self.fair_model, b.best_ask,
                                  None if b.best_bid is None else round(1.0 - b.best_bid, 6))
            self.shadow.record_fair(w.slug, w.end - now, self.fair_model, fair_lo, fair_hi, book_view)   # the model's views, logged per window
        if self.guard and not (self.rs.kill or self.rs.paused):
            reason = self.guard
        elif w is None and not (self.rs.kill or self.rs.paused):
            if now % self.span > 30:            # well into a window and still no market: say so (and log it once per window) instead of "between windows"
                slug = self.slug_for(int(now // self.span) * self.span)
                reason = (f"Polymarket has no order book yet for this window ({slug}): nobody is trading it; checking every 2 seconds."
                          if self._book404 == slug else f"Cannot find this window's {self.name} market ({slug}); retrying every 2 seconds.")
                if self._missing != slug:
                    self._missing = slug
                    self.log("warn", reason)
            else:
                reason = f"Between windows: waiting for the next {self.horizon} market to open."
        else:
            reason = None
        reason = reason or risk.blocked(self.cfg, self.rs, now=now, data_age_s=data_age, book_age_s=(now - b.ts) if b else 999.0,
                              market_open=bool(b and b.state == "MARKET_STATE_OPEN"), secs_since_open=(now - w.start) if w else 0.0,
                              secs_to_end=(w.end - now) if w else 0.0, balance=self.balance)
        if reason is None and not w.price_to_beat:
            reason = "Waiting for the price to beat."
        if reason is None and self.fair is None:
            reason = ("Not enough index history yet to price the window." if self.fair_model is None
                      else "The order book is one-sided or empty right now, so there is no market price to check the model against.")
        want: List[strategy.Quote] = []
        if reason is None:
            inv = self.open_windows[w.slug].inv if w.slug in self.open_windows else w.inv
            budget = risk.budget_left(self.cfg, self.rs, self.balance) + self.rs.resting      # its own resting orders do not block themselves
            can_up, can_down, why = brain.side_filters(self.momentum, self.cfg.momentum_z, self.name)
            fills = (self.open_windows.get(w.slug) or w).fills
            can_up = can_up and brain.can_add(fills, "up", self.cfg.max_fills_per_side)
            can_down = can_down and brain.can_add(fills, "down", self.cfg.max_fills_per_side)
            if not risk.in_hours(now, self.cfg.trade_start, self.cfg.trade_end):     # v1.13: overnight, only finish what is open
                can_up = can_up and inv.down_qty > inv.up_qty + 1e-9
                can_down = can_down and inv.up_qty > inv.down_qty + 1e-9
                if not (can_up or can_down):
                    why = why or f"Outside trading hours ({self.cfg.trade_start}-{self.cfg.trade_end} Pacific): no new trades."
            if w.end - now < self.cfg.open_cutoff_s:                 # late in the window: only buy what completes a pair
                can_up = can_up and inv.down_qty > inv.up_qty + 1e-9
                can_down = can_down and inv.up_qty > inv.down_qty + 1e-9
                if not (can_up or can_down):
                    why = why or "Last 90 seconds: only completing pairs, nothing new."
            self.trend = brain.momentum_z(self.tape, int(now), int(self.cfg.trend_s), self.sigma)
            if self.cfg.trend_z > 0:                                  # v1.10: every v1.8/v1.9 loss was Up bought into a falling BTC
                if self.trend <= -self.cfg.trend_z and can_up and not inv.down_qty > inv.up_qty + 1e-9:
                    can_up = False
                    why = why or f"{self.name} trending down over 15 min ({self.trend:+.1f} sd): no new Up."
                if self.trend >= self.cfg.trend_z and can_down and not inv.up_qty > inv.down_qty + 1e-9:
                    can_down = False
                    why = why or f"{self.name} trending up over 15 min ({self.trend:+.1f} sd): no new Down."
            # v1.12 hedge: a one-sided leg is finished (crossing if needed) once it has waited hedge_after_s, or near the close
            wobj = self.open_windows.get(w.slug) or w
            lopsided = abs(inv.up_qty - inv.down_qty) >= w.min_qty
            if not lopsided:
                wobj.unpaired_since = None
            elif wobj.unpaired_since is None:
                wobj.unpaired_since = now
            hedge = None
            # v1.16: a leftover leg may only be completed at $1.00 or less (never at a loss), and only if it is lopsided
            hedge_cap = 0.0 if self.cfg.pairs_only else self.cfg.hedge_max_loss
            if lopsided and (hedge_cap > 0 or self.cfg.pairs_only) and (now - wobj.unpaired_since >= self.cfg.hedge_after_s
                                                                       or w.end - now <= self.cfg.hedge_before_end_s):
                hedge = hedge_cap
                can_up, can_down = inv.down_qty > inv.up_qty, inv.up_qty > inv.down_qty
                why = "Finishing the pair (hedge)."
            # v1.21: a fresh window opens its first leg only where the model's confidence clears this coin's tier floor
            # (BTC/XRP 80%, ETH/SOL 95%); completing a leg already held is unchanged
            if inv.up_qty <= 0 and inv.down_qty <= 0:
                open_up, open_down = directional.open_sides(self.fair, self.cfg.asset)
                can_up, can_down = can_up and open_up, can_down and open_down
            extra = self.learner.extra_edge if self.cfg.learn else 0.0
            lim = replace(self.cfg.limits, min_edge=round(self.cfg.limits.min_edge + extra, 3))
            equity = self.equity()
            sizing = strategy.Sizing(equity=equity,
                                     kelly_fraction=self.cfg.kelly_fraction, window_pct=self.cfg.window_pct, min_order_usd=self.cfg.min_order_usd)
            want = strategy.quotes(self.fair, strategy.Book(b.best_bid, b.best_ask, w.tick, w.min_qty), inv, lim, budget_left=budget,
                                   can_up=can_up, can_down=can_down, sizing=sizing,
                                   min_cost=self.cfg.min_buy_price, max_cost=self.cfg.max_buy_price,
                                   open_min=self.cfg.open_min_price, open_max=self.cfg.open_max_price, hedge_loss=hedge,
                                   unpaired_usd=max(0.25, sizing.window_cap(self.cfg.limits) * self.cfg.unpaired_pct / 100.0)
                                   ) if (can_up or can_down) else []
            # v1.13 closing sweep (all hours): in the last sweep_secs_left seconds, when the model is confident, the desk only buys the
            # likely winner (never the other side, which would undo it) at 75-97c when the model's odds beat the price
            sweep_s = (self.cfg.sweep_secs_left if risk.in_hours(now, self.cfg.trade_start, self.cfg.trade_end)
                       else self.cfg.sweep_secs_left_night)                     # v1.14: 60 s by day, 90 s overnight
            if self.cfg.pairs_only:
                # v1.16: the only way in is a pair bought on both sides at once; no sweeps, no resting quotes
                want = []
                if w.slug in self._dir_pending and inv.spent > 0:
                    want, why = [], "Holding the directional trade to settlement."          # never hedge a directional bet away
                elif not lopsided:
                    hf = self.hour_factor(w.start)                     # v1.25: 7 AM windows at half size (the daily review may change it)
                    budget_usd = min(budget, sizing.window_cap(self.cfg.limits)) * hf
                    want, why = strategy.pair_takes(b.best_bid, b.best_ask,
                                                    b.offers[0][1] if b.offers else 0.0, b.bids[0][1] if b.bids else 0.0,
                                                    budget_usd, sizing.window_cap(self.cfg.limits) * hf, w.min_qty, self.cfg.pair_margin) \
                        if (inv.spent <= 0 and "pairs" in self.cfg.methods and hf > 0) else ([], "Not trading this hour (hour size 0).") if hf <= 0 else ([], "Pairs are off for this desk." if "pairs" not in self.cfg.methods else "Already in this window; holding to settlement.")
                    if want and any(self._entry_check(now, w, q.price, self.momentum, q.side) for q in want):
                        want, why = [], "Skipped by entry rule: pair leg at a blocked price."
                    can_up_m, can_down_m, mom_why = brain.side_filters(self.momentum, self.cfg.momentum_z, self.name)
                    if not want and not self._taken(w.slug, "directional") and self.cfg.directional and "directional" in self.cfg.methods \
                            and self.fair_model is not None and b.best_ask is not None and b.best_bid is not None \
                            and self.cfg.directional_min_secs <= w.end - now <= self.cfg.directional_secs_left:
                        z = directional.cushion_z(self.tape.at(int(now)), w.price_to_beat, self.sigma, w.end - now)
                        pick = directional.decide(self.fair_model, b.best_ask, round(1.0 - b.best_bid, 6),
                                                  self.shadow.bucket_stats(self.fair_model), self.shadow.bucket_stats(1.0 - self.fair_model), z=z,
                                                  up_ok=can_up_m, down_ok=can_down_m, asset=self.cfg.asset, calib=self._calib_for(w))
                        stopped = None if pick is None else self._entry_check(now, w, pick[1], self.momentum, pick[0])
                        if stopped:
                            why = f"Skipped by entry rule ({stopped}): buy at {pick[1] * 100:.0f}c."
                            pick = None
                        if pick is not None:
                            side, cost, tier, edge, lot_pct = pick
                            size_here = (b.offers[0][1] if b.offers else 0.0) if side == "up" else (b.bids[0][1] if b.bids else 0.0)
                            bets = self._bets.get(w.slug, {})
                            usd = min(budget_usd, equity * lot_pct / 100.0, exposure.headroom(equity, self._xacct()), directional.window_room(bets, equity, self.cfg.asset))   # lot by coin and confidence; capped across all desks and the window
                            ok_win, _ = directional.may_take(bets, side, usd, equity, self.cfg.asset)
                            q = strategy._qty(usd, cost, w.min_qty)
                            q = min(q, round(size_here // w.min_qty * w.min_qty, 6))
                            if q >= w.min_qty and ok_win:
                                price = b.best_ask if side == "up" else b.best_bid
                                want = [strategy.Quote(side, price, q, taker=True)]
                                self._place(w.slug, "directional", side, usd, q)
                                why = f"Directional ({tier}): {side} at {cost * 100:.0f}c, edge {edge * 100:.1f}c after fees, lot {lot_pct:.0f}%."
                    # v1.22: edge and late are extra single-bet methods: one bet each per window, never against a side already bet
                    if self.cfg.directional and self.fair_model is not None and b.best_ask is not None and b.best_bid is not None:
                        split = self.cfg.late_split_s                    # 60 s on 5m/15m desks; 300 s on the 1-hour desks (v1.27)
                        for method, lo, hi in (("edge", split, self.cfg.directional_secs_left), ("late", self.cfg.directional_min_secs, split)):
                            if want or method not in self.cfg.methods or self._taken(w.slug, method) or not (lo <= w.end - now <= hi):
                                continue
                            z = directional.cushion_z(self.tape.at(int(now)), w.price_to_beat, self.sigma, w.end - now)
                            pick = directional.method_pick(method, self.fair_model, b.best_ask, round(1.0 - b.best_bid, 6), z=z,
                                                           up_ok=can_up_m, down_ok=can_down_m, asset=self.cfg.asset, calib=self._calib_for(w))
                            if pick is None:
                                continue
                            side, cost, _, gap, lot_pct = pick
                            if self._entry_check(now, w, cost, self.momentum, side):
                                why = f"{method.title()}: skipped by entry rule, buy at {cost * 100:.0f}c."
                                continue
                            bets = self._bets.get(w.slug, {})
                            usd = min(budget_usd, equity * lot_pct / 100.0, exposure.headroom(equity, self._xacct()), directional.window_room(bets, equity, self.cfg.asset))
                            ok_win, why_win = directional.may_take(bets, side, usd, equity, self.cfg.asset)
                            if not ok_win:
                                why = f"{method.title()}: {why_win}."
                                continue
                            size_here = (b.offers[0][1] if b.offers else 0.0) if side == "up" else (b.bids[0][1] if b.bids else 0.0)
                            q = strategy._qty(usd, cost, w.min_qty)
                            q = min(q, round(size_here // w.min_qty * w.min_qty, 6))
                            if q >= w.min_qty:
                                price = b.best_ask if side == "up" else b.best_bid
                                want = [strategy.Quote(side, price, q, taker=True)]
                                self._place(w.slug, method, side, usd, q)
                                why = f"{method.title()}: {side} at {cost * 100:.0f}c, model {gap * 100:.0f} points over the price, lot {lot_pct:.0f}%."
                    if not want and not self._taken(w.slug, "directional") and self.cfg.directional:
                        z_now = directional.cushion_z(self.tape.at(int(now)), w.price_to_beat, self.sigma, w.end - now)
                        in_win = self.cfg.directional_min_secs <= w.end - now <= self.cfg.directional_secs_left
                        why_dir = directional.explain(self.fair_model, b.best_ask,
                                                      None if b.best_bid is None else round(1.0 - b.best_bid, 6),
                                                      self.shadow.bucket_stats(self.fair_model or 0.5),
                                                      self.shadow.bucket_stats(1.0 - (self.fair_model or 0.5)), z_now, in_win, asset=self.cfg.asset,
                                                      window=(self.cfg.directional_min_secs, self.cfg.directional_secs_left))
                        why = (why + " " if why else "") + why_dir + ("" if can_up_m and can_down_m else f" Momentum: {mom_why}")
                elif not want:
                    why = why or "Finishing the pair (one leg was left open)."
                if hedge is not None and lopsided and w.slug not in self._dir_pending:
                    want = want or strategy.quotes(self.fair, strategy.Book(b.best_bid, b.best_ask, w.tick, w.min_qty), inv, lim,
                                                   budget_left=budget, can_up=can_up, can_down=can_down, sizing=sizing,
                                                   hedge_loss=hedge, min_cost=self.cfg.min_buy_price, max_cost=self.cfg.max_buy_price)
            if self.cfg.sweep_pct > 0 and not self.cfg.pairs_only and w.end - now <= sweep_s and self.fair_model is not None:
                cap = min(max(0.0, equity) * self.cfg.sweep_pct / 100.0,
                          exposure.headroom(equity, self._xacct()))      # v1.28: sweeps count under the all-desk cap (20% since v1.33) too
                sw = strategy.sweep(self.fair_model, self.mid, strategy.Book(b.best_bid, b.best_ask, w.tick, w.min_qty), inv,
                                    cap_usd=cap, budget_left=budget, min_price=self.cfg.sweep_min_price,
                                    max_price=self.cfg.sweep_max_price, min_edge=self.cfg.sweep_min_edge,
                                    gap_bp=(None if not last or not w.price_to_beat else (last[1] - w.price_to_beat) / w.price_to_beat * 1e4),
                                    min_gap_bp=self.cfg.sweep_min_gap_bp)
                decisive = max(self.fair_model, 1.0 - self.fair_model) >= self.cfg.sweep_min_price + self.cfg.sweep_min_edge
                if sw is not None and sw.qty * sw.cost_per < 1.0:
                    sw = None                                         # v1.28: no room left under the all-desk cap
                if sw is not None:
                    want = [sw]
                    self._sweeps.add((w.slug, sw.side))
                    self._dir_pending.add(w.slug)                     # v1.28: a sweep is a one-sided bet: it counts in the all-desk tally
                    exposure.reserve(self.name, sw.qty * sw.cost_per, self._xacct())
                    if len(self._sweeps) > 50:
                        self._sweeps = set(list(self._sweeps)[-20:])
                    why = f"Closing sweep: buying {sw.side.upper()} at {sw.cost_per:.2f} (model {max(self.fair_model, 1 - self.fair_model):.0%})."
                elif decisive:
                    want = [q for q in want if (q.side == "up") == (self.fair_model >= 0.5)]    # never buy the likely loser now
            if want and getattr(self, "book_from_bbo", False):
                want = []                                             # best bid/offer has no sizes: wait for the full book to place anything
                why = (why + " " if why else "") + "(Prices from best bid/offer; waiting for the full book to size an order.)"
            if want:
                reason = "Quoting" + (f" (one side only: {why})" if why else ".")
            elif self.cfg.pairs_only and why:
                reason = why                                  # v1.18: say exactly why nothing is being bought
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

    def account_value(self) -> float:
        """Live: what the account is worth. Polymarket nets a matched Up + Down pair at once (each pair is worth $1 for sure,
        so that money is back in buying power straight away); only the unmatched part of a window is still out. Adding everything
        paid on top of buying power counted the pairs twice (6:08 PM: the Station showed $124.86 while the account was $116)."""
        unpaired = 0.0
        for w in self.open_windows.values():
            i = w.inv
            if i.up_qty > i.down_qty and i.up_qty > 0:
                unpaired += (i.up_qty - i.down_qty) * i.up_cost / i.up_qty
            elif i.down_qty > i.up_qty and i.down_qty > 0:
                unpaired += (i.down_qty - i.up_qty) * i.down_cost / i.down_qty
        return (self.balance or 0.0) + unpaired + self.external_at_risk

    def equity(self) -> float:
        """The account's value right now, for sizing: live = buying power + what is in open windows; paper = the simulated
        starting balance plus today's simulated P&L."""
        if self.cfg.mode == "live" and self.balance is not None:
            return round(max(0.0, self.account_value()), 2)
        start = self.rs.day_start if self.rs.day_start is not None else self.cfg.limits.bankroll_usd
        return round(max(0.0, start + self.rs.day_pnl), 2)

    def status(self, now: float) -> Dict[str, Any]:
        w, b = self.window, self.book
        last = self.tape.last()
        return {
            "version": VERSION, "heartbeat": now, "instance": self.instance, "stopped": self._stopping, "mode": self.cfg.mode, "allow_live": self.base.allow_live, "has_keys": bool(self.base.key_id and self.base.secret_key),
            "series": f"{self.base.coin.upper()} {self.horizon} Up/Down", "reason": self.reason,
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
            "equity": self.equity(), "window_pct": self.cfg.window_pct, "setups": self.setups.table()[:12], "unpaired_pct": self.cfg.unpaired_pct, "version_start": self.version_start, "deposits_start": self.deposits_start,
            "account_value": round(self.account_value(), 2) if self.cfg.mode == "live" and self.balance is not None else None,
            "window_cap": strategy.Sizing(equity=self.equity(), window_pct=self.cfg.window_pct).window_cap(self.cfg.limits),
            "hour_size": [[h, f] for h, f in self.cfg.hour_size],
            "balance": self.balance, "day": self.rs.day, "day_pnl": round(self.rs.day_pnl, 2),
            "day_start": self.rs.day_start, "day_limit": risk.day_limit(self.cfg, self.rs), "at_risk": round(self.rs.at_risk, 2),
            "today": dict(self.stats),
            "slug_prefix": self.slug_prefix, "slug_prefixes": dict(self._prefixes), "day_reset_seen": self._day_reset_seen,
            "open_windows": [{"slug": x.slug, "start": x.start, "end": x.end, "price_to_beat": x.price_to_beat, "tick": x.tick,
                              "min_qty": x.min_qty, "title": x.title, "mode": x.mode, "fills": dict(x.fills),
                              "up_qty": x.inv.up_qty, "up_cost": x.inv.up_cost, "down_qty": x.inv.down_qty,
                              "down_cost": x.inv.down_cost, "paid": round(x.inv.spent, 2)}
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
    async def load_setups(self) -> None:
        """v1.14: rebuild the per-setup pair record from the last three weeks of settled live trades (v1.12+, when hedged pairs began;
        XRP only from v1.14, as its earlier results were booked with the cent-rounding bug)."""
        fn = getattr(self.store, "pair_history", None)
        if fn is None:
            return
        try:
            rows = await fn(21)
        except Exception:
            logger.exception("pair history unreadable; learned sizing starts empty")
            return
        floor = "1.18"
        by: Dict[str, List[Dict[str, Any]]] = {}
        ups: Dict[str, bool] = {}
        for r in rows:
            if setups.version_at_least(r.get("version"), floor):
                by.setdefault(r["slug"], []).append(r)
                ups[r["slug"]] = bool(r["up"])
        for slug, fs in by.items():
            po = setups.pair_outcome(fs, ups[slug])
            if po is not None:
                self.setups.add(*po)
        n = sum(r.n for r in self.setups.rec.values())
        self.log("info", f"Learned pair sizing (shadow): {n} past pair windows filed under {len(self.setups.rec)} setups.")

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
        if prev.get("strategy") == brain.STRATEGY_VERSION and prev.get("deposits_start") is not None:
            self.deposits_start = float(prev["deposits_start"])
        self.restore_windows(prev.get("open_windows"), now)
        for p in (prev.get("slug_prefixes") or {}).values() if isinstance(prev.get("slug_prefixes"), dict) else []:
            if isinstance(p, str) and "-updown-" in p and not p.startswith("cpc"):     # saved cpc- addresses are retired: re-learn
                self._prefixes[p.rsplit("-updown-", 1)[1].rstrip("-")] = p
        if isinstance(prev.get("slug_prefix"), str) and prev["slug_prefix"].endswith(f"-updown-{self.horizon}-") and not prev["slug_prefix"].startswith("cpc"):
            self.slug_prefix = prev["slug_prefix"]
        self._day_reset_seen = prev.get("day_reset_seen")
        self._ready = True
        await self.load_setups()
        try:
            self.shadow.load((await self.store.get("shadow2")) or {})
        except Exception:
            logger.warning("shadow record unreadable; starting empty")
        if self.cfg.asset in shadow.LIVE_SEED:                     # v1.36: seed the live tally from the owner's settled live fills (once)
            self.shadow.live_seed(shadow.LIVE_SEED[self.cfg.asset])
        await self.load_index(now, backfill=True)
        self.log("info", f"Desk started in {self.cfg.mode.upper()} mode on {self.base.coin.upper()} {self.horizon} Up/Down.")
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

    async def cancel_own_orders(self) -> None:
        """Cancel this desk's open orders only: the other desks trade the same account."""
        try:
            for o in await self.c.open_orders():
                slug = str(o.get("marketSlug") or o.get("slug") or "")
                if f"-{self.base.coin}-updown-{self.horizon}-" in slug and o.get("id"):     # BTC 5m and BTC 15m never touch each other's orders
                    await self.c.cancel(str(o["id"]), slug)
        except PMError as exc:
            self.log("warn", f"Could not clear old orders: {exc}")

    def restore_windows(self, rows, now: float) -> None:
        """Windows that still held contracts when the last copy stopped (a deploy mid-window) are settled by this copy, so
        their result lands on the scoreboard instead of vanishing (the 4:20 PM window on Oct 29 was lost that way)."""
        for r in rows or []:
            try:
                if r.get("mode") not in (self.cfg.mode, self.lmode()) or r["slug"] in self.open_windows or now > float(r["end"]) + 3600:
                    continue
                w = Window(slug=r["slug"], start=float(r["start"]), end=float(r["end"]), price_to_beat=r.get("price_to_beat"),
                           tick=float(r.get("tick") or 0.01), min_qty=float(r.get("min_qty") or 0.01), title=r.get("title") or "",
                           mode=r["mode"], fills={k: int(v) for k, v in (r.get("fills") or {}).items()})
                w.inv.up_qty, w.inv.up_cost = float(r["up_qty"]), float(r["up_cost"])
                w.inv.down_qty, w.inv.down_cost = float(r["down_qty"]), float(r["down_cost"])
            except (KeyError, TypeError, ValueError):
                continue
            if w.inv.spent > 0:
                self.open_windows[w.slug] = w
                self.recent[w.slug] = w
                self.log("info", f"Picked up the {w.slug} window from the last run (${w.inv.spent:.2f} in it); it settles here.")

    def stop(self) -> None:
        self._stopping = True
