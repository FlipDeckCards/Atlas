"""Robinhood Crypto (the Robinhood Crypto Trading API): connection, request signing and a read-only check.

Read-only for now: nothing here places orders. Each request is signed with the Ed25519 private key:
  message = api_key + timestamp + path + METHOD + body   (body is empty for GET)
and sent with x-api-key, x-timestamp and x-signature (base64). The public key is stored with the others but is not needed to sign.
The signing scheme follows the public Robinhood client libraries; check it against Robinhood's API docs before enabling trading.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

DEFAULT_URL = "https://trading.robinhood.com"
ACCOUNTS_PATH = "/api/v1/crypto/trading/accounts/"
SETUP = ("Create an API key in Robinhood (web classic, crypto account settings, choose the actions the key may perform), then set on "
         "Render (Atlas service): ROBINHOOD_API_KEY, ROBINHOOD_PRIVATE_KEY (base64, 32 bytes), ROBINHOOD_PUBLIC_KEY, and optionally "
         "ROBINHOOD_API_URL. The Station only checks the key can read your crypto accounts; it never shows the keys again.")


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _get(env: Optional[Mapping[str, str]], name: str) -> str:
    return (_env(env).get(name) or "").strip()


def configured(env: Optional[Mapping[str, str]] = None) -> bool:
    return bool(_get(env, "ROBINHOOD_API_KEY") and _get(env, "ROBINHOOD_PRIVATE_KEY"))


def base_url(env: Optional[Mapping[str, str]] = None) -> str:
    return (_get(env, "ROBINHOOD_API_URL") or DEFAULT_URL).rstrip("/")


def sign_headers(method: str, path: str, body: str = "", env: Optional[Mapping[str, str]] = None,
                 ts: Optional[int] = None) -> Dict[str, str]:
    """The three headers for one request. Raises ValueError if the private key is malformed (the message never includes the key)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    api_key = _get(env, "ROBINHOOD_API_KEY")
    try:
        raw = base64.b64decode(_get(env, "ROBINHOOD_PRIVATE_KEY"), validate=True)
    except Exception as e:
        raise ValueError("ROBINHOOD_PRIVATE_KEY is not valid base64") from e
    if len(raw) != 32:
        raise ValueError("ROBINHOOD_PRIVATE_KEY must decode to 32 bytes")
    stamp = str(int(time.time()) if ts is None else int(ts))
    message = f"{api_key}{stamp}{path}{method.upper()}{body}".encode()
    signature = Ed25519PrivateKey.from_private_bytes(raw).sign(message)
    return {"x-api-key": api_key, "x-timestamp": stamp, "x-signature": base64.b64encode(signature).decode()}


async def check(env: Optional[Mapping[str, str]] = None,
                client_factory: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """Read-only: can the key list the crypto accounts? Returns {'ok': bool, 'detail': str}; never includes a key."""
    import httpx

    if not configured(env):
        return {"ok": False, "detail": "not configured"}
    try:
        headers = sign_headers("GET", ACCOUNTS_PATH, "", env)
    except ValueError as e:
        return {"ok": False, "detail": str(e)}
    make = client_factory or (lambda: httpx.AsyncClient(timeout=20))
    try:
        async with make() as client:
            resp = await client.get(base_url(env) + ACCOUNTS_PATH, headers=headers)
        return {"ok": resp.status_code == 200, "detail": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "detail": f"request failed: {type(e).__name__}"}


# ── 2026-11-20 (owner): the full client, ready for a strategy ────────────────────────────────────────────────────────────
# Endpoints and fields checked against the published Robinhood Crypto Trading API (v1, non-fee-tier orders) and two public
# client libraries: base https://trading.robinhood.com; signed headers x-api-key / x-timestamp / x-signature (Ed25519 over
# api_key + timestamp + path + METHOD + body, the timestamp good for 30 s); about 100 requests a minute per account.
# Robinhood's v1 crypto orders carry no separate fee: the spread is in the execution price, so P&L from executions is complete.
BEST_BID_ASK_PATH = "/api/v1/crypto/marketdata/best_bid_ask/"
ESTIMATED_PRICE_PATH = "/api/v1/crypto/marketdata/estimated_price/"
TRADING_PAIRS_PATH = "/api/v1/crypto/trading/trading_pairs/"
HOLDINGS_PATH = "/api/v1/crypto/trading/holdings/"
ORDERS_PATH = "/api/v1/crypto/trading/orders/"
ORDER_STATES_DONE = ("filled", "canceled", "failed")


class RobinhoodError(Exception):
    def __init__(self, msg: str, status: int = 0) -> None:
        super().__init__(msg)
        self.status = status


class _Bucket:
    """Token bucket: about 100 requests a minute (Robinhood's limit), with a small burst."""

    def __init__(self, per_min: float = 90.0, burst: float = 10.0) -> None:
        import asyncio as _a
        self.rate, self.cap, self.tokens, self.ts = per_min / 60.0, burst, burst, time.monotonic()
        self._lock = _a.Lock()

    async def take(self) -> None:
        import asyncio as _a
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.cap, self.tokens + (now - self.ts) * self.rate)
                self.ts = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await _a.sleep((1 - self.tokens) / self.rate)


