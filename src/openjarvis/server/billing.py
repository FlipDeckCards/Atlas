"""Aibussol subscription billing: a monthly Stripe subscription, checked by webhook.

Stripe is the source of truth. The webhook (POST /api/stripe/webhook) is the only route outside the login,
and it accepts a request only when the Stripe-Signature header matches STRIPE_WEBHOOK_SECRET.
Subscriptions are written to the `subscriptions` table; events are stored in `stripe_events` so a repeated
delivery changes nothing. Only events whose metadata says app=aibussol are used, because the Stripe account
is shared with FlipDeck and Marnyx."""

import hashlib
import hmac
import json
import logging
import os
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()

APP = "aibussol"
STRIPE_API = "https://api.stripe.com/v1"
ACTIVE = ("active", "trialing")

BILLING_DDL = """
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id                 TEXT PRIMARY KEY,
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT UNIQUE,
    status                  TEXT NOT NULL,
    current_period_end      TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS stripe_events (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def config(env=None) -> dict:
    e = os.environ if env is None else env
    return {
        "secret_key": (e.get("STRIPE_SECRET_KEY") or "").strip(),
        "webhook_secret": (e.get("STRIPE_WEBHOOK_SECRET") or "").strip(),
        "price": (e.get("STRIPE_PRICE_AIBUSSOL") or "").strip(),
    }


def enabled(env=None) -> bool:
    c = config(env)
    return bool(c["secret_key"] and c["webhook_secret"] and c["price"])


def verify_signature(payload: bytes, header: str, secret: str, now=None, tolerance: int = 300) -> bool:
    """Stripe's scheme: HMAC-SHA256 of "<timestamp>.<body>" with the endpoint secret; reject stale timestamps."""
    if not header or not secret:
        return False
    pairs = [p.split("=", 1) for p in header.split(",") if "=" in p]
    ts = next((v for k, v in pairs if k == "t"), "")
    sigs = [v for k, v in pairs if k == "v1"]
    if not ts.isdigit() or not sigs:
        return False
    now = time.time() if now is None else now
    if abs(now - int(ts)) > tolerance:
        return False
    expected = hmac.new(secret.encode(), ts.encode() + b"." + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)


async def create_checkout(cfg: dict, user_id: str, email: str, success_url: str, cancel_url: str) -> str:
    form = {
        "mode": "subscription",
        "line_items[0][price]": cfg["price"],
        "line_items[0][quantity]": "1",
        "client_reference_id": user_id,
        "customer_email": email,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata[app]": APP,
        "metadata[user_id]": user_id,
        "subscription_data[metadata][app]": APP,
        "subscription_data[metadata][user_id]": user_id,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{STRIPE_API}/checkout/sessions", data=form, auth=(cfg["secret_key"], ""))
    if r.status_code >= 400:
        logger.error("stripe checkout failed: %s %s", r.status_code, r.text[:300])
        raise RuntimeError("checkout unavailable")
    return r.json()["url"]


def _period_end(sub: dict):
    # Newer Stripe API versions carry the period on each subscription item; older ones on the subscription.
    items = (sub.get("items") or {}).get("data") or []
    ts = sub.get("current_period_end") or (items[0].get("current_period_end") if items else None)
    return ts


async def apply_event(conn, evt: dict) -> str:
    """Record one verified event in one transaction. A repeat delivery changes nothing; a failure rolls back
    the 'seen' mark too, so Stripe's retry is processed rather than skipped."""
    async with conn.transaction():
        inserted = await conn.fetchval(
            "INSERT INTO stripe_events (id, type) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING RETURNING id",
            evt["id"], evt["type"])
        if not inserted:
            return "duplicate"
        return await _apply(conn, evt)


async def _apply(conn, evt: dict) -> str:
    obj = (evt.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    if evt["type"] == "checkout.session.completed":
        user_id = meta.get("user_id") or obj.get("client_reference_id")
        if obj.get("mode") != "subscription" or meta.get("app") != APP or not user_id:
            return "ignored"
        # The subscription.created event that follows carries the real status; this only links the user.
        await conn.execute(
            """INSERT INTO subscriptions (user_id, stripe_customer_id, stripe_subscription_id, status)
               VALUES ($1, $2, $3, 'incomplete')
               ON CONFLICT (user_id) DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id,
                 stripe_subscription_id = EXCLUDED.stripe_subscription_id, updated_at = NOW()""",
            user_id, obj.get("customer"), obj.get("subscription"))
        return "linked"
    if evt["type"].startswith("customer.subscription.") and meta.get("app") == APP and meta.get("user_id"):
        status = "canceled" if evt["type"].endswith(".deleted") else obj.get("status", "incomplete")
        end = _period_end(obj)
        await conn.execute(
            """INSERT INTO subscriptions (user_id, stripe_customer_id, stripe_subscription_id, status, current_period_end)
               VALUES ($1, $2, $3, $4, to_timestamp($5))
               ON CONFLICT (user_id) DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id,
                 stripe_subscription_id = EXCLUDED.stripe_subscription_id, status = EXCLUDED.status,
                 current_period_end = EXCLUDED.current_period_end, updated_at = NOW()""",
            meta.get("user_id"), obj.get("customer"), obj.get("id"), status, end)
        return "updated"
    return "ignored"


async def active_subscribers(pool) -> int:
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT count(*) FROM subscriptions WHERE status = ANY($1)", list(ACTIVE)) or 0)


@router.post("/api/billing/checkout")
async def checkout(request: Request):
    if not enabled():
        return JSONResponse({"detail": "Billing is not set up yet."}, status_code=503)
    user_id = getattr(request.state, "user_id", "") or ""
    email = getattr(request.state, "user_email", "") or ""
    if not user_id or not email:
        return JSONResponse({"detail": "Sign in first."}, status_code=401)
    base = str(request.base_url).rstrip("/")
    try:
        url = await create_checkout(config(), user_id, email, f"{base}/?subscribed=1", f"{base}/?subscribed=0")
    except RuntimeError:
        return JSONResponse({"detail": "Checkout is unavailable right now. Try again shortly."}, status_code=502)
    return {"url": url}


@router.post("/api/stripe/webhook")
async def webhook(request: Request):
    payload = await request.body()
    if not enabled() or not verify_signature(payload, request.headers.get("stripe-signature", ""), config()["webhook_secret"]):
        return JSONResponse({"detail": "Invalid signature."}, status_code=400)
    try:
        evt = json.loads(payload)
    except ValueError:
        return JSONResponse({"detail": "Bad payload."}, status_code=400)
    try:
        async with request.app.state.session_store._pool.acquire() as conn:
            result = await apply_event(conn, evt)
    except Exception:
        logger.exception("stripe event %s failed", evt.get("id"))
        return JSONResponse({"detail": "Could not record event."}, status_code=500)    # Stripe retries on 5xx
    logger.info("stripe event %s %s: %s", evt.get("type"), evt.get("id"), result)
    return {"received": True, "result": result}
