import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
import litellm
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from pydantic import BaseModel, Field

from openjarvis.server import admin_analytics, briefs, hardening
from openjarvis.server.crew import helix as crew_helix
from openjarvis.server.media import service as media_service
from openjarvis.server.media import helix_tools
from openjarvis.server.media.service import MediaServiceError

logger = logging.getLogger(__name__)


router = APIRouter()

API_KEY             = os.getenv("OPENJARVIS_API_KEY", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ATLAS_MODEL_URL     = os.getenv("ATLAS_MODEL_URL", "")

CHANNEL = "web"


# CHANGE 1: accept client_date, fall back to UTC if not provided
def get_system_prompt(client_date: str = None) -> str:
    if client_date:
        date_str = client_date
    else:
        date_str = datetime.now(timezone.utc).strftime("%A, %B %d, %Y %H:%M UTC")
    return f"""You are Helix, the assistant of Aibussol (AI Business Solutions), a personal AI hub and orchestrator built by Michael.
You have persistent memory and can handle a wide range of tasks with intelligence and precision.
You are not OpenJarvis — you are Helix.
Today's date and time is {date_str}.
Be direct, sharp, and helpful."""


def _with_fallback(model_id: str) -> list:
    """The routed model, then the writing model (Claude) when they differ: a provider out of quota (Gemini's free tier) must not stop Helix."""
    writing = hardening.model_config().get("writing")
    return [model_id] + ([writing] if writing and writing != model_id else [])


def classify_model(message: str, has_image: bool, models: Optional[dict] = None) -> tuple:
    """Returns (litellm_model_id, display_name, color_class). Model names come from configuration
    (ATLAS_DEFAULT_MODEL, ATLAS_WRITING_MODEL, ATLAS_LOOKUP_MODEL); see hardening.model_config.
    The keyword routing is unchanged. NOTE: the "lookup" route is a plain model call with NO web access."""
    m = models or hardening.model_config()
    if has_image:
        return m["default"], hardening.display_name(m["default"]), "cyan"

    msg = message.lower()

    claude_signals = [
        "write", "essay", "explain", "analyze", "analyse", "review",
        "summarize", "summarise", "compare", "code", "debug", "refactor",
        "implement", "design", "pros and cons", "difference between",
        "how does", "why does", "haiku", "poem", "sonnet", "verse", "rhyme", "story", "creative", "step by step", "rewrite", "edit"
    ]
    gemini_signals = [
        "research", "find", "search", "latest", "current", "news",
        "who is", "what is", "when did", "where is", "how many",
        "statistics", "data", "list", "examples of", "types of",
        "look up", "fact"
    ]

    claude_score = sum(1 for s in claude_signals if s in msg)
    gemini_score = sum(1 for s in gemini_signals if s in msg)

    if claude_score > gemini_score and claude_score > 0:
        return m["writing"], hardening.display_name(m["writing"]), "orange"
    elif gemini_score >= claude_score and gemini_score > 0:
        return m["lookup"], hardening.display_name(m["lookup"]), "green"
    else:
        return m["default"], hardening.display_name(m["default"]), "cyan"


_HERE = os.path.dirname(os.path.abspath(__file__))
_MODEL_CANDIDATES = [
    os.path.join(_HERE, "static", "atlas-model.glb"),
    os.path.join(_HERE, "..", "..", "static", "atlas-model.glb"),
    os.path.join(_HERE, "..", "..", "..", "static", "atlas-model.glb"),
    "/app/static/atlas-model.glb",
]

def _find_model() -> Optional[str]:
    for p in _MODEL_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _get_user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "system")


# CHANGE 2: add client_date field
class ChatRequest(BaseModel):
    message: str = Field(..., max_length=hardening.MAX_MESSAGE_CHARS)
    image: Optional[str] = Field(None, max_length=hardening.MAX_IMAGE_B64_CHARS)
    client_date: Optional[str] = Field(None, max_length=100)


_BRIEF_REPLY = re.compile(r"^(?:Good (?:morning|afternoon|evening|day)[^\n]*\n\n)?(Morning|Afternoon|Evening) brief for ")      # how briefs.brief_reply starts (after its greeting)


def _exempt(request: Request) -> bool:
    """Owner accounts (SPEND_CAP_EXEMPT_EMAILS) skip the daily spend cap."""
    return hardening.spend_exempt(getattr(request.state, "user_email", "") or "")


def _limited(request: Request, rules: dict):
    """A 429 response when a rate limit is hit, otherwise None. rules: {rule_name: key}."""
    hit = hardening.enforce(rules)
    if hit:
        return JSONResponse(
            {"detail": "Too many requests. Please slow down.", "reply": "Too many requests. Please slow down."},
            status_code=429, headers={"Retry-After": str(hit[1])},
        )
    return None


def _spend_error(body: dict) -> JSONResponse:
    return JSONResponse(dict(body, reply=body["detail"]), status_code=429)


@router.get("/static/atlas-model.glb")
async def serve_model():
    path = _find_model()
    if path:
        return FileResponse(path, media_type="model/gltf-binary",
                            headers={"Cache-Control": "public, max-age=86400"})
    if not ATLAS_MODEL_URL:
        return JSONResponse({"error": "ATLAS_MODEL_URL not set"}, status_code=404)

    async def stream_glb():
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            async with client.stream("GET", ATLAS_MODEL_URL) as r:
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    yield chunk

    return StreamingResponse(stream_glb(), media_type="model/gltf-binary",
                             headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/session/history")
async def session_history(request: Request):
    user_id = _get_user_id(request)
    store = request.app.state.session_store
    session = await store.get_or_create(user_id, CHANNEL)
    return JSONResponse({
        "messages": [
            {
                "role": m["role"],
                "content": (
                    m["content"] if isinstance(m["content"], str)
                    else next((c.get("text","") for c in m["content"] if c.get("type")=="text"), "")
                    if isinstance(m["content"], list) else str(m["content"])
                )
            }
            for m in session["conversation_history"]
            if m["role"] in ("user", "assistant")
        ]
    })


@router.post("/api/voice/transcribe")
async def transcribe(request: Request, audio: UploadFile = File(...)):
    user_id = _get_user_id(request)
    limited = _limited(request, {"voice_user": user_id, "voice_ip": hardening.client_ip(request)})
    if limited:
        return limited
    if not OPENAI_API_KEY:
        return JSONResponse({"detail": "Speech-to-text is not configured"}, status_code=503)
    # read at most MAX_AUDIO_BYTES (+1 to notice an oversize upload) instead of the whole body
    audio_bytes = await audio.read(hardening.MAX_AUDIO_BYTES + 1)
    if len(audio_bytes) > hardening.MAX_AUDIO_BYTES:
        return JSONResponse({"detail": "Audio file too large (limit %d MB)" % (hardening.MAX_AUDIO_BYTES // (1024 * 1024))}, status_code=413)
    store = request.app.state.session_store
    cost = hardening.whisper_cost_usd(len(audio_bytes))
    blocked = await hardening.spend_blocked(store, user_id, cost, exempt=_exempt(request))
    if blocked:
        return _spend_error(blocked)
    await store.add_spend(user_id, hardening.utc_day(), cost)
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": (audio.filename or "audio.webm", audio_bytes, audio.content_type or "audio/webm")},
            data={"model": "whisper-1"}
        )
        return JSONResponse({"text": res.json().get("text", "")})


@router.post("/api/voice/speak")
async def speak(req: ChatRequest, request: Request):
    user_id = _get_user_id(request)
    limited = _limited(request, {"voice_user": user_id, "voice_ip": hardening.client_ip(request)})
    if limited:
        return limited
    if not ELEVENLABS_API_KEY:
        return JSONResponse({"detail": "Text-to-speech is not configured"}, status_code=503)
    text = req.message
    if len(text) > hardening.MAX_TTS_CHARS:
        return JSONResponse({"detail": "Text too long for speech (limit %d characters)" % hardening.MAX_TTS_CHARS}, status_code=413)
    store = request.app.state.session_store
    cost = hardening.tts_cost_usd(len(text))
    blocked = await hardening.spend_blocked(store, user_id, cost, exempt=_exempt(request))
    if blocked:
        return _spend_error(blocked)
    await store.add_spend(user_id, hardening.utc_day(), cost)
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": "eleven_turbo_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "speed": 0.85
                }
            }
        )
        content_type = res.headers.get("content-type", "")
        if res.status_code != 200 or "audio" not in content_type:
            return JSONResponse(
                {"error": f"ElevenLabs {res.status_code}", "detail": res.text[:300]},
                status_code=502
            )
        return StreamingResponse(iter([res.content]), media_type="audio/mpeg",
                                 headers={"Content-Disposition": "inline; filename=speech.mp3"})
@router.get("/admin/analytics")
async def admin_analytics_page():
    """The owner's analytics page. The numbers come from /api/admin/analytics, which checks the login."""
    return HTMLResponse(admin_analytics.PAGE)


@router.get("/api/admin/analytics")
async def admin_analytics_data(request: Request):
    email = getattr(request.state, "user_email", "") or ""
    if not email:
        return JSONResponse({"detail": "Sign in first."}, status_code=401)
    if not admin_analytics.is_admin(email):
        return JSONResponse({"detail": "Owner only."}, status_code=403)
    try:
        return await admin_analytics.gather(request.app.state.session_store._pool)
    except Exception:
        logger.exception("analytics query failed")
        return JSONResponse({"detail": "Analytics are unavailable right now."}, status_code=503)


