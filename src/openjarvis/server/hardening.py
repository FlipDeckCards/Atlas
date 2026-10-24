"""Security hardening for the Aibussol web app: required secrets, rate limits, size caps, spend cap, model names.

Everything here is plain Python with no network or database access of its own, so it is easy to test.
Environment variable NAMES are documented in SECURITY-CHANGES.md; values are never logged.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# ── required secrets ────────────────────────────────────────────────────────────────────────────

# the old public fallback; it must never be accepted as a real secret
DEFAULT_JWT_SECRET = "change-me-in-production-set-env-var"
_PLACEHOLDER_PREFIXES = ("change-me", "changeme", "replace-me", "your-secret", "secret")


class StartupConfigError(RuntimeError):
    """The server is missing a required secret (or has an unsafe one) and must not start."""


def jwt_secret_problem(value: Optional[str]) -> Optional[str]:
    """Why a JWT secret is not acceptable, or None when it is fine."""
    v = (value or "").strip()
    if not v:
        return "JWT_SECRET_KEY is not set"
    if v == DEFAULT_JWT_SECRET or v.lower().startswith(_PLACEHOLDER_PREFIXES):
        return "JWT_SECRET_KEY is still the default/placeholder string"
    return None


MIN_JWT_SECRET_BYTES = 32  # HS256 signs with a 256-bit key; PyJWT warns below 32 bytes
_STRICT_HINT = (
    " Set JWT_SECRET_STRICT=1 to make the server refuse to start with a short key."
)


def _truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def jwt_secret_length_warning(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """A sentence when JWT_SECRET_KEY is under 32 BYTES (counted in bytes, not characters), else None."""
    e = os.environ if env is None else env
    n = len((e.get("JWT_SECRET_KEY") or "").strip().encode("utf-8"))
    if 0 < n < MIN_JWT_SECRET_BYTES:
        return (
            f"JWT_SECRET_KEY is only {n} bytes long; use at least {MIN_JWT_SECRET_BYTES} "
            'bytes (for example: python -c "import secrets; print(secrets.token_urlsafe(48))").'
            + _STRICT_HINT
        )
    return None


def validate_startup(env: Optional[Mapping[str, str]] = None) -> None:
    """Refuse to start without JWT_SECRET_KEY and OPENJARVIS_API_KEY. Raises StartupConfigError with a clear message."""
    e = os.environ if env is None else env
    problems = []
    p = jwt_secret_problem(e.get("JWT_SECRET_KEY"))
    if p:
        problems.append(
            p
            + '. Set JWT_SECRET_KEY to a long random value (for example: python -c "import secrets; print(secrets.token_urlsafe(48))").'
        )
    if not (e.get("OPENJARVIS_API_KEY") or "").strip():
        problems.append(
            "OPENJARVIS_API_KEY is not set, so /api/* and /v1/* would not require authentication. Set it (or run: jarvis auth generate-key)."
        )
    short = jwt_secret_length_warning(e)
    if short and _truthy(e.get("JWT_SECRET_STRICT")):
        problems.append(short.replace(_STRICT_HINT, ""))
    if problems:
        raise StartupConfigError("Refusing to start: " + " ".join(problems))
    if short:
        logger.warning(short)  # still starts: refusing would take a running service down


def get_jwt_secret(env: Optional[Mapping[str, str]] = None) -> str:
    """The JWT signing secret, read at call time. Fails closed: no fallback value exists."""
    e = os.environ if env is None else env
    raw = e.get("JWT_SECRET_KEY")
    p = jwt_secret_problem(raw)
    if p:
        raise StartupConfigError(p)
    return (raw or "").strip()


# ── invite-only registration ────────────────────────────────────────────────────────────────────


def invite_code_configured(env: Optional[Mapping[str, str]] = None) -> bool:
    return bool(((os.environ if env is None else env).get("INVITE_CODE") or "").strip())


def invite_code_ok(
    given: Optional[str], env: Optional[Mapping[str, str]] = None
) -> bool:
    """True only when INVITE_CODE is set and matches (constant-time). Unset = registration is disabled."""
    want = ((os.environ if env is None else env).get("INVITE_CODE") or "").strip()
    if not want:
        return False
    return hmac.compare_digest(str(given or "").strip().encode(), want.encode())


# ── rate limits ─────────────────────────────────────────────────────────────────────────────────


class RateLimiter:
    """In-memory sliding window. One process only (Render Starter runs one); limits reset on restart."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: float) -> Tuple[bool, int]:
        """Record a hit. Returns (allowed, retry_after_seconds). A blocked attempt is not recorded."""
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= window_s:
                q.popleft()
            if len(q) >= limit:
                return False, max(1, int(window_s - (now - q[0])) + 1)
            q.append(now)
            if len(self._hits) > 20000:  # keep memory bounded
                for k in [k for k, v in self._hits.items() if not v][:5000]:
                    self._hits.pop(k, None)
            return True, 0


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def rate_limits() -> Dict[str, Tuple[int, float]]:
    """(max requests, window seconds) per rule. Defaults can be raised or lowered with environment variables."""
    return {
        "login_ip": (
            _int_env("RL_LOGIN_IP_MAX", 10),
            900.0,
        ),  # 10 attempts / 15 min per IP
        "login_user": (
            _int_env("RL_LOGIN_USER_MAX", 5),
            900.0,
        ),  # 5 attempts / 15 min per email
        "register_ip": (
            _int_env("RL_REGISTER_IP_MAX", 5),
            3600.0,
        ),  # 5 attempts / hour per IP
        "chat_user": (
            _int_env("RL_CHAT_USER_MAX", 20),
            60.0,
        ),  # 20 messages / min per user
        "chat_ip": (_int_env("RL_CHAT_IP_MAX", 60), 60.0),  # 60 messages / min per IP
        "voice_user": (
            _int_env("RL_VOICE_USER_MAX", 30),
            60.0,
        ),  # 30 voice calls / min per user
        "voice_ip": (_int_env("RL_VOICE_IP_MAX", 90), 60.0),
        "brief_ip": (_int_env("RL_BRIEF_IP_MAX", 60), 60.0),                 # brief ingest attempts per IP (also slows token guessing)
        "media_user": (_int_env("RL_MEDIA_USER_MAX", 6), 60.0),        # 6 image / sound generations per minute per user
        "media_ip": (_int_env("RL_MEDIA_IP_MAX", 20), 60.0),
        "media_read_user": (_int_env("RL_MEDIA_READ_USER_MAX", 60), 60.0),     # library browsing
        "media_read_ip": (_int_env("RL_MEDIA_READ_IP_MAX", 180), 60.0),
        "media_edit_user": (_int_env("RL_MEDIA_EDIT_USER_MAX", 30), 60.0),     # edit previews and saves (no provider cost)
        "media_edit_ip": (_int_env("RL_MEDIA_EDIT_IP_MAX", 90), 60.0),
    }


