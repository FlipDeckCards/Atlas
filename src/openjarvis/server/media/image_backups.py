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
GEMINI_PRO_USD_PER_IMAGE = 0.134      # Nano Banana Pro, 1K/2K picture: from a developer guide, not confirmed on Google's pricing page. GEMINI_PRO_IMAGE_USD overrides it.
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
    err = MediaError("The media provider could not make that. Please try again.", 502)
    err.http_status = status
    return err


def _unusable(provider: str) -> MediaError:
    logger.error("%s returned no usable image", provider)
    return MediaError("The media provider returned something unusable.", 502)


class GeminiImage:
    kind = "image"
    id = "google_gemini_image"
    model = "gemini-2.5-flash-image"

    modalities = ["IMAGE"]

    def configured(self) -> bool:
        return bool(_key(("GEMINI_API_KEY", "GOOGLE_API_KEY")))

    def model_name(self) -> str:
        return self.model

    def estimate_cost_usd(self, **_: Any) -> float:
        return GEMINI_USD_PER_IMAGE

    async def generate(self, prompt: str, client: Optional[httpx.AsyncClient] = None, reference: Optional[tuple] = None, aspect_ratio: Optional[str] = None, **_: Any) -> MediaResult:
        """reference: (bytes, mime) of a picture to build on (reference-image mode). The picture goes in the same request as the prompt.
        aspect_ratio: for example "21:9" for a wide strip; if the API refuses it the request is repeated without it."""
        key = _key(("GEMINI_API_KEY", "GOOGLE_API_KEY"))
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name()}:generateContent"
        parts: list = []
        if reference is not None:
            ref_data, ref_mime = reference
            parts.append({"inlineData": {"mimeType": ref_mime or "image/png", "data": base64.b64encode(ref_data).decode("ascii")}})
        parts.append({"text": prompt})
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

        async def post(ratio: Optional[str]) -> httpx.Response:
            cfg: dict = {"responseModalities": list(self.modalities)}
            if ratio:
                cfg["imageConfig"] = {"aspectRatio": ratio}
            body = {"contents": [{"parts": parts}], "generationConfig": cfg}
            if client is not None:
                return await client.post(url, json=body, headers=headers, timeout=TIMEOUT_S)
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
                return await c.post(url, json=body, headers=headers)

        try:
            r = await post(aspect_ratio)
            if r.status_code == 400 and aspect_ratio:
                logger.warning("Gemini refused aspect ratio %s; repeating without it", aspect_ratio)
                r = await post(None)
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
            if part.get("thought"):                    # a draft the model shows while it thinks (Nano Banana Pro): not the answer
                continue
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                try:
                    data = base64.b64decode(inline["data"])
                except Exception:
                    raise _unusable("Gemini") from None
                mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                return MediaResult("image", mime, data, self.id)
        raise _unusable("Gemini")


class GeminiProImage(GeminiImage):
    """Nano Banana Pro (Gemini 3 Pro Image): much better at keeping a character's design and art style from a reference picture.
    Used for work on an attached picture (variations, edits, sprite sheets). If Google refuses the model for this key (404, 403 or 400), the
    request is made once on the cheaper Flash image model instead, and the picture is charged at Flash's price."""

    id = "google_gemini_pro_image"
    model = "gemini-3-pro-image"
    modalities = ["TEXT", "IMAGE"]

    def model_name(self) -> str:
        return _key(("GEMINI_PRO_IMAGE_MODEL",)) or self.model

    def estimate_cost_usd(self, **_: Any) -> float:
        try:
            return max(0.0, float(_key(("GEMINI_PRO_IMAGE_USD",)) or GEMINI_PRO_USD_PER_IMAGE))
        except ValueError:
            return GEMINI_PRO_USD_PER_IMAGE

    async def generate(self, prompt: str, client: Optional[httpx.AsyncClient] = None, reference: Optional[tuple] = None, aspect_ratio: Optional[str] = None, **_: Any) -> MediaResult:
        try:
            return await super().generate(prompt, client=client, reference=reference, aspect_ratio=aspect_ratio)
        except MediaError as exc:
            if getattr(exc, "http_status", None) not in (400, 403, 404):
                raise
            logger.warning("Gemini Pro image model refused (HTTP %s); using the Flash image model for this picture", exc.http_status)
            result = await GeminiImage().generate(prompt, client=client, reference=reference, aspect_ratio=aspect_ratio)
            result.cost_usd = GEMINI_USD_PER_IMAGE
            return result


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
