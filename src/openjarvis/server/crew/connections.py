"""Outside platforms the crew can act on, through their OFFICIAL APIs only.

Each action has a risk level, and the server (not a model) decides what happens:
  read        runs straight away (nothing changes on the platform)
  draft       runs straight away; creates something private that only the owner can see and that costs nothing (an Etsy draft listing)
  checkpoint  never runs on an agent's say-so: it becomes a checkpoint the owner approves or declines in Aibussol

No browser logins, no scraping, no bot-detection workarounds: a platform with no official API for an action is simply not offered.
The owner does the one-time parts (accounts, identity, payouts, the "allow" screen of an OAuth connection); after that the crew can work.

  Stripe       STRIPE_SECRET_KEY (already used for Aibussol billing). Products are tagged metadata app=crew so the Aibussol
               subscription webhook (which only reads app=aibussol) ignores them.
  Etsy         ETSY_API_KEY (keystring), optional ETSY_SHARED_SECRET, ETSY_REDIRECT_URI; then "Connect Etsy" in the Station runs
               Etsy's OAuth (PKCE). Tokens are kept in crew_secrets and refreshed automatically.
  Google Play  not built yet (planned: Play Developer API with a service account)
  App Store    not built yet (planned: App Store Connect API key)
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"
ETSY_API = "https://openapi.etsy.com/v3/application"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
ETSY_CONNECT_URL = "https://www.etsy.com/oauth/connect"
ETSY_SCOPES = "listings_r listings_w shops_r"

ACTIONS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "stripe": {
        "list_products": {"risk": "read", "about": "List recent Stripe products and their prices.", "args": {}},
        "get_balance": {"risk": "read", "about": "Read the Stripe balance (available and pending, per currency).", "args": {}},
        "recent_payments": {"risk": "read", "about": "List the 20 most recent payments: amount, status, refunds, date and description (no card or contact details).",
                            "args": {}},
        "create_product": {"risk": "checkpoint", "about": "Create a live Stripe product with one price.",
                           "args": {"name": "text", "description": "text", "price_usd": "number", "interval": "one of: once, month, year"}},
        "create_payment_link": {"risk": "checkpoint", "about": "Create a public Stripe payment link for an existing price.",
                                "args": {"price_id": "text (price_...)"}},
    },
    "etsy": {
        "get_shop": {"risk": "read", "about": "Read the connected Etsy shop's name and listing counts.", "args": {}},
        "create_draft_listing": {"risk": "draft", "about": "Create a DRAFT Etsy listing (not visible to buyers, no listing fee until the owner activates it).",
                                 "args": {"title": "text", "description": "text", "price_usd": "number", "quantity": "integer",
                                          "taxonomy_id": "integer (Etsy category id)", "who_made": "i_did | someone_else | collective",
                                          "when_made": "made_to_order | 2020_2026 | ...", "type": "download (default) | physical",
                                          "shipping_profile_id": "integer, physical only"}},
    },
    "google_play": {},
    "app_store": {},
}

LABELS = {"stripe": "Stripe", "etsy": "Etsy", "google_play": "Google Play", "app_store": "App Store"}

SETUP = {
    "stripe": "Set STRIPE_SECRET_KEY on Render (the Aibussol billing key works).",
    "etsy": "Register an app at etsy.com/developers (wait for approval), set ETSY_API_KEY (and ETSY_SHARED_SECRET if Etsy shows one) and "
            "ETSY_REDIRECT_URI=https://aibussol.com/crew/connect/etsy/callback on Render, then tap Connect Etsy in the Station.",
    "google_play": "Not built yet. Needs: a Play developer account, a Google Cloud service account invited to Play Console, and its JSON key.",
    "app_store": "Not built yet. Needs: an Apple Developer account and an App Store Connect API key (issuer id, key id, .p8 file).",
}


class ConnectionError_(Exception):
    """A refused or failed platform action, with a message that is safe to show."""


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _clip(v: Any, n: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:n]


# ── status ───────────────────────────────────────────────────────────────────────────────────────

def etsy_app_configured(env=None) -> bool:
    e = _env(env)
    return bool((e.get("ETSY_API_KEY") or "").strip() and (e.get("ETSY_REDIRECT_URI") or "").strip())


async def status(store, user_id: str, env=None) -> Dict[str, Dict[str, Any]]:
    e = _env(env)
    stripe_on = bool((e.get("STRIPE_SECRET_KEY") or "").strip())
    etsy_linked = bool(await store.get_secret(user_id, "etsy_refresh"))
    out = {
        "stripe": {"connected": stripe_on, "built": True},
        "etsy": {"connected": etsy_app_configured(env) and etsy_linked, "built": True, "can_connect": etsy_app_configured(env) and not etsy_linked},
        "google_play": {"connected": False, "built": False},
        "app_store": {"connected": False, "built": False},
    }
    for k, v in out.items():
        v.update(label=LABELS[k], setup="" if v["connected"] else SETUP[k],
                 actions=[{"name": n, "risk": a["risk"], "about": a["about"]} for n, a in ACTIONS[k].items()])
    from openjarvis.server.crew import github, research

    out["github"] = {"connected": github.configured(env), "built": True, "label": "GitHub (Forge's code)",
                     "setup": "" if github.configured(env) else github.SETUP, "actions": []}
    out["workbench"] = {"connected": False, "built": True, "label": "Your PC (Workbench for Rig)", "setup": "", "actions": [], "pc": True}
    out["web_research"] = {"connected": research.configured(env), "built": True, "label": "Web research (GPT Researcher)",
                           "setup": "" if research.configured(env) else research.SETUP, "actions": []}
    return out


def describe_for_agent(conn_status: Dict[str, Dict[str, Any]]) -> str:
    """The text an operator agent sees: what it can use and the arguments each action takes."""
    lines = []
    for k, v in conn_status.items():
        if k not in ACTIONS:
            continue
        if not v["connected"]:
            lines.append(f"- {v['label']}: NOT connected ({'not built yet' if not v['built'] else 'owner must connect it'}). Do not plan on using it.")
            continue
        for n, a in ACTIONS[k].items():
            lines.append(f"- {k}.{n} [{a['risk']}]: {a['about']} args={json.dumps(a['args'])}")
    return "\n".join(lines)


def risk_of(platform: str, action: str) -> Optional[str]:
    a = ACTIONS.get(platform, {}).get(action)
    return a["risk"] if a else None


def describe_action(platform: str, action: str, args: Dict[str, Any]) -> str:
    """One plain sentence, written by the server, of what a checkpoint will do if approved."""
    if platform == "stripe" and action == "create_product":
        interval = args.get("interval") if args.get("interval") in ("month", "year") else "once"
        price = _price(args.get("price_usd"))
        when = "" if interval == "once" else f" per {interval}"
        return f'Create a live Stripe product "{_clip(args.get("name"), 80)}" priced ${price:.2f}{when}.'
    if platform == "stripe" and action == "create_payment_link":
        return f"Create a public Stripe payment link for price {_clip(args.get('price_id'), 60)} (anyone with the link can pay)."
    return f"{LABELS.get(platform, platform)}: {action} with {json.dumps(args)[:200]}"


def _price(v: Any) -> float:
    try:
        p = float(v)
    except (TypeError, ValueError):
        raise ConnectionError_("price_usd must be a number.")
    if not (0.5 <= p <= 10000):
        raise ConnectionError_("price_usd must be between 0.50 and 10000.")
    return round(p, 2)


# ── running an action ────────────────────────────────────────────────────────────────────────────

ClientFactory = Callable[[], httpx.AsyncClient]


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30)


async def run(platform: str, action: str, args: Dict[str, Any], *, store, user_id: str, mission_id: str = "",
              env=None, client_factory: Optional[ClientFactory] = None) -> str:
    """Run one action and return a short factual result. Raises ConnectionError_ with a safe message on any refusal or failure."""
    if risk_of(platform, action) is None:
        raise ConnectionError_(f"{platform}.{action} is not an available action.")
    if not isinstance(args, dict):
        raise ConnectionError_("The arguments must be an object.")
    st = await status(store, user_id, env)
    if not st[platform]["connected"]:
        raise ConnectionError_(f"{LABELS[platform]} is not connected. {SETUP[platform]}")
    cf = client_factory or _default_client
    if platform == "stripe":
        return await _stripe(action, args, env=env, mission_id=mission_id, cf=cf)
    if platform == "etsy":
        return await _etsy(action, args, store=store, user_id=user_id, env=env, cf=cf)
    raise ConnectionError_(f"{LABELS.get(platform, platform)} is not built yet.")


async def _stripe(action: str, args: Dict[str, Any], *, env, mission_id: str, cf: ClientFactory) -> str:
    key = (_env(env).get("STRIPE_SECRET_KEY") or "").strip()
    auth = {"Authorization": f"Bearer {key}"}
    async with cf() as c:
        if action == "list_products":
            r = await c.get(f"{STRIPE_API}/products", headers=auth, params={"limit": 10, "active": "true", "expand[]": "data.default_price"})
            body = _ok(r, "Stripe")
            rows = []
            for p in body.get("data", []):
                dp = p.get("default_price") or {}
                amt = dp.get("unit_amount") if isinstance(dp, dict) else None
                rows.append(f'{p.get("name")} ({p.get("id")}{", $%.2f" % (amt / 100) if amt else ""})')
            return "Stripe products: " + ("; ".join(rows) if rows else "none")
        if action == "get_balance":
            body = _ok(await c.get(f"{STRIPE_API}/balance", headers=auth), "Stripe")
            fmt = lambda xs: ", ".join(f'{x.get("amount", 0) / 100:.2f} {str(x.get("currency", "")).upper()}' for x in xs or []) or "0"  # noqa: E731
            return f"Stripe balance: available {fmt(body.get('available'))}; pending {fmt(body.get('pending'))}."
        if action == "recent_payments":
            body = _ok(await c.get(f"{STRIPE_API}/charges", headers=auth, params={"limit": 20}), "Stripe")
            rows = []
            for ch in body.get("data", []):
                when = dt.datetime.fromtimestamp(int(ch.get("created") or 0), dt.timezone.utc).strftime("%Y-%m-%d")
                ref = ch.get("amount_refunded") or 0
                rows.append(f'{when} {ch.get("amount", 0) / 100:.2f} {str(ch.get("currency", "")).upper()} {ch.get("status")}'
                            + (f' (refunded {ref / 100:.2f})' if ref else "") + (f' · {_clip(ch.get("description"), 60)}' if ch.get("description") else "")
                            + (f' · app={ch["metadata"]["app"]}' if (ch.get("metadata") or {}).get("app") else ""))
            return "Recent Stripe payments: " + ("; ".join(rows) if rows else "none")
        if action == "create_product":
            name = _clip(args.get("name"), 120)
            if not name:
                raise ConnectionError_("A product name is required.")
            price = _price(args.get("price_usd"))
            interval = args.get("interval") if args.get("interval") in ("month", "year") else "once"
            form = {"name": name, "description": _clip(args.get("description"), 500) or name,
                    "metadata[app]": "crew", "metadata[mission]": mission_id,
                    "default_price_data[currency]": "usd", "default_price_data[unit_amount]": str(int(round(price * 100)))}
            if interval != "once":
                form["default_price_data[recurring][interval]"] = interval
            body = _ok(await c.post(f"{STRIPE_API}/products", headers=auth, data=form), "Stripe")
            return f'Stripe product created: {body.get("id")} "{name}", price {body.get("default_price")} (${price:.2f}{"" if interval == "once" else "/" + interval}).'
        if action == "create_payment_link":
            pid = _clip(args.get("price_id"), 80)
            if not pid.startswith("price_"):
                raise ConnectionError_("price_id must be a Stripe price id (price_...).")
            form = {"line_items[0][price]": pid, "line_items[0][quantity]": "1", "metadata[app]": "crew", "metadata[mission]": mission_id}
            body = _ok(await c.post(f"{STRIPE_API}/payment_links", headers=auth, data=form), "Stripe")
            return f'Stripe payment link created: {body.get("url")}'
    raise ConnectionError_("Unsupported Stripe action.")


def _ok(r: httpx.Response, who: str) -> Dict[str, Any]:
    if r.status_code >= 400:
        try:
            msg = (r.json().get("error") or {}).get("message") or r.json().get("error_description") or r.json().get("error")
        except Exception:
            msg = ""
        raise ConnectionError_(f"{who} refused the request ({r.status_code}){': ' + _clip(msg, 200) if msg else ''}.")
    try:
        return r.json()
    except ValueError:
        raise ConnectionError_(f"{who} sent an unreadable answer.")


# ── Etsy: OAuth (PKCE) and actions ───────────────────────────────────────────────────────────────

def _etsy_key_header(env=None) -> str:
    e = _env(env)
    key, secret = (e.get("ETSY_API_KEY") or "").strip(), (e.get("ETSY_SHARED_SECRET") or "").strip()
    return f"{key}:{secret}" if secret else key


class OAuthStates:
    """Short-lived PKCE states for 'Connect Etsy', so the callback can only finish a connection this server started for this user."""

    TTL = 600

    def __init__(self, clock=time.monotonic) -> None:
        self._items: Dict[str, Dict[str, Any]] = {}
        self._clock = clock

    def new(self, user_id: str) -> Dict[str, str]:
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        self._items = {k: v for k, v in self._items.items() if self._clock() - v["t"] < self.TTL}
        self._items[state] = {"user_id": user_id, "verifier": verifier, "t": self._clock()}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return {"state": state, "verifier": verifier, "challenge": challenge}

    def take(self, state: str) -> Optional[Dict[str, Any]]:
        v = self._items.pop(state or "", None)
        if not v or self._clock() - v["t"] >= self.TTL:
            return None
        return v


ETSY_STATES = OAuthStates()


def etsy_connect_url(user_id: str, env=None, states: OAuthStates = ETSY_STATES) -> str:
    e = _env(env)
    s = states.new(user_id)
    q = {"response_type": "code", "redirect_uri": (e.get("ETSY_REDIRECT_URI") or "").strip(), "scope": ETSY_SCOPES,
         "client_id": (e.get("ETSY_API_KEY") or "").strip(), "state": s["state"], "code_challenge": s["challenge"], "code_challenge_method": "S256"}
    return f"{ETSY_CONNECT_URL}?{urlencode(q)}"


async def _save_etsy_tokens(store, user_id: str, body: Dict[str, Any]) -> None:
    if not body.get("access_token") or not body.get("refresh_token"):
        raise ConnectionError_("Etsy did not return tokens.")
    await store.set_secret(user_id, "etsy_access", body["access_token"])
    await store.set_secret(user_id, "etsy_refresh", body["refresh_token"])
    await store.set_secret(user_id, "etsy_expires", str(int(time.time()) + int(body.get("expires_in") or 3600) - 60))


async def etsy_finish_connect(store, state: str, code: str, env=None, states: OAuthStates = ETSY_STATES,
                              client_factory: Optional[ClientFactory] = None) -> str:
    """The OAuth callback: swap the code for tokens, find the shop, store both. Returns the user id it connected."""
    pending = states.take(state)
    if not pending:
        raise ConnectionError_("This Etsy connection link has expired. Start again from the Station.")
    if not code:
        raise ConnectionError_("Etsy did not send a code (was access denied?).")
    e = _env(env)
    cf = client_factory or _default_client
    async with cf() as c:
        r = await c.post(ETSY_TOKEN_URL, data={"grant_type": "authorization_code", "client_id": (e.get("ETSY_API_KEY") or "").strip(),
                                               "redirect_uri": (e.get("ETSY_REDIRECT_URI") or "").strip(), "code": code,
                                               "code_verifier": pending["verifier"]})
        body = _ok(r, "Etsy")
        await _save_etsy_tokens(store, pending["user_id"], body)
        etsy_user = str(body["access_token"]).split(".", 1)[0]
        shops = _ok(await c.get(f"{ETSY_API}/users/{etsy_user}/shops", headers={"x-api-key": _etsy_key_header(env),
                                                                                 "Authorization": f"Bearer {body['access_token']}"}), "Etsy")
    shop_id = shops.get("shop_id") or ((shops.get("results") or [{}])[0].get("shop_id"))
    if shop_id:
        await store.set_secret(pending["user_id"], "etsy_shop_id", str(shop_id))
    return pending["user_id"]


async def _etsy_access(store, user_id: str, env, c: httpx.AsyncClient) -> str:
    access, expires = await store.get_secret(user_id, "etsy_access"), await store.get_secret(user_id, "etsy_expires")
    if access and expires and expires.isdigit() and int(expires) > time.time():
        return access
    refresh = await store.get_secret(user_id, "etsy_refresh")
    if not refresh:
        raise ConnectionError_("Etsy is not connected.")
    r = await c.post(ETSY_TOKEN_URL, data={"grant_type": "refresh_token", "client_id": (_env(env).get("ETSY_API_KEY") or "").strip(),
                                           "refresh_token": refresh})
    body = _ok(r, "Etsy")
    await _save_etsy_tokens(store, user_id, body)
    return body["access_token"]


async def _etsy(action: str, args: Dict[str, Any], *, store, user_id: str, env, cf: ClientFactory) -> str:
    shop_id = await store.get_secret(user_id, "etsy_shop_id")
    if not shop_id:
        raise ConnectionError_("No Etsy shop was found on the connected account. Open a shop on Etsy, then reconnect.")
    async with cf() as c:
        token = await _etsy_access(store, user_id, env, c)
        h = {"x-api-key": _etsy_key_header(env), "Authorization": f"Bearer {token}"}
        if action == "get_shop":
            b = _ok(await c.get(f"{ETSY_API}/shops/{shop_id}", headers=h), "Etsy")
            return f'Etsy shop "{b.get("shop_name")}": {b.get("listing_active_count", 0)} active listings, {b.get("digital_listing_count", 0)} digital.'
        if action == "create_draft_listing":
            title = _clip(args.get("title"), 140)
            if not title:
                raise ConnectionError_("A listing title is required.")
            try:
                qty, tax = int(args.get("quantity") or 1), int(args.get("taxonomy_id"))
            except (TypeError, ValueError):
                raise ConnectionError_("quantity and taxonomy_id must be whole numbers.")
            form = {"title": title, "description": str(args.get("description") or title)[:5000], "price": f"{_price(args.get('price_usd')):.2f}",
                    "quantity": str(max(1, min(qty, 999))), "taxonomy_id": str(tax),
                    "who_made": args.get("who_made") if args.get("who_made") in ("i_did", "someone_else", "collective") else "i_did",
                    "when_made": _clip(args.get("when_made"), 30) or "made_to_order", "is_supply": "false",
                    "type": "physical" if args.get("type") == "physical" else "download"}
            if form["type"] == "physical":
                spid = _clip(args.get("shipping_profile_id"), 30)
                if not spid.isdigit():
                    raise ConnectionError_("Physical listings need shipping_profile_id (a shipping profile from the Etsy shop). Digital (download) listings do not.")
                form["shipping_profile_id"] = spid
            b = _ok(await c.post(f"{ETSY_API}/shops/{shop_id}/listings", headers=h, data=form), "Etsy")
            return f'Etsy draft listing created: {b.get("listing_id")} "{title}" (draft: not visible to buyers until you activate it on Etsy).'
    raise ConnectionError_("Unsupported Etsy action.")
