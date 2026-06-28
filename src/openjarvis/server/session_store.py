"""Postgres-backed session store for channel conversations."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import asyncpg
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

_MAX_HISTORY_TURNS = 20


class SessionStore:
    """Manages per-sender, per-channel conversation sessions via Neon Postgres."""

    def __init__(self, database_url: str = "") -> None:
        self._database_url = database_url or os.environ.get("DATABASE_URL", "")
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        """Create connection pool and ensure schema exists. Call once on startup."""
        self._pool = await asyncpg.create_pool(
            self._database_url,
            min_size=2,
            max_size=10,
        )
        await self._create_tables()

    async def _create_tables(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_sessions (
                    sender_id                    TEXT    NOT NULL,
                    channel_type                 TEXT    NOT NULL,
                    conversation_history         TEXT    NOT NULL DEFAULT '[]',
                    preferred_notification_channel TEXT,
                    pending_response             TEXT,
                    created_at                   TIMESTAMP DEFAULT NOW(),
                    updated_at                   TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (sender_id, channel_type)
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
                    ON channel_sessions (updated_at);

                CREATE TABLE IF NOT EXISTS users (
                    id            TEXT PRIMARY KEY,
                    email         TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name  TEXT NOT NULL DEFAULT '',
                    created_at    TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
            """)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    # ------------------------------------------------------------------
    # User auth methods
    # ------------------------------------------------------------------

    async def create_user(
        self,
        user_id: str,
        email: str,
        password_hash: str,
        display_name: str = "",
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (id, email, password_hash, display_name)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (email) DO NOTHING
                """,
                user_id, email, password_hash, display_name,
            )

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE email = $1", email
            )
            if row is None:
                return None
            return {
                "id":            row["id"],
                "email":         row["email"],
                "password_hash": row["password_hash"],
                "display_name":  row["display_name"],
                "created_at":    row["created_at"],
            }

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE id = $1", user_id
            )
            if row is None:
                return None
            return {
                "id":           row["id"],
                "email":        row["email"],
                "display_name": row["display_name"],
                "created_at":   row["created_at"],
            }

    # ------------------------------------------------------------------
    # Session methods
    # ------------------------------------------------------------------

    async def get_or_create(
        self, sender_id: str, channel_type: str
    ) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM channel_sessions "
                "WHERE sender_id = $1 AND channel_type = $2",
                sender_id, channel_type,
            )
            if row is None:
                await conn.execute(
                    "INSERT INTO channel_sessions (sender_id, channel_type) "
                    "VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    sender_id, channel_type,
                )
                return {
                    "sender_id": sender_id,
                    "channel_type": channel_type,
                    "conversation_history": [],
                    "preferred_notification_channel": None,
                    "pending_response": None,
                }
            return {
                "sender_id": row["sender_id"],
                "channel_type": row["channel_type"],
                "conversation_history": json.loads(
                    row["conversation_history"] or "[]"
                ),
                "preferred_notification_channel": row[
                    "preferred_notification_channel"
                ],
                "pending_response": row["pending_response"],
            }

    async def append_message(
        self,
        sender_id: str,
        channel_type: str,
        role: str,
        content: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT conversation_history FROM channel_sessions "
                "WHERE sender_id = $1 AND channel_type = $2",
                sender_id, channel_type,
            )
            if row is None:
                return
            history: List[Dict[str, str]] = json.loads(
                row["conversation_history"] or "[]"
            )
            history.append({"role": role, "content": content})
            if len(history) > _MAX_HISTORY_TURNS:
                history = history[-_MAX_HISTORY_TURNS:]
            await conn.execute(
                "UPDATE channel_sessions "
                "SET conversation_history = $1, updated_at = NOW() "
                "WHERE sender_id = $2 AND channel_type = $3",
                json.dumps(history), sender_id, channel_type,
            )

    async def set_notification_preference(
        self,
        sender_id: str,
        channel_type: str,
        preferred: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE channel_sessions "
                "SET preferred_notification_channel = $1, updated_at = NOW() "
                "WHERE sender_id = $2 AND channel_type = $3",
                preferred, sender_id, channel_type,
            )

    async def set_pending_response(
        self,
        sender_id: str,
        channel_type: str,
        response: Optional[str],
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE channel_sessions "
                "SET pending_response = $1, updated_at = NOW() "
                "WHERE sender_id = $2 AND channel_type = $3",
                response, sender_id, channel_type,
            )

    async def clear_pending_response(
        self, sender_id: str, channel_type: str
    ) -> None:
        await self.set_pending_response(sender_id, channel_type, None)

    async def expire_sessions(self, max_age_hours: int = 24) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE channel_sessions "
                "SET conversation_history = '[]', pending_response = NULL "
                "WHERE updated_at < NOW() - ($1 || ' hours')::INTERVAL",
                str(max_age_hours),
            )
            return int(result.split()[-1])

    async def get_last_active_channel(self, sender_id: str) -> Optional[str]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT channel_type FROM channel_sessions "
                "WHERE sender_id = $1 "
                "ORDER BY updated_at DESC LIMIT 1",
                sender_id,
            )
            return row["channel_type"] if row else None

    async def get_notification_targets(self) -> List[Dict[str, str]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT sender_id, channel_type, "
                "preferred_notification_channel "
                "FROM channel_sessions "
                "WHERE preferred_notification_channel IS NOT NULL"
            )
            return [dict(r) for r in rows]