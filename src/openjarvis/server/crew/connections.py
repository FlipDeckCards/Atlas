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
  Google Play  a Google Cloud service account JSON key (pasted on the Station, kept encrypted) that Play Console has invited; reads reviews
               through the Android Publisher API. Nothing is posted or changed on Play.
  App Store    not built yet (planned: App Store Connect API key)
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import os
import re
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
ETSY_SCOPES = "listings_r listings_w shops_r transactions_r"

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
        "get_sales": {"risk": "read", "about": "Read the shop's orders and revenue over the last N days (needs Etsy's sales permission: reconnect Etsy once after it was added).",
                      "args": {"days": "integer, 1 to 90 (default 30)"}},
        "create_draft_listing": {"risk": "draft", "about": "Create a DRAFT Etsy listing (not visible to buyers, no listing fee until the owner activates it).",
                                 "args": {"title": "text", "description": "text", "price_usd": "number", "quantity": "integer",
                                          "taxonomy_id": "integer (Etsy category id)", "who_made": "i_did | someone_else | collective",
                                          "when_made": "made_to_order | 2020_2026 | ...", "type": "download (default) | physical",
                                          "shipping_profile_id": "integer, physical only", "tags": "list of up to 13 tags (20 characters each)"}},
        "add_listing_image": {"risk": "draft", "about": "Add a photo or mockup to a DRAFT Etsy listing from the media library (Prism's generate_image "
                                                       "gives the item id). The first image is the main photo; up to 10.",
                              "args": {"listing_id": "integer", "media_item_id": "media library item id", "rank": "1-10, optional"}},
        "add_listing_file": {"risk": "draft", "about": "Attach a download file to a DRAFT digital Etsy listing. Give pack (a design pack from "
                                                      "make_design_files, e.g. gnome-snow-globe.pack.json) to attach one zip with the color PNG and JPG, "
                                                      "and the black-and-white PNG, JPG, SVG and DXF cut files. Or give file: an outbox file, sent as it is "
                                                      "(SVG, DXF, PDF); markdown or text is turned into a clean PDF first.",
                             "args": {"listing_id": "integer", "pack": "design pack file name (*.pack.json), optional",
                                      "file": "outbox file name, when not a pack", "name": "file name buyers see, e.g. Gnome-Snow-Globe.zip"}},
        "publish_listings": {"risk": "checkpoint", "about": "Make DRAFT Etsy listings live (public; Etsy charges $0.20 per listing). One approval "
                                                           "for the whole batch. Each listing needs a photo, and digital ones a file.",
                             "args": {"listing_ids": "list of integers"}},
    },
    "google": {
        "gmail_search": {"risk": "read", "about": "Search the owner's Gmail (Gmail search syntax, e.g. from:etsy newer_than:7d) and list the matches: sender, subject, date, a short snippet and the message id.",
                         "args": {"query": "Gmail search text", "max": "integer 1 to 20 (default 10)"}},
        "gmail_read": {"risk": "read", "about": "Read one Gmail message in full (headers and plain text).", "args": {"id": "message id from gmail_search"}},
        "gmail_send": {"risk": "checkpoint", "about": "Send an email from the owner's Gmail. The owner sees the exact recipient, subject and text and taps Approve first.",
                       "args": {"to": "one email address", "subject": "text (up to 200)", "body": "plain text (up to 8000)"}},
        "calendar_list": {"risk": "read", "about": "List the owner's upcoming Google Calendar events (title, start, end, place) for the next days.",
                          "args": {"days": "integer 1 to 31 (default 7)"}},
        "calendar_add": {"risk": "checkpoint", "about": "Add an event to the owner's Google Calendar. The owner sees the exact title, time and place and taps Approve first. Never invites other people.",
                         "args": {"title": "text (up to 200)", "start": "ISO time WITH offset, e.g. 2026-10-31T17:00:00-07:00", "end": "optional ISO time with offset", "minutes": "optional length, 5 to 1440 (default 60)",
                                  "location": "optional text", "notes": "optional text (up to 1000)"}},
        "drive_search": {"risk": "read", "about": "Search the owner's Google Drive by name or content and list files: name, type, modified date and the file id.",
                         "args": {"query": "words to find", "max": "integer 1 to 20 (default 10)"}},
        "drive_read": {"risk": "read", "about": "Read one Drive file as text: Google Docs, Sheets (as CSV) and plain-text files (not images or PDFs).", "args": {"file_id": "id from drive_search"}},
        "drive_save": {"risk": "draft", "about": "Save a file from a mission outbox into the owner's Drive, in a folder called Aibussol that this app creates. Never changes or deletes files he already has.",
                       "args": {"file": "outbox file name (a book PDF, a document...)", "name": "optional file name in Drive"}},
    },
    "marnyx": {
        "list_accounts": {"risk": "read", "about": "List the Marnyx businesses (brands) with their connected social accounts and how many email subscribers each has.",
                          "args": {}},
        "post_status": {"risk": "read", "about": "Where posts already sent to Marnyx stand (scheduled, published with link, failed with reason).",
                        "args": {"business": "business name or id", "ids": "list of Marnyx post ids"}},
        "schedule_week": {"risk": "draft", "about": "Put up to 28 posts for ONE brand on its Marnyx calendar as HELD DRAFTS at their posting times. Nothing is published: "
                                                   "Marnyx adds a picture to each, and the owner approves the whole week once in Aibussol before any of it goes out.",
                          "args": {"business": "business name or id (from list_accounts)",
                                   "posts": "list of up to 28: {body, hashtags, platforms: [instagram|facebook|x|tiktok], scheduled_at: ISO time with offset, "
                                            "e.g. 2026-10-27T09:00:00-07:00}"}},
        "list_awaiting": {"risk": "read", "about": "The held draft posts on one brand's Marnyx calendar that are still waiting for the owner's approval (count and times).",
                          "args": {"business": "business name or id"}},
        "approve_posts": {"risk": "checkpoint", "about": "Approve ALL of one brand's held drafts so Marnyx publishes each at its posting time. Only Helix asks for this, after the owner said yes.",
                          "args": {"business": "business name or id"}},
        "send_email": {"risk": "checkpoint", "about": "Send an email campaign to ONE brand's subscribed contacts through Marnyx (Postmark): consent-only list, "
                                                     "unsubscribe link and mailing address added, daily and monthly caps apply.",
                       "args": {"business": "business name or id", "subject": "text (up to 150)", "body_text": "plain text (up to 8000)"}},
    },
    "google_play": {
        "list_reviews": {"risk": "read", "about": "Recent Google Play reviews (stars, text, date) for one of the owner's apps; newest first.",
                         "args": {"package": "app package name, e.g. com.flipdeckllc.flipdeckcards (omit for every connected app)", "limit": "1-20, default 10"}},
        "reply_review": {"risk": "checkpoint", "about": "Post a PUBLIC reply to one Google Play review as the developer. The owner approves each reply first. Max 350 characters, "
                                                        "warm and honest, no promises you cannot keep, no links.",
                         "args": {"package": "app package name", "review_id": "the id shown by list_reviews", "reply_text": "the reply (max 350 characters)"}},
    },
    "app_store": {},
}

