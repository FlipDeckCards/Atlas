"""Where the desk keeps its record: the same Postgres database as Aibussol (DATABASE_URL), or JSON files for a local paper run.

Tables (created on first run):
  polybot_kv       'status' (written by the desk: mode, balance, what it is doing and why) and 'control' (written by Aibussol:
                   mode, paused, kill, limits). The desk never writes 'control'; Aibussol never writes 'status'.
  polybot_fills    every fill, paper or live
  polybot_windows  every window the desk traded, with what it paid, how it settled and its P&L
  polybot_events   plain-language log lines (entries, settlements, risk stops), newest read by Helix
Writes are buffered and flushed every POLYBOT_FLUSH_SECONDS, so the database is not touched every second.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional

DDL = """
CREATE TABLE IF NOT EXISTS polybot_kv (key TEXT PRIMARY KEY, value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS polybot_fills (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL, mode TEXT NOT NULL, slug TEXT NOT NULL, side TEXT NOT NULL,
  price DOUBLE PRECISION NOT NULL, qty DOUBLE PRECISION NOT NULL, cost DOUBLE PRECISION NOT NULL, fair DOUBLE PRECISION, order_id TEXT);
CREATE INDEX IF NOT EXISTS polybot_fills_ts ON polybot_fills (ts);
CREATE TABLE IF NOT EXISTS polybot_windows (
  slug TEXT NOT NULL, mode TEXT NOT NULL, window_start TIMESTAMPTZ, window_end TIMESTAMPTZ, price_to_beat DOUBLE PRECISION,
  close_price DOUBLE PRECISION, up BOOLEAN, up_qty DOUBLE PRECISION, up_cost DOUBLE PRECISION, down_qty DOUBLE PRECISION,
  down_cost DOUBLE PRECISION, pnl DOUBLE PRECISION, settled_at TIMESTAMPTZ, PRIMARY KEY (slug, mode));
CREATE INDEX IF NOT EXISTS polybot_windows_settled ON polybot_windows (settled_at);
ALTER TABLE polybot_fills ADD COLUMN IF NOT EXISTS version TEXT;
ALTER TABLE polybot_windows ADD COLUMN IF NOT EXISTS version TEXT;
ALTER TABLE polybot_fills ADD COLUMN IF NOT EXISTS features JSONB;
CREATE TABLE IF NOT EXISTS polybot_events (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(), level TEXT NOT NULL, text TEXT NOT NULL);
ALTER TABLE polybot_events ADD COLUMN IF NOT EXISTS asset TEXT;
"""


def kv_key(key: str, asset: str = "btc") -> str:
    """BTC keeps the original keys ('status', 'control'); other desks get their own ('status:eth')."""
    a = (asset or "btc").lower()
    return key if a == "btc" else f"{key}:{a}"


def asset_of(slug: str) -> str:
    """cpc-eth-updown-5m-... -> eth"""
    parts = (slug or "").split("-")
    return parts[1] if len(parts) > 1 else ""


class ScopedStore:
    """One desk's view of the shared store: its own status and control keys; the ledger tables are shared (rows carry the slug)."""

    def __init__(self, store, asset: str) -> None:
        self.store, self.asset = store, (asset or "btc").lower()

    async def get(self, key: str):
        return await self.store.get(kv_key(key, self.asset))

    async def put(self, key: str, value) -> None:
        await self.store.put(kv_key(key, self.asset), value)

    async def flush(self, ledger) -> None:
        await self.store.flush(ledger)

    async def pair_history(self, days: int = 21) -> List[Dict[str, Any]]:
        fn = getattr(self.store, "pair_history", None)
        return await fn(self.asset, days) if fn else []

    async def close(self) -> None:
        await self.store.close()


def _t(ts: Optional[float]) -> Optional[dt.datetime]:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc) if ts else None


class Ledger:
    """Buffers fills, windows and events, and flushes them in one go."""

    def __init__(self, asset: str = "btc") -> None:
        self.asset = asset
        self.fills: List[Dict[str, Any]] = []
        self.windows: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []

    def fill(self, mode: str, f, fair: Optional[float], features: Optional[Dict[str, Any]] = None) -> None:
        from openjarvis.polybot.brain import STRATEGY_VERSION

        self.fills.append({"version": STRATEGY_VERSION, "ts": f.ts, "mode": mode, "slug": f.slug, "side": f.side, "price": f.price, "qty": f.qty, "cost": f.cost,
                           "fair": fair, "order_id": f.order_id, "features": features or {}})

    def window(self, row: Dict[str, Any]) -> None:
        from openjarvis.polybot.brain import STRATEGY_VERSION

        row.setdefault("version", STRATEGY_VERSION)
        self.windows[f'{row["slug"]}|{row["mode"]}'] = row

    def event(self, level: str, text: str, ts: float) -> None:
        self.events.append({"ts": ts, "level": level, "text": text[:500], "asset": self.asset})

    def take(self):
        out = (self.fills, list(self.windows.values()), self.events)
        self.fills, self.windows, self.events = [], {}, []
        return out


