import asyncio
import json

from openjarvis.server.crew import desk as sdesk


class _Conn:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    async def fetch(self, q, *args):
        self.queries.append((q, args))
        if "coalesce(f.version, '1.0') = $2" in q:
            return [r for r in self.rows if r["_ver"] == args[1]]
        return list(self.rows)

    async def execute(self, *a, **k):
        return None


class _Pool:
    def __init__(self, rows):
        self.c = _Conn(rows)

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.c

            async def __aexit__(self, *a):
                return False
        return _Ctx()


def _fill(ver, side, price, model, mz, secs, up):
    return {"_ver": ver, "side": side, "price": price, "qty": 10.0, "cost": 10.0 * (price if side == "up" else 1 - price),
            "features": json.dumps({"model": model, "momentum_z": mz, "secs_left": secs, "edge": 0.05}), "up": up}


def test_three_scopes_and_the_current_rules_filter(monkeypatch):
    async def _noop(pool):
        return None
    monkeypatch.setattr(sdesk, "_ensure", _noop)
    sdesk._AN_CACHE.clear()
    rows = [
        _fill("1.22", "up", 0.80, 0.95, 1.0, 120, True),     # today's rules take it
        _fill("1.21", "up", 0.82, 0.96, 1.0, 120, True),     # an older version, still allowed today
        _fill("1.21", "up", 0.60, 0.90, 1.0, 120, False),    # BTC 5m under 70c: ruled out today
        _fill("1.21", "up", 0.85, 0.70, 1.0, 120, False),    # model under the 80% floor: ruled out today
        _fill("1.21", "up", 0.85, 0.95, 0.1, 120, False),    # flat momentum: ruled out today
    ]
    pool = _Pool(rows)
    out = asyncio.run(sdesk.analysis_scopes(pool, "live", "btc"))
    assert out["version"]["fills"] == 1 and out["all"]["fills"] == 5 and out["rules"]["fills"] == 2
    assert out["rules"]["scope_label"] == "all history, current rules"
    row = out["rules"]["groups"]["cost"][0]
    assert row["fills"] == 2 and row["win_rate"] == 100.0 and row["return_pct"] is not None and row["return_pct"] > 0
