"""The media library's logic, shared by the HTTP routes and Sol's tools so both obey the same rules:

  flag and media cap -> rate limit -> provider configured -> per-user and global daily cap -> storage quota -> (provider call) -> save.

Nothing is returned to the browser without a catalog row; every item belongs to one user and a lookup for anyone else is "not found".
"""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from openjarvis.server import hardening
from openjarvis.server.media import (
    edits,
    media_cap_usd,
    media_enabled,
    media_global_cap_usd,
    over_cap,
)
from openjarvis.server.media import (
    image as image_mod,
)
from openjarvis.server.media import (
    sound as sound_mod,
)
from openjarvis.server.media.base import MediaError
from openjarvis.server.media.catalog import (
    MediaCatalog,
    MediaItem,
    new_id,
    storage_key_for,
    valid_id,
)
from openjarvis.server.media.storage import StorageError, storage_from_env

logger = logging.getLogger(__name__)

GLOBAL_KEY = "media:__all__"
DEFAULT_QUOTA_BYTES = 1024 ** 3                    # 1 GB per user
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
LIST_DEFAULT, LIST_MAX = 30, 100


class MediaServiceError(Exception):
    def __init__(self, status: int, error: str, detail: str, headers: Optional[Dict[str, str]] = None, **extra: Any) -> None:
        super().__init__(detail)
        self.status, self.error, self.detail, self.headers, self.extra = status, error, detail, headers or {}, extra

    def body(self) -> Dict[str, Any]:
        return dict(error=self.error, detail=self.detail, **self.extra)


def quota_bytes(env: Optional[dict] = None) -> int:
    raw = ((os.environ if env is None else env).get("MEDIA_USER_QUOTA_BYTES") or "").strip()
    try:
        return max(0, int(raw)) if raw else DEFAULT_QUOTA_BYTES
    except ValueError:
        return DEFAULT_QUOTA_BYTES


@dataclass
class Ctx:
    user_id: str
    ip: str
    store: Any            # the session store (spend ledger)
    catalog: Any
    storage: Any


def ctx_from_request(request, paid: bool = False) -> Ctx:
    """The caller's context. The switches come first, so with media off the answer is 403 whether or not storage is set up.
    paid=True is for routes that can call a provider: they also need a media budget above zero."""
    require_flag()
    if paid and media_cap_usd() <= 0:
        raise MediaServiceError(403, "media_disabled", "Media generation is turned off (no media budget is set).")
    app = request.app
    store = app.state.session_store
    storage = getattr(app.state, "media_storage", None) or storage_from_env()
    if storage is None:
        raise MediaServiceError(503, "media_storage_not_configured", "Media storage is not configured.")
    catalog = getattr(app.state, "media_catalog", None)
    if catalog is None:
        catalog = MediaCatalog(store._pool)
    return Ctx(getattr(request.state, "user_id", "system"), hardening.client_ip(request), store, catalog, storage)


# ── gates ───────────────────────────────────────────────────────────────────────────────────────

def require_flag() -> None:
    if not media_enabled():
        raise MediaServiceError(403, "media_disabled", "Media generation is turned off.")


def rate_limit(ctx: Ctx, group: str = "media") -> None:
    """group: 'media' (generation), 'media_read' (browsing) or 'media_edit' (edits that cost no provider call)."""
    hit = hardening.enforce({f"{group}_user": ctx.user_id, f"{group}_ip": ctx.ip})
    if hit:
        raise MediaServiceError(429, "rate_limited", "Too many requests. Please slow down.", {"Retry-After": str(hit[1])})


async def check_quota(ctx: Ctx, extra: int = 0) -> None:
    used = await ctx.catalog.total_bytes(ctx.user_id)
    cap = quota_bytes()
    if used + extra > cap or (extra == 0 and used >= cap):          # extra=0 asks 'is there any room at all?' (before a paid call)
        raise MediaServiceError(
            413, "storage_quota_reached",
            f"Your media library is full ({used / 1024 ** 2:.0f} MB of {cap / 1024 ** 2:.0f} MB). Delete something to make room.",
            used_bytes=used, quota_bytes=cap,
        )