RENDER_SETUP = ("Paste your Render API key in the Connections panel on the Station. Create it in Render: Account Settings, API Keys, Create API Key. "
                "Aibussol only checks it can read your services; it never shows the key again.")
RENDER_API = "https://api.render.com/v1"

LABELS = {"stripe": "Stripe", "etsy": "Etsy", "marnyx": "Marnyx (your socials and email)", "google": "Google (Gmail, Drive and Calendar)", "google_play": "Google Play", "app_store": "App Store"}

SETUP = {
    "stripe": "Set STRIPE_SECRET_KEY on Render (the Aibussol billing key works).",
    "etsy": "Register an app at etsy.com/developers (wait for approval), set ETSY_API_KEY (and ETSY_SHARED_SECRET if Etsy shows one) and "
            "ETSY_REDIRECT_URI=https://aibussol.com/crew/connect/etsy/callback on Render, then tap Connect Etsy in the Station.",
    "google": "Create a Google Cloud OAuth client (type Web application), turn on the Gmail API and the Drive API, and set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and "
              "GOOGLE_REDIRECT_URI=https://aibussol.com/crew/connect/google/callback on Render, then tap Connect Google in the Station.",
    "marnyx": "In Marnyx open Settings → Connected apps → Create key, then tap Connect here and paste it.",
    "google_play": "Tap Connect and paste a Google Cloud service account JSON key that Play Console has invited, plus your app package names.",
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
    google_linked = bool(await store.get_secret(user_id, "google_refresh"))
    out = {
        "stripe": {"connected": stripe_on, "built": True},
        "etsy": {"connected": etsy_app_configured(env) and etsy_linked, "built": True, "can_connect": etsy_app_configured(env) and not etsy_linked,
                 "note": (await store.get_secret(user_id, "etsy_shop_note")) or ""},
        "marnyx": {"connected": bool(marnyx_key(env)), "built": True},
        "google": {"connected": google_app_configured(env) and google_linked, "built": True, "can_connect": google_app_configured(env) and not google_linked,
                   "note": (await store.get_secret(user_id, "google_email")) or ""},
        "google_play": {"connected": bool(play_key(env)), "built": True, "note": ", ".join(play_apps(env))},
        "app_store": {"connected": False, "built": False},
    }
    for k, v in out.items():
        v.update(label=LABELS[k], setup="" if v["connected"] else SETUP[k],
                 actions=[{"name": n, "risk": a["risk"], "about": a["about"]} for n, a in ACTIONS[k].items()])
    from openjarvis.server.crew import github, research

    out["github"] = {"connected": github.configured(env), "built": True, "label": "GitHub (Forge's code)",
                     "setup": "" if github.configured(env) else github.SETUP, "actions": []}
    out["workbench"] = {"connected": False, "built": True, "label": "Your PC (Workbench for Rig)", "setup": "", "actions": [], "pc": True}
    rk = bool((e.get("RENDER_API_KEY") or "").strip())
    out["render"] = {"connected": rk, "built": True, "label": "Render (your hosting)", "actions": [],
                     "setup": "" if rk else RENDER_SETUP}
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
        lines = [f'Put {len(posts)} held draft post(s) for {_clip(args.get("business"), 60)} on the Marnyx calendar (nothing goes out until you approve the week):']
        for i, p in enumerate(posts, 1):
            lines.append(f'{i}. {_when_label(p["scheduled_at"])} · {", ".join(PLAT_NAMES.get(x, x) for x in p["platforms"])}: {_clip(p["body"], 220)}'
                         + (f' {p["hashtags"]}' if p.get("hashtags") else ""))
        return "\n".join(lines)
    if platform == "google" and action == "gmail_send":
        to, subj, body = _mail_args(args)
        return f'Send this email from your Gmail to {to}. Subject: "{subj}"\n{_clip_text(body, 1200)}'
    if platform == "google" and action == "calendar_add":
        ev = _event_args(args)
        return f'Add this event to your Google Calendar: {_event_label(ev)}.' + (f' Notes: {ev["notes"]}' if ev["notes"] else "") + " Nobody else is invited."
    if platform == "google_play" and action == "reply_review":
        return (f'Post this PUBLIC reply to a Google Play review of {_clip(args.get("package"), 80)} (review {_clip(args.get("review_id"), 60)}): '
                f'"{_clip(args.get("reply_text"), 350)}"')
    if platform == "marnyx" and action == "approve_posts":
        return f'Approve every held draft for {_clip(args.get("business"), 60)} in Marnyx so each is published at its posting time.'
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
    if platform == "etsy" and action == "publish_listings":
        ids = _listing_ids(args)
        return (f"Publish {len(ids)} Etsy listing(s) so buyers can see and buy them (Etsy charges $0.20 each, about ${0.2 * len(ids):.2f}): "
                + ", ".join(str(i) for i in ids) + ".")
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


def play_key(env=None) -> str:
    return (_env(env).get("GOOGLE_PLAY_KEY") or "").strip()


def play_apps(env=None) -> List[str]:
    return [x.strip() for x in (_env(env).get("GOOGLE_PLAY_APPS") or "").split(",") if x.strip()]


PLAY_API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"
PLAY_SCOPE = "https://www.googleapis.com/auth/androidpublisher"


def parse_play_key(raw: str) -> Dict[str, str]:
    """The service account JSON file's text -> its fields, or ConnectionError_ with a safe message (never echoes the key)."""
    try:
        k = json.loads(raw)
    except Exception:
        raise ConnectionError_("That is not the JSON key file. Open the downloaded .json file, copy ALL of its text and paste it.") from None
    if not isinstance(k, dict) or k.get("type") != "service_account" or not k.get("client_email") or "BEGIN PRIVATE KEY" not in str(k.get("private_key") or ""):
        raise ConnectionError_("That JSON is not a service account key (it should contain type service_account, a client_email and a private_key).")
    return {"client_email": str(k["client_email"]), "private_key": str(k["private_key"]), "token_uri": str(k.get("token_uri") or "https://oauth2.googleapis.com/token")}


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


async def play_access_token(raw_key: str, c: httpx.AsyncClient) -> str:
    """Sign a short-lived JWT with the service account's private key and trade it at Google for an access token."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    k = parse_play_key(raw_key)
    now = int(time.time())
    head = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claim = _b64u(json.dumps({"iss": k["client_email"], "scope": PLAY_SCOPE, "aud": k["token_uri"], "iat": now, "exp": now + 3000}).encode())
    try:
        pk = serialization.load_pem_private_key(k["private_key"].encode(), password=None)
        sig = pk.sign(f"{head}.{claim}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        raise ConnectionError_("The private key in that file could not be read. Download a fresh JSON key and paste it again.") from None
    r = await c.post(k["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": f"{head}.{claim}.{_b64u(sig)}"})
    if r.status_code >= 400:
        raise ConnectionError_("Google did not accept that key. Check the Google Play Android Developer API is enabled on its Cloud project and the key was not deleted.")
    return str(r.json().get("access_token") or "")


async def play_reviews(c: httpx.AsyncClient, token: str, package: str, limit: int = 10) -> List[Dict[str, Any]]:
    r = await c.get(f"{PLAY_API}/{package}/reviews", params={"maxResults": max(1, min(limit, 20))}, headers={"Authorization": f"Bearer {token}"})
    if r.status_code in (401, 403):
        raise ConnectionError_(f"Play Console has not given this key access to {package} yet. In Play Console, Users and permissions, invite the service account and grant it that app "
                               "(it can take a while to start working after you invite it).")
    if r.status_code == 404:
        raise ConnectionError_(f"Google Play has no app with the package name {package}.")
    return _ok(r, "Google Play").get("reviews") or []


def _review_row(rv: Dict[str, Any]) -> Dict[str, Any]:
    """One Play review flattened: id, stars, text, when, and whether the developer has already replied."""
    cs = rv.get("comments") or []
    uc = ((cs[0] if cs else {}).get("userComment") or {})
    replied = any((c or {}).get("developerComment") for c in cs[1:])
    secs = int((uc.get("lastModified") or {}).get("seconds") or 0)
    return {"id": str(rv.get("reviewId") or ""), "stars": int(uc.get("starRating") or 0), "text": _clip(uc.get("text"), 400),
            "when": dt.datetime.fromtimestamp(secs, dt.timezone.utc), "replied": replied}


async def play_week(env, cf: "ClientFactory") -> Dict[str, Any]:
    """The last week's reviews (Google only returns that much) for every connected app: counts, average stars, and the ones still unanswered."""
    out: Dict[str, Any] = {"apps": {}, "new": 0, "stars": 0, "unanswered": 0}
    async with cf() as c:
        token = await play_access_token(play_key(env), c)
        for pkg in play_apps(env)[:6]:
            rows = [_review_row(r) for r in await play_reviews(c, token, pkg, 20)]
            out["apps"][pkg] = rows
            out["new"] += len(rows)
            out["stars"] += sum(r["stars"] for r in rows)
            out["unanswered"] += sum(1 for r in rows if not r["replied"])
    return out


async def _play(action: str, args: Dict[str, Any], *, env, cf: "ClientFactory") -> str:
    if action not in ("list_reviews", "reply_review"):
        raise ConnectionError_(f"google_play.{action} is not an available action.")
    pkg_re = r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+"
    if action == "reply_review":
        pkg, rid, text = str(args.get("package") or "").strip(), str(args.get("review_id") or "").strip(), " ".join(str(args.get("reply_text") or "").split())
        if pkg not in play_apps(env) or not re.fullmatch(pkg_re, pkg):
            raise ConnectionError_("That is not one of the connected Google Play apps.")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", rid):
            raise ConnectionError_("Give the review_id exactly as list_reviews shows it.")
        if not text or len(text) > 350:
            raise ConnectionError_("A Google Play reply must be 1 to 350 characters.")
        async with cf() as c:
            token = await play_access_token(play_key(env), c)
            r = await c.post(f"{PLAY_API}/{pkg}/reviews/{rid}:reply", json={"replyText": text}, headers={"Authorization": f"Bearer {token}"})
            if r.status_code in (401, 403):
                raise ConnectionError_("Play Console has not given this key permission to reply to reviews. Add Reply to reviews for the service account in Users and permissions.")
            _ok(r, "Google Play")
        return f"Replied on Google Play to review {rid} of {pkg}."
    pkgs = [str(args["package"]).strip()] if str(args.get("package") or "").strip() else play_apps(env)
    if not pkgs or any(not re.fullmatch(pkg_re, p) for p in pkgs):
        raise ConnectionError_("Give the app's package name, for example com.flipdeckllc.flipdeckcards.")
    try:
        limit = int(args.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    out = []
    async with cf() as c:
        token = await play_access_token(play_key(env), c)
        for pkg in pkgs[:6]:
            rows = sorted((_review_row(r) for r in await play_reviews(c, token, pkg, limit)), key=lambda r: r["when"], reverse=True)
            out.append(f"{pkg}: " + ("no reviews in the last week." if not rows else ""))
            for r in rows[:limit]:
                out.append(f"- id={r['id']} {r['stars']}/5 on {r['when']:%Y-%m-%d}" + (" (already replied)" if r["replied"] else " (no reply yet)") + f": {r['text']}")
    return "\n".join(out)


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
            probe = await c.get(f"{base}/businesses/{b['id']}/posts/awaiting", headers=h)          # an old Marnyx has no hold support and would PUBLISH: refuse to send
            if probe.status_code == 404:
                raise ConnectionError_("Marnyx has not been updated to hold drafts for approval yet, so nothing was sent. Deploy the latest Marnyx first.")
            out = ok(await c.post(f"{base}/businesses/{b['id']}/posts", headers=h, json={"approved_in": "Aibussol", "hold": True, "posts": posts}))
            made = [x for x in out.get("results") or [] if x.get("id")]
            bad = [f'post {x["index"] + 1}: {x.get("error")}' for x in out.get("results") or [] if not x.get("id")]
            return (f'Marnyx saved {len(made)} of {len(posts)} post(s) for {b["name"]} as HELD DRAFTS on its calendar at their posting times; nothing is published yet. '
                    f'Marnyx post ids: {", ".join(str(x["id"]) for x in made)}.' + (" Not accepted: " + "; ".join(bad) if bad else ""))
        if action == "list_awaiting":
            out = ok(await c.get(f"{base}/businesses/{b['id']}/posts/awaiting", headers=h))
            ps = out.get("posts") or []
            return (f'{b["name"]}: {out.get("awaiting", len(ps))} held draft(s) waiting for approval' + (f', from {_when_label(ps[0]["scheduled_at"])} to {_when_label(ps[-1]["scheduled_at"])}.' if ps else "."))
        if action == "approve_posts":
            out = ok(await c.post(f"{base}/businesses/{b['id']}/posts/approve", headers=h, json={"all": True, "approved_in": "Aibussol"}))
            return f'Approved {out.get("approved", 0)} held draft(s) for {b["name"]}; Marnyx publishes each at its posting time.'
        if action == "send_email":
            subj, body = _email(args)
            out = ok(await c.post(f"{base}/businesses/{b['id']}/emails", headers=h, json={"subject": subj, "body_text": body, "approved_in": "Aibussol"}))
            return f'Marnyx is sending "{subj}" to {out.get("recipients")} subscribed contact(s) of {b["name"]} (campaign {out.get("id")}).'
    raise ConnectionError_("Unsupported Marnyx action.")


# ── Google (Gmail and Drive): OAuth and actions ───────────────────────────────────────────────────

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GOOGLE_SCOPES = ("openid email https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send "
                 "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.file "
                 "https://www.googleapis.com/auth/calendar.events")


def google_app_configured(env=None) -> bool:
    e = _env(env)
    return bool((e.get("GOOGLE_CLIENT_ID") or "").strip() and (e.get("GOOGLE_CLIENT_SECRET") or "").strip() and (e.get("GOOGLE_REDIRECT_URI") or "").strip())


def google_connect_url(user_id: str, env=None, states=None) -> str:
    e = _env(env)
    s = (states or GOOGLE_STATES).new(user_id)
    q = {"client_id": (e.get("GOOGLE_CLIENT_ID") or "").strip(), "redirect_uri": (e.get("GOOGLE_REDIRECT_URI") or "").strip(), "response_type": "code",
         "scope": GOOGLE_SCOPES, "access_type": "offline", "prompt": "consent", "state": s["state"], "code_challenge": s["challenge"], "code_challenge_method": "S256"}
    return f"{GOOGLE_AUTH_URL}?{urlencode(q)}"


async def _save_google_tokens(store, user_id: str, body: Dict[str, Any], keep_refresh: bool = False) -> None:
    if not body.get("access_token"):
        raise ConnectionError_("Google did not return a login.")
    if not body.get("refresh_token") and not keep_refresh:
        raise ConnectionError_("Google did not return a long-lived login. Open myaccount.google.com/permissions, remove Aibussol, and connect again.")
    await store.set_secret(user_id, "google_access", body["access_token"])
    if body.get("refresh_token"):
        await store.set_secret(user_id, "google_refresh", body["refresh_token"])
    await store.set_secret(user_id, "google_expires", str(int(time.time()) + int(body.get("expires_in") or 3600) - 60))


async def google_finish_connect(store, state: str, code: str, env=None, states=None, client_factory: Optional[ClientFactory] = None) -> str:
    """The OAuth callback: swap the code for tokens and note which Google account it is. Returns the user id it connected."""
    pending = (states or GOOGLE_STATES).take(state)
    if not pending:
        raise ConnectionError_("This Google connection link has expired. Start again from the Station.")
    if not code:
        raise ConnectionError_("Google did not send a code (was access denied?).")
    e = _env(env)
    async with (client_factory or _default_client)() as c:
        r = await c.post(GOOGLE_TOKEN_URL, data={"grant_type": "authorization_code", "code": code, "client_id": (e.get("GOOGLE_CLIENT_ID") or "").strip(),
                                                 "client_secret": (e.get("GOOGLE_CLIENT_SECRET") or "").strip(), "redirect_uri": (e.get("GOOGLE_REDIRECT_URI") or "").strip(),
                                                 "code_verifier": pending["verifier"]})
        body = _ok(r, "Google")
        granted = str(body.get("scope") or "")
        if "gmail.readonly" not in granted or "drive.readonly" not in granted:
            raise ConnectionError_("Google connected, but not every permission was ticked (Gmail and Drive). Connect again and tick all of them.")
        await _save_google_tokens(store, pending["user_id"], body)
        try:
            me = _ok(await c.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {body['access_token']}"}), "Google")
            await store.set_secret(pending["user_id"], "google_email", str(me.get("email") or ""))
        except ConnectionError_:
            pass
    return pending["user_id"]


async def _google_access(store, user_id: str, env, c: httpx.AsyncClient) -> str:
    access, expires = await store.get_secret(user_id, "google_access"), await store.get_secret(user_id, "google_expires")
    if access and expires and int(expires) > time.time():
        return access
    refresh = await store.get_secret(user_id, "google_refresh")
    if not refresh:
        raise ConnectionError_("Google is not connected.")
    e = _env(env)
    r = await c.post(GOOGLE_TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": (e.get("GOOGLE_CLIENT_ID") or "").strip(),
                                             "client_secret": (e.get("GOOGLE_CLIENT_SECRET") or "").strip()})
    if r.status_code in (400, 401):
        raise ConnectionError_("Google no longer accepts the saved login (it was revoked, or the app is still in Testing mode, which expires logins after 7 days). Tap Connect Google again.")
    body = _ok(r, "Google")
    await _save_google_tokens(store, user_id, body, keep_refresh=True)
    return body["access_token"]


def _clip_text(v: Any, n: int) -> str:
    t = str(v if v is not None else "").strip()
    return t[:n] + ("…" if len(t) > n else "")


def _mail_args(args: Dict[str, Any]):
    to, subj, body = _clip(args.get("to"), 200), _clip(args.get("subject"), 200), str(args.get("body") or "").strip()[:8000]
    if not re.fullmatch(r"[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+", to):
        raise ConnectionError_("to must be one email address.")
    if not subj or not body:
        raise ConnectionError_("An email needs a subject and a body.")
    return to, subj, body


def _event_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """A calendar event the owner will be shown and must approve: title, start, end, place and notes, checked."""
    title = _clip(args.get("title"), 200)
    if not title:
        raise ConnectionError_("An event needs a title.")
    start = _when(args.get("start"))
    if str(args.get("end") or "").strip():
        end = _when(args.get("end"))
    else:
        try:
            minutes = int(args.get("minutes") or 60)
        except (TypeError, ValueError):
            minutes = 60
        end = start + dt.timedelta(minutes=min(1440, max(5, minutes)))
    if end <= start:
        raise ConnectionError_("The event must end after it starts.")
    return {"title": title, "start": start, "end": end, "location": _clip(args.get("location"), 200), "notes": _clip(args.get("notes"), 1000)}


def _event_label(ev: Dict[str, Any]) -> str:
    from zoneinfo import ZoneInfo

    la = ZoneInfo("America/Los_Angeles")
    s, e = ev["start"].astimezone(la), ev["end"].astimezone(la)
    when = s.strftime("%a %b %d %I:%M %p").replace(" 0", " ") + " to " + e.strftime("%I:%M %p").lstrip("0") + " PT"
    return f'"{ev["title"]}", {when}' + (f' at {ev["location"]}' if ev["location"] else "")


def _header(msg: Dict[str, Any], name: str) -> str:
    for h in (msg.get("payload") or {}).get("headers") or []:
        if str(h.get("name", "")).lower() == name.lower():
            return _clip(h.get("value"), 200)
    return ""


def _mail_text(payload: Dict[str, Any]) -> str:
    """The readable text of a Gmail message: the text/plain part if there is one, else the HTML with tags removed."""
    import base64 as _b64

    def dec(part):
        data = (part.get("body") or {}).get("data") or ""
        try:
            return _b64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace") if data else ""
        except Exception:
            return ""

    def walk(part, want):
        if part.get("mimeType") == want and dec(part):
            return dec(part)
        for sub in part.get("parts") or []:
            hit = walk(sub, want)
            if hit:
                return hit
        return ""
    text = walk(payload, "text/plain")
    if not text:
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", walk(payload, "text/html")))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


async def _google(action: str, args: Dict[str, Any], *, store, user_id: str, env, cf: ClientFactory, mission_id: str = "") -> str:
    async with cf() as c:
        token = await _google_access(store, user_id, env, c)
        h = {"Authorization": f"Bearer {token}"}
        if action == "gmail_search":
            q, n = _clip(args.get("query"), 300), max(1, min(int(args.get("max") or 10), 20)) if str(args.get("max") or "10").isdigit() else 10
            ids = _ok(await c.get(f"{GMAIL_API}/messages", headers=h, params={"q": q, "maxResults": n}), "Gmail").get("messages") or []
            if not ids:
                return f'Gmail: no messages match "{q}".'
            rows = []
            for m in ids[:n]:
                d = _ok(await c.get(f"{GMAIL_API}/messages/{m['id']}", headers=h, params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]}), "Gmail")
                rows.append(f'{m["id"]} | {_header(d, "Date")[:22]} | from {_header(d, "From")} | {_header(d, "Subject")} | {_clip(d.get("snippet"), 120)}')
            return f'Gmail, {len(rows)} match(es) for "{q}" (id | date | from | subject | snippet):\n' + "\n".join(rows)
        if action == "gmail_read":
            mid = _clip(args.get("id"), 60)
            if not re.fullmatch(r"[0-9a-fA-F]{6,40}", mid):
                raise ConnectionError_("id must be a message id from gmail_search.")
            d = _ok(await c.get(f"{GMAIL_API}/messages/{mid}", headers=h, params={"format": "full"}), "Gmail")
            return (f'From: {_header(d, "From")}\nTo: {_header(d, "To")}\nDate: {_header(d, "Date")}\nSubject: {_header(d, "Subject")}\n\n'
                    + _clip_text(_mail_text(d.get("payload") or {}) or d.get("snippet") or "(no text)", 6000))
        if action == "gmail_send":
            to, subj, body = _mail_args(args)
            from email.message import EmailMessage
            import base64 as _b64

            m = EmailMessage()
            m["To"], m["Subject"] = to, subj
            m.set_content(body)
            raw = _b64.urlsafe_b64encode(m.as_bytes()).decode()
            out = _ok(await c.post(f"{GMAIL_API}/messages/send", headers=h, json={"raw": raw}), "Gmail")
            return f'Email sent to {to} (Gmail message {out.get("id")}).'
        if action in ("calendar_list", "calendar_add"):
            return await _calendar(action, args, c, h)
        if action == "drive_search":
            q, n = _clip(args.get("query"), 200).replace("\\", " ").replace("'", " "), max(1, min(int(args.get("max") or 10), 20)) if str(args.get("max") or "10").isdigit() else 10
            files = _ok(await c.get(f"{DRIVE_API}/files", headers=h, params={"q": f"(name contains '{q}' or fullText contains '{q}') and trashed = false", "pageSize": n,
                                                                          "orderBy": "modifiedTime desc", "fields": "files(id,name,mimeType,modifiedTime,size)"}), "Drive").get("files") or []
            if not files:
                return f'Drive: no files match "{q}".'
            return f'Drive, {len(files)} file(s) for "{q}" (id | name | type | modified):\n' + "\n".join(
                f'{f["id"]} | {f["name"]} | {f["mimeType"].split(".")[-1].split("/")[-1]} | {str(f.get("modifiedTime", ""))[:10]}' for f in files)
        if action == "drive_read":
            fid = _clip(args.get("file_id"), 80)
            if not re.fullmatch(r"[A-Za-z0-9_-]{10,80}", fid):
                raise ConnectionError_("file_id must be an id from drive_search.")
            meta = _ok(await c.get(f"{DRIVE_API}/files/{fid}", headers=h, params={"fields": "id,name,mimeType,size"}), "Drive")
            mt = meta.get("mimeType", "")
            if mt == "application/vnd.google-apps.document":
                r = await c.get(f"{DRIVE_API}/files/{fid}/export", headers=h, params={"mimeType": "text/plain"})
            elif mt == "application/vnd.google-apps.spreadsheet":
                r = await c.get(f"{DRIVE_API}/files/{fid}/export", headers=h, params={"mimeType": "text/csv"})
            elif mt.startswith("text/") or mt in ("application/json", "text/markdown"):
                r = await c.get(f"{DRIVE_API}/files/{fid}", headers=h, params={"alt": "media"})
            else:
                raise ConnectionError_(f'"{meta.get("name")}" is a {mt.split("/")[-1]} file, not text. I can read Google Docs, Sheets and text files.')
            if r.status_code >= 400:
                raise ConnectionError_(f"Drive refused the file ({r.status_code}).")
            return f'{meta.get("name")}:\n' + _clip_text(r.text, 8000)
        if action == "drive_save":
            fname = _clip(args.get("file"), 120)
            full = await _find_output(store, user_id, mission_id, fname)
            if not full:
                raise ConnectionError_(f"{fname or 'That file'} is not in any of your missions' outboxes. Check the file name.")
            content = full.get("content") or ""
            if content.startswith("base64:"):
                import base64 as _b64

                data, mime = _b64.b64decode(content[7:]), ("application/pdf" if fname.lower().endswith(".pdf") else "application/octet-stream")
            else:
                data, mime = content.encode("utf-8"), "text/plain"
            name = re.sub(r"[^A-Za-z0-9 ._()-]", "", _clip(args.get("name"), 100)) or fname
            fl = _ok(await c.get(f"{DRIVE_API}/files", headers=h, params={"q": "name = 'Aibussol' and mimeType = 'application/vnd.google-apps.folder' and trashed = false", "fields": "files(id)"}), "Drive").get("files") or []
            folder = fl[0]["id"] if fl else _ok(await c.post(f"{DRIVE_API}/files", headers=h, json={"name": "Aibussol", "mimeType": "application/vnd.google-apps.folder"}), "Drive")["id"]
            boundary = "aibussol" + secrets.token_hex(8)
            meta = json.dumps({"name": name, "parents": [folder]}).encode()
            body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n").encode() + meta + (f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{boundary}--".encode()
            out = _ok(await c.post(DRIVE_UPLOAD, headers={**h, "Content-Type": f"multipart/related; boundary={boundary}"}, params={"uploadType": "multipart", "fields": "id,name,webViewLink"}, content=body), "Drive")
            return f'Saved "{out.get("name")}" ({len(data) // 1024} KB) to your Drive, in the Aibussol folder: {out.get("webViewLink") or out.get("id")}'
    raise ConnectionError_("Unsupported Google action.")


