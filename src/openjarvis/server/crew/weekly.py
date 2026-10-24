"""The weekly social plan: once a week the crew plans every brand's posts (and an email when a brand has subscribers) and sends each
brand's week to the owner as ONE approval. Approved weeks go to Marnyx, which adds pictures and publishes each post at its time.

When:  CREW_WEEKLY_SOCIAL="sun 17:00" (Los Angeles day and time, default) or "off". Runs once per week, only while Marnyx is connected.
Who:   the owner's crew (subscribers start the same mission themselves by asking Helix).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Optional

from openjarvis.server import brief_content

logger = logging.getLogger(__name__)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

GOAL = (
    "Weekly social plan for the week of {week}. For every brand's social calendar: first Echo runs marnyx.list_accounts "
    "to see the brands (FlipDeck, Marnyx, Aibussol when it has accounts) and their connected socials. For EACH brand with connected "
    "socials, Echo plans the coming Monday to Sunday: {per_day} posts a day spread across the day (about 7am to 10pm) on the platforms that "
    "brand has connected, scheduled in Los Angeles time (ISO with offset), varied (tips, behind the scenes, product, community, offers, "
    "questions to the audience), on brand and honest, with hashtags; no claims or prices that are not true and no two posts alike. "
    "Marnyx takes at most 28 posts per call, so Echo sends each brand's week in TWO marnyx.schedule_week calls (Monday to Wednesday, then "
    "Thursday to Sunday). Each call becomes one held approval for Michael: {hold}. If a brand has email subscribers, Spark writes one short, "
    "useful email for that brand's subscribers this week and sends it with marnyx.send_email (its own approval). Lens reviews before "
    "anything is sent. Budget $3."
)


def social_mode(env=None) -> str:
    """'auto_pause' (default): a brand's week schedules itself after a hold, unless declined; 'approve': it waits for the Approve tap."""
    raw = ((os.environ if env is None else env).get("CREW_SOCIAL_MODE") or "auto_pause").strip().lower()
    return "approve" if raw in ("approve", "manual", "off") else "auto_pause"


def hold_hours(env=None) -> float:
    try:
        return min(72.0, max(0.25, float((os.environ if env is None else env).get("CREW_SOCIAL_HOLD_HOURS") or 6)))
    except (TypeError, ValueError):
        return 6.0


def per_day(env=None) -> int:
    try:
        return min(8, max(1, int((os.environ if env is None else env).get("CREW_SOCIAL_PER_DAY") or 6)))
    except (TypeError, ValueError):
        return 6


def auto_at(env=None, now=None) -> str:
    """When a held social week schedules itself (an ISO time), or '' in approve mode."""
    if social_mode(env) != "auto_pause":
        return ""
    base = now or dt.datetime.now(dt.timezone.utc)
    return (base + dt.timedelta(hours=hold_hours(env))).isoformat()


def schedule(env=None):
    raw = ((os.environ if env is None else env).get("CREW_WEEKLY_SOCIAL") or "sun 17:00").strip().lower()
    if raw in ("off", "0", "false", "no"):
        return None
    try:
        day, hm = raw.split()
        h, m = (int(x) for x in hm.split(":"))
        return DAYS.index(day[:3]), h, m
    except (ValueError, IndexError):
        return 6, 17, 0


class WeeklySocial:
    def __init__(self, engine, herald=None, env=None, now=None) -> None:
        self.engine, self.herald, self.env = engine, herald, env
        self._done = ""
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    async def maybe_run(self) -> Optional[str]:
        """Called every crew tick. Starts this week's plan once, after the set time. Returns the mission id when it starts one."""
        sch = schedule(self.env)
        if sch is None or self.herald is None:
            return None
        la = brief_content.la_time(self.now())
        day, h, m = sch
        if (la.weekday(), la.hour, la.minute) < (day, h, m):
            return None
        uid = await self.herald.owner_id()
        if not uid:
            return None
        week = (la + dt.timedelta(days=7 - la.weekday())).date() if la.weekday() >= 4 else (la - dt.timedelta(days=la.weekday())).date()
        mark = f"weekly_social:{week.isoformat()}"
        store = self.engine.store
        if mark == self._done:                      # known started: no database query every tick for the rest of the week
            return None
        if await store.get_secret(uid, mark):
            self._done = mark
            return None
        from openjarvis.server.crew import connections as conns

        envx = await self.engine.env_for(uid)
        if not conns.marnyx_key(envx):
            return None
        await store.set_secret(uid, mark, self.now().isoformat())
        try:
            hold = ("each schedules itself automatically after a " + f"{hold_hours(envx):g}-hour hold unless Michael declines" if social_mode(envx) == "auto_pause"
                    else "Michael approves each one")
            m_ = await self.engine.start_mission(uid, GOAL.format(week=week.strftime("%B %d"), per_day=per_day(envx), hold=hold), 3.0)
        except Exception:
            logger.exception("weekly social plan could not start")
            return None
        await self.engine._notify(uid, "Weekly social plan started", "The crew is planning next week's posts for each brand. "
                                  + ("Each week schedules itself after the hold unless you decline it in Helix's inbox." if social_mode(envx) == "auto_pause"
                                     else "You'll get approvals in Helix's inbox."))
        return m_["id"]
