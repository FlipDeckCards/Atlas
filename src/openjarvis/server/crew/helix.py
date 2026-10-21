"""Helix as the crew's front door: Michael talks to Helix; Helix starts missions, reports on them and carries answers to the agents.

Tools offered to the chat model (owner accounts only, crew enabled):
  start_mission       never starts on the model's say-so: it is read back with its budget and runs only when Michael's NEXT message is
                      an explicit yes (the same rule as Helix's paid media tools)
  crew_status         what the crew is doing, what it is waiting for, what it has spent
  answer_crew_question  passes Michael's answer to an agent's plain question
  pause_mission / resume_mission
Checkpoints (publish, charge, anything irreversible) and budget requests are NOT answered through the model: Michael approves or
declines them with the buttons in the chat card or the Station, so a model can never approve its own action.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openjarvis.server.crew import crew_allowed, crew_enabled
from openjarvis.server.media.helix_tools import PendingActions, is_explicit_yes

logger = logging.getLogger(__name__)

CREW_PENDING = PendingActions()

CREW_TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "start_mission",
        "description": "Start a crew mission: Helix plans the goal into tasks and agents (Claude, Gemini) work them in parallel. Costs money, so the system asks Michael to confirm first.",
        "parameters": {"type": "object", "properties": {"goal": {"type": "string", "description": "The whole goal in Michael's words, with any details he gave."},
                                                        "budget_usd": {"type": "number", "description": "Only if Michael named a budget."}}, "required": ["goal"]}}},
    {"type": "function", "function": {
        "name": "crew_status",
        "description": "What the crew is doing: missions, tasks, open questions and checkpoints (with ids), spend.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "answer_crew_question",
        "description": "Pass Michael's answer to an agent's open QUESTION (not a checkpoint or budget request; those need his tap on Approve/Decline).",
        "parameters": {"type": "object", "properties": {"question_id": {"type": "string"}, "answer": {"type": "string"}}, "required": ["question_id", "answer"]}}},
    {"type": "function", "function": {
        "name": "pause_mission",
        "description": "Pause a mission (its agents stop starting new steps).",
        "parameters": {"type": "object", "properties": {"mission_id": {"type": "string"}}, "required": ["mission_id"]}}},
    {"type": "function", "function": {
        "name": "resume_mission",
        "description": "Resume a paused or stalled mission.",
        "parameters": {"type": "object", "properties": {"mission_id": {"type": "string"}}, "required": ["mission_id"]}}},
]


@dataclass
class CrewCtx:
    user_id: str
    engine: Any


def ctx_for(request) -> Optional[CrewCtx]:
    """A crew context only when the crew is on, the engine is running and the caller is an owner account."""
    engine = getattr(request.app.state, "crew_engine", None)
    email = getattr(request.state, "user_email", "") or ""
    uid = getattr(request.state, "user_id", "") or ""
    if engine is None or not uid or not crew_enabled() or not crew_allowed(email):
        return None
    return CrewCtx(uid, engine)


def _clip(v: Any, n: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:n]


async def status_text(ctx: CrewCtx) -> str:
    st = await ctx.engine.state(ctx.user_id, missions_limit=6)
    lines = []
    for m in st["missions"]:
        done = sum(1 for t in m["tasks"] if t["status"] == "done")
        lines.append(f'Mission {m["id"]} "{m["title"] or _clip(m["goal"], 60)}" [{m["status"]}] {done}/{len(m["tasks"])} tasks done, '
                     f'${m["spent_usd"]:.2f} of ${m["budget_usd"]:.2f}, {len(m["outputs"])} file(s)')
        for t in m["tasks"]:
            if t["status"] in ("running", "waiting", "failed"):
                lines.append(f'  - {t["title"]} ({t["role"]}): {t["status"]}')
    for q in st["questions"]:
        how = "Michael can answer in chat" if q["kind"] == "question" else "needs Approve/Decline tap"
        lines.append(f'Open {q["kind"]} {q["id"]}: {q["text"]}' + (f' Options: {" / ".join(q["options"])}' if q["options"] and q["kind"] == "question" else "") + f" ({how})")
    sp = st["spend"]
    lines.append(f'Crew spend today ${sp["today_usd"]:.2f} of ${sp["daily_cap_usd"]:.2f}. Night Shift {"on" if st["settings"]["night_shift"] else "off"}'
                 f'{", crew paused" if st["settings"]["paused"] else ""}{", not starting work now: " + st["blocked"] if st["blocked"] else ""}.')
    return "\n".join(lines) if st["missions"] or st["questions"] else "No missions yet. " + lines[-1]


async def system_addendum(ctx: CrewCtx) -> str:
    try:
        qs = await ctx.engine.store.list_questions(ctx.user_id, "open")
    except Exception:
        logger.exception("crew questions unavailable")
        qs = []
    s = (" You lead Michael's crew of agents. When he asks you to build, make or launch something bigger than one answer (a product, a business, "
         "a campaign, an app), call start_mission with his goal; the system asks him to confirm. Use crew_status when he asks what the crew is doing. "
         "Never say a mission started unless a tool result says so. Checkpoints and budget requests are approved only by his tap on the buttons.")
    if qs:
        s += " Open crew items: " + " | ".join(f'{q["kind"]} {q["id"]}: {_clip(q["text"], 160)}' for q in qs[:5])
        s += ". If his message answers an open question, call answer_crew_question with that id."
    return s


async def execute(ctx: CrewCtx, name: str, args: Dict[str, Any]) -> str:
    eng = ctx.engine
    try:
        if name == "start_mission":
            goal = str(args.get("goal") or "").strip()[:4000]
            if len(goal) < 8:
                return "Error: ask Michael what the mission should achieve."
            budget = eng.clamp_budget(args.get("budget_usd")) if args.get("budget_usd") is not None else eng.clamp_budget(None)
            CREW_PENDING.create(ctx.user_id, "start_mission", {"goal": goal, "budget_usd": budget}, budget, f'start a crew mission: "{_clip(goal, 140)}"')
            return f"NOT STARTED YET. Waiting for Michael's confirmation. Tell him the crew will plan and work this with a ${budget:.2f} budget, and ask him to reply 'yes'."
        if name == "crew_status":
            return await status_text(ctx)
        if name == "answer_crew_question":
            q = await eng.store.get_question(str(args.get("question_id") or ""))
            if not q or q["user_id"] != ctx.user_id:
                return "Error: no such open question."
            if q["kind"] != "question":
                return "Not done: checkpoints and budget requests need Michael's tap on Approve or Decline in the chat card or the Station."
            out = await eng.answer(ctx.user_id, q["id"], answer=str(args.get("answer") or ""))
            return out["message"]
        if name in ("pause_mission", "resume_mission"):
            fn = eng.pause_mission if name == "pause_mission" else eng.resume_mission
            m = await fn(ctx.user_id, str(args.get("mission_id") or ""))
            return f'Mission "{m["title"] or _clip(m["goal"], 60)}" is now {m["status"]}.'
    except (LookupError, ValueError) as exc:
        return f"Could not do that: {exc}"
    except Exception:
        logger.exception("crew tool %s failed", name)
        return "Could not do that right now."
    return "Error: unsupported."


async def pre_turn(ctx: CrewCtx, message: str) -> Optional[Dict[str, Any]]:
    """A yes to a read-back mission start. Any other message cancels the pending start and carries on to the model."""
    from openjarvis.server.media.helix_tools import PENDING as MEDIA_PENDING

    p = CREW_PENDING.get(ctx.user_id)
    if not p:
        return None
    CREW_PENDING.clear(ctx.user_id)
    if not (p.armed and is_explicit_yes(message)):
        return None
    MEDIA_PENDING.clear(ctx.user_id)              # one yes confirms one thing
    try:
        m = await ctx.engine.start_mission(ctx.user_id, p.args["goal"], p.args["budget_usd"])
    except ValueError as exc:
        return {"reply": f"I couldn't start it: {exc}", "ui": []}
    return {"reply": f'Mission started with a ${m["budget_usd"]:.2f} budget. The crew is planning it now; I will post progress, questions and approvals '
                     "here, and you can watch it live in the Station.", "ui": [{"type": "crew_refresh"}]}


def after_turn(ctx: CrewCtx, reply: str, created_before: Any) -> str:
    """The read-back for a mission start requested in this turn, in the server's own words; then the next message can confirm it."""
    p = CREW_PENDING.get(ctx.user_id)
    if p and p is not created_before and not p.armed:
        reply = (reply.strip() + "\n\n" if reply.strip() else "") + (
            f"Ready to {p.description} with a ${p.estimate_usd:.2f} budget. The crew asks before spending more or doing anything public. Reply 'yes' to go ahead.")
        CREW_PENDING.arm(ctx.user_id)
    return reply
