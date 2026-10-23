"""Backup image providers for the fallback chain: Google Gemini image, then OpenAI image.

They are used only when the provider before them cannot make the image (its free quota is used up, it is busy or it is down).
Keys are read from the environment only (GEMINI_API_KEY or GOOGLE_API_KEY, OPENAI_API_KEY), sent only in the request headers,
and never logged or returned. Errors are logged with the HTTP status only.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Optional

import httpx

from openjarvis.server.media.base import MediaError, MediaResult

logger = logging.getLogger(__name__)

TIMEOUT_S = 90.0
# Rough per-image prices on paid plans (checked when written; the spend cap uses these before each call)
GEMINI_USD_PER_IMAGE = 0.04
OPENAI_USD_PER_IMAGE = 0.05


def _key(names: tuple, env: Optional[dict] = None) -> str:
    e = os.environ if env is None else env
    for n in names:
        v = (e.get(n) or "").strip()
        if v:
            return v
    return ""


def _fail(provider: str, status: int) -> MediaError:
    logger.error("%s answered HTTP %s for an image", provider, status)
    if status in (429, 503):
        return MediaError("The media provider is busy or its free quota for today is used up. Please try again later.", 503)
    return MediaError("The media provider could not make that. Please try again.", 502)


def _unusable(provider: str) -> MediaError:
    logger.error("%s returned no usable image", provider)
    return MediaError("The media provider returned something unusable.", 502)


class GeminiImage:
    kind = "image"
    id = "google_gemini_image"
    model = "gemini-2.5-flash-image"

    def configured(self) -> bool:
        return bool(_key(("GEMINI_API_KEY", "GOOGLE_API_KEY")))

    def estimate_cost_usd(self, **_: Any) -> float:
        return GEMINI_USD_PER_IMAGE

    async def generate(self, prompt: str, client: Optional[httpx.AsyncClient] = None, **_: Any) -> MediaResult:
        key = _key(("GEMINI_API_KEY", "GOOGLE_API_KEY"))
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        try:
            if client is not None:
                r = await client.post(url, json=body, headers=headers, timeout=TIMEOUT_S)
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
                    r = await c.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.error("Gemini image request failed (%s)", type(exc).__name__)
            raise MediaError("The media provider could not be reached. Please try again.", 502) from None
        if r.status_code != 200:
            raise _fail("Gemini", r.status_code)
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
        except Exception:
            raise _unusable("Gemini") from None
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                try:
                    data = base64.b64decode(inline["data"])
                except Exception:
                    raise _unusable("Gemini") from None
                mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                return MediaResult("image", mime, data, self.id)
        raise _unusable("Gemini")


class OpenAIImage:
    kind = "image"
    id = "openai_gpt_image"
    model = "gpt-image-1"

    def configured(self) -> bool:
        return bool(_key(("OPENAI_API_KEY",)))

    def estimate_cost_usd(self, **_: Any) -> float:
        return OPENAI_USD_PER_IMAGE

    async def generate(self, prompt: str, client: Optional[httpx.AsyncClient] = None, **_: Any) -> MediaResult:
        key = _key(("OPENAI_API_KEY",))
        body = {"model": self.model, "prompt": prompt, "size": "1024x1024", "quality": "medium", "n": 1}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        url = "https://api.openai.com/v1/images/generations"
        try:
            if client is not None:
                r = await client.post(url, json=body, headers=headers, timeout=TIMEOUT_S)
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
                    r = await c.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.error("OpenAI image request failed (%s)", type(exc).__name__)
            raise MediaError("The media provider could not be reached. Please try again.", 502) from None
        if r.status_code != 200:
            raise _fail("OpenAI", r.status_code)
        try:
            b64 = r.json()["data"][0]["b64_json"]
            data = base64.b64decode(b64)
        except Exception:
            raise _unusable("OpenAI") from None
        return MediaResult("image", "image/png", data, self.id)
