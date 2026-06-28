"""API key + JWT authentication middleware for the OpenJarvis server."""

from __future__ import annotations

import logging
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates ``Authorization: Bearer <key>`` on ``/v1/*`` and ``/api/*`` routes.

    Accepts EITHER:
    - The system OPENJARVIS_API_KEY (existing behaviour), OR
    - A valid user JWT issued by /api/auth/login or /api/auth/register (new).

    /api/auth/* routes are public — no token required to log in or register.
    Webhook routes and health checks are exempt — they use per-channel
    signature verification instead.
    """

    def __init__(self, app, api_key: str = "") -> None:  # noqa: ANN001
        super().__init__(app)
        self._api_key = api_key or os.environ.get("OPENJARVIS_API_KEY", "")

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        if self._api_key and self._requires_auth(request.url.path):
            auth = request.headers.get("Authorization", "")
            if not auth:
                return JSONResponse(
                    {"detail": "Missing Authorization header"},
                    status_code=401,
                )
            scheme, _, token = auth.partition(" ")
            if scheme.lower() != "bearer":
                return JSONResponse(
                    {"detail": "Invalid Authorization scheme"},
                    status_code=401,
                )

            # 1. Accept system API key (constant-time comparison)
            if secrets.compare_digest(token, self._api_key):
                return await call_next(request)

            # 2. Accept valid user JWT
            try:
                from openjarvis.server.auth_routes import decode_token
                payload = decode_token(token)
                if payload:
                    request.state.user_id    = payload["sub"]
                    request.state.user_email = payload.get("email", "")
                    return await call_next(request)
            except Exception as exc:
                logger.debug("JWT decode error: %s", exc)

            return JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401,
            )
        return await call_next(request)

    @staticmethod
    def _requires_auth(path: str) -> bool:
        # Auth endpoints are public — needed to obtain a token
        if path.startswith("/api/auth/"):
            return False
        return (
            path.startswith("/v1/")
            or path.startswith("/api/")
            or path == "/metrics"
            or path.startswith("/metrics/")
        )


def generate_api_key() -> str:
    """Generate a new API key with ``oj_sk_`` prefix."""
    return f"oj_sk_{secrets.token_urlsafe(32)}"


def check_bind_safety(host: str, *, api_key: str) -> None:
    """Refuse to bind non-loopback without an API key."""
    import ipaddress
    import sys

    try:
        is_loop = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loop = host in ("localhost", "")

    if not is_loop and not api_key:
        logger.error(
            "Binding to %s requires OPENJARVIS_API_KEY to be set. "
            "Run: jarvis auth generate-key",
            host,
        )
        sys.exit(1)


def websocket_authorized(websocket, expected_key: str) -> bool:  # noqa: ANN001
    """Return ``True`` if a WebSocket connection presents the expected key."""
    if not expected_key:
        return True
    token = websocket.query_params.get("token", "")
    if not token:
        auth = websocket.headers.get("authorization", "")
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer":
            token = value
    if not token:
        return False
    return secrets.compare_digest(token, expected_key)