LIMITER = RateLimiter()


def client_ip(request) -> str:
    """The caller's IP. Behind Render's proxy the real address is the last X-Forwarded-For entry the proxy added
    (the left side can be forged by the caller). TRUSTED_PROXY_HOPS (default 1) says how many proxies sit in front."""
    hops = _int_env("TRUSTED_PROXY_HOPS", 1)
    xff = (
        request.headers.get("x-forwarded-for", "")
        if getattr(request, "headers", None) is not None
        else ""
    )
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if parts:
        return parts[-hops] if len(parts) >= hops else parts[0]
    c = getattr(request, "client", None)
    return c.host if c and c.host else "unknown"


def enforce(rules: Dict[str, str], limiter: RateLimiter = None):
    """Apply several rules at once: {rule_name: key}. Returns None when allowed, else (rule, retry_after)."""
    lim = limiter or LIMITER
    table = rate_limits()
    for rule, key in rules.items():
        limit, window = table[rule]
        ok, retry = lim.check(rule + ":" + key, limit, window)
        if not ok:
            return rule, retry
    return None


# ── input size caps ─────────────────────────────────────────────────────────────────────────────

MAX_MESSAGE_CHARS = 8_000  # one chat message
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # one attached image, decoded (5 MB)
MAX_IMAGE_B64_CHARS = (MAX_IMAGE_BYTES * 4) // 3 + 8
MAX_AUDIO_BYTES = (
    10 * 1024 * 1024
)  # one voice recording upload (10 MB; Whisper itself allows 25 MB)
MAX_TTS_CHARS = 1_500  # text sent to text-to-speech
MAX_ATTACHMENTS = 5  # images and documents on one chat message
MAX_ATTACH_TOTAL_BYTES = 12 * 1024 * 1024  # all of one message's attachments together, decoded
MAX_BODY_BYTES = (
    (MAX_ATTACH_TOTAL_BYTES * 4) // 3 + MAX_MESSAGE_CHARS + 16 * 1024
)  # a chat request, by Content-Length
MAX_UPLOAD_BODY_BYTES = MAX_AUDIO_BYTES + 64 * 1024  # a voice upload, by Content-Length
CHAT_MAX_TOKENS = (
    1024  # reply length asked from the model (bounds the cost of one call)
)
CREW_CHAT_MAX_TOKENS = 2000  # the same, when Helix leads the crew and investigates (diagnoses, plans, long answers)
CREW_TOOL_ROUNDS = 10  # how many tool calls in a row Helix may make to work something out
CREW_HISTORY_MESSAGES = 24  # how much of the conversation she remembers (ordinary chat keeps 10)


def too_large_for_path(path: str, content_length: Optional[str]) -> bool:
    """True when the declared body size is over the cap for this path. Only /api/* is checked."""
    if not path.startswith("/api/") or not content_length:
        return False
    try:
        n = int(content_length)
    except ValueError:
        return False
    cap = MAX_UPLOAD_BODY_BYTES if path == "/api/voice/transcribe" else MAX_BODY_BYTES
    return n > cap


# ── daily spend cap ─────────────────────────────────────────────────────────────────────────────

