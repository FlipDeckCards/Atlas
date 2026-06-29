import os
import httpx
import litellm
from datetime import datetime, timezone
from fastapi import APIRouter, File, UploadFile, Request
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional


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
    return f"""You are AiBusSol, a personal AI hub and orchestrator built by Michael.
You have persistent memory and can handle a wide range of tasks with intelligence and precision.
You are not OpenJarvis — you are AiBusSol.
Today's date and time is {date_str}.
Be direct, sharp, and helpful."""


def classify_model(message: str, has_image: bool) -> tuple:
    """Returns (litellm_model_id, display_name, color_class)"""
    if has_image:
        return "gpt-4o", "GPT-4O", "cyan"

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
        return "anthropic/claude-3-5-sonnet-20241022", "CLAUDE-3.5", "orange"
    elif gemini_score >= claude_score and gemini_score > 0:
        return "gemini/gemini-2.0-flash", "GEMINI-2.0", "green"
    else:
        return "gpt-4o", "GPT-4O", "cyan"


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
    message: str
    image: Optional[str] = None
    client_date: Optional[str] = None


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
async def transcribe(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": (audio.filename or "audio.webm", audio_bytes, audio.content_type or "audio/webm")},
            data={"model": "whisper-1"}
        )
        return JSONResponse({"text": res.json().get("text", "")})


