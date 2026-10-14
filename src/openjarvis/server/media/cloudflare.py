"""Cloudflare Workers AI providers: FLUX.1 [schnell] for images and MeloTTS for speech.

Why these two (licenses, free quota, source URLs and dates) is in MEDIA-PROVIDERS.md. The account id and API token are read from the
environment only (CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN), sent only in the Authorization header, and never logged or returned.
Errors are logged with the HTTP status only; the browser gets a fixed sentence.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Optional

import httpx

from openjarvis.server.media.base import MediaError, MediaNotConfigured, MediaResult

logger = logging.getLogger(__name__)

API_BASE = "https://api.cloudflare.com/client/v4/accounts"
TIMEOUT_S = 60.0
USD_PER_NEURON = (
    0.011 / 1000
)  # Workers Paid overage price; the free plan itself charges nothing

# neuron prices from Cloudflare's pricing page (checked 2026-10-13)
FLUX_NEURONS_PER_STEP = 9.60
FLUX_NEURONS_PER_TILE = 4.80  # per 512x512 output tile; a 1024x1024 image is 4 tiles
FLUX_TILES = 4  # assumed output size: 1024x1024 (the high estimate)
MELO_NEURONS_PER_MINUTE = 18.63
CHARS_PER_MINUTE = 900  # ~15 characters of speech per second


def _creds(env: Optional[dict] = None) -> Optional[tuple]:
    e = os.environ if env is None else env
    acct = (e.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = (e.get("CLOUDFLARE_API_TOKEN") or "").strip()
    return (acct, token) if acct and token else None


async def _run(
    model: str, payload: dict, client: Optional[httpx.AsyncClient] = None
) -> httpx.Response:
    creds = _creds()
    if not creds:
        raise MediaNotConfigured("Cloudflare Workers AI")
    acct, token = creds
    url = f"{API_BASE}/{acct}/ai/run/{model}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        if client is not None:
            r = await client.post(url, json=payload, headers=headers, timeout=TIMEOUT_S)
        else:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
                r = await c.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.error(
            "Cloudflare Workers AI request failed (%s)", type(exc).__name__
        )  # the exception text could echo the URL; the class is enough
        raise MediaError(
            "The media provider could not be reached. Please try again.", 502
        ) from None
    if r.status_code != 200:
        logger.error(
            "Cloudflare Workers AI answered HTTP %s for %s", r.status_code, model
        )
        if r.status_code == 429:
            raise MediaError(
                "The media provider is busy or its free quota for today is used up. Please try again later.",
                503,
            )
        raise MediaError(
            "The media provider could not make that. Please try again.", 502
        )
    return r


def _b64(value: Any, what: str) -> bytes:
    try:
        return base64.b64decode(value, validate=False) if value else b""
    except Exception:
        logger.error("Cloudflare Workers AI returned undecodable %s", what)
        raise MediaError(
            "The media provider returned something unusable.", 502
        ) from None


class CloudflareFluxSchnell:
    kind = "image"
    id = "cloudflare_flux_schnell"
    model = "@cf/black-forest-labs/flux-1-schnell"

    def configured(self) -> bool:
        return _creds() is not None

    def estimate_cost_usd(self, steps: int = 4, **_: Any) -> float:
        return round(
            (steps * FLUX_NEURONS_PER_STEP + FLUX_TILES * FLUX_NEURONS_PER_TILE)
            * USD_PER_NEURON,
            6,
        )

    async def generate(
        self,
        prompt: str,
        steps: int = 4,
        client: Optional[httpx.AsyncClient] = None,
        **_: Any,
    ) -> MediaResult:
        r = await _run(self.model, {"prompt": prompt, "steps": steps}, client)
        try:
            body = r.json()
        except Exception:
            raise MediaError(
                "The media provider returned something unusable.", 502
            ) from None
        data = _b64(((body or {}).get("result") or {}).get("image"), "image")
        if not data:
            logger.error("Cloudflare Workers AI returned no image")
            raise MediaError("The media provider returned no image.", 502)
        return MediaResult("image", "image/jpeg", data, self.id)


class CloudflareMeloTTS:
    kind = "sound"
    id = "cloudflare_melotts"
    model = "@cf/myshell-ai/melotts"

    def configured(self) -> bool:
        return _creds() is not None

    def estimate_cost_usd(self, text: str = "", **_: Any) -> float:
        minutes = max(len(text), 1) / CHARS_PER_MINUTE
        return round(minutes * MELO_NEURONS_PER_MINUTE * USD_PER_NEURON, 6)

    async def generate(
        self,
        text: str,
        lang: str = "en",
        client: Optional[httpx.AsyncClient] = None,
        **_: Any,
    ) -> MediaResult:
        r = await _run(self.model, {"prompt": text, "lang": lang}, client)
        ctype = (r.headers.get("content-type") or "").lower()
        if ctype.startswith("audio/"):
            data = r.content  # the API can answer with the MP3 bytes directly
        else:
            try:
                body = r.json()
            except Exception:
                raise MediaError(
                    "The media provider returned something unusable.", 502
                ) from None
            data = _b64(((body or {}).get("result") or {}).get("audio"), "audio")
        if not data:
            logger.error("Cloudflare Workers AI returned no audio")
            raise MediaError("The media provider returned no audio.", 502)
        return MediaResult("sound", "audio/mpeg", data, self.id)
