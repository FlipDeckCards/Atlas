"""Desk P&L: each desk is its own settled ledger; the account gap is reported as unreconciled, never absorbed by a desk."""

import asyncio
import datetime as dt
import json
from contextlib import asynccontextmanager

from openjarvis.server.crew import desk


class _Conn:
    def __init__(self, ledger, kv):
        self.ledger, self.kv = ledger, kv

    async def fetchval(self, sql, *args):
        if "polybot_kv" in sql:
            return json.dumps(self.kv.get(args[0], {})) if args[0] in self.kv else None
        asset = args[-1]
        if "settled_at" not in sql:                    # all-time query: only asset
            return self.ledger[asset]["all"]
        if args[0] > dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2):
            return self.ledger[asset]["today"]
        return self.ledger[asset]["d30"]

    async def execute(self, sql, *args):
        return None


class _Pool:
    def __init__(self, ledger, kv):
        self.conn = _Conn(ledger, kv)

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


LEDGER = {
    "btc": {"today": 1.0, "d30": 2.0, "all": 3.0},
    "eth": {"today": 0.5, "d30": 0.5, "all": 0.4},
    "sol": {"today": -0.2, "d30": -0.1, "all": -0.3},
    "xrp": {"today": 0.1, "d30": 0.2, "all": 0.15},
}


def _run(coro):
    return asyncio.run(coro)


def _pool(status):
    kv = {desk.kv_key("status", "btc"): status}
    return _Pool(LEDGER, kv)


def test_each_desk_is_its_own_ledger_no_residual_plug(monkeypatch):
    monkeypatch.setattr(desk, "deposits", lambda: 100.0)
    # account says a lot more was lost than the ledger shows: the desks must NOT absorb it
    st = {"mode": "live", "account_value": 8.96, "day_start": 9.0, "day": desk.la_day_str(dt.datetime.now(dt.timezone.utc))}
    per = _run(desk.live_pnl_all(_pool(st)))
    assert per["btc"]["d30"] == LEDGER["btc"]["d30"]
    assert per["btc"]["today"] == LEDGER["btc"]["today"]
    assert per["btc"]["all"] == LEDGER["btc"]["all"]


def test_reconciliation_reports_gap_as_unreconciled(monkeypatch):
    monkeypatch.setattr(desk, "deposits", lambda: 100.0)
    st = {"mode": "live", "account_value": 8.96, "day_start": 9.0, "day": desk.la_day_str(dt.datetime.now(dt.timezone.utc))}
    rec = _run(desk.reconciliation(_pool(st)))
    ledger_all = round(sum(v["all"] for v in LEDGER.values()), 2)          # 3.0 + 0.4 - 0.3 + 0.15 = 3.25
    assert rec["ledger_all_time"] == ledger_all
    assert rec["account_change_all_time"] == round(8.96 - 100.0, 2)
    assert rec["unreconciled_all_time"] == round((8.96 - 100.0) - ledger_all, 2)
    assert rec["account_change_today"] == round(8.96 - 9.0, 2)
    ledger_today = round(sum(v["today"] for v in LEDGER.values()), 2)
    assert rec["unreconciled_today"] == round((8.96 - 9.0) - ledger_today, 2)


def test_reconciliation_without_account_snapshot(monkeypatch):
    monkeypatch.setattr(desk, "deposits", lambda: None)
    rec = _run(desk.reconciliation(_pool({})))
    assert rec["account_value"] is None
    assert rec["unreconciled_all_time"] is None
    assert rec["unreconciled_today"] is None
