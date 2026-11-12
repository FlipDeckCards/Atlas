"""Helix's tools: lets the chat model create and manage media, with the server (not the model) enforcing every rule.

Tools: generate_image, generate_speech, vary_image (paid), list_library, open_item, edit_item (free).

vary_image takes a picture the person attached (saved to their library when the message arrives) and a prompt, and makes N new pictures
from it with Gemini's image model in reference-image mode (default 4, at most 10). Each picture that comes back is charged to the spend ledger
(the Station's Out column) and saved as a new library item.

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

from openjarvis.server import hardening
from openjarvis.server.media import service, spritesheet
from openjarvis.server.media.service import Ctx, MediaServiceError

logger = logging.getLogger(__name__)

PAID_TOOLS = {"generate_image", "generate_speech", "vary_image", "make_sprite_sheet"}
FREE_TOOLS = {"list_library", "open_item", "edit_item"}
PENDING_TTL_S = 10 * 60
MAX_TOOL_ROUNDS = 3

TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "generate_image",
        "description": "Make an image from a TEXT description only and save it to the user's library. It cannot see any picture. If the user attached an image or refers to one (similar to, based on, a version of, 'this image'), use vary_image instead. This costs money, so the system asks the user to confirm first.",
        "parameters": {"type": "object", "properties": {"prompt": {"type": "string", "description": "What the image shows (500 characters at most)."}}, "required": ["prompt"]}}},
    {"type": "function", "function": {
        "name": "generate_speech",
        "description": "Turn text into spoken audio (speech only, NOT sound effects or music) and save it to the library. Costs money, so the system asks the user to confirm first.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "The words to speak (1000 characters at most)."}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "vary_image",
        "description": "Make new pictures from a picture the user attached (or a saved library picture) plus a prompt: versions, variations, edits, a new character in the same style, a female or male version, different clothing or weapon. "
                       "Use it whenever the new picture should be based on, similar to or in the style of an attached or earlier image (count 1 for a single new picture). Costs money per picture, so the system asks the user to confirm first.",
        "parameters": {"type": "object", "properties": {
            "prompt": {"type": "string", "description": "What to change or how to vary the picture (500 characters at most)."},
            "count": {"type": "integer", "description": "How many pictures to make: the number the person asks for, 4 if not stated."},
            "item_id": {"type": "string", "description": "The library id of the picture. Leave out to use the most recently attached picture."}}, "required": ["prompt"]}}},
    {"type": "function", "function": {
        "name": "make_sprite_sheet",
        "description": "Make a game SPRITE SHEET from a character picture the person attached (or a saved library picture): one row per animation (default Idle, Walk, Run, Jump, Attack, Hurt, Death), "
                       "7 frames per row, with the animation name as a label on the left, plus a clean transparent version for a game engine. "
                       "ALWAYS use this, not vary_image, when the person asks for a sprite sheet. Costs money (one picture per animation), so the system asks the person to confirm first.",
        "parameters": {"type": "object", "properties": {
            "animations": {"type": "array", "items": {"type": "string"}, "description": "The rows, in order. Leave out for the standard seven: Idle, Walk, Run, Jump, Attack, Hurt, Death."},
            "frames": {"type": "integer", "description": "Frames per row: 7 if not stated."},
            "notes": {"type": "string", "description": "Anything said about the style or the character's moves (300 characters at most)."},
            "item_id": {"type": "string", "description": "The library id of the character picture. Leave out to use the most recently attached picture."}}}}},
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
    " You can create and manage the user's media with tools. When the user attaches a picture and asks for versions, variations or edits of it, call vary_image "
    "(the attached picture is saved and its library id is in the message; never describe it and call generate_image instead). "
    "If they ask for a sprite sheet, call make_sprite_sheet instead: it builds the standard sheet with a labelled row per animation. "
    "For generate_image, generate_speech and vary_image the system will ask the user to confirm: after "
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


ATTACHED: Dict[str, List[Dict[str, Any]]] = {}       # user id -> pictures attached in chat and saved to the library: [{'name', 'item_id', 'at'}]
ATTACHED_TTL_S = 2 * 60 * 60
MAX_ATTACHED_KEPT = 8


def remember_attached(user_id: str, saved: List[Dict[str, Any]]) -> None:
    now = time.monotonic()
    kept = [a for a in ATTACHED.get(user_id, []) if now - a["at"] < ATTACHED_TTL_S] + [dict(a, at=now) for a in saved]
    ATTACHED[user_id] = kept[-MAX_ATTACHED_KEPT:]


def latest_attached(user_id: str, within: Optional[float] = None) -> Optional[str]:
    now = time.monotonic()
    live = [a for a in ATTACHED.get(user_id, []) if now - a["at"] < (within or ATTACHED_TTL_S)]
    return live[-1]["item_id"] if live else None


# "make a new character similar to the image I just gave you", "a female version of this", "based on the attached picture": a request that
# builds on a picture the person attached. generate_image cannot see any picture, so such a request is run as vary_image (one picture).
ATTACHED_RECENT_S = 20 * 60
_BUILDS_ON_PICTURE = re.compile(
    r"\b(similar to|based on|inspired by|in the style of|same style as|like (?:this|that|the|my)|closer to|version of|variation of|redo|remake|"
    r"(?:this|that|the|attached|previous|other|above|last|same|given|latest) (?:image|picture|photo|pic|character|sprite|art|artwork|drawing|one))\b", re.I)


def builds_on_attached(user_id: str, message: str) -> bool:
    return bool(latest_attached(user_id, ATTACHED_RECENT_S) and _BUILDS_ON_PICTURE.search(str(message or "")))


def _short(v: Any, n: int) -> str:
    t = " ".join(str(v or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "\u2026"


async def keep_attached(ctx: Ctx, items: List[Any]) -> List[Dict[str, Any]]:
    """Save the pictures attached to a chat message to the user's library, as the real files (not a description), and remember their ids so
    vary_image and later turns can use them. items: [(name, base64, mime)]. A picture that cannot be saved is skipped."""
    import base64

    saved: List[Dict[str, Any]] = []
    for name, b64, mime in items[:hardening.UNLIMITED_ATTACHMENTS]:
        try:
            item = await service.save_item(ctx, kind="image", data=base64.b64decode(b64), mime=mime or "image/jpeg", title=f"Attached: {name}", prompt=None, provider="upload")
        except Exception:
            logger.warning("could not keep the attached image %s", _clip(name, 60))
            continue
        saved.append({"name": name, "item_id": item.id})
    if saved:
        remember_attached(ctx.user_id, saved)
    return saved


def describe(tool: str, args: Dict[str, Any]) -> str:
    if tool == "make_sprite_sheet":
        names = spritesheet.clean_animations(args.get("animations"), True)
        return f'make a sprite sheet from your picture: {len(names)} rows ({", ".join(names)}), {spritesheet.clean_frames(args.get("frames"), True)} frames each'
    if tool == "vary_image" and args.get("edit"):
        return f'edit your picture: "{_short(args.get("prompt"), 200)}"'
    if tool == "vary_image":
        n = service.clamp_variations(args.get("count"), True)         # args hold the already-clamped count (see execute_tool)
        return f'make {n} {"variation" if n == 1 else "variations"} based on your picture: "{_short(args.get("prompt"), 200)}"'
    if tool == "generate_image":
        return f'make an image: "{_short(args.get("prompt"), 200)}"'
    return f'make a speech clip saying: "{_clip(args.get("text"), 120)}"'


async def run_paid(ctx: Ctx, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a confirmed paid action through the normal gates (media cap, rate limit, quota) and save it."""
    if tool == "generate_image":
        item = await service.generate_and_save(ctx, "image", prompt=_clip(args.get("prompt"), 500))
    elif tool == "make_sprite_sheet":
        res = await service.sprite_sheet_and_save(ctx, str(args.get("item_id") or ""), args.get("animations"), args.get("frames"), _clip(args.get("notes"), 300))
        miss = f"; could not draw: {', '.join(res['missing'])}" if res["missing"] else ""
        return {"saved": True, "title": f"sprite sheet with {len(res['rows'])} rows (about ${res['cost_usd']:.2f}){miss}", "items": res["items"], "item": res["items"][0]}
    elif tool == "vary_image":
        res = await service.vary_and_save(ctx, str(args.get("item_id") or ""), _clip(args.get("prompt"), 500), args.get("count"), edit=bool(args.get("edit")))
        n, bad = len(res["items"]), res["failed"]
        title = f"{n} {'variation' if n == 1 else 'variations'} (about ${res['cost_usd']:.2f})" + (f"; {bad} could not be made" if bad else "")
        return {"saved": True, "title": title, "items": res["items"], "item": res["items"][0]}
    else:
        item = await service.generate_and_save(ctx, "sound", text=_clip(args.get("text"), 1000))
    return {"saved": True, "title": item.title, "item": service.with_url(ctx, item)}


