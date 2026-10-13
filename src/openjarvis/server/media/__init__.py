"""Media generation for the AiBusSol web app (images and speech). Video is not offered: no provider passed the license and free-quota checks.

Everything is OFF unless MEDIA_ENABLED is on AND DAILY_MEDIA_SPEND_CAP_USD is above 0. Chat keeps its own cap (DAILY_SPEND_CAP_USD).
"""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional

DEFAULT_GLOBAL_CAP_USD = 1.00


def media_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """MEDIA_ENABLED: off unless 1 / true / yes / on."""
    return bool(
        re.fullmatch(
            r"(?i)1|true|yes|on",
            ((os.environ if env is None else env).get("MEDIA_ENABLED") or "").strip(),
        )
    )


def _usd(raw: str, default: float) -> float:
    raw = (raw or "").strip()
    if raw == "":
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def media_cap_usd(env: Optional[Mapping[str, str]] = None) -> float:
    """DAILY_MEDIA_SPEND_CAP_USD, per user per UTC day. Default 0, which means media is off for everyone."""
    return _usd(
        (os.environ if env is None else env).get("DAILY_MEDIA_SPEND_CAP_USD", ""), 0.0
    )


def media_global_cap_usd(env: Optional[Mapping[str, str]] = None) -> float:
    """MEDIA_GLOBAL_DAILY_CAP_USD: a ceiling for ALL users together per UTC day (default $1.00)."""
    return _usd(
        (os.environ if env is None else env).get("MEDIA_GLOBAL_DAILY_CAP_USD", ""),
        DEFAULT_GLOBAL_CAP_USD,
    )


async def over_cap(
    store, key: str, estimate: float, cap: float, day: str
) -> Optional[dict]:
    """None when one more call fits under the cap; otherwise the error body. The provider must not be called then."""
    spent = float(await store.get_spend_today(key, day))
    if cap <= 0 or spent >= cap or spent + estimate > cap:
        return {
            "error": "daily_media_cap_reached",
            "detail": "Daily media limit reached. Image and sound generation resume at midnight UTC.",
            "cap_usd": cap,
            "spent_usd": round(spent, 4),
        }
    return None