async def _calendar(action: str, args: Dict[str, Any], c: httpx.AsyncClient, h: Dict[str, str]) -> str:
    from zoneinfo import ZoneInfo

    def need_reconnect(r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise ConnectionError_("Google has not given Aibussol the Calendar permission yet. On the Station, Disconnect Google and Connect it again, and tick Calendar. "
                                   "(Also check the Google Calendar API is switched on in the Aibussol Cloud project.)")

    la = ZoneInfo("America/Los_Angeles")
    if action == "calendar_list":
        try:
            days = min(31, max(1, int(args.get("days") or 7)))
        except (TypeError, ValueError):
            days = 7
        now = dt.datetime.now(dt.timezone.utc)
        r = await c.get(CALENDAR_API, headers=h, params={"timeMin": now.isoformat(), "timeMax": (now + dt.timedelta(days=days)).isoformat(), "singleEvents": "true",
                                                         "orderBy": "startTime", "maxResults": 25})
        need_reconnect(r)
        items = _ok(r, "Google Calendar").get("items") or []
        if not items:
            return f"Google Calendar: nothing scheduled in the next {days} day(s)."
        rows = []
        for it in items:
            st, en = it.get("start") or {}, it.get("end") or {}
            if st.get("dateTime"):
                a = dt.datetime.fromisoformat(st["dateTime"].replace("Z", "+00:00")).astimezone(la)
                when = a.strftime("%a %b %d %I:%M %p").replace(" 0", " ") + " PT"
            else:
                when = f'{st.get("date", "?")} (all day)'
            rows.append(f'{when} | {_clip(it.get("summary") or "(no title)", 120)}' + (f' | {_clip(it.get("location"), 80)}' if it.get("location") else ""))
        return f"Google Calendar, next {days} day(s) (when | title | place):\n" + "\n".join(rows)
    ev = _event_args(args)
    body = {"summary": ev["title"], "start": {"dateTime": ev["start"].isoformat()}, "end": {"dateTime": ev["end"].isoformat()}}
    if ev["location"]:
        body["location"] = ev["location"]
    if ev["notes"]:
        body["description"] = ev["notes"]
    r = await c.post(CALENDAR_API, headers=h, json=body)
    need_reconnect(r)
    out = _ok(r, "Google Calendar")
    return f'Added to your Google Calendar: {_event_label(ev)}.' + (f' {out["htmlLink"]}' if out.get("htmlLink") else "")


async def marnyx_awaiting(env, cf: "ClientFactory") -> List[Dict[str, Any]]:
    """[{id, name, awaiting}] for every Marnyx brand: how many held drafts wait for the owner's approval."""
    base, h = marnyx_url(env), {"Authorization": f"Bearer {marnyx_key(env)}", "Accept": "application/json"}
    out: List[Dict[str, Any]] = []
    async with cf() as c:
        r = await c.get(f"{base}/businesses", headers=h)
        if r.status_code >= 400:
            raise ConnectionError_(f"Marnyx answered {r.status_code}.")
        for b in r.json().get("businesses") or []:
            a = await c.get(f"{base}/businesses/{b['id']}/posts/awaiting", headers=h)
            n = int(a.json().get("awaiting", 0)) if a.status_code < 400 else 0
            out.append({"id": b["id"], "name": b["name"], "awaiting": n})
    return out


# ── running an action ────────────────────────────────────────────────────────────────────────────

ClientFactory = Callable[[], httpx.AsyncClient]


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30)


