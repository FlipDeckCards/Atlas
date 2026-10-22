"""Media routes. All need the user's token (the auth middleware covers /api/*), and every item belongs to one user.

  POST   /api/media/image, /api/media/sound      make something and save it to the library (shortcuts used by /image and /sound)
  GET    /api/media/status                       what is switched on
  GET    /api/media/library?kind=&limit=&cursor= the caller's own items, newest first
  GET    /api/media/items/{id}[?download=1]      metadata and a presigned URL (15 minutes)
  POST   /api/media/items/{id}/edit              preview or save an edit as a NEW item
  POST   /api/media/items/{id}/regenerate        make a new item from an edited prompt (counts against the daily media cap)
  DELETE /api/media/items/{id}                   remove the file and the row

There is no video route. Nothing is returned without a catalog row.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from openjarvis.server.media import image as image_mod
from openjarvis.server.media import media_cap_usd, media_enabled, service
from openjarvis.server.media import sound as sound_mod
from openjarvis.server.media.service import MediaServiceError

logger = logging.getLogger(__name__)
router = APIRouter()


class ImageRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=image_mod.MAX_PROMPT_CHARS)
    steps: Optional[int] = None


class SoundRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=sound_mod.MAX_TEXT_CHARS)
    lang: Optional[str] = "en"


class EditRequest(BaseModel):
    operation: str = Field(..., min_length=1, max_length=20)
    params: Dict[str, Any] = Field(default_factory=dict)
    preview: bool = False


class RegenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=max(image_mod.MAX_PROMPT_CHARS, sound_mod.MAX_TEXT_CHARS))


def _fail(exc: MediaServiceError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.status, headers=exc.headers or None)


async def _guard(fn):
    try:
        return await fn()
    except MediaServiceError as exc:
        return _fail(exc)
    except Exception:
        logger.exception("media route failed")
        return JSONResponse({"error": "media_failed", "detail": "That could not be done. Please try again."}, status_code=500)


@router.get("/api/media/status")
async def media_status(request: Request):
    on = media_enabled() and media_cap_usd() > 0
    img, snd = image_mod.get_provider(), sound_mod.get_provider()
    return JSONResponse({
        "enabled": media_enabled(),
        "generation": on,
        "image": bool(on and img.configured()),
        "sound": bool(on and snd.configured()),
        "video": False,
        "video_note": "Video generation is not available.",
        # what one paid action is estimated to cost (the Re-generate button shows it before asking)
        "estimates": {"image": img.estimate_cost_usd(steps=image_mod.DEFAULT_STEPS), "sound_per_1000_chars": snd.estimate_cost_usd(text="x" * 1000)},
    })


@router.post("/api/media/image")
async def make_image(req: ImageRequest, request: Request):
    async def go():
        ctx = service.ctx_from_request(request, paid=True)
        item = await service.generate_and_save(ctx, "image", prompt=req.prompt, steps=req.steps)
        return JSONResponse({"success": True, "item": service.with_url(ctx, item)})
    return await _guard(go)


@router.post("/api/media/sound")
async def make_sound(req: SoundRequest, request: Request):
    async def go():
        ctx = service.ctx_from_request(request, paid=True)
        item = await service.generate_and_save(ctx, "sound", text=req.text, lang=req.lang)
        return JSONResponse({"success": True, "item": service.with_url(ctx, item)})
    return await _guard(go)


@router.get("/api/media/library")
async def library(request: Request, kind: Optional[str] = Query(None, max_length=10), limit: Optional[int] = Query(None, ge=1, le=100),
                  cursor: Optional[str] = Query(None, max_length=200)):
    async def go():
        ctx = service.ctx_from_request(request)
        return JSONResponse({"success": True, **await service.list_items(ctx, kind, limit, cursor)})
    return await _guard(go)


@router.get("/api/media/items/{item_id}")
async def get_item(item_id: str, request: Request, download: bool = False):
    async def go():
        ctx = service.ctx_from_request(request)
        service.rate_limit(ctx, "media_read")
        item = await service.get_owned(ctx, item_id)
        return JSONResponse({"success": True, "item": service.with_url(ctx, item, download=download)})
    return await _guard(go)


@router.get("/api/media/items/{item_id}/file")
async def get_item_file(item_id: str, request: Request):
    """The file itself, read by the server with its own signed request and sent with the owner's sign-in. The Station loads images
    this way (a browser <img> cannot send the sign-in), so no storage link has to work in the browser."""
    async def go():
        ctx = service.ctx_from_request(request)
        service.rate_limit(ctx, "media_read")
        item = await service.get_owned(ctx, item_id)
        try:
            data = await ctx.storage.get(item.storage_key)
        except Exception as exc:
            logger.error("media file read failed for item %s: %s", item_id, getattr(exc, "public_message", None) or type(exc).__name__)
            return JSONResponse({"error": "storage_failed", "detail": "The file could not be read from storage."}, status_code=502)
        return Response(content=data, media_type=item.mime_type or "application/octet-stream",
                        headers={"Cache-Control": "private, max-age=600"})
    return await _guard(go)


@router.post("/api/media/items/{item_id}/edit")
async def edit_item(item_id: str, req: EditRequest, request: Request):
    async def go():
        ctx = service.ctx_from_request(request)
        return JSONResponse({"success": True, **await service.edit_item(ctx, item_id, req.operation, req.params, req.preview)})
    return await _guard(go)


@router.post("/api/media/items/{item_id}/regenerate")
async def regenerate_item(item_id: str, req: RegenerateRequest, request: Request):
    async def go():
        ctx = service.ctx_from_request(request, paid=True)
        item = await service.regenerate_item(ctx, item_id, req.prompt)
        return JSONResponse({"success": True, "item": service.with_url(ctx, item)})
    return await _guard(go)


@router.delete("/api/media/items/{item_id}")
async def delete_item(item_id: str, request: Request):
    async def go():
        ctx = service.ctx_from_request(request)
        await service.delete_item(ctx, item_id)
        return JSONResponse({"success": True})
    return await _guard(go)
