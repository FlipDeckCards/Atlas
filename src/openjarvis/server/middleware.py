"""Security middleware -- HTTP security headers and request guards."""

from __future__ import annotations

import logging
import time
from typing import Any

# uvicorn's own logger is always shown in the host's logs, so the timing lines are readable there
_timing_log = logging.getLogger("uvicorn.error")
TIMED_PATHS = ("/api/chat",)

__all__ = ["SECURITY_HEADERS", "create_security_middleware"]


def create_security_middleware() -> Any:
    try:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request
        from starlette.responses import Response
    except ImportError:
        return None

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Response:
            if request.method == "OPTIONS":
                return await call_next(request)

            started = time.monotonic()
            response = await call_next(request)
            if request.method == "POST" and request.url.path in TIMED_PATHS:
                ms = int((time.monotonic() - started) * 1000)
                response.headers["Server-Timing"] = f"total;dur={ms}"
                _timing_log.info("timing: POST %s took %.1fs (status %s)", request.url.path, ms / 1000, response.status_code)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=()"
            )
            # CSP disabled — blocks Three.js CDN (unpkg.com)
            # response.headers["Content-Security-Policy"] = (
            #     "default-src 'self' 'unsafe-inline' 'unsafe-eval'"
            # )
            return response

    return SecurityHeadersMiddleware


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # "Content-Security-Policy": "default-src 'self' 'unsafe-inline' 'unsafe-eval'",
}