async def run(platform: str, action: str, args: Dict[str, Any], *, store, user_id: str, mission_id: str = "",
              env=None, client_factory: Optional[ClientFactory] = None, media_fetch=None) -> str:
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
        return await _etsy(action, args, store=store, user_id=user_id, env=env, cf=cf, mission_id=mission_id, media_fetch=media_fetch)
    if platform == "marnyx":
        return await _marnyx(action, args, env=env, cf=cf)
    if platform == "google":
        return await _google(action, args, store=store, user_id=user_id, env=env, cf=cf, mission_id=mission_id)
    if platform == "google_play":
        return await _play(action, args, env=env, cf=cf)
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
GOOGLE_STATES = OAuthStates()


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


async def _find_output(store, user_id: str, mission_id: str, fname: str) -> Optional[Dict[str, Any]]:
    """The file by name: this mission's outbox first, then the owner's other missions (each product is often made in its own mission)."""
    if store is None or not fname:
        return None
    ids = ([mission_id] if mission_id else []) + [m["id"] for m in await store.list_missions(user_id, limit=50) if m["id"] != mission_id]
    for mid in ids:
        hit = next((o for o in await store.list_outputs(mid) if o["name"] == fname), None)
        if hit:
            return await store.get_output(hit["id"])
    return None


async def _design_pack(store, user_id: str, mission_id: str, pack_name: str, media_fetch, title: str):
    """One zip for a listing: color/ and bw/ folders with the PNG and JPG images, and the SVG and DXF cut files."""
    import json

    from openjarvis.server.crew import designs

    if not pack_name.endswith(".pack.json"):
        pack_name = re.sub(r"\.\w+$", "", pack_name) + ".pack.json"
    manifest = await _find_output(store, user_id, mission_id, pack_name)
    if not manifest:
        raise ConnectionError_(f"{pack_name} is not in any of your missions' outboxes. Make the design pack first.")
    try:
        pack = json.loads(manifest.get("content") or "{}")
    except ValueError:
        raise ConnectionError_(f"{pack_name} is not readable.")
    if media_fetch is None:
        raise ConnectionError_("The media library is not available to attach the pack.")
    base = re.sub(r"[^a-z0-9-]", "", str(pack.get("name") or "design"))[:40] or "design"
    files: Dict[str, bytes] = {}
    for key, path in (("color_png", f"color/{base}-color.png"), ("color_jpg", f"color/{base}-color.jpg"),
                      ("bw_png", f"bw/{base}-bw.png"), ("bw_jpg", f"bw/{base}-bw.jpg")):
        if not pack.get(key):
            raise ConnectionError_(f"{pack_name} is missing {key}.")
        data, _mime = await media_fetch(str(pack[key]))
        files[path] = data
    for key, path in (("svg", f"bw/{base}.svg"), ("dxf", f"bw/{base}.dxf")):
        out = await _find_output(store, user_id, mission_id, str(pack.get(key) or ""))
        if not out:
            raise ConnectionError_(f"{pack.get(key)} is not in any of your missions' outboxes.")
        files[path] = (out.get("content") or "").encode("utf-8")
    shown = re.sub(r"[^A-Za-z0-9 ._-]", "", _clip(title, 60)) or base
    shown = (re.sub(r"\.zip$", "", shown, flags=re.I) or base) + ".zip"
    return shown, "application/zip", designs.pack_zip(files)


