"""The early fee backfill: windows settled before Nov 10 that were booked without Polymarket's taker fee are charged once;
windows that already carry the fee are only marked."""
import asyncio, json
from contextlib import asynccontextmanager

from openjarvis.server.crew import desk

FEE = lambda p, q: 0.0695 * p * (1 - p) * q


def _fills(slug, side, price, qty, mode="live"):
    return {"mode": mode, "slug": slug, "side": side, "price": price, "qty": qty, "cost": price * qty, "features": None}


def test_plan_charges_only_windows_booked_without_the_fee():
    f_no = _fills("a", "up", 0.80, 10)                       # won: pnl without fee = 10 - 8 = 2
    f_has = _fills("b", "up", 0.80, 10)                      # same bet, but its stored pnl already has the fee
    f_loss = _fills("c", "down", 0.60, 10)                   # lost: pnl without fee = -6
    wins = [{"mode": "live", "slug": "a", "up": True, "pnl": 2.0},
            {"mode": "live", "slug": "b", "up": True, "pnl": 2.0 - FEE(0.80, 10)},
            {"mode": "live", "slug": "c", "up": True, "pnl": -6.0}]
    plan = {p["slug"]: p for p in desk.early_fee_plan(wins, [f_no, f_has, f_loss])}
    assert plan["a"]["charge"] and abs(plan["a"]["fee"] - FEE(0.80, 10)) < 1e-9
    assert not plan["b"]["charge"]                            # already after the fee: never charged twice
    assert plan["c"]["charge"] and plan["c"]["fee_dn"] > 0 and plan["c"]["fee_up"] == 0


def test_plan_uses_a_recorded_fee_and_skips_windows_without_fills():
    f = _fills("a", "up", 0.5, 4); f["features"] = json.dumps({"fee": 0.05})
    wins = [{"mode": "live", "slug": "a", "up": False, "pnl": -2.0}, {"mode": "live", "slug": "zzz", "up": True, "pnl": 1.0}]
    plan = desk.early_fee_plan(wins, [f])
    assert len(plan) == 1 and plan[0]["fee"] == 0.05 and plan[0]["charge"]


class _Conn:
    def __init__(self, wins, fills):
        self.wins, self.fills, self.many, self.exec = wins, fills, [], []

    async def fetch(self, sql, *a):
        return self.wins if "FROM polybot_windows" in sql else self.fills

    async def executemany(self, sql, rows):
        self.many.append((sql, list(rows)))

    async def execute(self, sql, *a):
        self.exec.append((sql, a))

    @asynccontextmanager
    async def transaction(self):
        yield


class _Pool:
    def __init__(self, c):
        self.c = c

    @asynccontextmanager
    async def acquire(self):
        yield self.c


def test_backfill_runs_once_and_updates(monkeypatch):
    wins = [{"mode": "live", "slug": "a", "up": True, "pnl": 2.0}, {"mode": "live", "slug": "b", "up": True, "pnl": 2.0 - FEE(0.8, 10)}]
    c = _Conn(wins, [_fills("a", "up", 0.8, 10), _fills("b", "up", 0.8, 10)])
    state = {}

    async def noop(*a, **k):
        return None

    async def kvget(pool, key):
        return state.get(key, {})
    monkeypatch.setattr(desk, "_ensure", noop)
    monkeypatch.setattr(desk, "get_kv", kvget)
    asyncio.run(desk.fees_backfill_early_once(_Pool(c)))
    charge_sql, charged = c.many[0]
    assert "pnl = pnl - $3" in charge_sql and [r[1] for r in charged] == ["a"]          # only the window booked without the fee
    assert [r[1] for r in c.many[1][1]] == ["b"]                                         # the other is only marked
    flag = json.loads(c.exec[-1][1][1])
    assert flag["done"] and flag["charged"] == 1 and flag["already_had_fee"] == 1
    state["fees_backfill_v2"] = {"done": True}                                            # second start: nothing happens
    c2 = _Conn(wins, [])
    asyncio.run(desk.fees_backfill_early_once(_Pool(c2)))
    assert not c2.many and not c2.exec