class RHClient:
    """Signed calls to Robinhood Crypto. Placing and cancelling orders live here; the engine decides whether they may run."""

    def __init__(self, env: Optional[Mapping[str, str]] = None, http: Any = None) -> None:
        self.env = env
        self.http = http                      # an httpx.AsyncClient-like object (tests pass a fake)
        self.bucket = _Bucket()

    async def _req(self, method: str, path: str, params: Optional[list] = None, body: Optional[dict] = None) -> Any:
        import json as _json
        from urllib.parse import urlencode

        import httpx

        if not configured(self.env):
            raise RobinhoodError("Robinhood is not connected (no API key)")
        q = ("?" + urlencode(params, doseq=True)) if params else ""
        text = _json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = sign_headers(method, path + q, text, self.env)
        headers["Content-Type"] = "application/json; charset=utf-8"
        await self.bucket.take()
        http = self.http or httpx.AsyncClient(timeout=20)
        try:
            resp = await http.request(method, base_url(self.env) + path + q, headers=headers, content=text or None)
        finally:
            if self.http is None:
                await http.aclose()
        if resp.status_code >= 400:
            raise RobinhoodError(f"HTTP {resp.status_code} on {method} {path}: {resp.text[:200]}", resp.status_code)
        try:
            return resp.json()
        except Exception:
            return resp.text

    async def account(self) -> Dict[str, Any]:
        d = await self._req("GET", ACCOUNTS_PATH)
        return d if isinstance(d, dict) else {}

    async def holdings(self) -> List[Dict[str, Any]]:
        d = await self._req("GET", HOLDINGS_PATH)
        return list((d or {}).get("results") or []) if isinstance(d, dict) else []

    async def best_bid_ask(self, symbols: List[str]) -> Dict[str, Dict[str, float]]:
        """symbol -> {bid, ask, mid}: bid and ask include Robinhood's spread (what a sell or buy would really get)."""
        d = await self._req("GET", BEST_BID_ASK_PATH, params=[("symbol", s) for s in symbols])
        out = {}
        for r in (d or {}).get("results") or []:
            try:
                out[r["symbol"]] = {"bid": float(r.get("bid_inclusive_of_sell_spread") or r.get("price")),
                                    "ask": float(r.get("ask_inclusive_of_buy_spread") or r.get("price")),
                                    "mid": float(r.get("price"))}
            except (KeyError, TypeError, ValueError):
                continue
        return out

    async def trading_pairs(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        d = await self._req("GET", TRADING_PAIRS_PATH, params=[("symbol", s) for s in symbols])
        return {r["symbol"]: r for r in (d or {}).get("results") or [] if r.get("symbol")}

    async def place_order(self, symbol: str, side: str, *, client_order_id: str, quote_amount: Optional[float] = None,
                          asset_quantity: Optional[float] = None, limit_price: Optional[float] = None) -> Dict[str, Any]:
        """A market order (limit_price None) or a good-for-day limit order ("gfd"). Exactly one of quote_amount / asset_quantity.
        Market orders take an asset quantity only; a dollar amount is turned into one by the engine."""
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        if limit_price is None:
            if asset_quantity is None:
                raise ValueError("a market order needs an asset quantity")
            body = {"symbol": symbol, "client_order_id": client_order_id, "side": side, "type": "market",
                    "market_order_config": {"asset_quantity": _num(asset_quantity)}}
        else:
            cfg: Dict[str, Any] = {"limit_price": _num(limit_price), "time_in_force": "gfd"}
            if asset_quantity is not None:
                cfg["asset_quantity"] = _num(asset_quantity)
            elif quote_amount is not None:
                cfg["quote_amount"] = _num(quote_amount)
            else:
                raise ValueError("a limit order needs an asset quantity or a dollar amount")
            body = {"symbol": symbol, "client_order_id": client_order_id, "side": side, "type": "limit", "limit_order_config": cfg}
        d = await self._req("POST", ORDERS_PATH, body=body)
        return d if isinstance(d, dict) else {}

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        d = await self._req("GET", f"{ORDERS_PATH}{order_id}/")
        return d if isinstance(d, dict) else {}

    async def open_orders(self) -> List[Dict[str, Any]]:
        d = await self._req("GET", ORDERS_PATH, params=[("state", "open")])
        out = list((d or {}).get("results") or []) if isinstance(d, dict) else []
        d2 = await self._req("GET", ORDERS_PATH, params=[("state", "partially_filled")])
        return out + (list((d2 or {}).get("results") or []) if isinstance(d2, dict) else [])

    async def cancel_order(self, order_id: str) -> None:
        await self._req("POST", f"{ORDERS_PATH}{order_id}/cancel/")


def _num(x: float) -> str:
    """Robinhood takes numbers as strings; no float noise or exponent."""
    s = f"{float(x):.10f}".rstrip("0").rstrip(".")
    return s or "0"