def _listing_ids(args: Dict[str, Any]) -> List[int]:
    raw = args.get("listing_ids") if isinstance(args.get("listing_ids"), list) else str(args.get("listing_ids") or "").split(",")
    ids = [int(str(x).strip()) for x in raw if str(x).strip().isdigit()]
    if not ids:
        raise ConnectionError_("listing_ids must list the Etsy listing ids to publish.")
    if len(ids) > 30:
        raise ConnectionError_("Up to 30 listings at a time.")
    return ids


def _listing_id(args: Dict[str, Any]) -> int:
    v = str(args.get("listing_id") or "").strip()
    if not v.isdigit():
        raise ConnectionError_("listing_id must be the Etsy listing id (a number) from create_draft_listing.")
    return int(v)


async def etsy_shop_id(store, user_id: str, env, c) -> str:
    """The connected shop's id. When none was found at connect time, ask Etsy again (the owner may have opened the shop since),
    so no reconnect is needed."""
    shop_id = await store.get_secret(user_id, "etsy_shop_id")
    if shop_id:
        return shop_id
    token = await _etsy_access(store, user_id, env, c)
    etsy_user = token.split(".", 1)[0]
    try:
        body = _ok(await c.get(f"{ETSY_API}/users/{etsy_user}/shops", headers={"x-api-key": _etsy_key_header(env), "Authorization": f"Bearer {token}"}), "Etsy")
    except ConnectionError_ as exc:
        raise ConnectionError_(f"No Etsy shop yet on the connected account ({exc}). Finish opening the shop at etsy.com/sell; the crew finds it automatically.")
    shop_id = str(body.get("shop_id") or ((body.get("results") or [{}])[0].get("shop_id")) or "")
    if not shop_id:
        raise ConnectionError_("No Etsy shop yet on the connected account. Finish opening the shop at etsy.com/sell; the crew finds it automatically.")
    await store.set_secret(user_id, "etsy_shop_id", shop_id)
    await store.delete_secret(user_id, "etsy_shop_note")
    return shop_id


