import asyncio
import datetime as dt
import json


class _Conn:
    def __init__(self, rows, kv):
        self.rows, self.kv = rows, kv

    async def fetch(self, sql, *args):
        return self.rows

    async def fetchval(self, sql, *args):
        return self.kv.get(args[0])


class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, rows, kv):
        self.conn = _Conn(rows, kv)

    def acquire(self):
        return _Acq(self.conn)


def test_candidates_export_marks_blocked_buys_with_the_window_result():
    from openjarvis.polybot import state
    from openjarvis.server.crew import desk

    ts = dt.datetime(2026, 11, 10, 17, 0, tzinfo=dt.timezone.utc)
    rows = [
        {"ts": ts, "asset": "eth", "slug": "cpc-eth-updown-5m-x", "side": "down", "price": 0.80, "fair": 0.9,
         "momentum_z": 0.1, "sigma": 0.02, "vol_tag": None, "secs_left": 120.0, "blocked_by": "eth 5m under 85c"},
        {"ts": ts, "asset": "btc", "slug": "cpc-btc-updown-5m-y", "side": "up", "price": 0.60, "fair": 0.7,
         "momentum_z": 0.5, "sigma": 0.02, "vol_tag": "mid", "secs_left": 100.0, "blocked_by": ""},
    ]
    kv = {state.kv_key("shadow", "eth", ""): json.dumps({"log": [{"slug": "cpc-eth-updown-5m-x", "up": False}]}),
          state.kv_key("shadow", "btc", ""): json.dumps({"log": [{"slug": "cpc-btc-updown-5m-y", "up": True}]})}
    body = asyncio.run(desk.export_candidates(_Pool(rows, kv)))
    lines = body.strip().splitlines()
    assert lines[0].startswith("time_utc,desk,slug,side,buy_price")
    assert "eth 5m under 85c" in lines[1] and lines[1].endswith(",DOWN,1") or lines[1].endswith("DOWN,1")
    assert lines[2].endswith("UP,1") and "BTC 5m" in lines[2]
    # subscribers get the header only
    assert asyncio.run(desk.export_candidates(_Pool(rows, kv), account="x@y")).strip() == lines[0]
