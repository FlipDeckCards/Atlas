"""User authentication routes for AiBusSol."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openjarvis.server import hardening

logger = logging.getLogger(__name__)

router = APIRouter()

# No fallback secret exists: the signing key is read from JWT_SECRET_KEY on every use, and a missing or default
# value makes token creation/checking fail closed (the server also refuses to start without it, see hardening.py).
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30


MIN_PASSWORD_LENGTH = 8


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""
    invite_code: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


def create_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, hardening.get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, hardening.get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except hardening.StartupConfigError:
        logger.error("JWT_SECRET_KEY is missing or unsafe: refusing to accept tokens")
        return None
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("JWT invalid: %s", e)
        return None


@router.post("/api/auth/register")
async def register(req: RegisterRequest, request: Request):
    # invite-only: with INVITE_CODE unset, registration is switched off
    if not hardening.invite_code_configured():
        raise HTTPException(status_code=403, detail="Registration is closed")
    blocked = hardening.enforce({"register_ip": hardening.client_ip(request)})
    if blocked:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.", headers={"Retry-After": str(blocked[1])})
    if not hardening.invite_code_ok(req.invite_code):
        raise HTTPException(status_code=403, detail="Invalid invite code")
    if len(req.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="Password must be at least %d characters" % MIN_PASSWORD_LENGTH)
    store = getattr(request.app.state, "session_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Service unavailable")

    existing = await store.get_user_by_email(req.email.lower().strip())
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    user_id       = str(uuid.uuid4())

    await store.create_user(
        user_id=user_id,
        email=req.email.lower().strip(),
        password_hash=password_hash,
        display_name=req.display_name or req.email.split("@")[0],
    )

    token = create_token(user_id, req.email.lower().strip())
    return JSONResponse({"token": token, "user_id": user_id, "email": req.email.lower().strip()})


@router.post("/api/auth/login")
async def login(req: LoginRequest, request: Request):
    blocked = hardening.enforce({
        "login_ip": hardening.client_ip(request),
        "login_user": req.email.lower().strip()[:200],
    })
    if blocked:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.", headers={"Retry-After": str(blocked[1])})
    store = getattr(request.app.state, "session_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Service unavailable")

    user = await store.get_user_by_email(req.email.lower().strip())
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not bcrypt.checkpw(req.password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token(user["id"], user["email"])
    return JSONResponse({"token": token, "user_id": user["id"], "email": user["email"]})


@router.get("/api/auth/me")
async def me(request: Request):
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return JSONResponse({"user_id": payload["sub"], "email": payload.get("email", "")})