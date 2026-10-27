"""Where the money went: the last N days of AI spend, split every way the Station has data for (owner only).

  * Crew work (crew_events, every priced step, plan and research run): by role, by kind of step, by mission and by day.
  * Helix (daily_spend): chat and voice, and images and sound, by day. The media total kept a second time under "media:__all__" and the
    "brief_listen:" counters are left out, because neither is money spent (finance.REAL_SPEND_ROWS).
  * The fixed monthly costs listed in CREW_FIXED_COSTS (hosting and tools), if any.
What it cannot see: the Anthropic, Google and ElevenLabs bills themselves. This is the app's own tracking of what it believes it spent.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Mapping, Optional

from openjarvis.server.crew.finance import REAL_SPEND_ROWS, fixed_costs

logger = logging.getLogger(__name__)

TOP_MISSIONS = 12


def _rank(d: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = [dict(v, usd=round(v["usd"], 2)) for v in d.values()]
    return sorted(out, key=lambda r: -r["usd"])


async def helix_rows(pool, since: dt.date) -> Dict[str, Any]:
    """{'chat_voice': usd, 'media': usd, 'by_day': {day: usd}} from daily_spend."""
    out: Dict[str, Any] = {"chat_voice": 0.0, "media": 0.0, "by_day": {}}
    if pool is None:
        return out
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT day, (user_id LIKE 'media:%') AS media, COALESCE(SUM(usd), 0) AS usd FROM daily_spend "
                                    "WHERE day >= $1 AND " + REAL_SPEND_ROWS + " GROUP BY 1, 2", since)
    except Exception:
        logger.exception("helix spend rows unavailable")
        return out
    for r in rows:
        usd = float(r["usd"])
        out["media" if r["media"] else "chat_voice"] += usd
        d = str(r["day"])
        out["by_day"][d] = out["by_day"].get(d, 0.0) + usd
    return out


async def report(store, uid: str, pool=None, env: Optional[Mapping[str, str]] = None, role_names: Optional[Dict[str, str]] = None,
                 now: Optional[dt.datetime] = None, days: int = 30) -> Dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=days)
    names = role_names or {}
    rows = await store.cost_rows(uid, since)

    by_role: Dict[str, Dict[str, Any]] = {}
    by_kind: Dict[str, Dict[str, Any]] = {}
    by_mission: Dict[str, Dict[str, Any]] = {}
    by_day: Dict[str, float] = {}
    for r in rows:
        who = r["role"] or "planning"
        e = by_role.setdefault(who, {"name": names.get(who) or ("Helix planning" if who == "planning" else who), "n": 0, "usd": 0.0})
        e["n"] += r["n"]; e["usd"] += r["usd"]
        k = by_kind.setdefault(r["kind"], {"name": r["kind"], "n": 0, "usd": 0.0})
        k["n"] += r["n"]; k["usd"] += r["usd"]
        m = by_mission.setdefault(r["mission_id"] or "-", {"name": r["title"] or "(no title)", "n": 0, "usd": 0.0})
        m["n"] += r["n"]; m["usd"] += r["usd"]
        by_day[r["day"]] = by_day.get(r["day"], 0.0) + r["usd"]
    crew_total = round(sum(v["usd"] for v in by_role.values()), 2)

    h = await helix_rows(pool, since.date())
    fixed = fixed_costs(env)
    days_out = {}
    for d, v in by_day.items():
        days_out[d] = days_out.get(d, 0.0) + v
    for d, v in h["by_day"].items():
        days_out[d] = days_out.get(d, 0.0) + v
    return {
        "days": days,
        "total_ai": round(crew_total + h["chat_voice"] + h["media"], 2),
        "crew": {"total": crew_total, "by_role": _rank(by_role), "by_kind": _rank(by_kind), "by_mission": _rank(by_mission)[:TOP_MISSIONS]},
        "helix": {"chat_voice": round(h["chat_voice"], 2), "media": round(h["media"], 2)},
        "by_day": [{"day": d, "usd": round(v, 2)} for d, v in sorted(days_out.items(), reverse=True)],
        "fixed_monthly": {k: round(v, 2) for k, v in fixed.items()},
        "note": ("This is what the app tracked itself spending on AI; the real bills are on the Anthropic, Google and ElevenLabs pages. "
                 "Helix chat, voice and media include every account on this server."),
    }
