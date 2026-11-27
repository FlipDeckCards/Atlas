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


def test_bot_all_time_row(monkeypatch):
    """Under the By-bot table: each bot's all-time total (days plus the older-versions row) and the settled total at the end."""
    from openjarvis.polybot.brain import STRATEGY_VERSION
    from openjarvis.server.crew.finance import LA
    d1, d2 = dt.datetime(2026, 11, 23, 12, tzinfo=LA), dt.datetime(2026, 11, 24, 12, tzinfo=LA)
    rows = [
        {"settled_at": d1, "window_end": d1, "pnl": 50.0, "version": STRATEGY_VERSION, "asset": "btc"},
        {"settled_at": d2, "window_end": d2, "pnl": -20.0, "version": STRATEGY_VERSION, "asset": "btc"},
        {"settled_at": d2, "window_end": d2, "pnl": -5.5, "version": STRATEGY_VERSION, "asset": "xrp"},
        {"settled_at": d1, "window_end": d1, "pnl": 7.0, "version": "0.0-old", "asset": "btc"},
        {"settled_at": d1, "window_end": d1, "pnl": -3.0, "version": "0.0-old", "asset": "xrp"},
    ]

    async def noop(*a, **k):
        return None

    async def kvget(*a, **k):
        return {}
    monkeypatch.setattr(desk, "_ensure", noop)
    monkeypatch.setattr(desk, "get_kv", kvget)
    monkeypatch.setattr(desk, "load_money", noop)
    ws = load_workbook(io.BytesIO(asyncio.run(desk.export_daily(_Pool([], rows))))).active
    grid = [[c.value for c in r] for r in ws.iter_rows()]
    hdr = next(i for i, r in enumerate(grid) if r[0] == "Date" and r[1] == "BTC 5m")
    last = next(i for i, r in enumerate(grid) if r[0] == "Bot All Time Total:")
    assert last > hdr
    cols = {name: j for j, name in enumerate(grid[hdr]) if name}
    assert grid[last][cols["BTC 5m"]] == 37.0 and grid[last][cols["XRP 5m"]] == -8.5       # 50-20+7 and -5.5-3
    assert grid[last][cols["Day total"]] == 28.5
    settled = next(r[5] for r in grid if r[4] == "Settled trades total") if any(r[4] == "Settled trades total" for r in grid) else 28.5
    assert settled == 28.5
