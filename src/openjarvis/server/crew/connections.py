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
from typing import Any, Callable, Dict, List, Mapping, Optional
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
        "list_payment_links": {"risk": "read", "about": "List recent Stripe payment links: id (plink_...), URL, active, and whether automatic delivery is on.", "args": {}},
        "get_balance": {"risk": "read", "about": "Read the Stripe balance (available and pending, per currency).", "args": {}},
        "recent_payments": {"risk": "read", "about": "List the 20 most recent payments: amount, status, refunds, date and description (no card or contact details).",
                            "args": {}},
        "create_product": {"risk": "checkpoint", "about": "Create a live Stripe product with one price.",
                           "args": {"name": "text", "description": "text", "price_usd": "number", "interval": "one of: once, month, year"}},
        "create_payment_link": {"risk": "checkpoint", "about": "Create a public Stripe payment link for an existing price. Give deliver_files (names of "
                                                              "files in THIS mission's outbox) and buyers are sent to a download page for exactly those files right after paying.",
                                "args": {"price_id": "text (price_...)", "deliver_files": "optional list of outbox file names the buyer gets"}},
        "set_delivery": {"risk": "checkpoint", "about": "Turn on automatic delivery for an existing payment link: after paying, the buyer goes to a "
                                                       "download page for exactly these files from THIS mission's outbox.",
                         "args": {"payment_link_id": "text (plink_...)", "deliver_files": "list of outbox file names the buyer gets"}},
    },
    "etsy": {
        "get_shop": {"risk": "read", "about": "Read the connected Etsy shop's name and listing counts.", "args": {}},
        "create_draft_listing": {"risk": "draft", "about": "Create a DRAFT Etsy listing (not visible to buyers, no listing fee until the owner activates it).",
                                 "args": {"title": "text", "description": "text", "price_usd": "number", "quantity": "integer",
                                          "taxonomy_id": "integer (Etsy category id)", "who_made": "i_did | someone_else | collective",
                                          "when_made": "made_to_order | 2020_2026 | ...", "type": "download (default) | physical",
                                          "shipping_profile_id": "integer, physical only"}},
    },
    "marnyx": {
        "list_accounts": {"risk": "read", "about": "List the Marnyx businesses (brands) with their connected social accounts and how many email subscribers each has.",
                          "args": {}},
        "post_status": {"risk": "read", "about": "Where posts already sent to Marnyx stand (scheduled, published with link, failed with reason).",
                        "args": {"business": "business name or id", "ids": "list of Marnyx post ids"}},
        "schedule_week": {"risk": "checkpoint", "about": "Schedule a week of posts for ONE brand on its connected socials through Marnyx. The owner approves the whole "
                                                        "week at once; Marnyx adds a picture to each post and publishes each at its time.",
                          "args": {"business": "business name or id (from list_accounts)",
                                   "posts": "list of up to 28: {body, hashtags, platforms: [instagram|facebook|x|tiktok], scheduled_at: ISO time with offset, "
                                            "e.g. 2026-10-27T09:00:00-07:00}"}},
        "send_email": {"risk": "checkpoint", "about": "Send an email campaign to ONE brand's subscribed contacts through Marnyx (Postmark): consent-only list, "
                                                     "unsubscribe link and mailing address added, daily and monthly caps apply.",
                       "args": {"business": "business name or id", "subject": "text (up to 150)", "body_text": "plain text (up to 8000)"}},
    },
    "google_play": {},
    "app_store": {},
}

LABELS = {"stripe": "Stripe", "etsy": "Etsy", "marnyx": "Marnyx (your socials and email)", "google_play": "Google Play", "app_store": "App Store"}

