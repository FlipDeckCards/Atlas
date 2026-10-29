"""The runner that keeps exactly one desk trading: lock, wait for another copy, start, stop cleanly."""

from __future__ import annotations

import asyncio
import json
import time

from openjarvis.polybot import runtime
from openjarvis.polybot.config import from_env


class Conn:
    def __init__(self, db):
        self.db = db

    async def execute(self, sql, *a):
        if "pg_advisory_unlock_all" in sql:
            self.db["locked"] = False
        elif "INSERT INTO polybot_kv" in sql:
            self.db["kv"][a[0]] = json.loads(a[1])

    async def fetchval(self, sql, *a):
        if "pg_try_advisory_lock" in sql:
            if self.db["locked"]:
                return False
            self.db["locked"] = True
            return True
        if "FROM polybot_kv" in sql:
            v = self.db["kv"].get(a[0])
            return json.dumps(v) if v is not None else None


class Pool:
    def __init__(self, locked=False, status=None):
        self.db = {"locked": locked, "kv": {"status": status} if status else {}}

    def acquire(self):
        pool = self

        class Ctx:
            def __await__(self):
                async def f():
                    return Conn(pool.db)
                return f().__await__()

            async def __aenter__(self):
                return Conn(pool.db)

            async def __aexit__(self, *a):
                return False
        return Ctx()

    async def release(self, conn):
        pass


class FakeDesk:
    started = 0

    def __init__(self, *a, **k):
        self.stopped = asyncio.Event()

    async def run(self):
        FakeDesk.started += 1
        await self.stopped.wait()

    def stop(self):
        self.stopped.set()


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def test_waits_while_another_copy_holds_the_lock(monkeypatch):
    monkeypatch.setattr(runtime, "LOCK_RETRY_S", 0.01)

    async def go():
        r = runtime.Runner(from_env({}), Pool(locked=True))
        r.start()
        await asyncio.sleep(0.05)
        assert r.state == "waiting"
        await r.stop(1)
        assert r.state == "stopped"
    run(go())


def test_waits_for_a_fresh_heartbeat_from_another_copy(monkeypatch):
    monkeypatch.setattr(runtime, "LOCK_RETRY_S", 0.01)

    async def go():
        pool = Pool(status={"heartbeat": time.time(), "instance": "someone-else"})
        r = runtime.Runner(from_env({}), pool)
        r.start()
        await asyncio.sleep(0.05)
        assert r.state == "waiting"
        pool.db["kv"]["status"]["stopped"] = True             # the other copy shut down cleanly
        await asyncio.sleep(0.05)
        await r.stop(1)
    run(go())


def test_runs_one_desk_and_releases_the_lock(monkeypatch):
    import openjarvis.polybot.desk as desk_mod

    monkeypatch.setattr(desk_mod, "Desk", FakeDesk)
    FakeDesk.started = 0

    async def go():
        pool = Pool()
        r = runtime.Runner(from_env({}), pool)
        r.start()
        await asyncio.sleep(0.05)
        assert r.state == "running" and FakeDesk.started == 1 and pool.db["locked"]
        await r.stop(1)
        assert not pool.db["locked"]
    run(go())
