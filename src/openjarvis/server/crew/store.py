"""Where the crew's state lives: missions, tasks, questions for the owner, the event ledger, finished outputs, settings and spend.

Two stores with the same methods: CrewStore (Postgres, the asyncpg pool the app already has) and MemoryCrewStore (tests).
Everything is per user: no method returns another user's rows. Tables are created add-only (IF NOT EXISTS).
A task is claimed with a conditional UPDATE (queued -> running), so two engine loops can never run the same task.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Dict, Iterable, List, Optional

CREW_DDL = """
CREATE TABLE IF NOT EXISTS crew_missions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    goal        TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    project     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL,
    budget_usd  NUMERIC(12,6) NOT NULL DEFAULT 0,
    spent_usd   NUMERIC(12,6) NOT NULL DEFAULT 0,
    summary     TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS crew_missions_user_idx ON crew_missions (user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS crew_tasks (
    id          TEXT PRIMARY KEY,
    mission_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    role        TEXT NOT NULL,
    brief       TEXT NOT NULL,
    depends_on  TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL,
    budget_usd  NUMERIC(12,6) NOT NULL DEFAULT 0,
    spent_usd   NUMERIC(12,6) NOT NULL DEFAULT 0,
    result      TEXT NOT NULL DEFAULT '',
    transcript  TEXT NOT NULL DEFAULT '[]',
    runs        INTEGER NOT NULL DEFAULT 0,
    claimed_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS crew_tasks_mission_idx ON crew_tasks (mission_id, idx);
CREATE INDEX IF NOT EXISTS crew_tasks_status_idx ON crew_tasks (status);
CREATE TABLE IF NOT EXISTS crew_questions (
    id          TEXT PRIMARY KEY,
    mission_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL,
    options     TEXT NOT NULL DEFAULT '[]',
    action      TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    answer      TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS crew_questions_user_idx ON crew_questions (user_id, status, created_at DESC);
CREATE TABLE IF NOT EXISTS crew_events (
    id          BIGSERIAL PRIMARY KEY,
    mission_id  TEXT NOT NULL DEFAULT '',
    task_id     TEXT NOT NULL DEFAULT '',
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL,
    cost_usd    NUMERIC(12,6) NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS crew_events_user_idx ON crew_events (user_id, id DESC);
CREATE TABLE IF NOT EXISTS crew_outputs (
    id          TEXT PRIMARY KEY,
    mission_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS crew_outputs_mission_idx ON crew_outputs (mission_id, created_at);
CREATE TABLE IF NOT EXISTS crew_settings (
    user_id     TEXT PRIMARY KEY,
    night_shift BOOLEAN NOT NULL DEFAULT FALSE,
    paused      BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS crew_spend (
    user_id TEXT NOT NULL,
    day     DATE NOT NULL,
    usd     NUMERIC(12,6) NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS crew_secrets (
    user_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, name)
);
"""

MISSION_FIELDS = {"title", "project", "status", "budget_usd", "summary"}
TASK_FIELDS = {"title", "brief", "status", "budget_usd", "result", "transcript", "runs", "claimed_at"}
OPEN_TASK = ("queued", "running", "waiting")


def new_id() -> str:
    return uuid.uuid4().hex


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _loads(v: Any, default: Any) -> Any:
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def _mission(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "user_id": r["user_id"], "goal": r["goal"], "title": r["title"], "project": r["project"],
            "status": r["status"], "budget_usd": round(float(r["budget_usd"]), 6), "spent_usd": round(float(r["spent_usd"]), 6),
            "summary": r["summary"], "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"])}


def _task(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "mission_id": r["mission_id"], "user_id": r["user_id"], "idx": int(r["idx"]), "title": r["title"],
            "role": r["role"], "brief": r["brief"], "depends_on": _loads(r["depends_on"], []), "status": r["status"],
            "budget_usd": round(float(r["budget_usd"]), 6), "spent_usd": round(float(r["spent_usd"]), 6), "result": r["result"],
            "transcript": _loads(r["transcript"], []), "runs": int(r["runs"]), "claimed_at": _iso(r["claimed_at"]),
            "updated_at": _iso(r["updated_at"])}


def _question(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "mission_id": r["mission_id"], "task_id": r["task_id"], "user_id": r["user_id"], "kind": r["kind"],
            "text": r["text"], "options": _loads(r["options"], []), "action": _loads(r["action"], None), "status": r["status"],
            "answer": r["answer"], "created_at": _iso(r["created_at"]), "answered_at": _iso(r["answered_at"])}


def _event(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": int(r["id"]), "mission_id": r["mission_id"], "task_id": r["task_id"], "kind": r["kind"], "text": r["text"],
            "cost_usd": round(float(r["cost_usd"]), 6), "created_at": _iso(r["created_at"])}


def _output(r: Dict[str, Any], with_content: bool = False) -> Dict[str, Any]:
    out = {"id": r["id"], "mission_id": r["mission_id"], "task_id": r["task_id"], "name": r["name"],
           "size": len(r["content"] or ""), "created_at": _iso(r["created_at"])}
    if with_content:
        out["content"] = r["content"]
    return out


def deps_done(task: Dict[str, Any], siblings: Iterable[Dict[str, Any]]) -> bool:
    by_idx = {t["idx"]: t["status"] for t in siblings}
    return all(by_idx.get(int(i)) == "done" for i in task["depends_on"])


def _as_date(day) -> dt.date:
    return day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day)[:10])


# ── Postgres ─────────────────────────────────────────────────────────────────────────────────────

class CrewStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def ensure_tables(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREW_DDL)

    # missions
    async def create_mission(self, user_id: str, goal: str, budget_usd: float, title: str = "", project: str = "",
                             status: str = "planning") -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow(
                "INSERT INTO crew_missions (id, user_id, goal, title, project, status, budget_usd) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *",
                new_id(), user_id, goal, title, project, status, budget_usd)
        return _mission(dict(r))

    async def get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow("SELECT * FROM crew_missions WHERE id = $1", mission_id)
        return _mission(dict(r)) if r else None

    async def list_missions(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM crew_missions WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2", user_id, limit)
        return [_mission(dict(r)) for r in rows]

    async def update_mission(self, mission_id: str, **fields) -> None:
        fields = {k: v for k, v in fields.items() if k in MISSION_FIELDS}
        if not fields:
            return
        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
        async with self._pool.acquire() as conn:
            await conn.execute(f"UPDATE crew_missions SET {sets}, updated_at = NOW() WHERE id = $1", mission_id, *fields.values())

    async def add_mission_spend(self, mission_id: str, usd: float) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("UPDATE crew_missions SET spent_usd = spent_usd + $2, updated_at = NOW() WHERE id = $1", mission_id, usd)

    # tasks
    async def create_tasks(self, mission_id: str, user_id: str, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for t in tasks:
                    r = await conn.fetchrow(
                        "INSERT INTO crew_tasks (id, mission_id, user_id, idx, title, role, brief, depends_on, status, budget_usd) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'queued',$9) RETURNING *",
                        new_id(), mission_id, user_id, t["idx"], t["title"], t["role"], t["brief"], json.dumps(t["depends_on"]), t["budget_usd"])
                    out.append(_task(dict(r)))
        return out

    async def list_tasks(self, mission_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM crew_tasks WHERE mission_id = $1 ORDER BY idx", mission_id)
        return [_task(dict(r)) for r in rows]

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow("SELECT * FROM crew_tasks WHERE id = $1", task_id)
        return _task(dict(r)) if r else None

    async def update_task(self, task_id: str, **fields) -> None:
        fields = {k: v for k, v in fields.items() if k in TASK_FIELDS}
        if not fields:
            return
        if "transcript" in fields:
            fields["transcript"] = json.dumps(fields["transcript"])
        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
        async with self._pool.acquire() as conn:
            await conn.execute(f"UPDATE crew_tasks SET {sets}, updated_at = NOW() WHERE id = $1", task_id, *fields.values())

    async def add_task_spend(self, task_id: str, usd: float) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("UPDATE crew_tasks SET spent_usd = spent_usd + $2, updated_at = NOW() WHERE id = $1", task_id, usd)

    async def queued_candidates(self) -> List[Dict[str, Any]]:
        """Queued tasks of active missions, oldest first, with their mission's sibling statuses for the dependency check."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT t.* FROM crew_tasks t JOIN crew_missions m ON m.id = t.mission_id "
                "WHERE t.status = 'queued' AND m.status = 'active' ORDER BY t.updated_at, t.idx LIMIT 50")
            tasks = [_task(dict(r)) for r in rows]
            mids = list({t["mission_id"] for t in tasks})
            sib = await conn.fetch("SELECT mission_id, idx, status FROM crew_tasks WHERE mission_id = ANY($1::text[])", mids) if mids else []
        by_m: Dict[str, List[Dict[str, Any]]] = {}
        for r in sib:
            by_m.setdefault(r["mission_id"], []).append({"idx": int(r["idx"]), "status": r["status"]})
        return [t for t in tasks if deps_done(t, by_m.get(t["mission_id"], []))]

    async def claim(self, task_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow(
                "UPDATE crew_tasks SET status = 'running', claimed_at = NOW(), updated_at = NOW() WHERE id = $1 AND status = 'queued' RETURNING *",
                task_id)
        return _task(dict(r)) if r else None

    async def requeue_stale(self, older_than_s: int) -> int:
        async with self._pool.acquire() as conn:
            res = await conn.execute(
                "UPDATE crew_tasks SET status = 'queued', updated_at = NOW() WHERE status = 'running' "
                "AND (claimed_at IS NULL OR claimed_at < NOW() - ($1 || ' seconds')::INTERVAL) "
                "AND (role <> 'workbench' OR claimed_at < NOW() - INTERVAL '5 hours')", str(int(older_than_s)))
        return int(res.split()[-1])

    async def set_mission_tasks_status(self, mission_id: str, from_statuses: Iterable[str], to_status: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("UPDATE crew_tasks SET status = $3, updated_at = NOW() WHERE mission_id = $1 AND status = ANY($2::text[])",
                               mission_id, list(from_statuses), to_status)

    # questions
    async def add_question(self, mission_id: str, task_id: str, user_id: str, kind: str, text: str,
                           options: Optional[List[str]] = None, action: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow(
                "INSERT INTO crew_questions (id, mission_id, task_id, user_id, kind, text, options, action) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *",
                new_id(), mission_id, task_id, user_id, kind, text, json.dumps(options or []), json.dumps(action) if action is not None else None)
        return _question(dict(r))

    async def get_question(self, question_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow("SELECT * FROM crew_questions WHERE id = $1", question_id)
        return _question(dict(r)) if r else None

    async def list_questions(self, user_id: str, status: str = "open", limit: int = 30) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM crew_questions WHERE user_id = $1 AND status = $2 ORDER BY created_at LIMIT $3", user_id, status, limit)
        return [_question(dict(r)) for r in rows]

    async def close_question(self, question_id: str, status: str, answer: str) -> Optional[Dict[str, Any]]:
        """open -> status, once. Returns None when the question was already closed (a double tap does nothing twice)."""
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow(
                "UPDATE crew_questions SET status = $2, answer = $3, answered_at = NOW() WHERE id = $1 AND status = 'open' RETURNING *",
                question_id, status, answer)
        return _question(dict(r)) if r else None

    async def expire_mission_questions(self, mission_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("UPDATE crew_questions SET status = 'expired', answered_at = NOW() WHERE mission_id = $1 AND status = 'open'", mission_id)

    # events
    async def add_event(self, user_id: str, kind: str, text: str, mission_id: str = "", task_id: str = "", cost_usd: float = 0.0) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("INSERT INTO crew_events (mission_id, task_id, user_id, kind, text, cost_usd) VALUES ($1,$2,$3,$4,$5,$6)",
                               mission_id, task_id, user_id, kind, text[:2000], cost_usd)

    async def cost_by_mission(self, user_id: str, since) -> Dict[str, float]:
        """Crew spend per mission since a moment (every priced step, plan and research run is an event with its cost)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT mission_id, COALESCE(SUM(cost_usd), 0) AS usd FROM crew_events WHERE user_id = $1 AND created_at >= $2 "
                                    "GROUP BY mission_id", user_id, since)
        return {r["mission_id"]: float(r["usd"]) for r in rows}

    async def list_events(self, user_id: str, mission_id: str = "", limit: int = 60) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            if mission_id:
                rows = await conn.fetch("SELECT * FROM crew_events WHERE user_id = $1 AND mission_id = $2 ORDER BY id DESC LIMIT $3", user_id, mission_id, limit)
            else:
                rows = await conn.fetch("SELECT * FROM crew_events WHERE user_id = $1 ORDER BY id DESC LIMIT $2", user_id, limit)
        return [_event(dict(r)) for r in rows]

    # outputs
    async def add_output(self, mission_id: str, task_id: str, user_id: str, name: str, content: str) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow("INSERT INTO crew_outputs (id, mission_id, task_id, user_id, name, content) VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
                                    new_id(), mission_id, task_id, user_id, name, content)
        return _output(dict(r))

    async def list_outputs(self, mission_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM crew_outputs WHERE mission_id = $1 ORDER BY created_at", mission_id)
        return [_output(dict(r)) for r in rows]

    async def list_user_outputs(self, user_id: str, limit: int = 300) -> List[Dict[str, Any]]:
        """Every file in this user's outboxes, across all their missions, newest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM crew_outputs WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2", user_id, limit)
        return [_output(dict(r)) for r in rows]

    async def get_output(self, output_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow("SELECT * FROM crew_outputs WHERE id = $1", output_id)
        return dict(_output(dict(r), with_content=True), user_id=r["user_id"]) if r else None

    # settings, spend, secrets
    async def get_settings(self, user_id: str) -> Dict[str, bool]:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow("SELECT night_shift, paused FROM crew_settings WHERE user_id = $1", user_id)
        return {"night_shift": bool(r["night_shift"]), "paused": bool(r["paused"])} if r else {"night_shift": False, "paused": False}

    async def set_settings(self, user_id: str, night_shift: bool, paused: bool) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO crew_settings (user_id, night_shift, paused) VALUES ($1,$2,$3) "
                "ON CONFLICT (user_id) DO UPDATE SET night_shift = EXCLUDED.night_shift, paused = EXCLUDED.paused, updated_at = NOW()",
                user_id, night_shift, paused)

    async def get_day_spend(self, user_id: str, day) -> float:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow("SELECT usd FROM crew_spend WHERE user_id = $1 AND day = $2", user_id, _as_date(day))
        return float(r["usd"]) if r else 0.0

    async def month_spend(self, user_id: str, since) -> float:
        async with self._pool.acquire() as conn:
            v = await conn.fetchval("SELECT COALESCE(SUM(usd), 0) FROM crew_spend WHERE user_id = $1 AND day >= $2", user_id, _as_date(since))
        return float(v or 0)

    async def add_day_spend(self, user_id: str, day, usd: float) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("INSERT INTO crew_spend (user_id, day, usd) VALUES ($1,$2,$3) "
                               "ON CONFLICT (user_id, day) DO UPDATE SET usd = crew_spend.usd + EXCLUDED.usd", user_id, _as_date(day), usd)

    async def get_secret(self, user_id: str, name: str) -> Optional[str]:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow("SELECT value FROM crew_secrets WHERE user_id = $1 AND name = $2", user_id, name)
        return r["value"] if r else None

    async def set_secret(self, user_id: str, name: str, value: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("INSERT INTO crew_secrets (user_id, name, value) VALUES ($1,$2,$3) "
                               "ON CONFLICT (user_id, name) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()", user_id, name, value)

    async def delete_secret(self, user_id: str, name: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM crew_secrets WHERE user_id = $1 AND name = $2", user_id, name)


# ── in memory (tests) ────────────────────────────────────────────────────────────────────────────

class MemoryCrewStore:
    def __init__(self, clock=None) -> None:
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.missions: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.questions: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []
        self.outputs: Dict[str, Dict[str, Any]] = {}
        self.settings: Dict[str, Dict[str, bool]] = {}
        self.spend: Dict[Any, float] = {}
        self.secrets: Dict[Any, str] = {}
        self._seq = 0

    def _tick(self):
        self._seq += 1
        return self.clock() + dt.timedelta(microseconds=self._seq)

    async def ensure_tables(self) -> None:
        return None

    async def create_mission(self, user_id, goal, budget_usd, title="", project="", status="planning"):
        now = self._tick()
        r = {"id": new_id(), "user_id": user_id, "goal": goal, "title": title, "project": project, "status": status,
             "budget_usd": float(budget_usd), "spent_usd": 0.0, "summary": "", "created_at": now, "updated_at": now}
        self.missions[r["id"]] = r
        return _mission(r)

    async def get_mission(self, mission_id):
        r = self.missions.get(mission_id)
        return _mission(r) if r else None

    async def list_missions(self, user_id, limit=20):
        rows = sorted((m for m in self.missions.values() if m["user_id"] == user_id), key=lambda m: m["created_at"], reverse=True)
        return [_mission(r) for r in rows[:limit]]

    async def update_mission(self, mission_id, **fields):
        r = self.missions.get(mission_id)
        if r:
            r.update({k: v for k, v in fields.items() if k in MISSION_FIELDS}, updated_at=self._tick())

    async def add_mission_spend(self, mission_id, usd):
        r = self.missions.get(mission_id)
        if r:
            r["spent_usd"] += float(usd)

    async def create_tasks(self, mission_id, user_id, tasks):
        out = []
        for t in tasks:
            now = self._tick()
            r = {"id": new_id(), "mission_id": mission_id, "user_id": user_id, "idx": t["idx"], "title": t["title"], "role": t["role"],
                 "brief": t["brief"], "depends_on": json.dumps(t["depends_on"]), "status": "queued", "budget_usd": float(t["budget_usd"]),
                 "spent_usd": 0.0, "result": "", "transcript": "[]", "runs": 0, "claimed_at": None, "updated_at": now}
            self.tasks[r["id"]] = r
            out.append(_task(r))
        return out

    async def list_tasks(self, mission_id):
        return [_task(r) for r in sorted((t for t in self.tasks.values() if t["mission_id"] == mission_id), key=lambda t: t["idx"])]

    async def get_task(self, task_id):
        r = self.tasks.get(task_id)
        return _task(r) if r else None

    async def update_task(self, task_id, **fields):
        r = self.tasks.get(task_id)
        if not r:
            return
        fields = {k: v for k, v in fields.items() if k in TASK_FIELDS}
        if "transcript" in fields:
            fields["transcript"] = json.dumps(fields["transcript"])
        r.update(fields, updated_at=self._tick())

    async def add_task_spend(self, task_id, usd):
        r = self.tasks.get(task_id)
        if r:
            r["spent_usd"] += float(usd)

    async def queued_candidates(self):
        active = {m["id"] for m in self.missions.values() if m["status"] == "active"}
        q = sorted((t for t in self.tasks.values() if t["status"] == "queued" and t["mission_id"] in active), key=lambda t: (t["updated_at"], t["idx"]))
        out = []
        for r in q:
            t = _task(r)
            sib = [{"idx": s["idx"], "status": s["status"]} for s in self.tasks.values() if s["mission_id"] == t["mission_id"]]
            if deps_done(t, sib):
                out.append(t)
        return out

    async def claim(self, task_id):
        r = self.tasks.get(task_id)
        if not r or r["status"] != "queued":
            return None
        r.update(status="running", claimed_at=self.clock(), updated_at=self._tick())
        return _task(r)

    async def requeue_stale(self, older_than_s):
        n = 0
        for r in self.tasks.values():
            age = None if r["claimed_at"] is None else (self.clock() - r["claimed_at"]).total_seconds()
            if r["role"] == "workbench" and age is not None and age < 5 * 3600:
                continue                                            # a PC job can run for hours; the PC reports when it is done
            if r["status"] == "running" and (age is None or age >= older_than_s):
                r.update(status="queued", updated_at=self._tick())
                n += 1
        return n

    async def set_mission_tasks_status(self, mission_id, from_statuses, to_status):
        fs = set(from_statuses)
        for r in self.tasks.values():
            if r["mission_id"] == mission_id and r["status"] in fs:
                r.update(status=to_status, updated_at=self._tick())

    async def add_question(self, mission_id, task_id, user_id, kind, text, options=None, action=None):
        r = {"id": new_id(), "mission_id": mission_id, "task_id": task_id, "user_id": user_id, "kind": kind, "text": text,
             "options": json.dumps(options or []), "action": json.dumps(action) if action is not None else None, "status": "open",
             "answer": "", "created_at": self._tick(), "answered_at": None}
        self.questions[r["id"]] = r
        return _question(r)

    async def get_question(self, question_id):
        r = self.questions.get(question_id)
        return _question(r) if r else None

    async def list_questions(self, user_id, status="open", limit=30):
        rows = sorted((q for q in self.questions.values() if q["user_id"] == user_id and q["status"] == status), key=lambda q: q["created_at"])
        return [_question(r) for r in rows[:limit]]

    async def close_question(self, question_id, status, answer):
        r = self.questions.get(question_id)
        if not r or r["status"] != "open":
            return None
        r.update(status=status, answer=answer, answered_at=self.clock())
        return _question(r)

    async def expire_mission_questions(self, mission_id):
        for r in self.questions.values():
            if r["mission_id"] == mission_id and r["status"] == "open":
                r.update(status="expired", answered_at=self.clock())

    async def add_event(self, user_id, kind, text, mission_id="", task_id="", cost_usd=0.0):
        self.events.append({"id": len(self.events) + 1, "mission_id": mission_id, "task_id": task_id, "user_id": user_id, "kind": kind,
                            "text": text[:2000], "cost_usd": float(cost_usd), "created_at": self._tick()})

    async def cost_by_mission(self, user_id, since):
        out: Dict[str, float] = {}
        for e in self.events:
            if e["user_id"] == user_id and e["created_at"] >= since:
                out[e["mission_id"]] = out.get(e["mission_id"], 0.0) + float(e["cost_usd"])
        return out

    async def list_events(self, user_id, mission_id="", limit=60):
        rows = [e for e in reversed(self.events) if e["user_id"] == user_id and (not mission_id or e["mission_id"] == mission_id)]
        return [_event(r) for r in rows[:limit]]

    async def add_output(self, mission_id, task_id, user_id, name, content):
        r = {"id": new_id(), "mission_id": mission_id, "task_id": task_id, "user_id": user_id, "name": name, "content": content, "created_at": self._tick()}
        self.outputs[r["id"]] = r
        return _output(r)

    async def list_outputs(self, mission_id):
        return [_output(r) for r in sorted((o for o in self.outputs.values() if o["mission_id"] == mission_id), key=lambda o: o["created_at"])]

    async def list_user_outputs(self, user_id, limit=300):
        return [_output(r) for r in sorted((o for o in self.outputs.values() if o["user_id"] == user_id), key=lambda o: o["created_at"], reverse=True)[:limit]]

    async def get_output(self, output_id):
        r = self.outputs.get(output_id)
        return dict(_output(r, with_content=True), user_id=r["user_id"]) if r else None

    async def get_settings(self, user_id):
        return dict(self.settings.get(user_id, {"night_shift": False, "paused": False}))

    async def set_settings(self, user_id, night_shift, paused):
        self.settings[user_id] = {"night_shift": bool(night_shift), "paused": bool(paused)}

    async def get_day_spend(self, user_id, day):
        return self.spend.get((user_id, str(day)[:10]), 0.0)

    async def month_spend(self, user_id, since):
        s = str(since)[:10]
        return sum(v for (u, d), v in self.spend.items() if u == user_id and d >= s)

    async def add_day_spend(self, user_id, day, usd):
        k = (user_id, str(day)[:10])
        self.spend[k] = self.spend.get(k, 0.0) + float(usd)

    async def get_secret(self, user_id, name):
        return self.secrets.get((user_id, name))

    async def set_secret(self, user_id, name, value):
        self.secrets[(user_id, name)] = value

    async def delete_secret(self, user_id, name):
        self.secrets.pop((user_id, name), None)
