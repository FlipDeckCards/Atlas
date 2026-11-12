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

TIMEOUT_S = 150.0      # a 4K Nano Banana Pro picture can take a minute or two
# Rough per-image prices on paid plans (checked when written; the spend cap uses these before each call)
GEMINI_USD_PER_IMAGE = 0.04
GEMINI_PRO_4K_USD_PER_IMAGE = 0.24    # a 4K picture; GEMINI_PRO_IMAGE_4K_USD overrides it (same source as the price below)
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
    supports_image_size = False

    def configured(self) -> bool:
        return bool(_key(("GEMINI_API_KEY", "GOOGLE_API_KEY")))

    def model_name(self) -> str:
        return self.model

    def estimate_cost_usd(self, **_: Any) -> float:
        return GEMINI_USD_PER_IMAGE

    async def generate(self, prompt: str, client: Optional[httpx.AsyncClient] = None, reference: Optional[tuple] = None, aspect_ratio: Optional[str] = None, image_size: Optional[str] = None, **_: Any) -> MediaResult:
        """reference: (bytes, mime) of a picture to build on, or a list of them (reference-image mode). They go in the same request as the prompt.
        aspect_ratio: for example "21:9" for a wide strip. image_size: "1K", "2K" or "4K" (Pro only). If the API refuses either, the request
        is repeated without the size, then without both."""
        key = _key(("GEMINI_API_KEY", "GOOGLE_API_KEY"))
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name()}:generateContent"
        parts: list = []
        refs = [] if reference is None else (list(reference) if isinstance(reference, list) else [reference])
        for ref_data, ref_mime in refs:
            parts.append({"inlineData": {"mimeType": ref_mime or "image/png", "data": base64.b64encode(ref_data).decode("ascii")}})
        parts.append({"text": prompt})
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

        size = image_size if self.supports_image_size else None

        async def post(ratio: Optional[str], px: Optional[str]) -> httpx.Response:
            cfg: dict = {"responseModalities": list(self.modalities)}
            conf = {k: v for k, v in (("aspectRatio", ratio), ("imageSize", px)) if v}
            if conf:
                cfg["imageConfig"] = conf
            body = {"contents": [{"parts": parts}], "generationConfig": cfg}
            if client is not None:
                return await client.post(url, json=body, headers=headers, timeout=TIMEOUT_S)
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
                return await c.post(url, json=body, headers=headers)

        try:
            tries = list(dict.fromkeys([(aspect_ratio, size), (aspect_ratio, None), (None, None)]))
            for i, (ratio, px) in enumerate(tries):
                r = await post(ratio, px)
                if r.status_code != 400 or i == len(tries) - 1:
                    break
                logger.warning("Gemini refused the image settings (%s, %s); repeating with fewer", ratio, px)
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
    supports_image_size = True

    def model_name(self) -> str:
        return _key(("GEMINI_PRO_IMAGE_MODEL",)) or self.model

    def estimate_cost_usd(self, image_size: Optional[str] = None, **_: Any) -> float:
        big = str(image_size or "").upper() == "4K"
        name, default = ("GEMINI_PRO_IMAGE_4K_USD", GEMINI_PRO_4K_USD_PER_IMAGE) if big else ("GEMINI_PRO_IMAGE_USD", GEMINI_PRO_USD_PER_IMAGE)
        try:
            return max(0.0, float(_key((name,)) or default))
        except ValueError:
            return default

    async def generate(self, prompt: str, client: Optional[httpx.AsyncClient] = None, reference: Optional[tuple] = None, aspect_ratio: Optional[str] = None, image_size: Optional[str] = None, **_: Any) -> MediaResult:
        try:
            return await super().generate(prompt, client=client, reference=reference, aspect_ratio=aspect_ratio, image_size=image_size)
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
