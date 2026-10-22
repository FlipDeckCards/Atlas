"""Guided connections for subscribers: step-by-step instructions, then the key is TESTED against the real service before it is saved.

Every connection uses the service's official, documented way for a person to give an app access to their own account:
  stripe  a restricted API key from their Stripe dashboard (they pick exactly what the crew may do)
  github  a fine-grained personal access token limited to the repos they choose
  phone   an ntfy.sh topic URL (free app) for alerts and briefs
  rules   their standing instructions for the crew (plain text)
Etsy (OAuth) and the Workbench (pairing) have their own buttons. Keys are stored encrypted (tenant.py) and never shown again.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

import httpx

from openjarvis.server.crew import tenant

GUIDES: Dict[str, Dict[str, Any]] = {
    "stripe": {
        "label": "Stripe (your payments, products and payment links)",
        "steps": [
            "Open your Stripe Dashboard → Developers → API keys: https://dashboard.stripe.com/apikeys",
            "Click “Create restricted key”. Name it “Aibussol”.",
            "Set these to Write: Products, Prices, Payment Links. Set these to Read: Charges, Customers, Subscriptions, Balance. Leave the rest None.",
            "Click Create key, copy the key that starts with rk_live_ (or rk_test_ to try it first), paste it below, then Test & save.",
        ],
        "field": "Restricted key (rk_…)",
    },
    "github": {
        "label": "GitHub (code for Forge)",
        "steps": [
            "Open https://github.com/settings/personal-access-tokens/new",
            "Name it “Aibussol crew”, pick an expiration, and under Repository access choose “Only select repositories” and pick your repos.",
            "Under Permissions → Repository permissions set Contents and Pull requests to “Read and write” (Metadata is added for you).",
            "Generate the token, paste it below, list the same repos as owner/repo separated by commas, then Test & save.",
        ],
        "field": "Token (github_pat_…)",
        "extra": "Repos, e.g. you/your-site, you/your-app",
    },
    "phone": {
        "label": "Phone alerts and briefs",
        "steps": [
            "Install the free ntfy app: iPhone https://apps.apple.com/app/ntfy/id1625396347 · Android https://play.google.com/store/apps/details?id=io.heckel.ntfy",
            "In the app tap +, and subscribe to a private topic name only you know (use the one suggested below or your own).",
            "Paste https://ntfy.sh/<your-topic> below and tap Test & save: a test alert arrives on your phone.",
        ],
        "field": "https://ntfy.sh/your-private-topic",
    },
    "rules": {
        "label": "Your house rules for the crew",
        "steps": ["Write the standing instructions every agent should follow (your brands, tone, tools you use, what never to do)."],
        "field": "Your rules",
    },
}

ClientFactory = Callable[[], httpx.AsyncClient]


class GuideError(Exception):
    """A failed check; the message is safe to show."""


async def test_and_save(store, user_id: str, kind: str, value: str, extra: str = "", client_factory: Optional[ClientFactory] = None) -> str:
    cf = client_factory or (lambda: httpx.AsyncClient(timeout=15))
    value = str(value or "").strip()
    if kind == "stripe":
        if not re.fullmatch(r"(rk|sk)_(live|test)_[A-Za-z0-9]{10,}", value):
            raise GuideError("That does not look like a Stripe key (it starts with rk_live_ or rk_test_).")
        async with cf() as c:
            r = await c.get("https://api.stripe.com/v1/products", params={"limit": 1}, headers={"Authorization": f"Bearer {value}"})
        if r.status_code in (401, 403):
            raise GuideError("Stripe refused that key, or it cannot read Products. Check step 3 and try again.")
        if r.status_code >= 400:
            raise GuideError(f"Stripe answered {r.status_code}. Try again in a minute.")
        await tenant.put(store, user_id, "stripe_key", value)
        return "Stripe connected" + (" (test mode)" if "_test_" in value else "") + "."
    if kind == "github":
        repos: List[str] = [x.strip() for x in str(extra or "").split(",") if x.strip()]
        if not value or len(value) < 20:
            raise GuideError("Paste the token GitHub showed you.")
        if not repos or not all(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r) for r in repos):
            raise GuideError("List your repos as owner/repo, separated by commas.")
        h = {"Authorization": f"Bearer {value}", "Accept": "application/vnd.github+json"}
        async with cf() as c:
            for r_ in repos[:20]:
                r = await c.get(f"https://api.github.com/repos/{r_}", headers=h)
                if r.status_code != 200:
                    raise GuideError(f"The token cannot open {r_} (GitHub answered {r.status_code}). Add it under Repository access.")
        await tenant.put(store, user_id, "github_token", value)
        await tenant.put(store, user_id, "github_repos", ",".join(repos[:20]))
        return f"GitHub connected for {len(repos[:20])} repo(s)."
    if kind == "phone":
        if not re.fullmatch(r"https://ntfy\.sh/[A-Za-z0-9_-]{8,64}", value):
            raise GuideError("Use https://ntfy.sh/ followed by a private topic of 8 to 64 letters, numbers, - or _.")
        async with cf() as c:
            r = await c.post(value, content="Aibussol is connected. Your crew's alerts and briefs will arrive here.".encode(),
                             headers={"Title": "Aibussol connected"})
        if r.status_code >= 400:
            raise GuideError(f"ntfy answered {r.status_code}. Check the topic and try again.")
        await tenant.put(store, user_id, "notify_url", value)
        return "Phone connected: a test alert was sent."
    if kind == "rules":
        if len(value) > 2000:
            raise GuideError("Keep the rules under 2,000 characters.")
        await tenant.put(store, user_id, "house_rules", value)
        return "Rules saved." if value else "Rules cleared."
    raise GuideError("Unknown connection.")


async def disconnect(store, user_id: str, kind: str) -> None:
    names = {"stripe": ["stripe_key"], "github": ["github_token", "github_repos"], "phone": ["notify_url"], "rules": ["house_rules"]}.get(kind)
    if not names:
        raise GuideError("Unknown connection.")
    for n in names:
        await store.delete_secret(user_id, n)
