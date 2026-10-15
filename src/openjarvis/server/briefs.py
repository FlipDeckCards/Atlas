"""Brief feed: scheduled briefs are posted here, stored, and shown in the chat with a Listen button.

  POST /api/briefs                  ingest: bearer token must equal BRIEF_INGEST_TOKEN (401 if the variable is unset or the token differs)
  GET  /api/briefs/latest           the latest brief for each period (morning, afternoon, evening); needs a logged-in session
  POST /api/briefs/{id}/listen      reserve one of the user's daily Listens and return the spoken summary to read aloud

Only the ingest route skips the normal login (the auth middleware exempts exactly POST /api/briefs); it has its own token check, done in constant
time, before the body is even parsed, and its own per-IP rate limit. The token is read from the environment only and never logged or returned.
A Listen reads the spoken_summary only. At most BRIEF_LISTEN_DAILY_LIMIT (default 5) Listens per user per UTC day are counted with the existing
spend tracker, and the text-to-speech cost (tts_cost_usd of the summary) is checked against the user's daily spend cap before one is allowed.
"""
from __future__ import annotations

import datetime as dt
import hmac
import logging
import os
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from openjarvis.server import hardening

logger = logging.getLogger(__name__)
router = APIRouter()

PERIODS = ("morning", "afternoon", "evening")
MAX_BODY_CHARS = 20_000
MAX_SPOKEN_CHARS = 600
DEFAULT_LISTEN_LIMIT = 5

BRIEFS_DDL = """
CREATE TABLE IF NOT EXISTS briefs (
    id              UUID PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    product         TEXT NOT NULL,
    period          TEXT NOT NULL CHECK (period IN ('morning', 'afternoon', 'evening')),
    body            TEXT NOT NULL,
    spoken_summary  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS briefs_period_created_idx ON briefs (period, created_at DESC);
"""


class BriefIn(BaseModel):
    product: str = Field(..., min_length=1, max_length=100)
    period: Literal["morning", "afternoon", "evening"]
    body_markdown: str = Field(..., min_length=1, max_length=MAX_BODY_CHARS)
    spoken_summary: str = Field(..., min_length=1, max_length=MAX_SPOKEN_CHARS)


# ── stores ──────────────────────────────────────────────────────────────────────────────────────

def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    created = row["created_at"]
    return {"id": str(row["id"]), "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
            "product": row["product"], "period": row["period"], "body": row["body"], "spoken_summary": row["spoken_summary"]}