SETUP = {
    "stripe": "Set STRIPE_SECRET_KEY on Render (the Aibussol billing key works).",
    "etsy": "Register an app at etsy.com/developers (wait for approval), set ETSY_API_KEY (and ETSY_SHARED_SECRET if Etsy shows one) and "
            "ETSY_REDIRECT_URI=https://aibussol.com/crew/connect/etsy/callback on Render, then tap Connect Etsy in the Station.",
    "marnyx": "In Marnyx open Settings → Connected apps → Create key, then tap Connect here and paste it.",
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
        "etsy": {"connected": etsy_app_configured(env) and etsy_linked, "built": True, "can_connect": etsy_app_configured(env) and not etsy_linked,
                 "note": (await store.get_secret(user_id, "etsy_shop_note")) or ""},
        "marnyx": {"connected": bool(marnyx_key(env)), "built": True},
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
    if platform == "marnyx" and action == "schedule_week":
        posts = _week(args)
        lines = [f'Schedule {len(posts)} post(s) for {_clip(args.get("business"), 60)} through Marnyx (each gets a picture and goes out at its time):']
        for i, p in enumerate(posts, 1):
            lines.append(f'{i}. {_when_label(p["scheduled_at"])} · {", ".join(PLAT_NAMES.get(x, x) for x in p["platforms"])}: {_clip(p["body"], 220)}'
                         + (f' {p["hashtags"]}' if p.get("hashtags") else ""))
        return "\n".join(lines)
    if platform == "marnyx" and action == "send_email":
        subj, body = _email(args)
        return (f'Email {_clip(args.get("business"), 60)}\'s subscribed contacts through Marnyx. Subject: "{subj}"\n'
                f'{_clip(body, 900)}' + ("…" if len(body) > 900 else ""))
    if platform == "stripe" and action == "create_product":
        interval = args.get("interval") if args.get("interval") in ("month", "year") else "once"
        price = _price(args.get("price_usd"))
        when = "" if interval == "once" else f" per {interval}"
        return f'Create a live Stripe product "{_clip(args.get("name"), 80)}" priced ${price:.2f}{when}.'
    if platform == "stripe" and action == "create_payment_link":
        files = _deliver_files(args, required=False)
        return (f"Create a public Stripe payment link for price {_clip(args.get('price_id'), 60)} (anyone with the link can pay)."
                + (f" Buyers get a download page for: {', '.join(files)}." if files else " No automatic delivery."))
    if platform == "stripe" and action == "set_delivery":
        return (f"Turn on automatic delivery for payment link {_clip(args.get('payment_link_id'), 60)}: after paying, buyers get a download page "
                f"for: {', '.join(_deliver_files(args))}.")
    return f"{LABELS.get(platform, platform)}: {action} with {json.dumps(args)[:200]}"


DELIVER_URL = "https://aibussol.com/deliver?session_id={CHECKOUT_SESSION_ID}"


def _deliver_files(args: Dict[str, Any], required: bool = True) -> List[str]:
    raw = args.get("deliver_files") or []
    files = [_clip(x, 80) for x in (raw if isinstance(raw, list) else str(raw).split(",")) if _clip(x, 80)]
    if required and not files:
        raise ConnectionError_("deliver_files must list the outbox file names the buyer gets.")
    if len(",".join(files)) > 480:
        raise ConnectionError_("Too many or too long file names for delivery (Stripe keeps up to 500 characters).")
    return files


def _delivery_form(files: List[str], mission_id: str) -> Dict[str, str]:
    return {"after_completion[type]": "redirect", "after_completion[redirect][url]": DELIVER_URL,
            "metadata[deliver_files]": ",".join(files), "metadata[mission]": mission_id, "metadata[app]": "crew"}


def _price(v: Any) -> float:
    try:
        p = float(v)
    except (TypeError, ValueError):
        raise ConnectionError_("price_usd must be a number.")
    if not (0.5 <= p <= 10000):
        raise ConnectionError_("price_usd must be between 0.50 and 10000.")
    return round(p, 2)


# ── Marnyx (the owner's own social media manager): posts and email through its API ──────────────

MARNYX_API = "https://marnyx.onrender.com/api/v1"
PLAT_NAMES = {"instagram": "Instagram", "facebook": "Facebook", "x": "X", "tiktok": "TikTok"}


def marnyx_key(env=None) -> str:
    return (_env(env).get("MARNYX_API_KEY") or "").strip()


def marnyx_url(env=None) -> str:
    return ((_env(env).get("MARNYX_API_URL") or "").strip() or MARNYX_API).rstrip("/")


def _when(v: Any) -> dt.datetime:
    try:
        t = dt.datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        raise ConnectionError_(f"scheduled_at must be an ISO time with its offset, like 2026-10-27T09:00:00-07:00 (got {_clip(v, 40)!r}).")
    if t.tzinfo is None:
        raise ConnectionError_("scheduled_at needs its time zone offset, like -07:00 for Los Angeles.")
    return t


def _when_label(iso: str) -> str:
    from zoneinfo import ZoneInfo

    t = _when(iso).astimezone(ZoneInfo("America/Los_Angeles"))
    return t.strftime("%a %b %d %I:%M %p").replace(" 0", " ") + " PT"


def _week(args: Dict[str, Any]) -> List[Dict[str, Any]]:
    posts = args.get("posts")
    if not isinstance(posts, list) or not posts:
        raise ConnectionError_("posts must be a list of {body, hashtags, platforms, scheduled_at}.")
    if len(posts) > 28:
        raise ConnectionError_("Up to 28 posts per brand per week.")
    if not _clip(args.get("business"), 60):
        raise ConnectionError_("Name the business (brand) the posts are for.")
    out = []
    for p in posts:
        if not isinstance(p, dict) or not str(p.get("body") or "").strip():
            raise ConnectionError_("Every post needs body text.")
        plats = [x for x in (p.get("platforms") or []) if x in PLAT_NAMES]
        if not plats:
            raise ConnectionError_("Every post needs platforms from: instagram, facebook, x, tiktok.")
        t = _when(p.get("scheduled_at"))
        if t < dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10):
            raise ConnectionError_("Every scheduled_at must be at least 10 minutes from now.")
        out.append({"body": str(p["body"]).strip()[:2200], "hashtags": _clip(p.get("hashtags"), 300), "platforms": plats,
                    "scheduled_at": t.isoformat(), "notes": _clip(p.get("notes"), 200)})
    return sorted(out, key=lambda x: x["scheduled_at"])


