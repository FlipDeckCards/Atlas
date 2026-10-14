"""Common pieces of media generation: the result type, the provider interface and the errors routes turn into answers.

A provider is a small class with a ``kind`` ("image" or "sound"), an ``id`` and ``generate``. Which provider serves a kind is chosen by
configuration (see ``image.py`` and ``sound.py``), so providers can be swapped without touching the routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class MediaError(Exception):
    """A failure with a message that is safe to show the person. Details stay in the server log."""

    def __init__(self, public_message: str, status_code: int = 502) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.status_code = status_code


class MediaNotConfigured(MediaError):
    def __init__(self, what: str = "Media provider") -> None:
        super().__init__(f"{what} is not configured", 503)


@dataclass
class MediaResult:
    kind: str  # "image" | "sound"
    mime: str  # "image/jpeg" | "audio/mpeg"
    data: bytes
    provider: str


class MediaProvider(Protocol):
    kind: str
    id: str

    def configured(self) -> bool:
        """True when the provider's settings (environment variables) are present. No network."""

    def estimate_cost_usd(self, **request: Any) -> float:
        """What this request would cost on a paid plan (0 on a free tier is NOT assumed). Used for the spend cap before the call."""

    async def generate(self, **request: Any) -> MediaResult:
        """Make the media. Raises MediaError on any provider failure."""