def _estimate(tool: str, args: Dict[str, Any], ctx: Optional[Ctx] = None) -> float:
    if tool == "make_sprite_sheet":
        return service.estimate_sprite_sheet(args.get("animations"), bool(ctx and ctx.unlimited))
    if tool == "vary_image":
        return service.estimate_variations(args.get("count"), bool(ctx and ctx.unlimited))
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
        if name == "generate_image" and builds_on_attached(ctx.user_id, user_message):
            name, args = "vary_image", {"prompt": args.get("prompt"), "count": 1}          # the picture goes to the image model as a reference
        if name in PAID_TOOLS:
            if name == "generate_speech" and looks_like_sound_effect(user_message):
                return {"text": "Refused: generate_speech is speech only and cannot make sound effects or music. " + SFX_REPLY, "ui": ui}
            if name != "make_sprite_sheet" and not _clip(args.get("text" if name == "generate_speech" else "prompt"), 5):
                return {"text": "Error: nothing to make. Ask the user what they want.", "ui": ui}
            service.require_flag()
            if name in ("vary_image", "make_sprite_sheet"):
                # the picture is fixed now (not at the yes), and must be the caller's own picture
                item_id = str(args.get("item_id") or "") or latest_attached(ctx.user_id) or ""
                if not item_id:
                    return {"text": "Error: there is no picture to work from. Ask the user to attach one.", "ui": ui}
                item = await service.get_owned(ctx, item_id)
                if item.kind != "image":
                    return {"text": "Error: that item is not a picture.", "ui": ui}
                args = dict(args, item_id=item.id)
                if name == "vary_image":
                    args["count"] = service.clamp_variations(args.get("count"), ctx.unlimited)
                else:
                    args["animations"] = spritesheet.clean_animations(args.get("animations"), ctx.unlimited)
                    args["frames"] = spritesheet.clean_frames(args.get("frames"), ctx.unlimited)
            est = _estimate(name, args, ctx)
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