def _email(args: Dict[str, Any]):
    subj, body = _clip(args.get("subject"), 150), str(args.get("body_text") or "").strip()[:8000]
    if not subj or not body:
        raise ConnectionError_("An email needs a subject and body_text.")
    if not _clip(args.get("business"), 60):
        raise ConnectionError_("Name the business the email is for.")
    return subj, body


async def _marnyx(action: str, args: Dict[str, Any], *, env, cf: "ClientFactory") -> str:
    base, h = marnyx_url(env), {"Authorization": f"Bearer {marnyx_key(env)}", "Accept": "application/json"}

    def ok(r):
        if r.status_code == 401:
            raise ConnectionError_("Marnyx refused the API key (revoked or wrong). Make a new one in Marnyx Settings → Connected apps.")
        if r.status_code >= 400:
            try:
                msg = r.json().get("error")
            except Exception:
                msg = ""
            raise ConnectionError_(f"Marnyx answered {r.status_code}" + (f": {_clip(msg, 200)}" if msg else "."))
        return r.json()

    async with cf() as c:
        biz = ok(await c.get(f"{base}/businesses", headers=h)).get("businesses") or []
        if action == "list_accounts":
            if not biz:
                return "Marnyx: no businesses yet."
            return "Marnyx businesses: " + "; ".join(
                f'{b["name"]} (id {b["id"]}): ' + (", ".join(f'{PLAT_NAMES.get(x["platform"], x["platform"])}{"" if x["status"] == "active" else " (needs reconnect)"}'
                                                          for x in b.get("connections") or []) or "no socials connected")
                + f', {b.get("subscribed_contacts", 0)} email subscriber(s)' + ("" if b.get("mailing_address_set") else ", no mailing address set (email cannot be sent)")
                for b in biz)
        want = str(args.get("business") or "").strip().lower()
        match = [b for b in biz if str(b["id"]) == want or b["name"].strip().lower() == want] or \
                [b for b in biz if want and want in b["name"].strip().lower()]
        if len(match) != 1:
            raise ConnectionError_(f'No single Marnyx business matches "{_clip(args.get("business"), 60)}". Businesses: ' + ", ".join(b["name"] for b in biz))
        b = match[0]
        if action == "post_status":
            ids = ",".join(str(int(x)) for x in (args.get("ids") or []) if str(x).isdigit())[:800]
            rows = ok(await c.get(f"{base}/businesses/{b['id']}/posts", headers=h, params={"ids": ids})).get("posts") or []
            return f'Marnyx posts for {b["name"]}: ' + ("; ".join(
                f'#{p["id"]} ' + ", ".join(f'{PLAT_NAMES.get(v["platform"], v["platform"])} {v["status"]}' + (f' {v["url"]}' if v.get("url") else "")
                                         + (f' ({_clip(v["error"], 80)})' if v.get("error") else "") for v in p.get("versions") or [])
                + (f' · held: {_clip(p["auto_note"], 160)}' if p.get("auto_note") else "") for p in rows) or "none found")
        if action == "schedule_week":
            posts = _week(args)
            out = ok(await c.post(f"{base}/businesses/{b['id']}/posts", headers=h, json={"approved_in": "Aibussol", "posts": posts}))
            made = [x for x in out.get("results") or [] if x.get("id")]
            bad = [f'post {x["index"] + 1}: {x.get("error")}' for x in out.get("results") or [] if not x.get("id")]
            return (f'Marnyx accepted {len(made)} of {len(posts)} post(s) for {b["name"]}; each is approved and scheduled once its picture is ready. '
                    f'Marnyx post ids: {", ".join(str(x["id"]) for x in made)}.' + (" Not accepted: " + "; ".join(bad) if bad else ""))
        if action == "send_email":
            subj, body = _email(args)
            out = ok(await c.post(f"{base}/businesses/{b['id']}/emails", headers=h, json={"subject": subj, "body_text": body, "approved_in": "Aibussol"}))
            return f'Marnyx is sending "{subj}" to {out.get("recipients")} subscribed contact(s) of {b["name"]} (campaign {out.get("id")}).'
    raise ConnectionError_("Unsupported Marnyx action.")


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
        return await _stripe(action, args, env=env, mission_id=mission_id, cf=cf, store=store)
    if platform == "etsy":
        return await _etsy(action, args, store=store, user_id=user_id, env=env, cf=cf)
    if platform == "marnyx":
        return await _marnyx(action, args, env=env, cf=cf)
    raise ConnectionError_(f"{LABELS.get(platform, platform)} is not built yet.")


