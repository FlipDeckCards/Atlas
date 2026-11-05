"""Shadow strategies: six fixed rulebooks (three per timeframe) that run beside the desk and only RECORD what they would have done.

They never place orders and are not shown on the Station. Each buys 1 contract per leg at the ask (plus the taker fee) and settles
like any window. Results are kept in the desk's key/value row 'shadow' (or 'shadow:<asset>'), readable in the database.

5-minute windows (desk btc):
  s5_late    10-60 s before the close: the model is 85%+ sure of a side, the book leans the same way, and the side costs 90c or less.
  s5_cheap   90-240 s before the close: buy a cheap side (20c or less) when the model prices it at least 8c above its ask.
  s5_book    60-180 s before the close: the book leans 65%+ one way, the model agrees at 60%+, and the ask is 45-65c with a 5c edge.

15-minute windows (desk btc15):
  s15_trend     180-600 s before the close: buy the side the last few seconds of spot movement favours, when its ask is 5c under fair.
  s15_distance  120-300 s before the close: the model is 80%+ sure and the side is at least 7c under fair.
  s15_vol       120-600 s before the close: the model is 70%+ sure under both the tighter and the wider volatility, and the ask is 5c under both.

Rules are fixed; nothing is tuned from the results inside a test period.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from openjarvis.polybot.strategy import taker_fee

MAKER_5M = "m5_pair"        # maker shadow, 5-minute: rest a bid on both sides under fair value and earn the spread
MAKER_15M = "m15_maker"     # maker shadow, 15-minute: rest a bid on the side the model favours, under fair value
MAKER_MIN_SECS = 60         # resting quotes are pulled in the last minute
GATE_5M = "s5_gate"         # gate shadow, 5-minute: buy only when the ask is 5c under the measured win rate of its confidence bucket
GATE_15M = "s15_gate"       # gate shadow, 15-minute: the same rule on 15-minute windows
GATE_MIN_TRADES = 30        # the bucket needs this many settled trades before the gate trusts it
GATE_MARGIN = 0.05
NAMES_5M = ("s5_late", "s5_cheap", "s5_book", MAKER_5M, GATE_5M)
NAMES_15M = ("s15_trend", "s15_distance", "s15_vol", MAKER_15M, GATE_15M)
NAMES = tuple(dict.fromkeys(NAMES_5M + NAMES_15M))


def names_for(window_secs: int) -> Tuple[str, ...]:
    """The shadows that belong to one timeframe."""
    return NAMES_15M if window_secs >= 900 else NAMES_5M


def decide(name: str, secs_left: float, fair_up: Optional[float], ask_up: Optional[float], ask_down: Optional[float],
           extra: Optional[Dict[str, Optional[float]]] = None) -> List[Tuple[str, float]]:
    """The legs this strategy would buy at this moment, as (side, price per contract); [] when it would not trade.
    extra: book_up (the book's Up view), fair_lo / fair_hi (the model at tighter / wider volatility), momentum (spot trend, sd)."""
    ex = extra or {}
    bu = ex.get("book_up")
    flo, fhi = ex.get("fair_lo"), ex.get("fair_hi")
    mom = ex.get("momentum")
    if fair_up is None:
        return []
    fd = 1.0 - fair_up
    if name == "s5_late":
        if not (10 <= secs_left <= 60) or bu is None:
            return []
        if fair_up >= 0.85 and bu >= 0.60 and ask_up is not None and ask_up <= 0.90:
            return [("up", ask_up)]
        if fd >= 0.85 and bu <= 0.40 and ask_down is not None and ask_down <= 0.90:
            return [("down", ask_down)]
        return []
    if name == "s5_cheap":
        if not (90 <= secs_left <= 240):
            return []
        if ask_up is not None and ask_up <= 0.20 and fair_up - ask_up >= 0.08:
            return [("up", ask_up)]
        if ask_down is not None and ask_down <= 0.20 and fd - ask_down >= 0.08:
            return [("down", ask_down)]
        return []
    if name == "s5_book":
        if not (60 <= secs_left <= 180) or bu is None:
            return []
        if bu >= 0.65 and fair_up >= 0.60 and ask_up is not None and 0.45 <= ask_up <= 0.65 and fair_up - ask_up >= 0.05:
            return [("up", ask_up)]
        if bu <= 0.35 and fd >= 0.60 and ask_down is not None and 0.45 <= ask_down <= 0.65 and fd - ask_down >= 0.05:
            return [("down", ask_down)]
        return []
    if name == "s15_trend":
        if not (180 <= secs_left <= 600) or mom is None:
            return []
        if mom >= 0.5 and ask_up is not None and ask_up <= 0.80 and fair_up - ask_up >= 0.05:
            return [("up", ask_up)]
        if mom <= -0.5 and ask_down is not None and ask_down <= 0.80 and fd - ask_down >= 0.05:
            return [("down", ask_down)]
        return []
    if name == "s15_distance":
        if not (120 <= secs_left <= 300):
            return []
        if fair_up >= 0.80 and ask_up is not None and fair_up - ask_up >= 0.07:
            return [("up", ask_up)]
        if fd >= 0.80 and ask_down is not None and fd - ask_down >= 0.07:
            return [("down", ask_down)]
        return []
    if name == "s15_vol":
        if not (120 <= secs_left <= 600) or flo is None or fhi is None:
            return []
        if min(flo, fhi) >= 0.70 and ask_up is not None and ask_up <= min(flo, fhi) - 0.05:
            return [("up", ask_up)]
        if min(1.0 - flo, 1.0 - fhi) >= 0.70 and ask_down is not None and ask_down <= min(1.0 - flo, 1.0 - fhi) - 0.05:
            return [("down", ask_down)]
        return []
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


# Sigma-tier sizing (shadow only, never traded): the lot as a % of the balance by the sigma at the window's first entry.
# Thresholds are the owner's plan from the 5m and 15m data; they stay fixed while the shadow runs.
SIGMA_TIERS = ((3.10, 10.0), (3.20, 8.0), (3.30, 7.0), (3.40, 5.0))


def sigma_pct(sigma: Optional[float]) -> float:
    """Lot % of the balance for a sigma (0 = no trade). Below 3.10 = 10%; 3.10-3.20 = 8%; 3.20-3.30 = 7%; 3.30-3.40 = 5%; 3.40 and up = no trade."""
    if sigma is None:
        return 0.0
    for limit, lot in SIGMA_TIERS:
        if sigma < limit:
            return lot
    return 0.0


class Shadow:
    def __init__(self, window_secs: int = 300) -> None:
        self.names = names_for(window_secs)
        self.calib: Dict[str, Dict[str, Dict[str, int]]] = {m: {lab: {"n": 0, "wins": 0} for _, _, lab in BUCKETS} for m in ("model", "book")}
        self.rec: Dict[str, Dict[str, float]] = {n: {"windows": 0, "wins": 0, "staked": 0.0, "profit": 0.0} for n in NAMES}
        self.open: Dict[str, Dict[str, List[Tuple[str, float]]]] = {}   # slug -> strategy -> legs taken
        self.done: Dict[str, set] = {}                                  # slug -> strategies that already traded this window
        self.maker_name = MAKER_15M if window_secs >= 900 else MAKER_5M
        self.window_secs = window_secs
        self.maker_quotes: Dict[str, Dict[str, float]] = {}   # slug -> side -> resting bid price
        self.maker_fills: Dict[str, Dict[str, float]] = {}    # slug -> side -> price a resting bid was filled at
        self.last_fair: Dict[str, Dict[str, Any]] = {}        # slug -> the latest fair values seen in the window (kept until settlement)
        self.log: List[Dict[str, Any]] = []                   # one row per settled window: the model, the tighter and wider volatility, the book, and the result
        self.sizing: Dict[str, float] = {"windows": 0, "sigma_pct": 0.0, "conf_pct": 0.0, "cal_pct": 0.0,
                                         "sigma_pts": 0.0, "conf_pts": 0.0, "cal_pts": 0.0}   # sizing on the same trades; pts = % of balance x return

    def tick(self, slug: str, secs_left: float, fair_up: Optional[float], ask_up: Optional[float], ask_down: Optional[float],
             extra: Optional[Dict[str, Optional[float]]] = None) -> None:
        for n in self.names:
            if n in self.done.get(slug, set()):
                continue
            legs = decide(n, secs_left, fair_up, ask_up, ask_down, extra)
            if legs:
                self.open.setdefault(slug, {})[n] = legs
                self.done.setdefault(slug, set()).add(n)

    def maker_tick(self, slug: str, secs_left: float, fair_up: Optional[float], ask_up: Optional[float],
                   ask_down: Optional[float]) -> None:
        """Maker shadow: post resting bids once per window, then count a bid as filled when the market sells down to it.
        Nothing is paid as a fee (a resting bid is a maker), and the rebate is not counted, so the result is conservative."""
        if fair_up is None:
            return
        if slug not in self.maker_quotes:
            q: Dict[str, float] = {}
            if self.maker_name == MAKER_5M and 150 <= secs_left <= 240:
                q = {"up": fair_up - 0.03, "down": (1.0 - fair_up) - 0.03}
            elif self.maker_name == MAKER_15M and 300 <= secs_left <= 600:
                side, p = ("up", fair_up) if fair_up >= 0.5 else ("down", 1.0 - fair_up)
                if p >= 0.60:
                    q = {side: p - 0.06}
            self.maker_quotes[slug] = {k: round(v, 2) for k, v in q.items() if v > 0.01}
            return
        if secs_left < MAKER_MIN_SECS:
            return
        fills = self.maker_fills.setdefault(slug, {})
        for side, price in self.maker_quotes[slug].items():
            ask = ask_up if side == "up" else ask_down
            if side not in fills and ask is not None and ask <= price:
                fills[side] = price

    def gate_tick(self, slug: str, secs_left: float, fair_up: Optional[float], ask_up: Optional[float],
                  ask_down: Optional[float]) -> None:
        """Gate shadow: buy a side only when its ask is at least GATE_MARGIN under the measured win rate of its confidence bucket."""
        name = GATE_15M if self.window_secs >= 900 else GATE_5M
        lo, hi = (120, 600) if self.window_secs >= 900 else (60, 240)
        if fair_up is None or not (lo <= secs_left <= hi) or slug in self.done.get(slug, set()) or name in self.done.get(slug, set()):
            return
        for side, conf, ask in (("up", fair_up, ask_up), ("down", 1.0 - fair_up, ask_down)):
            if ask is None or not (0.0 < conf < 1.0):
                continue
            n, wins = self.bucket_stats(conf)
            if n >= GATE_MIN_TRADES and wins / n - GATE_MARGIN >= ask:
                self.open.setdefault(slug, {})[name] = [(side, ask)]
                self.done.setdefault(slug, set()).add(name)
                return

    def record_fair(self, slug: str, secs_left: float, fair_up: Optional[float], fair_lo: Optional[float],
                    fair_hi: Optional[float], book_up: Optional[float]) -> None:
        """The fair values at the latest tick of the window, so settlement can log what each view said last."""
        self.last_fair[slug] = {"secs_left": round(float(secs_left), 1), "fair": fair_up, "lo": fair_lo, "hi": fair_hi, "book": book_up}

    def settle(self, slug: str, up: bool) -> None:
        last = self.last_fair.pop(slug, None)
        if last is not None:
            self.log.append({"slug": slug, "up": bool(up), **last})
            del self.log[:-600]                                # keep the most recent 600 windows
        filled = self.maker_fills.pop(slug, {})
        self.maker_quotes.pop(slug, None)
        if filled:
            staked = sum(filled.values())
            payout = sum(1.0 for side in filled if (side == "up") == bool(up))
            profit = payout - staked
            r = self.rec[self.maker_name]
            r["windows"] += 1
            r["wins"] += 1 if profit > 0 else 0
            r["staked"] = round(r["staked"] + staked, 4)
            r["profit"] = round(r["profit"] + profit, 4)
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

    def calibrated_conf(self, raw: Optional[float], prior_n: float = 20.0) -> Optional[float]:
        """The model's confidence corrected toward what its bucket has actually won: the bucket's win rate, shrunk toward the raw
        number so a thin bucket counts for little (prior_n trades' worth of weight on the raw value)."""
        if raw is None or not (0.0 <= raw < 1.01):
            return None
        n, wins = self.bucket_stats(raw)
        return round((wins + prior_n * raw) / (n + prior_n), 4)

    def add_sizing(self, slug: str, sigma: Optional[float], conf: Optional[float], conf_lot: float, cal_conf: Optional[float],
                   cal_lot: float, ret: float) -> None:
        """A traded window's return, sized three ways on the same trades: by the sigma tier, by the confidence tier the bot used,
        and by the same tiers applied to the calibrated confidence."""
        s_pct = sigma_pct(sigma)
        r = self.sizing
        r["windows"] += 1
        r["sigma_pct"] += s_pct
        r["conf_pct"] += conf_lot
        r["cal_pct"] += cal_lot
        r["sigma_pts"] = round(r["sigma_pts"] + s_pct * ret, 4)
        r["conf_pts"] = round(r["conf_pts"] + conf_lot * ret, 4)
        r["cal_pts"] = round(r["cal_pts"] + cal_lot * ret, 4)
        for row in reversed(self.log):
            if row.get("slug") == slug:
                row.update({"entry_sigma": sigma, "entry_conf": conf, "entry_cal_conf": cal_conf, "window_return": round(ret, 4),
                            "sigma_lot_pct": s_pct, "conf_lot_pct": conf_lot, "cal_lot_pct": cal_lot})
                break

    def to_dict(self) -> Dict[str, Any]:
        return {"rec": self.rec, "calib": self.calib, "log": self.log, "sizing": self.sizing}

    def load(self, d: Optional[Dict[str, Any]]) -> None:
        """Older saved results (the v1.15-v1.21 shadows) are dropped here: only the six current strategies are kept."""
        for n, r in ((d or {}).get("rec") or {}).items():
            if n in self.rec and isinstance(r, dict):
                self.rec[n] = {k: float(r.get(k, 0) or 0) for k in ("windows", "wins", "staked", "profit")}
        if isinstance((d or {}).get("sizing"), dict):
            self.sizing.update({k: float((d or {})["sizing"].get(k, 0) or 0) for k in self.sizing})
        if isinstance((d or {}).get("log"), list):
            self.log = [x for x in d["log"] if isinstance(x, dict)][-600:]
        for kind, rows in ((d or {}).get("calib") or {}).items():
            if kind in self.calib and isinstance(rows, dict):
                for lab, c in rows.items():
                    if lab in self.calib[kind] and isinstance(c, dict):
                        self.calib[kind][lab] = {"n": int(c.get("n", 0) or 0), "wins": int(c.get("wins", 0) or 0)}
