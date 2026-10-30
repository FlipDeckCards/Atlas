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


BUCKETS = ((0.0, 0.5, "under 50"), (0.5, 0.6, "50-59"), (0.6, 0.7, "60-69"), (0.7, 0.8, "70-79"), (0.8, 0.9, "80-89"), (0.9, 1.01, "90+"))


def book_up(bids: Optional[List[tuple]], offers: Optional[List[tuple]], levels: int = 5) -> Optional[float]:
    """The order book's own view that Up wins: the share of resting size on the bid side, over the top `levels` levels.
    A heavy bid side (buyers of Up waiting) reads as more likely Up. None when the book is empty on either side."""
    b = sum(float(q) for _, q in (bids or [])[:levels])
    o = sum(float(q) for _, q in (offers or [])[:levels])
    if b <= 0 or o <= 0:
        return None
    return round(min(0.999, max(0.001, b / (b + o))), 4)


SCALP_ENTRY = (0.60, 0.68)      # v1.18 shadow scalp: buy a side priced 60-68c that is trending its way (momentum 0.5 sd+) ...
SCALP_MOM = 0.5
SCALP_TARGET = 0.15             # ... sell the moment its bid reaches entry + 15c (target), or cut at entry - 15c (stop)
SCALP_MIN_SECS = 60             # no new entries in the last minute


class Shadow:
    def __init__(self) -> None:
        self.scalp_stats: Dict[str, float] = {"entries": 0, "target": 0, "stop": 0, "settled": 0, "wins": 0, "profit": 0.0, "staked": 0.0}
        self.scalp_open: Dict[str, Dict[str, Dict[str, float]]] = {}     # slug -> side -> {entry, cost}
        self.scalp_done: Dict[str, set] = {}                              # slug -> sides already entered this window
        self.calib: Dict[str, Dict[str, Dict[str, int]]] = {m: {lab: {"n": 0, "wins": 0} for _, _, lab in BUCKETS} for m in ("model", "book")}
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

    def calib_add(self, model_conf: Optional[float], book_conf: Optional[float], won: bool) -> None:
        """One fill: the confidence the model gave the side it bought, and the book's, against whether that side won."""
        for kind, c in (("model", model_conf), ("book", book_conf)):
            if c is None:
                continue
            lab = next(l for lo, hi, l in BUCKETS if lo <= c < hi)
            cell = self.calib[kind][lab]
            cell["n"] += 1
            cell["wins"] += 1 if won else 0

    def _scalp_close(self, slug: str, side: str, pos: Dict[str, float], proceeds: float, how: str) -> None:
        pnl = round(proceeds - pos["cost"], 4)
        st = self.scalp_stats
        st[how] += 1
        st["profit"] = round(st["profit"] + pnl, 4)
        st["staked"] = round(st["staked"] + pos["cost"], 4)
        st["wins"] += 1 if pnl > 0 else 0
        self.scalp_open.get(slug, {}).pop(side, None)

    def scalp_tick(self, slug: str, secs_left: float, up_bid: Optional[float], up_ask: Optional[float], momentum_z: float) -> None:
        """One look at the book: enter a side that is cheap and trending its way; exit it at target or stop as soon as the bid says so."""
        if up_bid is None or up_ask is None:
            return
        sides = {"up": (up_ask, up_bid, momentum_z), "down": (1.0 - up_bid, 1.0 - up_ask, -momentum_z)}   # (buy price, sell price, trend)
        for side, (buy, sell, mom) in sides.items():
            pos = self.scalp_open.get(slug, {}).get(side)
            if pos is not None:
                if sell >= pos["entry"] + SCALP_TARGET:
                    self._scalp_close(slug, side, pos, sell - taker_fee(sell), "target")
                elif sell <= pos["entry"] - SCALP_TARGET:
                    self._scalp_close(slug, side, pos, sell - taker_fee(sell), "stop")
            elif (secs_left >= SCALP_MIN_SECS and SCALP_ENTRY[0] <= buy <= SCALP_ENTRY[1] and mom >= SCALP_MOM
                  and side not in self.scalp_done.get(slug, set())):
                self.scalp_open.setdefault(slug, {})[side] = {"entry": buy, "cost": buy + taker_fee(buy)}
                self.scalp_done.setdefault(slug, set()).add(side)
                self.scalp_stats["entries"] += 1

    def settle_scalp(self, slug: str, up: bool) -> None:
        """Positions still open at the close are held to settlement: the side pays $1 or nothing."""
        for side, pos in self.scalp_open.pop(slug, {}).items():
            won = (side == "up") == bool(up)
            self.scalp_stats["settled"] += 1
            pnl = round((1.0 if won else 0.0) - pos["cost"], 4)
            self.scalp_stats["profit"] = round(self.scalp_stats["profit"] + pnl, 4)
            self.scalp_stats["staked"] = round(self.scalp_stats["staked"] + pos["cost"], 4)
            self.scalp_stats["wins"] += 1 if pnl > 0 else 0
        self.scalp_done.pop(slug, None)

    def bucket_stats(self, prob: float) -> Tuple[int, int]:
        """(trades, wins) of the calibration bucket that a model confidence falls in."""
        lab = next(l for lo, hi, l in BUCKETS if lo <= prob < hi) if 0.0 <= prob < 1.01 else "90+"
        c = self.calib["model"][lab]
        return c["n"], c["wins"]

    def add_result(self, name: str, profit: float, staked: float) -> None:
        r = self.rec.setdefault(name, {"windows": 0, "wins": 0, "staked": 0.0, "profit": 0.0})
        r["windows"] += 1
        r["wins"] += 1 if profit > 0 else 0
        r["staked"] = round(r["staked"] + staked, 4)
        r["profit"] = round(r["profit"] + profit, 4)

    def calib_table(self) -> Dict[str, List[Dict[str, Any]]]:
        return {kind: [{"bucket": lab, "trades": c["n"], "won": c["wins"],
                        "win_rate": round(100.0 * c["wins"] / c["n"], 1) if c["n"] else None}
                       for _, _, lab in BUCKETS for c in [self.calib[kind][lab]]] for kind in ("model", "book")}

    def to_dict(self) -> Dict[str, Any]:
        return {"rec": self.rec, "calib": self.calib, "scalp": self.scalp_stats}

    def load(self, d: Optional[Dict[str, Any]]) -> None:
        for n, r in ((d or {}).get("rec") or {}).items():
            if n in self.rec and isinstance(r, dict):
                self.rec[n] = {k: float(r.get(k, 0) or 0) for k in ("windows", "wins", "staked", "profit")}
        for k, v in ((d or {}).get("scalp") or {}).items():
            if k in self.scalp_stats:
                self.scalp_stats[k] = float(v or 0)
        for kind, rows in ((d or {}).get("calib") or {}).items():
            if kind in self.calib and isinstance(rows, dict):
                for lab, c in rows.items():
                    if lab in self.calib[kind] and isinstance(c, dict):
                        self.calib[kind][lab] = {"n": int(c.get("n", 0) or 0), "wins": int(c.get("wins", 0) or 0)}
