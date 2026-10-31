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
from typing import Any, Callable, Dict, Mapping, Optional

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
