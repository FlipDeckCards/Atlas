import datetime as dt

from openjarvis.polybot import risk
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


class _W:
    def __init__(self, start):
        self.start, self.end, self.slug = start, start + 300, f"w{start}"


def _window_at(hour):
    day = dt.datetime(2026, 11, 12, hour, 0, tzinfo=risk.LA)
    return _W(day.timestamp())


def test_caution_hour_needs_85_calibrated_and_70c():
    d = Desk(from_env({}, asset="btc"), None, None)
    d.shadow.live_seed({"80-89": (170, 128), "90+": (174, 138)})
    w7, w10 = _window_at(7), _window_at(10)
    assert d._caution(w7) and not d._caution(w10)
    assert d._calib_for(w7)(0.92) == 0.0                     # ~83.6% calibrated: under the 85% caution floor
    assert d._calib_for(w7)(0.99) >= 0.85                    # ~85.7% calibrated: allowed
    assert d._calib_for(w10)(0.92) == d._calib(0.92)        # a normal hour keeps the normal confidence
    assert d._entry_check(w7.start + 60, w7, 0.65, 1.0, "up") == "caution hour under 70c"
    assert d._entry_check(w10.start + 60, w10, 0.65, 1.0, "up") is None
