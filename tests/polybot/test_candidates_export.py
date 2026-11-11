import asyncio
import datetime as dt


class _Conn:
    def __init__(self, cands, results):
        self.cands, self.results = cands, results

    async def fetch(self, sql, *args):
        if "polybot_candidates" in sql:
            return self.cands
        return [r for r in self.results if r["slug"] in args[0]]


class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, cands, results):
        self.conn = _Conn(cands, results)

    def acquire(self):
        return _Acq(self.conn)


def _cand(asset, slug, side, price, blocked):
    ts = dt.datetime(2026, 11, 10, 17, 0, tzinfo=dt.timezone.utc)
    return {"ts": ts, "asset": asset, "slug": slug, "side": side, "price": price, "fair": 0.9, "momentum_z": 0.1,
            "sigma": 0.02, "vol_tag": None, "secs_left": 120.0, "blocked_by": blocked}


def test_candidates_export_checks_blocked_buys_against_the_window_result_and_leaves_out_subscribers():
    from openjarvis.server.crew import desk

    cands = [
        _cand("eth", "cpc-eth-updown-5m-x", "down", 0.80, "eth 5m under 85c"),   # blocked; window settled DOWN -> Down would have won
        _cand("btc", "cpc-btc-updown-5m-y", "up", 0.60, ""),                     # allowed; window settled UP -> won
        _cand("btc@ucc70a6f6b1be", "cpc-btc-updown-5m-z", "up", 0.60, ""),       # a subscriber's copy: left out
    ]
    results = [{"slug": "cpc-eth-updown-5m-x", "asset": "eth", "up": False},
               {"slug": "cpc-btc-updown-5m-y", "asset": "btc", "up": True}]
    lines = asyncio.run(desk.export_candidates(_Pool(cands, results))).strip().splitlines()
    assert lines[0].startswith("time_utc,desk,slug,side,buy_price")
    assert len(lines) == 3                                                       # header + two owner rows
    assert "eth 5m under 85c" in lines[1] and lines[1].endswith("DOWN,1")
    assert lines[2].endswith("UP,1") and "BTC 5m" in lines[2]
    assert asyncio.run(desk.export_candidates(_Pool(cands, results), account="x@y")).strip() == lines[0]
