import os
import httpx
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional

from openjarvis.server.session_store import SessionStore

router = APIRouter()

API_KEY = os.getenv("OPENJARVIS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel

OWNER_USER_ID = "michael"
CHANNEL = "web"

SYSTEM_PROMPT = """You are Atlas, a personal AI hub and orchestrator built by Michael.
You coordinate a network of specialized agents: OMNI (trading advisor), FlipDeck (card flipping tool), and Deadzone (games platform).
You have persistent memory and can route tasks to the right spoke. You are not OpenJarvis — you are Atlas.
Be direct, sharp, and helpful."""

_store: Optional[SessionStore] = None

# ── GLB model path — looks next to this file first, then repo root /static ──
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


async def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
        await _store.connect()
    return _store


class ChatRequest(BaseModel):
    message: str


# ── Serve the GLB without needing StaticFiles on the main app ────────────────
@router.get("/static/atlas-model.glb")
async def serve_model():
    path = _find_model()
    if not path:
        return JSONResponse({"error": "model not found"}, status_code=404)
    return FileResponse(
        path,
        media_type="model/gltf-binary",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/session/history")
async def session_history():
    store = await get_store()
    session = await store.get_or_create(OWNER_USER_ID, CHANNEL)
    return JSONResponse({
        "messages": [
            {"role": m["role"], "content": m["content"]}
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
        data = res.json()
        return JSONResponse({"text": data.get("text", "")})


@router.post("/api/voice/speak")
async def speak(req: ChatRequest):
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "text": req.message,
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
            }
        )
        return StreamingResponse(
            iter([res.content]),
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=speech.mp3"}
        )


@router.get("/")
async def index():
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Atlas</title>
  <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&display=swap" rel="stylesheet"/>
  <!-- model-viewer web component -->
  <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.4.0/model-viewer.min.js"></script>
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
      display: grid; grid-template-rows: 48px 1fr 44px; gap: 1px;
      background-image:
        linear-gradient(rgba(0,229,255,0.018) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0,229,255,0.018) 1px, transparent 1px);
      background-size: 44px 44px;
    }

    /* ── HEADER ─────────────────────────────────────── */
    #hud-header {
      background: var(--panel); border-bottom: 1px solid var(--border);
      display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
    }
    #hud-title {
      font-family: 'Orbitron', monospace; font-size: 16px; font-weight: 900;
      color: var(--cyan); letter-spacing: 4px; text-shadow: 0 0 20px var(--cyan);
    }
    #hud-title span { color: var(--orange); }
    .hud-meta { display: flex; gap: 24px; font-size: 11px; color: var(--text-dim); letter-spacing: 1px; }
    .hud-meta .v { color: var(--cyan); }

    /* ── MAIN GRID ──────────────────────────────────── */
    #hud-main { display: grid; grid-template-columns: 200px 1fr 320px; gap: 1px; overflow: hidden; }

    /* ── LEFT PANEL ─────────────────────────────────── */
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
    .spoke-row { display: flex; align-items: center; gap: 8px; padding: 7px 0; font-size: 11px; letter-spacing: 1px; }
    .s-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
    .s-dot.on   { background: #00ff88; box-shadow: 0 0 6px #00ff88; animation: blink 2s infinite; }
    .s-dot.stby { background: var(--orange); box-shadow: 0 0 6px var(--orange); }
    .s-dot.off  { background: #333; }
    .s-name { color: var(--text); flex: 1; }
    .s-tag  { color: var(--text-dim); font-size: 9px; }
    #live-feed {
      flex: 1; padding: 14px 16px; overflow: hidden;
      font-size: 10px; line-height: 1.9; letter-spacing: 0.5px;
    }
    .feed-line { color: var(--text-dim); transition: color 0.5s, opacity 0.5s; }
    @keyframes blink { 0%,100%{opacity:1}50%{opacity:0.3} }

    /* ── CENTER ──────────────────────────────────────── */
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
    #face-wrap { position:relative; width:240px; height:300px; }
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

    /* atlas-face = div wrapper; filter + talking class apply to everything inside */
    #atlas-face {
      position: relative;
      width: 100%; height: 100%;
      filter: drop-shadow(0 0 10px var(--cyan)) drop-shadow(0 0 22px rgba(0,229,255,0.25));
      animation: idle-breathe 4s ease-in-out infinite;
      transition: filter 0.3s;
    }
    #atlas-face.talking {
      filter: drop-shadow(0 0 18px var(--cyan)) drop-shadow(0 0 40px rgba(0,229,255,0.55)) drop-shadow(0 0 10px var(--orange));
    }

    /* model-viewer fills the face-wrap, transparent bg */
    model-viewer {
      width: 100%;
      height: 100%;
      background-color: transparent;
      --poster-color: transparent;
    }

    /* SVG overlay sits on top of the 3D model */
    #atlas-face-svg {
      position: absolute;
      top: 0; left: 0;
      width: 100%; height: 100%;
      pointer-events: none;
    }

    @keyframes idle-breathe { 0%,100%{transform:scale(1)} 50%{transform:scale(1.006)} }

    .face-state {
      margin-top:16px; text-align:center; font-size:11px; letter-spacing:3px; color:var(--text-dim);
    }
    #atlas-state {
      font-family:'Orbitron',monospace; font-size:10px; color:var(--cyan); margin-bottom:4px;
    }

    /* ── RIGHT CHAT PANEL ────────────────────────────── */
    #panel-right {
      background:var(--panel); border-left:1px solid var(--border);
      display:flex; flex-direction:column; overflow:hidden;
    }
    #chat-hdr {
      padding:12px 16px; border-bottom:1px solid var(--border);
      display:flex; align-items:center; justify-content:space-between;
    }
    #wake-ind { display:flex; align-items:center; gap:6px; font-size:10px; color:var(--text-dim); }
    #wake-dot { width:6px; height:6px; border-radius:50%; background:#1a3a4a; transition:all 0.3s; }
    #wake-dot.listening  { background:var(--cyan); box-shadow:0 0 8px var(--cyan); animation:blink 2s infinite; }
    #wake-dot.recording  { background:#ff3344; box-shadow:0 0 8px #ff3344; animation:blink 0.4s infinite; }
    #wake-dot.processing { background:var(--orange); box-shadow:0 0 8px var(--orange); animation:blink 0.8s infinite; }
    #messages {
      flex:1; overflow-y:auto; padding:14px;
      display:flex; flex-direction:column; gap:10px;
      scrollbar-width:thin; scrollbar-color:var(--cyan-dim) transparent;
    }
    #messages::-webkit-scrollbar{width:3px}
    #messages::-webkit-scrollbar-thumb{background:var(--cyan-dim);border-radius:2px}
    .msg { max-width:92%; padding:10px 12px; font-size:12px; line-height:1.6; border-radius:2px; }
    .msg-tag { font-size:9px; letter-spacing:2px; margin-bottom:4px; opacity:0.5; }
    .user  { align-self:flex-end; background:rgba(255,109,0,0.08); border:1px solid rgba(255,109,0,0.25); color:#ffa040; }
    .user .msg-tag { color:var(--orange); }
    .atlas { align-self:flex-start; background:rgba(0,229,255,0.04); border:1px solid rgba(0,229,255,0.14); color:var(--text); }
    .atlas .msg-tag { color:var(--cyan); }
    #input-row {
      display:flex; padding:12px; gap:8px;
      border-top:1px solid var(--border); align-items:center;
    }
    #input {
      flex:1; background:rgba(0,229,255,0.03); border:1px solid var(--border);
      color:var(--text); padding:10px 12px; font-family:'Share Tech Mono',monospace;
      font-size:12px; letter-spacing:0.5px; outline:none; border-radius:2px;
    }
    #input:focus { border-color:var(--cyan); box-shadow:0 0 8px rgba(0,229,255,0.1); }
    #input::placeholder { color:var(--text-dim); }
    #send {
      background:transparent; border:1px solid var(--cyan); color:var(--cyan);
      padding:10px 14px; font-family:'Orbitron',monospace; font-size:10px;
      letter-spacing:2px; cursor:pointer; border-radius:2px; transition:all 0.2s;
    }
    #send:hover { background:rgba(0,229,255,0.1); box-shadow:0 0 12px rgba(0,229,255,0.2); }
    #send:disabled { opacity:0.3; cursor:not-allowed; }
    #mic {
      background:transparent; border:1px solid var(--border); color:var(--text-dim);
      padding:10px 12px; font-size:15px; cursor:pointer; border-radius:2px; transition:all 0.2s;
    }
    #mic:hover { border-color:var(--orange); color:var(--orange); }
    #mic.recording  { border-color:#ff3344; color:#ff3344; background:rgba(255,51,68,0.08); animation:blink 0.5s infinite; }
    #mic.processing { border-color:var(--orange); color:var(--orange); }

    /* ── BOTTOM NAV ──────────────────────────────────── */
    #hud-nav {
      background:var(--panel); border-top:1px solid var(--border);
      display:flex; align-items:center; justify-content:center;
    }
    .nav-tab {
      padding:0 28px; height:100%; display:flex; align-items:center;
      font-family:'Orbitron',monospace; font-size:9px; letter-spacing:3px;
      color:var(--text-dim); cursor:pointer; border-right:1px solid var(--border);
      transition:all 0.2s; user-select:none;
    }
    .nav-tab:hover  { color:var(--text); background:rgba(0,229,255,0.03); }
    .nav-tab.active { color:var(--cyan); border-bottom:2px solid var(--cyan); }
  </style>
