"""Running the desk: inside Aibussol's own server (the default) or as a separate process, never two at once.

Only one desk may ever trade. Whoever runs it first takes a Postgres advisory lock and holds it on its own connection for as long as
it runs; any other copy (a second instance during a Render deploy, or a separate worker someone also started) waits quietly until
the lock is free. When the holder stops, it cancels its resting orders first, then lets go.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Optional

from openjarvis.polybot.config import Config

logger = logging.getLogger("polybot.runtime")
LOCK_ID = 7_310_442_118          # an arbitrary constant: "the Polymarket Desk" (BTC); other assets get their own lock


def lock_id(asset: str) -> int:
    a = (asset or "btc").lower()
    return LOCK_ID if a == "btc" else LOCK_ID + 1 + (int.from_bytes(a.encode()[:6].ljust(6, b"\0"), "big") % 1_000_000)
LOCK_RETRY_S = 15.0


def ntfy_pusher(url: str):
    """A phone push through an ntfy topic URL (the same CREW_NOTIFY_URL Aibussol uses), or None when there is none."""
    if not url:
        return None

    async def push(title: str, text: str) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, content=text[:1000].encode("utf-8"), headers={"Title": title.encode("ascii", "ignore").decode()[:120] or "Desk"})
    return push


class Runner:
    """Owns one desk: waits for the lock, runs it, restarts it after a crash, and stops it cleanly."""

    def __init__(self, cfg: Config, pool: Any) -> None:
        self.cfg, self.pool = cfg, pool
        self.desk = None
        self.state = "starting"            # starting | waiting (another copy holds the lock) | running | stopped | crashed
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self.instance = uuid.uuid4().hex[:12]

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._main(), name=f"polymarket-desk-{self.cfg.asset}")
        return self._task

    async def stop(self, timeout: float = 20.0) -> None:
        self._stop.set()
        if self.desk is not None:
            self.desk.stop()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            except Exception:
                logger.exception("desk stopped with an error")

    async def _wait_for_other_copy(self) -> None:
        """Wait while another desk's heartbeat (written every flush, about once a minute) is fresh."""
        from openjarvis.polybot.state import PgStore, ScopedStore

        store = ScopedStore(PgStore(self.pool), self.cfg.asset)
        while not self._stop.is_set():
            st = await store.get("status")
            hb, other = float(st.get("heartbeat") or 0), st.get("instance")
            if not other or other == self.instance or time.time() - hb > max(90.0, self.cfg.flush_s * 2.5) or st.get("stopped"):
                return
            self.state = "waiting"
            try:
                await asyncio.wait_for(self._stop.wait(), LOCK_RETRY_S)
            except asyncio.TimeoutError:
                pass

    async def _main(self) -> None:
        from openjarvis.polybot.client import PolymarketUS
        from openjarvis.polybot.desk import Desk
        from openjarvis.polybot.state import DDL, PgStore, ScopedStore

        async with self.pool.acquire() as c:
            await c.execute(DDL)
        lock_conn = await self.pool.acquire()                 # held for the desk's whole life: the lock belongs to this connection
        try:
            while not self._stop.is_set():
                if await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_id(self.cfg.asset)):
                    break
                if self.state != "waiting":
                    logger.info("another copy of the desk is running; waiting for it to stop")
                self.state = "waiting"
                try:
                    await asyncio.wait_for(self._stop.wait(), LOCK_RETRY_S)
                except asyncio.TimeoutError:
                    pass
            if self._stop.is_set():
                return
            await self._wait_for_other_copy()       # belt and braces: a pooled connection (pgbouncer) may not keep session locks
            if self._stop.is_set():
                return
            delay = 5.0
            while not self._stop.is_set():
                client = PolymarketUS(self.cfg.key_id, self.cfg.secret_key)
                self.desk = Desk(self.cfg, client, ScopedStore(PgStore(self.pool), self.cfg.asset), notify=ntfy_pusher(self.cfg.notify_url))
                self.desk.instance = self.instance
                self.state = "running"
                try:
                    await self.desk.run()                        # returns only when stopped; it cancels its orders on the way out
                    delay = 5.0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.state = "crashed"
                    logger.exception("the desk crashed; restarting in %.0fs", delay)
                finally:
                    await client.close()
                if not self._stop.is_set():
                    try:
                        await asyncio.wait_for(self._stop.wait(), delay)
                    except asyncio.TimeoutError:
                        pass
                    delay = min(delay * 2, 300.0)
        finally:
            self.state = "stopped"
            try:
                await lock_conn.execute("SELECT pg_advisory_unlock($1)", lock_id(self.cfg.asset))
            except Exception:
                pass
            await self.pool.release(lock_conn)
