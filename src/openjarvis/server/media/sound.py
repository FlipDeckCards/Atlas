"""Sound generation (speech): request checks and the provider chosen by configuration (MEDIA_SOUND_PROVIDER).

Only speech is implemented. Music and sound effects have no provider that passed the license and free-quota checks (see MEDIA-PROVIDERS.md).
"""

from __future__ import annotations

import os
from typing import Dict, Optional

from openjarvis.server.media.base import MediaProvider
from openjarvis.server.media.cloudflare import CloudflareMeloTTS

MAX_TEXT_CHARS = 1_000
LANGS = {"en", "fr", "es", "zh", "jp", "kr"}  # MeloTTS languages

PROVIDERS: Dict[str, MediaProvider] = {CloudflareMeloTTS.id: CloudflareMeloTTS()}
DEFAULT_PROVIDER = CloudflareMeloTTS.id


def get_provider(env: Optional[dict] = None) -> MediaProvider:
    name = (
        (os.environ if env is None else env).get("MEDIA_SOUND_PROVIDER")
        or DEFAULT_PROVIDER
    ).strip()
    return PROVIDERS.get(name) or PROVIDERS[DEFAULT_PROVIDER]


def clean_text(text: str) -> str:
    return " ".join(str(text or "").split())


def clean_lang(lang: Optional[str]) -> str:
    v = str(lang or "en").strip().lower()
    return v if v in LANGS else "en"