async def gate_generation(ctx: Ctx, provider, estimate: float) -> None:
    """Everything that must hold before a paid provider call."""
    require_flag()
    cap = media_cap_usd()
    if cap <= 0:
        raise MediaServiceError(403, "media_disabled", "Media generation is turned off (no media budget is set).")
    rate_limit(ctx)
    if not provider.configured():
        raise MediaServiceError(503, "media_not_configured", "This media provider is not configured.")
    day = hardening.utc_day()
    blocked = await over_cap(ctx.store, "media:" + ctx.user_id, estimate, cap, day) or await over_cap(ctx.store, GLOBAL_KEY, estimate, media_global_cap_usd(), day)
    if blocked:
        raise MediaServiceError(429, blocked["error"], blocked["detail"], cap_usd=blocked["cap_usd"], spent_usd=blocked["spent_usd"])
    await check_quota(ctx)                    # no point paying for something that cannot be saved


def estimate_cost(kind: str, **request: Any) -> float:
    provider = image_mod.get_provider() if kind == "image" else sound_mod.get_provider()
    return provider.estimate_cost_usd(**request)


def make_title(text: str, limit: int = 60) -> str:
    t = " ".join(str(text or "").split()).strip(" .,;:-") or "Untitled"
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


# ── save ────────────────────────────────────────────────────────────────────────────────────────

async def save_item(ctx: Ctx, *, kind: str, data: bytes, mime: str, title: str, prompt: Optional[str], provider: Optional[str],
                    duration: Optional[float] = None, edited_from_id: Optional[str] = None) -> MediaItem:
    await check_quota(ctx, len(data))
    item_id = new_id()
    key = storage_key_for(ctx.user_id, item_id, mime)
    try:
        await ctx.storage.put(key, data, mime)
    except StorageError as exc:
        raise MediaServiceError(exc.status_code, "storage_failed", exc.public_message) from None
    item = MediaItem(id=item_id, user_id=ctx.user_id, kind=kind, title=make_title(title, 80), prompt=prompt, provider=provider, mime_type=mime,
                     size_bytes=len(data), storage_key=key, duration_seconds=duration, edited_from_id=edited_from_id)
    try:
        await ctx.catalog.insert(item)
    except Exception:
        logger.exception("media catalog insert failed")
        try:
            await ctx.storage.delete(key)                      # no orphan file
        except StorageError:
            pass
        raise MediaServiceError(500, "catalog_failed", "The item could not be saved.") from None
    return item


async def generate_and_save(ctx: Ctx, kind: str, *, prompt: str = "", steps: Optional[int] = None, text: str = "", lang: Optional[str] = None,
                            edited_from_id: Optional[str] = None) -> MediaItem:
    if kind == "image":
        provider, p, st = image_mod.get_provider(), image_mod.clean_prompt(prompt), image_mod.clamp_steps(steps)
        if not p:
            raise MediaServiceError(422, "bad_request", "Describe the image you want.")
        request, estimate, label = {"prompt": p, "steps": st}, provider.estimate_cost_usd(steps=st), p
    elif kind == "sound":
        provider, t = sound_mod.get_provider(), sound_mod.clean_text(text)
        if not t:
            raise MediaServiceError(422, "bad_request", "Give the text to turn into speech.")
        request, estimate, label = {"text": t, "lang": sound_mod.clean_lang(lang)}, provider.estimate_cost_usd(text=t), t
    else:
        raise MediaServiceError(400, "unsupported", "Video is not supported.")
    await gate_generation(ctx, provider, estimate)
    try:
        result = await provider.generate(**request)
    except MediaError as exc:
        raise MediaServiceError(exc.status_code, "media_failed", exc.public_message) from None
    except Exception:
        logger.exception("%s generation failed", kind)
        raise MediaServiceError(502, "media_failed", "That could not be made. Please try again.") from None
    day = hardening.utc_day()                                   # the provider call happened: the cost counts even if saving fails
    await ctx.store.add_spend("media:" + ctx.user_id, day, estimate)
    await ctx.store.add_spend(GLOBAL_KEY, day, estimate)
    duration = await _duration(result.data) if kind == "sound" else None
    item = await save_item(ctx, kind=kind, data=result.data, mime=result.mime, title=label, prompt=label, provider=result.provider,
                           duration=duration, edited_from_id=edited_from_id)
    return item


