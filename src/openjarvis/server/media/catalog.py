"""The media catalog: one row per saved item (table media_items). Every lookup is scoped to the owner, so another user's id is simply "not found".

The table is created with the rest of the app's tables (SessionStore._create_tables runs MEDIA_ITEMS_DDL). Sizes are counted from this table for the
per-user quota. MemoryCatalog has the same interface for tests.
"""
from __future__ import annotations

import base64
import datetime as dt
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MEDIA_ITEMS_DDL = """
CREATE TABLE IF NOT EXISTS media_items (
    id               UUID PRIMARY KEY,
    user_id          TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('image', 'sound', 'video')),
    title            TEXT NOT NULL,
    prompt           TEXT,
    provider         TEXT,
    mime_type        TEXT NOT NULL,
    size_bytes       BIGINT NOT NULL,
    storage_key      TEXT NOT NULL,
    duration_seconds REAL,
    edited_from_id   UUID REFERENCES media_items (id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS media_items_user_created_idx ON media_items (user_id, created_at);
"""

KINDS = ("image", "sound", "video")
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "audio/mpeg": "mp3", "video/mp4": "mp4"}


@dataclass
class MediaItem:
    id: str
    user_id: str
    kind: str
    title: str
    mime_type: str
    size_bytes: int
    storage_key: str
    prompt: Optional[str] = None
    provider: Optional[str] = None
    duration_seconds: Optional[float] = None
    edited_from_id: Optional[str] = None
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def public(self) -> Dict[str, Any]:
        """What the browser may see: never the owner id or the storage key."""
        d = asdict(self)
        d.pop("user_id"), d.pop("storage_key")
        d["created_at"] = self.created_at.isoformat()
        return d


def new_id() -> str:
    return str(uuid.uuid4())


def valid_id(value: str) -> bool:
    try:
        return str(uuid.UUID(str(value))) == str(value).lower()
    except (ValueError, AttributeError, TypeError):
        return False


def storage_key_for(user_id: str, item_id: str, mime: str) -> str:
    return f"users/{user_id}/{item_id}.{EXTENSIONS.get(mime, 'bin')}"


def encode_cursor(created_at: dt.datetime, item_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{item_id}".encode()).decode()


def decode_cursor(cursor: Optional[str]) -> Optional[Tuple[dt.datetime, str]]:
    if not cursor:
        return None
    try:
        ts, item_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        if not valid_id(item_id):
            return None
        return dt.datetime.fromisoformat(ts), item_id
    except Exception:
        return None


_COLS = "id, user_id, kind, title, prompt, provider, mime_type, size_bytes, storage_key, duration_seconds, edited_from_id, created_at"


def _row(r) -> MediaItem:
    return MediaItem(
        id=str(r["id"]), user_id=r["user_id"], kind=r["kind"], title=r["title"], prompt=r["prompt"], provider=r["provider"],
        mime_type=r["mime_type"], size_bytes=int(r["size_bytes"]), storage_key=r["storage_key"], duration_seconds=r["duration_seconds"],
        edited_from_id=str(r["edited_from_id"]) if r["edited_from_id"] else None, created_at=r["created_at"],
    )


class MediaCatalog:
    """Postgres catalog (asyncpg pool)."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def insert(self, item: MediaItem) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO media_items ({_COLS}) VALUES ($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::uuid,$12)",
                item.id, item.user_id, item.kind, item.title, item.prompt, item.provider, item.mime_type, item.size_bytes,
                item.storage_key, item.duration_seconds, item.edited_from_id, item.created_at,
            )

    async def get(self, user_id: str, item_id: str) -> Optional[MediaItem]:
        if not valid_id(item_id):
            return None
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow(f"SELECT {_COLS} FROM media_items WHERE id = $1::uuid AND user_id = $2", item_id, user_id)
        return _row(r) if r else None

    async def list(self, user_id: str, kind: Optional[str] = None, limit: int = 30, cursor: Optional[str] = None) -> Tuple[List[MediaItem], Optional[str]]:
        cur = decode_cursor(cursor)
        args: List[Any] = [user_id]
        sql = f"SELECT {_COLS} FROM media_items WHERE user_id = $1"
        if kind in KINDS:
            args.append(kind)
            sql += f" AND kind = ${len(args)}"
        if cur:
            args += [cur[0], cur[1]]
            sql += f" AND (created_at, id) < (${len(args) - 1}, ${len(args)}::uuid)"
        args.append(limit + 1)
        sql += f" ORDER BY created_at DESC, id DESC LIMIT ${len(args)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        items = [_row(r) for r in rows]
        more = len(items) > limit
        items = items[:limit]
        return items, (encode_cursor(items[-1].created_at, items[-1].id) if more and items else None)

    async def delete(self, user_id: str, item_id: str) -> bool:
        async with self._pool.acquire() as conn:
            r = await conn.execute("DELETE FROM media_items WHERE id = $1::uuid AND user_id = $2", item_id, user_id)
        return str(r).endswith(" 1")

    async def total_bytes(self, user_id: str) -> int:
        async with self._pool.acquire() as conn:
            v = await conn.fetchval("SELECT COALESCE(SUM(size_bytes), 0) FROM media_items WHERE user_id = $1", user_id)
        return int(v or 0)


class MemoryCatalog:
    def __init__(self) -> None:
        self.items: Dict[str, MediaItem] = {}

    async def insert(self, item: MediaItem) -> None:
        self.items[item.id] = item

    async def get(self, user_id: str, item_id: str) -> Optional[MediaItem]:
        it = self.items.get(str(item_id))
        return it if it and it.user_id == user_id else None

    async def list(self, user_id: str, kind: Optional[str] = None, limit: int = 30, cursor: Optional[str] = None) -> Tuple[List[MediaItem], Optional[str]]:
        rows = sorted((i for i in self.items.values() if i.user_id == user_id and (kind not in KINDS or i.kind == kind)), key=lambda i: (i.created_at, i.id), reverse=True)
        cur = decode_cursor(cursor)
        if cur:
            rows = [i for i in rows if (i.created_at, i.id) < cur]
        more = len(rows) > limit
        rows = rows[:limit]
        return rows, (encode_cursor(rows[-1].created_at, rows[-1].id) if more and rows else None)

    async def delete(self, user_id: str, item_id: str) -> bool:
        it = self.items.get(str(item_id))
        if it and it.user_id == user_id:
            del self.items[str(item_id)]
            for other in self.items.values():
                if other.edited_from_id == str(item_id):
                    other.edited_from_id = None
            return True
        return False

    async def total_bytes(self, user_id: str) -> int:
        return sum(i.size_bytes for i in self.items.values() if i.user_id == user_id)