class BriefStore:
    """Postgres (asyncpg pool)."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def insert(self, product: str, period: str, body: str, spoken: str) -> Dict[str, Any]:
        bid = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO briefs (id, product, period, body, spoken_summary) VALUES ($1::uuid, $2, $3, $4, $5) RETURNING id, created_at, product, period, body, spoken_summary",
                bid, product, period, body, spoken)
        return _public(dict(row))

    async def latest_per_period(self) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT DISTINCT ON (period) id, created_at, product, period, body, spoken_summary FROM briefs ORDER BY period, created_at DESC, id DESC")
        return [_public(dict(r)) for r in rows]

    async def get(self, brief_id: str) -> Optional[Dict[str, Any]]:
        try:
            uuid.UUID(brief_id)
        except (ValueError, TypeError):
            return None
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow("SELECT id, created_at, product, period, body, spoken_summary FROM briefs WHERE id = $1::uuid", brief_id)
        return _public(dict(r)) if r else None


class MemoryBriefStore:
    def __init__(self, clock=None) -> None:
        self.rows: List[Dict[str, Any]] = []
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))

    async def insert(self, product: str, period: str, body: str, spoken: str) -> Dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "created_at": self.clock(), "product": product, "period": period, "body": body, "spoken_summary": spoken}
        self.rows.append(row)
        return _public(row)

    async def latest_per_period(self) -> List[Dict[str, Any]]:
        out = []
        for p in PERIODS:
            mine = [r for r in self.rows if r["period"] == p]
            if mine:
                out.append(_public(max(mine, key=lambda r: (r["created_at"], r["id"]))))
        return out

    async def get(self, brief_id: str) -> Optional[Dict[str, Any]]:
        return next((_public(r) for r in self.rows if r["id"] == brief_id), None)


def _store(request: Request):
    s = getattr(request.app.state, "brief_store", None)
    return s if s is not None else BriefStore(request.app.state.session_store._pool)


# ── ingest ──────────────────────────────────────────────────────────────────────────────────────

def ingest_token_ok(request: Request) -> bool:
    """True only when BRIEF_INGEST_TOKEN is set and the bearer token equals it (constant-time compare)."""
    want = (os.environ.get("BRIEF_INGEST_TOKEN") or "").strip()
    if not want:
        return False
    scheme, _, given = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not given.strip():
        return False
    return hmac.compare_digest(given.strip().encode("utf-8"), want.encode("utf-8"))


@router.post("/api/briefs")
async def post_brief(request: Request):
    # the token is checked FIRST: a caller without it learns nothing about the body rules
    hit = hardening.enforce({"brief_ip": hardening.client_ip(request)})
    if hit:
        return JSONResponse({"detail": "Too many requests."}, status_code=429, headers={"Retry-After": str(hit[1])})
    if not ingest_token_ok(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = await request.json()
        brief = BriefIn.model_validate(payload)
    except ValidationError as exc:
        return JSONResponse({"detail": "Invalid brief", "errors": [{"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]} for e in exc.errors(include_input=False)]}, status_code=422)
    except ValueError:
        return JSONResponse({"detail": "The body must be JSON."}, status_code=400)
    saved = await _store(request).insert(brief.product.strip(), brief.period, brief.body_markdown, brief.spoken_summary.strip())
    return JSONResponse({"success": True, "id": saved["id"], "created_at": saved["created_at"]}, status_code=201)


# ── read ────────────────────────────────────────────────────────────────────────────────────────

@router.get("/api/briefs/latest")
async def latest(request: Request):
    """The latest brief of each period. The auth middleware has already required a logged-in session."""
    rows = await _store(request).latest_per_period()
    by_period = {p: next((r for r in rows if r["period"] == p), None) for p in PERIODS}
    items = sorted((r for r in rows), key=lambda r: r["created_at"], reverse=True)
    return JSONResponse({"success": True, "briefs": by_period, "items": items})


# ── Listen ──────────────────────────────────────────────────────────────────────────────────────

def listen_limit(env: Optional[dict] = None) -> int:
    raw = ((os.environ if env is None else env).get("BRIEF_LISTEN_DAILY_LIMIT") or "").strip()
    try:
        return max(0, int(raw)) if raw else DEFAULT_LISTEN_LIMIT
    except ValueError:
        return DEFAULT_LISTEN_LIMIT


@router.post("/api/briefs/{brief_id}/listen")
async def listen(brief_id: str, request: Request):
    from openjarvis.server import (
        atlas_ui,  # read at request time: the key may be set or unset
    )

    user_id = getattr(request.state, "user_id", "system")
    brief = await _store(request).get(brief_id)
    if brief is None:
        return JSONResponse({"detail": "That brief was not found."}, status_code=404)
    if not atlas_ui.ELEVENLABS_API_KEY:
        return JSONResponse({"detail": "Text-to-speech is not configured"}, status_code=503)         # nothing is counted
    store = request.app.state.session_store
    day = hardening.utc_day()
    key, limit = "brief_listen:" + user_id, listen_limit()
    used = float(await store.get_spend_today(key, day))
    if used >= limit:
        return JSONResponse({"error": "listen_limit_reached", "detail": f"You have used your {limit} briefing listens for today. They reset at midnight UTC.", "limit": limit}, status_code=429)
    text = brief["spoken_summary"]
    blocked = await hardening.spend_blocked(store, user_id, hardening.tts_cost_usd(len(text)))       # the speech costs money: the daily spend cap applies
    if blocked:
        return JSONResponse(dict(blocked, detail=blocked["detail"]), status_code=429)
    await store.add_spend(key, day, 1.0)                       # the existing spend tracker used as a per-day counter (1.0 per Listen)
    return JSONResponse({"success": True, "text": text, "remaining": max(0, int(limit - used - 1)), "estimated_cost_usd": hardening.tts_cost_usd(len(text))})
