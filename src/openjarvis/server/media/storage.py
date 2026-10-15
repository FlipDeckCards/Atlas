"""Object storage for the media library: Cloudflare R2 (private bucket) and an in-memory stand-in for tests.

R2 settings come only from the environment: R2_BUCKET (default aibussol-media), R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and CLOUDFLARE_ACCOUNT_ID
(for the endpoint https://<account>.r2.cloudflarestorage.com). They are never logged or returned. Failures are logged as a status code only.
Files are private: the browser only ever receives a presigned GET URL that stops working after 15 minutes.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Dict, Optional, Protocol, Tuple
from urllib.parse import quote, urlencode

import httpx

from openjarvis.server.media import sigv4

logger = logging.getLogger(__name__)

PRESIGN_SECONDS = 15 * 60
TIMEOUT_S = 60.0
DEFAULT_BUCKET = "aibussol-media"


class StorageError(Exception):
    """A storage failure with a message that is safe to show. Details stay in the log (status codes only)."""

    def __init__(self, public_message: str = "The media storage could not be reached. Please try again.", status_code: int = 502) -> None:
        super().__init__(public_message)
        self.public_message, self.status_code = public_message, status_code


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    def presign_get(self, key: str, filename: Optional[str] = None) -> str: ...


class R2Storage:
    def __init__(self, account_id: str, access_key: str, secret_key: str, bucket: str = DEFAULT_BUCKET) -> None:
        self._acct, self._ak, self._sk, self.bucket = account_id, access_key, secret_key, bucket
        self.host = f"{account_id}.r2.cloudflarestorage.com"

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> Optional["R2Storage"]:
        e = os.environ if env is None else env
        acct, ak, sk = (e.get("CLOUDFLARE_ACCOUNT_ID") or "").strip(), (e.get("R2_ACCESS_KEY_ID") or "").strip(), (e.get("R2_SECRET_ACCESS_KEY") or "").strip()
        if not (acct and ak and sk):
            return None
        return cls(acct, ak, sk, (e.get("R2_BUCKET") or DEFAULT_BUCKET).strip() or DEFAULT_BUCKET)

    def _path(self, key: str) -> str:
        return f"/{self.bucket}/{key}"

    async def _call(self, method: str, key: str, payload: bytes = b"", content_type: Optional[str] = None) -> httpx.Response:
        path = self._path(key)
        headers = sigv4.sign_headers(method=method, host=self.host, path=path, payload=payload, access_key=self._ak, secret_key=self._sk, content_type=content_type)
        url = f"https://{self.host}{sigv4.uri_encode(path, keep_slash=True)}"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
                return await c.request(method, url, content=payload if method == "PUT" else None, headers=headers)
        except httpx.HTTPError as exc:
            logger.error("R2 request failed (%s)", type(exc).__name__)       # the class only: the message could echo the URL
            raise StorageError() from None

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        r = await self._call("PUT", key, data, content_type)
        if r.status_code not in (200, 201):
            logger.error("R2 PUT answered HTTP %s", r.status_code)
            raise StorageError()

    async def get(self, key: str) -> bytes:
        r = await self._call("GET", key)
        if r.status_code == 404:
            raise StorageError("That file is no longer in storage.", 404)
        if r.status_code != 200:
            logger.error("R2 GET answered HTTP %s", r.status_code)
            raise StorageError()
        return r.content

    async def delete(self, key: str) -> None:
        r = await self._call("DELETE", key)
        if r.status_code not in (200, 202, 204, 404):
            logger.error("R2 DELETE answered HTTP %s", r.status_code)
            raise StorageError()

    def presign_get(self, key: str, filename: Optional[str] = None, now: Optional[dt.datetime] = None) -> str:
        extra = {"response-content-disposition": f'attachment; filename="{filename}"'} if filename else None
        return sigv4.presign_get(host=self.host, path=self._path(key), access_key=self._ak, secret_key=self._sk, expires=PRESIGN_SECONDS, now=now, extra_query=extra)


class MemoryStorage:
    """Test double with the same interface. Presigned 'URLs' carry an expiry that `fetch` enforces."""

    def __init__(self, clock=None) -> None:
        self.objects: Dict[str, Tuple[bytes, str]] = {}
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.fail_delete = False

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise StorageError("That file is no longer in storage.", 404)
        return self.objects[key][0]

    async def delete(self, key: str) -> None:
        if self.fail_delete:
            raise StorageError()
        self.objects.pop(key, None)

    def presign_get(self, key: str, filename: Optional[str] = None) -> str:
        exp = int((self.clock() + dt.timedelta(seconds=PRESIGN_SECONDS)).timestamp())
        return f"memory://bucket/{quote(key)}?{urlencode({'exp': exp, 'sig': 'x'})}"

    def fetch(self, url: str) -> bytes:
        """What the browser would get from the URL; refuses after the expiry."""
        from urllib.parse import parse_qs, unquote, urlparse

        u = urlparse(url)
        if int(parse_qs(u.query)["exp"][0]) < int(self.clock().timestamp()):
            raise PermissionError("expired")
        return self.objects[unquote(u.path.lstrip("/"))][0]


def storage_from_env() -> Optional[R2Storage]:
    return R2Storage.from_env()
