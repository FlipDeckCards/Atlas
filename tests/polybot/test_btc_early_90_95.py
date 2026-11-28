"""2026-11-27 (owner): BTC 5m skips a bet with more than 3 minutes left when the model's raw confidence in the side is 90-95%.
Everything else (other timings, other confidences, other desks) is untouched."""
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def _desk(asset="btc", model=0.92):
    d = Desk(from_env({}, asset=asset), None, None)
    d.fair_model = model
    return d


def test_blocks_early_90_95_on_btc():
    d = _desk(model=0.92)
    assert d._block_rule(200, 0.80, 1.5, "up") == "btc early 90-95%"
    assert d._block_rule(181, 0.80, 1.5, "up") == "btc early 90-95%"
    assert d._block_rule(240, 0.80, 1.5, "up") == "btc early 90-95%"
    d = _desk(model=0.08)                                              # Down side: the model is 92% sure of Down
    assert d._block_rule(200, 0.80, -1.5, "down") == "btc early 90-95%"


def test_other_timings_and_confidences_are_allowed():
    d = _desk(model=0.92)
    assert d._block_rule(180, 0.80, 1.5, "up") is None                # exactly 3 minutes: allowed
    assert d._block_rule(120, 0.80, 1.5, "up") is None
    assert d._block_rule(90, 0.80, 1.5, "up") is None
    for m in (0.80, 0.899, 0.95, 0.97):
        assert _desk(model=m)._block_rule(200, 0.80, 1.5, "up") is None
    assert _desk(model=0.90)._block_rule(200, 0.80, 1.5, "up") == "btc early 90-95%"       # 90% is in
    assert _desk(model=0.9499)._block_rule(200, 0.80, 1.5, "up") == "btc early 90-95%"
    assert _desk(model=0.92)._block_rule(200, 0.80, 1.5, None) is None                      # no side given: no check


def test_no_model_no_block_and_other_desks_untouched():
    assert _desk(model=None)._block_rule(200, 0.80, 1.5, "up") is None
    for a in ("eth", "sol", "xrp", "btc15"):
        d = _desk(asset=a, model=0.92)
        assert d._block_rule(200, 0.90, 0.5, "up") != "btc early 90-95%"
