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

from openjarvis.server.crew import crew_access, crew_enabled
from openjarvis.server.media.helix_tools import PendingActions, is_explicit_yes

logger = logging.getLogger(__name__)

CREW_PENDING = PendingActions()
SETUP_STEPS = [
    ("github", "GitHub", "Create a GitHub token that can read and write the repos the crew should use. Paste it into the GitHub box in the Connections panel on the Station.", "https://github.com/settings/tokens"),
    ("render", "Render hosting", "In Render, open Account Settings, go to API Keys, and create a key. Paste it into the Render box in the Connections panel.", "https://dashboard.render.com"),
    ("stripe", "Stripe", "In Stripe, open Developers, then API keys, and copy your secret key. Paste it into the Stripe box in the Connections panel.", "https://dashboard.stripe.com/apikeys"),
    ("workbench", "your PC (Rig)", "Run the Workbench helper on the computer that has your project. The Connections panel shows the command to copy.", ""),
]
OWNER_FIXED_KEYS_HELIX = {"flipdeck", "marnyx", "deadzone"}   # the owner's rows that are not stored (see routes.OWNER_BUSINESSES)

CREW_TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "start_mission",
        "description": "Start a crew mission: Helix plans the goal into tasks and agents (Claude, Gemini) work them in parallel. Costs money, so the system asks the user to confirm first.",
        "parameters": {"type": "object", "properties": {"goal": {"type": "string", "description": "The whole goal in the user's words, with any details he gave."},
                                                        "budget_usd": {"type": "number", "description": "Only if the user named a budget."}}, "required": ["goal"]}}},
    {"type": "function", "function": {
        "name": "crew_status",
        "description": "What the crew is doing: missions, tasks, open questions and checkpoints (with ids), spend.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "answer_crew_question",
        "description": "Pass the user's answer to an agent's open QUESTION (not a checkpoint or budget request; those need his tap on Approve/Decline).",
        "parameters": {"type": "object", "properties": {"question_id": {"type": "string"}, "answer": {"type": "string"}}, "required": ["question_id", "answer"]}}},
    {"type": "function", "function": {
        "name": "add_business",
        "description": "Add a business to the All businesses box on the Station (its name, and optionally its website and a color). Nothing is spent and nothing is published; the user can remove it.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "The business name, as the user said it."},
                                                        "url": {"type": "string", "description": "Its website, if given."},
                                                        "color": {"type": "string", "description": "A hex color like #00e5ff, if given."}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "rename_business",
        "description": "Change the display name of a business you added (its numbers stay with it). The FlipDeck, Marnyx and Deadzone rows cannot be renamed.",
        "parameters": {"type": "object", "properties": {"old_name": {"type": "string"}, "new_name": {"type": "string"}}, "required": ["old_name", "new_name"]}}},
    {"type": "function", "function": {
        "name": "next_setup_step",
        "description": "Setup guide: the next connection the user needs (GitHub, Render, Stripe, then their PC for Rig), with what to do and where. Call it when the user asks to set up or connect, and again after each step.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "rename_mission",
        "description": "Change a mission's title (the name on the Station's missions list). Keeps the mission and its work as they are.",
        "parameters": {"type": "object", "properties": {"mission_id": {"type": "string", "description": "The mission id from crew_status."}, "title": {"type": "string"}}, "required": ["mission_id", "title"]}}},
    {"type": "function", "function": {
        "name": "factory_products",
        "description": "List what a factory has for sale or live: its products and listings. Name a factory: etsy, stripe, google_play, app_store, or a business on the All businesses box.",
        "parameters": {"type": "object", "properties": {"factory": {"type": "string"}}, "required": ["factory"]}}},
    {"type": "function", "function": {
        "name": "check_connections",
        "description": "Check the connections a user needs before missions can run (GitHub, Stripe, Render, their PC for Rig) and say what to connect next.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "remove_business",
        "description": "Remove a business you added from the All businesses box. The FlipDeck, Marnyx and Deadzone rows cannot be removed.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "The business name to remove."}}, "required": ["name"]}}},
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


async def ctx_for(request) -> Optional[CrewCtx]:
    """A crew context only when the crew is on, the engine is running and the caller is an owner account."""
    engine = getattr(request.app.state, "crew_engine", None)
    uid = getattr(request.state, "user_id", "") or ""
    if engine is None or not uid or not crew_enabled() or not await crew_access(request):
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
        how = "the user can answer in chat" if q["kind"] == "question" else "needs Approve/Decline tap"
        lines.append(f'Open {q["kind"]} {q["id"]}: {q["text"]}' + (f' Options: {" / ".join(q["options"])}' if q["options"] and q["kind"] == "question" else "") + f" ({how})")
    sp = st["spend"]
    lines.append(f'Crew spend today ${sp["today_usd"]:.2f}' + (" (no daily cap)" if sp["daily_cap_usd"] is None else f' of ${sp["daily_cap_usd"]:.2f}') + f'. Night Shift {"on" if st["settings"]["night_shift"] else "off"}'
                 f'{", crew paused" if st["settings"]["paused"] else ""}{", not starting work now: " + st["blocked"] if st["blocked"] else ""}.')
    return "\n".join(lines) if st["missions"] or st["questions"] else "No missions yet. " + lines[-1]


async def system_addendum(ctx: CrewCtx) -> str:
    try:
        qs = await ctx.engine.store.list_questions(ctx.user_id, "open")
    except Exception:
        logger.exception("crew questions unavailable")
        qs = []
    s = (" You lead the user's crew of agents. When he asks you to build, make or launch something bigger than one answer (a product, a business, "
         "a campaign, an app), call start_mission with his goal; the system asks him to confirm. Use crew_status when he asks what the crew is doing. "
         "Never say a mission started unless a tool result says so. Checkpoints and budget requests are approved only by his tap on the buttons. "
         "To put a business on the All businesses box, call add_business with the name he gave; never say it was added unless the tool result says so. "
         "When someone asks to set up or connect things, call next_setup_step and walk them through one step at a time; never ask for a key or password in chat.")
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
                return "Error: ask the user what the mission should achieve."
            missing = await eng.missing_for_missions(ctx.user_id)
            if missing:
                return "NOT STARTED: connect " + " and ".join(missing) + " first. Walk the user through it; do not start the mission."
            budget = eng.clamp_budget(args.get("budget_usd")) if args.get("budget_usd") is not None else eng.clamp_budget(None)
            CREW_PENDING.create(ctx.user_id, "start_mission", {"goal": goal, "budget_usd": budget}, budget, f'start a crew mission: "{_clip(goal, 140)}"')
            return f"NOT STARTED YET. Waiting for the user's confirmation. Tell them the crew will plan and work this with a ${budget:.2f} budget, and ask them to reply 'yes'."
        if name == "crew_status":
            return await status_text(ctx)
        if name == "add_business":
            from openjarvis.server.crew import tenant

            name_ = _clip(args.get("name"), 40)
            if tenant.slug(name_) in OWNER_FIXED_KEYS_HELIX:
                return f'"{name_}" is already on the Station.'
            items = await tenant.add_business(eng.store, ctx.user_id, name_, _clip(args.get("url"), 200), _clip(args.get("color"), 9))
            eng.drop_products_cache(ctx.user_id)
            await eng.store.add_event(ctx.user_id, "business", f"Added {name_} to the Station (by Helix).")
            return f'Added "{name_}" to the All businesses box on the Station. It has {len(items)} business(es) you added.'
        if name == "next_setup_step":
            from openjarvis.server.crew import connections as conns

            st = await conns.status(eng.store, ctx.user_id, await eng.env_for(ctx.user_id))
            pc_online = bool(eng.presence.status(ctx.user_id).get("online"))
            done = {"github": st["github"]["connected"], "stripe": st["stripe"]["connected"],
                    "render": st["render"]["connected"], "workbench": pc_online}
            for i, (key, label, how, url) in enumerate(SETUP_STEPS, 1):
                if not done[key]:
                    link = f" Link: {url}." if url else ""
                    return (f"Step {i} of {len(SETUP_STEPS)}: {label}. {how}{link} Do not paste any key or password into this chat. "
                            "When it's done, tell me and I'll check it.")
            return "All four are connected. Missions can run now."
        if name == "rename_mission":
            mid = _clip(args.get("mission_id"), 80)
            m = await eng.store.get_mission(mid)
            if not m or m["user_id"] != ctx.user_id:
                return "Error: no such mission."
            title = _clip(args.get("title"), 80)
            if len(title) < 2:
                return "Give the mission a title."
            await eng.store.update_mission(mid, title=title)
            await eng.store.add_event(ctx.user_id, "mission", f'Renamed to "{title}" (by Helix).', mission_id=mid)
            return f'Mission renamed to "{title}".'
        if name == "factory_products":
            from openjarvis.server.crew import catalog, tenant
            from openjarvis.server.crew import connections as conns_mod
            from openjarvis.server.crew.routes import owner_businesses

            want = _clip(args.get("factory"), 40)
            biz = await owner_businesses(eng, ctx.user_id)
            key = None
            if want.lower().replace(" ", "_") in ("etsy", "stripe", "google_play", "app_store"):
                key = want.lower().replace(" ", "_")
            else:
                hit = [b for b in biz if b["name"].lower() == want.lower() or b["key"] == tenant.slug(want)]
                key = hit[0]["key"] if hit else None
            if not key:
                return f'No factory called "{want}" on the Station.'
            res = await catalog.for_factory(key, store=eng.store, uid=ctx.user_id, env=await eng.env_for(ctx.user_id),
                                            cf=eng.client_factory or conns_mod._default_client, businesses=biz)
            items = res.get("items") or []
            rows = [f'{i.get("name")} ({i.get("status") or "listed"}{", " + i["price"] if i.get("price") else ""})' for i in items[:15]]
            more = f" and {len(items) - 15} more" if len(items) > 15 else ""
            return (f'{want}: ' + ("; ".join(rows) + more if rows else "nothing listed yet") + ". " + (res.get("note") or "")).strip()
        if name == "check_connections":
            from openjarvis.server.crew import connections as conns

            st = await conns.status(eng.store, ctx.user_id, await eng.env_for(ctx.user_id))
            pc = eng.presence.status(ctx.user_id).get("online")
            lines, todo = [], []
            for k in ("github", "stripe", "render"):
                ok = st[k]["connected"]
                lines.append(f'{st[k]["label"]}: {"connected" if ok else "not connected"}')
                if not ok:
                    todo.append(f'{st[k]["label"]}: {st[k]["setup"]}')
            lines.append(f'Your PC (Rig): {"online" if pc else "not online"}')
            if not pc:
                todo.append("Your PC (Rig): run the Workbench helper on the computer that has your project; the Connections panel shows the command.")
            return "\n".join(lines) + ("\nNext: " + " ".join(todo) if todo else "\nAll set: missions can run.")
        if name == "rename_business":
            from openjarvis.server.crew import tenant
            import json as _json

            old, new = _clip(args.get("old_name"), 40), _clip(args.get("new_name"), 40)
            if len(new) < 2:
                return "Give the business a new name."
            if tenant.slug(old) in OWNER_FIXED_KEYS_HELIX:
                return f'"{old}" cannot be renamed: it is one of the fixed rows on the Station.'
            items = await tenant.businesses(eng.store, ctx.user_id)
            hit = [b for b in items if b["name"].lower() == old.lower()]
            if not hit:
                return f'No business called "{old}" that you added.'
            hit[0]["name"] = new                            # the key stays, so its numbers stay with it
            await eng.store.set_secret(ctx.user_id, "businesses", _json.dumps(items))
            eng.drop_products_cache(ctx.user_id)
            await eng.store.add_event(ctx.user_id, "business", f"Renamed {old} to {new} (by Helix).")
            return f'Renamed "{old}" to "{new}" on the All businesses box.'
        if name == "remove_business":
            from openjarvis.server.crew import tenant

            key = tenant.slug(_clip(args.get("name"), 40))
            if key in OWNER_FIXED_KEYS_HELIX:
                return "Not removed: the FlipDeck, Marnyx and Deadzone rows stay on the Station."
            await tenant.remove_business(eng.store, ctx.user_id, key)
            eng.drop_products_cache(ctx.user_id)
            await eng.store.add_event(ctx.user_id, "business", f"Removed {_clip(args.get('name'), 40)} from the Station (by Helix).")
            return f'Removed "{_clip(args.get("name"), 40)}" from the All businesses box.'

        if name == "answer_crew_question":
            q = await eng.store.get_question(str(args.get("question_id") or ""))
            if not q or q["user_id"] != ctx.user_id:
                return "Error: no such open question."
            if q["kind"] != "question":
                return "Not done: checkpoints and budget requests need the user's tap on Approve or Decline in the chat card or the Station."
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
