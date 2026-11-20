import asyncio

from openjarvis.polybot import shadow
from openjarvis.polybot.shadow import Shadow, parse_live_calib


def test_parse_live_calib():
    assert parse_live_calib(None) is None
    assert parse_live_calib({"live_calib": {"90+": {"n": 0, "wins": 0}}}) is None
    t = parse_live_calib({"live_calib": {"90+": {"n": 40, "wins": 30}, "80-89": {"n": 5, "wins": 9}}})
    assert t == {"90+": {"n": 40, "wins": 30}}          # a bucket with more wins than trades is dropped


def test_table_overrides_own_record():
    s = Shadow()
    own = s.live_calibrated_conf(0.95)                  # empty own record: the raw number
    assert own == 0.95
    owner = {"90+": {"n": 80, "wins": 60}}              # the owner's 90+ bucket won 75%
    c = s.live_calibrated_conf(0.95, table=owner)
    assert abs(c - (60 + 20 * 0.95) / 100) < 1e-4
    assert s.live_calibrated_conf(0.85, table=owner) == 0.85     # no owner trades in that bucket: raw


class _Store:
    def __init__(self, kv):
        self.kv = kv

    async def get(self, key):
        return self.kv.get(key, {})


def test_brother_desk_loads_owner_record():
    from openjarvis.polybot.desk import Desk

    d = Desk.__new__(Desk)
    d._t = {}
    d.log = lambda *a: None
    d.base = type("B", (), {"account": "bro"})()
    d.cfg = type("C", (), {"asset": "xrp"})()
    d.store = type("S", (), {"store": _Store({"shadow2:xrp": {"live_calib": {"90+": {"n": 50, "wins": 40}}}})})()
    asyncio.run(d.load_owner_calib(1000.0))
    assert d._owner_live == {"90+": {"n": 50, "wins": 40}}
    d.base.account = ""                                  # the owner's own desk never swaps its record
    d2 = Desk.__new__(Desk)
    d2._t, d2.base, d2.cfg, d2.store = {}, type("B", (), {"account": ""})(), d.cfg, d.store
    d2.log = lambda *a: None
    asyncio.run(d2.load_owner_calib(1000.0))
    assert getattr(d2, "_owner_live", None) is None
