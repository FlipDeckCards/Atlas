"""What each factory has for sale or live, for the Station's factory panel.

* etsy         - the connected shop's listings (active and drafts), through Etsy's official API
* stripe       - active Stripe products with their price (crew-made ones marked)
* deadzone     - the games on the Deadzone Games site, read from its home page cards
* google_play / app_store - the apps marked live by the owner (no store API is connected yet); kept as a small list
                 in the crew store, and editable from the panel

Every function returns {"items": [{"name", "status", "price", "url"}...], "note": "..."} and never raises for a platform
problem: the panel shows the note instead.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List

from openjarvis.server.crew import connections as conns

MANUAL = ("google_play", "app_store")
MANUAL_LABEL = {"google_play": "Google Play", "app_store": "the App Store"}
MAX_MANUAL = 50


def _item(name, status="", price="", url="") -> Dict[str, str]:
    return {"name": str(name or "")[:140], "status": str(status or "")[:40], "price": str(price or "")[:30], "url": str(url or "")[:300]}


async def etsy(store, uid: str, env, cf) -> Dict[str, Any]:
    items: List[Dict[str, str]] = []
    async with cf() as c:
        shop_id = await conns.etsy_shop_id(store, uid, env, c)
        token = await conns._etsy_access(store, uid, env, c)
        h = {"x-api-key": conns._etsy_key_header(env), "Authorization": f"Bearer {token}"}
        for state in ("active", "draft"):
            body = conns._ok(await c.get(f"{conns.ETSY_API}/shops/{shop_id}/listings", headers=h,
                                         params={"state": state, "limit": 100}), "Etsy")
            for li in body.get("results") or []:
                p = li.get("price") or {}
                price = f'{p.get("amount", 0) / (p.get("divisor") or 100):.2f} {p.get("currency_code", "")}'.strip() if p else ""
                items.append(_item(li.get("title"), "for sale" if state == "active" else "draft", price,
                                   li.get("url") or f'https://www.etsy.com/listing/{li.get("listing_id")}'))
    live = sum(1 for i in items if i["status"] == "for sale")
    return {"items": items, "note": f"{live} for sale, {len(items) - live} draft(s)."}


async def stripe(env, cf) -> Dict[str, Any]:
    key = (conns._env(env).get("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        return {"items": [], "note": "Stripe is not connected."}
    async with cf() as c:
        body = conns._ok(await c.get(f"{conns.STRIPE_API}/products", headers={"Authorization": f"Bearer {key}"},
                                     params={"limit": 100, "active": "true", "expand[]": "data.default_price"}), "Stripe")
    items = []
    for p in body.get("data") or []:
        dp = p.get("default_price") if isinstance(p.get("default_price"), dict) else {}
        amt, rec = dp.get("unit_amount"), (dp.get("recurring") or {}).get("interval")
        price = (f"${amt / 100:.2f}" + (f"/{rec}" if rec else "")) if amt else ""
        app = (p.get("metadata") or {}).get("app") or ""
        items.append(_item(p.get("name"), "crew-made" if app == "crew" else app or "product", price, p.get("url") or ""))
    return {"items": items, "note": f"{len(items)} active product(s) on Stripe."}


_CARD = re.compile(r'<div class="card ([a-z ]+)"(?:\s+onclick="window\.location\.href=\'([^\']*)\'")?[^>]*>(.*?)</div>\s*</div>', re.S)


def parse_games(page: str, base: str) -> List[Dict[str, str]]:
    out = []
    for cls, href, inner in _CARD.findall(page or ""):
        t = re.search(r'class="card-title">([^<]+)<', inner)
        if not t:
            continue
        st = re.search(r'class="card-status">([^<]+)<', inner)
        status = html.unescape(st.group(1)).replace("▶", "").strip() if st else ("live" if "live" in cls else "")
        out.append(_item(html.unescape(t.group(1)).strip(), "playable" if "live" in cls.split() else status.lower() or "coming soon",
                         "", base.rstrip("/") + href if href else ""))
    return out


async def site_games(url: str, cf) -> Dict[str, Any]:
    async with cf() as c:
        r = await c.get(url, headers={"User-Agent": "Aibussol-Station/1.0"}, follow_redirects=True)
    if r.status_code >= 400:
        return {"items": [], "note": f"The site answered {r.status_code}."}
    games = parse_games(r.text, url)
    live = sum(1 for g in games if g["status"] == "playable")
    return {"items": games, "note": f"{live} playable on the site, {len(games) - live} coming soon." if games else "No game cards found on the site."}


async def manual(store, uid: str, key: str) -> Dict[str, Any]:
    try:
        items = json.loads(await store.get_secret(uid, f"catalog_{key}") or "[]")
    except ValueError:
        items = []
    return {"items": items, "editable": True,
            "note": (f"{len(items)} app(s) live on {MANUAL_LABEL[key]}." if items else f"No apps live on {MANUAL_LABEL[key]} yet.")
            + " The store isn't connected, so this list is kept by hand (add an app when it goes live)."}


async def manual_edit(store, uid: str, key: str, add: Dict[str, Any] = None, remove: str = "") -> Dict[str, Any]:
    cur = (await manual(store, uid, key))["items"]
    if remove:
        cur = [i for i in cur if i.get("name") != remove]
    if add and str(add.get("name") or "").strip():
        cur = [i for i in cur if i.get("name") != add["name"].strip()]
        url = str(add.get("url") or "").strip()
        cur.append(_item(add["name"].strip(), str(add.get("status") or "live"), "", url if url.startswith("https://") else ""))
    await store.set_secret(uid, f"catalog_{key}", json.dumps(cur[:MAX_MANUAL]))
    return await manual(store, uid, key)


async def for_factory(key: str, *, store, uid: str, env, cf, businesses: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        if key == "etsy":
            if not (await conns.status(store, uid, env))["etsy"]["connected"]:
                return {"items": [], "note": "Etsy is not connected."}
            return await etsy(store, uid, env, cf)
        if key == "stripe":
            return await stripe(env, cf)
        if key in MANUAL:
            return await manual(store, uid, key)
        if key == "deadzone":
            b = next((x for x in businesses if x.get("key") == key), None)
            if b and b.get("url"):
                return await site_games(b["url"], cf)
        return {"items": [], "note": ""}
    except conns.ConnectionError_ as exc:
        return {"items": [], "note": str(exc)}
    except Exception:
        return {"items": [], "note": "The list could not be loaded just now."}
