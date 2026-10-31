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

from typing import Optional, Tuple

from openjarvis.polybot import setups
from openjarvis.polybot.strategy import taker_fee

PROVEN_N = 50
PROVEN_EDGE = 0.03
EXPLORE_EDGE = 0.05
# v1.20, the owner's direction plan: lot % of the balance by the model's confidence, per coin (floor of the percentage).
# BTC and XRP: 80-84 = 5%, 85-89 = 7%, 90-94 = 8%, 95%+ = 10%; under 80 = no trade.
# ETH and SOL: trade only at 95%+, at 1% of the balance for now (the owner is still collecting data).
TIERS = {
    "btc": ((95, 10.0), (90, 8.0), (85, 7.0), (80, 5.0)),
    "xrp": ((95, 10.0), (90, 8.0), (85, 7.0), (80, 5.0)),
    "eth": ((95, 1.0),),
    "sol": ((95, 1.0),),
}


def min_conf(asset: str = "btc") -> float:
    """The lowest model confidence this coin will trade at (no trade below it)."""
    tiers = TIERS.get(asset)
    return min(floor for floor, _ in tiers) / 100.0 if tiers else 1.0


def size_pct(prob: float, asset: str = "btc") -> float:
    """Lot size as a % of the balance for this coin and the model's confidence (0 = no trade). The percentage is floored: 84.6% counts as 84%."""
    pct = int(prob * 100 + 1e-9)
    for floor, lot in TIERS.get(asset, ()):
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
           asset: str = "btc") -> Optional[Tuple[str, float, str, float, float]]:
    """(side, cost per contract, tier, edge after fees, lot % of balance) for the better side, or None.
    bucket_up / bucket_down: (trades, wins) in the calibration bucket of that side's model confidence."""
    best = None
    for side, prob, cost, (n, wins) in (("up", prob_up, up_cost, bucket_up), ("down", 1.0 - prob_up, down_cost, bucket_down)):
        if cost is None or not (0.05 <= cost <= 0.95) or prob < min_conf(asset):
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
        pct = size_pct(prob, asset)
        if edge >= need and pct > 0 and (best is None or edge > best[3]):
            best = (side, cost, tier, round(edge, 4), pct)
    return best


def explain(prob_up: Optional[float], up_cost: Optional[float], down_cost: Optional[float], bucket_up: Tuple[int, int],
            bucket_down: Tuple[int, int], z: Optional[float], in_window: bool, asset: str = "btc") -> str:
    """Plain words for why the directional rule did not trade this moment (shown on the desk status)."""
    if not in_window:
        return "Directional: outside its 30-240 second window."
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