class PgStore:
    def __init__(self, pool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "PgStore":
        import asyncpg

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        s = cls(pool)
        async with pool.acquire() as c:
            await c.execute(DDL)
        return s

    async def get(self, key: str) -> Dict[str, Any]:
        async with self.pool.acquire() as c:
            v = await c.fetchval("SELECT value FROM polybot_kv WHERE key = $1", key)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                v = {}
        return v if isinstance(v, dict) else {}

    async def put(self, key: str, value: Dict[str, Any]) -> None:
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO polybot_kv (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
                            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", key, json.dumps(value))

    async def flush(self, ledger: Ledger) -> None:
        fills, windows, events = ledger.take()
        if not (fills or windows or events):
            return
        async with self.pool.acquire() as c:
            async with c.transaction():
                if fills:
                    await c.executemany("INSERT INTO polybot_fills (ts, mode, slug, side, price, qty, cost, fair, order_id, version, features) "
                                        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)",
                                        [(_t(f["ts"]), f["mode"], f["slug"], f["side"], f["price"], f["qty"], f["cost"], f["fair"], f["order_id"], f.get("version"),
                                          json.dumps(f.get("features") or {})) for f in fills])
                for w in windows:
                    await c.execute(
                        "INSERT INTO polybot_windows (slug, mode, window_start, window_end, price_to_beat, close_price, up, up_qty, up_cost, "
                        "down_qty, down_cost, pnl, settled_at, version) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) ON CONFLICT (slug, mode) DO UPDATE SET "
                        "close_price = EXCLUDED.close_price, up = EXCLUDED.up, up_qty = EXCLUDED.up_qty, up_cost = EXCLUDED.up_cost, "
                        "down_qty = EXCLUDED.down_qty, down_cost = EXCLUDED.down_cost, pnl = EXCLUDED.pnl, settled_at = EXCLUDED.settled_at",
                        w["slug"], w["mode"], _t(w["window_start"]), _t(w["window_end"]), w["price_to_beat"], w.get("close_price"), w.get("up"),
                        w["up_qty"], w["up_cost"], w["down_qty"], w["down_cost"], w.get("pnl"), _t(w.get("settled_at")), w.get("version"))
                if events:
                    await c.executemany("INSERT INTO polybot_events (ts, level, text, asset) VALUES ($1,$2,$3,$4)",
                                        [(_t(e["ts"]), e["level"], e["text"], e.get("asset") or "btc") for e in events])

    async def pair_history(self, asset: str, days: int = 21) -> List[Dict[str, Any]]:
        """v1.14: settled LIVE fills of one desk over the last `days` days, with how their window settled (for learned pair sizing)."""
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT f.slug, f.side, f.qty, f.cost, f.features, f.version, w.up FROM polybot_fills f JOIN polybot_windows w "
                "ON w.slug = f.slug AND w.mode = f.mode WHERE f.mode = 'live' AND w.up IS NOT NULL AND split_part(f.slug, '-', 2) = $1 "
                "AND f.ts >= now() - make_interval(days => $2) ORDER BY f.ts", asset, int(days))
        out = []
        for r in rows:
            feat = r["features"]
            if isinstance(feat, str):
                try:
                    feat = json.loads(feat)
                except ValueError:
                    feat = {}
            out.append({"slug": r["slug"], "side": r["side"], "qty": r["qty"], "cost": r["cost"], "features": feat or {},
                        "version": r["version"], "up": r["up"]})
        return out

    async def close(self) -> None:
        await self.pool.close()


class FileStore:
    """JSON files in a folder: enough for a local paper run (`python -m openjarvis.polybot run` with no DATABASE_URL)."""

    def __init__(self, folder: str) -> None:
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def _path(self, name: str) -> str:
        return os.path.join(self.folder, name)

    async def get(self, key: str) -> Dict[str, Any]:
        try:
            with open(self._path(f"{key}.json")) as fh:
                v = json.load(fh)
            return v if isinstance(v, dict) else {}
        except (OSError, ValueError):
            return {}

    async def put(self, key: str, value: Dict[str, Any]) -> None:
        tmp = self._path(f"{key}.json.tmp")
        with open(tmp, "w") as fh:
            json.dump(value, fh, indent=1, default=str)
        os.replace(tmp, self._path(f"{key}.json"))

    async def flush(self, ledger: Ledger) -> None:
        fills, windows, events = ledger.take()
        for name, rows in (("fills.jsonl", fills), ("windows.jsonl", windows), ("events.jsonl", events)):
            if rows:
                with open(self._path(name), "a") as fh:
                    for r in rows:
                        fh.write(json.dumps(r, default=str) + "\n")

    async def close(self) -> None:
        return None
