"""A small async client for Polymarket US (the CFTC-regulated US exchange), matching its official SDK (polymarket-us-python):

  public gateway   https://gateway.polymarket.us   markets, books, asset prices (no key)
  trading API      https://api.polymarket.us       orders, positions, balances (signed)

Signing: X-PM-Access-Key (key id), X-PM-Timestamp (ms), X-PM-Signature = base64(Ed25519(timestamp + METHOD + path)); the path has
no query string and the body is not signed. The secret is the base64 Ed25519 key from polymarket.us/developer (a 64-byte value is
seed + public key: the first 32 bytes are used).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from openjarvis.polybot.config import API, GATEWAY

logger = logging.getLogger("polybot.client")


class PMError(Exception):
    def __init__(self, message: str, status: int = 0, body: Any = None) -> None:
        super().__init__(message)
        self.status, self.body = status, body


def auth_headers(key_id: str, secret_key: str, method: str, path: str, now_ms: Optional[int] = None) -> Dict[str, str]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    ts = str(int(time.time() * 1000) if now_ms is None else now_ms)
    raw = base64.b64decode(secret_key)
    if len(raw) == 64:
        raw = raw[:32]
    if len(raw) != 32:
        raise PMError("POLYMARKET_SECRET_KEY is not a base64 Ed25519 key (expected 32 or 64 bytes).")
    sig = Ed25519PrivateKey.from_private_bytes(raw).sign(f"{ts}{method.upper()}{path}".encode())
    return {"X-PM-Access-Key": key_id, "X-PM-Timestamp": ts, "X-PM-Signature": base64.b64encode(sig).decode()}


def money(v: Any) -> Optional[float]:
    """An Amount ({"value": "0.55", "currency": "USD"}), a number or a numeric string -> float."""
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


class PolymarketUS:
    def __init__(self, key_id: str = "", secret_key: str = "", *, http=None, gateway: str = GATEWAY, api: str = API,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        import httpx

        self.key_id, self.secret_key = key_id, secret_key
        self.gateway, self.api = gateway.rstrip("/"), api.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=10.0)
        self.sleep = sleep

    async def close(self) -> None:
        await self.http.aclose()

    async def _req(self, method: str, path: str, *, params=None, body=None, auth: bool = False, tries: int = 3) -> Any:
        base = self.api if auth else self.gateway
        last: Optional[Exception] = None
        for attempt in range(tries):
            headers = {"Content-Type": "application/json", "User-Agent": "aibussol-polybot/1.0"}
            if auth:
                if not (self.key_id and self.secret_key):
                    raise PMError("Polymarket US API keys are not set (POLYMARKET_KEY_ID / POLYMARKET_SECRET_KEY).")
                headers.update(auth_headers(self.key_id, self.secret_key, method, path))
            try:
                r = await self.http.request(method, base + path, params=params, json=body, headers=headers)
            except Exception as exc:                      # timeouts, connection resets
                last = exc
                await self.sleep(0.5 * (2 ** attempt))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = PMError(f"HTTP {r.status_code} on {path}", r.status_code, r.text[:300])
                # a POST that may have reached the exchange is not resent blindly (it could double an order)
                if method == "POST" and path == "/v1/orders":
                    raise last
                await self.sleep(1.0 * (2 ** attempt))
                continue
            if r.status_code >= 400:
                try:
                    detail = r.json()
                except ValueError:
                    detail = r.text[:300]
                raise PMError(f"HTTP {r.status_code} on {path}: {detail}", r.status_code, detail)
            if not r.content:
                return {}
            try:
                return r.json()
            except ValueError:
                return {}
        raise last if isinstance(last, PMError) else PMError(f"{method} {path} failed: {last}")

    # ── public ───────────────────────────────────────────────────────────────────────
    async def markets(self, **params) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        offset, limit = 0, 200
        while True:
            page = await self._req("GET", "/v1/markets", params={**params, "limit": limit, "offset": offset})
            ms = page.get("markets") or []
            out += ms
            if len(ms) < limit or offset > 5000:
                return out
            offset += limit

    async def market(self, slug: str) -> Dict[str, Any]:
        return (await self._req("GET", f"/v1/market/slug/{slug}")).get("market") or {}

    async def book(self, slug: str) -> Dict[str, Any]:
        return (await self._req("GET", f"/v1/markets/{slug}/book")).get("marketData") or {}

    async def index_prices(self, asset: str, start: int, end: int = 0, bucket: str = "ASSET_PRICE_BUCKET_1S") -> List[tuple]:
        """[(unix second, close)] for real prints only (gap-filled candles are dropped)."""
        d = await self._req("GET", "/v1/asset-prices/history", params={"assets": f"crypto:{asset}", "from": int(start), "to": int(end), "bucket": bucket})
        out = []
        for s in d.get("series") or []:
            for c in s.get("candles") or []:
                if int(c.get("tickCount") or 0) > 0 and money(c.get("close")):
                    out.append((int(c["timestamp"]), float(c["close"])))
        return out

    async def settlement(self, slug: str) -> Optional[float]:
        try:
            return money((await self._req("GET", f"/v1/markets/{slug}/settlement")).get("settlement"))
        except PMError:
            return None

    # ── trading (signed) ─────────────────────────────────────────────────────────────
    async def place(self, slug: str, side: str, long_price: float, qty: float) -> str:
        """A post-only, good-till-cancel limit order. side "up" buys Yes, "down" buys No; the price is ALWAYS the long price."""
        body = {"marketSlug": slug, "type": "ORDER_TYPE_LIMIT",
                "intent": "ORDER_INTENT_BUY_LONG" if side == "up" else "ORDER_INTENT_BUY_SHORT",
                "price": {"value": f"{long_price:.2f}", "currency": "USD"}, "quantity": round(qty, 2),
                "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL", "participateDontInitiate": True,
                "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}
        d = await self._req("POST", "/v1/orders", body=body, auth=True, tries=1)
        oid = str(d.get("id") or "")
        if not oid:
            raise PMError(f"No order id returned: {d}")
        return oid

    async def cancel(self, order_id: str, slug: str) -> None:
        await self._req("POST", f"/v1/order/{order_id}/cancel", body={"marketSlug": slug}, auth=True)

    async def cancel_all(self, slugs: Optional[List[str]] = None) -> List[str]:
        d = await self._req("POST", "/v1/orders/open/cancel", body={"slugs": slugs} if slugs else {}, auth=True)
        return list(d.get("canceledOrderIds") or [])

    async def open_orders(self, slugs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return (await self._req("GET", "/v1/orders/open", params={"slugs": slugs} if slugs else None, auth=True)).get("orders") or []

    async def order(self, order_id: str) -> Dict[str, Any]:
        return (await self._req("GET", f"/v1/order/{order_id}", auth=True)).get("order") or {}

    async def positions(self) -> Dict[str, Any]:
        return (await self._req("GET", "/v1/portfolio/positions", auth=True)).get("positions") or {}

    async def buying_power(self) -> Optional[float]:
        d = await self._req("GET", "/v1/account/balances", auth=True)
        for b in d.get("balances") or []:
            if (b.get("currency") or "USD") == "USD":
                return money(b.get("buyingPower"))
        return None

