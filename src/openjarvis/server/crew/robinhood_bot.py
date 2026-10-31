"""Robinhood crypto bot: risk limits, paper trading and the live-order gate. No network calls in this module.

Rules set by the owner:
  - each order risks at most 3% of the total account value;
  - the four coins together never have more than 12% of the account at risk at once;
  - paper trading first; live orders only when the owner approves each one (the Station's go-live button).
The live order path is not wired to Robinhood yet: it refuses to run and says why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

COINS = ("BTC", "ETH", "SOL", "XRP")
PER_ORDER_PCT = 3.0            # % of total account value per order
MAX_EXPOSURE_PCT = 12.0        # % of total account value at risk across all coins at once


class LiveTradingNotEnabled(Exception):
    """Raised when a live order is attempted without the live path or an owner approval."""


@dataclass
class Sizing:
    notional: float            # dollars to risk on this order (0 means do not trade)
    reason: str


def size_order(account_value: float, open_exposure: Dict[str, float], coin: str) -> Sizing:
    """How much to risk on a new order. open_exposure maps coin -> dollars already at risk."""
    if coin not in COINS:
        return Sizing(0.0, f"{coin} is not on the list")
    if account_value <= 0:
        return Sizing(0.0, "no account value")
    total_open = sum(max(0.0, v) for v in open_exposure.values())
    headroom = account_value * MAX_EXPOSURE_PCT / 100.0 - total_open
    if headroom <= 0:
        return Sizing(0.0, f"exposure cap reached ({MAX_EXPOSURE_PCT:g}% of the account)")
    per_order = account_value * PER_ORDER_PCT / 100.0
    notional = round(min(per_order, headroom), 2)
    return Sizing(notional, "per-order cap" if notional == round(per_order, 2) else "exposure headroom")


@dataclass
class PaperBook:
    """Simulated orders. Nothing here reaches Robinhood."""
    fills: List[Dict] = field(default_factory=list)

    def place(self, coin: str, side: str, notional: float, price: float) -> Dict:
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        if notional <= 0 or price <= 0:
            raise ValueError("notional and price must be positive")
        fill = {"coin": coin, "side": side, "notional": round(notional, 2), "price": price, "mode": "paper"}
        self.fills.append(fill)
        return fill

    def exposure(self) -> Dict[str, float]:
        out: Dict[str, float] = {c: 0.0 for c in COINS}
        for f in self.fills:
            sign = 1.0 if f["side"] == "buy" else -1.0
            out[f["coin"]] = round(out[f["coin"]] + sign * f["notional"], 2)
        return out


def place_live(order: Dict, *, owner_approved: bool, live_enabled: bool) -> None:
    """The only door to real money. Refuses unless the owner approved this order and live trading is switched on.
    Robinhood's order request is not implemented yet, so even an approved order is refused here."""
    if not live_enabled:
        raise LiveTradingNotEnabled("live trading is off; the bot is in paper mode")
    if not owner_approved:
        raise LiveTradingNotEnabled("this order has not been approved by the owner")
    raise LiveTradingNotEnabled("Robinhood order placement is not built yet")
