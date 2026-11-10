"""v1.38 research helpers: event blackout windows, volatility buckets and blocked-candidate rows.

Nothing in this module changes a trade. The desk only uses these when a setting turns them on, and the
blackout is off until its calendar is filled in and the change is approved."""
from __future__ import annotations

import datetime as dt
import functools
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _unix(text: str) -> float:
    """ISO-8601 UTC text ("2026-12-10T13:30:00Z") to unix seconds."""
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def load_events(path: str) -> List[Tuple[float, str]]:
    """The blackout calendar: a JSON list of {"at": "<UTC time>", "name": "<event>"}. Missing or empty file means no events."""
    try:
        with open(path) as f:
            rows = json.load(f) or []
    except FileNotFoundError:
        return []
    return sorted((_unix(r["at"]), str(r.get("name", "event"))) for r in rows)


def in_blackout(ts: float, events: Iterable[Tuple[float, str]], before_s: float = 900.0, after_s: float = 900.0) -> Optional[str]:
    """The name of the event whose blackout covers ts (unix seconds), or None. A blackout runs from `before_s` before
    each event to `after_s` after it (15 minutes each side by default)."""
    for at, name in events:
        if at - before_s <= ts <= at + after_s:
            return name
    return None


# Volatility cut-offs in the bot's sigma units, from the terciles of BTC 5m fills (Oct 29 to Nov 9, 2026, 725 fills):
# low under 2.67, high over 4.89. High-volatility BTC 5m fills won 57% and made +$2; low and mid made about +$62 and +$66.
VOL_LOW = 2.67
VOL_HIGH = 4.89

# The event blackout is OFF until the owner approves turning it on (it stops entries around the listed events).
BLACKOUT_ON = False


def shipped_events() -> List[Tuple[float, str]]:
    """The blackout calendar that ships with the bot (blackout_calendar.json), read once."""
    return _shipped()


@functools.lru_cache(maxsize=1)
def _shipped() -> List[Tuple[float, str]]:
    import os
    return load_events(os.path.join(os.path.dirname(__file__), "blackout_calendar.json"))


def vol_bucket(sigma: Optional[float], low: float = VOL_LOW, high: float = VOL_HIGH) -> Optional[str]:
    """Tag a window's volatility as low, mid or high, against the cut-offs above. None when the desk has no sigma yet."""
    if sigma is None:
        return None
    if sigma < low:
        return "low"
    if sigma > high:
        return "high"
    return "mid"


def candidate_row(slug: str, ts: float, secs_left: float, side: str, price: float, fair: Optional[float],
                  momentum: Optional[float], sigma: Optional[float], vol_tag: Optional[str],
                  blocked_by: Optional[str]) -> Dict[str, Any]:
    """One row per candidate entry the desk considered, taken or not. blocked_by names the rule that stopped it
    (None means it was allowed)."""
    return {
        "time_utc": dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(),
        "slug": slug,
        "side": side,
        "price": round(price, 4),
        "fair": None if fair is None else round(fair, 4),
        "momentum_z": None if momentum is None else round(momentum, 2),
        "sigma": None if sigma is None else round(sigma, 4),
        "vol_tag": vol_tag,
        "secs_left": round(secs_left, 1),
        "blocked_by": blocked_by or "",
    }
