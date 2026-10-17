"""Video jobs. Video takes minutes, so it is never done inside one request: a job is created (the request returns a job id at once) and the
browser polls the job. The server advances a job when it is polled (it asks the provider; when the provider is done it fetches the file and
saves it to the library). There is no background worker to lose when the server restarts: the job row survives and the next poll carries on.

The table is created with the rest of the app's tables (SessionStore._create_tables runs MEDIA_JOBS_DDL). MemoryJobs has the same interface for tests.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

MEDIA_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS media_jobs (
    id              UUID PRIMARY KEY,
    user_id         TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'video',
    status          TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    prompt          TEXT NOT NULL,
    seconds         INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    provider        TEXT NOT NULL,
    provider_job_id TEXT NOT NULL,
    item_id         UUID,
    error           TEXT,
    cost_usd        NUMERIC(12,6) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS media_jobs_user_idx ON media_jobs (user_id, created_at);
"""

RUNNING, SUCCEEDED, FAILED = "running", "succeeded", "failed"


@dataclass
class MediaJob:
    id: str
    user_id: str
    prompt: str
    seconds: int
    height: int
    provider: str
    provider_job_id: str
    status: str = RUNNING
    kind: str = "video"
    item_id: Optional[str] = None
    error: Optional[str] = None
    cost_usd: float = 0.0
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    updated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def public(self) -> Dict[str, Any]:
        """What the browser may see: the job id and its state, never the owner id or the provider's own job id."""
        d = asdict(self)
        for hidden in ("user_id", "provider_job_id", "provider"):
            d.pop(hidden)
        d["job_id"] = d.pop("id")
        d["created_at"], d["updated_at"] = self.created_at.isoformat(), self.updated_at.isoformat()
        return d


_COLS = "id, user_id, kind, status, prompt, seconds, height, provider, provider_job_id, item_id, error, cost_usd, created_at, updated_at"


def _row(r) -> MediaJob:
    return MediaJob(
        id=str(r["id"]), user_id=r["user_id"], kind=r["kind"], status=r["status"], prompt=r["prompt"], seconds=int(r["seconds"]), height=int(r["height"]),
        provider=r["provider"], provider_job_id=r["provider_job_id"], item_id=str(r["item_id"]) if r["item_id"] else None, error=r["error"],
        cost_usd=float(r["cost_usd"] or 0), created_at=r["created_at"], updated_at=r["updated_at"],
    )


class MediaJobs:
    """Postgres jobs (asyncpg pool). Every lookup is scoped to the owner: another user's job id is simply 'not found'."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def insert(self, job: MediaJob) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO media_jobs ({_COLS}) VALUES ($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10::uuid,$11,$12,$13,$14)",
                job.id, job.user_id, job.kind, job.status, job.prompt, job.seconds, job.height, job.provider, job.provider_job_id, job.item_id, job.error,
                job.cost_usd, job.created_at, job.updated_at,
            )

    async def get(self, user_id: str, job_id: str) -> Optional[MediaJob]:
        from openjarvis.server.media.catalog import valid_id

        if not valid_id(job_id):
            return None
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow(f"SELECT {_COLS} FROM media_jobs WHERE id = $1::uuid AND user_id = $2", job_id, user_id)
        return _row(r) if r else None

    async def update(self, job: MediaJob) -> None:
        job.updated_at = dt.datetime.now(dt.timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE media_jobs SET status=$3, item_id=$4::uuid, error=$5, updated_at=$6 WHERE id = $1::uuid AND user_id = $2",
                job.id, job.user_id, job.status, job.item_id, job.error, job.updated_at,
            )

    async def count_running(self, user_id: str) -> int:
        async with self._pool.acquire() as conn:
            v = await conn.fetchval("SELECT COUNT(*) FROM media_jobs WHERE user_id = $1 AND status = 'running'", user_id)
        return int(v or 0)

    async def list(self, user_id: str, limit: int = 20) -> List[MediaJob]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT {_COLS} FROM media_jobs WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2", user_id, limit)
        return [_row(r) for r in rows]


class MemoryJobs:
    def __init__(self) -> None:
        self.jobs: Dict[str, MediaJob] = {}

    async def insert(self, job: MediaJob) -> None:
        self.jobs[job.id] = job

    async def get(self, user_id: str, job_id: str) -> Optional[MediaJob]:
        j = self.jobs.get(str(job_id))
        return j if j and j.user_id == user_id else None

    async def update(self, job: MediaJob) -> None:
        job.updated_at = dt.datetime.now(dt.timezone.utc)
        self.jobs[job.id] = job

    async def count_running(self, user_id: str) -> int:
        return sum(1 for j in self.jobs.values() if j.user_id == user_id and j.status == RUNNING)

    async def list(self, user_id: str, limit: int = 20) -> List[MediaJob]:
        return sorted((j for j in self.jobs.values() if j.user_id == user_id), key=lambda j: j.created_at, reverse=True)[:limit]
