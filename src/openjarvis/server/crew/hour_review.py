"""The daily review of the 7 AM hour (v1.25): is half size still right, should it go back to full size, or stop trading 7 AM?

Every morning after the 7 AM hour has settled (8:15 AM Pacific), for the owner's desks and every subscriber's desk:
  - count the LIVE windows opened in the 7 AM hour since half size began (and all time), with their win rate and return on staked;
  - decide: fewer than MIN_TRADES since the change -> keep half size (not enough data yet);
             return on staked >= +UP_PCT  -> recommend going back to full size;
             return on staked <= -DOWN_PCT -> recommend never trading 7 AM (size 0);
             otherwise                     -> keep half size.
  - post the result to that account's Station ledger and phone, and put an Approve card in that account holder's Helix inbox when a
    change is recommended (each account approves changes to its own money; nothing changes without a tap).
The owner's daily note covers his brother's (every subscriber's) desks too.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("crew.hour_review")

HOUR = 7
MIN_TRADES = 20
UP_PCT = 5.0
DOWN_PCT = 5.0
RUN_AT = (8, 15)
HALF_SINCE = "2026-11-04T08:00:00+00:00"   # half size at 7 AM began with Nov 4 (midnight Pacific)                     # Los Angeles time
KV = "hour_review"


def decide(n: int, ret_pct: Optional[float], current: float) -> Dict[str, Any]:
    """What the numbers say. current is today's factor for the hour (0.5 half, 1 full, 0 skip)."""
    if current <= 0:
        return {"factor": 0.0, "change": False, "why": "7 AM is switched off; no new trades to judge."}
    if n < MIN_TRADES or ret_pct is None:
        return {"factor": current, "change": False, "why": f"not enough data yet ({n} of {MIN_TRADES} trades since the change)"}
    if ret_pct >= UP_PCT:
        return {"factor": 1.0, "change": current != 1.0, "why": f"7 AM is making money ({ret_pct:+.1f}% on staked over {n} trades): back to full size"}
    if ret_pct <= -DOWN_PCT:
        return {"factor": 0.0, "change": True, "why": f"7 AM keeps losing ({ret_pct:+.1f}% on staked over {n} trades): stop trading 7 AM"}
    return {"factor": current, "change": False, "why": f"7 AM is about break-even ({ret_pct:+.1f}% over {n} trades): keep it as is"}


def stats(rows) -> Dict[str, Any]:
    n = len(rows)
    wins = sum(1 for r in rows if float(r["pnl"] or 0) > 0)
    staked = sum(float(r["up_cost"] or 0) + float(r["down_cost"] or 0) for r in rows)
    pnl = sum(float(r["pnl"] or 0) for r in rows)
    return {"trades": n, "wins": wins, "win_rate": round(100.0 * wins / n, 1) if n else None, "staked": round(staked, 2),
            "profit": round(pnl, 2), "return_pct": round(100.0 * pnl / staked, 1) if staked else None}


async def _hour_rows(pool, mode: str, asset: str, since: Optional[dt.datetime]):
    from openjarvis.server.crew.desk import ASSET_SQL, _ensure

    await _ensure(pool)
    q = (f"SELECT window_start, up_cost, down_cost, pnl FROM polybot_windows WHERE mode = $1 AND settled_at IS NOT NULL "
         f"AND {ASSET_SQL} = $2 AND extract(hour FROM window_start AT TIME ZONE 'America/Los_Angeles') = $3"
         + (" AND window_start >= $4" if since else ""))
    async with pool.acquire() as c:
        return await c.fetch(q, mode, asset, HOUR, *([since] if since else []))


