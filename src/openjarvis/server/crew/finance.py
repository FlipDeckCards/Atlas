"""Profit and loss for the Station: what each business made and what it cost, today and over the last 30 days.

Revenue comes from Stripe (the one account all products share), net of refunds, US dollars only:
  * a charge belongs to the app named in its metadata.app; otherwise to the app of its customer's subscription (subscriptions carry
    metadata.app: Marnyx and Aibussol tag theirs); otherwise it is FlipDeck's (FlipDeck's own checkouts are untagged).
  * MRR and active subscribers come from active and trialing subscriptions, by the same tag.
Costs, by factory:
  * the crew's model spend on that factory's missions (crew_events);
  * Aibussol: Helix's own chat, voice and media spend (daily_spend, UTC days);
  * optional fixed monthly costs (hosting, tools) from CREW_FIXED_COSTS="flipdeck=7,marnyx=14,aibussol=7", spread evenly over the days.
Today means since midnight in Los Angeles. Stripe fees are not subtracted (gross revenue). Results are cached for 5 minutes.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import time
from typing import Any, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

LA = ZoneInfo("America/Los_Angeles")
STRIPE_API = "https://api.stripe.com/v1"
KNOWN = ("flipdeck", "marnyx", "deadzone", "aibussol")
CACHE_S = 300
MAX_PAGES = 10
# daily_spend also holds two things that are not money spent: the media total kept a second time under "media:__all__" (every image and
# sound is added to the person's own row AND to that one), and the "brief_listen:" counters (1.0 per Listen, a count, not dollars).
REAL_SPEND_ROWS = "user_id <> 'media:__all__' AND user_id NOT LIKE 'brief_listen:%'"


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def factory_key(project: str, title: str = "", goal: str = "", mission_id: str = "", known=KNOWN) -> str:
    """The same rule as the Station: a known business by name, otherwise a factory per new business."""
    p = "".join(ch for ch in str(project or "").lower() if ch.isalnum())
    if p in known:
        return p
    return "new:" + str(title or goal or mission_id).lower()[:40]


def fixed_costs(env=None) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for part in str(_env(env).get("CREW_FIXED_COSTS") or "").split(","):
        k, _, v = part.partition("=")
        try:
            if k.strip():
                out[k.strip().lower()] = max(0.0, float(v))
        except ValueError:
            continue
    return out


def la_midnight(now: dt.datetime) -> dt.datetime:
    local = now.astimezone(LA)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc)


def _monthly(item: Dict[str, Any]) -> float:
    price = item.get("price") or item.get("plan") or {}
    amount = (price.get("unit_amount") if isinstance(price, dict) else None) or (price.get("amount") if isinstance(price, dict) else 0) or 0
    rec = (price.get("recurring") or {}) if isinstance(price, dict) else {}
    interval = rec.get("interval") or price.get("interval") or "month"
    count = rec.get("interval_count") or price.get("interval_count") or 1
    per = {"day": 30.0, "week": 52.0 / 12, "month": 1.0, "year": 1 / 12.0}.get(interval, 1.0) / max(1, int(count))
    return amount / 100.0 * float(item.get("quantity") or 1) * per


class Finance:
    def __init__(self, store, pool=None, env=None, client_factory=None, now=None) -> None:
        self.store = store
        self.pool = pool
        self.env = env
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=20))
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._stripe_cache: Optional[Dict[str, Any]] = None
        self._stripe_at = 0.0

    def _untagged(self) -> str:
        return (_env(self.env).get("CREW_STRIPE_UNTAGGED") or "flipdeck").strip().lower() or "flipdeck"

    async def _pages(self, c, path: str, params: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
        out, params = [], dict(params, limit=100)
        for _ in range(MAX_PAGES):
            r = await c.get(f"{STRIPE_API}/{path}", params=params, headers={"Authorization": f"Bearer {key}"})
            if r.status_code >= 400:
                raise RuntimeError(f"Stripe {path} answered {r.status_code}")
            body = r.json()
            data = body.get("data") or []
            out += data
            if not body.get("has_more") or not data:
                break
            params["starting_after"] = data[-1]["id"]
        return out

    async def stripe_numbers(self) -> Dict[str, Any]:
        """{'today': {app: usd}, 'd30': {app: usd}, 'mrr': {app: usd}, 'subs': {app: n}, 'ok': bool, 'note': str}"""
        if self._stripe_cache is not None and time.monotonic() - self._stripe_at < CACHE_S:
            return self._stripe_cache
        key = (_env(self.env).get("STRIPE_SECRET_KEY") or "").strip()
        res: Dict[str, Any] = {"today": {}, "d30": {}, "mrr": {}, "subs": {}, "ok": False, "note": ""}
        if not key:
            res["note"] = "Stripe is not connected (STRIPE_SECRET_KEY)."
            return res
        now = self.now()
        since30, since_today = int((now - dt.timedelta(days=30)).timestamp()), int(la_midnight(now).timestamp())
        try:
            async with self.client_factory() as c:
                subs = await self._pages(c, "subscriptions", {"status": "all"}, key)
                charges = await self._pages(c, "charges", {"created[gte]": since30}, key)
        except Exception as exc:
            logger.warning("stripe numbers unavailable: %s", exc)
            res["note"] = "Stripe could not be read right now."
            return res
        cust_app: Dict[str, str] = {}
        for s in subs:
            app = str((s.get("metadata") or {}).get("app") or "").lower() or self._untagged()
            cust = s.get("customer") if isinstance(s.get("customer"), str) else (s.get("customer") or {}).get("id")
            if cust and cust not in cust_app:
                cust_app[cust] = app
            if s.get("status") in ("active", "trialing", "past_due"):
                res["subs"][app] = res["subs"].get(app, 0) + 1
                res["mrr"][app] = round(res["mrr"].get(app, 0.0) + sum(_monthly(i) for i in (s.get("items") or {}).get("data") or []), 2)
        skipped = 0
        for ch in charges:
            if ch.get("status") != "succeeded" or not ch.get("paid", True):
                continue
            if str(ch.get("currency") or "usd").lower() != "usd":
                skipped += 1
                continue
            cust = ch.get("customer") if isinstance(ch.get("customer"), str) else (ch.get("customer") or {}).get("id")
            app = str((ch.get("metadata") or {}).get("app") or "").lower() or cust_app.get(cust or "", "") or self._untagged()
            usd = (int(ch.get("amount") or 0) - int(ch.get("amount_refunded") or 0)) / 100.0
            res["d30"][app] = round(res["d30"].get(app, 0.0) + usd, 2)
            if int(ch.get("created") or 0) >= since_today:
                res["today"][app] = round(res["today"].get(app, 0.0) + usd, 2)
        res["ok"] = True
        res["note"] = f"{skipped} non-USD charge(s) not counted." if skipped else ""
        self._stripe_cache, self._stripe_at = res, time.monotonic()
        return res

    async def helix_spend(self) -> Dict[str, float]:
        """Helix's own model, voice and media spend (all users), today (UTC day) and the last 30 days."""
        if self.pool is None:
            return {"today": 0.0, "d30": 0.0}
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT COALESCE(SUM(usd) FILTER (WHERE day = current_date), 0) AS today, "
                                          "COALESCE(SUM(usd) FILTER (WHERE day > current_date - 30), 0) AS d30 FROM daily_spend WHERE " + REAL_SPEND_ROWS)
            return {"today": float(row["today"]), "d30": float(row["d30"])}
        except Exception:
            logger.exception("helix spend unavailable")
            return {"today": 0.0, "d30": 0.0}

    async def report(self, user_id: str, missions: List[Dict[str, Any]], factory_keys: List[str], owner: bool = True,
                     trading: Optional[Dict[str, Dict[str, float]]] = None) -> Dict[str, Any]:
        """P&L per factory and in total. factory_keys: the factories the Station shows (every key gets a row).
        owner=False (a subscriber): only their own Stripe and their crew's spend; no Aibussol row, no hosting, no Helix-wide spend.
        trading: realised LIVE trading profit per factory ({"polymarket": {"today": x, "d30": y}}), counted as that factory's revenue
        (a losing day is negative revenue)."""
        now = self.now()
        known = tuple(k for k in factory_keys if not str(k).startswith("new:"))
        mkey = {m["id"]: factory_key(m.get("project", ""), m.get("title", ""), m.get("goal", ""), m["id"], known) for m in missions}
        crew_today = await self.store.cost_by_mission(user_id, la_midnight(now))
        crew_30 = await self.store.cost_by_mission(user_id, now - dt.timedelta(days=30))
        stripe = await self.stripe_numbers()
        helix = await self.helix_spend() if owner else {"today": 0.0, "d30": 0.0}
        fixed = fixed_costs(self.env) if owner else {}
        keys = list(dict.fromkeys(list(factory_keys) + (["aibussol"] if owner else [])))
        rows: Dict[str, Dict[str, Any]] = {}

        def row(k: str) -> Dict[str, Any]:
            if k not in rows:
                rows[k] = {"revenue_today": 0.0, "revenue_30d": 0.0, "mrr": 0.0, "subscribers": 0,
                           "crew_cost_today": 0.0, "crew_cost_30d": 0.0, "ai_cost_today": 0.0, "ai_cost_30d": 0.0,
                           "fixed_cost_today": round(fixed.get(k, 0.0) / 30.0, 2), "fixed_cost_30d": round(fixed.get(k, 0.0), 2)}
            return rows[k]

        for k in keys:
            row(k)
        for mid, usd in crew_today.items():
            if mid in mkey:
                row(mkey[mid])["crew_cost_today"] += usd
        for mid, usd in crew_30.items():
            if mid in mkey:
                row(mkey[mid])["crew_cost_30d"] += usd
        for app, usd in stripe["today"].items():
            row(app)["revenue_today"] += usd
        for app, usd in stripe["d30"].items():
            row(app)["revenue_30d"] += usd
        for app, usd in stripe["mrr"].items():
            row(app)["mrr"] += usd
        for app, n in stripe["subs"].items():
            row(app)["subscribers"] += n
        for k, v in (trading or {}).items():
            row(k)["revenue_today"] += float(v.get("today") or 0)
            row(k)["revenue_30d"] += float(v.get("d30") or 0)
            row(k)["trading"] = True
        if owner:
            rows["aibussol"]["ai_cost_today"] = helix["today"]
            rows["aibussol"]["ai_cost_30d"] = helix["d30"]
        total = {"revenue_today": 0.0, "revenue_30d": 0.0, "cost_today": 0.0, "cost_30d": 0.0, "mrr": 0.0, "subscribers": 0}
        for r in rows.values():
            r["cost_today"] = round(r["crew_cost_today"] + r["ai_cost_today"] + r["fixed_cost_today"], 2)
            r["cost_30d"] = round(r["crew_cost_30d"] + r["ai_cost_30d"] + r["fixed_cost_30d"], 2)
            r["net_today"] = round(r["revenue_today"] - r["cost_today"], 2)
            r["net_30d"] = round(r["revenue_30d"] - r["cost_30d"], 2)
            for k in list(r):
                if isinstance(r[k], float):
                    r[k] = round(r[k], 2)
            for k in ("revenue_today", "revenue_30d", "cost_today", "cost_30d", "mrr", "subscribers"):
                total[k] += r.get(k, 0)
        total = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in total.items()}
        total["net_today"] = round(total["revenue_today"] - total["cost_today"], 2)
        total["net_30d"] = round(total["revenue_30d"] - total["cost_30d"], 2)
        notes = [n for n in (stripe.get("note"),) if n]
        if owner and not fixed:
            notes.append("Hosting and tool costs are not included yet: set CREW_FIXED_COSTS on Render (monthly dollars per factory).")
        notes.append("Revenue is gross (Stripe fees not subtracted)." + (" Untagged Stripe payments count as FlipDeck's." if owner else
                     " Payments without an app tag count toward your first business."))
        return {"total": total, "factories": rows, "stripe_ok": stripe["ok"], "notes": notes, "as_of": now.isoformat()}