def _money(m: Any) -> float:
    try:
        return int(m.get("amount")) / max(1, int(m.get("divisor") or 100))
    except (AttributeError, TypeError, ValueError):
        return 0.0


async def _etsy_sales(c: httpx.AsyncClient, shop_id: str, h: Dict[str, str], args: Dict[str, Any]) -> str:
    """Orders and revenue over the last N days from the shop's receipts. Etsy's own fees are not in this number."""
    try:
        days = max(1, min(int(args.get("days") or 30), 90))
    except (TypeError, ValueError):
        days = 30
    since = int(time.time()) - days * 86400
    rows: List[Dict[str, Any]] = []
    for page in range(5):                                                       # up to 500 orders
        r = await c.get(f"{ETSY_API}/shops/{shop_id}/receipts", headers=h, params={"min_created": since, "limit": 100, "offset": page * 100})
        if r.status_code in (401, 403):
            raise ConnectionError_("Etsy has not allowed reading orders yet. In the Station, Disconnect Etsy and Connect it again, and approve the sales (transactions) permission.")
        body = _ok(r, "Etsy")
        batch = body.get("results") or []
        rows += batch
        if len(batch) < 100:
            break
    paid = [x for x in rows if x.get("was_paid", True)]
    if not paid:
        return f"Etsy: no paid orders in the last {days} days."
    cur = ((paid[0].get("grand_total") or {}).get("currency_code")) or "USD"
    gross = sum(_money(x.get("grand_total")) for x in paid)
    items = sum(_money(x.get("subtotal")) for x in paid)
    recent = sorted(paid, key=lambda x: x.get("create_timestamp") or 0, reverse=True)[:5]
    lines = [f'{time.strftime("%b %d", time.gmtime(x.get("create_timestamp") or 0))}: {_money(x.get("grand_total")):.2f} {cur}' for x in recent]
    return (f"Etsy, last {days} days: {len(paid)} paid order(s), {gross:.2f} {cur} total paid by buyers ({items:.2f} for the items). "
            "Etsy's fees and payment costs are not taken out of these numbers. Latest orders: " + "; ".join(lines) + ".")


