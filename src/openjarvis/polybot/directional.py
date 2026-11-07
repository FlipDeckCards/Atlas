"""v1.18 confident directional trades (live or paper).

The model's number is only trusted as far as the calibration tally says it is. For each side the desk looks at the calibration
bucket its model confidence falls in:
  * proven: the bucket has at least PROVEN_N settled trades. Confidence = the pessimistic win rate (Wilson lower bound). The side
    is bought only if that confidence beats its cost plus taker fee by at least PROVEN_EDGE.
  * exploring: fewer trades than that. Confidence = the raw model number, bought only with a bigger edge (EXPLORE_EDGE), and at
    half size, so the bucket gathers results without much at stake.
Only one side is bought per window. Each coin has its own confidence tiers and lot sizes (TIERS); below a coin's first tier there is no trade.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

from openjarvis.polybot import setups
from openjarvis.polybot.strategy import taker_fee

PROVEN_N = 50
PROVEN_EDGE = 0.03
# Hour switch (owner, 2026-11-05): in these Los Angeles hours (window open) the bot trades on the raw model; in every other hour
# on the calibrated model. Green hours from each bot's hourly results. Reviewed every Friday; not changed in between.
RAW_HOURS = {
    "btc": frozenset({0, 1, 3, 4, 5, 10, 11, 12, 13, 15, 18, 19, 20, 21, 22}),        # BTC 5m
    "btc15": frozenset({0, 1, 2, 3, 4, 5, 6, 9, 10, 12, 14, 16, 17, 18, 20, 22}),     # BTC 15m
}


def _tiers(asset: str):
    """A desk's tiers, or its coin's (eth15 and eth1h trade like ETH 5m; btc1h like BTC)."""
    from openjarvis.polybot.config import coin_of
    a = (asset or "btc").lower()
    return TIERS.get(a) or TIERS.get(coin_of(a))


def raw_hour(asset: str, ts: float) -> bool:
    """True when a window opening at ts trades on the raw model for this bot (its hour is green)."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    return dt.datetime.fromtimestamp(ts, ZoneInfo("America/Los_Angeles")).hour in RAW_HOURS.get((asset or "btc").lower(), frozenset())
EXPLORE_EDGE = 0.05
# v1.20, the owner's direction plan: lot % of the balance by the model's confidence, per coin (floor of the percentage).
# BTC: 80-84 = 5%, 85-89 = 7%, 90-94 = 8%, 95%+ = 10%; under 80 = no trade.
# XRP: same as BTC but the floor is 90% (owner, 2026-10-31, after an 88% loss): 90-94 = 8%, 95%+ = 10%; under 90 = no trade.
# The four methods (directional, edge, late, sweep) use the same floor and lot sizes for XRP as for BTC.
# ETH: trade only at 95%+, at 1% of the balance for now (the owner is still collecting data).
# SOL: trade at 90%+ (owner, 2026-10-31; was 95%), still 1% of the balance.
TIERS = {
    "btc": ((95, 10.0), (90, 8.0), (85, 7.0), (80, 5.0)),
    "btc15": ((95, 10.0), (90, 8.0), (85, 7.0), (80, 5.0)),
    "btclate": ((95, 10.0), (90, 8.0), (85, 7.0), (80, 5.0)),      # v1.23: the late-only paper desk on BTC 5m: the same tiers      # BTC's 15-minute desk: the same tiers as BTC 5m
    "xrp": ((90, 1.0),),                       # owner, 2026-11-07: 1% (was 8-10%) while XRP builds a track record
    "eth": ((95, 1.0),),
    "sol": ((90, 1.0),),
}


# Calibrated tiers (owner, 2026-11-05): when a bot trades on the calibrated confidence (every hour outside its green hours), the lot is
# set by the win rate that confidence has actually shown, not by the raw number. Raw-confidence tiers (TIERS) are unchanged.
CAL_TIERS = ((88, 10.0), (84, 8.0), (80, 7.0), (77, 5.0))
CAL_ASSETS = ("btc", "btc15", "btclate")


