"""Each account's own crew: what a subscriber's crew may see and use, kept apart from the owner's.

The owner's crew runs on the server's environment (his Stripe, GitHub, Render, databases, phone push, house rules). A subscriber's crew
NEVER sees those: env_for() builds its environment from a short allow-list of platform settings (limits, the research service, the
Etsy app) plus the subscriber's OWN connections, which they add through the Station's guided Connect steps:

  stripe_key    a Stripe restricted key from THEIR account (read + products/prices/payment links)   -> STRIPE_SECRET_KEY
  github_token  a fine-grained token on THEIR repos, and github_repos (their chosen owner/repo list) -> CREW_GITHUB_TOKEN / _REPOS
  notify_url    THEIR ntfy topic for phone alerts                                                     (used by the Notifier)
  house_rules   THEIR standing instructions for the crew                                              -> CREW_HOUSE_RULES
Etsy stays OAuth per account (connections.py), and the Workbench pairs per account (workbench.py).

Keys are stored encrypted (Fernet; key from CREW_SECRET_KEY, else derived from JWT_SECRET_KEY). Businesses (the factories on a
subscriber's Station) are a small JSON list per account.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional

PLATFORM_KEYS = (
    "CREW_ENABLED", "CREW_DAILY_CAP_USD", "CREW_MISSION_BUDGET_USD", "CREW_MAX_MISSION_BUDGET_USD", "CREW_MAX_CONCURRENT",
    "CREW_MAX_TASKS", "CREW_TICK_SECONDS", "CREW_QUIET_HOURS", "TAVILY_API_KEY", "OPENAI_API_KEY",
    "ETSY_API_KEY", "ETSY_SHARED_SECRET", "ETSY_REDIRECT_URI", "GPTR_FAST_LLM", "GPTR_SMART_LLM", "GPTR_STRATEGIC_LLM",
)
SECRET_ENV = {"stripe_key": "STRIPE_SECRET_KEY", "github_token": "CREW_GITHUB_TOKEN", "github_repos": "CREW_GITHUB_REPOS", "render_key": "RENDER_API_KEY",
              "house_rules": "CREW_HOUSE_RULES", "marnyx_key": "MARNYX_API_KEY"}
ENCRYPTED = ("stripe_key", "github_token", "notify_url", "marnyx_key", "render_key")
GENERIC_RULES = (
    "- Work only for this account and only inside the accounts and repos they connected. Never assume access you were not given.\n"
    "- Games: built in Unity (mobile) or Unreal (open world) on their own PC by Rig through the Workbench, never as a web prototype "
    "unless they ask; if their PC is not connected, Rig's task waits.\n"
    "- Anything public, paid or permanent waits for their Approve."
)
MAX_BUSINESSES = 6
PALETTE = ["#ff6d00", "#b06cff", "#ff3344", "#00e5ff", "#34d17a", "#ffd23f"]


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _fernet(env=None):
    from cryptography.fernet import Fernet

    e = _env(env)
    raw = (e.get("CREW_SECRET_KEY") or "").strip() or ("crew:" + (e.get("JWT_SECRET_KEY") or "aibussol-dev"))
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest()))


def seal(value: str, env=None) -> str:
    return "enc1:" + _fernet(env).encrypt(value.encode("utf-8")).decode("ascii")


def unseal(value: Optional[str], env=None) -> Optional[str]:
    if not value:
        return value
    if not value.startswith("enc1:"):
        return value
    try:
        return _fernet(env).decrypt(value[5:].encode("ascii")).decode("utf-8")
    except Exception:
        return None


async def get(store, user_id: str, name: str, env=None) -> Optional[str]:
    v = await store.get_secret(user_id, name)
    return unseal(v, env) if name in ENCRYPTED else v


async def put(store, user_id: str, name: str, value: str, env=None) -> None:
    await store.set_secret(user_id, name, seal(value, env) if name in ENCRYPTED else value)


async def subscriber_env(store, user_id: str, base: Optional[Mapping[str, str]] = None, name: str = "") -> Dict[str, str]:
    """The environment a subscriber's crew runs with: platform settings plus their own connections, nothing of the owner's."""
    b = _env(base)
    out = {k: b[k] for k in PLATFORM_KEYS if b.get(k)}
    for secret, key in SECRET_ENV.items():
        v = await get(store, user_id, secret, base)
        out[key] = v or ""                                  # an empty value means "not connected", never "use the server's"
    out["CREW_HOUSE_RULES"] = out.get("CREW_HOUSE_RULES") or GENERIC_RULES
    out["CREW_PERSON"] = name or "the account owner"
    out["CREW_BUSINESSES"] = ", ".join(b["name"] for b in await businesses(store, user_id))
    return out


# ── businesses: the factories on a subscriber's Station ───────────────────────────────────────

def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())[:24] or "business"


async def businesses(store, user_id: str) -> List[Dict[str, Any]]:
    raw = await store.get_secret(user_id, "businesses")
    try:
        items = json.loads(raw) if raw else []
    except ValueError:
        items = []
    return [b for b in items if isinstance(b, dict) and b.get("key")][:MAX_BUSINESSES]


async def add_business(store, user_id: str, name: str, url: str = "", color: str = "") -> List[Dict[str, Any]]:
    name = " ".join(str(name or "").split())[:40]
    if len(name) < 2:
        raise ValueError("Give the business a name.")
    url = str(url or "").strip()[:200]
    if url and not url.startswith("https://"):
        url = "https://" + url.removeprefix("http://")
    items = await businesses(store, user_id)
    if len(items) >= MAX_BUSINESSES:
        raise ValueError(f"Up to {MAX_BUSINESSES} businesses for now.")
    key = slug(name)
    if any(b["key"] == key for b in items):
        raise ValueError("You already have a business with that name.")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color or ""):
        color = PALETTE[len(items) % len(PALETTE)]
    items.append({"key": key, "name": name, "url": url, "color": color})
    await store.set_secret(user_id, "businesses", json.dumps(items))
    return items


async def remove_business(store, user_id: str, key: str) -> List[Dict[str, Any]]:
    items = [b for b in await businesses(store, user_id) if b["key"] != key]
    await store.set_secret(user_id, "businesses", json.dumps(items))
    return items