@router.get("/classic")
async def index():
    html = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover"/>
  <title>Aibussol</title>
  <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&display=swap" rel="stylesheet"/>
  <style>
    :root {
      --cyan: #00e5ff; --cyan-dim: #005f6b;
      --orange: #ff6d00;
      --bg: #050508; --panel: #08080f; --border: #0d2535;
      --text: #8ecfe0; --text-dim: #2a5060;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg); color: var(--text);
      font-family: 'Share Tech Mono', monospace;
      height: 100vh; overflow: hidden;
      display: grid; grid-template-rows: 48px 1fr; gap: 1px;
      background-image:
        linear-gradient(rgba(0,229,255,0.018) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0,229,255,0.018) 1px, transparent 1px);
      background-size: 44px 44px;
    }
    #auth-overlay {
      position: fixed; inset: 0; z-index: 9999;
      background: var(--bg);
      display: flex; align-items: center; justify-content: center;
      background-image:
        linear-gradient(rgba(0,229,255,0.018) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0,229,255,0.018) 1px, transparent 1px);
      background-size: 44px 44px;
    }
    #auth-box {
      background: var(--panel); border: 1px solid var(--cyan-dim);
      padding: 40px 36px; width: 380px; display: flex; flex-direction: column; gap: 18px;
      position: relative;
    }
    .auth-corner { position:absolute; width:14px; height:14px; border-color:var(--cyan); border-style:solid; }
    .auth-corner.tl{top:8px;left:8px;border-width:1px 0 0 1px}
    .auth-corner.tr{top:8px;right:8px;border-width:1px 1px 0 0}
    .auth-corner.bl{bottom:8px;left:8px;border-width:0 0 1px 1px}
    .auth-corner.br{bottom:8px;right:8px;border-width:0 1px 1px 0}
    #auth-title {
      font-family: 'Orbitron', monospace; font-size: 20px; font-weight: 900;
      color: var(--cyan); letter-spacing: 4px; text-shadow: 0 0 20px var(--cyan);
      text-align: center;
    }
    #auth-title span { color: var(--orange); }
    #auth-mode-label {
      font-size: 9px; letter-spacing: 4px; color: var(--text-dim);
      text-align: center; text-transform: uppercase;
    }
    .auth-field { display: flex; flex-direction: column; gap: 6px; }
    .auth-field label { font-size: 9px; letter-spacing: 3px; color: var(--text-dim); text-transform: uppercase; }
    .auth-field input {
      background: rgba(0,229,255,0.03); border: 1px solid var(--border);
      color: var(--text); padding: 10px 12px;
      font-family: 'Share Tech Mono', monospace; font-size: 13px;
      outline: none; letter-spacing: 1px; width: 100%;
    }
    .auth-field input:focus { border-color: var(--cyan); box-shadow: 0 0 8px rgba(0,229,255,0.1); }
    #auth-btn {
      background: transparent; border: 1px solid var(--cyan); color: var(--cyan);
      padding: 13px; font-family: 'Orbitron', monospace; font-size: 10px;
      letter-spacing: 3px; cursor: pointer; transition: all 0.2s; margin-top: 4px;
    }
    #auth-btn:hover { background: rgba(0,229,255,0.1); box-shadow: 0 0 16px rgba(0,229,255,0.25); }
    #auth-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    #auth-error { font-size: 10px; color: #ff3344; text-align: center; letter-spacing: 1px; min-height: 14px; }
    #auth-toggle { font-size: 10px; color: var(--text-dim); text-align: center; letter-spacing: 1px; cursor: default; }
    #auth-toggle-link { color: var(--cyan); cursor: pointer; text-decoration: underline; }
    #auth-toggle-link:hover { color: #fff; }
    #auth-display-wrap { display: none; }
    #auth-invite-wrap { display: none; }
    .hud-hidden { display: none !important; }
    #hud-header {
      background: var(--panel); border-bottom: 1px solid var(--border);
      display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
    }
    #hud-title {
      font-family: 'Orbitron', monospace; font-size: 16px; font-weight: 900;
      color: var(--cyan); letter-spacing: 4px; text-shadow: 0 0 20px var(--cyan);
    }
    #hud-title span { color: var(--orange); }
    .hud-meta { display: flex; gap: 24px; font-size: 11px; color: var(--text-dim); letter-spacing: 1px; align-items: center; }
    .hud-meta .v { color: var(--cyan); }
    #logout-btn {
      background: transparent; border: 1px solid var(--border); color: var(--text-dim);
      padding: 4px 10px; font-family: 'Orbitron', monospace; font-size: 8px;
      letter-spacing: 2px; cursor: pointer; transition: all 0.2s; border-radius: 2px;
    }
    #logout-btn:hover { border-color: #ff3344; color: #ff3344; }
    #sub-btn {
      background: transparent; border: 1px solid var(--cyan); color: var(--cyan);
      padding: 4px 10px; font-family: 'Orbitron', monospace; font-size: 8px;
      letter-spacing: 2px; cursor: pointer; transition: all 0.2s; border-radius: 2px;
    }
    #sub-btn:hover { background: var(--cyan); color: var(--bg, #000); }
    #sub-btn[disabled] { border-color: var(--border); color: var(--text-dim); cursor: default; background: transparent; }
    #station-btn {
      background: transparent; border: 1px solid var(--orange); color: var(--orange);
      padding: 4px 10px; font-family: 'Orbitron', monospace; font-size: 8px;
      letter-spacing: 2px; cursor: pointer; transition: all 0.2s; border-radius: 2px; position: relative;
    }
    #station-btn:hover { background: var(--orange); color: var(--bg, #000); }
    #station-badge { position: absolute; top: -7px; right: -7px; min-width: 15px; height: 15px; border-radius: 8px; background: var(--orange); color: #050508; font-family: 'Share Tech Mono', monospace; font-size: 10px; line-height: 15px; text-align: center; padding: 0 3px; letter-spacing: 0; }
    .crew-card { border-color: rgba(255,109,0,0.35) !important; background: rgba(255,109,0,0.05) !important; }
    .crew-card .msg-tag { color: var(--orange) !important; opacity: 0.9; }
    .crew-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; align-items: center; }
    .crew-ok:hover { border-color: #00ff88 !important; color: #00ff88 !important; }
    #hud-main { display: grid; grid-template-columns: 200px 1fr 400px; gap: 1px; overflow: hidden; }
    #panel-left {
      background: var(--panel); border-right: 1px solid var(--border);
      display: flex; flex-direction: column; overflow: hidden;
    }
    .sys-block { padding: 14px 16px; border-bottom: 1px solid var(--border); }
    .p-label {
      font-family: 'Orbitron', monospace; font-size: 9px; letter-spacing: 3px;
      color: var(--text-dim); margin-bottom: 10px; text-transform: uppercase;
    }
    .stat-row {
      display: flex; justify-content: space-between; align-items: center;
      padding: 5px 0; font-size: 11px; border-bottom: 1px solid rgba(0,229,255,0.04);
    }
    .stat-row .lbl { color: var(--text-dim); letter-spacing: 1px; }
    .stat-row .val { color: var(--cyan); font-weight: 700; }
    .stat-row .val.g { color: #00ff88; }
    .stat-row .val.o { color: var(--orange); }
    .stat-row .val.d { color: #335566; }
    #engine-val.engine-cyan   { color: var(--cyan); }
    #engine-val.engine-orange { color: var(--orange); }
    #engine-val.engine-green  { color: #00ff88; }
    @keyframes engineFlash {
      0%   { opacity: 1; }
      25%  { opacity: 0.05; }
      60%  { opacity: 1; }
      80%  { opacity: 0.3; }
      100% { opacity: 1; }
    }
    #engine-val.flash { animation: engineFlash 0.45s ease; }
    #live-feed { flex: 0 0 150px; padding: 14px 16px; overflow: hidden; font-size: 10px; line-height: 1.9; letter-spacing: 0.5px; }
    .feed-line { color: var(--text-dim); transition: color 0.5s, opacity 0.5s; }
    @keyframes blink { 0%,100%{opacity:1}50%{opacity:0.3} }
    #panel-center {
      background: var(--panel);
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      position: relative; overflow: hidden;
    }
    #panel-center::after {
      content:''; position:absolute; inset:0; pointer-events:none;
      background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,229,255,0.01) 2px, rgba(0,229,255,0.01) 4px);
    }
    .corner { position:absolute; width:20px; height:20px; border-color:var(--cyan-dim); border-style:solid; opacity:0.4; }
    .tl{top:12px;left:12px;border-width:1px 0 0 1px}
    .tr{top:12px;right:12px;border-width:1px 1px 0 0}
    .bl{bottom:12px;left:12px;border-width:0 0 1px 1px}
    .br{bottom:12px;right:12px;border-width:0 1px 1px 0}
    .face-label {
      font-family:'Orbitron',monospace; font-size:9px; letter-spacing:4px;
      color:var(--text-dim); margin-bottom:16px; text-transform:uppercase;
    }
    #face-wrap { position:relative; height:min(960px, 100%); aspect-ratio: 780 / 960; max-width:100%; flex: 0 1 auto; min-height:0; }
    .f-ring {
      position:absolute; inset:-16px; border:1px solid rgba(0,229,255,0.15);
      border-radius:50%; animation:spin 20s linear infinite;
    }
    .f-ring::before {
      content:''; position:absolute; top:-2px; left:50%; width:4px; height:4px;
      background:var(--cyan); border-radius:50%; box-shadow:0 0 8px var(--cyan); margin-left:-2px;
    }
    .f-ring2 {
      position:absolute; inset:-28px; border:1px solid rgba(255,109,0,0.1);
      border-radius:50%; animation:spin 35s linear infinite reverse;
    }
    @keyframes spin { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
    #atlas-face { position: relative; width: 100%; height: 100%; transition: filter 0.3s; }
    #atlas-face-svg { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; }
    .face-state { margin-top:16px; text-align:center; font-size:11px; letter-spacing:3px; color:var(--text-dim); }
    #atlas-state { font-family:'Orbitron',monospace; font-size:10px; color:var(--cyan); margin-bottom:4px; }
    #panel-right {
      background:var(--panel); border-left:1px solid var(--border);
      display:flex; flex-direction:column; overflow:hidden; min-width:0;
    }
    #chat-hdr {
      padding:12px 16px; border-bottom:1px solid var(--border);
      display:flex; align-items:center; justify-content:space-between; flex-shrink:0;
    }
    #wake-ind { display:flex; align-items:center; gap:6px; font-size:10px; color:var(--text-dim); }
    #wake-dot { width:6px; height:6px; border-radius:50%; background:#1a3a4a; transition:all 0.3s; }
    #wake-dot.listening  { background:var(--cyan); box-shadow:0 0 8px var(--cyan); animation:blink 2s infinite; }
    #wake-dot.recording  { background:#ff3344; box-shadow:0 0 8px #ff3344; animation:blink 0.4s infinite; }
    #wake-dot.processing { background:var(--orange); box-shadow:0 0 8px var(--orange); animation:blink 0.8s infinite; }
    #messages {
      flex:1; overflow-y:auto; padding:14px; display:flex; flex-direction:column; gap:10px;
      scrollbar-width:thin; scrollbar-color:var(--cyan-dim) transparent; min-height:0;
    }
    #messages::-webkit-scrollbar{width:3px}
    #messages::-webkit-scrollbar-thumb{background:var(--cyan-dim);border-radius:2px}
    .voice-note { color:#ff3344; margin-top:6px; font-size:11px; line-height:1.5; white-space:normal; overflow-wrap:anywhere; }
    .msg { max-width:92%; padding:10px 12px; font-size:12px; line-height:1.6; border-radius:2px; }
    .msg-tag { font-size:9px; letter-spacing:2px; margin-bottom:4px; opacity:0.5; }
    .msg-thumb { margin-top:6px; border-radius:2px; overflow:hidden; border:1px solid rgba(0,229,255,0.2); display:inline-block; }
    .msg-thumb img { display:block; max-width:160px; max-height:100px; object-fit:cover; }
    .user  { align-self:flex-end; background:rgba(255,109,0,0.08); border:1px solid rgba(255,109,0,0.25); color:#ffa040; }
    .user .msg-tag { color:var(--orange); }
    .atlas { align-self:flex-start; background:rgba(0,229,255,0.04); border:1px solid rgba(0,229,255,0.14); color:var(--text); }
    .atlas .msg-tag { color:var(--cyan); }
    #input-area { flex-shrink:0; border-top:1px solid var(--border); }
    #img-preview-bar { display:none; align-items:center; gap:8px; padding:6px 12px 0; font-size:10px; color:#00ff88; }
    #img-preview-bar img { width:36px; height:36px; object-fit:cover; border-radius:2px; border:1px solid rgba(0,255,136,0.3); }
    #img-preview-bar .clear-img { cursor:pointer; color:var(--text-dim); font-size:14px; line-height:1; }
    #img-preview-bar .clear-img:hover { color:#ff3344; }
    #input-row { display:flex; padding:10px 12px; gap:6px; align-items:center; }
    #input {
      flex:1; min-width:0; background:rgba(0,229,255,0.03); border:1px solid var(--border);
      color:var(--text); padding:10px 12px; font-family:'Share Tech Mono',monospace;
      font-size:12px; letter-spacing:0.5px; outline:none; border-radius:2px;
    }
    #input:focus { border-color:var(--cyan); box-shadow:0 0 8px rgba(0,229,255,0.1); }
    #input::placeholder { color:var(--text-dim); }
    #send {
      flex-shrink:0; background:transparent; border:1px solid var(--cyan); color:var(--cyan);
      padding:10px 12px; font-family:'Orbitron',monospace; font-size:9px;
      letter-spacing:2px; cursor:pointer; border-radius:2px; transition:all 0.2s; white-space:nowrap;
    }
    #send:hover { background:rgba(0,229,255,0.1); box-shadow:0 0 12px rgba(0,229,255,0.2); }
    #send:disabled { opacity:0.3; cursor:not-allowed; }
    #mic {
      flex-shrink:0; background:transparent; border:1px solid var(--border); color:var(--text-dim);
      padding:10px 11px; font-size:15px; cursor:pointer; border-radius:2px; transition:all 0.2s;
    }
    #mic:hover { border-color:var(--orange); color:var(--orange); }
    #mic.recording  { border-color:#ff3344; color:#ff3344; background:rgba(255,51,68,0.08); animation:blink 0.5s infinite; }
    #mic.processing { border-color:var(--orange); color:var(--orange); }
    #upload-btn {
      flex-shrink:0; background:transparent; border:1px solid var(--border); color:var(--text-dim);
      padding:10px 11px; font-size:15px; cursor:pointer; border-radius:2px;
      transition:all 0.2s; display:flex; align-items:center; line-height:1;
    }
    #upload-btn:hover { border-color:var(--cyan); color:var(--cyan); }
    #upload-btn.has-image { border-color:#00ff88; color:#00ff88; }
    #img-input { display:none; }
    /* media library (left) and viewer (center, lower half) */
    #core-area { flex: 1 1 54%; min-height: 0; width: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; overflow: hidden; }
    #viewer-panel { flex: 0 0 44%; min-height: 0; width: 100%; border-top: 1px solid var(--border); padding: 10px 14px; overflow-y: auto; font-size: 11px; position: relative; z-index: 2; background: var(--panel); }
    #library-block { flex: 1; min-height: 0; display: flex; flex-direction: column; border-bottom: 1px solid var(--border); padding: 12px 12px 8px; }
    .lib-filters { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 8px; }
    .lib-f, .v-btn { background: transparent; border: 1px solid var(--border); color: var(--text-dim); padding: 4px 7px; font-family: 'Share Tech Mono', monospace; font-size: 10px; letter-spacing: 1px; cursor: pointer; border-radius: 2px; }
    .lib-f.active, .lib-f:hover, .v-btn:hover { border-color: var(--cyan); color: var(--cyan); }
    .v-btn.danger:hover { border-color: #ff3344; color: #ff3344; }
    #lib-list { flex: 1; min-height: 0; overflow-y: auto; scrollbar-width: thin; }
    .lib-row { display: flex; align-items: center; gap: 8px; padding: 5px 4px; cursor: pointer; border-bottom: 1px solid rgba(0,229,255,0.05); }
    .lib-row:hover, .lib-row.sel { background: rgba(0,229,255,0.07); }
    .lib-thumb { width: 30px; height: 30px; object-fit: cover; border-radius: 2px; border: 1px solid var(--border); flex-shrink: 0; }
    .lib-icon { width: 30px; height: 30px; display: flex; align-items: center; justify-content: center; border: 1px solid var(--border); border-radius: 2px; flex-shrink: 0; color: var(--orange); }
    .lib-t { font-size: 10px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .lib-k { font-size: 9px; color: var(--text-dim); letter-spacing: 1px; text-transform: uppercase; }
    .lib-note { font-size: 10px; color: var(--text-dim); padding: 6px 2px; }
    .v-head { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
    .v-title { color: var(--cyan); font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .v-meta { color: var(--text-dim); white-space: nowrap; }
    .v-stage { display: flex; justify-content: center; margin-bottom: 8px; }
    .v-stage img { max-width: 100%; max-height: 150px; border: 1px solid var(--border); }
    .v-stage audio { width: 100%; }
    .v-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 6px; }
    .v-row label { color: var(--text-dim); font-size: 10px; }
    .v-row input[type=number], .v-row input[type=text], .v-row select { background: rgba(0,229,255,0.03); border: 1px solid var(--border); color: var(--text); padding: 3px 5px; font-family: inherit; font-size: 11px; width: 70px; }
    .v-row input[type=text] { width: 100%; flex: 1; }
    .v-row input[type=range] { width: 150px; }
    .v-status { color: #00ff88; font-size: 10px; min-height: 14px; }
    .v-status.err { color: #ff3344; }
    .saved-open { margin-left: 8px; }
    @media (max-width: 768px) {
      body { grid-template-rows: 40px 1fr; overflow: hidden; height: 100dvh; }
      #hud-header { padding: 0 12px; }
      #hud-title { font-size: 13px; letter-spacing: 2px; }
      .hud-meta { gap: 8px; }
      .hud-extra { display: none; }
      #operator-name { white-space: nowrap; }
      #hud-main { grid-template-columns: 1fr; height: 100%; overflow: hidden; }
      #panel-left, #panel-center { display: none; }
      #panel-right { border-left: none; width: 100%; height: 100%; display: flex; flex-direction: column; overflow: hidden; }
      #messages { flex: 1; min-height: 0; padding: 10px; overflow-y: auto; }
      #input-area { flex-shrink: 0; }
      .msg { max-width: 96%; font-size: 13px; }
      #input-row { padding: 8px 10px; gap: 5px; }
      #input { font-size: 14px; padding: 10px 10px; }
      #send { padding: 10px 10px; font-size: 8px; letter-spacing: 1px; }
      #mic, #upload-btn { padding: 10px 10px; font-size: 14px; }
      #auth-box { width: 92vw; padding: 28px 20px; }
      #input-area { padding-bottom: env(safe-area-inset-bottom); }
      #mob-lib-btn { display: none; }
      #mob-lib-btn.on { display: inline-block; }
      #hud-main.mob-lib #panel-right { display: none; }
      #hud-main.mob-lib #panel-left { display: flex; position: fixed; top: 40px; bottom: 0; left: 0; right: 0; z-index: 40; background: var(--bg); flex-direction: column; overflow: hidden; }
      #hud-main.mob-lib #panel-left .sys-block, #hud-main.mob-lib #live-feed { display: none; }
      #hud-main.mob-lib #library-block { display: flex; flex: 1; min-height: 0; border-bottom: 1px solid var(--border); }
      #hud-main.mob-lib #mob-lib-close { display: inline-block; }
      #hud-main.mob-lib #panel-center { display: none; }
      #hud-main.mob-lib.has-viewer #panel-left { bottom: auto; height: calc(50dvh - 40px); }
      #hud-main.mob-lib.has-viewer #panel-center { display: flex; flex-direction: column; position: fixed; top: 50dvh; bottom: 0; left: 0; right: 0; z-index: 40; background: var(--bg); }
      #hud-main.mob-lib #core-area { display: none; }
      #hud-main.mob-lib #viewer-panel { flex: 1 1 auto; border-top: none; }
    }
    #mob-lib-btn, #mob-lib-close { font-family: 'Share Tech Mono', monospace; font-size: 10px; letter-spacing: 1px; color: var(--cyan); background: transparent; border: 1px solid var(--cyan-dim); padding: 4px 8px; border-radius: 2px; cursor: pointer; }
    #mob-lib-close { display: none; margin-left: auto; }
  </style>
</head>
<body>

  <div id="auth-overlay">
    <div id="auth-box">
      <div class="auth-corner tl"></div>
      <div class="auth-corner tr"></div>
      <div class="auth-corner bl"></div>
      <div class="auth-corner br"></div>
      <div id="auth-title">AIBUS<span>SOL</span></div>
      <div id="auth-mode-label">OPERATOR AUTHENTICATION</div>
      <div class="auth-field" id="auth-display-wrap">
        <label>Display Name</label>
        <input id="auth-display" type="text" placeholder="e.g. Michael" autocomplete="name"/>
      </div>
      <div class="auth-field" id="auth-invite-wrap">
        <label>Invite Code</label>
        <input id="auth-invite" type="password" placeholder="required to register" autocomplete="off"/>
      </div>
      <div class="auth-field">
        <label>Email</label>
        <input id="auth-email" type="email" placeholder="[email]" autocomplete="email"/>
      </div>
      <div class="auth-field">
        <label>Password</label>
        <input id="auth-password" type="password" placeholder="••••••••" autocomplete="current-password"/>
      </div>
      <div id="auth-error"></div>
      <button id="auth-btn">ACCESS SYSTEM</button>
      <div id="auth-toggle">
        <span id="auth-toggle-text">NO ACCESS YET? </span>
        <span id="auth-toggle-link" onclick="toggleAuthMode()">REGISTER</span>
      </div>
    </div>
  </div>

  <div id="hud-header" class="hud-hidden">
    <div id="hud-title">AIBUS<span>SOL</span></div>
    <div class="hud-meta">
      <span>OPERATOR: <span class="v" id="operator-name">—</span></span>
      <span class="hud-extra">SESSION: <span class="v">WEB-01</span></span>
      <span class="hud-extra">UPTIME: <span class="v" id="uptime">00:00:00</span></span>
      <button id="station-btn" onclick="location.href='/station'" title="Open the Station: your crew, live" hidden>STATION<span id="station-badge" hidden></span></button>
      <button id="sub-btn" onclick="subscribe()" hidden>SUBSCRIBE</button>
      <button id="logout-btn" onclick="logout()">LOGOUT</button>
    </div>
  </div>

  <div id="hud-main" class="hud-hidden">

    <div id="panel-left">
      <div class="sys-block">
        <div class="p-label">System Vitals</div>
        <div class="stat-row"><span class="lbl">STATUS</span><span class="val g">NOMINAL</span></div>
        <div class="stat-row"><span class="lbl">MEMORY</span><span class="val">ACTIVE</span></div>
        <div class="stat-row"><span class="lbl">ENGINE</span><span class="val engine-cyan" id="engine-val">GPT-4O</span></div>
        <div class="stat-row"><span class="lbl">VOICE</span><span class="val" id="voice-status">READY</span></div>
        <div class="stat-row"><span class="lbl">PROTOCOL</span><span class="val d">JSON-RPC</span></div>
      </div>
      <div id="library-block" style="display:none">
        <div style="display:flex;align-items:center;"><div class="p-label">Library</div><button id="mob-lib-close">CLOSE</button></div>
        <div class="lib-filters">
          <button class="lib-f active" data-kind="all">All</button>
          <button class="lib-f" data-kind="image">Images</button>
          <button class="lib-f" data-kind="sound">Sound</button>
          <button class="lib-f" data-kind="video" title="Video is not available">Video</button>
        </div>
        <div id="lib-list"></div>
      </div>
      <div id="live-feed">
        <div class="p-label">Live Feed</div>
        <div class="feed-line">&#9656; Aibussol core initialized</div>
        <div class="feed-line">&#9656; Neon DB connected</div>
        <div class="feed-line">&#9656; Voice engine loaded</div>
        <div class="feed-line" id="feed-last">&#9656; Awaiting input...</div>
      </div>
    </div>

    <div id="panel-center">
      <div class="corner tl"></div><div class="corner tr"></div>
      <div class="corner bl"></div><div class="corner br"></div>
      <div id="core-area">
      <div class="face-label">CORE INTERFACE</div>
      <div id="face-wrap">
        <div class="f-ring"></div>
        <div class="f-ring2"></div>
        <div id="atlas-face">
          <canvas id="atlas-canvas" style="position:absolute;top:0;left:0;width:100%;height:100%;"></canvas>
          <svg id="atlas-face-svg" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg"></svg>
        </div>
      </div>
      <div class="face-state">
        <div id="atlas-state">STANDBY</div>
        <div>&#9656; <span id="center-sub">WAITING FOR INPUT</span></div>
      </div>
      </div>
      <div id="viewer-panel" style="display:none"></div>
    </div>

    <div id="panel-right">
      <div id="chat-hdr">
        <div class="p-label" style="margin:0">Command Console</div>
        <div style="display:flex;align-items:center;gap:10px;">
          <button id="mob-lib-btn" title="Media library">LIBRARY</button>
          <div id="wake-ind">
          <div id="wake-dot"></div>
          <span id="wake-label">INITIALIZING</span>
          </div>
        </div>
      </div>
      <div id="messages"></div>
      <div id="input-area">
        <div id="img-preview-bar">
          <img id="img-preview-thumb" src="" alt="preview"/>
          <span id="img-preview-name"></span>
          <span class="clear-img" id="clear-img" title="Remove image">✕</span>
        </div>
        <div id="input-row">
          <button id="mic" title="Record">&#127897;</button>
          <label id="upload-btn" for="img-input" title="Attach image">📎</label>
          <input id="img-input" type="file" accept="image/*"/>
          <input id="input" type="text" placeholder='Type, or say "Hey Helix"...' autocomplete="off"/>
          <button id="send">SEND</button>
        </div>
      </div>
    </div>

  </div>

  <script>
    const TOKEN_KEY = 'aibussol_token';
    function getToken()       { return localStorage.getItem(TOKEN_KEY); }
    function setToken(t)      { localStorage.setItem(TOKEN_KEY, t); }
    function clearToken()     { localStorage.removeItem(TOKEN_KEY); }
    function authHeader()     { return { 'Authorization': 'Bearer ' + getToken() }; }
    function jsonAuthHeader() { return { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + getToken() }; }

    function parseJwt(token) {
      try {
        const b64 = token.split('.')[1].replace(/-/g,'+').replace(/_/g,'/');
        return JSON.parse(atob(b64));
      } catch { return {}; }
    }

    let _audioCtxUnlocked = false;
    function unlockAudio() {
      if (_audioCtxUnlocked) return;
      _audioCtxUnlocked = true;
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        ctx.resume().then(() => ctx.close()).catch(() => {});
      } catch(e) {}
    }

    let _helixAudioCtx = null;
    let authMode = 'login';

    function toggleAuthMode() {
      authMode = authMode === 'login' ? 'register' : 'login';
      document.getElementById('auth-mode-label').textContent =
        authMode === 'login' ? 'OPERATOR AUTHENTICATION' : 'NEW OPERATOR REGISTRATION';
      document.getElementById('auth-btn').textContent =
        authMode === 'login' ? 'ACCESS SYSTEM' : 'CREATE ACCOUNT';
      document.getElementById('auth-toggle-text').textContent =
        authMode === 'login' ? 'NO ACCESS YET? ' : 'ALREADY REGISTERED? ';
      document.getElementById('auth-toggle-link').textContent =
        authMode === 'login' ? 'REGISTER' : 'LOGIN';
      document.getElementById('auth-display-wrap').style.display =
        authMode === 'register' ? 'flex' : 'none';
      document.getElementById('auth-invite-wrap').style.display =
        authMode === 'register' ? 'flex' : 'none';
      document.getElementById('auth-error').textContent = '';
    }

    document.getElementById('auth-btn').addEventListener('click', handleAuth);
    document.getElementById('auth-password').addEventListener('keydown', e => {
      if (e.key === 'Enter') handleAuth();
    });

    async function handleAuth() {
      const email    = document.getElementById('auth-email').value.trim();
      const password = document.getElementById('auth-password').value;
      const errorEl  = document.getElementById('auth-error');
      const btn      = document.getElementById('auth-btn');
      if (!email || !password) { errorEl.textContent = 'EMAIL AND PASSWORD REQUIRED'; return; }
      errorEl.textContent = '';
      btn.disabled = true;
      btn.textContent = 'CONNECTING...';
      const endpoint = authMode === 'login' ? '/api/auth/login' : '/api/auth/register';
      const body = authMode === 'login'
        ? { email, password }
        : { email, password, invite_code: document.getElementById('auth-invite').value.trim(), display_name: document.getElementById('auth-display').value.trim() || email.split('@')[0] };
      try {
        const res  = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const data = await res.json();
        if (res.ok && data.token) {
          setToken(data.token);
          showHUD();
        } else {
          errorEl.textContent = (data.detail || 'AUTHENTICATION FAILED').toUpperCase();
        }
      } catch(e) {
        errorEl.textContent = 'CONNECTION ERROR';
      }
      btn.disabled = false;
      btn.textContent = authMode === 'login' ? 'ACCESS SYSTEM' : 'CREATE ACCOUNT';
    }

    function logout() { clearToken(); location.reload(); }

    async function refreshSubscription() {
      const btn = document.getElementById('sub-btn');
      try {
        const r = await fetch('/api/billing/status', { headers: { 'Authorization': 'Bearer ' + getToken() } });
        if (!r.ok) return;
        const d = await r.json();
        btn.hidden = !d.enabled;
        btn.disabled = !!d.active;
        btn.textContent = d.active ? 'SUBSCRIBED' : 'SUBSCRIBE';
      } catch (e) { btn.hidden = true; }
    }
    async function subscribe() {
      const btn = document.getElementById('sub-btn');
      btn.disabled = true; btn.textContent = 'OPENING...';
      try {
        const r = await fetch('/api/billing/checkout', { method: 'POST', headers: jsonAuthHeader(), body: '{}' });
        const d = await r.json().catch(() => ({}));
        if (r.ok && d.url) { window.location.href = d.url; return; }
        btn.textContent = (d && d.detail) ? d.detail.slice(0, 40).toUpperCase() : 'UNAVAILABLE';
      } catch (e) { btn.textContent = 'NO CONNECTION'; }
      setTimeout(() => { btn.disabled = false; btn.textContent = 'SUBSCRIBE'; }, 4000);
    }

    let _uptimeStarted = false;
    function startUptimeClock() {
      if (_uptimeStarted) return; _uptimeStarted = true;
      const t0 = Date.now();
      setInterval(() => {
        const s  = Math.floor((Date.now() - t0) / 1000);
        const h  = String(Math.floor(s / 3600)).padStart(2,'0');
        const m  = String(Math.floor((s % 3600) / 60)).padStart(2,'0');
        const sc = String(s % 60).padStart(2,'0');
        document.getElementById('uptime').textContent = h+':'+m+':'+sc;
      }, 1000);
    }

    function showHUD() {
      document.getElementById('auth-overlay').style.display = 'none';
      document.getElementById('hud-header').classList.remove('hud-hidden');
      document.getElementById('hud-main').classList.remove('hud-hidden');
      const payload = parseJwt(getToken());
      const email   = payload.email || '';
      document.getElementById('operator-name').textContent =
        email ? email.split('@')[0].toUpperCase() : 'OPERATOR';
      loadHistory();
      loadLibrary().then(renderViewer);
      refreshSubscription();
      checkBriefs();
      if (!_briefTimer) _briefTimer = setInterval(checkBriefs, 60000);
      checkCrew();
      if (!_crewTimer) _crewTimer = setInterval(checkCrew, 20000);
      startUptimeClock();
    }

    if (getToken()) { showHUD(); }

    const messagesEl      = document.getElementById('messages');
    const inputEl         = document.getElementById('input');
    const sendBtn         = document.getElementById('send');
    const micBtn          = document.getElementById('mic');
    const wakeDot         = document.getElementById('wake-dot');
    const wakeLabel       = document.getElementById('wake-label');
    const atlasFace       = document.getElementById('atlas-face');
    const atlasState      = document.getElementById('atlas-state');
    const centerSub       = document.getElementById('center-sub');
    const voiceStat       = document.getElementById('voice-status');
    const imgInput        = document.getElementById('img-input');
    const imgPreviewBar   = document.getElementById('img-preview-bar');
    const imgPreviewThumb = document.getElementById('img-preview-thumb');
    const imgPreviewName  = document.getElementById('img-preview-name');
    const clearImgBtn     = document.getElementById('clear-img');
    const uploadBtn       = document.getElementById('upload-btn');

    let pendingImageB64 = null, pendingImageDataUrl = null;

    function clearPendingImage() {
      pendingImageB64 = null; pendingImageDataUrl = null;
      imgPreviewBar.style.display = 'none';
      imgPreviewThumb.src = ''; imgPreviewName.textContent = '';
      uploadBtn.classList.remove('has-image');
      imgInput.value = '';
    }

    imgInput.addEventListener('change', () => {
      const file = imgInput.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        pendingImageDataUrl = e.target.result;
        pendingImageB64 = e.target.result.split(',')[1];
        imgPreviewThumb.src = pendingImageDataUrl;
        imgPreviewName.textContent = file.name.slice(0, 20);
        imgPreviewBar.style.display = 'flex';
        uploadBtn.classList.add('has-image');
      };
      reader.readAsDataURL(file);
    });

    clearImgBtn.addEventListener('click', clearPendingImage);

    function setTalking(on) {
      window._helixTalking = on;
      if (on) {
        atlasFace.classList.add('talking');
        atlasState.textContent = 'SPEAKING'; atlasState.style.color = 'var(--orange)';
        centerSub.textContent  = 'TRANSMITTING AUDIO'; voiceStat.textContent = 'SPEAKING';
      } else {
        atlasFace.classList.remove('talking');
        atlasState.textContent = 'STANDBY'; atlasState.style.color = 'var(--cyan)';
        centerSub.textContent  = 'WAITING FOR INPUT'; voiceStat.textContent = 'READY';
      }
    }

    function setWakeStatus(state) {
      wakeDot.className = '';
      if (state === 'listening') {
        wakeDot.classList.add('listening'); wakeLabel.textContent = 'LISTENING';
        centerSub.textContent = 'SAY "HEY HELIX" TO WAKE';
        atlasState.textContent = 'ACTIVE'; atlasState.style.color = 'var(--cyan)';
      } else if (state === 'recording') {
        wakeDot.classList.add('recording'); wakeLabel.textContent = 'RECORDING';
        centerSub.textContent = 'RECORDING — SAY "SEND" OR TAP MIC';
        atlasState.textContent = 'LISTENING'; atlasState.style.color = '#ff3344';
      } else if (state === 'processing') {
        wakeDot.classList.add('processing'); wakeLabel.textContent = 'PROCESSING';
        centerSub.textContent = 'PROCESSING AUDIO';
        atlasState.textContent = 'PROCESSING'; atlasState.style.color = 'var(--orange)';
      } else if (state === 'unsupported') {
        wakeLabel.textContent = 'UNAVAILABLE';
      } else {
        wakeLabel.textContent = 'OFFLINE';
      }
    }

    function feedLog(text) {
      const feed = document.getElementById('live-feed');
      const d = document.createElement('div');
      d.className = 'feed-line'; d.textContent = '\u25b8 ' + text; d.style.color = 'var(--cyan)';
      feed.appendChild(d);
      const lines = feed.querySelectorAll('.feed-line');
      lines.forEach((l, i) => {
        const age = lines.length - i;
        l.style.opacity = String(Math.max(0.1, 0.85 - age * 0.1));
        l.style.color = age > 4 ? 'var(--text-dim)' : 'var(--text)';
      });
      if (lines.length > 12) lines[0].remove();
    }

    function addMsg(role, text, imageDataUrl) {
      const wrap = document.createElement('div');
      wrap.className = 'msg ' + (role === 'user' ? 'user' : 'atlas');
      const tag = document.createElement('div');
      tag.className = 'msg-tag';
      tag.textContent = role === 'user' ? 'YOU' : 'HELIX';
      const body = document.createElement('div');
      body.textContent = text;
      wrap.appendChild(tag); wrap.appendChild(body);
      if (imageDataUrl) {
        const thumb = document.createElement('div');
        thumb.className = 'msg-thumb';
        const img = document.createElement('img');
        img.src = imageDataUrl; img.alt = 'attached image';
        thumb.appendChild(img); wrap.appendChild(thumb);
      }
      messagesEl.appendChild(wrap);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return wrap;
    }

    async function loadHistory() {
      try {
        const res  = await fetch('/api/session/history', { headers: authHeader() });
        if (res.status === 401) { return; }
        const data = await res.json();
        (data.messages || []).forEach(m => addMsg(m.role, m.content, null));
        if ((data.messages || []).length) feedLog('Session history loaded');
        else feedLog('New session started');
      } catch(e) { console.warn('History load failed:', e.message); }
    }

    function updateEngineIndicator(modelDisplay, modelColor) {
      const el  = document.getElementById('engine-val');
      const prev = el.textContent;
      el.textContent = modelDisplay;
      el.className = 'val engine-' + (modelColor || 'cyan');
      if (prev !== modelDisplay) {
        el.classList.add('flash');
        el.addEventListener('animationend', () => el.classList.remove('flash'), { once: true });
      }
    }

    // ── Media library (left sidebar) and viewer (center, lower half) ──────────────────────────
    const lib = { kind: 'all', items: [], selected: null, on: false, estimates: null, preview: null };
    const libList = document.getElementById('lib-list');
    const viewerEl = document.getElementById('viewer-panel');
    function libApi(path, method, body) {
      return fetch(path, { method: method || 'GET', headers: jsonAuthHeader(), body: body ? JSON.stringify(body) : undefined })
        .then(async r => ({ ok: r.ok, status: r.status, data: await r.json().catch(() => ({})) }));
    }
    function mk(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
    function fmtBytes(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB'; }
    function money(x) { return '$' + Number(x).toFixed(4); }

    async function loadLibrary() {
      const st = await libApi('/api/media/status').catch(() => null);
      lib.on = !!(st && st.data && st.data.enabled);
      lib.estimates = (st && st.data && st.data.estimates) || null;
      document.getElementById('library-block').style.display = lib.on ? '' : 'none';
      document.getElementById('mob-lib-btn').classList.toggle('on', lib.on);
      if (!lib.on) { viewerEl.style.display = 'none'; return; }
      viewerEl.style.display = '';
      const r = await libApi('/api/media/library' + (lib.kind === 'all' ? '' : '?kind=' + lib.kind));
      lib.items = r.ok ? (r.data.items || []) : [];
      renderLibrary(r.ok ? '' : (r.data.detail || 'The library is unavailable.'));
    }
    document.getElementById('mob-lib-btn').addEventListener('click', () => {
      document.getElementById('hud-main').classList.add('mob-lib');
      loadLibrary();
    });
    // The library list fills the screen until an item is open; then the viewer takes the lower half.
    new MutationObserver(() => document.getElementById('hud-main').classList.toggle('has-viewer',
      viewerEl.childElementCount > 0 && viewerEl.style.display !== 'none'))
      .observe(viewerEl, { attributes: true, childList: true, attributeFilter: ['style'] });
    document.getElementById('mob-lib-close').addEventListener('click', () => {
      document.getElementById('hud-main').classList.remove('mob-lib');
    });
    function renderLibrary(note) {
      libList.textContent = '';
      if (note) libList.appendChild(mk('div', 'lib-note', note));
      if (!note && !lib.items.length) libList.appendChild(mk('div', 'lib-note', lib.kind === 'video' ? 'Video is not available.' : 'Nothing saved yet. Ask Helix to make something.'));
      lib.items.forEach(it => {
        const row = mk('div', 'lib-row' + (lib.selected && lib.selected.id === it.id ? ' sel' : ''));
        if (it.kind === 'image') { const im = mk('img', 'lib-thumb'); im.src = it.url; im.alt = ''; row.appendChild(im); }
        else row.appendChild(mk('span', 'lib-icon', it.kind === 'sound' ? '\u266a' : '\u25b6'));
        const box = mk('div'); box.style.minWidth = '0';
        box.appendChild(mk('div', 'lib-t', it.title)); box.appendChild(mk('div', 'lib-k', it.kind));
        row.appendChild(box);
        row.addEventListener('click', () => openItem(it.id));
        libList.appendChild(row);
      });
    }
    document.querySelectorAll('.lib-f').forEach(b => b.addEventListener('click', () => {
      document.querySelectorAll('.lib-f').forEach(x => x.classList.toggle('active', x === b));
      lib.kind = b.dataset.kind; loadLibrary();
    }));

    async function openItem(id) {
      const r = await libApi('/api/media/items/' + encodeURIComponent(id));
      if (!r.ok) { setViewerStatus(r.data.detail || 'That item could not be opened.', true); return; }
      lib.selected = r.data.item; lib.preview = null;
      renderViewer(); renderLibrary('');
    }
    function setViewerStatus(msg, bad) { const s = document.getElementById('v-status'); if (s) { s.textContent = msg; s.className = 'v-status' + (bad ? ' err' : ''); } }

    const OPS = {
      image: { crop: ['left', 'top', 'right', 'bottom'], resize: ['width', 'height'], brightness: ['factor'], contrast: ['factor'] },
      sound: { trim: ['start', 'end'], fade_in: ['seconds'], fade_out: ['seconds'], volume: ['factor'] },
    };
    function renderViewer() {
      const v = viewerEl; v.textContent = '';
      const it = lib.selected;
      if (!it) { v.appendChild(mk('div', 'lib-note', 'Select an item in the Library, or ask Helix to make one.')); return; }
      const head = mk('div', 'v-head');
      head.appendChild(mk('span', 'v-title', it.title));
      head.appendChild(mk('span', 'v-meta', it.kind + ' \u00b7 ' + fmtBytes(it.size_bytes) + (it.duration_seconds ? ' \u00b7 ' + it.duration_seconds + 's' : '')));
      v.appendChild(head);
      const stage = mk('div', 'v-stage');
      const media = document.createElement(it.kind === 'image' ? 'img' : 'audio');
      media.id = 'v-media'; if (it.kind === 'sound') media.controls = true;
      media.src = it.url; stage.appendChild(media); v.appendChild(stage);
      if (it.kind !== 'image' && it.kind !== 'sound') return;

      const ops = OPS[it.kind];
      const row1 = mk('div', 'v-row');
      const sel = document.createElement('select');
      Object.keys(ops).forEach(o => { const op = document.createElement('option'); op.value = o; op.textContent = o.replace('_', ' '); sel.appendChild(op); });
      row1.appendChild(mk('label', null, 'Edit')); row1.appendChild(sel); v.appendChild(row1);
      const params = mk('div', 'v-row'); v.appendChild(params);
      let inputs = {};
      function buildParams() {
        params.textContent = ''; inputs = {};
        ops[sel.value].forEach(name => {
          const lab = mk('label', null, name);
          const inp = document.createElement('input');
          if (name === 'factor' && (sel.value === 'brightness' || sel.value === 'contrast')) { inp.type = 'range'; inp.min = '0.1'; inp.max = '3'; inp.step = '0.05'; inp.value = '1'; }
          else { inp.type = 'number'; inp.step = 'any'; }
          const defaults = { start: '0', end: it.duration_seconds ? String(it.duration_seconds) : '', seconds: '1', factor: '1' };
          if (name in defaults && inp.type === 'number') inp.value = defaults[name];
          if (it.kind === 'image' && sel.value === 'crop' && media.naturalWidth) { inp.value = { left: 0, top: 0, right: media.naturalWidth, bottom: media.naturalHeight }[name]; }
          if (it.kind === 'image' && sel.value === 'resize' && media.naturalWidth) { inp.value = name === 'width' ? media.naturalWidth : media.naturalHeight; }
          params.appendChild(lab); params.appendChild(inp);
          if (inp.type === 'range') { const val = mk('span', 'v-meta', inp.value); inp.addEventListener('input', () => { val.textContent = inp.value; }); params.appendChild(val); }
          inputs[name] = inp;
        });
      }
      sel.addEventListener('change', buildParams); buildParams();
      if (it.kind === 'image') media.addEventListener('load', buildParams, { once: true });
      function collect() { const p = {}; Object.keys(inputs).forEach(k => { if (inputs[k].value !== '') p[k] = Number(inputs[k].value); }); return p; }

      const row2 = mk('div', 'v-row');
      const bPrev = mk('button', 'v-btn', 'Preview'), bSave = mk('button', 'v-btn', 'Save as new'), bDisc = mk('button', 'v-btn', 'Discard');
      bSave.style.display = bDisc.style.display = 'none';
      row2.append(bPrev, bSave, bDisc); v.appendChild(row2);
      bPrev.addEventListener('click', async () => {
        setViewerStatus('Making a preview...');
        const r = await libApi('/api/media/items/' + it.id + '/edit', 'POST', { operation: sel.value, params: collect(), preview: true });
        if (!r.ok) { setViewerStatus(r.data.detail || 'That edit could not be previewed.', true); return; }
        lib.preview = { id: it.id, op: sel.value, params: collect() };
        media.src = 'data:' + r.data.preview.mime + ';base64,' + r.data.preview.data_base64;
        bSave.style.display = bDisc.style.display = ''; setViewerStatus('Preview only. Nothing is saved yet.');
      });
      bDisc.addEventListener('click', () => { lib.preview = null; media.src = it.url; bSave.style.display = bDisc.style.display = 'none'; setViewerStatus(''); });
      bSave.addEventListener('click', async () => {
        if (!lib.preview) return;
        const r = await libApi('/api/media/items/' + it.id + '/edit', 'POST', { operation: lib.preview.op, params: lib.preview.params, preview: false });
        if (!r.ok) { setViewerStatus(r.data.detail || 'Could not save the edit.', true); return; }
        await loadLibrary(); await openItem(r.data.item.id); setViewerStatus('Saved as a new item: ' + r.data.item.title);
      });

      const row3 = mk('div', 'v-row');
      const prompt = document.createElement('input'); prompt.type = 'text'; prompt.value = it.prompt || ''; prompt.maxLength = 500; prompt.placeholder = it.kind === 'sound' ? 'Text to speak' : 'Describe the image';
      row3.appendChild(prompt); v.appendChild(row3);
      const row4 = mk('div', 'v-row');
      const bDown = mk('button', 'v-btn', 'Download'), bRegen = mk('button', 'v-btn', 'Re-generate'), bDel = mk('button', 'v-btn danger', 'Delete');
      row4.append(bDown, bRegen, bDel); v.appendChild(row4);
      v.appendChild(Object.assign(mk('div', 'v-status'), { id: 'v-status' }));
      bDown.addEventListener('click', async () => {
        const r = await libApi('/api/media/items/' + it.id + '?download=true');
        if (r.ok) window.open(r.data.item.url, '_blank', 'noopener'); else setViewerStatus(r.data.detail || 'Could not download.', true);
      });
      bRegen.addEventListener('click', async () => {
        const text = prompt.value.trim(); if (!text) { setViewerStatus('Write a prompt first.', true); return; }
        const est = lib.estimates ? (it.kind === 'image' ? lib.estimates.image : lib.estimates.sound_per_1000_chars * Math.max(text.length, 1) / 1000) : null;
        if (!confirm('Re-generate from this prompt?' + (est != null ? '\nEstimated cost about ' + money(est) + ' (counts against your daily media budget).' : '') + '\nThe original stays in your library.')) return;
        setViewerStatus('Generating...');
        const r = await libApi('/api/media/items/' + it.id + '/regenerate', 'POST', { prompt: text });
        if (!r.ok) { setViewerStatus(r.data.detail || 'That could not be made.', true); return; }
        await loadLibrary(); await openItem(r.data.item.id); setViewerStatus('Saved: ' + r.data.item.title);
      });
      bDel.addEventListener('click', async () => {
        if (!confirm('Delete "' + it.title + '"? This removes the file for good.')) return;
        const r = await libApi('/api/media/items/' + it.id, 'DELETE');
        if (!r.ok) { setViewerStatus(r.data.detail || 'Could not delete.', true); return; }
        lib.selected = null; await loadLibrary(); renderViewer();
      });
    }
    // chat: one line "Saved: <title>" with a button that opens the item in the panel (the file itself is never put in the chat)
    function addOpenButton(wrap, itemId) {
      const b = mk('button', 'v-btn saved-open', 'Open'); b.addEventListener('click', () => openItem(itemId)); wrap.appendChild(b);
    }
    function handleUiActions(actions, wrap) {
      (actions || []).forEach(a => {
        if (a.type === 'saved' && a.item) { addOpenButton(wrap, a.item.id); loadLibrary(); }
        else if (a.type === 'open_item' && a.id) openItem(a.id);
        else if (a.type === 'crew_refresh') setTimeout(checkCrew, 1500);
      });
    }

    // ── Briefs: each new scheduled brief appears in the chat with its full text and a Listen button ──
    const BRIEF_SEEN_KEY = 'aibussol_seen_briefs';
    function seenBriefs() { try { return JSON.parse(localStorage.getItem(BRIEF_SEEN_KEY) || '[]'); } catch (e) { return []; } }
    function markBriefSeen(id) {
      try { const s = seenBriefs(); if (!s.includes(id)) { s.push(id); localStorage.setItem(BRIEF_SEEN_KEY, JSON.stringify(s.slice(-60))); } } catch (e) { /* the list is only a convenience */ }
    }
    // The greeting comes from the time the brief was made, in Los Angeles: morning 05:00-11:59, afternoon 12:00-16:59, evening 17:00-04:59.
    // (The scheduled slot's name is never used: a "morning" brief made at 10:30 PM still says "Good evening".)
    function briefGreetingPeriod(hour) { return hour >= 5 && hour < 12 ? 'morning' : (hour >= 12 && hour < 17 ? 'afternoon' : 'evening'); }
    function briefGreeting(iso) {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return '';
      const h = parseInt(new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles', hour: 'numeric', hourCycle: 'h23' }).format(d), 10) % 24;
      return 'Good ' + briefGreetingPeriod(h) + ', Michael.';
    }
    function addBriefMessage(b) {
      const wrap = mk('div', 'msg atlas');
      wrap.appendChild(mk('div', 'msg-tag', 'HELIX \u00b7 ' + String(b.period).toUpperCase() + ' BRIEF \u00b7 ' + b.product + ' \u00b7 ' + new Date(b.created_at).toLocaleString()));
      const plain = String(b.body || '').replace(/^\s*(?:#+\s*)?good\s+(?:morning|afternoon|evening|night)[^\n]*\n*/i, '');     // a greeting an older brief was stored with is dropped
      const body = mk('div', null, b.body); body.style.whiteSpace = 'pre-wrap';
      body.textContent = briefGreeting(b.created_at) + '\n\n' + plain;                 // plain text, never HTML
      wrap.appendChild(body);
      const row = mk('div'); row.style.marginTop = '8px';
      const listen = mk('button', 'v-btn', 'Listen'); const note = mk('span', 'v-meta'); note.style.marginLeft = '8px';
      listen.addEventListener('click', async () => {
        listen.disabled = true; note.textContent = ''; note.style.color = '';
        const r = await libApi('/api/briefs/' + encodeURIComponent(b.id) + '/listen', 'POST');
        if (!r.ok || !r.data.text) { note.textContent = r.data.detail || 'This brief could not be played right now.'; note.style.color = '#ff3344'; }
        else { note.textContent = r.data.remaining + ' listens left today'; speakReply(r.data.text, wrap); }   // the spoken summary only, never the full text
        listen.disabled = false;
      });
      row.append(listen, note); wrap.appendChild(row);
      messagesEl.appendChild(wrap); messagesEl.scrollTop = messagesEl.scrollHeight;
    }
    async function checkBriefs() {
      try {
        const r = await fetch('/api/briefs/latest', { headers: authHeader() });
        if (!r.ok) return;
        const d = await r.json();
        const fresh = (d.items || []).filter(b => !seenBriefs().includes(b.id)).reverse();      // oldest first
        fresh.forEach(b => { addBriefMessage(b); markBriefSeen(b.id); });
        if (fresh.length) feedLog('New brief: ' + fresh[fresh.length - 1].period);
      } catch (e) { /* try again at the next check */ }
    }
    var _briefTimer = null;      // var: showHUD() runs before this line during page load

    // ── Crew: questions, approvals and budget requests from the agents arrive here as cards; STATION opens the live view ──
    // Approve / Decline are buttons on purpose: a checkpoint is never approved by typing to the model.
    var _crewTimer;              // no initializer: showHUD() may already have set it
    const crewShown = new Map();     // open question id -> its card
    function addCrewCard(q, missions) {
      const m = (missions || []).find(x => x.id === q.mission_id);
      const wrap = mk('div', 'msg atlas crew-card');
      const kind = q.kind === 'checkpoint' ? 'APPROVAL NEEDED' : (q.kind === 'budget' ? 'BUDGET REQUEST' : 'QUESTION');
      wrap.appendChild(mk('div', 'msg-tag', 'CREW \u00b7 ' + kind + (m ? ' \u00b7 ' + (m.title || 'mission') : '')));
      wrap.appendChild(mk('div', null, q.text));
      const row = mk('div', 'crew-row'); const note = mk('span', 'v-meta');
      const send = async (body) => {
        row.querySelectorAll('button').forEach(b => b.disabled = true);
        const r = await libApi('/api/crew/questions/' + encodeURIComponent(q.id) + '/answer', 'POST', body);
        note.textContent = r.ok ? (r.data.message || 'Done.') : (r.data.detail || 'That did not go through.');
        note.style.color = r.ok ? '#00ff88' : '#ff3344';
        if (!r.ok && r.status !== 409) row.querySelectorAll('button').forEach(b => b.disabled = false);
        setTimeout(checkCrew, 1000);
      };
      if (q.kind === 'question') {
        (q.options || []).forEach(o => { const b = mk('button', 'v-btn', o); b.addEventListener('click', () => send({ answer: o })); row.appendChild(b); });
        note.textContent = (q.options || []).length ? 'or just answer me here' : 'Answer me here in chat.';
      } else {
        const ok = mk('button', 'v-btn crew-ok', 'Approve'), no = mk('button', 'v-btn danger', 'Decline');
        ok.addEventListener('click', () => send({ approve: true })); no.addEventListener('click', () => send({ approve: false }));
        row.append(ok, no);
      }
      row.appendChild(note); wrap.appendChild(row);
      messagesEl.appendChild(wrap); messagesEl.scrollTop = messagesEl.scrollHeight;
      return wrap;
    }
    async function checkCrew() {
      const btn = document.getElementById('station-btn'), badge = document.getElementById('station-badge');
      try {
        const r = await fetch('/api/crew/state', { headers: authHeader() });
        if (!r.ok) { btn.hidden = true; return; }
        const d = await r.json();
        btn.hidden = !d.enabled;
        if (!d.enabled) return;
        const qs = d.questions || [], open = new Set(qs.map(q => q.id));
        crewShown.forEach((card, id) => { if (!open.has(id)) { card.querySelectorAll('button').forEach(b => b.disabled = true); crewShown.delete(id); } });
        qs.forEach(q => { if (!crewShown.has(q.id)) { crewShown.set(q.id, addCrewCard(q, d.missions)); feedLog('Crew: ' + q.kind); } });
        badge.hidden = !qs.length; badge.textContent = String(qs.length);
      } catch (e) { /* try again at the next check */ }
    }

    // "/image a red fox" and "/sound hello there": shortcuts. The result is saved to the library; the chat shows one line with an Open button.
    async function sendMedia(kind, prompt) {
      addMsg('user', '/' + kind + ' ' + prompt, null);
      feedLog('Media: ' + kind);
      const pending = addMsg('atlas', kind === 'image' ? 'Making the image...' : 'Making the sound...', null);
      sendBtn.disabled = true;
      try {
        const res  = await fetch('/api/media/' + kind, {
          method: 'POST', headers: jsonAuthHeader(),
          body: JSON.stringify(kind === 'image' ? { prompt: prompt } : { text: prompt })
        });
        if (res.status === 401) { pending.remove(); sendBtn.disabled = false; return; }
        const data = await res.json().catch(() => ({}));
        const body = pending.querySelector('div:last-child');
        if (!res.ok || !data.item) {
          body.textContent = (data.detail && typeof data.detail === 'string') ? data.detail : 'That could not be made right now.';
          body.style.color = '#ff3344';
        } else {
          body.textContent = 'Saved: ' + data.item.title;
          addOpenButton(pending, data.item.id);
          loadLibrary();
        }
      } catch (e) {
        const body = pending.querySelector('div:last-child');
        body.textContent = 'Error: ' + e.message; body.style.color = '#ff3344';
      }
      sendBtn.disabled = false;
      inputEl.focus();
    }

    // CHANGE 3: add client_date to the fetch body
    async function sendMessage(text) {
      if (!text) text = inputEl.value.trim();
      if (!text) return;
      const mediaCmd = text.match(/^\/(image|sound)\s+([\s\S]+)/i);
      if (mediaCmd) { inputEl.value = ''; return sendMedia(mediaCmd[1].toLowerCase(), mediaCmd[2].trim()); }
      inputEl.value = '';
      sendBtn.disabled = true;
      const imageToSend  = pendingImageB64;
      const imageDataUrl = pendingImageDataUrl;
      clearPendingImage();
      addMsg('user', text, imageDataUrl);
      feedLog('You: ' + text.slice(0, 40) + (text.length > 40 ? '...' : ''));
      const thinking = addMsg('atlas', 'Processing...', null);
      thinking.querySelector('div:last-child').style.color = 'var(--text-dim)';
      atlasState.textContent = 'THINKING'; atlasState.style.color = 'var(--orange)';
      try {
        const body = {
          message: text,
          client_date: new Date().toLocaleString('en-US', { timeZoneName: 'short' })
        };
        if (imageToSend) body.image = imageToSend;
        const res  = await fetch('/api/chat', {
          method: 'POST',
          headers: jsonAuthHeader(),
          body: JSON.stringify(body)
        });
        if (res.status === 401) { sendBtn.disabled = false; return; }
        const data = await res.json();
        thinking.remove();
        const reply = data.reply || data.detail || 'No response';
        const replyEl = addMsg('atlas', reply, null);
        handleUiActions(data.ui_actions, replyEl);
        feedLog('Helix responded');
        if (data.model_display) {
          updateEngineIndicator(data.model_display, data.model_color);
          feedLog('Engine: ' + data.model_display);
        }
        speakReply(reply, replyEl);
      } catch(e) {
        thinking.querySelector('div:last-child').textContent = 'Error: ' + e.message;
        thinking.querySelector('div:last-child').style.color = '#ff3344';
        atlasState.textContent = 'ERROR'; atlasState.style.color = '#ff3344';
      }
      sendBtn.disabled = false;
      inputEl.focus();
    }

    // A spoken reply that fails says so under the message instead of failing silently.
    function voiceNote(el, res, data) {
      let why = (data && typeof data.detail === 'string' && data.detail) ? data.detail : '';
      if (res.status === 429 && data && data.error === 'daily_spend_cap_reached') why = 'daily usage limit reached; voice resumes at midnight UTC.';
      else if (res.status === 429) why = why || 'too many requests; try again in a minute.';
      else if (res.status === 503) why = why || 'text-to-speech is not configured.';
      else if (res.status === 413) why = why || 'reply too long to speak.';
      else why = 'the voice service failed (HTTP ' + res.status + ').';
      feedLog('Voice off: HTTP ' + res.status);
      if (!el) return;
      const n = document.createElement('div');
      n.className = 'voice-note';
      n.textContent = 'Voice unavailable: ' + why;
      el.appendChild(n);
    }

    // Long text (a full brief) is spoken in chunks of whole sentences; the mp3 parts are joined into one clip.
    const SPEECH_CHUNK = 1400;
    function speechChunks(text) {
      const sentences = (String(text).match(/[^.!?\n]+[.!?]*|\n+/g) || []).map(x => x.trim()).filter(Boolean);
      const out = []; let cur = '';
      for (let snip of sentences) {
        while (snip.length > SPEECH_CHUNK) { if (cur) { out.push(cur); cur = ''; } out.push(snip.slice(0, SPEECH_CHUNK)); snip = snip.slice(SPEECH_CHUNK); }
        if (cur && (cur + ' ' + snip).length > SPEECH_CHUNK) { out.push(cur); cur = snip; }
        else cur = cur ? cur + ' ' + snip : snip;
      }
      if (cur) out.push(cur);
      return out.length ? out : [''];
    }
    async function speakReply(text, el) {
      try {
        const parts = [];
        for (const chunk of speechChunks(text)) {
          const res = await fetch('/api/voice/speak', {
            method: 'POST',
            headers: jsonAuthHeader(),
            body: JSON.stringify({ message: chunk })
          });
          if (!res.ok) {
            const raw = await res.text();
            let data = null; try { data = JSON.parse(raw); } catch (e) { /* not JSON */ }
            console.warn('TTS HTTP error:', res.status, raw);
            voiceNote(el, res, data);
            resumeWakeWord();
            return;
          }
          parts.push(await res.arrayBuffer());
        }
        const total = parts.reduce((n, p) => n + p.byteLength, 0);
        const joined = new Uint8Array(total); let off = 0;
        for (const p of parts) { joined.set(new Uint8Array(p), off); off += p.byteLength; }
        const arrayBuffer = joined.buffer;
        const blob  = new Blob([arrayBuffer], { type: 'audio/mpeg' });
        const url   = URL.createObjectURL(blob);
        const audio = new Audio(url);
        try {
          if (!_helixAudioCtx) {
            _helixAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
          }
          if (_helixAudioCtx.state === 'suspended') await _helixAudioCtx.resume();
          const analyser = _helixAudioCtx.createAnalyser();
          analyser.fftSize = 64;
          const src = _helixAudioCtx.createMediaElementSource(audio);
          src.connect(analyser);
          analyser.connect(_helixAudioCtx.destination);
          window._helixAnalyser = analyser;
          window._helixTalkData = new Uint8Array(analyser.frequencyBinCount);
        } catch(e) {
          console.warn('Analyser setup failed:', e.message);
          window._helixAnalyser = null;
        }
        audio.onplay  = () => setTalking(true);
        audio.onended = () => {
          setTalking(false);
          window._helixAnalyser = null;
          URL.revokeObjectURL(url);
          if (!isRecording) resumeWakeWord();
        };
        audio.onerror = () => {
          setTalking(false);
          window._helixAnalyser = null;
          URL.revokeObjectURL(url);
          resumeWakeWord();
        };
        pauseWakeWord();
        audio.play().catch(e => {
          console.warn('Play blocked:', e.message);
          setTalking(false);
          window._helixAnalyser = null;
          URL.revokeObjectURL(url);
          resumeWakeWord();
        });
      } catch(e) {
        console.warn('TTS failed:', e.message);
        resumeWakeWord();
      }
    }

    let isRecording = false, mediaRecorder = null, audioChunks = [];
    let wakeRecognition = null, wakeActive = false, _srRunning = false;
    let _srMode = 'wake';
    // Watchdog: if the listener should be on but is not running (the browser dropped it), start it again.
    setInterval(() => {
      if (wakeActive && wakeRecognition && !_srRunning && !isRecording) {
        try { wakeRecognition.start(); } catch(e) { /* already starting */ }
      }
    }, 2000);

    function initWakeWord() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) { setWakeStatus('unsupported'); return; }
      wakeRecognition = new SR();
      wakeRecognition.continuous = true;
      wakeRecognition.interimResults = true;
      wakeRecognition.lang = 'en-US';
      wakeActive = true;
      _srMode = 'wake';
      const thisInst = wakeRecognition;
      let triggered = false;
      // "hey helix" and the usual misrecognitions (helics, helex, hellix, healix, heyhelix). Matched on the whole recent
      // speech, not just the last fragment, because the browser splits a phrase across results.
      const WAKE_RE = /\b(?:he?y|hi|ok|a)\s*h[aeiou]{0,2}l+[aeiou]?[xcks]s?\b/;
      const norm = s => s.toLowerCase().replace(/[^a-z\s]/g, ' ').replace(/\s+/g, ' ').trim();
      wakeRecognition.onstart = () => { _srRunning = true; };
      wakeRecognition.onresult = (e) => {
        let heard = '';
        for (let i = e.resultIndex; i < e.results.length; i++) heard += ' ' + e.results[i][0].transcript;
        const latest = norm(heard);
        if (_srMode === 'wake') {
          if (triggered || isRecording) return;
          if (WAKE_RE.test(latest)) {
            triggered = true;
            setTimeout(() => { triggered = false; }, 3000);
            feedLog('Wake word detected: Hey Helix');
            startRecording();
          }
        } else if (_srMode === 'send') {
          if (!isRecording) return;
          if (/\bsend\b/.test(latest)) {
            feedLog('Voice command: SEND');
            stopRecording();
          }
        }
      };
      wakeRecognition.onend = () => {
        _srRunning = false;
        // the browser ends a session after a pause; restart it at once, and retry if the browser is still settling
        if (wakeActive && wakeRecognition === thisInst) {
          const again = () => { if (wakeActive && wakeRecognition === thisInst && !_srRunning) { try { wakeRecognition.start(); } catch(e) { setTimeout(again, 400); } } };
          again();
        }
      };
      wakeRecognition.onerror = (e) => {
        if (e.error === 'not-allowed') {
          wakeActive = false; wakeRecognition = null; setWakeStatus('unsupported');
        }
      };
      try {
        wakeRecognition.start();
        setWakeStatus('listening');
        feedLog('Say "Hey Helix" to wake');
      } catch(e) { setWakeStatus('unsupported'); }
    }

    function pauseWakeWord() {
      wakeActive = false;
      if (!wakeRecognition) return;
      try { wakeRecognition.abort(); } catch(e) {}
      wakeRecognition = null;
    }

    function resumeWakeWord() {
      if (wakeActive) return;
      initWakeWord();
    }

    async function startRecording() {
      if (isRecording) return;
      try {
        _srMode = 'send';
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks  = [];
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
        mediaRecorder.onstop = async () => {
          stream.getTracks().forEach(t => t.stop());
          isRecording = false;
          _srMode = 'wake';
          micBtn.textContent = '\u23f3'; micBtn.className = 'processing';
          setWakeStatus('processing');
          const blob = new Blob(audioChunks, { type: 'audio/webm' });
          const fd   = new FormData();
          fd.append('audio', blob, 'recording.webm');
          try {
            const res  = await fetch('/api/voice/transcribe', {
              method: 'POST', headers: authHeader(), body: fd
            });
            const data = await res.json();
            if (data.text) {
              let clean = data.text.trim()
                .replace(/^(hey\s+helix|hey\s+helics|hey\s+helic|a\s+helix)[,.]?\s*/i, '')
                .replace(/[,.]?\s*send\.?\s*$/i, '')
                .trim();
              if (clean) await sendMessage(clean);
            }
          } catch(e) { console.warn('Transcribe failed:', e.message); }
          micBtn.textContent = '\u{1F399}'; micBtn.className = '';
          setTimeout(() => { if (!isRecording && wakeActive) setWakeStatus('listening'); }, 500);
        };
        mediaRecorder.start();
        isRecording = true;
        micBtn.textContent = '\u{1F534}'; micBtn.className = 'recording';
        setWakeStatus('recording');
        feedLog('Recording — say "send" or tap mic to stop');
      } catch(e) {
        console.warn('Mic denied:', e.message);
        _srMode = 'wake';
        alert('Microphone access denied.');
      }
    }

    function stopRecording() {
      if (!isRecording || !mediaRecorder) return;
      mediaRecorder.stop();
    }

    micBtn.addEventListener('click', () => { unlockAudio(); isRecording ? stopRecording() : startRecording(); });
    sendBtn.addEventListener('click', () => { unlockAudio(); sendMessage(); });
    inputEl.addEventListener('keydown', e => { if (e.key === 'Enter') { unlockAudio(); sendMessage(); } });

    let _wakeStarted = false;
    function maybeStartWakeWord() {
      if (_wakeStarted || !getToken()) return;
      _wakeStarted = true;
      initWakeWord();
    }
    document.addEventListener('click',   maybeStartWakeWord);
    document.addEventListener('keydown', maybeStartWakeWord);
  </script>
  <script type="importmap">
  {
    "imports": {
      "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
      "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
    }
  }
  </script>
  <script type="module">
  import * as THREE from 'three';
  import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
  import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';

  const canvas = document.getElementById('atlas-canvas');
  const wrap   = document.getElementById('face-wrap');
  // Size the drawing buffer from the panel's real size, and keep it in step when the layout changes.
  // (Sizing once at load could lock in a stale size, which pushed the head off-centre.)
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setClearColor(0x000000, 0);
  const scene  = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
  function fitToPanel() {
    const w = wrap.clientWidth, h = wrap.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);      // false: CSS (width/height 100%) controls the displayed size
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(fitToPanel).observe(wrap);
  fitToPanel();
  camera.position.set(0, 0.5, 3);
  scene.add(new THREE.AmbientLight(0x00e5ff, 0.8));
  const dir = new THREE.DirectionalLight(0xffffff, 1);
  dir.position.set(1, 2, 3); scene.add(dir);

  let model, jawBone = null, mouthMesh = null;
  const SPIN_INTERVAL = 100000, SPIN_DURATION = 2500;
  let _lastSpin = Date.now(), _spinning = false, _spinStartAngle = 0, _spinStartTime = 0;
  const _t0 = Date.now();

  MeshoptDecoder.ready.then(() => {
    const loader = new GLTFLoader();
    loader.setMeshoptDecoder(MeshoptDecoder);
    loader.load('/static/atlas-model.glb', function(gltf) {
      model = gltf.scene;
      model.traverse((node) => {
        if (node.isBone) {
          const n = node.name.toLowerCase();
          if (n.includes('jaw') || n.includes('chin') || n.includes('mandible') ||
              n.includes('lower') || n.includes('mouth') || n.includes('lip')) {
            if (!jawBone) { jawBone = node; }
          }
        }
        if (node.isMesh && node.morphTargetDictionary) {
          const keys = Object.keys(node.morphTargetDictionary);
          const mouthKey = keys.find(k => {
            const kl = k.toLowerCase();
            return kl.includes('jaw') || kl.includes('open') ||
                   kl.includes('mouth') || kl.includes('aa') || kl.includes('lip');
          });
          if (mouthKey && !mouthMesh) {
            mouthMesh = { mesh: node, idx: node.morphTargetDictionary[mouthKey] };
          }
        }
      });
      const box    = new THREE.Box3().setFromObject(model);
      const center = box.getCenter(new THREE.Vector3());
      const size   = box.getSize(new THREE.Vector3());
      const s = 1.8 / Math.max(size.x, size.y, size.z);
      model.scale.setScalar(s);
      model.position.sub(center.multiplyScalar(s));
      scene.add(model);
    }, undefined, (e) => console.error('GLB error:', e));
  });

  let _smoothJaw = 0;

  (function animate() {
    requestAnimationFrame(animate);
    if (model) {
      const t   = (Date.now() - _t0) / 1000;
      const now = Date.now();

      if (window._helixTalking && window._helixAnalyser) {
        window._helixAnalyser.getByteFrequencyData(window._helixTalkData);
        const bass = window._helixTalkData.slice(0, 4).reduce((a, b) => a + b, 0) / 4 / 255;
        const mid  = window._helixTalkData.slice(4, 8).reduce((a, b) => a + b, 0) / 4 / 255;
        const raw  = Math.max(bass, mid * 0.6);
        _smoothJaw += (raw - _smoothJaw) * 0.35;
        if (jawBone)   jawBone.rotation.x = _smoothJaw * 0.4;
        if (mouthMesh) mouthMesh.mesh.morphTargetInfluences[mouthMesh.idx] = Math.min(_smoothJaw * 1.2, 1);
        model.rotation.x = Math.sin(t * 1.5) * 0.003;
        model.rotation.z = Math.sin(t * 1.1) * 0.002;
        model.position.y = Math.sin(t * 2.0) * 0.002;
      } else {
        _smoothJaw += (0 - _smoothJaw) * 0.15;
        if (jawBone)   jawBone.rotation.x = _smoothJaw * 0.4;
        if (mouthMesh) mouthMesh.mesh.morphTargetInfluences[mouthMesh.idx] = Math.min(_smoothJaw * 1.2, 1);
        const breath = Math.sin(t * 1.1) * 0.004;
        const sway   = Math.sin(t * 0.37) * 0.006 + Math.sin(t * 0.91) * 0.003;
        const nod    = Math.sin(t * 0.6 + 0.8) * 0.004;
        const drift  = Math.sin(t * 0.5) * 0.008;
        model.rotation.x = nod;
        model.rotation.z = sway;
        model.position.y = drift + breath;
        if (!_spinning && now - _lastSpin > SPIN_INTERVAL) {
          _spinStartAngle = model.rotation.y;
          _spinStartTime  = now;
          _spinning = true;
          _lastSpin = now;
        }
        if (_spinning) {
          const p = Math.min((now - _spinStartTime) / SPIN_DURATION, 1);
          const e = p < 0.5 ? 2*p*p : 1 - Math.pow(-2*p+2,2)/2;
          model.rotation.y = _spinStartAngle + e * Math.PI * 2;
          if (p >= 1) { model.rotation.y = _spinStartAngle; _spinning = false; }
        }
      }
    }
    renderer.render(scene, camera);
  })();
  </script>

</body>
</html>
"""
    return HTMLResponse(content=html, headers={"Permissions-Policy": "microphone=*"})


@router.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    user_id = _get_user_id(request)
    limited = _limited(request, {"chat_user": user_id, "chat_ip": hardening.client_ip(request)})
    if limited:
        return limited
    if req.image and len(req.image) > hardening.MAX_IMAGE_B64_CHARS:
        return JSONResponse({"detail": "Image too large (limit %d MB)" % (hardening.MAX_IMAGE_BYTES // (1024 * 1024)), "reply": "Image too large."}, status_code=413)
    store = request.app.state.session_store

    # "read me the brief": answered from the brief feed, with no model call and no spend
    brief_period = None if req.image or not briefs.owner_request(request) else briefs.wants_brief(req.message)
    if brief_period:
        try:
            found = await briefs.find_brief(briefs.store_for(request), brief_period)
            reply = briefs.brief_reply(found, brief_period)
        except Exception:
            logger.exception("brief lookup failed")
            reply = "I couldn't load the brief feed right now. Please try again."
        await store.get_or_create(user_id, CHANNEL)
        await store.append_message(user_id, CHANNEL, "user", req.message)
        await store.append_message(user_id, CHANNEL, "assistant", reply)
        return JSONResponse({"reply": reply, "model_display": "BRIEF FEED", "model_color": "#00e5ff"})

    # the daily spend cap is checked BEFORE the provider is called
    blocked = await hardening.spend_blocked(store, user_id, hardening.FLAT_CHAT_ESTIMATE_USD, exempt=_exempt(request))
    if blocked:
        return _spend_error(blocked)
    session = await store.get_or_create(user_id, CHANNEL)

    model_id, model_display, model_color = classify_model(req.message, bool(req.image))

    # a follow-up about the brief ("what's pending?" right after it was read) gets the full brief as context
    brief_ctx = ""
    recent = [m for m in session["conversation_history"][-4:] if m["role"] == "assistant" and isinstance(m["content"], str)]
    if briefs.owner_request(request) and (re.search(r"\bbrief(?:ing)?s?\b", req.message, re.I) or any(_BRIEF_REPLY.match(m["content"]) for m in recent)):
        try:
            latest_brief = await briefs.find_brief(briefs.store_for(request), "latest")
            if latest_brief:
                brief_ctx = briefs.brief_context(latest_brief)
        except Exception:
            logger.exception("brief context lookup failed")

    # CHANGE 4: pass client_date to get_system_prompt
    # Helix's media tools are offered only when media is fully on (flag, budget above zero, storage configured); otherwise chat is exactly as before
    media_ctx = None
    if helix_tools.tools_available(request.app.state):
        try:
            media_ctx = media_service.ctx_from_request(request, paid=True)
        except MediaServiceError:
            media_ctx = None
    # the crew (owner accounts, CREW_ENABLED): a yes to a read-back mission start is handled here, with no model call
    crew_ctx = await crew_helix.ctx_for(request)
    if crew_ctx is not None and not req.image:
        early = await crew_helix.pre_turn(crew_ctx, req.message)
        if early is not None:
            await store.append_message(user_id, CHANNEL, "user", req.message)
            await store.append_message(user_id, CHANNEL, "assistant", early["reply"])
            return JSONResponse({"reply": early["reply"], "ui_actions": early["ui"], "model_display": model_display, "model_color": model_color})
    if media_ctx is not None and not req.image:
        early = await helix_tools.pre_turn(media_ctx, req.message)         # a yes to a read-back action, a cancel, a sound-effect request
        if early is not None:
            await store.append_message(user_id, CHANNEL, "user", req.message)
            await store.append_message(user_id, CHANNEL, "assistant", early["reply"])
            return JSONResponse({"reply": early["reply"], "ui_actions": early["ui"], "model_display": model_display, "model_color": model_color})

    system_text = get_system_prompt(req.client_date) + (helix_tools.SYSTEM_ADDENDUM if media_ctx is not None else "") + brief_ctx
    if crew_ctx is not None:
        system_text += await crew_helix.system_addendum(crew_ctx)
    messages = [{"role": "system", "content": system_text}]
    for m in session["conversation_history"][-10:]:
        if m["role"] in ("user", "assistant"):
            content = m["content"]
            if isinstance(content, list):
                content = next((c.get("text","") for c in content if c.get("type")=="text"), "")
            messages.append({"role": m["role"], "content": content})

    if req.image:
        user_content = [
            {"type": "text", "text": req.message},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{req.image}"}}
        ]
    else:
        user_content = req.message

    messages.append({"role": "user", "content": user_content})

    if media_ctx is not None or crew_ctx is not None:
        async def _cost(resp):
            await store.add_spend(user_id, hardening.utc_day(), hardening.chat_cost_usd(resp))

        crew_before = crew_helix.CREW_PENDING.get(user_id) if crew_ctx is not None else None

        async def _crew_exec(name, args):
            return await crew_helix.execute(crew_ctx, name, args)

        out = None
        for mid_ in _with_fallback(model_id):
            try:
                out = await helix_tools.run_model_with_tools(
                    media_ctx, messages, mid_, req.message,
                    acompletion=litellm.acompletion, on_cost=_cost, max_tokens=hardening.CHAT_MAX_TOKENS,
                    extra_tools=crew_helix.CREW_TOOLS if crew_ctx is not None else None,
                    extra_executor=_crew_exec if crew_ctx is not None else None,
                )
                if mid_ != model_id:
                    model_display, model_color = hardening.display_name(mid_), "orange"
                break
            except Exception:
                logger.exception("model call with tools failed (%s)", mid_)
        if out is None:
            return JSONResponse({"reply": "The model could not answer right now. Please try again.", "model_display": model_display, "model_color": model_color})
        if crew_ctx is not None:
            out["reply"] = crew_helix.after_turn(crew_ctx, out["reply"], crew_before)
        await store.append_message(user_id, CHANNEL, "user", req.message)
        await store.append_message(user_id, CHANNEL, "assistant", out["reply"])
        return JSONResponse({"reply": out["reply"], "ui_actions": out["ui"], "model_display": model_display, "model_color": model_color})

    response = None
    for mid_ in _with_fallback(model_id):
        try:
            response = await litellm.acompletion(model=mid_, messages=messages, max_tokens=hardening.CHAT_MAX_TOKENS)
            reply = response.choices[0].message.content
            if mid_ != model_id:
                model_display, model_color = hardening.display_name(mid_), "orange"
            break
        except Exception:
            logger.exception("model call failed (%s)", mid_)       # details stay in the server log, not in the answer
            response = None
    if response is None:
        return JSONResponse({"reply": "The model could not answer right now. Please try again.", "model_display": model_display, "model_color": model_color})
    await store.add_spend(user_id, hardening.utc_day(), hardening.chat_cost_usd(response))

    await store.append_message(user_id, CHANNEL, "user", req.message)
    await store.append_message(user_id, CHANNEL, "assistant", reply)

    return JSONResponse({
        "reply": reply,
        "model_display": model_display,
        "model_color": model_color,
    })
