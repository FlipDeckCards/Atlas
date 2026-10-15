"""AWS Signature Version 4 for S3-compatible storage (Cloudflare R2), standard library only.

Two things are needed: a presigned GET URL (so the browser can fetch a private object for a limited time) and signed headers for the
server's own PUT / GET / DELETE calls. Written by hand to avoid pulling boto3 into a 512 MB service; checked against the example in
AWS's documentation and against botocore's own signer (see tests/server/test_media_library.py).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
from typing import Dict, Iterable, Optional, Tuple
from urllib.parse import quote

UNSIGNED = "UNSIGNED-PAYLOAD"
ALGORITHM = "AWS4-HMAC-SHA256"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    k = _hmac(("AWS4" + secret).encode("utf-8"), date)
    return _hmac(_hmac(_hmac(k, region), service), "aws4_request")


def uri_encode(s: str, keep_slash: bool = False) -> str:
    return quote(s, safe="/~" if keep_slash else "~")


def canonical_query(params: Iterable[Tuple[str, str]]) -> str:
    return "&".join(f"{uri_encode(k)}={uri_encode(v)}" for k, v in sorted(params))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stamp(now: Optional[dt.datetime]) -> Tuple[str, str]:
    n = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    return n.strftime("%Y%m%dT%H%M%SZ"), n.strftime("%Y%m%d")


def presign_get(
    *, host: str, path: str, access_key: str, secret_key: str, region: str = "auto", service: str = "s3",
    expires: int = 900, now: Optional[dt.datetime] = None, scheme: str = "https", extra_query: Optional[Dict[str, str]] = None,
) -> str:
    """A URL that lets anyone holding it GET `path` until `expires` seconds from `now`. `path` is the object path as sent (starts with /)."""
    amz_date, date = _stamp(now)
    scope = f"{date}/{region}/{service}/aws4_request"
    params = {
        "X-Amz-Algorithm": ALGORITHM,
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(int(expires)),
        "X-Amz-SignedHeaders": "host",
    }
    params.update(extra_query or {})
    cq = canonical_query(params.items())
    canonical = "\n".join(["GET", uri_encode(path, keep_slash=True), cq, f"host:{host}\n", "host", UNSIGNED])
    to_sign = "\n".join([ALGORITHM, amz_date, scope, sha256_hex(canonical.encode("utf-8"))])
    sig = hmac.new(signing_key(secret_key, date, region, service), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{scheme}://{host}{uri_encode(path, keep_slash=True)}?{cq}&X-Amz-Signature={sig}"


def sign_headers(
    *, method: str, host: str, path: str, payload: bytes, access_key: str, secret_key: str, region: str = "auto", service: str = "s3",
    now: Optional[dt.datetime] = None, query: Optional[Dict[str, str]] = None, content_type: Optional[str] = None,
) -> Dict[str, str]:
    """Headers (including Authorization) for a signed request with a body hash. The caller sends them as they are."""
    amz_date, date = _stamp(now)
    scope = f"{date}/{region}/{service}/aws4_request"
    payload_hash = sha256_hex(payload)
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    if content_type:
        headers["content-type"] = content_type
    names = sorted(headers)
    canonical_headers = "".join(f"{n}:{headers[n].strip()}\n" for n in names)
    signed = ";".join(names)
    canonical = "\n".join([method.upper(), uri_encode(path, keep_slash=True), canonical_query((query or {}).items()), canonical_headers, signed, payload_hash])
    to_sign = "\n".join([ALGORITHM, amz_date, scope, sha256_hex(canonical.encode("utf-8"))])
    sig = hmac.new(signing_key(secret_key, date, region, service), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["authorization"] = f"{ALGORITHM} Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return headers
