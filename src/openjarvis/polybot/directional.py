"""v1.18 confident directional trades (PAPER ONLY for now).

The model's number is only trusted as far as the calibration tally says it is. For each side the desk looks at the calibration
bucket its model confidence falls in:
  * proven: the bucket has at least PROVEN_N settled trades. Confidence = the pessimistic win rate (Wilson lower bound). The side
    is bought only if that confidence beats its cost plus taker fee by at least PROVEN_EDGE.
  * exploring: fewer trades than that. Confidence = the raw model number, bought only with a bigger edge (EXPLORE_EDGE), and at
    half size, so the bucket gathers results without much at stake.
Only one side is bought per window, and only in paper mode.
"""

from __future__ import annotations

from typing import Optional, Tuple

from openjarvis.polybot import setups
from openjarvis.polybot.strategy import taker_fee

PROVEN_N = 50
PROVEN_EDGE = 0.03
EXPLORE_EDGE = 0.05
MIN_CONF = 0.55                 # never buy a side the model itself calls less likely than not, however cheap it is


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
           z: Optional[float] = None) -> Optional[Tuple[str, float, str, float]]:
    """(side, cost per contract, tier, edge after fees) for the better side, or None.
    bucket_up / bucket_down: (trades, wins) in the calibration bucket of that side's model confidence."""
    best = None
    for side, prob, cost, (n, wins) in (("up", prob_up, up_cost, bucket_up), ("down", 1.0 - prob_up, down_cost, bucket_down)):
        if cost is None or not (0.05 <= cost <= 0.95) or prob < MIN_CONF:
            continue
        if z is not None and (z if side == "up" else -z) < CUSHION_Z:
            continue                                  # v1.18: the index is too close to the price to beat on this side
        if n >= PROVEN_N:
            conf, need, tier = setups.wilson_lb(wins, n), PROVEN_EDGE, "proven"
        else:
            conf, need, tier = prob, EXPLORE_EDGE, "exploring"
        edge = conf - cost - taker_fee(cost)
        if edge >= need and (best is None or edge > best[3]):
            best = (side, cost, tier, round(edge, 4))
    return best
