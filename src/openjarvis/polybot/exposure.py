"""Cross-desk cap on directional exposure: the four desks run in one process, so one shared tally covers them all.

The owner's rule: the directional bets of all the desks together never have more than CROSS_DESK_PCT of the account at risk.
Each desk publishes the dollars it has in directional windows every tick; a new directional order is cut down to the headroom.
Pairs are not capped here (they keep their own rules).
"""

from __future__ import annotations

from typing import Dict

CROSS_DESK_PCT = 25.0          # % of the account value, across every desk's directional trades together

_open: Dict[str, float] = {}   # desk name -> dollars at risk in its directional windows


def _k(desk_name: str, account: str = "") -> str:
    """Each account's desks share one tally (one Polymarket account); a subscriber's never count against the owner's."""
    return f"{account}|{desk_name}" if account else desk_name


def _acct(key: str) -> str:
    return key.split("|", 1)[0] if "|" in key else ""


def publish(desk_name: str, usd: float, account: str = "") -> None:
    """A desk reports what it has at risk in directional windows right now (overwrites the last report)."""
    _open[_k(desk_name, account)] = max(0.0, float(usd))


def reserve(desk_name: str, usd: float, account: str = "") -> None:
    """Count an order just placed, before the next tick reports the real figure."""
    k = _k(desk_name, account)
    _open[k] = _open.get(k, 0.0) + max(0.0, float(usd))


def total_open(account: str = "") -> float:
    return round(sum(v for k, v in _open.items() if _acct(k) == (account or "")), 2)


def headroom(equity: float, account: str = "") -> float:
    """Dollars left under the cross-desk cap for a new directional order, from every desk of this account together."""
    cap = max(0.0, float(equity)) * CROSS_DESK_PCT / 100.0
    return round(max(0.0, cap - total_open(account)), 2)


def reset() -> None:
    """For tests."""
    _open.clear()
