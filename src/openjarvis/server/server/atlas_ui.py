import os
import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()

API_KEY = os.getenv("OPENJARVIS_API_KEY", "")
SYSTEM_PROMPT = """You are Atlas, a personal AI hub and orchestrator built by Michael.
You coordinate a network of specialized agents: OMNI (trading advisor), FlipDeck (card flipping tool), and Deadzone (games platform).
You have persistent memory and can route tasks to the right spoke. You are not OpenJarvis — you are Atlas.
Be direct, sharp, and helpful."""

class ChatRequest(BaseModel):
    message: str
    history: list = []

@router.post("/api/chat")
async def chat(req: ChatRequest):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in req.history[-10:]:
        if m.get("role") in ("user", "assistant"):
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
        return JSONResponse({"reply": reply})