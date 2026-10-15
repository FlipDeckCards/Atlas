"""Shared test doubles for the media library and Helix tool tests (no network, no database)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from openjarvis.server import hardening
from openjarvis.server.media import edits
from openjarvis.server.media.base import MediaResult
from openjarvis.server.media.catalog import MemoryCatalog
from openjarvis.server.media.service import Ctx
from openjarvis.server.media.storage import MemoryStorage

API_KEY = "test-system-key-0123456789"
JWT_SECRET = "a-long-random-test-secret-0123456789abcdef"


class FakeStore:
    """The parts of the session store the routes use: chat history and the spend ledger."""

    def __init__(self) -> None:
        self.spend: Dict[Any, float] = {}
        self.history: Dict[str, list] = {}

    async def get_or_create(self, sender_id, channel_type):
        return {"conversation_history": self.history.setdefault(sender_id, [])}

    async def append_message(self, sender_id, channel_type, role, content):
        self.history.setdefault(sender_id, []).append({"role": role, "content": content})

    async def get_spend_today(self, user_id, day):
        return self.spend.get((user_id, day), 0.0)

    async def add_spend(self, user_id, day, usd):
        self.spend[(user_id, day)] = self.spend.get((user_id, day), 0.0) + usd

    def spent(self, key):
        return self.spend.get((key, hardening.utc_day()), 0.0)


def tiny_jpeg(color=(200, 30, 30), size=(64, 48)) -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


def tiny_mp3(seconds: float = 3.0) -> bytes:
    """A real MP3 (a sine tone) made with the bundled ffmpeg, so the audio edits run for real."""
    p = subprocess.run(
        [edits.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:a", "libmp3lame", "-q:a", "5", "-f", "mp3", "pipe:1"],
        capture_output=True, check=True,
    )
    return p.stdout


class FakeProvider:
    def __init__(self, kind: str, data: Optional[bytes] = None, cost: float = 0.01, configured: bool = True) -> None:
        self.kind, self.id = kind, "fake_" + kind
        self._data = data if data is not None else (tiny_jpeg() if kind == "image" else tiny_mp3(1.0))
        self._cost, self._configured = cost, configured
        self.calls: List[dict] = []

    def configured(self) -> bool:
        return self._configured

    def estimate_cost_usd(self, **_: Any) -> float:
        return self._cost

    async def generate(self, **request: Any) -> MediaResult:
        self.calls.append(request)
        return MediaResult(self.kind, "image/jpeg" if self.kind == "image" else "audio/mpeg", self._data, self.id)


def make_ctx(user_id: str = "alice", store: Optional[FakeStore] = None, catalog=None, storage=None) -> Ctx:
    return Ctx(user_id, "10.0.0.1", store or FakeStore(), catalog or MemoryCatalog(), storage or MemoryStorage())


def tool_call(name: str, args: dict, call_id: str = "call_1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=__import__("json").dumps(args)))


def completion(content: Optional[str] = None, tool_calls: Optional[list] = None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
