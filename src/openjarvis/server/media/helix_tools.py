"""Helix's tools: lets the chat model create and manage media, with the server (not the model) enforcing every rule.

Tools: generate_image, generate_speech (paid), list_library, open_item, edit_item (free).

Rules enforced here, whatever the model does:
  * A paid tool call NEVER runs on the model's say-so. It is recorded as a pending action with an estimated cost; the model reads it back
    to the person; the action runs only when the person's NEXT message is an explicit yes. Any other message cancels it. A yes in the same
    turn as the request cannot confirm it (the action is armed only after the read-back reply is produced). Voice is the same: the
    transcribed words must be an explicit yes.
  * Tools act only for the caller (the user id comes from the logged-in request, never from the model's arguments) and go through
    service.py, so the media cap, global ceiling, rate limits and storage quota are checked before any provider call.
  * An unknown tool name is refused. generate_speech is speech only: requests for sound effects are refused.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from openjarvis.server.media import service
from openjarvis.server.media.service import Ctx, MediaServiceError

logger = logging.getLogger(__name__)

PAID_TOOLS = {"generate_image", "generate_speech"}
FREE_TOOLS = {"list_library", "open_item", "edit_item"}
PENDING_TTL_S = 10 * 60
MAX_TOOL_ROUNDS = 3

TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "generate_image",
        "description": "Make an image from a text description and save it to the user's library. This costs money, so the system asks the user to confirm first.",
        "parameters": {"type": "object", "properties": {"prompt": {"type": "string", "description": "What the image shows (500 characters at most)."}}, "required": ["prompt"]}}},
    {"type": "function", "function": {
        "name": "generate_speech",
        "description": "Turn text into spoken audio (speech only, NOT sound effects or music) and save it to the library. Costs money, so the system asks the user to confirm first.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "The words to speak (1000 characters at most)."}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "list_library",
        "description": "List the user's saved media (titles and ids).",
        "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["all", "image", "sound"]}}}}},
    {"type": "function", "function": {
        "name": "open_item",
        "description": "Open a saved item in the viewer panel.",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "edit_item",
        "description": "Edit a saved item and save the result as a NEW item (the original is kept). Images: crop(left,top,right,bottom), resize(width,height), brightness(factor), contrast(factor). Sound: trim(start,end), fade_in(seconds), fade_out(seconds), volume(factor).",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "operation": {"type": "string"}, "params": {"type": "object"}}, "required": ["id", "operation"]}}},
]

SYSTEM_ADDENDUM = (
    " You can create and manage the user's media with tools. For generate_image and generate_speech the system will ask the user to confirm: after "
    "calling one, say in one sentence exactly what you will make and that it needs their yes; never say something was created unless a tool result says "
    "'Saved'. You can only do speech, not sound effects or music. Use list_library, open_item and edit_item for the user's own library only."
)

SFX_REPLY = (
    "I can't generate sound effects like that. I can read text aloud as speech, or look through your saved library for something that fits. "
    "Want me to do either?"
)

_SFX = re.compile(
    r"\b(sound[- ]?effects?|sfx|foley|gun ?shots?|gunfire|explosions?|explode|footsteps?|door (?:slam|creak)|thunder|siren|laser|"
    r"car crash|glass (?:break|shatter)|applause|laugh ?track|ambient (?:sound|noise)|background (?:music|noise)|music|song|beat|jingle|melody)\b", re.I)
_SPEECH_HINT = re.compile(r"\b(say|says|saying|speak|read|reads|narrate|voice|spoken|that says|aloud)\b", re.I)


_VERB = re.compile(r"\b(make|generate|create|produce|give me|record|synthesi[sz]e|i want|i need|can you (?:do|make)|play)\b", re.I)


def looks_like_sound_effect(text: str) -> bool:
    """True when the message ASKS FOR a sound effect or music (a making verb plus an effect word) rather than speech.
    Talking ABOUT music or thunder is fine."""
    t = str(text or "")
    if not (_VERB.search(t) and _SFX.search(t)):
        return False
    explicit = re.search(r"\bsound[- ]?effects?\b|\bsfx\b", t, re.I)
    return bool(explicit) or not _SPEECH_HINT.search(t)


_YES = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok|okay|confirm|confirmed|go ahead|do it|go for it|please do|yes please|yes do it|yes go ahead|yes, please|yes, go ahead|yes, do it)[\s.!,]*$", re.I)


def is_explicit_yes(text: str) -> bool:
    """The whole message is a plain yes. 'yes but make it blue' or 'maybe' is not."""
    return bool(_YES.match(" ".join(str(text or "").split())))


@dataclass
class Pending:
    tool: str
    args: Dict[str, Any]
    estimate_usd: float
    description: str
    created: float = field(default_factory=time.monotonic)
    armed: bool = False                        # becomes true only after the read-back reply has been produced


class PendingActions:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._items: Dict[str, Pending] = {}
        self._clock = clock

    def create(self, user_id: str, tool: str, args: Dict[str, Any], estimate: float, description: str) -> Pending:
        p = Pending(tool, args, estimate, description, created=self._clock())
        self._items[user_id] = p                # one pending action per person: a new request replaces the old one
        return p

    def get(self, user_id: str) -> Optional[Pending]:
        p = self._items.get(user_id)
        if p and self._clock() - p.created > PENDING_TTL_S:
            self._items.pop(user_id, None)
            return None
        return p

    def arm(self, user_id: str) -> None:
        p = self.get(user_id)
        if p:
            p.armed = True

    def clear(self, user_id: str) -> None:
        self._items.pop(user_id, None)


PENDING = PendingActions()


# ── describing and running ──────────────────────────────────────────────────────────────────────

def _clip(v: Any, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def describe(tool: str, args: Dict[str, Any]) -> str:
    if tool == "generate_image":
        return f'make an image: "{_clip(args.get("prompt"), 120)}"'
    return f'make a speech clip saying: "{_clip(args.get("text"), 120)}"'


async def run_paid(ctx: Ctx, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a confirmed paid action through the normal gates (media cap, rate limit, quota) and save it."""
    if tool == "generate_image":
        item = await service.generate_and_save(ctx, "image", prompt=_clip(args.get("prompt"), 500))
    else:
        item = await service.generate_and_save(ctx, "sound", text=_clip(args.get("text"), 1000))
    return {"saved": True, "title": item.title, "item": service.with_url(ctx, item)}


