"""v1.21: shadow results (calibration and scalp) are read from polybot_kv for the Station and the API."""

import asyncio
import json
from contextlib import asynccontextmanager

from openjarvis.server.crew import desk


class _Conn:
    def __init__(self, kv):
        self.kv = kv

    async def fetchval(self, sql, *args):
        assert "polybot_kv" in sql
        return self.kv.get(args[0])


class _Pool:
    def __init__(self, kv):
        self.kv = kv

    @asynccontextmanager
    async def acquire(self):
        yield _Conn(self.kv)


SAVED = {"rec": {}, "calib": {
    "model": {"under 50": {"n": 0, "wins": 0}, "80-89": {"n": 15, "wins": 12}, "90+": {"n": 4, "wins": 4}},
    "book": {"80-89": {"n": 9, "wins": 5}}},
    "scalp": {"entries": 6, "target": 4, "stop": 2, "settled": 6, "wins": 4, "profit": 0.31, "staked": 2.4}}


def test_reads_btc_key_and_reports_win_rates():
    pool = _Pool({"shadow": json.dumps(SAVED)})
    out = asyncio.run(desk.shadow_detail(pool, "btc"))
    model = {r["bucket"]: r for r in out["calib"]["model"]}
    assert model["80-89"] == {"bucket": "80-89", "trades": 15, "won": 12, "win_rate": 80.0}
    assert model["under 50"]["win_rate"] is None          # no trades: no rate, not a divide-by-zero
    assert out["calib"]["book"][0]["win_rate"] is not None
    assert out["scalp"]["entries"] == 6


def test_other_coins_use_their_own_key():
    pool = _Pool({"shadow:eth": json.dumps(SAVED)})
    assert asyncio.run(desk.shadow_detail(pool, "eth"))["scalp"]["target"] == 4
    assert asyncio.run(desk.shadow_detail(pool, "sol")) is None   # nothing saved for SOL yet