</head>
<body>

  <div id="hud-header">
    <div id="hud-title">ATL<span>▲</span>S</div>
    <div class="hud-meta">
      <span>OPERATOR: <span class="v">MICHAEL</span></span>
      <span>SESSION: <span class="v">WEB-01</span></span>
      <span>UPTIME: <span class="v" id="uptime">00:00:00</span></span>
      <span>SPOKES: <span class="v">3 ONLINE</span></span>
    </div>
  </div>

  <div id="hud-main">

    <!-- LEFT -->
    <div id="panel-left">
      <div class="sys-block">
        <div class="p-label">System Vitals</div>
        <div class="stat-row"><span class="lbl">STATUS</span><span class="val g">NOMINAL</span></div>
        <div class="stat-row"><span class="lbl">MEMORY</span><span class="val">ACTIVE</span></div>
        <div class="stat-row"><span class="lbl">ENGINE</span><span class="val">GPT-4O</span></div>
        <div class="stat-row"><span class="lbl">VOICE</span><span class="val" id="voice-status">READY</span></div>
        <div class="stat-row"><span class="lbl">PROTOCOL</span><span class="val d">JSON-RPC</span></div>
      </div>
      <div class="sys-block">
        <div class="p-label">Spoke Network</div>
        <div class="spoke-row"><div class="s-dot on"></div><span class="s-name">OMNI</span><span class="s-tag">TRADING</span></div>
        <div class="spoke-row"><div class="s-dot on"></div><span class="s-name">FLIPDECK</span><span class="s-tag">OPS</span></div>
        <div class="spoke-row"><div class="s-dot stby"></div><span class="s-name">DEADZONE</span><span class="s-tag">STANDBY</span></div>
      </div>
      <div id="live-feed">
        <div class="p-label">Live Feed</div>
        <div class="feed-line">&#9656; Atlas core initialized</div>
        <div class="feed-line">&#9656; Neon DB connected</div>
        <div class="feed-line">&#9656; Voice engine loaded</div>
        <div class="feed-line" id="feed-last">&#9656; Awaiting input...</div>
      </div>
    </div>

    <!-- CENTER — 3D model -->
    <div id="panel-center">
      <div class="corner tl"></div><div class="corner tr"></div>
      <div class="corner bl"></div><div class="corner br"></div>
      <div class="face-label">CORE INTERFACE</div>
      <div id="face-wrap">
        <div class="f-ring"></div>
        <div class="f-ring2"></div>

        <div id="atlas-face">

          <!-- 3D model — auto-rotates, mouse-draggable -->
          <model-viewer
            id="atlas-model"
            src="/static/atlas-model.glb"
            alt="Atlas 3D"
            auto-rotate
            auto-rotate-delay="500"
            rotation-per-second="12deg"
            camera-controls
            camera-orbit="0deg 80deg 2.2m"
            min-camera-orbit="auto 60deg 1.2m"
            max-camera-orbit="auto 100deg 4m"
            environment-image="neutral"
            shadow-intensity="0"
            exposure="0.7"
            tone-mapping="neutral"
          ></model-viewer>

          <!-- SVG overlay: mouth glow pulses when talking -->
          <svg id="atlas-face-svg" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
            <!-- Scan line — decorative -->
            <line x1="20" y1="100" x2="180" y2="100"
                  stroke="#00e5ff" stroke-width="0.4" opacity="0.08"/>
            <!-- Mouth — JS-driven, IDs must stay -->
            <path id="mouth-upper" d="M 78 130 Q 100 127 122 130"
                  fill="none" stroke="#a855f7" stroke-width="1.8" stroke-linecap="round" opacity="0.9"/>
            <path id="mouth-lower" d="M 78 130 Q 100 133 122 130"
                  fill="none" stroke="#a855f7" stroke-width="1.8" stroke-linecap="round" opacity="0.9"/>
            <path id="mouth-fill"  d="M 78 130 Q 100 127 122 130 Q 100 133 78 130"
                  fill="#2a0040" opacity="0"/>
          </svg>

        </div>
      </div>
      <div class="face-state">
        <div id="atlas-state">STANDBY</div>
        <div>&#9656; <span id="center-sub">WAITING FOR INPUT</span></div>
      </div>
    </div>

    <!-- RIGHT -->
    <div id="panel-right">
      <div id="chat-hdr">
        <div class="p-label" style="margin:0">Command Console</div>
        <div id="wake-ind">
          <div id="wake-dot"></div>
          <span id="wake-label">INITIALIZING</span>
        </div>
      </div>
      <div id="messages"></div>
      <div id="input-row">
        <button id="mic" title="Record">&#127897;</button>
        <input id="input" type="text" placeholder='Type or say "Atlas..."' autocomplete="off"/>
        <button id="send">SEND</button>
      </div>
    </div>

  </div>

  <div id="hud-nav">
    <div class="nav-tab active">CHAT</div>
    <div class="nav-tab">MEMORY</div>
    <div class="nav-tab">SPOKES</div>
    <div class="nav-tab">ANALYTICS</div>
    <div class="nav-tab">SETTINGS</div>
  </div>

  <script>
    const messagesEl = document.getElementById('messages');
    const inputEl    = document.getElementById('input');
    const sendBtn    = document.getElementById('send');
    const micBtn     = document.getElementById('mic');
    const wakeDot    = document.getElementById('wake-dot');
    const wakeLabel  = document.getElementById('wake-label');
    const atlasFace  = document.getElementById('atlas-face');
    const atlasModel = document.getElementById('atlas-model');
    const atlasState = document.getElementById('atlas-state');
    const centerSub  = document.getElementById('center-sub');
    const voiceStat  = document.getElementById('voice-status');
    const API_KEY    = '__ATLAS_API_KEY__';

    // ── Uptime clock ──────────────────────────────────
    const uptimeStart = Date.now();
    setInterval(() => {
      const s  = Math.floor((Date.now() - uptimeStart) / 1000);
      const h  = String(Math.floor(s / 3600)).padStart(2, '0');
      const m  = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
      const sc = String(s % 60).padStart(2, '0');
      document.getElementById('uptime').textContent = h + ':' + m + ':' + sc;
    }, 1000);

    let isRecording = false, mediaRecorder = null, audioChunks = [];
    let wakeRecognition = null, wakeActive = false;

    // ── Mouth animation (y=130 matches GLB face framing) ──
    function setMouthOpen(a) {
      const y = 130, w = 22, drop = a * 20;
      const U = `M ${100-w} ${y} Q 100 ${y-3} ${100+w} ${y}`;
      const L = `M ${100-w} ${y} Q 100 ${y+3+drop} ${100+w} ${y}`;
      const F = `M ${100-w} ${y} Q 100 ${y-3} ${100+w} ${y} Q 100 ${y+3+drop} ${100-w} ${y}`;
      document.getElementById('mouth-upper').setAttribute('d', U);
      document.getElementById('mouth-lower').setAttribute('d', L);
      const mf = document.getElementById('mouth-fill');
      mf.setAttribute('d', F);
      mf.style.opacity = a > 0.05 ? String(0.35 + a * 0.45) : '0';
    }

    // ── Talking state — glow + pause auto-rotate ──────
    function setTalking(on) {
      if (on) {
        atlasFace.classList.add('talking');
        if (atlasModel) atlasModel.removeAttribute('auto-rotate');
        atlasState.textContent = 'SPEAKING';
        atlasState.style.color = 'var(--orange)';
        centerSub.textContent  = 'TRANSMITTING AUDIO';
        voiceStat.textContent  = 'SPEAKING';
      } else {
        atlasFace.classList.remove('talking');
        if (atlasModel) atlasModel.setAttribute('auto-rotate', '');
        atlasState.textContent = 'STANDBY';
        atlasState.style.color = 'var(--cyan)';
        centerSub.textContent  = 'WAITING FOR INPUT';
        voiceStat.textContent  = 'READY';
        setMouthOpen(0);
      }
    }

    function setWakeStatus(state) {
      wakeDot.className = '';
      if (state === 'listening') {
        wakeDot.classList.add('listening');
        wakeLabel.textContent  = 'LISTENING';
        centerSub.textContent  = 'SAY "ATLAS" TO WAKE';
        atlasState.textContent = 'ACTIVE';
        atlasState.style.color = 'var(--cyan)';
      } else if (state === 'recording') {
        wakeDot.classList.add('recording');
        wakeLabel.textContent  = 'RECORDING';
        centerSub.textContent  = 'RECORDING INPUT';
        atlasState.textContent = 'LISTENING';
        atlasState.style.color = '#ff3344';
      } else if (state === 'processing') {
        wakeDot.classList.add('processing');
        wakeLabel.textContent  = 'PROCESSING';
        centerSub.textContent  = 'PROCESSING AUDIO';
        atlasState.textContent = 'PROCESSING';
        atlasState.style.color = 'var(--orange)';
      } else if (state === 'unsupported') {
        wakeLabel.textContent = 'UNAVAILABLE';
      } else {
        wakeLabel.textContent = 'OFFLINE';
      }
    }

    function feedLog(text) {
      const feed = document.getElementById('live-feed');
      const d = document.createElement('div');
      d.className = 'feed-line';
      d.textContent = '\u25b8 ' + text;
      d.style.color = 'var(--cyan)';
      feed.appendChild(d);
      const lines = feed.querySelectorAll('.feed-line');
      lines.forEach((l, i) => {
        const age = lines.length - i;
        l.style.opacity = String(Math.max(0.1, 0.85 - age * 0.1));
        l.style.color = age > 4 ? 'var(--text-dim)' : 'var(--text)';
      });
      if (lines.length > 12) lines[0].remove();
    }

    function addMsg(role, text) {
      const wrap = document.createElement('div');
      wrap.className = 'msg ' + (role === 'user' ? 'user' : 'atlas');
      const tag = document.createElement('div');
      tag.className = 'msg-tag';
      tag.textContent = role === 'user' ? 'YOU' : 'ATLAS';
      const body = document.createElement('div');
      body.textContent = text;
      wrap.appendChild(tag);
      wrap.appendChild(body);
      messagesEl.appendChild(wrap);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return wrap;
    }

    async function loadHistory() {
      try {
        const res  = await fetch('/api/session/history', {
          headers: { 'Authorization': 'Bearer ' + API_KEY }
        });
        const data = await res.json();
        (data.messages || []).forEach(m => addMsg(m.role, m.content));
        if ((data.messages || []).length) feedLog('Session history loaded');
      } catch(e) { console.warn('History load failed:', e.message); }
    }

    async function sendMessage(text) {
      if (!text) text = inputEl.value.trim();
      if (!text) return;
      inputEl.value = '';
      sendBtn.disabled = true;
      addMsg('user', text);
      feedLog('You: ' + text.slice(0, 40) + (text.length > 40 ? '...' : ''));
      const thinking = addMsg('atlas', 'Processing...');
      thinking.querySelector('div:last-child').style.color = 'var(--text-dim)';
      atlasState.textContent = 'THINKING';
      atlasState.style.color = 'var(--orange)';
      try {
        const res  = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API_KEY },
          body: JSON.stringify({ message: text })
        });
        const data = await res.json();
        thinking.remove();
        const reply = data.reply || data.detail || 'No response';
        addMsg('atlas', reply);
        feedLog('Atlas responded');
        speakReply(reply);
      } catch(e) {
        thinking.querySelector('div:last-child').textContent = 'Error: ' + e.message;
        thinking.querySelector('div:last-child').style.color = '#ff3344';
        atlasState.textContent = 'ERROR';
        atlasState.style.color = '#ff3344';
      }
      sendBtn.disabled = false;
      inputEl.focus();
    }

    async function speakReply(text) {
      try {
        const res  = await fetch('/api/voice/speak', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API_KEY },
          body: JSON.stringify({ message: text })
        });
        const blob  = await res.blob();
        const url   = URL.createObjectURL(blob);
        const audio = new Audio(url);

        let audioCtx, analyser, dataArray, animating = false;
        try {
          audioCtx = new (window.AudioContext || window.webkitAudioContext)();
          const src = audioCtx.createMediaElementSource(audio);
          analyser  = audioCtx.createAnalyser();
          analyser.fftSize = 256;
          src.connect(analyser);
          analyser.connect(audioCtx.destination);
          dataArray = new Uint8Array(analyser.frequencyBinCount);
        } catch(e) { console.warn('Web Audio setup failed:', e.message); }

        function animateMouth() {
          if (!animating) return;
          if (analyser && dataArray) {
            analyser.getByteFrequencyData(dataArray);
            const slice = dataArray.slice(2, 18);
            const avg   = slice.reduce((a, b) => a + b, 0) / slice.length;
            setMouthOpen(Math.min(avg / 70, 1));
          }
          requestAnimationFrame(animateMouth);
        }

        audio.onplay  = () => { animating = true;  setTalking(true);  animateMouth(); };
        audio.onended = () => {
          animating = false;
          setTalking(false);
          if (audioCtx) audioCtx.close().catch(() => {});
          if (!isRecording) resumeWakeWord();
        };

        pauseWakeWord();
        audio.play().catch(e => {
          console.warn('Play failed:', e.message);
          setTalking(false);
          resumeWakeWord();
        });
      } catch(e) { console.warn('TTS failed:', e.message); resumeWakeWord(); }
    }

    async function startRecording() {
      if (isRecording) return;
      try {
        pauseWakeWord();
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks  = [];
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
        mediaRecorder.onstop = async () => {
          stream.getTracks().forEach(t => t.stop());
          isRecording = false;
          micBtn.textContent = '\u23f3'; micBtn.className = 'processing';
          setWakeStatus('processing');
          const blob = new Blob(audioChunks, { type: 'audio/webm' });
          const fd   = new FormData();
          fd.append('audio', blob, 'recording.webm');
          try {
            const res  = await fetch('/api/voice/transcribe', {
              method: 'POST',
              headers: { 'Authorization': 'Bearer ' + API_KEY },
              body: fd
            });
            const data = await res.json();
            if (data.text) {
              let clean = data.text.trim().replace(/^atlas[,.]?\s*/i, '');
              if (clean) await sendMessage(clean);
            }
          } catch(e) { console.warn('Transcribe failed:', e.message); }
          micBtn.textContent = '\ud83c\udf99'; micBtn.className = '';
          setTimeout(() => { if (!isRecording) resumeWakeWord(); }, 5000);
        };
        mediaRecorder.start();
        isRecording = true;
        micBtn.textContent = '\ud83d\udd34'; micBtn.className = 'recording';
        setWakeStatus('recording');
        feedLog('Recording started');
      } catch(e) {
        console.warn('Mic denied:', e.message);
        alert('Microphone access denied.');
        resumeWakeWord();
      }
    }

    function stopRecording() {
      if (!isRecording || !mediaRecorder) return;
      mediaRecorder.stop();
    }

    micBtn.addEventListener('click', () => { isRecording ? stopRecording() : startRecording(); });

    function initWakeWord() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) { setWakeStatus('unsupported'); return; }
      wakeRecognition = new SR();
      wakeRecognition.continuous     = true;
      wakeRecognition.interimResults = true;
      wakeRecognition.lang           = 'en-US';
      let triggered = false;
      wakeRecognition.onresult = (e) => {
        if (isRecording || triggered) return;
        const t = Array.from(e.results).map(r => r[0].transcript).join(' ').toLowerCase();
        if (t.includes('atlas')) {
          triggered = true;
          setTimeout(() => { triggered = false; }, 3000);
          feedLog('Wake word detected');
          startRecording();
        }
      };
      wakeRecognition.onend = () => {
        if (wakeActive) { try { wakeRecognition.start(); } catch(e){} }
      };
      wakeRecognition.onerror = (e) => {
        if (e.error === 'not-allowed') { wakeActive = false; setWakeStatus('unsupported'); }
      };
      wakeActive = true;
      try { wakeRecognition.start(); setWakeStatus('listening'); feedLog('Say "Atlas" to wake'); }
      catch(e) { setWakeStatus('unsupported'); }
    }

    function pauseWakeWord()  {
      if (!wakeRecognition) return;
      wakeActive = false;
      try { wakeRecognition.stop(); } catch(e){}
    }
    function resumeWakeWord() {
      if (!wakeRecognition) return;
      wakeActive = true;
      try { wakeRecognition.start(); } catch(e){}
      setWakeStatus('listening');
    }

    sendBtn.addEventListener('click', () => sendMessage());
    inputEl.addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(); });
    document.querySelectorAll('.nav-tab').forEach(t => {
      t.addEventListener('click', () => {
        document.querySelectorAll('.nav-tab').forEach(x => x.classList.remove('active'));
        t.classList.add('active');
      });
    });

    loadHistory();
    let _wakeStarted = false;
    function maybeStartWakeWord() {
      if (_wakeStarted) return; _wakeStarted = true; initWakeWord();
    }
    document.addEventListener('click',   maybeStartWakeWord);
    document.addEventListener('keydown', maybeStartWakeWord);
  </script>
</body>
</html>
"""
    html = html.replace("__ATLAS_API_KEY__", API_KEY)
    return HTMLResponse(
        content=html,
        headers={"Permissions-Policy": "microphone=*"}
    )


@router.post("/api/chat")
async def chat(req: ChatRequest):
    store   = await get_store()
    session = await store.get_or_create(OWNER_USER_ID, CHANNEL)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in session["conversation_history"][-10:]:
        if m["role"] in ("user", "assistant"):
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": req.message})

    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            "http://localhost:10000/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": "gpt-4o", "messages": messages}
        )
        data  = res.json()
        reply = data["choices"][0]["message"]["content"]

    await store.append_message(OWNER_USER_ID, CHANNEL, "user",      req.message)
    await store.append_message(OWNER_USER_ID, CHANNEL, "assistant", reply)

    return JSONResponse({"reply": reply})