DEFAULT_DAILY_SPEND_CAP_USD = 1.00
FLAT_CHAT_ESTIMATE_USD = (
    0.02  # used when a model's price is unknown (deliberately on the high side)
)
TTS_USD_PER_1K_CHARS = 0.30  # ElevenLabs, pay-as-you-go ballpark; a high estimate
WHISPER_USD_PER_MB = (
    0.006  # Whisper is $0.006/minute; assuming 1 MB of audio is a minute errs high
)


def daily_spend_cap_usd(env: Optional[Mapping[str, str]] = None) -> float:
    """DAILY_SPEND_CAP_USD per user per UTC day. Unset -> 1.00. 0 -> every paid call is refused."""
    raw = (
        (os.environ if env is None else env).get("DAILY_SPEND_CAP_USD") or ""
    ).strip()
    if raw == "":
        return DEFAULT_DAILY_SPEND_CAP_USD
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_DAILY_SPEND_CAP_USD


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def tts_cost_usd(chars: int) -> float:
    return round(chars / 1000.0 * TTS_USD_PER_1K_CHARS, 4)


def whisper_cost_usd(nbytes: int) -> float:
    return round(max(nbytes, 1) / (1024 * 1024) * WHISPER_USD_PER_MB, 4)


def chat_cost_usd(response, litellm_module=None) -> float:
    """The price of a finished model call from LiteLLM's price table; a flat estimate when the model is not in it."""
    try:
        lm = litellm_module
        if lm is None:
            import litellm as lm
        c = lm.completion_cost(completion_response=response)
        if c is not None and float(c) >= 0:
            return round(float(c), 6)
    except Exception:
        pass
    return FLAT_CHAT_ESTIMATE_USD


OWNER_EMAIL = "michael.dano1523@gmail.com"


def spend_exempt(email: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """True for the owner accounts listed in SPEND_CAP_EXEMPT_EMAILS (comma-separated; default: the owner).
    They skip the daily spend cap and the daily briefing-Listen limit. Everyone else is unchanged."""
    e = os.environ if env is None else env
    raw = (e.get("SPEND_CAP_EXEMPT_EMAILS") or "").strip() or OWNER_EMAIL
    allowed = {x.strip().lower() for x in raw.split(",") if x.strip()}
    return bool(email) and email.strip().lower() in allowed


async def spend_blocked(
    store, user_id: str, estimate: float = 0.0, cap: Optional[float] = None, exempt: bool = False
) -> Optional[dict]:
    """None when the user may make another paid call today; otherwise the error body (the provider must NOT be called)."""
    if exempt:
        return None
    cap = daily_spend_cap_usd() if cap is None else cap
    spent = float(await store.get_spend_today(user_id, utc_day()))
    if cap <= 0 or spent >= cap or spent + estimate > cap:
        return {
            "error": "daily_spend_cap_reached",
            "detail": "Daily usage limit reached. Paid model calls are paused until midnight UTC.",
            "cap_usd": cap,
            "spent_usd": round(spent, 4),
        }
    return None


# ── model names (config instead of hard-coded strings) ──────────────────────────────────────────

DEFAULT_MODEL = "gpt-4o"
DEFAULT_WRITING_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_LOOKUP_MODEL = "gemini/gemini-2.5-flash"
# the crew's tiers: Helix thinks on Sonnet (leading, planning, diagnosing), her light chat and most agents run on Haiku (cheap, high volume),
# and Forge, who writes the code, on Sonnet. Each can be changed on Render: ATLAS_HELIX_MODEL, ATLAS_HELIX_LIGHT_MODEL, ATLAS_AGENT_MODEL, ATLAS_CODE_MODEL.
DEFAULT_HELIX_MODEL = "claude-sonnet-5-5"
DEFAULT_HELIX_LIGHT_MODEL = "claude-haiku-5-5"
DEFAULT_AGENT_MODEL = "claude-haiku-5-5"
DEFAULT_CODE_MODEL = "claude-sonnet-5-5"


def model_config(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Model names for the three routes. ATLAS_DEFAULT_MODEL (also used for images), ATLAS_WRITING_MODEL (writing/code
    keywords) and ATLAS_LOOKUP_MODEL (lookup keywords). NOTE: the lookup route is a plain model call with NO web access."""
    e = os.environ if env is None else env
    g = lambda k, d: (e.get(k) or "").strip() or d  # noqa: E731
    return {
        "default": g("ATLAS_DEFAULT_MODEL", DEFAULT_MODEL),
        "writing": g("ATLAS_WRITING_MODEL", DEFAULT_WRITING_MODEL),
        "lookup": g("ATLAS_LOOKUP_MODEL", DEFAULT_LOOKUP_MODEL),
        "helix": g("ATLAS_HELIX_MODEL", DEFAULT_HELIX_MODEL),
        "helix_light": g("ATLAS_HELIX_LIGHT_MODEL", DEFAULT_HELIX_LIGHT_MODEL),
        "agent": g("ATLAS_AGENT_MODEL", DEFAULT_AGENT_MODEL),
        "code": g("ATLAS_CODE_MODEL", DEFAULT_CODE_MODEL),
    }


def display_name(model_id: str) -> str:
    """A short label for the engine indicator: provider prefix removed, upper case, at most 18 characters."""
    base = str(model_id or "").split("/")[-1].upper()
    return base[:18]
