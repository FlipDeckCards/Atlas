"""When the desk must not quote. Every check returns a plain reason (shown on the Station and to Helix) or None."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo

from openjarvis.polybot.config import Config

ET = ZoneInfo("America/New_York")
LA = ZoneInfo("America/Los_Angeles")


def la_day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, LA).strftime("%Y-%m-%d")


def in_hours(ts: float, start: str, end: str) -> bool:
    """Inside the trading hours (Los Angeles time, "HH:MM"); an empty start or end means always."""
    if not start or not end:
        return True
    t = dt.datetime.fromtimestamp(ts, LA)
    m = t.hour * 60 + t.minute
    a, b = (int(x.split(":")[0]) * 60 + int(x.split(":")[1]) for x in (start, end))
    return a <= m < b if a < b else (m >= a or m < b)


def in_maintenance(ts: float) -> bool:
    """Polymarket US's weekly maintenance: Thursdays 6:00-8:00 AM Eastern (with 10 minutes' margin either side)."""
    t = dt.datetime.fromtimestamp(ts, ET)
    if t.weekday() != 3:
        return False
    minutes = t.hour * 60 + t.minute
    return 5 * 60 + 50 <= minutes < 8 * 60 + 10


@dataclass
class RiskState:
    day: str = ""
    day_pnl: float = 0.0            # settled P&L since midnight Los Angeles
    day_start: Optional[float] = None   # the balance the day started with (live: buying power + money in open windows; paper: bankroll)
    at_risk: float = 0.0            # paid into windows not yet settled
    resting: float = 0.0            # what resting orders would cost if all filled
    cooldown_until: float = 0.0
    errors_in_row: int = 0
    paused: bool = False
    kill: bool = False
    real_pnl: Optional[float] = None    # live: the whole account's change today (all desks share it), from Polymarket itself

    def roll(self, now: float) -> None:
        d = la_day(now)
        if d != self.day:
            self.day, self.day_pnl, self.day_start = d, 0.0, None


def day_limit(cfg: Config, rs: RiskState) -> float:
    """Today's loss limit in dollars: daily_loss_pct of the day's starting balance, or the flat daily_loss_usd when the % is 0."""
    lim = cfg.limits
    if lim.daily_loss_pct > 0:
        start = rs.day_start if rs.day_start is not None else lim.bankroll_usd
        return round(max(0.0, start) * lim.daily_loss_pct / 100.0, 2)
    return lim.daily_loss_usd


def blocked(cfg: Config, rs: RiskState, *, now: float, data_age_s: float, book_age_s: float, market_open: bool,
            secs_since_open: float, secs_to_end: float, balance: Optional[float]) -> Optional[str]:
    lim = cfg.limits
    if rs.kill:
        return "Kill switch is on."
    if rs.paused:
        return "Paused from Aibussol."
    dl = day_limit(cfg, rs)
    if cfg.mode == "live" and dl > 0 and rs.real_pnl is not None and rs.real_pnl <= -dl:      # the shared account: all desks stop
        return f"Daily loss limit reached on the account (${-rs.real_pnl:.2f} of ${dl:.2f}); back tomorrow."
    if cfg.mode == "live" and dl > 0 and rs.day_pnl <= -dl:          # paper keeps collecting data past the stop (it only notes it)
        return f"Daily loss limit reached (${-rs.day_pnl:.2f} of ${dl:.2f}); back tomorrow."
    if in_maintenance(now):
        return "Polymarket US weekly maintenance (Thursday 6-8 AM ET)."
    if rs.errors_in_row >= 5:
        return "Too many API errors in a row; waiting."
    if data_age_s > cfg.stale_s:
        return f"Index data is stale ({data_age_s:.0f}s old)."
    if book_age_s > cfg.stale_s + 2:
        return f"Order book is stale ({book_age_s:.0f}s old)."
    if not market_open:
        return "The current window is not open for trading."
    if now < rs.cooldown_until:
        return "Cooling down after a fast move in BTC."
    if secs_since_open < cfg.quote_after_open_s:
        return "Waiting for the window to settle in."
    if secs_to_end < cfg.stop_before_end_s:
        return "Too close to the close; holding to settlement."
    if lim.bankroll_usd <= 0:
        return "Bankroll is set to $0."
    if cfg.mode == "live" and balance is not None and balance < 1.0:
        return "Not enough buying power on Polymarket US."
    return None


def budget_left(cfg: Config, rs: RiskState, balance: Optional[float]) -> float:
    """How much more the desk may commit right now: bankroll minus what is at risk, minus room kept for the daily loss limit."""
    room = cfg.limits.bankroll_usd - rs.at_risk - rs.resting
    if cfg.mode == "live" and balance is not None:
        room = balance - 0.5                  # live: the bankroll is whatever is in the account (buying power left), not a fixed number
    if cfg.mode != "live":
        return max(0.0, room)
    loss_room = day_limit(cfg, rs) + rs.day_pnl - rs.at_risk - rs.resting     # never be able to lose past the daily limit
    return max(0.0, min(room, loss_room))