async def _etsy(action: str, args: Dict[str, Any], *, store, user_id: str, env, cf: ClientFactory, mission_id: str = "", media_fetch=None) -> str:
    async with cf() as c:
        shop_id = await etsy_shop_id(store, user_id, env, c)
        token = await _etsy_access(store, user_id, env, c)
        h = {"x-api-key": _etsy_key_header(env), "Authorization": f"Bearer {token}"}
        if action == "get_shop":
            b = _ok(await c.get(f"{ETSY_API}/shops/{shop_id}", headers=h), "Etsy")
            return f'Etsy shop "{b.get("shop_name")}": {b.get("listing_active_count", 0)} active listings, {b.get("digital_listing_count", 0)} digital.'
        if action == "get_sales":
            return await _etsy_sales(c, shop_id, h, args)
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
            tags = [_clip(t, 20) for t in (args.get("tags") or [])][:13] if isinstance(args.get("tags"), list) else []
            if tags:
                form["tags"] = tags
            if form["type"] == "physical":
                spid = _clip(args.get("shipping_profile_id"), 30)
                if not spid.isdigit():
                    raise ConnectionError_("Physical listings need shipping_profile_id (a shipping profile from the Etsy shop). Digital (download) listings do not.")
                form["shipping_profile_id"] = spid
            b = _ok(await c.post(f"{ETSY_API}/shops/{shop_id}/listings", headers=h, data=form), "Etsy")
            return f'Etsy draft listing created: {b.get("listing_id")} "{title}" (draft: not visible to buyers until it is published).'
        if action == "add_listing_image":
            lid, item = _listing_id(args), _clip(args.get("media_item_id"), 80)
            if media_fetch is None or not item:
                raise ConnectionError_("Give media_item_id: the media library item id from generate_image.")
            data, mime = await media_fetch(item)
            ext = "png" if "png" in mime else "jpg"
            files = {"image": (f"photo.{ext}", data, mime)}
            form = {"rank": str(max(1, min(int(args.get("rank") or 1), 10)))} if str(args.get("rank") or "").isdigit() else {}
            b = _ok(await c.post(f"{ETSY_API}/shops/{shop_id}/listings/{lid}/images", headers=h, files=files, data=form), "Etsy")
            return f"Photo added to Etsy draft {lid} (image {b.get('listing_image_id')})."
        if action == "add_listing_file":
            lid = _listing_id(args)
            if str(args.get("pack") or "").strip():
                shown, mime, data = await _design_pack(store, user_id, mission_id, _clip(args.get("pack"), 120), media_fetch,
                                                       _clip(args.get("name"), 60))
            else:
                fname = _clip(args.get("file"), 120)
                full = await _find_output(store, user_id, mission_id, fname)
                if not full:
                    raise ConnectionError_(f"{fname or 'That file'} is not in any of your missions' outboxes. Check the file name.")
                content = full.get("content") or ""
                if content.startswith("%PDF") or re.search(r"\.(svg|dxf|json|csv|pdf)$", fname, re.I):
                    shown = re.sub(r"[^A-Za-z0-9 ._-]", "", _clip(args.get("name"), 60)) or fname
                    mime = "application/pdf" if fname.lower().endswith(".pdf") or content.startswith("%PDF") else "application/octet-stream"
                    data = content.encode("latin-1", "replace") if mime == "application/pdf" else content.encode("utf-8")
                else:
                    # Written notes (markdown or text) become a PDF product guide
                    shown = re.sub(r"[^A-Za-z0-9 ._-]", "", _clip(args.get("name"), 60)) or re.sub(r"\.(md|txt)$", "", fname) + ".pdf"
                    from openjarvis.server.crew.pdf import make_pdf

                    data = make_pdf(re.sub(r"\.(md|txt|pdf)$", "", shown).replace("_", " "), content)
                    shown = re.sub(r"\.\w+$", "", shown) + ".pdf"
                    mime = "application/pdf"
            b = _ok(await c.post(f"{ETSY_API}/shops/{shop_id}/listings/{lid}/files", headers=h,
                                 files={"file": (shown, data, mime)}, data={"name": shown}), "Etsy")
            return f'Download file "{shown}" ({len(data) // 1024} KB) attached to Etsy draft {lid} (file {b.get("listing_file_id")}).'
        if action == "publish_listings":
            done, bad = [], []
            for lid in _listing_ids(args):
                r = await c.patch(f"{ETSY_API}/shops/{shop_id}/listings/{lid}", headers=h, data={"state": "active"})
                try:
                    _ok(r, "Etsy")
                    done.append(str(lid))
                except ConnectionError_ as exc:
                    bad.append(f"{lid}: {exc}")
            return (f"Published on Etsy: {', '.join(done) or 'none'}." + (" Not published: " + "; ".join(bad) if bad else "")
                    + (" Live at " + ", ".join(f"https://www.etsy.com/listing/{i}" for i in done) if done else ""))
    raise ConnectionError_("Unsupported Etsy action.")
