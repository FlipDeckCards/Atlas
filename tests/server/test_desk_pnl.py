"""Desk P&L: each desk is its own settled ledger for the current strategy version; earlier versions are shown separately
and count only in the account reconciliation. The account gap is reported as unreconciled, never absorbed by a desk."""

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
            return json.dumps(self.kv[args[0]]) if args[0] in self.kv else None
        if "<>" in sql:                                   # earlier strategy versions
            if "settled_at" in sql:                       # (asset, time, version) order: asset is $2
                return self.ledger[args[1]]["pre_today"]
            return self.ledger[args[0]]["pre_all"]
        if "settled_at" not in sql:                       # all-time, current version: (asset, version)
            return self.ledger[args[0]]["all"]
        asset = args[1]                                   # (time, asset, version)
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
    "btc": {"today": 1.0, "d30": 2.0, "all": 3.0, "pre_today": -5.0, "pre_all": -6.0},
    "eth": {"today": 0.5, "d30": 0.5, "all": 0.4, "pre_today": 0.0, "pre_all": 0.0},
    "sol": {"today": -0.2, "d30": -0.1, "all": -0.3, "pre_today": 0.0, "pre_all": -1.0},
    "xrp": {"today": 0.1, "d30": 0.2, "all": 0.15, "pre_today": 0.0, "pre_all": 0.0},
}


def _run(coro):
    return asyncio.run(coro)


def _pool(status):
    return _Pool(LEDGER, {desk.kv_key("status", "btc"): status})


def _today_str():
    return desk.la_day_str(dt.datetime.now(dt.timezone.utc))


def test_desk_figures_are_current_version_only(monkeypatch):
    monkeypatch.setattr(desk, "deposits", lambda: 100.0)
    st = {"mode": "live", "account_value": 8.96, "day_start": 9.0, "day": _today_str()}
    per = _run(desk.live_pnl_all(_pool(st)))
    assert per["btc"]["today"] == LEDGER["btc"]["today"]        # earlier-version -5.0 is not in the desk figure
    assert per["btc"]["d30"] == LEDGER["btc"]["d30"]
    assert per["btc"]["all"] == LEDGER["btc"]["all"]
    assert per["btc"]["pre_today"] == LEDGER["btc"]["pre_today"]  # reported on its own


def test_reconciliation_includes_earlier_versions_and_reports_gap(monkeypatch):
    monkeypatch.setattr(desk, "deposits", lambda: 100.0)
    st = {"mode": "live", "account_value": 8.96, "day_start": 9.0, "day": _today_str()}
    rec = _run(desk.reconciliation(_pool(st)))
    ledger_all = round(sum(v["all"] + v["pre_all"] for v in LEDGER.values()), 2)
    ledger_today = round(sum(v["today"] + v["pre_today"] for v in LEDGER.values()), 2)
    assert rec["ledger_all_time"] == ledger_all
    assert rec["ledger_today"] == ledger_today
    assert rec["account_change_all_time"] == round(8.96 - 100.0, 2)
    assert rec["unreconciled_all_time"] == round((8.96 - 100.0) - ledger_all, 2)
    assert rec["account_change_today"] == round(8.96 - 9.0, 2)
    assert rec["unreconciled_today"] == round((8.96 - 9.0) - ledger_today, 2)


def test_reconciliation_without_account_snapshot(monkeypatch):
    monkeypatch.setattr(desk, "deposits", lambda: None)
    rec = _run(desk.reconciliation(_pool({})))
    assert rec["account_value"] is None
    assert rec["unreconciled_all_time"] is None
    assert rec["unreconciled_today"] is None
