"""v1.21: the four confidence pieces run as shadow strategies next to the three existing ones."""

from openjarvis.polybot import shadow


def test_seven_shadow_strategies_are_recorded():
    assert set(shadow.NAMES) == {"edge", "pairs", "late", "book", "imbalance", "vol_narrow", "vol_wide"}
    assert set(shadow.Shadow().rec) == set(shadow.NAMES)


def test_book_needs_model_and_book_to_agree():
    assert shadow.decide("book", 120, 0.85, 0.80, 0.20, {"book_up": 0.70}) == [("up", 0.80)]
    assert shadow.decide("book", 120, 0.85, 0.80, 0.20, {"book_up": 0.50}) == []        # book neutral
    assert shadow.decide("book", 30, 0.85, 0.80, 0.20, {"book_up": 0.70}) == []         # outside 60-240 s


def test_imbalance_follows_the_book_alone():
    assert shadow.decide("imbalance", 120, 0.50, 0.70, 0.35, {"book_up": 0.80}) == [("up", 0.70)]
    assert shadow.decide("imbalance", 120, 0.50, 0.70, 0.35, {"book_up": 0.20}) == [("down", 0.35)]
    assert shadow.decide("imbalance", 120, 0.50, 0.70, 0.35, {"book_up": None}) == []


def test_vol_variants_use_their_own_fair_value():
    assert shadow.decide("vol_narrow", 120, 0.5, 0.60, 0.40, {"fair_lo": 0.90}) == [("up", 0.60)]
    assert shadow.decide("vol_wide", 120, 0.5, 0.60, 0.40, {"fair_hi": 0.80}) == []      # not 85% under the wide view
    assert shadow.decide("vol_narrow", 120, 0.5, 0.60, 0.40, None) == []