@router.post("/api/voice/speak")
async def speak(req: ChatRequest):
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={
                "text": req.message,
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
@router.get("/")
async def index():
    html = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>AiBusSol</title>
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
    #live-feed { flex: 1; padding: 14px 16px; overflow: hidden; font-size: 10px; line-height: 1.9; letter-spacing: 0.5px; }
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
    #face-wrap { position:relative; width:780px; height:960px; }
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
    @media (max-width: 768px) {
      body { grid-template-rows: 40px 1fr; overflow: hidden; height: 100dvh; }
      #hud-header { padding: 0 12px; }
      #hud-title { font-size: 13px; letter-spacing: 2px; }
      .hud-meta { gap: 8px; }
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
    }
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
      <span>SESSION: <span class="v">WEB-01</span></span>
      <span>UPTIME: <span class="v" id="uptime">00:00:00</span></span>
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
      <div id="live-feed">
        <div class="p-label">Live Feed</div>
        <div class="feed-line">&#9656; AiBusSol core initialized</div>
        <div class="feed-line">&#9656; Neon DB connected</div>
        <div class="feed-line">&#9656; Voice engine loaded</div>
        <div class="feed-line" id="feed-last">&#9656; Awaiting input...</div>
      </div>
    </div>

    <div id="panel-center">
      <div class="corner tl"></div><div class="corner tr"></div>
      <div class="corner bl"></div><div class="corner br"></div>
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

    <div id="panel-right">
      <div id="chat-hdr">
        <div class="p-label" style="margin:0">Command Console</div>
        <div id="wake-ind">
          <div id="wake-dot"></div>
          <span id="wake-label">INITIALIZING</span>
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
          <input id="input" type="text" placeholder='Type or say "Hey Sol..."' autocomplete="off"/>
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

    let _solAudioCtx = null;
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
        : { email, password, display_name: document.getElementById('auth-display').value.trim() || email.split('@')[0] };
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
      window._solTalking = on;
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
        centerSub.textContent = 'SAY "HEY SOL" TO WAKE';
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
      tag.textContent = role === 'user' ? 'YOU' : 'AIBUSSOL';
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

    // CHANGE 3: add client_date to the fetch body
    async function sendMessage(text) {
      if (!text) text = inputEl.value.trim();
      if (!text) return;
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
        addMsg('atlas', reply, null);
        feedLog('AiBusSol responded');
        if (data.model_display) {
          updateEngineIndicator(data.model_display, data.model_color);
          feedLog('Engine: ' + data.model_display);
        }
        speakReply(reply);
      } catch(e) {
        thinking.querySelector('div:last-child').textContent = 'Error: ' + e.message;
        thinking.querySelector('div:last-child').style.color = '#ff3344';
        atlasState.textContent = 'ERROR'; atlasState.style.color = '#ff3344';
      }
      sendBtn.disabled = false;
      inputEl.focus();
    }

    async function speakReply(text) {
      try {
        const res = await fetch('/api/voice/speak', {
          method: 'POST',
          headers: jsonAuthHeader(),
          body: JSON.stringify({ message: text })
        });
        if (!res.ok) {
          console.warn('TTS HTTP error:', res.status, await res.text());
          resumeWakeWord();
          return;
        }
        const arrayBuffer = await res.arrayBuffer();
        const blob  = new Blob([arrayBuffer], { type: 'audio/mpeg' });
        const url   = URL.createObjectURL(blob);
        const audio = new Audio(url);
        try {
          if (!_solAudioCtx) {
            _solAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
          }
          if (_solAudioCtx.state === 'suspended') await _solAudioCtx.resume();
          const analyser = _solAudioCtx.createAnalyser();
          analyser.fftSize = 64;
          const src = _solAudioCtx.createMediaElementSource(audio);
          src.connect(analyser);
          analyser.connect(_solAudioCtx.destination);
          window._solAnalyser = analyser;
          window._solTalkData = new Uint8Array(analyser.frequencyBinCount);
        } catch(e) {
          console.warn('Analyser setup failed:', e.message);
          window._solAnalyser = null;
        }
        audio.onplay  = () => setTalking(true);
        audio.onended = () => {
          setTalking(false);
          window._solAnalyser = null;
          URL.revokeObjectURL(url);
          if (!isRecording) resumeWakeWord();
        };
        audio.onerror = () => {
          setTalking(false);
          window._solAnalyser = null;
          URL.revokeObjectURL(url);
          resumeWakeWord();
        };
        pauseWakeWord();
        audio.play().catch(e => {
          console.warn('Play blocked:', e.message);
          setTalking(false);
          window._solAnalyser = null;
          URL.revokeObjectURL(url);
          resumeWakeWord();
        });
      } catch(e) {
        console.warn('TTS failed:', e.message);
        resumeWakeWord();
      }
    }

    let isRecording = false, mediaRecorder = null, audioChunks = [];
    let wakeRecognition = null, wakeActive = false;
    let _srMode = 'wake';

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
      const WAKE_VARIANTS = [
        'hey sol','hey soul','hey saul','hey sal',
        'hey so','hey soll','a sol','heysel','hey-sol'
      ];
      wakeRecognition.onresult = (e) => {
        const latest = e.results[e.results.length - 1][0].transcript.toLowerCase().trim();
        if (_srMode === 'wake') {
          if (triggered || isRecording) return;
          if (WAKE_VARIANTS.some(v => latest.includes(v))) {
            triggered = true;
            setTimeout(() => { triggered = false; }, 3000);
            feedLog('Wake word detected: Hey Sol');
            startRecording();
          }
        } else if (_srMode === 'send') {
          if (!isRecording) return;
          if (latest === 'send' || latest.endsWith(' send') || latest.endsWith('. send') || latest.includes('send')) {
            feedLog('Voice command: SEND');
            stopRecording();
          }
        }
      };
      wakeRecognition.onend = () => {
        if (wakeActive && wakeRecognition === thisInst) {
          try { wakeRecognition.start(); } catch(e) {}
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
        feedLog('Say "Hey Sol" to wake');
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
                .replace(/^(hey\s+sol|hey\s+soul)[,.]?\s*/i, '')
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
  const w = wrap.offsetWidth, h = wrap.offsetHeight;
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setSize(w, h);
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setClearColor(0x000000, 0);
  const scene  = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, w / h, 0.1, 100);
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

      if (window._solTalking && window._solAnalyser) {
        window._solAnalyser.getByteFrequencyData(window._solTalkData);
        const bass = window._solTalkData.slice(0, 4).reduce((a, b) => a + b, 0) / 4 / 255;
        const mid  = window._solTalkData.slice(4, 8).reduce((a, b) => a + b, 0) / 4 / 255;
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
    store = request.app.state.session_store
    session = await store.get_or_create(user_id, CHANNEL)

    model_id, model_display, model_color = classify_model(req.message, bool(req.image))

    # CHANGE 4: pass client_date to get_system_prompt
    messages = [{"role": "system", "content": get_system_prompt(req.client_date)}]
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

    try:
        response = await litellm.acompletion(model=model_id, messages=messages)
        reply = response.choices[0].message.content
    except Exception as e:
        return JSONResponse({"reply": f"Model error: {str(e)[:200]}", "model_display": model_display, "model_color": model_color})

    await store.append_message(user_id, CHANNEL, "user", req.message)
    await store.append_message(user_id, CHANNEL, "assistant", reply)

    return JSONResponse({
        "reply": reply,
        "model_display": model_display,
        "model_color": model_color,
    })