def min_conf(asset: str = "btc", calibrated: bool = False) -> float:
    """The lowest model confidence this coin will trade at (no trade below it)."""
    if calibrated and asset in CAL_ASSETS:
        return min(floor for floor, _ in CAL_TIERS) / 100.0
    tiers = _tiers(asset)
    return min(floor for floor, _ in tiers) / 100.0 if tiers else 1.0


def size_pct(prob: float, asset: str = "btc", calibrated: bool = False) -> float:
    """Lot size as a % of the balance for this coin and the model's confidence (0 = no trade). The percentage is floored: 84.6% counts as 84%."""
    pct = int(prob * 100 + 1e-9)
    tiers = CAL_TIERS if (calibrated and asset in CAL_ASSETS) else (_tiers(asset) or ())
    for floor, lot in tiers:
        if pct >= floor:
            return lot
    return 0.0


def open_sides(fair_up: Optional[float], asset: str = "btc") -> Tuple[bool, bool]:
    """v1.21: which sides a fresh window may open a single leg on. A side opens only when the model's confidence in it
    meets this coin's tier floor (the same gate as a directional trade). Both False when the model is missing."""
    if fair_up is None:
        return False, False
    return size_pct(fair_up, asset) > 0, size_pct(1.0 - fair_up, asset) > 0


CUSHION_Z = 0.5
# v1.22: each method (directional, edge, late) may place ONE bet per window. Edge and Late are extra triggers with their own
# entry rules; they keep the same confidence floor, lot tier, cushion and momentum filters as directional. Every method's bets in
# a window together stay under WINDOW_TOTAL_PCT of the balance, and no method may bet against a side already bet in that window.
WINDOW_TOTAL_PCT = 20.0
EDGE_GAP = 0.04          # edge: the model's odds beat the ask by at least 4 cents (owner, 2026-10-31; was 6)
LATE_PROB = 0.90         # late: the model is at least 90% sure
LATE_MAX_COST = 0.95     # ... and the side costs 95 cents or less


def method_pick(method: str, prob_up: float, up_cost: Optional[float], down_cost: Optional[float], z: Optional[float] = None,
                up_ok: bool = True, down_ok: bool = True, asset: str = "btc",
                calib: Optional[Callable[[float], float]] = None) -> Optional[Tuple[str, float, str, float, float]]:
    """(side, cost, method, edge, lot % of balance) for an edge or late bet, or None. The caller checks the window timing.
    calib (live calibration): maps a side's raw model confidence to what that confidence has actually won; the floor and lot use it."""
    if method not in ("edge", "late"):
        return None
    best = None
    for side, prob, cost, ok in (("up", prob_up, up_cost, up_ok), ("down", 1.0 - prob_up, down_cost, down_ok)):
        if calib is not None:
            prob = calib(prob)
        if cost is None or not (0.05 <= cost <= 0.95) or prob < min_conf(asset, calib is not None) or not ok:
            continue
        if z is not None and (z if side == "up" else -z) < CUSHION_Z:
            continue
        pct = size_pct(prob, asset, calibrated=calib is not None)
        if pct <= 0:
            continue
        if method == "edge":
            if prob - cost < EDGE_GAP:
                continue
        elif prob < LATE_PROB or cost > LATE_MAX_COST:
            continue
        gap = round(prob - cost, 4)
        if best is None or gap > best[3]:
            best = (side, cost, method, gap, pct)
    return best


def may_take(bets: dict, side: str, usd: float, equity: float) -> Tuple[bool, str]:
    """Whether a new bet may go into this window: bets is {method: (side, usd, qty)} already placed in the window."""
    if bets and side not in {s for s, _, _ in bets.values()}:
        return False, "the other side is already bet in this window: not betting against it"
    room = max(0.0, float(equity)) * WINDOW_TOTAL_PCT / 100.0 - sum(u for _, u, _ in bets.values())
    if usd <= 0 or room <= 0 or usd > room + 1e-9:
        return False, f"the window's {WINDOW_TOTAL_PCT:.0f}% total in bets has no room for this one"
    return True, ""


def window_room(bets: dict, equity: float) -> float:
    """Dollars a new bet may still put into this window across every method (v1.22 per-window total)."""
    return round(max(0.0, max(0.0, float(equity)) * WINDOW_TOTAL_PCT / 100.0 - sum(u for _, u, _ in bets.values())), 2)