async def _duration(data: bytes) -> Optional[float]:
    import asyncio

    try:
        return await asyncio.to_thread(edits.probe_duration, data)
    except Exception:
        return None


# ── library ─────────────────────────────────────────────────────────────────────────────────────

async def get_owned(ctx: Ctx, item_id: str) -> MediaItem:
    item = await ctx.catalog.get(ctx.user_id, item_id) if valid_id(item_id) else None
    if item is None:
        raise MediaServiceError(404, "not_found", "That item was not found.")           # also what another user's id gets
    return item


def with_url(ctx: Ctx, item: MediaItem, download: bool = False) -> Dict[str, Any]:
    d = item.public()
    ext = item.storage_key.rsplit(".", 1)[-1]
    d["url"] = ctx.storage.presign_get(item.storage_key, filename=f"{make_title(item.title, 40)}.{ext}".replace('"', "") if download else None)
    d["url_expires_in"] = 15 * 60
    return d


async def list_items(ctx: Ctx, kind: Optional[str], limit: Optional[int], cursor: Optional[str]) -> Dict[str, Any]:
    require_flag()
    rate_limit(ctx, "media_read")
    n = max(1, min(LIST_MAX, int(limit) if limit else LIST_DEFAULT))
    items, nxt = await ctx.catalog.list(ctx.user_id, kind if kind in ("image", "sound", "video") else None, n, cursor)
    return {"items": [with_url(ctx, i) for i in items], "next_cursor": nxt, "used_bytes": await ctx.catalog.total_bytes(ctx.user_id), "quota_bytes": quota_bytes()}


async def delete_item(ctx: Ctx, item_id: str) -> None:
    require_flag()
    rate_limit(ctx, "media_edit")
    item = await get_owned(ctx, item_id)
    try:
        await ctx.storage.delete(item.storage_key)
    except StorageError as exc:
        raise MediaServiceError(exc.status_code, "storage_failed", exc.public_message) from None      # keep the row: the file is still there
    await ctx.catalog.delete(ctx.user_id, item.id)


async def edit_item(ctx: Ctx, item_id: str, operation: str, params: Optional[Dict[str, Any]], preview: bool = False) -> Dict[str, Any]:
    """Preview (nothing saved) or save as a NEW item with edited_from_id. The original is never changed."""
    require_flag()
    rate_limit(ctx, "media_edit")
    item = await get_owned(ctx, item_id)
    if item.kind not in ("image", "sound"):
        raise MediaServiceError(400, "unsupported", "Video cannot be edited: video is not supported.")
    try:
        original = await ctx.storage.get(item.storage_key)
    except StorageError as exc:
        raise MediaServiceError(exc.status_code, "storage_failed", exc.public_message) from None
    try:
        data, mime, duration = await edits.apply_edit(item.kind, item.mime_type, original, operation, params)
    except edits.EditError as exc:
        raise MediaServiceError(exc.status_code, "bad_edit", exc.message) from None
    if preview:
        if len(data) > MAX_PREVIEW_BYTES:
            raise MediaServiceError(413, "too_large", "That preview is too large.")
        return {"preview": {"mime": mime, "data_base64": base64.b64encode(data).decode("ascii"), "size_bytes": len(data), "duration_seconds": duration}, "saved": False}
    new = await save_item(ctx, kind=item.kind, data=data, mime=mime, title=f"{item.title} ({operation.replace('_', ' ')})", prompt=item.prompt,
                          provider="edit:" + operation, duration=duration, edited_from_id=item.id)
    return {"item": with_url(ctx, new), "saved": True}


async def regenerate_item(ctx: Ctx, item_id: str, prompt: str) -> MediaItem:
    item = await get_owned(ctx, item_id)
    if item.kind == "image":
        return await generate_and_save(ctx, "image", prompt=prompt, edited_from_id=item.id)
    if item.kind == "sound":
        return await generate_and_save(ctx, "sound", text=prompt, edited_from_id=item.id)
    raise MediaServiceError(400, "unsupported", "Video is not supported.")
