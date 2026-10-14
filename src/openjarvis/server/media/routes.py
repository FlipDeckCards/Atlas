"""Media routes: POST /api/media/image, POST /api/media/sound, GET /api/media/status. There is no video route.

Same protection as the chat (the auth middleware, per-user and per-IP rate limits, input caps) plus its own switch (MEDIA_ENABLED) and
its own spend cap (DAILY_MEDIA_SPEND_CAP_USD, default 0 = off). The cap is checked BEFORE any provider call. Generated files are NOT stored:
the bytes go back in the answer (base64) and the page shows them; they are gone on reload. Nothing is written to Render's disk.
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from openjarvis.server import hardening
from openjarvis.server.media import image as image_mod
from openjarvis.server.media import (
    media_cap_usd,
    media_enabled,
    media_global_cap_usd,
    over_cap,
)
from openjarvis.server.media import sound as sound_mod
from openjarvis.server.media.base import MediaError

logger = logging.getLogger(__name__)
router = APIRouter()

GLOBAL_KEY = "media:__all__"


class ImageRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=image_mod.MAX_PROMPT_CHARS)
    steps: Optional[int] = None


class SoundRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=sound_mod.MAX_TEXT_CHARS)
    lang: Optional[str] = "en"


def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "system")


def _err(status: int, error: str, detail: str, **extra) -> JSONResponse:
    return JSONResponse(dict(error=error, detail=detail, **extra), status_code=status)


async def _gate(request: Request, provider, estimate: float):
    """Everything that must hold before a provider is called. Returns (error_response | None, store, user_key)."""
    if not media_enabled():
        return (
            _err(403, "media_disabled", "Media generation is turned off."),
            None,
            None,
        )
    cap = media_cap_usd()
    if cap <= 0:
        return (
            _err(
                403,
                "media_disabled",
                "Media generation is turned off (no media budget is set).",
            ),
            None,
            None,
        )
    uid = _user_id(request)
    hit = hardening.enforce(
        {"media_user": uid, "media_ip": hardening.client_ip(request)}
    )
    if hit:
        return (
            JSONResponse(
                {
                    "error": "rate_limited",
                    "detail": "Too many requests. Please slow down.",
                },
                status_code=429,
                headers={"Retry-After": str(hit[1])},
            ),
            None,
            None,
        )
    if not provider.configured():
        return (
            _err(503, "media_not_configured", "This media provider is not configured."),
            None,
            None,
        )
    store = request.app.state.session_store
    day = hardening.utc_day()
    user_key = "media:" + uid
    blocked = await over_cap(store, user_key, estimate, cap, day)
    if blocked is None:
        blocked = await over_cap(
            store, GLOBAL_KEY, estimate, media_global_cap_usd(), day
        )
    if blocked:
        return JSONResponse(blocked, status_code=429), None, None
    return None, store, user_key


async def _finish(store, user_key: str, cost: float, result) -> JSONResponse:
    day = hardening.utc_day()
    await store.add_spend(user_key, day, cost)
    await store.add_spend(GLOBAL_KEY, day, cost)
    return JSONResponse(
        {
            "success": True,
            "kind": result.kind,
            "mime": result.mime,
            "provider": result.provider,
            "data_base64": base64.b64encode(result.data).decode("ascii"),
            "estimated_cost_usd": cost,
            "stored": False,
            "note": "Not saved: this file is shown now and is gone when you reload.",
        }
    )


@router.get("/api/media/status")
async def media_status(request: Request):
    on = media_enabled() and media_cap_usd() > 0
    img, snd = image_mod.get_provider(), sound_mod.get_provider()
    return JSONResponse(
        {
            "enabled": on,
            "image": bool(on and img.configured()),
            "sound": bool(on and snd.configured()),
            "video": False,
            "video_note": "Video generation is not available.",
        }
    )


@router.post("/api/media/image")
async def make_image(req: ImageRequest, request: Request):
    provider = image_mod.get_provider()
    prompt, steps = image_mod.clean_prompt(req.prompt), image_mod.clamp_steps(req.steps)
    if not prompt:
        return _err(422, "bad_request", "Describe the image you want.")
    cost = provider.estimate_cost_usd(steps=steps)
    err, store, user_key = await _gate(request, provider, cost)
    if err:
        return err
    try:
        result = await provider.generate(prompt=prompt, steps=steps)
    except MediaError as exc:
        return _err(exc.status_code, "media_failed", exc.public_message)
    except Exception:
        logger.exception(
            "image generation failed"
        )  # details stay in the log (the provider code never logs keys)
        return _err(
            502, "media_failed", "The image could not be made. Please try again."
        )
    return await _finish(store, user_key, cost, result)


@router.post("/api/media/sound")
async def make_sound(req: SoundRequest, request: Request):
    provider = sound_mod.get_provider()
    text, lang = sound_mod.clean_text(req.text), sound_mod.clean_lang(req.lang)
    if not text:
        return _err(422, "bad_request", "Give the text to turn into speech.")
    cost = provider.estimate_cost_usd(text=text)
    err, store, user_key = await _gate(request, provider, cost)
    if err:
        return err
    try:
        result = await provider.generate(text=text, lang=lang)
    except MediaError as exc:
        return _err(exc.status_code, "media_failed", exc.public_message)
    except Exception:
        logger.exception("sound generation failed")
        return _err(
            502, "media_failed", "The sound could not be made. Please try again."
        )
    return await _finish(store, user_key, cost, result)
