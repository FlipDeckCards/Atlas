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
