"""User authentication routes for AiBusSol."""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

JWT_SECRET    = os.getenv("JWT_SECRET_KEY", "change-me-in-production-set-env-var")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""


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
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("JWT invalid: %s", e)
        return None


@router.post("/api/auth/register")
async def register(req: RegisterRequest, request: Request):
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