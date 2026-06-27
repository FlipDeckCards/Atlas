import os
import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import Optional

from openjarvis.server.session_store import SessionStore


router = APIRouter()

API_KEY = os.getenv("OPENJARVIS_API_KEY", "")
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
    session = await store.get_or_create(OWNER_USER_ID, CHANNEL)  # FIXED: added CHANNEL
    return JSONResponse({
        "messages": [
            {"role": m["role"], "content": m["content"]}  # FIXED: dict access
            for m in session["conversation_history"]       # FIXED: dict access
            if m["role"] in ("user", "assistant")
        ]
    })


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
    #header { padding: 16px 24px; border-bottom: 1px solid #222; font-size: 20px; font-weight: 700; color: #fff; letter-spacing: 1px; }
    #header span { color: #7c6af7; }
    #messages { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 12px; }
    .msg { max-width: 75%; padding: 12px 16px; border-radius: 12px; line-height: 1.5; font-size: 15px; }
    .user { align-self: flex-end; background: #7c6af7; color: #fff; border-bottom-right-radius: 4px; }
    .atlas { align-self: flex-start; background: #1a1a1a; color: #e0e0e0; border-bottom-left-radius: 4px; border: 1px solid #2a2a2a; }
    .thinking { color: #555; font-style: italic; }
    #input-row { display: flex; padding: 16px 24px; gap: 12px; border-top: 1px solid #222; }
    #input { flex: 1; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 12px 16px; border-radius: 8px; font-size: 15px; outline: none; }
    #input:focus { border-color: #7c6af7; }
    #send { background: #7c6af7; color: #fff; border: none; padding: 12px 24px; border-radius: 8px; font-size: 15px; cursor: pointer; font-weight: 600; }
    #send:hover { background: #6a5ce0; }
    #send:disabled { background: #333; cursor: not-allowed; }
  </style>
</head>
<body>
  <div id="header"><span>Atlas</span> — AI Hub</div>
  <div id="messages"></div>
  <div id="input-row">
    <input id="input" type="text" placeholder="Message Atlas..." autocomplete="off"/>
    <button id="send">Send</button>
  </div>
  <script>
    const messagesEl = document.getElementById('messages');
    const input = document.getElementById('input');
    const send = document.getElementById('send');
    const API_KEY = '""" + API_KEY + """';

    function addMsg(role, text) {
      const div = document.createElement('div');
      div.className = 'msg ' + (role === 'user' ? 'user' : 'atlas');
      div.textContent = text;
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return div;
    }

    async function loadHistory() {
      try {
        const res = await fetch('/api/session/history', {
          headers: { 'Authorization': 'Bearer ' + API_KEY }
        });
        const data = await res.json();
        (data.messages || []).forEach(m => addMsg(m.role, m.content));
      } catch(e) {
        console.warn('Could not load history:', e.message);
      }
    }

    async function sendMessage() {
      const text = input.value.trim();
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
      } catch(e) {
        thinking.textContent = 'Error: ' + e.message;
        thinking.classList.remove('thinking');
      }
      send.disabled = false;
      input.focus();
    }

    send.addEventListener('click', sendMessage);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(); });

    loadHistory();
  </script>
</body>
</html>
"""
    return HTMLResponse(html)


@router.post("/api/chat")
async def chat(req: ChatRequest):
    store = await get_store()
    session = await store.get_or_create(OWNER_USER_ID, CHANNEL)  # FIXED: added CHANNEL

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in session["conversation_history"][-10:]:  # FIXED: dict access
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

    # FIXED: was save_message — now uses append_message with correct signature
    await store.append_message(OWNER_USER_ID, CHANNEL, "user", req.message)
    await store.append_message(OWNER_USER_ID, CHANNEL, "assistant", reply)

    return JSONResponse({"reply": reply})