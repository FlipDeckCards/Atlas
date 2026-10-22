"""Aibussol crew: Helix plans a goal into tasks and a crew of agents works them, in parallel, inside hard limits.

Off unless CREW_ENABLED is true. Only owner accounts (ANALYTICS_ADMIN_EMAILS, default the owner) can use it, because agents
spend money and act on the owner's connected platforms.

Limits, all enforced by the server, never by a model:
  * every model call is priced and charged to the task, the mission and the crew's daily total before the next one is allowed;
  * a task stops and asks (through Helix) when its own budget or its mission's budget is used up; the day stops at CREW_DAILY_CAP_USD;
  * anything that publishes, charges or cannot be undone is a checkpoint: the server records it and runs it only after the owner
    taps Approve. A model can never approve its own checkpoint;
  * agents act on outside platforms only through official APIs listed in connections.py.

Settings (environment):
  CREW_ENABLED                 true/1/yes turns the crew on (default off)
  CREW_DAILY_CAP_USD           crew model spend per owner per UTC day (default 3.00; 0 stops all crew calls)
  CREW_MISSION_BUDGET_USD      default budget for a new mission (default 1.00)
  CREW_MAX_MISSION_BUDGET_USD  the most a single mission may be given (default 25.00)
  CREW_MAX_CONCURRENT          tasks running at the same time (default 3)
  CREW_MAX_TASKS               most tasks the planner may create for one mission (default 8)
  CREW_TICK_SECONDS            how often the engine looks for work (default 10)
  CREW_IDLE_TICK_SECONDS       how long it sleeps when there is no work at all (default 600; new work wakes it at once)
  CREW_QUIET_HOURS             Los Angeles hours when no new work starts unless Night Shift is on (default "22-6")
  CREW_NOTIFY_URL              optional push URL (an ntfy.sh topic URL works): each notice is POSTed as plain text
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

_TRUE = {"1", "true", "yes", "on"}


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _float(env: Optional[Mapping[str, str]], name: str, default: float, lo: float = 0.0) -> float:
    raw = (_env(env).get(name) or "").strip()
    try:
        return max(lo, float(raw)) if raw else default
    except ValueError:
        return default


def _int(env: Optional[Mapping[str, str]], name: str, default: int, lo: int = 1) -> int:
    raw = (_env(env).get(name) or "").strip()
    try:
        return max(lo, int(raw)) if raw else default
    except ValueError:
        return default


def crew_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    return (_env(env).get("CREW_ENABLED") or "").strip().lower() in _TRUE


def crew_allowed(email: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """Owner accounts only: the same list as the owner analytics page."""
    from openjarvis.server import admin_analytics

    return admin_analytics.is_admin(email or "", env)


def daily_cap_usd(env=None) -> float:
    return _float(env, "CREW_DAILY_CAP_USD", 3.00)


def default_mission_budget(env=None) -> float:
    return min(_float(env, "CREW_MISSION_BUDGET_USD", 1.00, lo=0.05), max_mission_budget(env))


def max_mission_budget(env=None) -> float:
    return _float(env, "CREW_MAX_MISSION_BUDGET_USD", 25.00, lo=0.05)


def max_concurrent(env=None) -> int:
    return min(_int(env, "CREW_MAX_CONCURRENT", 3), 8)


def max_tasks(env=None) -> int:
    return min(_int(env, "CREW_MAX_TASKS", 8), 12)


def tick_seconds(env=None) -> float:
    return _float(env, "CREW_TICK_SECONDS", 10.0, lo=2.0)


def idle_tick_seconds(env=None) -> float:
    """How long the loop sleeps when there is no work at all (longer than Neon's 5-minute suspend delay, so the database can sleep)."""
    return _float(env, "CREW_IDLE_TICK_SECONDS", 600.0, lo=5.0)


def quiet_hours(env=None) -> Optional[tuple]:
    """(start_hour, end_hour) in Los Angeles time, or None when CREW_QUIET_HOURS is 'off'."""
    raw = (_env(env).get("CREW_QUIET_HOURS") or "22-6").strip().lower()
    if raw in ("", "off", "none", "0"):
        return None
    try:
        a, b = (int(x) for x in raw.split("-", 1))
        if 0 <= a <= 23 and 0 <= b <= 23 and a != b:
            return (a, b)
    except ValueError:
        pass
    return (22, 6)


def notify_url(env=None) -> str:
    return (_env(env).get("CREW_NOTIFY_URL") or "").strip()


_SUB_CACHE: dict = {}


async def crew_access(request) -> bool:
    """The crew is for owner/admin accounts and for accounts with an active Aibussol subscription (checked at most once a minute)."""
    import time

    email = getattr(request.state, "user_email", "") or ""
    uid = getattr(request.state, "user_id", "") or ""
    if not uid:
        return False
    if crew_allowed(email):
        return True
    hit = _SUB_CACHE.get(uid)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    ok = False
    pool = getattr(getattr(request.app.state, "session_store", None), "_pool", None)
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT status FROM subscriptions WHERE user_id = $1", uid)
            ok = bool(row) and row["status"] in ("active", "trialing")
        except Exception:
            ok = False
    _SUB_CACHE[uid] = (time.monotonic(), ok)
    return ok