def _estimate(tool: str, args: Dict[str, Any]) -> float:
    if tool == "generate_image":
        return service.estimate_cost("image", steps=4)
    return service.estimate_cost("sound", text=_clip(args.get("text"), 1000))


async def execute_tool(ctx: Ctx, name: str, args: Dict[str, Any], *, user_message: str, pending: Optional[PendingActions] = None) -> Dict[str, Any]:
    """-> {'text': what the model is told, 'ui': [actions for the page]}. Never raises for a refused or failed request."""
    pending = pending or PENDING
    ui: List[Dict[str, Any]] = []
    try:
        if name not in PAID_TOOLS | FREE_TOOLS:
            logger.warning("refused unknown tool call: %s", _clip(name, 40))
            return {"text": f"Error: there is no tool named '{_clip(name, 40)}'.", "ui": ui}
        if not isinstance(args, dict):
            return {"text": "Error: the tool arguments were not valid.", "ui": ui}
        if name in PAID_TOOLS:
            if name == "generate_speech" and looks_like_sound_effect(user_message):
                return {"text": "Refused: generate_speech is speech only and cannot make sound effects or music. " + SFX_REPLY, "ui": ui}
            if not _clip(args.get("prompt" if name == "generate_image" else "text"), 5):
                return {"text": "Error: nothing to make. Ask the user what they want.", "ui": ui}
            service.require_flag()
            est = _estimate(name, args)
            p = pending.create(ctx.user_id, name, args, est, describe(name, args))
            # nothing has been spent and nothing runs until the person's next message is an explicit yes
            return {"text": f"NOT RUN YET. Waiting for the user's confirmation. Tell them you will {p.description}, estimated cost about ${est:.4f}, and ask them to reply 'yes'.", "ui": ui}
        if name == "list_library":
            kind = args.get("kind") if args.get("kind") in ("image", "sound") else None
            data = await service.list_items(ctx, kind, 20, None)
            rows = [f'{i["title"]} ({i["kind"]}, id {i["id"]})' for i in data["items"]]
            return {"text": "Library: " + ("; ".join(rows) if rows else "empty"), "ui": ui}
        if name == "open_item":
            item = await service.get_owned(ctx, str(args.get("id", "")))
            ui.append({"type": "open_item", "id": item.id})
            return {"text": f'Opened "{item.title}" in the viewer.', "ui": ui}
        if name == "edit_item":
            res = await service.edit_item(ctx, str(args.get("id", "")), str(args.get("operation", "")), args.get("params") or {}, preview=False)
            ui.append({"type": "saved", "item": res["item"]})
            return {"text": f'Saved: {res["item"]["title"]}', "ui": ui}
    except MediaServiceError as exc:
        return {"text": f"Could not do that: {exc.detail}", "ui": ui}
    except Exception:
        logger.exception("tool %s failed", name)
        return {"text": "Could not do that right now.", "ui": ui}
    return {"text": "Error: unsupported.", "ui": ui}