def cushion_z(index: Optional[float], beat: Optional[float], sigma_per_s: Optional[float], secs_left: float) -> Optional[float]:
    """How far the index sits past the price to beat, in standard deviations of the move still to come (positive = on Up's side).
    None when the inputs are missing."""
    if index is None or beat is None or not sigma_per_s or sigma_per_s <= 0:
        return None
    remaining = sigma_per_s * max(1.0, secs_left) ** 0.5
    return (index - beat) / remaining


def decide(prob_up: float, up_cost: Optional[float], down_cost: Optional[float],
           bucket_up: Tuple[int, int], bucket_down: Tuple[int, int],
           z: Optional[float] = None, up_ok: bool = True, down_ok: bool = True,
           asset: str = "btc", calib: Optional[Callable[[float], float]] = None) -> Optional[Tuple[str, float, str, float, float]]:
    """(side, cost per contract, tier, edge after fees, lot % of balance) for the better side, or None.
    bucket_up / bucket_down: (trades, wins) in the calibration bucket of that side's model confidence."""
    best = None
    for side, prob, cost, (n, wins) in (("up", prob_up, up_cost, bucket_up), ("down", 1.0 - prob_up, down_cost, bucket_down)):
        if calib is not None:
            prob = calib(prob)                        # live calibration: the confidence the bot trusts is what the bucket has actually won
        if cost is None or not (0.05 <= cost <= 0.95) or prob < min_conf(asset, calib is not None):
            continue
        if z is not None and (z if side == "up" else -z) < CUSHION_Z:
            continue                                  # v1.18: the index is too close to the price to beat on this side
        if not (up_ok if side == "up" else down_ok):
            continue                                  # v1.18: the recent move is against this side (momentum rule)
        if n >= PROVEN_N:
            conf, need, tier = setups.wilson_lb(wins, n), PROVEN_EDGE, "proven"
        else:
            conf, need, tier = prob, EXPLORE_EDGE, "exploring"
        edge = conf - cost - taker_fee(cost)
        pct = size_pct(prob, asset, calibrated=calib is not None)
        if edge >= need and pct > 0 and (best is None or edge > best[3]):
            best = (side, cost, tier, round(edge, 4), pct)
    return best


def explain(prob_up: Optional[float], up_cost: Optional[float], down_cost: Optional[float], bucket_up: Tuple[int, int],
            bucket_down: Tuple[int, int], z: Optional[float], in_window: bool, asset: str = "btc",
            window: Tuple[float, float] = (30.0, 240.0)) -> str:
    """Plain words for why the directional rule did not trade this moment (shown on the desk status)."""
    if not in_window:
        lo, hi = window
        if hi >= 600:
            return f"Directional: outside its {lo / 60:.0f}-{hi / 60:.0f} minute window."
        return f"Directional: outside its {lo:.0f}-{hi:.0f} second window."
    if prob_up is None:
        return "Directional: no model reading yet."
    parts = []
    for side, prob, cost, (n, wins) in (("Up", prob_up, up_cost, bucket_up), ("Down", 1.0 - prob_up, down_cost, bucket_down)):
        if cost is None:
            parts.append(f"{side}: no price")
        elif not (0.05 <= cost <= 0.95):
            parts.append(f"{side}: costs {cost * 100:.0f}c, outside 5-95c")
        elif prob < min_conf(asset):
            parts.append(f"{side}: model {prob * 100:.0f}% is under {min_conf(asset) * 100:.0f}%")
        elif z is not None and (z if side == "Up" else -z) < CUSHION_Z:
            parts.append(f"{side}: index too close to the beat ({(z if side == 'Up' else -z):.2f} sd, needs 0.5)")
        else:
            conf, need = (setups.wilson_lb(wins, n), PROVEN_EDGE) if n >= PROVEN_N else (prob, EXPLORE_EDGE)
            edge = conf - cost - taker_fee(cost)
            parts.append(f"{side}: edge {edge * 100:.1f}c after fees, needs {need * 100:.0f}c" if edge < need else f"{side}: passes")
    return "Directional: " + "; ".join(parts) + "."