_SPRITE_SHEET = re.compile(r"\bsprite[\s-]*sheets?\b", re.I)


async def sprite_sheet_request(ctx: Ctx, message: str, pending: Optional[PendingActions] = None) -> Optional[Dict[str, Any]]:
    """A request for a sprite sheet when a picture was attached recently is set up by the server, not left to the model's choice of tool
    (some chat models answer 'I can't do that' instead of calling it). The usual rules still apply: the cost is read back and nothing runs
    until the person's next message is an explicit yes. -> {'reply', 'ui'} or None (not a sprite-sheet request, or no picture to work from)."""
    if not _SPRITE_SHEET.search(str(message or "")) or str(message).lstrip().startswith("/") or not latest_attached(ctx.user_id, ATTACHED_RECENT_S):
        return None
    pending = pending or PENDING
    out = await execute_tool(ctx, "make_sprite_sheet", {"notes": _clip(message, 300)}, user_message=message, pending=pending)
    p = pending.get(ctx.user_id)
    if p is None or p.tool != "make_sprite_sheet":
        return {"reply": out["text"].removeprefix("Could not do that: ") if out["text"].startswith("Could not do that") else "I couldn't set that up right now.", "ui": []}
    pending.arm(ctx.user_id)
    return {"reply": f"Ready to {p.description} (estimated cost about ${p.estimate_usd:.2f}). Say 'yes' to go ahead, or tell me which animations you want instead.", "ui": []}


