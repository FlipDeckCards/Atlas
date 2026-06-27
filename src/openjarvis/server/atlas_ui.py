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
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #0d0d0d; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; height: 100vh; display: flex; flex-direction: column; }
    #header { padding: 16px 24px; border-bottom: 1px solid #222; display: flex; align-items: center; justify-content: space-between; }
    #header-title { font-size: 20px; font-weight: 700; color: #fff; letter-spacing: 1px; }
    #header-title span { color: #7c6af7; }
    #wake-status { font-size: 12px; color: #555; display: flex; align-items: center; gap: 6px; }
    #wake-dot { width: 8px; height: 8px; border-radius: 50%; background: #333; }
    #wake-dot.listening { background: #7c6af7; animation: pulse 2s infinite; }
    #wake-dot.recording { background: #f77; animation: pulse 0.5s infinite; }
    #messages { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 12px; }
    .msg { max-width: 75%; padding: 12px 16px; border-radius: 12px; line-height: 1.5; font-size: 15px; }
    .user { align-self: flex-end; background: #7c6af7; color: #fff; border-bottom-right-radius: 4px; }
    .atlas { align-self: flex-start; background: #1a1a1a; color: #e0e0e0; border-bottom-left-radius: 4px; border: 1px solid #2a2a2a; }
    .thinking { color: #555; font-style: italic; }
    #input-row { display: flex; padding: 16px 24px; gap: 12px; border-top: 1px solid #222; align-items: center; }
    #input { flex: 1; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 12px 16px; border-radius: 8px; font-size: 15px; outline: none; }
    #input:focus { border-color: #7c6af7; }
    #send { background: #7c6af7; color: #fff; border: none; padding: 12px 24px; border-radius: 8px; font-size: 15px; cursor: pointer; font-weight: 600; }
    #send:hover { background: #6a5ce0; }
    #send:disabled { background: #333; cursor: not-allowed; }
    #mic { background: #1a1a1a; border: 1px solid #333; color: #aaa; padding: 12px 14px; border-radius: 8px; font-size: 18px; cursor: pointer; transition: all 0.2s; }
    #mic:hover { border-color: #7c6af7; color: #7c6af7; }
    #mic.recording { background: #3a1a1a; border-color: #f77; color: #f77; animation: pulse 1s infinite; }
    #mic.processing { background: #1a1a2a; border-color: #7c6af7; color: #7c6af7; }
    @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }
    #wake-hint { text-align: center; font-size: 12px; color: #444; padding: 4px 0 0; }
  </style>
</head>
<body>
  <div id="header">
    <div id="header-title"><span>Atlas</span> — AI Hub</div>
    <div id="wake-status">
      <div id="wake-dot"></div>
      <span id="wake-label">Initializing...</span>
    </div>
  </div>
  <div id="messages"></div>
  <div id="input-row">
    <button id="mic" title="Click to record">🎙️</button>
    <input id="input" type="text" placeholder='Say "Atlas..." or type here' autocomplete="off"/>
    <button id="send">Send</button>
  </div>
  <script>
    const messagesEl = document.getElementById('messages');
    const input = document.getElementById('input');
    const send = document.getElementById('send');
    const mic = document.getElementById('mic');
    const wakeDot = document.getElementById('wake-dot');
    const wakeLabel = document.getElementById('wake-label');
    const API_KEY = '""" + API_KEY + """';

    // ── State ──────────────────────────────────────────────
    let isRecording = false;
    let mediaRecorder = null;
    let audioChunks = [];
    let wakeRecognition = null;
    let wakeActive = false;

    // ── UI helpers ─────────────────────────────────────────
    function addMsg(role, text) {
      const div = document.createElement('div');
      div.className = 'msg ' + (role === 'user' ? 'user' : 'atlas');
      div.textContent = text;
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return div;
    }

    function setWakeStatus(state) {
      // state: 'off' | 'listening' | 'recording' | 'processing' | 'unsupported'
      wakeDot.className = '';
      if (state === 'listening') {
        wakeDot.classList.add('listening');
        wakeLabel.textContent = 'Listening for "Atlas"';
      } else if (state === 'recording') {
        wakeDot.classList.add('recording');
        wakeLabel.textContent = 'Recording...';
      } else if (state === 'processing') {
        wakeDot.classList.add('listening');
        wakeLabel.textContent = 'Processing...';
      } else if (state === 'unsupported') {
        wakeLabel.textContent = 'Wake word unavailable';
      } else {
        wakeLabel.textContent = 'Wake word off';
      }
    }

    // ── History ────────────────────────────────────────────
    async function loadHistory() {
      try {
        const res = await fetch('/api/session/history', {
          headers: { 'Authorization': 'Bearer ' + API_KEY }
        });
        const data = await res.json();
        (data.messages || []).forEach(m => addMsg(m.role, m.content));
      } catch(e) { console.warn('Could not load history:', e.message); }
    }

    // ── Send message ───────────────────────────────────────
    async function sendMessage(text) {
      if (!text) text = input.value.trim();
      if (!text) return;
      input.value = '';
      send.disabled = true;
      addMsg('user', text);
      const thinking = addMsg('atlas', 'Thinking...');
      thinking.classList.add('thinking');

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
        speakReply(reply);
      } catch(e) {
        thinking.textContent = 'Error: ' + e.message;
        thinking.classList.remove('thinking');
      }
      send.disabled = false;
      input.focus();
    }

    // ── TTS ────────────────────────────────────────────────
    async function speakReply(text) {
      try {
        const res = await fetch('/api/voice/speak', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API_KEY },
          body: JSON.stringify({ message: text })
        });
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => {
          // Resume wake word detection after Atlas finishes speaking
          if (!isRecording) resumeWakeWord();
        };
        // Pause wake word while Atlas is speaking (avoid feedback loop)
        pauseWakeWord();
        audio.play();
      } catch(e) { console.warn('TTS failed:', e.message); resumeWakeWord(); }
    }

    // ── Whisper recording ──────────────────────────────────
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
          mic.textContent = '⏳';
          mic.className = 'processing';
          setWakeStatus('processing');

          const blob = new Blob(audioChunks, { type: 'audio/webm' });
          const formData = new FormData();
          formData.append('audio', blob, 'recording.webm');
          try {
            const res = await fetch('/api/voice/transcribe', {
              method: 'POST',
              headers: { 'Authorization': 'Bearer ' + API_KEY },
              body: formData
            });
            const data = await res.json();
            if (data.text) {
              // Strip the wake word if it's at the start (e.g. "Atlas what's the market doing")
              let clean = data.text.trim();
              clean = clean.replace(/^atlas[,.]?\s*/i, '');
              if (clean) await sendMessage(clean);
            }
          } catch(e) { console.warn('Transcription failed:', e.message); }

          mic.textContent = '🎙️';
          mic.className = '';
          // speakReply handles resumeWakeWord after audio finishes
          // but if TTS fails we still need to resume:
          setTimeout(() => { if (!isRecording) resumeWakeWord(); }, 5000);
        };
        mediaRecorder.start();
        isRecording = true;
        mic.textContent = '🔴';
        mic.className = 'recording';
        setWakeStatus('recording');
      } catch(e) {
        console.warn('Mic access denied:', e.message);
        alert('Microphone access denied. Please allow mic permissions and try again.');
        resumeWakeWord();
      }
    }

    function stopRecording() {
      if (!isRecording || !mediaRecorder) return;
      mediaRecorder.stop();
    }

    // ── Click-to-toggle mic ────────────────────────────────
    mic.addEventListener('click', () => {
      if (isRecording) {
        stopRecording();
      } else {
        startRecording();
      }
    });

    // ── Wake word: "Atlas" ─────────────────────────────────
    function initWakeWord() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) {
        setWakeStatus('unsupported');
        return;
      }

      wakeRecognition = new SR();
      wakeRecognition.continuous = true;
      wakeRecognition.interimResults = true;
      wakeRecognition.lang = 'en-US';

      let triggered = false;

      wakeRecognition.onresult = (event) => {
        if (isRecording || triggered) return;
        const transcript = Array.from(event.results)
          .map(r => r[0].transcript)
          .join(' ')
          .toLowerCase();

        if (transcript.includes('atlas')) {
          triggered = true;
          setTimeout(() => { triggered = false; }, 3000); // debounce
          startRecording();
        }
      };

      wakeRecognition.onend = () => {
        // Auto-restart unless we paused it intentionally
        if (wakeActive) {
          try { wakeRecognition.start(); } catch(e) {}
        }
      };

      recognition.onerror = function(event) {
    console.log('Wake word error: ' + event.error);
    if (event.error === 'not-allowed') {
        console.log('Mic permission denied — not retrying.');
        return; // stop the loop
    }
    startWakeWordDetection();
};

      wakeActive = true;
      try {
        wakeRecognition.start();
        setWakeStatus('listening');
      } catch(e) {
        setWakeStatus('unsupported');
      }
    }

    function pauseWakeWord() {
      if (!wakeRecognition) return;
      wakeActive = false;
      try { wakeRecognition.stop(); } catch(e) {}
    }

    function resumeWakeWord() {
      if (!wakeRecognition) return;
      wakeActive = true;
      try { wakeRecognition.start(); } catch(e) {}
      setWakeStatus('listening');
    }

    // ── Keyboard ───────────────────────────────────────────
    send.addEventListener('click', () => sendMessage());
    input.addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(); });

    // ── Boot ───────────────────────────────────────────────
    loadHistory();
    // Small delay so browser is ready for mic permission request
    setTimeout(initWakeWord, 1000);
  </script>
</body>
</html>
"""
    return HTMLResponse(
    content=html,
    headers={"Permissions-Policy": "microphone=*"}
)


@router.post("/api/chat")
async def chat(req: ChatRequest):
    store = await get_store()
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
        data = res.json()
        reply = data["choices"][0]["message"]["content"]

    await store.append_message(OWNER_USER_ID, CHANNEL, "user", req.message)
    await store.append_message(OWNER_USER_ID, CHANNEL, "assistant", reply)

    return JSONResponse({"reply": reply})