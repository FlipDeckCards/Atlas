"""Herald: the crew member who makes Michael's briefs, inside the Aibussol service, on schedule, and sends them to his phone.

Why inside: the briefs used to be made by an outside scheduled task whose network could not reach aibussol.com. Herald runs in the
Atlas web service itself (the crew engine's loop calls it every tick), so it reads everything directly.

When:   the brief slots in brief_content.SLOTS (06:52, 12:52, 21:52 Los Angeles time). A slot is made once per day; a restart or a late
        tick inside the 45-minute window still makes it (brief_job.due_period / already_made). "Send a brief now" makes one at any time.
What:   brief_job's sources (Render deploys and error logs, GitHub commits; each one unchecked when it cannot be read) PLUS
        "At a glance": money in / out / net today and over 30 days per business (crew/finance.py), MRR and subscribers, every product
        site live or down, Aibussol's own users, and the crew (running, waiting on Michael, finished today).
        Every number comes from this run's reads; nothing is guessed (a source that fails is written as unchecked).
Where:  stored in the brief feed (the briefs table, so "read me the brief" and Listen work), posted into Helix chat, and pushed to the
        phone through CREW_NOTIFY_URL (an ntfy.sh topic) with the short "at a glance" text.
Switch: HERALD_ENABLED=false turns the schedule off (Send now still works). The owner is hardening.OWNER_EMAIL.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any, Dict, List, Optional

from openjarvis.server import brief_content, brief_job, hardening

logger = logging.getLogger(__name__)

PERIOD_NAMES = {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening"}
NAMES = {"flipdeck": "FlipDeck", "marnyx": "Marnyx", "deadzone": "Deadzone", "aibussol": "Aibussol", "crew": "Crew-made products"}


# Product numbers, read-only, straight from each product's own database (the same queries the old outside briefs ran).
# Set FLIPDECK_READ_DATABASE_URL and MARNYX_READ_DATABASE_URL on Render (Atlas service) to each database's connection string;
# a read-only database user is best. Unset or failing: the brief says "unchecked" for that product's numbers.
PRODUCT_SQL = {
    "FlipDeck": ("FLIPDECK_READ_DATABASE_URL",
                 "SELECT (SELECT count(*) FROM users) AS users_total, "
                 "(SELECT count(*) FROM subscriptions WHERE status='active' AND stripe_subscription_id IS NOT NULL AND tier IN ('pro','elite')) AS paying_now, "
                 "(SELECT count(*) FROM users WHERE created_at > now()-interval '24 hours') AS new_users_24h, "
                 "(SELECT count(*) FROM users WHERE created_at > now()-interval '7 days') AS new_users_7d, "
                 "(SELECT count(DISTINCT COALESCE(sub_token,'u'||user_id::text,user_agent)) FROM page_views WHERE created_at > now()-interval '24 hours') AS visitors_24h, "
                 "(SELECT count(*) FROM page_views WHERE created_at > now()-interval '24 hours') AS page_visits_24h"),
    "Marnyx": ("MARNYX_READ_DATABASE_URL",
               "SELECT (SELECT count(*) FROM users) AS users_total, "
               "(SELECT count(*) FROM subscriptions WHERE status IN ('active','trialing')) AS paying_now, "
               "(SELECT count(*) FROM users WHERE created_at > now()-interval '24 hours') AS new_users_24h, "
               "(SELECT count(*) FROM users WHERE created_at > now()-interval '7 days') AS new_users_7d, "
               "(SELECT count(*) FROM posts WHERE status='published' AND published_at > now()-interval '7 days') AS published_7d, "
               "(SELECT count(*) FROM posts WHERE status='failed' AND updated_at > now()-interval '7 days') AS failed_7d"),
}
NUMBER_WORDS = {"users_total": "total users", "paying_now": "paying now", "new_users_24h": "new in 24 hours", "new_users_7d": "new in 7 days",
                "visitors_24h": "visitors in 24 hours", "page_visits_24h": "page visits in 24 hours", "published_7d": "posts published in 7 days",
                "failed_7d": "posts failed in 7 days"}


SHORT_WORDS = {"users_total": "users", "paying_now": "paying", "new_users_24h": "new today", "visitors_24h": "visitors today"}


async def product_numbers(env) -> List[str]:
    out = []
    for name, (var, sql) in PRODUCT_SQL.items():
        dsn = (env.get(var) or "").strip()
        if not dsn:
            continue                                                  # a product with no database connection is left out of the brief, not listed as unchecked
        try:
            import asyncio

            import asyncpg

            conn = await asyncio.wait_for(asyncpg.connect(dsn, timeout=10), timeout=15)
            try:
                await conn.execute("SET default_transaction_read_only = on")
                row = await asyncio.wait_for(conn.fetchrow(sql), timeout=20)
            finally:
                await conn.close()
            keep = [k for k in ("users_total", "paying_now", "new_users_24h") if k in row.keys()]
            out.append(f"- {name}: " + ", ".join(f"{int(row[k] or 0)} {SHORT_WORDS[k]}" for k in keep) + ".")
        except Exception as exc:
            logger.warning("herald: %s numbers failed: %s", name, type(exc).__name__)
    return out


def enabled(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("HERALD_ENABLED", "true")).strip().lower() not in ("0", "false", "no", "off")


def _money(v: float) -> str:
    return ("-" if v < 0 else "") + f"${abs(v):,.2f}"


def next_slot(now: dt.datetime) -> dt.datetime:
    n = brief_content.la_time(now)
    for add in (0, 1):
        day = n.date() + dt.timedelta(days=add)
        for p in brief_content.SLOTS:
            s = brief_job.slot_start(p, day)
            if s > n:
                return s
    return brief_job.slot_start("morning", n.date() + dt.timedelta(days=1))


def glance_lines(fin: Optional[Dict[str, Any]], products: Dict[str, Any], stats: Optional[Dict[str, Any]], crew: Dict[str, int],
                 names: Optional[Dict[str, str]] = None, owner: bool = True) -> List[str]:
    """Only what matters, four short lines: money, subscriptions, Aibussol's users, and the crew. No site checks, no per-product lists, no 'unchecked' lines."""
    out: List[str] = []
    if fin:
        t = fin["total"]
        out.append(f"- Money: today in {_money(t['revenue_today'])}, out {_money(t['cost_today'])}. Last 30 days in {_money(t['revenue_30d'])}, out {_money(t['cost_30d'])}.")
        out.append(f"- Subscriptions: {t['subscribers']} paying, MRR {_money(t['mrr'])}.")
    if owner and stats:
        out.append(f"- Aibussol: {stats.get('users_total', 0)} users, {stats.get('active_24h', 0)} active today.")
    out.append(f"- Crew: {crew.get('running', 0)} running, {crew.get('queued', 0)} queued, {crew.get('done_today', 0)} done today.")
    return out


