"""The owner's business brief: a Word file built from the live settings and ledger."""

import asyncio
import datetime as dt
import io
import json


class _Conn:
    def __init__(self, kv, rows):
        self.kv, self.rows = kv, rows

    async def execute(self, *a):
        return None

    async def fetchval(self, q, key, *a):
        v = self.kv.get(key)
        return json.dumps(v) if v is not None else None

    async def fetch(self, q, *a):
        return self.rows if "GROUP BY" in q else []


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self, kv=None, rows=None):
        self.c = _Conn(kv or {}, rows or [])

    def acquire(self):
        return _Acq(self.c)


def test_brief_is_a_word_file_with_every_desk_and_the_live_numbers():
    from docx import Document
    from openjarvis.server.crew import brief

    kv = {"control": {"mode": "live"}, "control:sol": {"mode": "live"}, "status": {"heartbeat": 1, "account_value": 123.45}}
    rows = [{"mode": "live", "asset": "btc", "n": 40, "wins": 35, "pnl": 12.5, "staked": 300.0, "n7": 10, "pnl7": 3.0},
            {"mode": "paper", "asset": "eth1h", "n": 5, "wins": 2, "pnl": -1.0, "staked": 20.0, "n7": 5, "pnl7": -1.0}]
    pool = FakePool(kv, rows)
    facts = asyncio.run(brief._desk_facts(pool))
    assert len(facts) == 12
    f = {x["asset"]: x for x in facts}
    assert f["btc"]["mode"] == "live" and f["sol"]["mode"] == "live" and f["eth"]["mode"] == "paper"
    assert f["xrp"]["floor"] == 85 and f["xrp"]["window_total"] == 5.0 and f["btc"]["window_total"] == 20.0
    assert f["btc"]["live"]["pnl"] == 12.5 and f["btc"]["balance"] == 123.45
    body = brief.build_doc(facts, [{"name": "Brother", "pnl": {"btc": {"all": 2.0}}, "modes": {"btc": "live"}}],
                           dt.datetime(2026, 11, 7, 18, 0, tzinfo=dt.timezone.utc))
    doc = Document(io.BytesIO(body))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    for want in ("Executive summary", "BTC 5m", "XRP 1h", "20% of the account", "85%+ → 1%", "Family accounts", "Brother", "+$12.50"):
        assert want in text, want
