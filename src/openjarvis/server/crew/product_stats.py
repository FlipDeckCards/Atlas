"""Users and traffic for every factory, read-only, straight from each product's own database.

  aibussol  Aibussol's own database (the app's pool)
  flipdeck  FLIPDECK_READ_DATABASE_URL
  marnyx    MARNYX_READ_DATABASE_URL
  deadzone  DEADZONE_READ_DATABASE_URL

For each: total users, new in 24 hours and 7 days, paying now (where the product has subscriptions), and a daily chart for the last
90 days (Los Angeles days): page views and unique visitors when the product records page views (FlipDeck's page_views table),
otherwise sign-ups per day. Every query runs read-only with a timeout; a query that does not fit a product's tables is skipped, and a
product whose database is not connected says so. Results are cached for 5 minutes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DB_VARS = {"flipdeck": "FLIPDECK_READ_DATABASE_URL", "marnyx": "MARNYX_READ_DATABASE_URL", "deadzone": "DEADZONE_READ_DATABASE_URL"}
CACHE_S = 300
DAYS = 90

USERS_SQL = ("SELECT count(*) AS users_total, count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS new_24h, "
             "count(*) FILTER (WHERE created_at > now() - interval '7 days') AS new_7d FROM users")
PAYING_SQL = {
    "flipdeck": "SELECT count(*) FROM subscriptions WHERE status='active' AND stripe_subscription_id IS NOT NULL AND tier IN ('pro','elite')",
    "marnyx": "SELECT count(*) FROM subscriptions WHERE status IN ('active','trialing')",
    "aibussol": "SELECT count(*) FROM subscriptions WHERE status IN ('active','trialing')",
    "deadzone": "SELECT count(*) FROM subscriptions WHERE status IN ('active','trialing')",
}
ACTIVE_SQL = {
    "aibussol": "SELECT count(DISTINCT sender_id) FROM channel_sessions WHERE updated_at > now() - interval '24 hours'",
}
EXTRA_SQL = {
    "marnyx": ("SELECT count(*) FILTER (WHERE status='published' AND published_at > now() - interval '7 days') AS posts_published_7d, "
               "count(*) FILTER (WHERE status='failed' AND updated_at > now() - interval '7 days') AS posts_failed_7d, "
               "count(*) FILTER (WHERE status IN ('pending','scheduled','approved')) AS posts_queued FROM posts"),
}
LA_DAY = "(created_at AT TIME ZONE 'America/Los_Angeles')::date"
TRAFFIC_SQL = (f"SELECT {LA_DAY} AS d, count(*) AS views, count(DISTINCT COALESCE(sub_token, 'u' || user_id::text, user_agent)) AS visitors "
               f"FROM page_views WHERE created_at > now() - interval '{DAYS} days' GROUP BY 1 ORDER BY 1")
TRAFFIC_SIMPLE_SQL = (f"SELECT {LA_DAY} AS d, count(*) AS views FROM page_views WHERE created_at > now() - interval '{DAYS} days' "
                      "GROUP BY 1 ORDER BY 1")
SIGNUPS_SQL = f"SELECT {LA_DAY} AS d, count(*) AS signups FROM users WHERE created_at > now() - interval '{DAYS} days' GROUP BY 1 ORDER BY 1"


async def _try(conn, sql: str, kind: str = "row"):
    """One query; None when it does not fit this product's tables (the transaction is rolled back to a savepoint)."""
    try:
        async with conn.transaction():
            q = conn.fetchrow if kind == "row" else conn.fetch
            return await asyncio.wait_for(q(sql), timeout=20)
    except Exception as exc:
        logger.info("product stats query skipped: %s", type(exc).__name__)
        return None


async def read_product(key: str, conn) -> Dict[str, Any]:
    out: Dict[str, Any] = {"connected": True}
    await conn.execute("SET default_transaction_read_only = on")
    u = await _try(conn, USERS_SQL)
    if u:
        out.update(users_total=int(u["users_total"]), new_24h=int(u["new_24h"]), new_7d=int(u["new_7d"]))
    if key in PAYING_SQL:
        p = await _try(conn, PAYING_SQL[key])
        if p is not None:
            out["paying"] = int(p[0] or 0)
    if key in ACTIVE_SQL:
        a = await _try(conn, ACTIVE_SQL[key])
        if a is not None:
            out["active_24h"] = int(a[0] or 0)
    if key in EXTRA_SQL:
        e = await _try(conn, EXTRA_SQL[key])
        if e is not None:
            out["extra"] = {k: int(e[k] or 0) for k in e.keys()}
    has_views = await _try(conn, "SELECT to_regclass('public.page_views') IS NOT NULL")
    rows = None
    if has_views and has_views[0]:
        rows = await _try(conn, TRAFFIC_SQL, "rows")
        if rows is not None:
            out["traffic"] = {"kind": "views", "days": [{"d": str(r["d"]), "views": int(r["views"]), "visitors": int(r["visitors"])} for r in rows]}
        else:
            rows = await _try(conn, TRAFFIC_SIMPLE_SQL, "rows")
            if rows is not None:
                out["traffic"] = {"kind": "views", "days": [{"d": str(r["d"]), "views": int(r["views"])} for r in rows]}
    if "traffic" not in out:
        rows = await _try(conn, SIGNUPS_SQL, "rows")
        if rows is not None:
            out["traffic"] = {"kind": "signups", "days": [{"d": str(r["d"]), "signups": int(r["signups"])} for r in rows]}
    return out


class ProductStats:
    def __init__(self, pool=None, env=None, connect=None) -> None:
        self.pool = pool
        self.env = env
        self.connect = connect
        self._cache: Optional[Dict[str, Any]] = None
        self._at = 0.0

    def _e(self):
        return os.environ if self.env is None else self.env

    async def _one(self, key: str) -> Dict[str, Any]:
        try:
            if key == "aibussol":
                if self.pool is None:
                    return {"connected": False, "note": "Aibussol's database is not available."}
                async with self.pool.acquire() as conn:
                    async with conn.transaction(readonly=True):
                        return await read_product(key, conn)
            var = DB_VARS[key]
            dsn = (self._e().get(var) or "").strip()
            if not dsn:
                return {"connected": False, "note": f"Not connected: set {var} on Render (Atlas service) to its database URL."}
            if self.connect is not None:
                conn = await self.connect(dsn)
            else:
                import asyncpg

                conn = await asyncio.wait_for(asyncpg.connect(dsn, timeout=10), timeout=15)
            try:
                return await read_product(key, conn)
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("product stats for %s failed: %s", key, type(exc).__name__)
            return {"connected": False, "note": "The database could not be read just now."}

    async def all(self) -> Dict[str, Any]:
        if self._cache is not None and time.monotonic() - self._at < CACHE_S:
            return self._cache
        keys = ["aibussol", *DB_VARS]
        res = await asyncio.gather(*(self._one(k) for k in keys))
        self._cache, self._at = dict(zip(keys, res)), time.monotonic()
        return self._cache
