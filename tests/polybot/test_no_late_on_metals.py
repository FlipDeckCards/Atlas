"""2026-11-27 (owner): gold and silver run no late (last-minute) bets; every other desk's methods are unchanged."""
from openjarvis.polybot.config import METHODS, from_env


def test_gold_and_silver_have_no_late_method():
    for a in ("gold", "silver"):
        assert "late" not in from_env({}, asset=a).methods
        assert "edge" in from_env({}, asset=a).methods
        assert "late" not in METHODS[a]


def test_other_desks_keep_their_methods():
    assert from_env({}, asset="btc").methods == ("pairs", "directional", "edge", "late")
    for a in ("xrp", "sol", "eth", "wti", "natgas"):
        assert from_env({}, asset=a).methods == ("pairs", "edge", "late")
    assert from_env({}, asset="btclate").methods == ("late",)
