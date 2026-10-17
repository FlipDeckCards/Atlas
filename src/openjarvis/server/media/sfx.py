"""Sound effects (not speech): request checks, the hard length limit and the provider chosen by configuration (MEDIA_SFX_PROVIDER).

Same pattern as ``sound.py`` (speech). NO provider is selected yet: the only registered provider, ``none``, is never configured, so the route answers
503 "not configured" until a real provider is approved and added to PROVIDERS (see MEDIA-PROVIDERS.md: no free hosted sound-effects API with commercial
terms was found). Tests use a fake provider.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from openjarvis.server.media.base import MediaError, MediaProvider, MediaResult

MAX_PROMPT_CHARS = 300
DEFAULT_SECONDS = 5
HARD_MAX_SECONDS = 15           # the config cannot raise the limit above this
DEFAULT_MAX_SECONDS = 10


class NoSfxProvider:
    """Placeholder until a provider is approved. Never configured, never called."""

    kind = "sound"
    id = "none"

    def configured(self) -> bool:
        return False

    def estimate_cost_usd(self, **_: Any) -> float:
        return 0.0

    async def generate(self, **_: Any) -> MediaResult:
        raise MediaError("Sound effects are not available: no provider is set up.", 503)


PROVIDERS: Dict[str, MediaProvider] = {NoSfxProvider.id: NoSfxProvider()}
DEFAULT_PROVIDER = NoSfxProvider.id


def get_provider(env: Optional[dict] = None) -> MediaProvider:
    name = ((os.environ if env is None else env).get("MEDIA_SFX_PROVIDER") or DEFAULT_PROVIDER).strip()
    return PROVIDERS.get(name) or PROVIDERS[DEFAULT_PROVIDER]


def max_seconds(env: Optional[dict] = None) -> int:
    """MEDIA_SFX_MAX_SECONDS (default 10). A hard limit: requests for more are refused, and the config cannot go above HARD_MAX_SECONDS."""
    raw = ((os.environ if env is None else env).get("MEDIA_SFX_MAX_SECONDS") or "").strip()
    try:
        n = int(raw) if raw else DEFAULT_MAX_SECONDS
    except ValueError:
        n = DEFAULT_MAX_SECONDS
    return max(1, min(HARD_MAX_SECONDS, n))


def clean_prompt(prompt: str) -> str:
    return " ".join(str(prompt or "").split())


def check_seconds(seconds: Optional[int], env: Optional[dict] = None) -> int:
    """The requested length in seconds, or ValueError with a message that names the limit."""
    limit = max_seconds(env)
    try:
        n = int(seconds) if seconds is not None else min(DEFAULT_SECONDS, limit)
    except (TypeError, ValueError):
        raise ValueError("Length must be a whole number of seconds.") from None
    if n < 1 or n > limit:
        raise ValueError(f"Sound effects can be 1 to {limit} seconds long.")
    return n


def limits(env: Optional[dict] = None) -> Dict[str, Any]:
    return {"max_seconds": max_seconds(env), "max_prompt_chars": MAX_PROMPT_CHARS, "default_seconds": min(DEFAULT_SECONDS, max_seconds(env))}
