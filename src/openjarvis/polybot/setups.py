"""v1.14 learned pair sizing (SHADOW): which pair setups have earned a bigger bet.

A matched pair (Up + Down bought for under $1 together) wins whichever way the price goes; a pair loses only when its second
leg never fills or has to be finished at a loss. So each pair window is filed under the SETUP it opened in (what the book and
the price looked like when its first leg filled), and the desk keeps a record per setup: how many pair windows, how many made
money, what they made.

Sizing by setup is earned, not assumed: the size follows the PESSIMISTIC win rate (the Wilson lower bound at 90% confidence),
so 3 out of 3 wins proves nothing while 30 out of 30 does:
  * lower bound >= 90%                      -> 15% of the balance per window   ("proven")
  * lower bound >= 80%                      -> 12.5%                           ("strong")
  * 10+ windows and losing money or < 60%   -> 5%                              ("weak")
  * anything else                           -> the normal window size          ("unproven")
In shadow mode the desk only RECORDS the size each window would have got (fill features: setup, shadow_pct, actual_pct); it
still trades at the normal size. The Station compares what shadow sizing would have made with what actually happened.

Sweeps are not part of this: they stay at their own fixed cap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

Z90 = 1.645


def wilson_lb(wins: int, n: int, z: float = Z90) -> float:
    """The lowest win rate the evidence still supports (one-sided, ~90-95% confidence)."""
    if n <= 0:
        return 0.0
    p = wins / n
    d = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / d)


def pair_setup(side: str, f: Dict[str, Any]) -> Optional[str]:
    """The setup a pair window opened in, from the features of its FIRST (non-sweep) fill. None when the fill lacks the data."""
    spread, mz, secs = f.get("spread"), f.get("momentum_z"), f.get("secs_left")
    if spread is None or mz is None or secs is None:
        return None
    with_bet = float(mz) if side == "up" else -float(mz)                 # positive: the price was moving the way this leg bets
    move = "flat" if abs(with_bet) < 0.3 else ("with" if with_bet > 0 else "against")
    return f"{'tight' if float(spread) <= 0.02 else 'wide'}/{move}/{'early' if float(secs) > 180 else 'late'}"


LABELS = {"tight": "spread ≤2¢", "wide": "spread >2¢", "flat": "price flat", "with": "price moving with the first leg",
          "against": "price moving against the first leg", "early": "opened 3+ min before the close", "late": "opened in the last 3 min"}


def describe(key: str) -> str:
    return ", ".join(LABELS.get(p, p) for p in key.split("/"))


def pair_outcome(fills: Iterable[Dict[str, Any]], up: bool) -> Optional[Tuple[str, float]]:
    """(setup, pair P&L) for one settled window from its non-sweep fills (dicts with side, qty, cost, features), else None."""
    fs = [f for f in fills if not (f.get("features") or {}).get("sweep")]
    if not fs:
        return None
    key = pair_setup(fs[0]["side"], fs[0].get("features") or {})
    if key is None:
        return None
    pnl = sum(float(f["qty"]) * (1.0 if (f["side"] == "up") == bool(up) else 0.0) - float(f["cost"]) for f in fs)
    return key, round(pnl, 4)


@dataclass
class Record:
    n: int = 0
    wins: int = 0
    pnl: float = 0.0

    @property
    def lb(self) -> float:
        return wilson_lb(self.wins, self.n)


@dataclass
class SetupBook:
    proven_pct: float = 15.0
    strong_pct: float = 12.5
    weak_pct: float = 5.0
    rec: Dict[str, Record] = field(default_factory=dict)

    def add(self, key: str, pnl: float) -> None:
        r = self.rec.setdefault(key, Record())
        r.n += 1
        r.wins += 1 if pnl > 0 else 0
        r.pnl = round(r.pnl + pnl, 4)

    def size(self, key: Optional[str], base_pct: float) -> Tuple[float, str]:
        """(window % this setup has earned, tier name)."""
        r = self.rec.get(key or "")
        if r is None or r.n == 0:
            return base_pct, "unproven"
        if r.lb >= 0.90:
            return max(base_pct, self.proven_pct), "proven"
        if r.lb >= 0.80:
            return max(base_pct, self.strong_pct), "strong"
        if r.n >= 10 and (r.pnl < 0 or r.wins / r.n < 0.60):
            return min(base_pct, self.weak_pct), "weak"
        return base_pct, "unproven"

    def table(self) -> List[Dict[str, Any]]:
        return sorted(({"setup": k, "label": describe(k), "windows": r.n, "wins": r.wins, "win_rate": round(100.0 * r.wins / r.n, 1),
                        "floor": round(100.0 * r.lb, 1), "profit": round(r.pnl, 2)} for k, r in self.rec.items() if r.n),
                      key=lambda x: (-x["floor"], -x["windows"]))


def version_at_least(v: Optional[str], floor: str) -> bool:
    def parts(s: str) -> Tuple[int, ...]:
        try:
            return tuple(int(x) for x in str(s).split("."))
        except ValueError:
            return (0,)
    return parts(v or "1.0") >= parts(floor)
