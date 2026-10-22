"""Pay as you go for subscribers, the way Claude's plans work: the monthly price stays the same and includes a set amount of crew
work; past that, extra work is billed by usage on the same Stripe subscription, up to a monthly cap.

  CREW_PLAN_INCLUDED_USD      crew model cost included in the monthly plan per subscriber (default 5.00). Keep it well under the plan
                              price so every subscription makes a profit before any extra usage.
  CREW_OVERAGE_MULTIPLIER     what a subscriber pays per $1 of crew cost past the included amount (default 2.0: a 50% margin on extras)
  CREW_MONTHLY_HARD_CAP_USD   the most extra usage billed to one subscriber in a month (default 50.00); the crew stops there
  STRIPE_PRICE_USAGE          a metered Stripe price ($0.01 per unit) tied to a Billing Meter; without it there is no pay as you go,
                              and a subscriber's crew stops when the included usage is used up
  CREW_USAGE_METER_EVENT      the meter's event name (default "aibussol_crew_usage"); each event's value is cents

The owner is never metered. Usage is reported to Stripe's official Billing Meter Events API as it accrues (cents, cumulative per month,
each event with an idempotent identifier), using the customer on the subscriber's Aibussol subscription.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import os
from typing import Any, Dict, Mapping, Optional

import httpx

logger = logging.getLogger(__name__)
STRIPE_API = "https://api.stripe.com/v1"


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _f(env, name: str, default: float) -> float:
    try:
        raw = (_env(env).get(name) or "").strip()
        return max(0.0, float(raw)) if raw else default
    except ValueError:
        return default


def plan(env=None) -> Dict[str, Any]:
    e = _env(env)
    return {"included_usd": _f(env, "CREW_PLAN_INCLUDED_USD", 5.0), "multiplier": max(1.0, _f(env, "CREW_OVERAGE_MULTIPLIER", 2.0)),
            "cap_usd": _f(env, "CREW_MONTHLY_HARD_CAP_USD", 50.0),
            "payg": bool((e.get("STRIPE_PRICE_USAGE") or "").strip() and (e.get("STRIPE_SECRET_KEY") or "").strip()),
            "event": (e.get("CREW_USAGE_METER_EVENT") or "aibussol_crew_usage").strip()}


def month_start(now: dt.datetime) -> dt.date:
    return now.date().replace(day=1)


def summary(spent_usd: float, env=None) -> Dict[str, Any]:
    """What a subscriber sees: how much of the included work is used, and what extra usage costs them so far."""
    p = plan(env)
    over_cost = max(0.0, spent_usd - p["included_usd"])
    billed = min(over_cost * p["multiplier"], p["cap_usd"]) if p["payg"] else 0.0
    left = p["included_usd"] - spent_usd
    pct = 100.0 if p["included_usd"] <= 0 else min(100.0, 100.0 * spent_usd / p["included_usd"])
    return {"included_pct": round(pct, 1), "included_left_usd": round(max(0.0, left), 2), "extra_billed_usd": round(billed, 2),
            "extra_cap_usd": p["cap_usd"], "payg": p["payg"], "rate": p["multiplier"]}


def room_usd(spent_usd: float, env=None) -> float:
    """Crew cost still allowed this month for a subscriber."""
    p = plan(env)
    limit = p["included_usd"] + (p["cap_usd"] / p["multiplier"] if p["payg"] else 0.0)
    return limit - spent_usd


def billable_cents(spent_usd: float, env=None) -> int:
    p = plan(env)
    if not p["payg"]:
        return 0
    over = max(0.0, spent_usd - p["included_usd"]) * p["multiplier"]
    return int(math.floor(min(over, p["cap_usd"]) * 100 + 1e-9))


async def report(store, pool, user_id: str, spent_usd: float, now: dt.datetime, env=None, client_factory=None) -> int:
    """Send Stripe the cents accrued since the last report this month. Returns the cents sent (0 when nothing new)."""
    p = plan(env)
    total = billable_cents(spent_usd, env)
    month = now.strftime("%Y-%m")
    key = f"usage_reported:{month}"
    done = int(await store.get_secret(user_id, key) or 0)
    delta = total - done
    if delta <= 0 or pool is None:
        return 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT stripe_customer_id FROM subscriptions WHERE user_id = $1", user_id)
    cust = row["stripe_customer_id"] if row else None
    if not cust:
        return 0
    cf = client_factory or (lambda: httpx.AsyncClient(timeout=15))
    form = {"event_name": p["event"], "payload[stripe_customer_id]": cust, "payload[value]": str(delta),
            "identifier": f"{user_id}-{month}-{total}", "timestamp": str(int(now.timestamp()))}
    async with cf() as c:
        r = await c.post(f"{STRIPE_API}/billing/meter_events", data=form, auth=((_env(env).get("STRIPE_SECRET_KEY") or "").strip(), ""))
    duplicate = r.status_code == 400 and "identifier" in r.text      # already recorded by an earlier attempt
    if r.status_code >= 400 and not duplicate:
        logger.error("crew usage report failed: %s %s", r.status_code, r.text[:200])
        return 0
    await store.set_secret(user_id, key, str(total))
    return delta