# ── the chat turn ───────────────────────────────────────────────────────────────────────────────

def _msg_dict(msg: Any) -> Dict[str, Any]:
    calls = []
    for tc in getattr(msg, "tool_calls", None) or []:
        calls.append({"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}})
    return {"role": "assistant", "content": getattr(msg, "content", None) or "", "tool_calls": calls}


async def pre_turn(ctx: Ctx, message: str, pending: Optional[PendingActions] = None) -> Optional[Dict[str, Any]]:
    """Handle the cases that never need the model: a yes to a read-back action, a cancel, a sound-effect request.
    -> a finished answer {'reply', 'ui'} or None to carry on to the model."""
    pending = pending or PENDING
    p = pending.get(ctx.user_id)
    if p:
        pending.clear(ctx.user_id)                              # whatever happens next, this action is used up
        if p.armed and is_explicit_yes(message):
            try:
                res = await run_paid(ctx, p.tool, p.args)
            except MediaServiceError as exc:
                return {"reply": f"I couldn't do that: {exc.detail}", "ui": []}
            return {"reply": f'Saved: {res["title"]}', "ui": [{"type": "saved", "item": res["item"]}]}
        # not a yes: the action is cancelled and the message is handled normally
    if looks_like_sound_effect(message) and not message.lstrip().startswith("/"):
        return {"reply": SFX_REPLY, "ui": []}
    return None


async def run_model_with_tools(ctx: Optional[Ctx], messages: List[Dict[str, Any]], model_id: str, user_message: str, *, acompletion: Callable[..., Awaitable[Any]],
                               on_cost: Callable[[Any], Awaitable[None]], max_tokens: int, pending: Optional[PendingActions] = None,
                               extra_tools: Optional[List[Dict[str, Any]]] = None,
                               extra_executor: Optional[Callable[[str, Dict[str, Any]], Awaitable[str]]] = None) -> Dict[str, Any]:
    """Call the model with the tools; run what it asks; return {'reply', 'ui'}. A pending paid action is armed after the reply is produced.
    ctx is None when media is off: then only extra_tools (the crew's, see server/crew/helix.py) are offered, run by extra_executor."""
    pending = pending or PENDING
    ui: List[Dict[str, Any]] = []
    extra_tools = extra_tools or []
    extra_names = {t["function"]["name"] for t in extra_tools}
    tools = (TOOLS if ctx is not None else []) + extra_tools
    created_before = pending.get(ctx.user_id) if ctx is not None else None
    for _ in range(MAX_TOOL_ROUNDS + 1):
        resp = await acompletion(model=model_id, messages=messages, tools=tools, max_tokens=max_tokens)
        await on_cost(resp)
        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            reply = getattr(msg, "content", None) or ""
            break
        messages.append(_msg_dict(msg))
        for tc in calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = None
            if tc.function.name in extra_names and extra_executor is not None:
                text = await extra_executor(tc.function.name, args if isinstance(args, dict) else {})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
                continue
            if ctx is None:
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": f"Error: there is no tool named '{_clip(tc.function.name, 40)}'."})
                continue
            out = await execute_tool(ctx, tc.function.name, args, user_message=user_message, pending=pending)
            ui += out["ui"]
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": out["text"]})
    else:
        reply = "I couldn't finish that. Please try again."
    if ctx is None:
        return {"reply": reply, "ui": ui}
    p = pending.get(ctx.user_id)
    if p and p is not created_before and not p.armed:
        # the read-back: always say what will happen and what it costs, in the server's own words, then wait for a yes
        reply = (reply.strip() + "\n\n" if reply.strip() else "") + f"Ready to {p.description} (estimated cost about ${p.estimate_usd:.4f}). Reply 'yes' to go ahead."
        pending.arm(ctx.user_id)
    return {"reply": reply, "ui": ui}


def tools_available(request_app_state: Any) -> bool:
    """Tools are offered only when media is fully on: the flag, a budget above zero and a configured storage."""
    from openjarvis.server.media import media_cap_usd, media_enabled
    from openjarvis.server.media.storage import storage_from_env

    return bool(media_enabled() and media_cap_usd() > 0 and (getattr(request_app_state, "media_storage", None) or storage_from_env()))
