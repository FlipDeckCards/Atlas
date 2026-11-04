"""A subscriber's own Polymarket Desk: copies of the owner's two bots (BTC 5m and BTC 15m) on THEIR Polymarket keys.

Nothing is shared with the owner's desks: the keys are the subscriber's (stored encrypted in crew_secrets), and every row the desk
writes carries their account tag (kv keys 'status@<tag>', ledger labels 'live@<tag>' / 'paper@<tag>', log lines 'btc@<tag>'), so
scoreboards, controls, daily stops, cross-desk caps and advisory locks are all separate. The owner can only LOOK at a subscriber's
desk (the account switcher on his Station); he cannot trade it.

A new copy starts in PAPER with the owner's current limits; the account holder taps LIVE on their own Station to trade real money.

Seeding (one-time, from Render): POLYBOT_SEED_ACCOUNTS is a JSON list of {"email", "key_id", "secret_key"}. On startup each
entry's keys are sealed into that account's secrets, the Polymarket Desk becomes its only business, and its desks start. Once the
desk shows on their Station, clear the variable: the keys live encrypted in the database from then on (seeding an account that
already has keys does nothing, so a restart never resets it).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("crew.tenant_desks")

KEY_ID, SECRET = "polymarket_key_id", "polymarket_secret"


def account_tag(uid: str) -> str:
    """The short, stable tag a subscriber's desk rows carry (letters and digits only)."""
    return "u" + re.sub(r"[^a-z0-9]", "", str(uid or "").lower())[:12]


async def keys_for(store, uid: str, env=None):
    from openjarvis.server.crew import tenant

    return (await tenant.get(store, uid, KEY_ID, env)) or "", (await tenant.get(store, uid, SECRET, env)) or ""


async def has_desk(store, uid: str) -> bool:
    k, s = await keys_for(store, uid)
    return bool(k and s)


async def seed_control(pool, account: str) -> None:
    """A new copy takes the owner's current limits (the same bots), in paper, unpaused. An existing control row is left alone."""
    from openjarvis.polybot.state import kv_key
    from openjarvis.server.crew import desk

    for a in desk.TENANT_ASSETS:
        if await desk.get_kv(pool, kv_key("control", a, account)):
            continue
        owner = await desk.get_kv(pool, kv_key("control", a))
        ctl = {"mode": "paper", "paused": False, "kill": False}
        if isinstance(owner.get("limits"), dict):
            ctl["limits"] = dict(owner["limits"])
        await desk.put_control(pool, ctl, a, account)


async def uids_with_desks(pool) -> List[str]:
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT DISTINCT user_id FROM crew_secrets WHERE name = $1", KEY_ID)
    return [str(r["user_id"]) for r in rows]


class TenantDesks:
    """Starts, restarts and stops each subscriber's runners inside the app (the same Runner the owner's desks use)."""

    def __init__(self, store, pool, env=None) -> None:
        self.store, self.pool, self.env = store, pool, env
        self.runners: Dict[str, list] = {}                  # uid -> [Runner, Runner]

    async def start(self, uid: str) -> bool:
        from openjarvis.polybot.config import for_account, from_env
        from openjarvis.polybot.runtime import Runner
        from openjarvis.server.crew import desk, tenant

        if uid in self.runners:
            return True
        key_id, secret = await keys_for(self.store, uid, self.env)
        if not (key_id and secret):
            return False
        tag = account_tag(uid)
        await seed_control(self.pool, tag)
        notify = (await tenant.get(self.store, uid, "notify_url", self.env)) or ""       # THEIR phone topic, never the owner's
        rs = [Runner(for_account(from_env(asset=a), tag, key_id, secret, notify), self.pool) for a in desk.TENANT_ASSETS]
        for r in rs:
            r.start()
        self.runners[uid] = rs
        logger.info("Polymarket Desk started for account %s", tag)
        return True

    async def restart(self, uid: str) -> bool:
        await self.stop(uid)
        return await self.start(uid)

    async def stop(self, uid: str) -> None:
        import asyncio

        rs = self.runners.pop(uid, [])
        if rs:
            await asyncio.gather(*(r.stop() for r in rs), return_exceptions=True)

    async def stop_all(self) -> None:
        for uid in list(self.runners):
            await self.stop(uid)

    async def start_all(self) -> None:
        for uid in await uids_with_desks(self.pool):
            try:
                await self.start(uid)
            except Exception:
                logger.exception("a subscriber's desk did not start")


async def connect(store, uid: str, key_id: str, secret_key: str, env=None) -> None:
    """Save a subscriber's Polymarket US keys (encrypted) and put the Polymarket Desk on their Station."""
    from openjarvis.server.crew import desk, tenant

    key_id, secret_key = str(key_id or "").strip(), str(secret_key or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{20,64}", key_id) or len(secret_key) < 20:
        raise ValueError("That does not look like a Polymarket US key ID and secret key.")
    await tenant.put(store, uid, KEY_ID, key_id, env)
    await tenant.put(store, uid, SECRET, secret_key, env)
    items = [b for b in await tenant.businesses(store, uid) if b["key"] != "polymarket"]
    await store.set_secret(uid, "businesses", json.dumps([dict(desk.TENANT_BUSINESS)] + items))


async def seed_from_env(store, pool, env=None) -> List[str]:
    """POLYBOT_SEED_ACCOUNTS (see the module notes). Returns the emails seeded. The Station is reset to just the Polymarket Desk."""
    from openjarvis.server.crew import desk

    e = os.environ if env is None else env
    raw = (e.get("POLYBOT_SEED_ACCOUNTS") or "").strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        logger.warning("POLYBOT_SEED_ACCOUNTS is not valid JSON; nothing seeded")
        return []
    done = []
    for it in items if isinstance(items, list) else []:
        email = str((it or {}).get("email") or "").strip().lower()
        if not email:
            continue
        async with pool.acquire() as c:
            uid = await c.fetchval("SELECT id FROM users WHERE lower(email) = $1", email)
        if not uid:
            logger.warning("POLYBOT_SEED_ACCOUNTS: no Aibussol account for %s yet", email)
            continue
        uid = str(uid)
        if await has_desk(store, uid):                    # already seeded: never reset their Station or keys on a later restart
            continue
        try:
            await connect(store, uid, it.get("key_id"), it.get("secret_key"), env)
        except ValueError as exc:
            logger.warning("POLYBOT_SEED_ACCOUNTS: %s: %s", email, exc)
            continue
        await store.set_secret(uid, "businesses", json.dumps([dict(desk.TENANT_BUSINESS)]))   # a clean Station: just the desk
        done.append(email)
        logger.info("POLYBOT_SEED_ACCOUNTS: seeded %s (account %s); clear the variable now", email, account_tag(uid))
    return done


async def overview(pool, store, uid: str) -> Dict[str, Any]:
    """What the owner sees when he switches to a subscriber's account: their desks' live and paper results and account value."""
    from openjarvis.server.crew import desk

    tag = account_tag(uid)
    desks = {a: await desk.summary(pool, trade_limit=50, asset=a, account=tag) for a in desk.TENANT_ASSETS}
    pnl = await desk.live_pnl_all(pool, tag)
    total = {k: round(sum(v[k] + (v.get("pre_" + k, 0.0) if k in ("today", "all") else 0.0) for v in pnl.values()), 2)
             for k in ("today", "d30", "all")}
    st = (desks.get("btc") or {}).get("status") or {}
    return {"account": tag, "desks": desks, "income": {"trading": total, "account_value": st.get("account_value"),
                                                       "buying_power": st.get("balance")}}
