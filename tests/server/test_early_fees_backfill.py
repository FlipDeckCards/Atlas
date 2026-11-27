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


def test_recompute_sets_pnl_from_fills_whatever_the_row_holds():
    """Absolute P&L = payout - cost - fee from the fills, so an over-charged row and an untouched row both land on the same number."""
    f1 = _fills("a", "up", 0.80, 10)
    f2 = _fills("b", "down", 0.60, 10)
    both = _fills("c", "up", 0.50, 4) , _fills("c", "down", 0.40, 4)             # both sides in one window
    wins = [{"mode": "live", "slug": "a", "up": True}, {"mode": "live", "slug": "b", "up": True}, {"mode": "live", "slug": "c", "up": False},
            {"mode": "live", "slug": "nofills", "up": True}]
    plan = {p["slug"]: p for p in desk.early_recompute_plan(wins, [f1, f2, *both])}
    assert abs(plan["a"]["pnl"] - (10 - 8 - FEE(0.80, 10))) < 1e-9
    assert abs(plan["b"]["pnl"] - (0 - 6 - FEE(0.60, 10))) < 1e-9
    assert abs(plan["c"]["pnl"] - (4 - 2.0 - 1.6 - FEE(0.5, 4) - FEE(0.4, 4))) < 1e-9
    assert "nofills" not in plan
    assert abs(plan["a"]["up_cost"] - (8 + FEE(0.80, 10))) < 1e-9 and plan["a"]["down_cost"] == 0


def test_recompute_runs_once(monkeypatch):
    c = _Conn([{"mode": "live", "slug": "a", "up": True}], [_fills("a", "up", 0.8, 10)])
    state = {}

    async def noop(*a, **k):
        return None

    async def kvget(pool, key):
        return state.get(key, {})
    monkeypatch.setattr(desk, "_ensure", noop)
    monkeypatch.setattr(desk, "get_kv", kvget)
    asyncio.run(desk.fees_recompute_early_once(_Pool(c)))
    sql, rows = c.many[0]
    assert "SET pnl = $3" in sql and rows[0][1] == "a" and abs(rows[0][2] - (2 - FEE(0.8, 10))) < 1e-9
    assert json.loads(c.exec[-1][1][1])["done"]
    state["fees_backfill_v3"] = {"done": True}
    c2 = _Conn([{"mode": "live", "slug": "a", "up": True}], [])
    asyncio.run(desk.fees_recompute_early_once(_Pool(c2)))
    assert not c2.many and not c2.exec
