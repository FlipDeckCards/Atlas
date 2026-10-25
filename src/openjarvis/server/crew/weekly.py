"""The weekly social plan: once a week the crew plans every brand's posts (and an email when a brand has subscribers) and sends each
brand's week to the owner as ONE approval. Approved weeks go to Marnyx, which adds pictures and publishes each post at its time.

When:  CREW_WEEKLY_SOCIAL="sun 17:00" (Los Angeles day and time, default) or "off". Runs once per week, only while Marnyx is connected.
Who:   the owner's crew (subscribers start the same mission themselves by asking Helix).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Optional

from openjarvis.server import brief_content

logger = logging.getLogger(__name__)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

GOAL = (
    "Weekly social plan for the week of {week}. Work through the brands ONE AT A TIME, FlipDeck first, then Marnyx (then Aibussol if it has "
    "accounts). Echo first runs marnyx.list_accounts to see each brand and its connected socials. For each brand with connected socials Echo "
    "writes the coming Monday to Sunday: {per_day} posts a day spread across the day (about 7am to 10pm Los Angeles time, ISO with offset), "
    "on the platforms that brand has connected, varied (tips, behind the scenes, product, community, offers, questions to the audience), on "
    "brand and honest, with hashtags; no claims or prices that are not true and no two posts alike. Marnyx takes at most 28 posts per call, so "
    "Echo puts each brand's week on its Marnyx calendar with TWO marnyx.schedule_week calls (Monday to Wednesday, then Thursday to Sunday). "
    "These are HELD DRAFTS at their posting times: nothing is published. Do NOT ask Michael anything and do NOT approve anything: after the "
    "drafts are on the calendar Helix sends Michael ONE approval for the whole week and approves them in Marnyx herself. If a brand has "
    "email subscribers, Spark writes one short, useful email and sends it with marnyx.send_email (its own approval). Lens checks that every "
    "brand has its full week of drafts. Budget $3."
)


def social_mode(env=None) -> str:
    """'approve' (default): the drafted week waits for Michael's one Approve tap; 'auto_pause': it approves itself after a hold, unless declined."""
    raw = ((os.environ if env is None else env).get("CREW_SOCIAL_MODE") or "approve").strip().lower()
    return "auto_pause" if raw in ("auto_pause", "auto", "automatic") else "approve"


def hold_hours(env=None) -> float:
    try:
        return min(72.0, max(0.25, float((os.environ if env is None else env).get("CREW_SOCIAL_HOLD_HOURS") or 6)))
    except (TypeError, ValueError):
        return 6.0


def per_day(env=None) -> int:
    try:
        return min(8, max(1, int((os.environ if env is None else env).get("CREW_SOCIAL_PER_DAY") or 5)))
    except (TypeError, ValueError):
        return 5


def auto_at(env=None, now=None) -> str:
    """When a held social week schedules itself (an ISO time), or '' in approve mode."""
    if social_mode(env) != "auto_pause":
        return ""
    base = now or dt.datetime.now(dt.timezone.utc)
    return (base + dt.timedelta(hours=hold_hours(env))).isoformat()


def notify_time(env=None):
    """(weekday, hour, minute) in Los Angeles time when Michael is asked to approve the drafted week: CREW_SOCIAL_NOTIFY, default sun 17:00."""
    raw = ((os.environ if env is None else env).get("CREW_SOCIAL_NOTIFY") or "sun 17:00").strip().lower()
    try:
        day, hm = raw.split()
        h, m = (int(x) for x in hm.split(":"))
        return DAYS.index(day[:3]), h, m
    except (ValueError, IndexError):
        return 6, 17, 0


def schedule(env=None):
    raw = ((os.environ if env is None else env).get("CREW_WEEKLY_SOCIAL") or "sun 09:00").strip().lower()
    if raw in ("off", "0", "false", "no"):
        return None
    try:
        day, hm = raw.split()
        h, m = (int(x) for x in hm.split(":"))
        return DAYS.index(day[:3]), h, m
    except (ValueError, IndexError):
        return 6, 9, 0


