"""v1.15 shadow strategies: three fixed rulebooks that run beside the desk and only RECORD what they would have done.

They never place orders and are not shown on the Station. Each one buys 1 contract per leg at the ask (plus the taker fee) and is
settled like any window, so the profit per dollar staked can be compared across the three. Results are kept in the desk's
key/value row 'shadow' (or 'shadow:<asset>'), readable in the database.

  edge   one tick per window, 60-240 s before the close: buy the side the model prices at least 6c above its ask.
  pairs  once per window, from 90 s before the close: buy both sides when their asks total at most 97c.
  late   once per window, in the last 60 s: buy the likely side when the model is at least 90% sure and it costs 95c or less.

Rules are fixed; nothing is tuned from the results inside a test period.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from openjarvis.polybot.strategy import taker_fee

NAMES = ("edge", "pairs", "late")


def decide(name: str, secs_left: float, fair_up: Optional[float], ask_up: Optional[float],
           ask_down: Optional[float]) -> List[Tuple[str, float]]:
    """The legs this strategy would buy at this moment, as (side, price per contract); [] when it would not trade."""
    if name == "edge":
        if fair_up is None or not (60 <= secs_left <= 240):
            return []
        if ask_up is not None and fair_up - ask_up >= 0.06:
            return [("up", ask_up)]
        if ask_down is not None and (1.0 - fair_up) - ask_down >= 0.06:
            return [("down", ask_down)]
        return []
    if name == "pairs":
        if ask_up is None or ask_down is None or secs_left < 90:
            return []
        return [("up", ask_up), ("down", ask_down)] if ask_up + ask_down <= 0.97 else []
    if name == "late":
        if fair_up is None or secs_left > 60:
            return []
        if fair_up >= 0.90 and ask_up is not None and ask_up <= 0.95:
            return [("up", ask_up)]
        if fair_up <= 0.10 and ask_down is not None and ask_down <= 0.95:
            return [("down", ask_down)]
    return []


class Shadow:
    def __init__(self) -> None:
        self.rec: Dict[str, Dict[str, float]] = {n: {"windows": 0, "wins": 0, "staked": 0.0, "profit": 0.0} for n in NAMES}
        self.open: Dict[str, Dict[str, List[Tuple[str, float]]]] = {}   # slug -> strategy -> legs taken
        self.done: Dict[str, set] = {}                                  # slug -> strategies that already traded this window

    def tick(self, slug: str, secs_left: float, fair_up: Optional[float], ask_up: Optional[float], ask_down: Optional[float]) -> None:
        for n in NAMES:
            if n in self.done.get(slug, set()):
                continue
            legs = decide(n, secs_left, fair_up, ask_up, ask_down)
            if legs:
                self.open.setdefault(slug, {})[n] = legs
                self.done.setdefault(slug, set()).add(n)

    def settle(self, slug: str, up: bool) -> None:
        for n, legs in self.open.pop(slug, {}).items():
            staked = sum(p + taker_fee(p) for _, p in legs)
            payout = sum(1.0 for side, _ in legs if (side == "up") == bool(up))
            profit = payout - staked
            r = self.rec[n]
            r["windows"] += 1
            r["wins"] += 1 if profit > 0 else 0
            r["staked"] = round(r["staked"] + staked, 4)
            r["profit"] = round(r["profit"] + profit, 4)
        self.done.pop(slug, None)

    def to_dict(self) -> Dict[str, Any]:
        return {"rec": self.rec}

    def load(self, d: Optional[Dict[str, Any]]) -> None:
        for n, r in ((d or {}).get("rec") or {}).items():
            if n in self.rec and isinstance(r, dict):
                self.rec[n] = {k: float(r.get(k, 0) or 0) for k in ("windows", "wins", "staked", "profit")}