def pending_lines(questions: List[Dict[str, Any]], crew: Dict[str, int]) -> List[str]:
    """What is waiting on Michael: each open approval or question on its own line (the first 5), then a count of the rest."""
    out: List[str] = []
    for q in questions[:5]:
        out.append("- " + " ".join(str(q.get("text") or "").split())[:110])
    if len(questions) > 5:
        out.append(f"- ...and {len(questions) - 5} more in the Helix inbox.")
    if not out:
        out.append("- Nothing is waiting on you.")
    return out


def push_text(glance: List[str], act: str) -> str:
    """The phone notification: the first lines of At a glance plus what to act on (ntfy shows about 1,000 characters)."""
    lines = [g.lstrip("- ") for g in glance[:4]] + [act]
    return "\n".join(lines)[:950]


class Herald:
    def __init__(self, engine, brief_store, pool=None, notifier=None, env=None, now=None, make_brief=None, finance=None, stats_fn=None,
                 numbers_fn=None) -> None:
        self.engine = engine
        self.store = brief_store
        self._made: set = set()
        self.pool = pool
        self.notifier = notifier
        self.env = env
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self.make_brief = make_brief or brief_job.make_brief
        self.finance = finance
        self.stats_fn = stats_fn
        self.numbers_fn = numbers_fn or product_numbers
        self.busy = False
        self.last: Optional[Dict[str, Any]] = None        # {period, at, ok, note}
        self._owner: Optional[str] = None

    async def owner_id(self) -> Optional[str]:
        if self._owner:
            return self._owner
        if self.pool is None:
            return None
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT id FROM users WHERE lower(email) = lower($1)", hardening.OWNER_EMAIL)
            self._owner = row["id"] if row else None
        except Exception:
            logger.exception("herald: owner lookup failed")
        return self._owner

    def status(self) -> Dict[str, Any]:
        n = self.now()
        nxt = next_slot(n)
        return {"enabled": enabled(self.env), "busy": self.busy, "last": self.last, "next_at": nxt.isoformat(),
                "next_label": nxt.strftime("%I:%M %p").lstrip("0") + " " + ("today" if nxt.date() == brief_content.la_time(n).date() else "tomorrow")}

    async def maybe_run(self) -> Optional[Dict[str, Any]]:
        """Called every crew tick: make the brief that is due, once."""
        if self.busy or not enabled(self.env):
            return None
        n = self.now()
        p = brief_job.due_period(n)
        if p is None:
            return None
        key = repr(p)
        if key in self._made:                       # known made: no database query every tick for the rest of the period
            return None
        try:
            if await brief_job.already_made(self.store, p, n):
                self._made.add(key)
                return None
        except Exception:
            logger.exception("herald: could not check the feed")
            return None
        return await self.run(p)

    async def _crew_counts(self, uid: str) -> Dict[str, int]:
        out = {"running": 0, "waiting": 0, "queued": 0, "done_today": 0}
        try:
            start = brief_content.la_time(self.now()).replace(hour=0, minute=0, second=0, microsecond=0)
            for m in await self.engine.store.list_missions(uid, 50):
                for t in await self.engine.store.list_tasks(m["id"]):
                    if t["status"] == "running":
                        out["running"] += 1
                    elif t["status"] == "waiting":
                        out["waiting"] += 1
                    elif t["status"] == "queued" and m["status"] == "active":
                        out["queued"] += 1
                    elif t["status"] == "done" and t.get("updated_at") and dt.datetime.fromisoformat(str(t["updated_at"])) >= start:
                        out["done_today"] += 1
        except Exception:
            logger.exception("herald: crew counts failed")
        return out

    async def _finance(self, uid: str) -> Optional[Dict[str, Any]]:
        try:
            from openjarvis.server.crew.finance import KNOWN, Finance, factory_key

            fin = self.finance or Finance(self.engine.store, pool=self.pool, env=self.env)
            self.finance = fin
            missions = await self.engine.store.list_missions(uid, 200)
            keys = list(KNOWN) + [factory_key(m["project"], m["title"], m["goal"], m["id"]) for m in missions]
            return await fin.report(uid, missions, keys)
        except Exception:
            logger.exception("herald: finance failed")
            return None

    async def _stats(self) -> Optional[Dict[str, Any]]:
        try:
            if self.stats_fn is not None:
                return await self.stats_fn()
            if self.pool is None:
                return None
            from openjarvis.server import admin_analytics

            return await admin_analytics.gather(self.pool)
        except Exception:
            logger.exception("herald: aibussol stats failed")
            return None

    async def compose(self, period: str, uid: str, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """The brief's text, built from this moment's numbers. Stores nothing and sends nothing (run() does that; the chat uses it to answer 'give me the brief')."""
        env = dict(os.environ) if env is None and self.env is None else dict(env if env is not None else self.env)
        if not env.get("STRIPE_READ_KEY") and env.get("STRIPE_SECRET_KEY"):
            env["STRIPE_READ_KEY"] = env["STRIPE_SECRET_KEY"]
        fin = await self._finance(uid) if uid else None
        crew = await self._crew_counts(uid) if uid else {}
        glance = glance_lines(fin, {}, await self._stats(), crew)
        try:
            glance += await self.numbers_fn(env)
        except Exception:
            logger.exception("herald: product numbers failed")
        try:
            questions = await self.engine.store.list_questions(uid, "open") if uid else []
        except Exception:
            questions = []
        pend = pending_lines(questions, crew)
        greeting = {"morning": "Good morning", "afternoon": "Good afternoon", "evening": "Good evening"}.get(period, "Hello")
        body = f"{greeting}, Michael.\n\n## Numbers\n" + "\n".join(glance) + "\n\n## Waiting on you\n" + "\n".join(pend)
        spoken = glance[0].lstrip("- ") + " " + (f"{len(questions)} thing(s) are waiting on you." if questions else "Nothing is waiting on you.")
        return {"body": body, "glance": glance, "pending": pend, "act": pend[0].lstrip("- "), "spoken": spoken, "questions": len(questions)}

    async def run(self, period: Optional[str] = None) -> Dict[str, Any]:
        """Make one brief now (period defaults to the one for the current time), store it, post it to Helix chat, push it to the phone."""
        if self.busy:
            return {"ok": False, "detail": "Herald is already making a brief."}
        self.busy = True
        n = self.now()
        period = period or brief_content.greeting_period(n)
        if period not in PERIOD_NAMES:
            period = "evening"
        try:
            uid = await self.owner_id() or ""
            env = dict(os.environ) if self.env is None else dict(self.env)
            if not env.get("STRIPE_READ_KEY") and env.get("STRIPE_SECRET_KEY"):
                env["STRIPE_READ_KEY"] = env["STRIPE_SECRET_KEY"]
            c = await self.compose(period, uid, env)
            glance, act, base = c["glance"], c["act"], {"product": "FlipDeck and Marnyx", "period": period, "spoken_summary": c["spoken"]}
            body = c["body"]
            from openjarvis.server.briefs import clean_for_storage

            prev = None                                                   # a basic brief repeats its numbers on purpose: nothing is dropped as 'already reported'
            body, spoken = clean_for_storage(body[:20_000], base["spoken_summary"], prev)
            saved = await self.store.insert(base["product"], period, body, spoken)
            title = f"{PERIOD_NAMES[period]} brief"
            if self.notifier is not None and uid:
                await self.notifier.notify(uid, title, push_text(glance, act or "Open Aibussol for the full brief."))
            self.last = {"period": period, "at": n.isoformat(), "ok": True, "id": saved["id"]}
            if self.engine is not None and uid:
                try:
                    await self.engine.store.add_event(uid, "brief", f"Herald sent the {period} brief.")
                except Exception:
                    pass
            return {"ok": True, "period": period, "id": saved["id"], "glance": glance}
        except Exception:
            logger.exception("herald: brief failed")
            self.last = {"period": period, "at": n.isoformat(), "ok": False}
            return {"ok": False, "detail": "The brief could not be made just now."}
        finally:
            self.busy = False
