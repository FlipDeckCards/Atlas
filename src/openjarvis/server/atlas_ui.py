import os
import httpx
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
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

async def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
        await _store.connect()
    return _store


class ChatRequest(BaseModel):
    message: str


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
    .s-dot.on  { background: #00ff88; box-shadow: 0 0 6px #00ff88; animation: blink 2s infinite; }
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
    #face-wrap { position:relative; width:240px; height:240px; }
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
    #atlas-face {
      width:100%; height:100%;
      filter: drop-shadow(0 0 10px var(--cyan)) drop-shadow(0 0 22px rgba(0,229,255,0.25));
      animation: idle-breathe 4s ease-in-out infinite;
      transition: filter 0.3s;
    }
    #atlas-face.talking {
      filter: drop-shadow(0 0 16px var(--cyan)) drop-shadow(0 0 36px rgba(0,229,255,0.5)) drop-shadow(0 0 8px var(--orange));
    }
    @keyframes idle-breathe { 0%,100%{transform:scale(1)} 50%{transform:scale(1.006)} }
    @keyframes eye-blink {
      0%,90%,100%{transform:scaleY(1)} 93%,97%{transform:scaleY(0.06)}
    }
    #eye-l { transform-origin:76px 90px; animation:eye-blink 5s ease-in-out infinite; }
    #eye-r { transform-origin:124px 90px; animation:eye-blink 5s ease-in-out 0.2s infinite; }
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
    #wake-dot.listening { background:var(--cyan); box-shadow:0 0 8px var(--cyan); animation:blink 2s infinite; }
    #wake-dot.recording { background:#ff3344; box-shadow:0 0 8px #ff3344; animation:blink 0.4s infinite; }
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
    .user { align-self:flex-end; background:rgba(255,109,0,0.08); border:1px solid rgba(255,109,0,0.25); color:#ffa040; }
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
    #mic.recording { border-color:#ff3344; color:#ff3344; background:rgba(255,51,68,0.08); animation:blink 0.5s infinite; }
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
    .nav-tab:hover { color:var(--text); background:rgba(0,229,255,0.03); }
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
        <div class="feed-line">▸ Atlas core initialized</div>
        <div class="feed-line">▸ Neon DB connected</div>
        <div class="feed-line">▸ Voice engine loaded</div>
        <div class="feed-line" id="feed-last">▸ Awaiting input...</div>
      </div>
    </div>

    <!-- CENTER -->
    <div id="panel-center">
      <div class="corner tl"></div><div class="corner tr"></div>
      <div class="corner bl"></div><div class="corner br"></div>
      <div class="face-label">CORE INTERFACE</div>
      <div id="face-wrap">
        <div class="f-ring"></div>
        <div class="f-ring2"></div>
        <svg id="atlas-face" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
          <!-- outer rings -->
          <circle cx="100" cy="100" r="88" fill="none" stroke="#00e5ff" stroke-width="0.5" opacity="0.15"/>
          <circle cx="100" cy="100" r="82" fill="none" stroke="#00e5ff" stroke-width="0.3" opacity="0.1"/>
          <!-- face base -->
          <circle cx="100" cy="100" r="76" fill="#060610" stroke="#00e5ff" stroke-width="1.2"/>
          <!-- forehead lines -->
          <line x1="80" y1="63" x2="80" y2="72" stroke="#00e5ff" stroke-width="0.6" opacity="0.3"/>
          <line x1="100" y1="59" x2="100" y2="71" stroke="#00e5ff" stroke-width="0.6" opacity="0.4"/>
          <line x1="120" y1="63" x2="120" y2="72" stroke="#00e5ff" stroke-width="0.6" opacity="0.3"/>
          <line x1="86" y1="66" x2="114" y2="66" stroke="#00e5ff" stroke-width="0.4" opacity="0.2"/>
          <!-- cheek accents -->
          <line x1="34" y1="100" x2="52" y2="100" stroke="#00e5ff" stroke-width="0.5" opacity="0.18"/>
          <line x1="148" y1="100" x2="166" y2="100" stroke="#00e5ff" stroke-width="0.5" opacity="0.18"/>
          <!-- eye sockets -->
          <ellipse cx="76" cy="90" rx="14" ry="10" fill="#020210" stroke="#00e5ff" stroke-width="0.8" opacity="0.5"/>
          <ellipse cx="124" cy="90" rx="14" ry="10" fill="#020210" stroke="#00e5ff" stroke-width="0.8" opacity="0.5"/>
          <!-- eyes (blink via CSS) -->
          <g id="eye-l">
            <ellipse cx="76" cy="90" rx="9" ry="7" fill="#00e5ff" opacity="0.85"/>
            <ellipse cx="76" cy="90" rx="5" ry="5" fill="#001a2a"/>
            <ellipse cx="78" cy="88" rx="2" ry="2" fill="#00e5ff" opacity="0.9"/>
          </g>
          <g id="eye-r">
            <ellipse cx="124" cy="90" rx="9" ry="7" fill="#00e5ff" opacity="0.85"/>
            <ellipse cx="124" cy="90" rx="5" ry="5" fill="#001a2a"/>
            <ellipse cx="126" cy="88" rx="2" ry="2" fill="#00e5ff" opacity="0.9"/>
          </g>
          <!-- nose bridge -->
          <path d="M 96 100 L 92 112 L 108 112 L 104 100" fill="none" stroke="#00e5ff" stroke-width="0.7" opacity="0.2"/>
          <!-- mouth (JS-driven) -->
          <path id="mouth-upper" d="M 78 124 Q 100 121 122 124" fill="none" stroke="#00e5ff" stroke-width="1.5" stroke-linecap="round"/>
          <path id="mouth-lower" d="M 78 124 Q 100 127 122 124" fill="none" stroke="#00e5ff" stroke-width="1.5" stroke-linecap="round"/>
          <path id="mouth-fill"  d="M 78 124 Q 100 121 122 124 Q 100 127 78 124" fill="#001a1a" opacity="0"/>
          <!-- chin -->
          <line x1="88" y1="150" x2="112" y2="150" stroke="#00e5ff" stroke-width="0.5" opacity="0.18"/>
        </svg>
      </div>
      <div class="face-state">
        <div id="atlas-state">STANDBY</div>
        <div>▸ <span id="center-sub">WAITING FOR INPUT</span></div>
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
        <button id="mic" title="Record">🎙</button>
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
    const atlasState = document.getElementById('atlas-state');
    const centerSub  = document.getElementById('center-sub');
    const voiceStat  = document.getElementById('voice-status');
    const API_KEY    = '""" + API_KEY + """';

    // ── Uptime ─────────────────────────────────────────
    const uptimeStart = Date.now();
    setInterval(() => {
      const s = Math.floor((Date.now() - uptimeStart) / 1000);
      const h = String(Math.floor(s/3600)).padStart(2,'0');
      const m = String(Math.floor((s%3600)/60)).padStart(2,'0');
      const sc = String(s%60).padStart(2,'0');
      document.getElementById('uptime').textContent = h+':'+m+':'+sc;
    }, 1000);

    // ── State ──────────────────────────────────────────
    let isRecording = false, mediaRecorder = null, audioChunks = [];
    let wakeRecognition = null, wakeActive = false;

    // ── Mouth / face ───────────────────────────────────
    function setMouthOpen(a) {
      // a = 0.0 closed → 1.0 max open
      const y = 124, w = 22, drop = a * 22;
      const U = `M ${100-w} ${y} Q 100 ${y-3} ${100+w} ${y}`;
      const L = `M ${100-w} ${y} Q 100 ${y+3+drop} ${100+w} ${y}`;
      const F = `M ${100-w} ${y} Q 100 ${y-3} ${100+w} ${y} Q 100 ${y+3+drop} ${100-w} ${y}`;
      document.getElementById('mouth-upper').setAttribute('d', U);
      document.getElementById('mouth-lower').setAttribute('d', L);
      const mf = document.getElementById('mouth-fill');
      mf.setAttribute('d', F);
      mf.style.opacity = a > 0.05 ? String(0.35 + a*0.45) : '0';
    }

    function setTalking(on) {
      if (on) {
        atlasFace.classList.add('talking');
        atlasState.textContent = 'SPEAKING';
        atlasState.style.color = 'var(--orange)';
        centerSub.textContent  = 'TRANSMITTING AUDIO';
        voiceStat.textContent  = 'SPEAKING';
      } else {
        atlasFace.classList.remove('talking');
        atlasState.textContent = 'STANDBY';
        atlasState.style.color = 'var(--cyan)';
        centerSub.textContent  = 'WAITING FOR INPUT';
        voiceStat.textContent  = 'READY';
        setMouthOpen(0);
      }
    }

    // ── Wake status ────────────────────────────────────
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

    // ── Live feed ──────────────────────────────────────
    function feedLog(text) {
      const feed = document.getElementById('live-feed');
      const d = document.createElement('div');
      d.className = 'feed-line';
      d.textContent = '▸ ' + text;
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

    // ── Message bubble ─────────────────────────────────
    function addMsg(role, text) {
      const wrap = document.createElement('div');
      wrap.className = 'msg ' + (role === 'user' ? 'user' : 'atlas');
      const tag = document.createElement('div');
      tag.className = 'msg-tag';
      tag.textContent = role === 'user' ? 'YOU' : 'ATLAS';
      const body = document.createElement('div');
      body.textContent = text;
      wrap.appendChild(tag); wrap.appendChild(body);
      messagesEl.appendChild(wrap);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return wrap;
    }

    // ── Load history ───────────────────────────────────
    async function loadHistory() {
      try {
        const res = await fetch('/api/session/history', {
          headers: { 'Authorization': 'Bearer ' + API_KEY }
        });
        const data = await res.json();
        (data.messages || []).forEach(m => addMsg(m.role, m.content));
        if ((data.messages||[]).length) feedLog('Session history loaded');
      } catch(e) { console.warn('History load failed:', e.message); }
    }

    // ── Send message ───────────────────────────────────
    async function sendMessage(text) {
      if (!text) text = inputEl.value.trim();
      if (!text) return;
      inputEl.value = '';
      sendBtn.disabled = true;
      addMsg('user', text);
      feedLog('You: ' + text.slice(0,40) + (text.length>40?'...':''));
      const thinking = addMsg('atlas', 'Processing...');
      thinking.querySelector('div:last-child').style.color = 'var(--text-dim)';
      atlasState.textContent = 'THINKING';
      atlasState.style.color = 'var(--orange)';
      try {
        const res = await fetch('/api/chat', {
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

    // ── TTS + lip-sync ─────────────────────────────────
    async function speakReply(text) {
      try {
        const res = await fetch('/api/voice/speak', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API_KEY },
          body: JSON.stringify({ message: text })
        });
        const blob = await res.blob();
        const url  = URL.createObjectURL(blob);
        const audio = new Audio(url);

        let audioCtx, analyser, dataArray, animating = false;
        try {
          audioCtx = new (window.AudioContext || window.webkitAudioContext)();
          const src = audioCtx.createMediaElementSource(audio);
          analyser = audioCtx.createAnalyser();
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
            const avg   = slice.reduce((a,b) => a+b, 0) / slice.length;
            setMouthOpen(Math.min(avg / 70, 1));
          }
          requestAnimationFrame(animateMouth);
        }

        audio.onplay   = () => { animating = true;  setTalking(true);  animateMouth(); };
        audio.onended  = () => {
          animating = false; setTalking(false);
          if (audioCtx) audioCtx.close().catch(()=>{});
          if (!isRecording) resumeWakeWord();
        };

        pauseWakeWord();
        audio.play().catch(e => { console.warn('Play failed:', e.message); setTalking(false); resumeWakeWord(); });
      } catch(e) { console.warn('TTS failed:', e.message); resumeWakeWord(); }
    }

    // ── Whisper recording ──────────────────────────────
    async function startRecording() {
      if (isRecording) return;
      try {
        pauseWakeWord();
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
        mediaRecorder.onstop = async () => {
          stream.getTracks().forEach(t => t.stop());
          isRecording = false;
          micBtn.textContent = '⏳'; micBtn.className = 'processing';
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
              let clean = data.text.trim().replace(/^atlas[,.]?\\s*/i, '');
              if (clean) await sendMessage(clean);
            }
          } catch(e) { console.warn('Transcribe failed:', e.message); }
          micBtn.textContent = '🎙'; micBtn.className = '';
          setTimeout(() => { if (!isRecording) resumeWakeWord(); }, 5000);
        };
        mediaRecorder.start();
        isRecording = true;
        micBtn.textContent = '🔴'; micBtn.className = 'recording';
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

    // ── Wake word ──────────────────────────────────────
    function initWakeWord() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) { setWakeStatus('unsupported'); return; }
      wakeRecognition = new SR();
      wakeRecognition.continuous = true;
      wakeRecognition.interimResults = true;
      wakeRecognition.lang = 'en-US';
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
      wakeRecognition.onend = () => { if (wakeActive) { try { wakeRecognition.start(); } catch(e){} } };
      wakeRecognition.onerror = (e) => {
        if (e.error === 'not-allowed') { wakeActive = false; setWakeStatus('unsupported'); }
      };
      wakeActive = true;
      try { wakeRecognition.start(); setWakeStatus('listening'); feedLog('Say "Atlas" to wake'); }
      catch(e) { setWakeStatus('unsupported'); }
    }

    function pauseWakeWord()  { if (!wakeRecognition) return; wakeActive = false; try { wakeRecognition.stop(); } catch(e){} }
    function resumeWakeWord() {
      if (!wakeRecognition) return;
      wakeActive = true; try { wakeRecognition.start(); } catch(e){}
      setWakeStatus('listening');
    }

    // ── Events ─────────────────────────────────────────
    sendBtn.addEventListener('click', () => sendMessage());
    inputEl.addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(); });
    document.querySelectorAll('.nav-tab').forEach(t => {
      t.addEventListener('click', () => {
        document.querySelectorAll('.nav-tab').forEach(x => x.classList.remove('active'));
        t.classList.add('active');
      });
    });

    // ── Boot ───────────────────────────────────────────
    loadHistory();
    let _wakeStarted = false;
    function maybeStartWakeWord() {
      if (_wakeStarted) return; _wakeStarted = true; initWakeWord();
    }
    document.addEventListener('click', maybeStartWakeWord);
    document.addEventListener('keydown', maybeStartWakeWord);
  </script>
</body>
    """
    return HTMLResponse(
        content=html,
        headers={"Permissions-Policy": "microphone=*"}
    )