class WeeklySocial:
    def __init__(self, engine, herald=None, env=None, now=None) -> None:
        self.engine, self.herald, self.env = engine, herald, env
        self._done = ""
        self._open = None                           # None: not read yet; {}: nothing waiting for the notify time
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    OPEN_KEY = "weekly_social_open"

    async def _remember_open(self, uid, mission_id, week, la) -> None:
        """Note which mission is drafting the week and when Michael is to be asked to approve it (the next notify time on or after now)."""
        day, h, m = notify_time(self.env)
        at = la.replace(hour=h, minute=m, second=0, microsecond=0) + dt.timedelta(days=(day - la.weekday()) % 7)
        if at < la:
            at += dt.timedelta(days=7)
        self._open = {"mission": mission_id, "week": week.isoformat(), "notify_at": at.isoformat()}
        await self.engine.store.set_secret(uid, self.OPEN_KEY, json.dumps(self._open))

    async def finalize(self, uid: str, now_la) -> Optional[str]:
        """Once the drafts are on the Marnyx calendar and it is the notify time: ONE approval for the whole week, then Helix approves them in Marnyx.
        Returns the question id when it asks."""
        store = self.engine.store
        if self._open is None:
            try:
                self._open = json.loads(await store.get_secret(uid, self.OPEN_KEY) or "null") or {}
            except ValueError:
                self._open = {}
        info = self._open
        if not info:
            return None
        at = dt.datetime.fromisoformat(info["notify_at"])
        if now_la < at:
            return None
        m = await store.get_mission(info["mission"])
        if m and m["status"] in ("active", "planning") and now_la < at + dt.timedelta(hours=10):
            return None                                                  # still drafting: wait (up to 10 hours past the notify time)
        from openjarvis.server.crew import connections as conns

        envx = await self.engine.env_for(uid)
        self._open = {}
        await store.delete_secret(uid, self.OPEN_KEY)
        try:
            brands = [b for b in await conns.marnyx_awaiting(envx, self.engine.client_factory or conns._default_client) if b["awaiting"] > 0]
        except Exception:
            logger.exception("weekly social: could not read the held drafts")
            await self.engine._notify(uid, "Weekly social plan", "I could not read the drafts from Marnyx. Open the Marnyx calendar to check them.")
            return None
        week = dt.date.fromisoformat(info["week"]).strftime("%B %d")
        if not brands:
            await self.engine._notify(uid, "Weekly social plan", f"No drafts were saved to Marnyx for the week of {week}. Ask Helix to try again.")
            return None
        total = sum(b["awaiting"] for b in brands)
        parts = ", ".join(f'{b["name"]} {b["awaiting"]}' for b in brands)
        hold = ""
        act = {"type": "marnyx_approve", "businesses": [b["name"] for b in brands]}
        if social_mode(envx) == "auto_pause":
            act["auto_at"] = auto_at(envx, self.engine.now())
            hold = f" They approve themselves in {hold_hours(envx):g} hours unless you decline."
        text = (f"Approve {total} social posts for the week of {week}: {parts}. They are drafts on your Marnyx calendar at their posting times; "
                f"on approval Helix goes into Marnyx and approves them, and Marnyx publishes each at its time.{hold}")
        q = await self.engine.store.add_question("", "", uid, "ops", text, ["Approve", "Decline"], act)
        await self.engine._notify(uid, f"Approve {total} posts for next week", text)
        await self.engine.store.add_event(uid, "ops", f"Helix asked for approval of {total} drafted posts ({parts}).")
        return q["id"]

    async def maybe_run(self) -> Optional[str]:
        """Called every crew tick. Starts this week's plan once, after the set time. Returns the mission id when it starts one."""
        sch = schedule(self.env)
        if self.herald is None:
            return None
        la = brief_content.la_time(self.now())
        try:
            uid0 = await self.herald.owner_id()
            if uid0 and (self._open is None or self._open):
                await self.finalize(uid0, la)
        except Exception:
            logger.exception("weekly social: finalize failed")
        if sch is None:
            return None
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
            m_ = await self.engine.start_mission(uid, GOAL.format(week=week.strftime("%B %d"), per_day=per_day(envx)), 3.0)
            await self._remember_open(uid, m_["id"], week, la)
        except Exception:
            logger.exception("weekly social plan could not start")
            return None
        await self.engine._notify(uid, "Weekly social plan started", "The crew is drafting next week's posts for each brand onto your Marnyx calendar. "
                                  "Helix will ask you to approve the whole week once they are ready.")
        return m_["id"]