_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a few": 3, "several": 5}
_VARIATIONS = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|a few|several)?\s*(?:more |new |different |other )?"
                         r"(?:versions?|variations?|variants?|alternatives?|options)\b", re.I)
_EDIT = re.compile(r"\b(remove|delete|erase|get rid of|take (?:out|off|away)|replace|swap|change|re-?colou?r|edit|redraw|retouch|clean up|add|"
                   r"give (?:him|her|it|them)|turn (?:him|her|it|this|the)|make (?:it|him|her|them|this|the)|without)\b", re.I)


def _wanted_count(message: str) -> Optional[int]:
    m = _VARIATIONS.search(message)
    if not m:
        return None
    word = (m.group(1) or "").lower()
    return int(word) if word.isdigit() else _NUMBER_WORDS.get(word)


async def picture_request(ctx: Ctx, message: str, new_image: bool, pending: Optional[PendingActions] = None) -> Optional[Dict[str, Any]]:
    """'make 10 versions of this' or 'remove the sword from this character', about a picture attached in this message (new_image) or one the
    message points at ('this character', 'the image'): set up by the server as a Gemini request on the picture, with the cost read back and
    a yes needed. (A chat model asked to do this sometimes just says it did, with nothing made.) -> {'reply', 'ui'} or None."""
    text = str(message or "")
    if text.lstrip().startswith("/") or not latest_attached(ctx.user_id, ATTACHED_RECENT_S):
        return None
    if not (new_image or builds_on_attached(ctx.user_id, text)):
        return None
    count = _wanted_count(text)
    if count is not None or _VARIATIONS.search(text):
        args: Dict[str, Any] = {"prompt": _clip(text, 500), "count": count or service.VARIATION_DEFAULT}
    elif _EDIT.search(text):
        args = {"prompt": _clip(text, 500), "count": 1, "edit": True}
    else:
        return None
    pending = pending or PENDING
    out = await execute_tool(ctx, "vary_image", args, user_message=text, pending=pending)
    p = pending.get(ctx.user_id)
    if p is None or p.tool != "vary_image":
        return {"reply": out["text"].replace("Could not do that: ", "", 1) if out["text"].startswith("Could not do that") else "I couldn't set that up right now.", "ui": []}
    pending.arm(ctx.user_id)
    return {"reply": f"Ready to {p.description} (estimated cost about ${p.estimate_usd:.2f}). Say 'yes' to go ahead.", "ui": []}


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
            if res.get("items"):
                return {"reply": f'Saved: {res["title"]}', "ui": [{"type": "saved_many", "items": res["items"]}]}
            return {"reply": f'Saved: {res["title"]}', "ui": [{"type": "saved", "item": res["item"]}]}
        # not a yes: the action is cancelled and the message is handled normally
    if looks_like_sound_effect(message) and not message.lstrip().startswith("/"):
        return {"reply": SFX_REPLY, "ui": []}
    return await sprite_sheet_request(ctx, message, pending) or await picture_request(ctx, message, False, pending)


async def run_model_with_tools(ctx: Optional[Ctx], messages: List[Dict[str, Any]], model_id: str, user_message: str, *, acompletion: Callable[..., Awaitable[Any]],
                               on_cost: Callable[[Any], Awaitable[None]], max_tokens: int, pending: Optional[PendingActions] = None,
                               extra_tools: Optional[List[Dict[str, Any]]] = None,
                               extra_executor: Optional[Callable[[str, Dict[str, Any]], Awaitable[str]]] = None,
                               max_rounds: int = MAX_TOOL_ROUNDS) -> Dict[str, Any]:
    """Call the model with the tools; run what it asks; return {'reply', 'ui'}. A pending paid action is armed after the reply is produced.
    ctx is None when media is off: then only extra_tools (the crew's, see server/crew/helix.py) are offered, run by extra_executor."""
    pending = pending or PENDING
    ui: List[Dict[str, Any]] = []
    extra_tools = extra_tools or []
    extra_names = {t["function"]["name"] for t in extra_tools}
    tools = (TOOLS if ctx is not None else []) + extra_tools
    created_before = pending.get(ctx.user_id) if ctx is not None else None
    for _ in range(max_rounds + 1):
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