async def _check_outbox(store, mission_id: str, files: List[str]) -> None:
    have = {o["name"] for o in await store.list_outputs(mission_id)} if store is not None and mission_id else set()
    missing = [f for f in files if f not in have]
    if missing:
        raise ConnectionError_(f"These files are not in this mission's outbox: {', '.join(missing)}. Outbox: {', '.join(sorted(have)) or 'empty'}.")


async def _stripe(action: str, args: Dict[str, Any], *, env, mission_id: str, cf: ClientFactory, store=None) -> str:
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
        if action == "list_payment_links":
            body = _ok(await c.get(f"{STRIPE_API}/payment_links", headers=auth, params={"limit": 20}), "Stripe")
            rows = [f'{p.get("id")} {p.get("url")} {"active" if p.get("active") else "inactive"}'
                    + (f' · delivers {(p.get("metadata") or {}).get("deliver_files")}' if (p.get("metadata") or {}).get("deliver_files") else " · no delivery")
                    for p in body.get("data", [])]
            return "Stripe payment links: " + ("; ".join(rows) if rows else "none")
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
            files = _deliver_files(args, required=False)
            if files:
                await _check_outbox(store, mission_id, files)
            form = {"line_items[0][price]": pid, "line_items[0][quantity]": "1", "metadata[app]": "crew", "metadata[mission]": mission_id}
            if files:
                form.update(_delivery_form(files, mission_id))
            body = _ok(await c.post(f"{STRIPE_API}/payment_links", headers=auth, data=form), "Stripe")
            return f'Stripe payment link created: {body.get("url")} ({body.get("id")})' + (f"; buyers get {len(files)} file(s) automatically." if files else ".")
        if action == "set_delivery":
            lid = _clip(args.get("payment_link_id"), 80)
            if not lid.startswith("plink_"):
                raise ConnectionError_("payment_link_id must be a Stripe payment link id (plink_...).")
            files = _deliver_files(args)
            await _check_outbox(store, mission_id, files)
            body = _ok(await c.post(f"{STRIPE_API}/payment_links/{lid}", headers=auth, data=_delivery_form(files, mission_id)), "Stripe")
            return f'Automatic delivery is on for {body.get("url")}: buyers get {", ".join(files)} right after paying.'
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
        try:
            shops = _ok(await c.get(f"{ETSY_API}/users/{etsy_user}/shops", headers={"x-api-key": _etsy_key_header(env),
                                                                                     "Authorization": f"Bearer {body['access_token']}"}), "Etsy")
        except ConnectionError_ as exc:
            # the account is connected (tokens saved); the shop lookup failed: keep the reason, the Station and Relay show it
            logger.warning("etsy shop lookup failed: %s", exc)
            await store.set_secret(pending["user_id"], "etsy_shop_note", f"Connected, but Etsy did not return a shop: {exc}")
            return pending["user_id"]
    shop_id = shops.get("shop_id") or ((shops.get("results") or [{}])[0].get("shop_id"))
    if shop_id:
        await store.set_secret(pending["user_id"], "etsy_shop_id", str(shop_id))
        await store.delete_secret(pending["user_id"], "etsy_shop_note")
    else:
        await store.set_secret(pending["user_id"], "etsy_shop_note", "Connected, but this Etsy account has no shop yet. Open a shop on Etsy, then reconnect.")
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
        note = await store.get_secret(user_id, "etsy_shop_note")
        raise ConnectionError_(note or "No Etsy shop was found on the connected account. Open a shop on Etsy, then reconnect.")
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
