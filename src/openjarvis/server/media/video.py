"""Video generation: request checks, the HARD length and resolution limits, and the provider chosen by configuration (MEDIA_VIDEO_PROVIDER).

Video is asynchronous. A provider has three calls: ``start`` (returns the provider's own job id at once), ``status`` and ``fetch`` (the finished file).
service.py turns that into a job the browser polls (see jobs.py).

NO provider is selected yet: the only registered provider, ``none``, is never configured, so the route answers 503 "not configured" until a real
provider is approved and added to PROVIDERS. MEDIA-PROVIDERS.md records why none qualified on free-tier and commercial-use grounds; every video
provider found is paid. Tests use a fake provider. Nothing here calls a paid API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from openjarvis.server.media.base import MediaError, MediaResult

MAX_PROMPT_CHARS = 500
HARD_MAX_SECONDS = 10           # the configuration can never raise the limits above these
HARD_MAX_HEIGHT = 1080
DEFAULT_MAX_SECONDS = 5
DEFAULT_MAX_HEIGHT = 720
ALLOWED_HEIGHTS = (360, 480, 720, 1080)
MAX_ACTIVE_JOBS_PER_USER = 2


@dataclass
class JobState:
    state: str                    # "running" | "succeeded" | "failed"
    error: Optional[str] = None   # a message that is safe to show the person


class VideoProvider(Protocol):
    kind: str
    id: str

    def configured(self) -> bool: ...

    def estimate_cost_usd(self, **request: Any) -> float:
        """Estimated price of one video of this length and size (seconds=, height=). Used for the spend cap BEFORE the provider is called."""

    async def start(self, *, prompt: str, seconds: int, height: int) -> str:
        """Ask the provider to make the video. Returns the provider's job id. Raises MediaError."""

    async def status(self, provider_job_id: str) -> JobState:
        """Ask how the job is going. Raises MediaError only when the provider cannot be reached."""

    async def fetch(self, provider_job_id: str) -> MediaResult:
        """Download the finished video (MediaResult with mime video/mp4)."""


class NoVideoProvider:
    """Placeholder until a provider is approved. Never configured, never called."""

    kind = "video"
    id = "none"

    def configured(self) -> bool:
        return False

    def estimate_cost_usd(self, **_: Any) -> float:
        return 0.0

    async def start(self, **_: Any) -> str:
        raise MediaError("Video is not available: no provider is set up.", 503)

    async def status(self, provider_job_id: str) -> JobState:
        raise MediaError("Video is not available: no provider is set up.", 503)

    async def fetch(self, provider_job_id: str) -> MediaResult:
        raise MediaError("Video is not available: no provider is set up.", 503)


PROVIDERS: Dict[str, VideoProvider] = {NoVideoProvider.id: NoVideoProvider()}
DEFAULT_PROVIDER = NoVideoProvider.id


def get_provider(env: Optional[dict] = None) -> VideoProvider:
    name = ((os.environ if env is None else env).get("MEDIA_VIDEO_PROVIDER") or DEFAULT_PROVIDER).strip()
    return PROVIDERS.get(name) or PROVIDERS[DEFAULT_PROVIDER]


def _int_env(env: Optional[dict], name: str, default: int, low: int, high: int) -> int:
    raw = ((os.environ if env is None else env).get(name) or "").strip()
    try:
        n = int(raw) if raw else default
    except ValueError:
        n = default
    return max(low, min(high, n))


def max_seconds(env: Optional[dict] = None) -> int:
    """VIDEO_MAX_SECONDS (default 5, never above 10). A hard limit: longer requests are refused."""
    return _int_env(env, "VIDEO_MAX_SECONDS", DEFAULT_MAX_SECONDS, 1, HARD_MAX_SECONDS)


def max_height(env: Optional[dict] = None) -> int:
    """VIDEO_MAX_HEIGHT in pixels (default 720, never above 1080). A hard limit: taller requests are refused."""
    return _int_env(env, "VIDEO_MAX_HEIGHT", DEFAULT_MAX_HEIGHT, 240, HARD_MAX_HEIGHT)


def limits(env: Optional[dict] = None) -> Dict[str, Any]:
    """What the UI shows next to the video controls."""
    h = max_height(env)
    return {"max_seconds": max_seconds(env), "max_height": h, "heights": [x for x in ALLOWED_HEIGHTS if x <= h], "max_prompt_chars": MAX_PROMPT_CHARS,
            "max_active_jobs": MAX_ACTIVE_JOBS_PER_USER}


def clean_prompt(prompt: str) -> str:
    return " ".join(str(prompt or "").split())


def check_request(seconds: Optional[int], height: Optional[int], env: Optional[dict] = None) -> tuple:
    """(seconds, height) within the limits, or ValueError naming the limit. Nothing is clamped silently: a request over the limit is refused."""
    lim_s, lim_h = max_seconds(env), max_height(env)
    try:
        s = int(seconds) if seconds is not None else lim_s
        h = int(height) if height is not None else lim_h
    except (TypeError, ValueError):
        raise ValueError("Length and height must be whole numbers.") from None
    if s < 1 or s > lim_s:
        raise ValueError(f"Video can be 1 to {lim_s} seconds long.")
    if h < 240 or h > lim_h:
        raise ValueError(f"Video can be at most {lim_h} pixels tall.")
    return s, h
