"""Credits left on the paid AI APIs (Anthropic, Google, ElevenLabs), for the Station's System Vitals box.

  * ElevenLabs: read live from its own account endpoint (characters used and the limit, and when they reset).
  * Anthropic and Google: neither offers a "balance" call to an ordinary API key, so the owner types what the billing page shows.
    The Station keeps that number with the date it was entered, and for Anthropic subtracts what this app has tracked it spent since
    (crew work and Helix chat) and calls the result an estimate. Google shows the entered number and its age.
Owner only. The balances are saved as one small JSON secret per API in the crew store.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

MANUAL = {"anthropic": "Anthropic (Claude)", "google": "Google (Gemini)"}
LABELS = {**MANUAL, "elevenlabs": "ElevenLabs voice"}
DEFAULT_WARN_USD = 10.0
ELEVEN_WARN_FRACTION = 0.15
ELEVEN_URL = "https://api.elevenlabs.io/v1/user/subscription"


def _secret(name: str) -> str:
    return f"credits_{name}"


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat()


def _parse(s: str) -> Optional[dt.datetime]:
    try:
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


async def save_balance(store, uid: str, name: str, balance_usd: float, warn_usd: Optional[float], now: dt.datetime) -> Dict[str, Any]:
    """Remember what the owner read off the billing page (Anthropic or Google)."""
    if name not in MANUAL:
        raise ValueError("Only Anthropic and Google balances are entered by hand.")
    cur = await _saved(store, uid, name)
    warn = float(warn_usd) if warn_usd is not None else cur.get("warn_usd", DEFAULT_WARN_USD)
    rec = {"balance_usd": round(max(0.0, float(balance_usd)), 2), "warn_usd": round(max(0.0, warn), 2), "set_at": _iso(now)}
    await store.set_secret(uid, _secret(name), json.dumps(rec))
    return rec


async def _saved(store, uid: str, name: str) -> Dict[str, Any]:
    raw = await store.get_secret(uid, _secret(name))
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


async def elevenlabs(env, client_factory) -> Dict[str, Any]:
    """{'ok': bool, 'used': int, 'limit': int, 'resets': iso or '', 'tier': str, 'note': str}"""
    key = ((env or {}).get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        return {"ok": False, "note": "No ElevenLabs key on the server."}
    try:
        async with client_factory() as c:
            r = await c.get(ELEVEN_URL, headers={"xi-api-key": key})
        if r.status_code >= 400:
            return {"ok": False, "note": f"ElevenLabs answered {r.status_code}."}
        d = r.json()
        reset = int(d.get("next_character_count_reset_unix") or 0)
        return {"ok": True, "used": int(d.get("character_count") or 0), "limit": int(d.get("character_limit") or 0),
                "resets": _iso(dt.datetime.fromtimestamp(reset, dt.timezone.utc)) if reset else "", "tier": str(d.get("tier") or ""), "note": ""}
    except Exception as exc:
        logger.warning("elevenlabs credits unavailable: %s", exc)
        return {"ok": False, "note": "ElevenLabs could not be reached."}


async def spent_since(store, uid: str, pool, since: dt.datetime) -> float:
    """What this app tracked spending on AI since then: the crew's work plus Helix's chat, voice and media."""
    total = 0.0
    try:
        total += sum((await store.cost_by_mission(uid, since)).values())
    except Exception:
        logger.exception("crew spend unavailable")
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT COALESCE(SUM(usd), 0) AS usd FROM daily_spend WHERE day >= $1", since.date())
            total += float(row["usd"])
        except Exception:
            logger.exception("helix spend unavailable")
    return round(total, 2)


async def report(store, uid: str, env, pool=None, client_factory=None, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=15))
    items = []

    eleven = await elevenlabs(env, client_factory)
    item: Dict[str, Any] = {"key": "elevenlabs", "label": LABELS["elevenlabs"], "kind": "live", "unit": "chars", "left": None, "total": None,
                            "low": False, "as_of": _iso(now), "resets": "", "note": eleven["note"]}
    if eleven["ok"]:
        left = max(0, eleven["limit"] - eleven["used"])
        item.update(left=left, total=eleven["limit"], resets=eleven["resets"],
                    low=bool(eleven["limit"]) and left <= eleven["limit"] * ELEVEN_WARN_FRACTION)
    items.append(item)

    for name in ("anthropic", "google"):
        rec = await _saved(store, uid, name)
        it = {"key": name, "label": LABELS[name], "kind": "manual", "unit": "usd", "left": None, "total": None, "low": False,
              "as_of": rec.get("set_at", ""), "warn_usd": rec.get("warn_usd", DEFAULT_WARN_USD), "estimated": False,
              "note": "" if rec else "Not set. Type the balance from the billing page."}
        if rec:
            bal = float(rec.get("balance_usd") or 0)
            left = bal
            since = _parse(rec.get("set_at", ""))
            if name == "anthropic" and since:
                left = max(0.0, bal - await spent_since(store, uid, pool, since))
                it["estimated"] = True
            it.update(left=round(left, 2), total=round(bal, 2), low=left <= float(it["warn_usd"]))
        items.append(it)
    return {"items": items}
