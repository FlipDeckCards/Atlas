"""v1.33: XRP's confidence is corrected toward its LIVE fills only, and the live tally survives a save and reload."""
from openjarvis.polybot import shadow


def test_live_fills_pull_a_high_bucket_down_and_paper_does_not():
    s = shadow.Shadow()
    raw = 0.93
    before = s.live_calibrated_conf(raw)
    assert before == raw                       # no live fills yet: the raw number stands
    for won in [True] * 8 + [False] * 6:       # 14 live fills in the 90+ bucket, 8 won
        s.live_add(raw, won)
    after = s.live_calibrated_conf(raw)
    assert after < raw and after > 0.6
    # paper-side calibration does not touch the live tally
    s.calib_add(raw, None, True)
    assert s.live_calibrated_conf(raw) == after


def test_live_tally_survives_save_and_reload():
    s = shadow.Shadow()
    for won in [True, False, False]:
        s.live_add(0.87, won)
    s2 = shadow.Shadow()
    s2.load(s.to_dict())
    assert s2.live_calib == s.live_calib
    assert s2.live_calibrated_conf(0.87) == s.live_calibrated_conf(0.87)


def test_every_desk_uses_live_fills_and_no_hour_is_raw():
    from openjarvis.polybot import directional
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    for a in ("btc", "btc15", "eth", "sol", "xrp"):
        d = Desk(from_env({}, asset=a), None, None)
        for won in [False] * 10:
            d.shadow.live_add(0.9, won)          # ten live losses in the 90+ bucket
        assert d._calib(0.9) < 0.9               # corrected toward the live result, for every desk
    assert not any(directional.raw_hour("btc", ts) for ts in range(0, 86400 * 2, 3600))


def test_live_seed_starts_the_tally_once_and_is_not_repeated():
    s = shadow.Shadow()
    s.live_seed(shadow.LIVE_SEED["btc"])
    assert s.live_calib["90+"] == {"n": 172, "wins": 136}
    assert s.live_calibrated_conf(0.93) < 0.93      # corrected from the start
    s.live_add(0.93, True)
    s.live_seed(shadow.LIVE_SEED["btc"])            # a second seed does nothing
    assert s.live_calib["90+"]["n"] == 173


def test_every_5m_desk_with_live_history_is_seeded():
    for a in ("btc", "btc15", "sol", "eth", "xrp"):
        s = shadow.Shadow()
        s.live_seed(shadow.LIVE_SEED[a])
        assert sum(c["n"] for c in s.live_calib.values()) > 0


def test_correction_is_full_strength():
    from openjarvis.polybot import desk as desk_mod
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    d = Desk(from_env({}, asset="btc"), None, None)
    d.shadow.live_seed(shadow.LIVE_SEED["btc"])
    full = d.shadow.live_calibrated_conf(0.93)
    assert desk_mod.CORRECTION_STRENGTH == 1.0
    assert abs(d._calib(0.93) - full) < 0.001