async def review_desk(pool, asset: str, account: str, now: dt.datetime) -> Dict[str, Any]:
    """One desk's review; remembers when half size began (the first review) so 'since the change' is measured from there."""
    from openjarvis.polybot.config import label_for, parse_hour_size
    from openjarvis.polybot.state import kv_key
    from openjarvis.server.crew import desk

    mem = await desk.get_kv(pool, kv_key(KV, asset, account))
    since = dt.datetime.fromisoformat(mem.get("since") or HALF_SINCE)      # reset whenever an approved change takes effect
    live = label_for(desk.LIVE_OF.get(asset, "live"), account)
    ctl = await desk.get_kv(pool, kv_key("control", asset, account))
    from openjarvis.polybot.config import default_hour_size
    hs = dict(default_hour_size(asset))
    if isinstance(ctl.get("hour_size"), dict):
        hs.update({int(h): min(1.0, max(0.0, float(f))) for h, f in ctl["hour_size"].items() if str(h).strip().isdigit()})
    current = hs.get(HOUR, 1.0)
    recent, all_time = stats(await _hour_rows(pool, live, asset, since)), stats(await _hour_rows(pool, live, asset, None))
    d = decide(recent["trades"], recent["return_pct"], current)
    out = {"asset": asset, "name": desk.NAMES[asset], "account": account, "current": current, "since_change": recent,
           "all_time": all_time, **d}
    mem.update(last_day=now.astimezone(_la()).strftime("%Y-%m-%d"), last=out)
    async with pool.acquire() as c:
        await c.execute("INSERT INTO polybot_kv (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                        kv_key(KV, asset, account), __import__("json").dumps(mem, default=str))
    return out


def _la():
    from openjarvis.polybot.risk import LA
    return LA


def line(r: Dict[str, Any]) -> str:
    s, a = r["since_change"], r["all_time"]
    size = "skipped" if r["current"] <= 0 else "full size" if r["current"] >= 1 else f"{r['current']:.0%} size"
    since = (f"{s['trades']} trades since the change, {s['win_rate']}% won, {s['return_pct']:+.1f}% on staked ({s['profit']:+.2f})"
             if s["trades"] else "no 7 AM trades since the change yet")
    return f"{r['name']} (7 AM now {size}): {since}; all time {a['trades']} trades, {a['profit']:+.2f}. Verdict: {r['why']}."


async def run_once(engine, pool, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    """Review every desk (owner's and each subscriber's), post notes, and put Approve cards where a change is recommended."""
    from openjarvis.polybot.runtime import ntfy_pusher
    from openjarvis.server.crew import desk, notify_url, tenant, tenant_desks

    now = now or dt.datetime.now(dt.timezone.utc)
    owner = await engine.herald.owner_id() if getattr(engine, "herald", None) else None
    accounts = [("", str(owner) if owner else "", "Your desks")]
    for uid in await tenant_desks.uids_with_desks(pool):
        name = (await engine.name_fn(uid)) if getattr(engine, "name_fn", None) else uid
        accounts.append((tenant_desks.account_tag(uid), uid, f"{name}'s desks"))
    results, owner_lines = [], []
    for account, uid, title in accounts:
        assets = desk.TENANT_ASSETS if account else ("btc", "btc15")
        lines = []
        for a in assets:
            try:
                r = await review_desk(pool, a, account, now)
            except Exception:
                logger.exception("hour review failed for %s %s", account or "owner", a)
                continue
            results.append(r)
            lines.append(line(r))
            if r["change"] and uid:
                text = (f"Daily 7 AM review, {r['name']}: {r['why']}. Approve to set 7 AM windows to "
                        + ("full size." if r["factor"] >= 1 else "never trade (skip 7 AM)." if r["factor"] <= 0 else f"{r['factor']:.0%} size."))
                await engine.store.add_question("", "", uid, "ops", text, ["Approve", "Decline"],
                                                {"type": "desk_control", "asset": a, "set": {"hour_size": {str(HOUR): r["factor"]}}})
        if not lines:
            continue
        note = "Daily 7 AM review. " + " | ".join(lines)
        owner_lines.append(f"{title}: " + " | ".join(lines))
        if uid:
            await engine.store.add_event(uid, "ops", note)
        if account and uid:                                   # the subscriber's own phone, never the owner's topic
            push = ntfy_pusher((await tenant.get(engine.store, uid, "notify_url")) or "")
            if push:
                try:
                    await push("7 AM review", note)
                except Exception:
                    pass
    push = ntfy_pusher(notify_url())                          # the owner's daily note covers every account
    if push and owner_lines:
        try:
            await push("7 AM review", "\n".join(owner_lines))
        except Exception:
            pass
    return results


async def loop(app) -> None:
    """Once a day at 8:15 AM Pacific (after the 7 AM hour has settled)."""
    done = ""
    while True:
        try:
            la = dt.datetime.now(_la())
            day = la.strftime("%Y-%m-%d")
            engine = getattr(app.state, "crew_engine", None)
            pool = getattr(engine, "pool", None) if engine else None
            if day != done and (la.hour, la.minute) >= RUN_AT and engine is not None and pool is not None:
                from openjarvis.polybot.state import kv_key
                from openjarvis.server.crew import desk

                last = await desk.get_kv(pool, kv_key(KV + "_run", "btc"))
                if last.get("day") != day:                    # once a day even across restarts
                    async with pool.acquire() as c:
                        await c.execute("INSERT INTO polybot_kv (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
                                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                                        kv_key(KV + "_run", "btc"), '{"day": "%s"}' % day)
                    await run_once(engine, pool)
                done = day
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("the daily 7 AM review failed; trying again in 10 minutes")
        await asyncio.sleep(600)
