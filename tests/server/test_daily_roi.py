"""The daily P&L download has a 'Daily ROI %' column: each day's profit over that day's starting balance."""
import asyncio, datetime as dt, io, json
from contextlib import asynccontextmanager

from openpyxl import load_workbook

from openjarvis.server.crew import desk


class _Conn:
    def __init__(self, kv, rows):
        self.kv, self.rows = kv, rows

    async def fetch(self, sql, *a):
        return self.kv if "polybot_kv" in sql else self.rows


class _Pool:
    def __init__(self, kv, rows):
        self.c = _Conn(kv, rows)

    @asynccontextmanager
    async def acquire(self):
        yield self.c


def test_roi_column(monkeypatch):
    from openjarvis.polybot.brain import STRATEGY_VERSION
    from openjarvis.server.crew.finance import LA
    t = dt.datetime(2026, 11, 23, 12, tzinfo=LA)
    kv = [{"key": "day_start:2026-11-23", "value": json.dumps({"start": 200.0})}]
    rows = [{"settled_at": t, "window_end": t, "pnl": 50.0, "version": STRATEGY_VERSION, "asset": "btc"}]

    async def noop(*a, **k):
        return None

    async def kvget(*a, **k):
        return {}
    monkeypatch.setattr(desk, "_ensure", noop)
    monkeypatch.setattr(desk, "get_kv", kvget)
    monkeypatch.setattr(desk, "load_money", noop)
    data = asyncio.run(desk.export_daily(_Pool(kv, rows)))
    ws = load_workbook(io.BytesIO(data)).active
    assert ws["D1"].value == "Daily ROI %"
    assert ws["D2"].value == 0.25 and "%" in ws["D2"].number_format
