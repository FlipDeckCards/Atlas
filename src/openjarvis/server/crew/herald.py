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


async def product_numbers(env) -> List[str]:
    out = []
    for name, (var, sql) in PRODUCT_SQL.items():
        dsn = (env.get(var) or "").strip()
        if not dsn:
            out.append(brief_content.unchecked_line(f"{name} user numbers", f"{var} is not set"))
            continue
        try:
            import asyncio

            import asyncpg

            conn = await asyncio.wait_for(asyncpg.connect(dsn, timeout=10), timeout=15)
            try:
                await conn.execute("SET default_transaction_read_only = on")
                row = await asyncio.wait_for(conn.fetchrow(sql), timeout=20)
            finally:
                await conn.close()
            out.append(f"- {name}: " + ", ".join(f"{int(row[k] or 0)} {NUMBER_WORDS.get(k, k)}" for k in row.keys()) + ".")
        except Exception as exc:
            logger.warning("herald: %s numbers failed: %s", name, type(exc).__name__)
            out.append(brief_content.unchecked_line(f"{name} user numbers", "the database could not be read"))
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
    out: List[str] = []
    NAMES = dict(globals()["NAMES"], **(names or {}))
    if fin:
        t = fin["total"]
        out.append(f"- Money today: in {_money(t['revenue_today'])}, out {_money(t['cost_today'])}, net {_money(t['net_today'])}.")
        out.append(f"- Last 30 days: in {_money(t['revenue_30d'])}, out {_money(t['cost_30d'])}, net {_money(t['net_30d'])}. "
                   f"MRR {_money(t['mrr'])} from {t['subscribers']} subscriber(s).")
        rows = sorted(fin["factories"].items(), key=lambda kv: -(kv[1]["revenue_30d"] + kv[1]["cost_30d"]))
        for k, r in rows:
            if r["revenue_30d"] or r["cost_30d"] or r["mrr"]:
                out.append(f"- {NAMES.get(k, k.removeprefix('new:'))}: today in {_money(r['revenue_today'])} / out {_money(r['cost_today'])}; "
                           f"30 days net {_money(r['net_30d'])}; MRR {_money(r['mrr'])}.")
        if not fin.get("stripe_ok"):
            out.append("- Stripe: unchecked (" + "; ".join(n for n in fin.get("notes", []) if "Stripe" in n)[:120] + ").")
    else:
        out.append(brief_content.unchecked_line("Money per business", "could not be read"))
    if products:
        down = [NAMES.get(k, k) for k, p in products.items() if not p.get("live")]
        up = [NAMES.get(k, k) for k, p in products.items() if p.get("live")]
        out.append("- Sites: " + (", ".join(up) + " live" if up else "none live") + ("; DOWN: " + ", ".join(down) if down else "") + ".")
    else:
        out.append(brief_content.unchecked_line("Product sites", "not checked yet"))
    if not owner:
        pass
    elif stats:
        out.append(f"- Aibussol: {stats.get('users_total', 0)} users, {stats.get('active_24h', 0)} active in 24 hours, "
                   f"{stats.get('new_users_7d', 0)} new this week, {stats.get('subscribers_active', 0)} subscriber(s).")
    else:
        out.append(brief_content.unchecked_line("Aibussol users", "could not be read"))
    out.append(f"- Crew: {crew.get('running', 0)} task(s) running, {crew.get('waiting', 0)} waiting on you, "
               f"{crew.get('done_today', 0)} finished today.")
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
        try:
            if await brief_job.already_made(self.store, p, n):
                return None
        except Exception:
            logger.exception("herald: could not check the feed")
            return None
        return await self.run(p)

    async def _crew_counts(self, uid: str) -> Dict[str, int]:
        out = {"running": 0, "waiting": 0, "done_today": 0}
        try:
            start = brief_content.la_time(self.now()).replace(hour=0, minute=0, second=0, microsecond=0)
            for m in await self.engine.store.list_missions(uid, 50):
                for t in await self.engine.store.list_tasks(m["id"]):
                    if t["status"] == "running":
                        out["running"] += 1
                    elif t["status"] == "waiting":
                        out["waiting"] += 1
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
            base = await self.make_brief(period, n, env)
            fin = await self._finance(uid) if uid else None
            glance = glance_lines(fin, dict(getattr(self.engine, "products", {}) or {}), await self._stats(), await self._crew_counts(uid) if uid else {})
            try:
                glance += await self.numbers_fn(env)
            except Exception:
                logger.exception("herald: product numbers failed")
            act = base["body_markdown"].split("## Act on first", 1)[-1].strip().splitlines()[0] if "## Act on first" in base["body_markdown"] else ""
            body = base["body_markdown"]
            head, _, rest = body.partition("\n\n")
            body = head + "\n\n## At a glance\n" + "\n".join(glance) + "\n\n" + rest
            from openjarvis.server.briefs import clean_for_storage

            prev = None
            try:
                prev_row = await self.store.latest_for_product(base["product"]) if hasattr(self.store, "latest_for_product") else None
                prev = prev_row["body"] if prev_row else None
            except Exception:
                prev = None
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
