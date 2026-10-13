"""Image generation: request checks and the provider chosen by configuration (MEDIA_IMAGE_PROVIDER)."""

from __future__ import annotations

import os
from typing import Dict, Optional

from openjarvis.server.media.base import MediaProvider
from openjarvis.server.media.cloudflare import CloudflareFluxSchnell

MAX_PROMPT_CHARS = (
    500  # Cloudflare allows 2048; shorter keeps prompts (and abuse) small
)
MIN_STEPS, MAX_STEPS, DEFAULT_STEPS = 1, 8, 4

PROVIDERS: Dict[str, MediaProvider] = {
    CloudflareFluxSchnell.id: CloudflareFluxSchnell()
}
DEFAULT_PROVIDER = CloudflareFluxSchnell.id


def get_provider(env: Optional[dict] = None) -> MediaProvider:
    name = (
        (os.environ if env is None else env).get("MEDIA_IMAGE_PROVIDER")
        or DEFAULT_PROVIDER
    ).strip()
    return PROVIDERS.get(name) or PROVIDERS[DEFAULT_PROVIDER]


def clean_prompt(prompt: str) -> str:
    return " ".join(str(prompt or "").split())


def clamp_steps(steps: Optional[int]) -> int:
    try:
        n = int(steps) if steps is not None else DEFAULT_STEPS
    except (TypeError, ValueError):
        n = DEFAULT_STEPS
    return max(MIN_STEPS, min(MAX_STEPS, n))
