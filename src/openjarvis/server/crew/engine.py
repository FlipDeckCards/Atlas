"""The crew engine: Helix turns a goal into a plan, then agents work the tasks in parallel, each inside its own limits.

  mission   one goal from the owner ("build me X"), with a budget. planning -> active -> done (or paused / cancelled / stalled)
  task      one agent's job inside a mission: a role, a brief, the tasks it waits for, its own budget, its own transcript
  question  how an agent reaches the owner, always through Helix: a plain question, a checkpoint (approve/decline a risky action)
            or a budget request. The task waits; nothing else stops.
  event     the ledger: every step, cost, output, question and result, newest first in the Station

The server decides everything that matters. Models only write text and ask for tools; every tool call is checked here:
  * before each model call: the task budget, the mission budget and the crew's daily cap (CREW_DAILY_CAP_USD) must all have room;
  * a platform action runs at once only when connections.py rates it read or draft; anything else becomes a checkpoint that runs
    only after the owner approves it (the approval comes from a button, never from a model);
  * a task gets ROUNDS_PER_RUN model calls per turn and MAX_RUNS turns; then it fails and the owner is told.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from openjarvis.server import hardening
from openjarvis.server.crew import connections as conns
from openjarvis.server.crew import (
    daily_cap_usd,
    default_mission_budget,
    idle_tick_seconds,
    max_concurrent,
    max_mission_budget,
    max_tasks,
    quiet_hours,
    tick_seconds,
)
from openjarvis.server.crew import github as gh
from openjarvis.server.crew import research as web

logger = logging.getLogger(__name__)

LA = ZoneInfo("America/Los_Angeles")
EST_CALL_USD = hardening.FLAT_CHAT_ESTIMATE_USD     # what one model call is assumed to cost before it is made
ROUNDS_PER_RUN = 6
MAX_RUNS = 4
STALE_RUNNING_S = 15 * 60
MAX_OUTPUT_CHARS = 200_000
MAX_OUTPUTS_PER_MISSION = 60
PLANNER_MAX_TOKENS = 5000
AGENT_MAX_TOKENS = 8000          # room to write a whole file in one save_output call (only tokens used are paid)
_CUT_OFF_MSG = ("Error: your reply was cut off at the length limit, so this call was incomplete and nothing was saved. "
                "Save each chapter or section as its own file, about 4,000 words per save_output call, then finish.")

ROLES: Dict[str, Dict[str, str]] = {
    "researcher": {"name": "Scout", "model": "lookup", "about": "Research and analysis with Gemini. Searches the live web with web_search (quick, free) and, for a deep sourced report, web_research (GPT Researcher) when it is connected. Cites the pages it used.",
                   "can": "Live web search (free), deep sourced reports (GPT Researcher), market and buyer research, competitors, pricing",
                   "cannot": "Log in to sites or post anything"},
    "builder": {"name": "Forge", "model": "writing", "about": "Codes with Claude, straight in Michael's GitHub repos: reads the code, commits changes to its own crew/ branch, and asks Michael to approve merging into main (request_merge). It never writes to main itself.",
                "can": "Read the product repos, write code, commit to a crew/ branch, request a merge you approve",
                "cannot": "Write to main or deploy without your Approve, change GitHub workflows"},
    "writer": {"name": "Quill", "model": "writing", "about": "Writes with Claude: product copy, listings, posts, emails, scripts, documents.",
               "can": "Product copy, Etsy listings, social posts, emails, scripts, docs",
               "cannot": "Post or send anything itself"},
    "designer": {"name": "Prism", "model": "writing", "about": "Designs with Claude: brand direction, layouts, color and type, and real images with generate_image (saved to Michael's Aibussol media library).",
                 "can": "Brand direction, layouts, color and type, generate product images (up to 4 per task)",
                 "cannot": "Publish images anywhere itself"},
    "operator": {"name": "Relay", "model": "writing", "about": "The only role that can use connected platforms (Stripe, Etsy...). Risky actions become checkpoints for the owner.",
                 "can": "Use connected platforms: Stripe products and payment links, Etsy listings with photos and download files, publishing",
                 "cannot": "Anything public, paid or permanent without your Approve"},
    "workbench": {"name": "Rig", "model": "writing", "about": "Works on Michael's own computer through the Workbench helper: Claude Code in his real project folders with his Unity / Unreal editors, on his own Claude plan (no API cost). Builds, tests and fixes games (and anything that needs his machine) the way Deadzone Run was built. Rig's tasks wait until the PC is connected.",
                  "can": "Build and test in your real projects on your PC: Unity, Unreal, Git, Claude Code (your plan, no API cost)",
                  "cannot": "Publish, upload to a store or push to main; it runs only while the Workbench helper is on"},
    "marketer": {"name": "Spark", "model": "writing", "about": "Marketing with Claude: positioning, launch plans, campaigns, SEO, landing-page and ad copy, email sequences, pricing pages. Researches the market and competitors with web_search and makes campaign images with generate_image.",
                 "can": "Positioning, launch and campaign plans, SEO, landing-page, ad and email copy, campaign images",
                 "cannot": "Spend on ads or publish anything itself"},
    "social": {"name": "Echo", "model": "writing", "about": "Social media with Claude: content calendars, platform-ready posts (TikTok, Instagram, Facebook, X, YouTube Shorts), captions, hooks, hashtags and short-video scripts, plus post images with generate_image. Checks what is trending with web_search. Saves posts as files ready for Marnyx or the user to schedule.",
               "can": "Content calendars, posts and captions per platform, video scripts, trend checks, post images",
               "cannot": "Post to any account itself"},
    "support": {"name": "Haven", "model": "writing", "about": "Customer support with Claude: FAQs, help-center articles, reply templates, refund and policy wording, onboarding emails, triage of customer issues. Reads recent Stripe payments (read only) to understand billing questions.",
                "can": "FAQs, help articles, reply templates, policies, onboarding emails, billing lookups (read only)",
                "cannot": "Email customers, refund or change anything"},
    "analyst": {"name": "Sage", "model": "writing", "about": "Data analyst with Claude: reads the account's live business numbers with business_numbers (money in and out per business, MRR, subscribers, crew cost) and Stripe (read only), finds trends, sets KPIs, builds forecasts and reports with the math shown.",
                "can": "Live P&L per business, KPIs, trends, forecasts, dashboards as files, Stripe reads",
                "cannot": "Change data, prices or anything else"},
    "qa": {"name": "Probe", "model": "writing", "about": "QA tester with Claude: tests the live sites with check_site (status, speed, title, page text, broken pages), reads the code in the connected repos (read only), writes test plans, bug reports with steps to reproduce, and checks fixes. Games are play-tested by Rig on the PC; Probe writes their test plans.",
           "can": "Test live sites, read code, test plans, bug reports with repro steps, release checklists",
           "cannot": "Change code (it reports; Forge fixes) or log in to accounts"},
    "finance": {"name": "Tally", "model": "writing", "about": "Finance with Claude: bookkeeping summaries, budgets, cash flow, unit economics, pricing and margin math, subscription metrics and tax-prep checklists, from business_numbers and Stripe (read only). Not a licensed accountant: flags what needs one.",
                "can": "P&L and cash-flow summaries, budgets, pricing and margin math, tax-prep checklists, Stripe balance reads",
                "cannot": "Move money, file taxes or give licensed advice"},
    "reviewer": {"name": "Lens", "model": "writing", "about": "Checks the crew's work before it reaches Michael: reads every deliverable, fact-checks with web_search, and sends a task back once with request_changes when it is wrong or incomplete. Saves a short review.",
                 "can": "Read every deliverable, fact-check on the web, send work back once for fixes, write the review",
                 "cannot": "Change the work itself or approve anything for you"},
}

_BASE_TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "save_output",
        "description": "Save a finished deliverable (a file) to the mission's outbox, where the owner can open it. Use a file name with an extension.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "content": {"type": "string"}}, "required": ["name", "content"]}}},
    {"type": "function", "function": {
        "name": "ask_michael",
        "description": "Ask the owner one concrete question through Helix when you cannot continue without his decision. Give 2-4 short options when you can. Your task pauses until he answers.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Finish this task with a short summary of what was done and where the outputs are.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]

_CONNECTION_TOOL = {"type": "function", "function": {
    "name": "use_connection",
    "description": "Act on a connected platform through its official API. Read and draft actions run now; any other action is sent to the owner as a checkpoint and runs only if he approves it.",
    "parameters": {"type": "object", "properties": {"platform": {"type": "string"}, "action": {"type": "string"}, "args": {"type": "object"}},
                   "required": ["platform", "action"]}}}

_RESEARCH_TOOL = {"type": "function", "function": {
    "name": "web_research",
    "description": "Research a question on the live web (GPT Researcher: searches, reads pages, writes a report with sources). Slow (1-4 minutes) and costs about $0.25, so ask one well-formed question at a time.",
    "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}}

_SEARCH_TOOL = {"type": "function", "function": {
    "name": "web_search",
    "description": "Search the live web (fast and free). Returns titles, links and snippets. Put a URL in the query to read that page instead.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}

_READ_OUTPUT_TOOL = {"type": "function", "function": {
    "name": "read_output",
    "description": "Read a file another crew member saved to this mission's outbox (by its file name).",
    "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}

_IMAGE_TOOL = {"type": "function", "function": {
    "name": "generate_image",
    "description": "Generate one image from a detailed description (500 characters at most) and save it to Michael's Aibussol media library. Costs a few cents; at most 10 per task.",
    "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}}

_DESIGN_TOOL = {"type": "function", "function": {
    "name": "make_design_files",
    "description": "Turn one of your generate_image artworks (a dark design on a plain background) into a finished design pack: color PNG and JPG, "
                   "black-and-white PNG and JPG, and SVG and DXF cut files traced from the artwork. Saves the images to Michael's media library and "
                   "the cut files and a pack file to this mission's outbox. Name the pack (short, lowercase, e.g. gnome-snow-globe). At most 10 per task.",
    "parameters": {"type": "object", "properties": {"media_item_id": {"type": "string"}, "name": {"type": "string"}}, "required": ["media_item_id", "name"]}}}

_REPO_TOOLS = [
    {"type": "function", "function": {
        "name": "repo_files",
        "description": "List the files in one of Michael's repos (optionally under a folder, optionally on a branch).",
        "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "path": {"type": "string"}, "branch": {"type": "string"}}, "required": ["repo"]}}},
    {"type": "function", "function": {
        "name": "repo_read",
        "description": "Read one file from a repo (default branch unless you name your crew/ branch).",
        "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "path": {"type": "string"}, "branch": {"type": "string"}}, "required": ["repo", "path"]}}},
    {"type": "function", "function": {
        "name": "repo_commit",
        "description": "Commit complete file contents to your own branch (it is always named crew/<name>; created from main if new). Never touches main. Give whole files, not diffs.",
        "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "branch": {"type": "string"}, "message": {"type": "string"},
                                                        "files": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
                       "required": ["repo", "branch", "message", "files"]}}},
    {"type": "function", "function": {
        "name": "request_merge",
        "description": "Ask Michael to approve merging your crew/ branch into main. It becomes a checkpoint; nothing merges until he taps Approve. Explain what changed and how it was checked.",
        "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "branch": {"type": "string"}, "title": {"type": "string"}, "summary": {"type": "string"}},
                       "required": ["repo", "branch", "title", "summary"]}}},
]

_NUMBERS_TOOL = {"type": "function", "function": {
    "name": "business_numbers",
    "description": "Read this account's live business numbers: money in and out today and over 30 days per business, MRR, subscribers, crew and hosting costs, and which sites are up.",
    "parameters": {"type": "object", "properties": {}}}}

_SITE_TOOL = {"type": "function", "function": {
    "name": "check_site",
    "description": "Test one page of this account's own live sites: HTTP status, load time, page title, size and the start of its text. Only the account's own sites (https).",
    "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}}

_READ_CONNECTION_TOOL = {"type": "function", "function": {
    "name": "use_connection",
    "description": "READ from a connected platform through its official API (read actions only, for example stripe.recent_payments, stripe.get_balance, stripe.list_products, etsy.get_shop).",
    "parameters": {"type": "object", "properties": {"platform": {"type": "string"}, "action": {"type": "string"}, "args": {"type": "object"}},
                   "required": ["platform", "action"]}}}

_MARNYX_TOOL = {"type": "function", "function": {
    "name": "use_connection",
    "description": "Use Marnyx, the owner's social media and email manager: platform 'marnyx', actions list_accounts and post_status (run now), "
                   "schedule_week (one brand's week of posts) and send_email (one campaign). schedule_week and send_email go to the owner as ONE "
                   "approval each and run only if approved. Read-only actions on other platforms (e.g. stripe.recent_payments) also work.",
    "parameters": {"type": "object", "properties": {"platform": {"type": "string"}, "action": {"type": "string"}, "args": {"type": "object"}},
                   "required": ["platform", "action"]}}}

_REVIEW_TOOL = {"type": "function", "function": {
    "name": "request_changes",
    "description": "Send one finished task back to its agent with concrete fixes (each task can be sent back once). You continue after the rework is done.",
    "parameters": {"type": "object", "properties": {"task_number": {"type": "integer"}, "notes": {"type": "string"}}, "required": ["task_number", "notes"]}}}

LIMITS = {"web_search": 12, "generate_image": 16, "make_design_files": 10, "repo_commit": 10, "check_site": 20, "business_numbers": 3}
SEARCHERS = ("researcher", "reviewer", "marketer", "social", "support", "analyst", "qa", "finance")
IMAGERS = ("designer", "marketer", "social")
READERS = ("support", "analyst", "finance")                # read-only platform access
PUBLISHERS = ("social", "marketer")                       # Marnyx posts and email (each an approval); everything else read-only
REVIEW_MARK = "REVIEW: changes requested."

AcompletionFn = Callable[..., Awaitable[Any]]


def _provider_unavailable(exc: Exception) -> bool:
    """Out of quota, rate limited or down: worth retrying on another provider."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(k in text for k in ("429", "503", "ratelimit", "rate limit", "quota", "resource_exhausted", "overloaded", "unavailable"))


def pc_project(names: List[str], task: Dict[str, Any], mission: Dict[str, Any]) -> str:
    """Which folder on the PC Rig works in: the PC project the task (or else the mission goal) names, preferring 'in project X',
    then the first one mentioned; otherwise the mission's project. Factories and PC folders are separate: a game is in the Deadzone
    factory while Rig builds it in its own Unity project."""
    names = sorted({str(n) for n in names if str(n).strip()}, key=len, reverse=True)
    texts = (f'{task.get("title", "")}\n{task.get("brief", "")}', str(mission.get("goal") or ""))
    for text in texts:                                   # an explicit "in project X" anywhere wins
        for n in names:
            if re.search(r"\bin (?:the )?(?:new )?project\s+" + re.escape(n) + r"(?![A-Za-z0-9])", text, re.I):
                return n
    for text in texts:                                   # "in (the) new project X": a project the PC does not have yet (the helper creates its folder)
        m = re.search(r"\bin (?:the )?new project\s+([A-Za-z][A-Za-z0-9_-]{1,39})", text, re.I)
        if m:
            return m.group(1)
    for text in texts:
        hits = [(m.start(), n) for n in names for m in [re.search(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", text, re.I)] if m]
        if hits:
            return min(hits)[1]
    return str(mission.get("project") or "")


def _clip(v: Any, n: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:n]


HOUSE_RULES = (
    "- Games: mobile games are built in Unity (C#, the Input System), never as a web prototype unless he asks for one; open-world games in Unreal. "
    "Every game must play with keyboard AND a connected PlayStation controller. Deadzone Run is the reference game.\n"
    "- The Unity / Unreal editors run only on Michael's computer. Game building and testing is Rig's job (role workbench): Rig runs Claude "
    "Code in his real project folder on his PC, the way Deadzone Run was built. Plan the design, art and copy with the other agents, then "
    "give Rig the build: 'in project <PC project name>, implement ..., build and test', using one of the PC project names listed under "
    "his PC (they are folders on his computer, not factories). A NEW game gets its own new project: write 'in new project <GameName>' "
    "(one word, e.g. NeonDrift) and Rig creates the Unity project in its own folder; never build a new game inside an existing game's project. Game missions belong to the Deadzone factory: set the mission project to "
    "Deadzone for every game and for the Deadzone Games site. If his PC "
    "is not connected, Rig's task simply waits. Never write a web prototype for a game, and never claim a game was built unless Rig reports it.\n"
    "- Social and email go through Marnyx (connection 'marnyx'): Echo plans each brand's week (FlipDeck, Marnyx, and Aibussol once its "
    "accounts exist) and sends it with marnyx.schedule_week, one approval per brand per week; Spark writes campaigns and sends them with "
    "marnyx.send_email, one approval each. Never post or email any other way.\n"
    "- Etsy (connection 'etsy') is done end to end by the crew. For each product: Prism makes the artwork with generate_image (a dark design on "
    "a plain background), then make_design_files on it (color and black-and-white PNG and JPG go to the library, SVG and DXF cut files go to "
    "the outbox, plus a .pack.json). Prism also makes 2-3 mockup photos with generate_image. Quill writes the product guide as an outbox "
    "file (markdown becomes a clean PDF). Relay runs create_draft_listing (with 13 tags), add_listing_image for each photo (first = main), "
    "add_listing_file with pack=<name>.pack.json (the buyer's download: one zip with every format) and the guide, then asks Michael once, "
    "with ask_michael, to approve the designs in his library, listing the product names. Only after he says yes does Relay run ONE "
    "publish_listings checkpoint for the whole batch. Never ask Michael to upload anything by hand.\n"
    "- Products: FlipDeck Cards (card-flipping subscription), Marnyx (AI social media manager), Deadzone Games (games site, mobile games "
    "go to Google Play first), Aibussol (this assistant). One shared Stripe account, tagged with metadata app=<product>."
)


def house_rules(env=None) -> str:
    """CREW_HOUSE_RULES replaces the built-in rules when set."""
    import os

    e = os.environ if env is None else env
    return (e.get("CREW_HOUSE_RULES") or "").strip() or HOUSE_RULES


def tools_for(role: str) -> List[Dict[str, Any]]:
    extra: List[Dict[str, Any]] = [_READ_OUTPUT_TOOL]
    if role == "operator":
        extra.append(_CONNECTION_TOOL)
    if role == "researcher":
        extra += [_SEARCH_TOOL, _RESEARCH_TOOL]
    if role in IMAGERS:
        extra.append(_IMAGE_TOOL)
    if role == "designer":
        extra.append(_DESIGN_TOOL)
    if role == "builder":
        extra += _REPO_TOOLS
    if role == "reviewer":
        extra += [_SEARCH_TOOL, _REVIEW_TOOL]
    if role in ("marketer", "social", "support", "analyst", "qa", "finance"):
        extra.append(_SEARCH_TOOL)
    if role in READERS:
        extra.append(_READ_CONNECTION_TOOL)
    if role in PUBLISHERS:
        extra.append(_MARNYX_TOOL)
    if role in ("analyst", "finance"):
        extra.append(_NUMBERS_TOOL)
    if role == "qa":
        extra += [_SITE_TOOL] + [t for t in _REPO_TOOLS if t["function"]["name"] in ("repo_files", "repo_read")]
    return _BASE_TOOLS + extra


def _calls_made(transcript: List[Dict[str, Any]], name: str) -> int:
    return sum(1 for m in transcript for c in (m.get("tool_calls") or []) if (c.get("function") or {}).get("name") == name)


def add_review_task(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every plan with real work ends with Lens checking it, unless the planner already put a reviewer in."""
    if len(tasks) < 2 or any(t["role"] == "reviewer" for t in tasks):
        return tasks
    return tasks + [{"idx": len(tasks), "title": "Review the crew's work", "role": "reviewer", "depends_on": [t["idx"] for t in tasks],
                     "brief": "Read every deliverable in the outbox and each task's result. Check it does what the mission asked, is complete, and that facts, "
                              "prices and claims hold up (use web_search). Send back at most the tasks that truly need fixing with request_changes. Save "
                              "review.md (what is ready, what Michael must do next, any risks) and finish."}]


def parse_plan(text: str, limit: int) -> Optional[Dict[str, Any]]:
    """The planner's JSON, checked: 1..limit tasks, known roles, dependencies only on EARLIER tasks. None when unusable."""
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(t[start:end + 1])
    except ValueError:
        return None
    raw = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return None
    tasks = []
    for i, item in enumerate(raw[:limit]):
        if not isinstance(item, dict):
            continue
        title, brief = _clip(item.get("title"), 120), str(item.get("brief") or "").strip()[:3000]
        if not title or not brief:
            continue
        role = item.get("role") if item.get("role") in ROLES else "builder"
        deps = []
        for d in item.get("depends_on") or []:
            try:
                d = int(d)
            except (TypeError, ValueError):
                continue
            if 0 <= d < len(tasks) and d not in deps:
                deps.append(d)
        tasks.append({"idx": len(tasks), "title": title, "role": role, "brief": brief, "depends_on": deps})
    if not tasks:
        return None
    return {"title": _clip(data.get("title"), 80), "project": _clip(data.get("project"), 40), "tasks": tasks}


def in_quiet_hours(now: Optional[dt.datetime] = None, env=None) -> bool:
    q = quiet_hours(env)
    if not q:
        return False
    h = (now or dt.datetime.now(dt.timezone.utc)).astimezone(LA).hour
    a, b = q
    return (a <= h or h < b) if a > b else (a <= h < b)


class _Done(Exception):
    """Internal: the approved checkpoint was already handled."""


def _web_search(query: str) -> str:
    """OpenJarvis's built-in web search: Tavily when TAVILY_API_KEY is set, otherwise DuckDuckGo (no key)."""
    from openjarvis.tools.web_search import (
        WebSearchTool,  # noqa: PLC0415 (imported only when used)
    )

    r = WebSearchTool(max_results=6).execute(query=query)
    return r.content if r.success else f"Search failed: {r.content}"


def _msg_dict(msg: Any) -> Dict[str, Any]:
    calls = []
    for tc in getattr(msg, "tool_calls", None) or []:
        calls.append({"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}})
    out: Dict[str, Any] = {"role": "assistant", "content": getattr(msg, "content", None) or ""}
    if calls:
        out["tool_calls"] = calls
    return out


class CrewEngine:
    def __init__(self, store, acompletion: AcompletionFn, *, notifier=None, env=None, models: Optional[Dict[str, str]] = None,
                 cost_fn: Callable[[Any], float] = hardening.chat_cost_usd, now: Optional[Callable[[], dt.datetime]] = None,
                 client_factory=None, research_fn=None, search_fn=None, media_ctx_fn=None) -> None:
        self.store = store
        self.acompletion = acompletion
        self.notifier = notifier
        self.env = env
        self.models = models or hardening.model_config(env)
        self.cost_fn = cost_fn
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self.client_factory = client_factory
        self.research_fn = research_fn or web.gpt_researcher_run
        self.search_fn = search_fn or _web_search
        self.media_ctx_fn = media_ctx_fn          # user_id -> media Ctx (or None when media is off); set by the app
        self.running: Dict[str, asyncio.Task] = {}
        self._role_of: Dict[str, str] = {}              # task id -> role, so designers can run one at a time
        self._plans: Dict[str, asyncio.Task] = {}
        self._loop: Optional[asyncio.Task] = None
        self._cap_noted: set = set()
        self._ticks = 0
        self.products: Dict[str, Dict[str, Any]] = {}
        self._product_task: Optional[asyncio.Task] = None
        self.herald = None                                  # crew/herald.py, set by the app: makes the briefs on schedule
        from openjarvis.server.crew.workbench import Presence

        self.presence = Presence()                          # which PCs (Workbench helpers) said hello recently
        self.owner_fn = None                                # async user_id -> bool, set by the app: the owner has no daily cap and auto top-ups
        self.name_fn = None                                 # async user_id -> display name (for a subscriber's prompts)
        self._owner_cache: Dict[str, bool] = {}
        self._herald_task: Optional[asyncio.Task] = None
        self.weekly = None                                  # crew/weekly.py: the weekly social plan (set by the app)
        self._weekly_task: Optional[asyncio.Task] = None
        self.pool = None                                    # the app's database pool (subscriptions, for usage billing)
        self._biz_health: Dict[str, Any] = {}              # a subscriber's site checks: uid -> (checked monotonic, results)
        self._usage_tasks: Dict[str, asyncio.Task] = {}
        # idle mode: with no work anywhere the loop sleeps long so the database (Neon) can suspend; any new work pokes it awake
        self._wake = asyncio.Event()
        self._idle_ticks = 0
        self._wb_empty_until: Dict[str, float] = {}       # uid -> monotonic time until which the PC queue is known empty

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self.store.ensure_tables()
        n = await self.store.requeue_stale(0)            # after a restart nothing is really running
        if n:
            logger.info("crew: %d interrupted task(s) queued again", n)
        self._loop = asyncio.create_task(self._forever())
        self._refresh_products_soon()

    async def stop(self) -> None:
        for t in [self._loop, *self.running.values(), *self._plans.values()]:
            if t:
                t.cancel()

    def poke(self) -> None:
        """New work may exist: wake the loop now and forget the 'PC queue is empty' notes."""
        self._idle_ticks = 0
        self._wb_empty_until.clear()
        self._wake.set()

    def _sleep_for(self) -> float:
        busy = bool(self.running) or any(not t.done() for t in self._plans.values())
        if busy or self._idle_ticks < 3:
            return tick_seconds(self.env)
        return idle_tick_seconds(self.env)

    async def _forever(self) -> None:
        while True:
            try:
                started = await self.tick()
                self._idle_ticks = 0 if (started or self.running) else self._idle_ticks + 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("crew tick failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._sleep_for())
            except asyncio.TimeoutError:
                pass

    def _day(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    async def is_owner(self, user_id: str) -> bool:
        if user_id in self._owner_cache:
            return self._owner_cache[user_id]
        ok = False
        if self.owner_fn is not None:
            try:
                ok = bool(await self.owner_fn(user_id))
            except Exception:
                logger.exception("crew owner check failed")
                return False
        self._owner_cache[user_id] = ok
        return ok

    def _media_fetcher(self, uid: str):
        """(item_id) -> (bytes, mime) from this user's own media library, for Etsy listing photos."""
        async def fetch(item_id: str):
            ctx = self.media_ctx_fn(uid) if self.media_ctx_fn else None
            if ctx is None:
                raise conns.ConnectionError_("The media library is not set up on the server.")
            from openjarvis.server.media import service as media
            try:
                item = await media.get_owned(ctx, item_id)
                return await ctx.storage.get(item.storage_key), item.mime_type
            except Exception as exc:
                raise conns.ConnectionError_(f"Could not read media item {item_id}: {getattr(exc, 'message', None) or exc}") from None
        return fetch

    async def env_for(self, user_id: str):
        """The owner's crew uses the server's environment; anyone else's crew only the platform settings and their OWN connections."""
        if await self.is_owner(user_id):
            from openjarvis.server.crew import tenant

            key = await tenant.get(self.store, user_id, "marnyx_key")       # pasted on the Station (Connections → Marnyx)
            if not key:
                return self.env
            import os

            return dict(os.environ if self.env is None else self.env, MARNYX_API_KEY=key)
        from openjarvis.server.crew import tenant

        name = ""
        if self.name_fn is not None:
            try:
                name = await self.name_fn(user_id) or ""
            except Exception:
                name = ""
        return await tenant.subscriber_env(self.store, user_id, self.env, name)

    @staticmethod
    def _for_person(text: str, envx) -> str:
        """Prompts are written for Michael; a subscriber's crew gets them with the subscriber's name instead."""
        person = (envx or {}).get("CREW_PERSON") if isinstance(envx, dict) else None
        return text.replace("Michael", person) if person else text

    async def _day_room(self, user_id: str) -> float:
        if await self.is_owner(user_id):
            return 1e9                                      # the owner's own crew has no daily cap
        return min(daily_cap_usd(self.env) - float(await self.store.get_day_spend(user_id, self._day())), await self._month_room(user_id))

    async def _month_room(self, user_id: str) -> float:
        from openjarvis.server.crew import usage as us

        return us.room_usd(await self.store.month_spend(user_id, us.month_start(self.now())), self.env)

    async def usage(self, user_id: str) -> Optional[Dict[str, Any]]:
        """A subscriber's month: included work used and extra usage billed. None for the owner (never metered)."""
        if await self.is_owner(user_id):
            return None
        from openjarvis.server.crew import usage as us

        return us.summary(await self.store.month_spend(user_id, us.month_start(self.now())), self.env)

    async def _after_spend(self, user_id: str) -> None:
        """Report a subscriber's extra usage to Stripe in the background (at most one report running per account)."""
        if await self.is_owner(user_id) or self.pool is None:
            return
        t = self._usage_tasks.get(user_id)
        if t is not None and not t.done():
            return

        async def go():
            from openjarvis.server.crew import usage as us

            try:
                spent = await self.store.month_spend(user_id, us.month_start(self.now()))
                await us.report(self.store, self.pool, user_id, spent, self.now(), self.env, self.client_factory)
            except Exception:
                logger.exception("crew usage report failed")

        self._usage_tasks[user_id] = asyncio.create_task(go())

    async def may_run(self, user_id: str) -> Optional[str]:
        """None when this owner's crew may start work now, otherwise the reason it may not."""
        s = await self.store.get_settings(user_id)
        if s["paused"]:
            return "paused"
        if not s["night_shift"] and in_quiet_hours(self.now(), self.env):
            return "quiet hours (Night Shift is off)"
        if await self._day_room(user_id) < EST_CALL_USD:
            monthly = await self._month_room(user_id) < EST_CALL_USD and not await self.is_owner(user_id)
            key = (user_id, self._day(), monthly)
            if key not in self._cap_noted:
                self._cap_noted.add(key)
                if monthly:
                    from openjarvis.server.crew import usage as us

                    why = ("This month's extra-usage cap is reached." if us.plan(self.env)["payg"] else "This month's included crew work is used up.")
                    await self.store.add_event(user_id, "budget", why + " Work resumes on the 1st.")
                    await self._notify(user_id, "Monthly crew usage reached", why + " Queued work resumes on the 1st of next month.")
                else:
                    await self.store.add_event(user_id, "budget", f"Daily crew limit (${daily_cap_usd(self.env):.2f}) reached. Work resumes after midnight UTC.")
                    await self._notify(user_id, "Daily crew limit reached", f"The crew has used today's ${daily_cap_usd(self.env):.2f}. Queued work resumes after midnight UTC.")
            return "monthly usage reached" if monthly else "daily limit reached"
        return None

    async def tick(self) -> int:
        """Start as many runnable tasks as there are free slots. Returns how many were started."""
        self._ticks += 1
        if self.herald is not None and (self._herald_task is None or self._herald_task.done()):
            self._herald_task = asyncio.create_task(self.herald.maybe_run())
        if self.weekly is not None and self._ticks % 6 == 1 and (self._weekly_task is None or self._weekly_task.done()):
            self._weekly_task = asyncio.create_task(self.weekly.maybe_run())
        if self._ticks % 30 == 0:
            self._refresh_products_soon()
            await self.store.requeue_stale(STALE_RUNNING_S)
        self.running = {k: v for k, v in self.running.items() if not v.done()}
        free = max_concurrent(self.env) - len(self.running)
        started, blocked = 0, {}
        if free <= 0:
            return 0
        for t in await self.store.queued_candidates():
            if started >= free:
                break
            if t["role"] == "workbench":                    # Rig's jobs run on Michael's PC: the Workbench helper collects them
                continue
            uid = t["user_id"]
            if uid not in blocked:
                blocked[uid] = await self.may_run(uid)
            if blocked[uid]:
                continue
            if t["role"] == "designer" and self._designer_busy():
                continue                                    # designers make one design at a time: the image service has a rate limit
            claimed = await self.store.claim(t["id"])
            if claimed:
                self._role_of[claimed["id"]] = claimed["role"]
                self.running[claimed["id"]] = asyncio.create_task(self._run_guarded(claimed))
                started += 1
        return started

    def _designer_busy(self) -> bool:
        return any(not t.done() for tid, t in self.running.items() if self._role_of.get(tid) == "designer")

    async def run_until_idle(self, max_ticks: int = 50) -> None:
        """Tick and wait until nothing more can start (used by tests and by a manual 'run now')."""
        for _ in range(max_ticks):
            await asyncio.gather(*self._plans.values(), return_exceptions=True)
            started = await self.tick()
            if self.running:
                await asyncio.gather(*self.running.values(), return_exceptions=True)
            elif not started:
                return

    # ── accounting ───────────────────────────────────────────────────────────────────────────────

    async def _charge(self, user_id: str, mission_id: str, task_id: str, resp: Any) -> float:
        cost = float(self.cost_fn(resp))
        await self.store.add_day_spend(user_id, self._day(), cost)
        await self.store.add_mission_spend(mission_id, cost)
        if task_id:
            await self.store.add_task_spend(task_id, cost)
        if cost > 0:
            await self._after_spend(user_id)
        return cost

    async def _notify(self, user_id: str, title: str, text: str) -> None:
        if self.notifier is not None:
            try:
                await self.notifier.notify(user_id, title, text)
            except Exception:
                logger.exception("crew notify failed")

    # ── missions ─────────────────────────────────────────────────────────────────────────────────

    def clamp_budget(self, budget: Any) -> float:
        try:
            b = float(budget)
        except (TypeError, ValueError):
            return default_mission_budget(self.env)
        return round(min(max(b, 0.05), max_mission_budget(self.env)), 2)

    # ── are the products themselves up? (a plain GET of each live site; the Station shows LIVE or DOWN) ─────────

    def product_urls(self) -> Dict[str, str]:
        """CREW_PRODUCT_URLS as 'key=url,key=url'. Unset in production: the known live sites. Unset in tests: none."""
        import os

        env = os.environ if self.env is None else self.env
        raw = env.get("CREW_PRODUCT_URLS")
        if raw is None:
            if self.env is not None:
                return {}
            raw = "flipdeck=https://flipdeckcards.com,marnyx=https://marnyx.onrender.com,deadzone=https://cybervoid-6tbo.onrender.com"
        out = {}
        for part in str(raw).split(","):
            k, _, v = part.partition("=")
            if k.strip() and v.strip().startswith("https://"):
                out[k.strip().lower()] = v.strip()
        return out

    async def products_for(self, user_id: str, businesses) -> Dict[str, Dict[str, Any]]:
        """A subscriber's own sites, checked like the owner's (cached 10 minutes)."""
        import time

        import httpx

        hit = self._biz_health.get(user_id)
        if hit and time.monotonic() - hit[0] < 600:
            return hit[1]
        cf = self.client_factory or (lambda: httpx.AsyncClient(timeout=8, follow_redirects=True))
        out: Dict[str, Dict[str, Any]] = {}

        async def one(b) -> None:
            url = str(b.get("url") or "")
            if not url.startswith("https://"):
                return
            live, code = False, None
            try:
                async with cf() as c:
                    r = await c.get(url)
                code, live = r.status_code, r.status_code < 500
            except Exception:
                live = False
            out[b["key"]] = {"url": url, "live": live, "status": code, "checked_at": self.now().isoformat()}

        await asyncio.gather(*(one(b) for b in businesses or []), return_exceptions=True)
        self._biz_health[user_id] = (time.monotonic(), out)
        return out

    def drop_products_cache(self, user_id: str) -> None:
        self._biz_health.pop(user_id, None)

    def _refresh_products_soon(self) -> None:
        if self._product_task is None or self._product_task.done():
            self._product_task = asyncio.create_task(self.refresh_products())

    async def refresh_products(self) -> Dict[str, Dict[str, Any]]:
        import httpx

        urls = self.product_urls()
        if not urls:
            return self.products
        cf = self.client_factory or (lambda: httpx.AsyncClient(timeout=10, follow_redirects=True))

        async def one(key: str, url: str) -> None:
            live, code = False, None
            try:
                async with cf() as c:
                    r = await c.get(url)
                code = r.status_code
                live = code < 500
            except Exception:
                live = False
            self.products[key] = {"url": url, "live": live, "status": code, "checked_at": self.now().isoformat()}

        await asyncio.gather(*(one(k, u) for k, u in urls.items()), return_exceptions=True)
        return self.products

    async def missing_for_missions(self, user_id: str) -> List[str]:
        """Subscribers must connect their own GitHub and Stripe before a mission can run. The owner is not held to this."""
        if await self.is_owner(user_id):
            return []
        st = await conns.status(self.store, user_id, await self.env_for(user_id))
        return [st[k]["label"] for k in ("github", "stripe", "render") if not st[k]["connected"]]

    async def start_mission(self, user_id: str, goal: str, budget: Any = None, *, background: bool = True) -> Dict[str, Any]:
        goal = str(goal or "").strip()[:4000]
        if len(goal) < 8:
            raise ValueError("Describe the goal in a sentence or two.")
        missing = await self.missing_for_missions(user_id)
        if missing:
            raise ValueError("Connect " + " and ".join(missing) + " first. Helix can walk you through it.")
        m = await self.store.create_mission(user_id, goal, self.clamp_budget(budget) if budget is not None else default_mission_budget(self.env))
        await self.store.add_event(user_id, "mission", f"New mission: {_clip(goal, 200)} (budget ${m['budget_usd']:.2f})", mission_id=m["id"])
        if background:
            self._plans[m["id"]] = asyncio.create_task(self.plan(m["id"]))
        else:
            await self.plan(m["id"])
        return await self.store.get_mission(m["id"])

    def _planner_prompt(self, conn_text: str, envx=None) -> str:
        roles = "\n".join(f"- {k} ({v['name']}): {v['about']}" for k, v in ROLES.items())
        sub = isinstance(envx, dict) and envx.get("CREW_PERSON")
        intro = (f"You are the planner of Michael's Aibussol crew. Michael's businesses: {envx.get('CREW_BUSINESSES') or 'none added yet'}. "
                 if sub else "You are the planner of Aibussol's crew, working for Michael (owner of FlipDeck LLC; products: FlipDeck Cards, Marnyx, Deadzone Games). ")
        projects = ("|".join(x.strip() for x in str(envx.get("CREW_BUSINESSES") or "").split(",") if x.strip()) + "|Etsy|New").lstrip("|") if sub \
            else "FlipDeck|Marnyx|Deadzone|Aibussol|Etsy|Google Play|App Store|New"
        return (
            intro +
            f"Break his goal into 2 to {max_tasks(self.env)} concrete tasks that agents can do in parallel where possible.\n"
            f"Roles:\n{roles}\n"
            f"Connected platforms right now:\n{conn_text or '- none'}\n"
            "Project (which factory on the Station the work lives in): anything sold on Etsy (digital products, printables, listings) is "
            "project Etsy; games, game builds and putting games on the games site are Deadzone; publishing an app to Google Play or the "
            "App Store is Google Play / App Store; work on an existing business uses that business. Only a genuinely NEW business that is "
            "none of these uses project New, with a title that is the business's name, so it gets its own factory.\n"
            "Rules: each task produces something concrete (a file in the outbox, a draft, a decision for Michael). Do not plan work on a platform that is "
            "not connected; plan a task that prepares it instead and says what Michael must set up. Account creation, identity checks, payouts and app "
            "store submissions are Michael's; plan around them. Keep briefs specific.\n"
            f"Michael's house rules (follow them):\n{house_rules(envx if envx is not None else self.env)}\n"
            'Answer with JSON only: {"title": "short mission name", "project": "' + projects + '", '
            '"tasks": [{"title": "...", "role": "' + "|".join(ROLES) + '", "brief": "what to do and what to deliver", '
            '"depends_on": [indexes of earlier tasks]}]}'
        )

    async def plan(self, mission_id: str) -> None:
        m = await self.store.get_mission(mission_id)
        if not m or m["status"] != "planning":
            return
        uid = m["user_id"]
        try:
            if await self._day_room(uid) < EST_CALL_USD:
                await self.store.update_mission(mission_id, status="paused")
                await self.store.add_event(uid, "budget", "Not planned yet: the daily crew limit is used up. Resume it tomorrow.", mission_id=mission_id)
                await self._notify(uid, "Mission waiting", f'"{_clip(m["goal"], 80)}" will be planned when the daily crew limit resets. Resume it from the Station.')
                return
            envx = await self.env_for(uid)
            conn_text = conns.describe_for_agent(await conns.status(self.store, uid, envx))
            pc = self.presence.status(uid)
            conn_text += ("\n- Michael's PC (Workbench, for Rig): " + ("connected" + (", Unity" if pc.get("unity") else "") + (", Unreal" if pc.get("unreal") else "")
                                                                         + (", projects: " + ", ".join(pc.get("projects") or []) if pc.get("projects") else "")
                                                                         if pc.get("online") else "not connected right now (Rig tasks wait until it is)"))
            msgs = [{"role": "system", "content": self._for_person(self._planner_prompt(conn_text, envx), envx)},
                    {"role": "user", "content": m["goal"]}]
            cost, plan = 0.0, None
            for attempt in range(2):
                resp = await self.acompletion(model=self.models["writing"], max_tokens=PLANNER_MAX_TOKENS, messages=msgs)
                cost += await self._charge(uid, mission_id, "", resp)
                text = resp.choices[0].message.content or ""
                plan = parse_plan(text, max_tasks(self.env))
                if plan:
                    break
                logger.warning("crew planner answer unusable (attempt %d, finish=%s, %d chars): %s", attempt + 1,
                               getattr(resp.choices[0], "finish_reason", "?"), len(text), text[:300].replace("\n", " "))
                msgs = msgs + [{"role": "assistant", "content": text[:2000]},
                               {"role": "user", "content": "That was not valid JSON or was cut off. Answer again with ONLY the JSON object, "
                                                           f"at most {max_tasks(self.env)} tasks, each brief under 80 words."}]
            if not plan:
                await self.store.update_mission(mission_id, status="failed", summary="The planner did not return a usable plan.")
                await self.store.add_event(uid, "error", "Planning failed: no usable plan came back.", mission_id=mission_id, cost_usd=cost)
                await self._notify(uid, "Mission could not be planned", f'"{_clip(m["goal"], 80)}": the planner\'s answer was not usable. Try rewording the goal.')
                return
            plan["tasks"] = add_review_task(plan["tasks"])
            left = max(0.05, m["budget_usd"] - cost)
            per = round(left / len(plan["tasks"]), 4)
            for t in plan["tasks"]:
                t["budget_usd"] = per
            await self.store.create_tasks(mission_id, uid, plan["tasks"])
            self.poke()
            if re.search(r"\betsy\b", m["goal"], re.I) and str(plan["project"]).strip().lower() in ("", "new", "aibussol"):
                plan["project"] = "Etsy"                              # Etsy work lives in the Etsy factory, not a new one
            await self.store.update_mission(mission_id, status="active", title=plan["title"] or _clip(m["goal"], 60), project=plan["project"])
            lines = "; ".join(f'{t["idx"] + 1}. {t["title"]} ({t["role"]})' for t in plan["tasks"])
            await self.store.add_event(uid, "plan", f"Planned {len(plan['tasks'])} tasks: {lines}", mission_id=mission_id, cost_usd=cost)
            await self._notify(uid, f"Mission planned: {plan['title'] or _clip(m['goal'], 40)}",
                               f"{len(plan['tasks'])} tasks, ${m['budget_usd']:.2f} budget. The crew is starting. Watch it in the Station.")
        except Exception:
            logger.exception("planning failed for %s", mission_id)
            await self.store.update_mission(mission_id, status="failed", summary="Planning failed (the model could not be reached).")
            await self.store.add_event(uid, "error", "Planning failed: the model could not be reached.", mission_id=mission_id)
        finally:
            self._plans.pop(mission_id, None)

    async def _owned_mission(self, user_id: str, mission_id: str) -> Dict[str, Any]:
        m = await self.store.get_mission(mission_id)
        if not m or m["user_id"] != user_id:
            raise LookupError("No such mission.")
        return m

    async def pause_mission(self, user_id: str, mission_id: str) -> Dict[str, Any]:
        m = await self._owned_mission(user_id, mission_id)
        if m["status"] in ("active", "planning"):
            await self.store.update_mission(mission_id, status="paused")
            await self.store.add_event(user_id, "mission", "Mission paused.", mission_id=mission_id)
        return await self.store.get_mission(mission_id)

    async def resume_mission(self, user_id: str, mission_id: str) -> Dict[str, Any]:
        self.poke()                                         # the loop goes back to quick ticks
        m = await self._owned_mission(user_id, mission_id)
        if m["status"] in ("paused", "stalled"):
            tasks = await self.store.list_tasks(mission_id)
            if not tasks:
                await self.store.update_mission(mission_id, status="planning")
                self._plans[mission_id] = asyncio.create_task(self.plan(mission_id))
            else:
                retried = 0
                for t in tasks:
                    if t["status"] == "failed":                     # Resume = try the failed tasks again, each with a fresh set of steps
                        note = [{"role": "user", "content": await self._context(t, m)}] if not t["transcript"] else \
                            list(t["transcript"]) + [{"role": "user", "content": "Michael resumed the mission. Continue from here and finish."}]
                        await self.store.update_task(t["id"], status="queued", runs=0, transcript=note)
                        retried += 1
                await self.store.update_mission(mission_id, status="active")
                if retried:
                    await self.store.add_event(user_id, "mission", f"Retrying {retried} failed task(s).", mission_id=mission_id)
            await self.store.add_event(user_id, "mission", "Mission resumed.", mission_id=mission_id)
        return await self.store.get_mission(mission_id)

    async def cancel_mission(self, user_id: str, mission_id: str) -> Dict[str, Any]:
        m = await self._owned_mission(user_id, mission_id)
        if m["status"] not in ("done", "cancelled"):
            await self.store.update_mission(mission_id, status="cancelled")
            await self.store.set_mission_tasks_status(mission_id, ("queued", "waiting", "running"), "cancelled")
            await self.store.expire_mission_questions(mission_id)
            await self.store.add_event(user_id, "mission", "Mission cancelled.", mission_id=mission_id)
        return await self.store.get_mission(mission_id)

    async def set_mission_budget(self, user_id: str, mission_id: str, budget: Any) -> Dict[str, Any]:
        """Michael sets a mission's budget himself. Clamped to the allowed maximum and never below what is already spent.
        A stalled mission stays stalled: Michael resumes it when he is ready."""
        m = await self._owned_mission(user_id, mission_id)
        try:
            b = float(budget)
        except (TypeError, ValueError):
            raise ValueError("Enter a dollar amount.")
        spent = float(m["spent_usd"] or 0.0)
        cap = max_mission_budget(self.env)
        if b > cap:
            raise ValueError(f"The most a mission can have is ${cap:.2f}.")
        if b < spent:
            raise ValueError(f"It has already spent ${spent:.2f}, so the budget cannot go below that.")
        b = round(max(b, 0.05), 2)
        await self.store.update_mission(mission_id, budget_usd=b)
        await self.store.add_event(user_id, "budget", f"Mission budget set to ${b:.2f} by Michael.", mission_id=mission_id)
        return await self.store.get_mission(mission_id)

    async def _check_mission(self, mission_id: str) -> None:
        m = await self.store.get_mission(mission_id)
        if not m or m["status"] != "active":
            return
        tasks = await self.store.list_tasks(mission_id)
        if await self._hold_for_art(m, tasks):
            return
        if tasks and all(t["status"] == "done" for t in tasks):
            summary = "\n".join(f'- {t["title"]}: {_clip(t["result"], 300)}' for t in tasks)
            # a mission is not done while work is unfinished: it stalls with the reason, and Michael is told
            reason = await self._unfinished_reason(m, tasks)
            if reason:
                await self.store.update_mission(mission_id, status="stalled", summary=summary)
                await self.store.add_event(m["user_id"], "error", f"Mission not finished: {reason} Nothing was marked complete.", mission_id=mission_id)
                await self._notify(m["user_id"], "Mission not finished", f'"{m["title"] or _clip(m["goal"], 60)}": {reason} Open the Station to see what is left.')
                return
            await self.store.update_mission(mission_id, status="done", summary=summary)
            await self._ask_publish_drafts(m, tasks)          # the next step is an approval, never a chat promise
            outs = await self.store.list_outputs(mission_id)
            await self.store.add_event(m["user_id"], "done", f"Mission complete. {len(outs)} file(s) in the outbox. Spent ${m['spent_usd']:.2f}.", mission_id=mission_id)
            import os

            on = ((os.environ if self.env is None else self.env).get("CREW_MISSION_REPORTS") or "on").strip().lower() not in ("off", "0", "false", "no")
            report = await self._mission_report(m, tasks, outs) if on else ""
            await self._notify(m["user_id"], f"Mission complete: {m['title'] or _clip(m['goal'], 40)}",
                               _clip(report, 900) if report else f"{len(tasks)} tasks done, {len(outs)} file(s) in the outbox, ${m['spent_usd']:.2f} spent.\n{_clip(summary, 700)}")
            return
        live = [t for t in tasks if t["status"] in ("queued", "running", "waiting")]
        failed = [t for t in tasks if t["status"] == "failed"]
        if failed and not live:
            await self.store.update_mission(mission_id, status="stalled")
            await self.store.add_event(m["user_id"], "error", f"Mission stalled: {len(failed)} task(s) failed and nothing else can run.", mission_id=mission_id)
            await self._notify(m["user_id"], "Mission stalled", f'"{m["title"] or _clip(m["goal"], 60)}": {failed[0]["title"]} failed. Open the Station to see why.')
        elif failed:
            # tasks that wait on a failed task can never start: mark them so the mission can settle
            failed_idx = {t["idx"] for t in failed}
            for t in tasks:
                if t["status"] == "queued" and failed_idx.intersection(t["depends_on"]):
                    await self.store.update_task(t["id"], status="failed", result="Not started: a task it depends on failed.")
            await self._recheck_once(mission_id, tasks)

    async def _art_state(self, uid: str, mid: str):
        """The artwork gate of a mission: 'waiting', 'approved', 'declined', or None when no gate was raised."""
        v = await self.store.get_secret(uid, f"artgate:{mid}")       # one stored value per mission: no event scans on every check
        return v if v in ("waiting", "approved", "declined") else None

    async def _art_blocks(self, uid: str, mid: str) -> bool:
        """True while Etsy listing actions must not run: the owner has not approved the artwork yet."""
        state = await self._art_state(uid, mid)
        if state == "approved":
            return False
        if state is not None:
            return True
        return any(t.get("role") == "designer" and t["status"] != "done" for t in await self.store.list_tasks(mid))

    async def _hold_for_art(self, m: Dict[str, Any], tasks: List[Dict[str, Any]]) -> bool:
        """Once all artwork is finished for a listing mission, hold the rest until Michael approves the art on one card.
        Returns True while the mission is held."""
        import re as _re
        if not _re.search(r"\betsy\b|\blisting|\bdraft|\bstore\b|\bpublish", f'{m.get("title") or ""} {m.get("goal") or ""}', _re.I):
            return False
        designers = [t for t in tasks if t.get("role") == "designer"]
        if not designers or any(t["status"] != "done" for t in designers):
            return False
        state = await self._art_state(m["user_id"], m["id"])
        if state in ("approved", "declined"):
            return False                                  # declined: the held tasks were stopped, so the mission can settle
        held = [t for t in tasks if t["status"] == "queued" and t.get("role") not in ("designer", "reviewer")]
        if state is None:
            if not held:
                return False
            what = ("Approve the artwork for this mission: " + "; ".join(t["title"] for t in designers)[:300]
                    + ". Nothing is drafted or published until you approve it.")
            await self.store.add_question(m["id"], designers[-1]["id"], m["user_id"], "checkpoint", what, ["Approve", "Decline"],
                                          {"platform": "internal", "action": "approve_art", "args": {}})
            await self.store.add_event(m["user_id"], "checkpoint", f"Checkpoint for Michael: {what}", mission_id=m["id"], task_id=designers[-1]["id"])
            await self.store.set_secret(m["user_id"], f"artgate:{m['id']}", "waiting")
            await self.store.add_event(m["user_id"], "checkpoint", "Artwork waiting for approval.", mission_id=m["id"])
            await self._notify(m["user_id"], "Art approval needed", what + "\nApprove or decline it in Aibussol.")
        for t in held:
            await self.store.update_task(t["id"], status="waiting")
        return True

    async def _settle_art(self, mid: str, uid: str, approve: bool) -> None:
        """Michael answered the art card: release the held tasks, or stop them."""
        open_task_ids = {q["task_id"] for q in await self.store.list_questions(uid, status="open")}
        held = [t for t in await self.store.list_tasks(mid) if t["status"] == "waiting" and t["id"] not in open_task_ids]
        if approve:
            await self.store.set_secret(uid, f"artgate:{mid}", "approved")
            await self.store.add_event(uid, "checkpoint", "Artwork approved.", mission_id=mid)
            for t in held:
                await self.store.update_task(t["id"], status="queued")
        else:
            await self.store.set_secret(uid, f"artgate:{mid}", "declined")
            await self.store.add_event(uid, "checkpoint", "Artwork declined.", mission_id=mid)
            for t in held:
                await self.store.update_task(t["id"], status="failed", result="Stopped: the artwork was not approved.")
            await self._check_mission(mid)

    async def _unfinished_reason(self, m: Dict[str, Any], tasks: List[Dict[str, Any]]) -> str:
        """Why a mission whose tasks are all done still is not finished ('' when it is). Two checks:
        1) the reviewer (Lens) says something is missing, unfinished, waiting or needs rework;
        2) the goal asks for Etsy drafts and no draft listing was actually created."""
        import re as _re
        for t in tasks:
            if t.get("role") == "reviewer" and _re.search(
                    r"\bmissing\b|\bnot (?:yet )?(?:made|created|drafted|finished|complete|completed|reworked|ready)\b"
                    r"|\bwaiting (?:for|on)\b|\bincomplete\b|\bunfinished\b|\bneeds? (?:a )?rework\b|\bpending\b",
                    str(t.get("result") or ""), _re.I):
                return "the reviewer found work still missing or waiting, so the mission is not complete."
        goal = f'{m.get("title") or ""} {m.get("goal") or ""}'
        if _re.search(r"\betsy\b", goal, _re.I) and _re.search(r"\bdraft", goal, _re.I):
            made = any("Etsy draft listing created:" in str(e.get("text") or "")
                       for e in await self.store.list_events(m["user_id"], m["id"], limit=500))
            if not made:
                return "the goal asks for Etsy drafts, but no draft listing was created."
        return ""

    async def _ask_publish_drafts(self, m: Dict[str, Any], tasks: List[Dict[str, Any]]) -> None:
        """When a mission made Etsy draft listings, the next step is ONE approval to publish exactly those drafts.
        Only listings this mission created are included (never other drafts in the shop)."""
        import re as _re
        from openjarvis.server.crew import connections as conns
        found: Dict[int, str] = {}
        for e in await self.store.list_events(m["user_id"], m["id"], limit=500):
            for lid, title in _re.findall(r'Etsy draft listing created: (\d+) "([^"]*)"', str(e.get("text") or "")):
                found[int(lid)] = title
        if not found or not tasks:
            return
        ids = sorted(found)
        tid = tasks[-1]["id"]
        what = conns.describe_action("etsy", "publish_listings", {"listing_ids": ids}) + " " + "; ".join(found[i][:60] for i in ids)
        await self.store.add_question(m["id"], tid, m["user_id"], "checkpoint", what, ["Approve", "Decline"],
                                      {"platform": "etsy", "action": "publish_listings", "args": {"listing_ids": ids}})
        await self.store.add_event(m["user_id"], "checkpoint", "Checkpoint for Michael: " + what, mission_id=m["id"], task_id=tid)
        await self._notify(m["user_id"], "Approval needed", f"Publish {len(ids)} Etsy draft(s) from this mission? Approve in Aibussol, or say approve.")

    async def write_report(self, user_id: str, mission_id: str) -> Dict[str, Any]:
        """The report on demand (for missions that finished before reports existed, or to refresh one)."""
        m = await self._owned_mission(user_id, mission_id)
        await self._mission_report(m, await self.store.list_tasks(mission_id), await self.store.list_outputs(mission_id))
        return await self.store.get_mission(mission_id)

    async def _mission_report(self, m: Dict[str, Any], tasks: List[Dict[str, Any]], outs: List[Dict[str, Any]]) -> str:
        """Helix's plain report when a mission finishes: what was made, what is live (with links), what was spent, and what Michael
        must do next. Saved as mission_report.md and used as the mission summary and the phone notice."""
        uid, mid = m["user_id"], m["id"]
        try:
            events = await self.store.list_events(uid, mission_id=mid, limit=60)
            facts = "\n".join(f'- {t["title"]} ({ROLES.get(t["role"], {}).get("name", t["role"])}): {_clip(t["result"], 1200)}' for t in tasks)
            approvals = "\n".join(f'- {e["text"]}' for e in events if e["kind"] in ("checkpoint", "connection", "code"))[:3000]
            files = ", ".join(o["name"] for o in outs) or "none"
            envx = await self.env_for(uid)
            prompt = (
                "Write the mission report for Michael, the owner, in plain words. Use ONLY the facts below; never invent links, numbers or results. "
                "Sections, each short:\n## What was done\n(who on the crew did what)\n## What is live now\n(anything public or paid: products, prices, "
                "payment links with their URLs, posts, merges; say clearly if nothing is live)\n## Files\n(what is in the outbox and what each is for)\n"
                "## Money\n(spent on the crew; any price set)\n## What you need to do next\n(numbered, concrete; include anything unfinished or risky)\n\n"
                f"Mission: {m['title'] or ''}\nGoal: {m['goal']}\nSpent: ${m['spent_usd']:.2f} of ${m['budget_usd']:.2f}\n\nTasks and results:\n{facts}\n\n"
                f"Approvals and platform actions:\n{approvals or '- none'}\n\nOutbox files: {files}"
            )
            resp = await self.acompletion(model=self.models["writing"], max_tokens=1500,
                                          messages=[{"role": "system", "content": self._for_person("You write short, factual reports for Michael.", envx)},
                                                    {"role": "user", "content": prompt}])
            await self._charge(uid, mid, "", resp)
            report = (resp.choices[0].message.content or "").strip()
        except Exception:
            logger.exception("mission report failed")
            return ""
        if not report:
            return ""
        await self.store.add_output(mid, "", uid, "mission_report.md", f"# {m['title'] or 'Mission'} report\n\n{report}\n")
        await self.store.update_mission(mid, summary=report[:8000])
        await self.store.add_event(uid, "done", "Helix wrote the mission report (mission_report.md).", mission_id=mid)
        return report

    async def _recheck_once(self, mission_id: str, before: List[Dict[str, Any]]) -> None:
        after = await self.store.list_tasks(mission_id)
        if [t["status"] for t in after] != [t["status"] for t in before]:
            await self._check_mission(mission_id)

    # ── running a task ───────────────────────────────────────────────────────────────────────────

    async def _run_guarded(self, task: Dict[str, Any]) -> None:
        try:
            await self._run_guarded_inner(task)
        finally:
            self.poke()                                     # follow-up tasks may be runnable now

    async def _run_guarded_inner(self, task: Dict[str, Any]) -> None:
        try:
            await self.run_task(task)
        except asyncio.CancelledError:
            await self.store.update_task(task["id"], status="queued")
            raise
        except Exception:
            logger.exception("crew task %s failed", task["id"])
            t = await self.store.get_task(task["id"]) or task
            runs = t["runs"] + 1
            if runs >= MAX_RUNS:
                await self.store.update_task(task["id"], status="failed", runs=runs, result="Stopped after repeated errors.")
                await self.store.add_event(task["user_id"], "error", f'"{task["title"]}" stopped after repeated errors.', mission_id=task["mission_id"], task_id=task["id"])
                await self._check_mission(task["mission_id"])
            else:
                await self.store.update_task(task["id"], status="queued", runs=runs)
                await self.store.add_event(task["user_id"], "error", f'"{task["title"]}" hit an error and will retry.', mission_id=task["mission_id"], task_id=task["id"])

    async def _context(self, task: Dict[str, Any], mission: Dict[str, Any]) -> str:
        siblings = await self.store.list_tasks(task["mission_id"])
        deps = [s for s in siblings if s["idx"] in task["depends_on"]]
        dep_text = "\n".join(f'- {d["title"]}: {d["result"][:1500]}' for d in deps) or "- none"
        outs = await self.store.list_outputs(task["mission_id"])
        out_text = ", ".join(o["name"] for o in outs) or "none yet"
        team = "\n".join(f'- {s["idx"] + 1}. {s["title"]} ({s["role"]}, {s["status"]})' for s in siblings)
        return (f"Mission: {mission['goal']}\n\nYour task ({task['title']}):\n{task['brief']}\n\n"
                f"Results from tasks you depend on:\n{dep_text}\n\nOutbox so far: {out_text}\n\nThe whole crew:\n{team}")

    def _system(self, task: Dict[str, Any], conn_text: str, envx=None) -> str:
        envx = self.env if envx is None else envx
        role = ROLES.get(task["role"], ROLES["builder"])
        s = (f"You are {role['name']}, the {task['role']} agent in Aibussol's crew, working for Michael. {role['about']}\n"
             "Work only on your task. Save every deliverable with save_output (complete files, not outlines). Keep each save_output call to about "
             "4,000 words or less: a long book part is several files, one per chapter, never one huge file. If you truly need Michael's decision, "
             "use ask_michael with one concrete question and short options; do not ask for things you can decide sensibly yourself. "
             "Never claim something was published, paid, uploaded or sent unless a tool result says so. When done, call finish with a short summary.\n"
             f"Michael's house rules:\n{house_rules(envx)}")
        if task["role"] == "operator":
            s += f"\nConnected platforms and actions:\n{conn_text}\nRisky actions become checkpoints: say so in your summary and wait for the result."
        if task["role"] == "builder":
            if gh.configured(envx) and gh.repos(envx):
                s += ("\nRepos you may use: " + ", ".join(gh.repos(envx)) + ". Read the existing code before changing it, keep changes small and complete, "
                      "commit to one crew/ branch per task with repo_commit, then call request_merge once and stop: Michael approves the merge. Atlas deploys "
                      "on merge, so be careful there. If a change is not ready to merge, finish without request_merge and say why.")
            else:
                s += "\nGitHub is not connected yet, so save code as files with save_output and say in your summary that GitHub needs connecting."
        if task["role"] == "reviewer":
            s += "\nBe strict but fair: send work back only for real problems (wrong, missing, risky, or not what was asked), with exact fixes."
        return self._for_person(s, envx)

    async def run_task(self, task: Dict[str, Any]) -> None:
        tid, uid, mid = task["id"], task["user_id"], task["mission_id"]
        mission = await self.store.get_mission(mid)
        if not mission or mission["status"] != "active":
            await self.store.update_task(tid, status="cancelled" if mission and mission["status"] == "cancelled" else "queued")
            return
        task = await self.store.get_task(tid) or task
        envx = await self.env_for(uid)
        conn_text = conns.describe_for_agent(await conns.status(self.store, uid, envx)) if task["role"] == "operator" else ""
        transcript: List[Dict[str, Any]] = list(task["transcript"])
        if not transcript:
            transcript.append({"role": "user", "content": await self._context(task, mission)})
            await self.store.add_event(uid, "task", f'{task["role"].title()} started "{task["title"]}".', mission_id=mid, task_id=tid)
        model = self.models.get(ROLES.get(task["role"], ROLES["builder"])["model"], self.models["writing"])
        tools = tools_for(task["role"])

        for _ in range(ROUNDS_PER_RUN):
            mission = await self.store.get_mission(mid)
            if not mission or mission["status"] != "active":
                await self.store.update_task(tid, status="queued", transcript=transcript)
                return
            task = await self.store.get_task(tid) or task
            if await self._budget_stop(task, mission, transcript):
                return
            if await self._day_room(uid) < EST_CALL_USD:
                await self.store.update_task(tid, status="queued", transcript=transcript)
                return
            call_msgs = [{"role": "system", "content": self._system(task, conn_text, envx)}] + transcript
            try:
                resp = await self.acompletion(model=model, messages=call_msgs, tools=tools, max_tokens=AGENT_MAX_TOKENS)
            except Exception as exc:
                # a provider out of quota or down (Gemini 429 / 503): finish the task on the writing model instead of failing it
                if model == self.models["writing"] or not _provider_unavailable(exc):
                    raise
                logger.warning("crew: %s unavailable (%s); task %s continues on %s", model, type(exc).__name__, tid, self.models["writing"])
                model = self.models["writing"]
                resp = await self.acompletion(model=model, messages=call_msgs, tools=tools, max_tokens=AGENT_MAX_TOKENS)
            cost = await self._charge(uid, mid, tid, resp)
            msg = resp.choices[0].message
            calls = getattr(msg, "tool_calls", None) or []
            if not calls:
                text = (getattr(msg, "content", None) or "").strip()
                transcript.append({"role": "assistant", "content": text})
                await self._finish(task, text or "Finished.", transcript, cost)
                return
            transcript.append(_msg_dict(msg))
            outcome = None
            for tc in calls:
                if outcome:
                    transcript.append({"role": "tool", "tool_call_id": tc.id, "content": "Not run: the task is already paused or finished."})
                    continue
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = None
                if args is None and getattr(resp.choices[0], "finish_reason", "") == "length":
                    transcript.append({"role": "tool", "tool_call_id": tc.id, "content": _CUT_OFF_MSG})
                    continue
                text, outcome = await self._tool(dict(task, _transcript=transcript, _env=envx), mission, tc.function.name, args if isinstance(args, dict) else None)
                transcript.append({"role": "tool", "tool_call_id": tc.id, "content": text})
            await self.store.add_event(uid, "step", f'"{task["title"]}": {", ".join(tc.function.name for tc in calls)}', mission_id=mid, task_id=tid, cost_usd=cost)
            if outcome == "waiting":
                await self.store.update_task(tid, status="waiting", transcript=transcript)
                return
            if outcome == "rework":
                transcript.append({"role": "user", "content": "The tasks you sent back are being reworked. When they are done you continue here: re-check them, then save review.md and finish."})
                await self.store.update_task(tid, status="queued", transcript=transcript)
                return
            if outcome and outcome.startswith("finish:"):
                await self._finish(task, outcome[7:], transcript, 0.0)
                return
            await self.store.update_task(tid, transcript=transcript)

        runs = task["runs"] + 1
        if runs >= MAX_RUNS and await self._wrap_up(task, transcript, model, envx, conn_text):
            return
        if runs >= MAX_RUNS:
            await self.store.update_task(tid, status="failed", runs=runs, transcript=transcript, result="Stopped: it used all its steps without finishing.")
            await self.store.add_event(uid, "error", f'"{task["title"]}" used all its steps without finishing.', mission_id=mid, task_id=tid)
            await self._notify(uid, "Task stopped", f'"{task["title"]}" used all its steps without finishing. Open the Station to look at it.')
            await self._check_mission(mid)
        else:
            transcript.append({"role": "user", "content": "Continue. Wrap up soon: save your deliverables and call finish."})
            await self.store.update_task(tid, status="queued", runs=runs, transcript=transcript)

    async def _wrap_up(self, task: Dict[str, Any], transcript: List[Dict[str, Any]], model: str, envx, conn_text: str) -> bool:
        """Out of steps: one last call that may only save a file and finish, so finished work is not thrown away as a failure."""
        uid, mid, tid = task["user_id"], task["mission_id"], task["id"]
        if await self._day_room(uid) < EST_CALL_USD:
            return False
        transcript = transcript + [{"role": "user", "content": "You are out of steps. Do not read or search anything more. If a deliverable is "
                                    "not saved yet, save it now with save_output, then call finish with an honest summary: what is done, what "
                                    "is not, and what Michael should check."}]
        tools = [t for t in _BASE_TOOLS if t["function"]["name"] in ("save_output", "finish")]
        try:
            resp = await self.acompletion(model=model, messages=[{"role": "system", "content": self._system(task, conn_text, envx)}] + transcript,
                                          tools=tools, max_tokens=AGENT_MAX_TOKENS)
        except Exception:
            logger.exception("crew wrap-up call failed")
            return False
        cost = await self._charge(uid, mid, tid, resp)
        msg = resp.choices[0].message
        transcript.append(_msg_dict(msg))
        summary = None
        for tc in getattr(msg, "tool_calls", None) or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = None
            if args is None and getattr(resp.choices[0], "finish_reason", "") == "length":
                text = _CUT_OFF_MSG
            elif tc.function.name == "save_output" and isinstance(args, dict):
                text, _ = await self._tool(dict(task, _transcript=transcript, _env=envx), {}, "save_output", args)
            elif tc.function.name == "finish":
                summary = str((args or {}).get("summary") or "").strip() or "Finished."
                text = "Finished."
            else:
                text = "Not run."
            transcript.append({"role": "tool", "tool_call_id": tc.id, "content": text})
        if summary is None and not (getattr(msg, "tool_calls", None)) and (getattr(msg, "content", None) or "").strip():
            summary = msg.content.strip()
        if summary is None:
            return False
        await self.store.add_event(uid, "task", f'"{task["title"]}" ran out of steps and wrapped up.', mission_id=mid, task_id=tid, cost_usd=cost)
        await self._finish(task, "(Wrapped up at the step limit.) " + summary, transcript, 0.0)
        return True

    async def _budget_stop(self, task: Dict[str, Any], mission: Dict[str, Any], transcript: List[Dict[str, Any]]) -> bool:
        task_left = task["budget_usd"] - task["spent_usd"]
        mission_left = mission["budget_usd"] - mission["spent_usd"]
        if task_left >= EST_CALL_USD and mission_left >= EST_CALL_USD:
            return False
        add = round(max(task["budget_usd"] * 0.5, 0.10), 2)
        room = max_mission_budget(self.env) - mission["budget_usd"]
        if room < EST_CALL_USD:
            await self.store.update_task(task["id"], status="failed", transcript=transcript, result="Stopped: the mission reached the largest budget allowed.")
            await self.store.add_event(task["user_id"], "budget", f'"{task["title"]}" stopped: mission budget is at the maximum allowed.', mission_id=mission["id"], task_id=task["id"])
            await self._check_mission(mission["id"])
            return True
        add = min(add, round(room, 2))
        if await self.is_owner(task["user_id"]):
            # the owner's missions top themselves up (up to the per-mission maximum) instead of asking every few minutes
            await self.store.update_task(task["id"], budget_usd=round(task["budget_usd"] + add, 6))
            if mission["budget_usd"] - mission["spent_usd"] < add:
                await self.store.update_mission(mission["id"], budget_usd=round(mission["budget_usd"] + add, 6))
            await self.store.add_event(task["user_id"], "budget", f'Auto-added ${add:.2f} for "{task["title"]}".', mission_id=mission["id"], task_id=task["id"])
            return False
        which = "its own budget" if task_left < EST_CALL_USD else "the mission budget"
        q = await self.store.add_question(mission["id"], task["id"], task["user_id"], "budget",
                                          f'"{task["title"]}" used {which} (task ${task["spent_usd"]:.2f} of ${task["budget_usd"]:.2f}; mission ${mission["spent_usd"]:.2f} of ${mission["budget_usd"]:.2f}). Add ${add:.2f} so it can keep going?',
                                          ["Approve", "Decline"], {"add_usd": add})
        await self.store.update_task(task["id"], status="waiting", transcript=transcript)
        await self.store.add_event(task["user_id"], "budget", f'"{task["title"]}" is waiting for more budget.', mission_id=mission["id"], task_id=task["id"])
        await self._notify(task["user_id"], "Budget needed", q["text"])
        return True

    # ── Rig: jobs that run on Michael's PC through the Workbench helper (crew/workbench.py) ──────────────────

    async def workbench_next(self, user_id: str) -> Optional[Dict[str, Any]]:
        import time as _time

        from openjarvis.server.crew.workbench import job_for

        if self._wb_empty_until.get(user_id, 0) > _time.monotonic():
            return None                                     # nothing queued for the PC; skip the database until something is
        if await self.may_run(user_id) == "paused":
            return None
        cands = await self.store.queued_candidates()
        if not any(t["role"] == "workbench" and t["user_id"] == user_id for t in cands):
            self._wb_empty_until[user_id] = _time.monotonic() + idle_tick_seconds(self.env)
            return None
        for t in cands:
            if t["role"] != "workbench" or t["user_id"] != user_id:
                continue
            claimed = await self.store.claim(t["id"])
            if not claimed:
                continue
            mission = await self.store.get_mission(t["mission_id"]) or {}
            siblings = await self.store.list_tasks(t["mission_id"])
            deps = "\n".join(f'- {d["title"]}: {d["result"][:1500]}' for d in siblings if d["idx"] in claimed["depends_on"]) or "- none"
            outs = ", ".join(o["name"] for o in await self.store.list_outputs(t["mission_id"]))
            if outs:
                deps += f"\nFiles in the mission outbox (on aibussol.com): {outs}"
            await self.store.add_event(user_id, "task", f'Rig started "{t["title"]}" on your PC.', mission_id=t["mission_id"], task_id=t["id"])
            envx = await self.env_for(user_id)
            job = job_for(claimed, mission, deps, self._for_person(house_rules(envx), envx))
            job["project"] = pc_project(self.presence.status(user_id).get("projects") or [], claimed, mission)
            return job
        return None

    async def _own_running_pc_task(self, user_id: str, task_id: str) -> Dict[str, Any]:
        t = await self.store.get_task(task_id)
        if not t or t["user_id"] != user_id or t["role"] != "workbench":
            raise LookupError("No such job.")
        if t["status"] != "running":
            raise ValueError("That job is not running.")
        return t

    async def workbench_progress(self, user_id: str, task_id: str, note: str) -> None:
        t = await self._own_running_pc_task(user_id, task_id)
        await self.store.update_task(t["id"], claimed_at=self.now())       # keeps it from counting as stuck
        await self.store.add_event(user_id, "step", f'"{t["title"]}" on your PC: {_clip(note, 160)}', mission_id=t["mission_id"], task_id=t["id"])

    async def workbench_done(self, user_id: str, task_id: str, ok: bool, summary: str, minutes: int = 0) -> None:
        self.poke()                                         # the loop goes back to quick ticks
        t = await self._own_running_pc_task(user_id, task_id)
        summary = str(summary or "").strip()[:20000] or ("Finished." if ok else "It did not finish.")
        slug = re.sub(r"[^a-z0-9]+", "-", t["title"].lower()).strip("-")[:40] or "job"
        await self.store.add_output(t["mission_id"], t["id"], user_id, f"rig-{slug}.md", f"# {t['title']}\n\nOn Michael's PC, {minutes} min, "
                                    f"{'finished' if ok else 'stopped'}.\n\n{summary}\n")
        if ok:
            await self._finish(t, summary[:4000], list(t["transcript"]), 0.0)
            await self._notify(user_id, "Rig finished on your PC", f'"{t["title"]}" ({minutes} min). {_clip(summary, 600)}')
        else:
            await self.store.update_task(t["id"], status="failed", result=summary[:4000])
            await self.store.add_event(user_id, "error", f'"{t["title"]}" stopped on your PC: {_clip(summary, 200)}', mission_id=t["mission_id"], task_id=t["id"])
            await self._notify(user_id, "Rig stopped on your PC", f'"{t["title"]}": {_clip(summary, 600)}')
            await self._check_mission(t["mission_id"])

    async def _finish(self, task: Dict[str, Any], summary: str, transcript: List[Dict[str, Any]], cost: float) -> None:
        await self.store.update_task(task["id"], status="done", result=summary[:4000], transcript=transcript)
        await self.store.add_event(task["user_id"], "task", f'Done: "{task["title"]}". {_clip(summary, 240)}', mission_id=task["mission_id"], task_id=task["id"], cost_usd=cost)
        await self._check_mission(task["mission_id"])

    async def _tool(self, task: Dict[str, Any], mission: Dict[str, Any], name: str, args: Optional[Dict[str, Any]]):
        """-> (text the model sees, outcome). outcome: None to keep going, 'waiting' to pause the task, 'finish:<summary>'."""
        uid, mid, tid = task["user_id"], task["mission_id"], task["id"]
        if args is None:
            return "Error: the arguments were not valid JSON.", None
        if name == "save_output":
            fname = re.sub(r"[^A-Za-z0-9._ -]", "_", _clip(args.get("name"), 80)).strip() or "output.txt"
            content = str(args.get("content") or "")
            if not content.strip():
                return "Error: the file is empty.", None
            if len(await self.store.list_outputs(mid)) >= MAX_OUTPUTS_PER_MISSION:
                return "Error: the outbox for this mission is full.", None
            await self.store.add_output(mid, tid, uid, fname, content[:MAX_OUTPUT_CHARS])
            await self.store.add_event(uid, "output", f'Saved "{fname}" ({len(content[:MAX_OUTPUT_CHARS]):,} characters).', mission_id=mid, task_id=tid)
            return f'Saved "{fname}" to the outbox.' + (" It was cut to the size limit." if len(content) > MAX_OUTPUT_CHARS else ""), None
        if name == "ask_michael":
            question = _clip(args.get("question"), 600)
            if not question:
                return "Error: the question is empty.", None
            options = [_clip(o, 60) for o in (args.get("options") or []) if _clip(o, 60)][:4] if isinstance(args.get("options"), list) else []
            await self.store.add_question(mid, tid, uid, "question", question, options)
            await self.store.add_event(uid, "question", f'"{task["title"]}" asks: {question}', mission_id=mid, task_id=tid)
            await self._notify(uid, f"Question from the crew · {ROLES.get(task['role'], ROLES['builder'])['name']} ({task['role']})", question + (("\nOptions: " + " / ".join(options)) if options else ""))
            return "Sent to Michael through Helix. Stop here; you will continue with his answer.", "waiting"
        if name == "finish":
            return "Finished.", "finish:" + (str(args.get("summary") or "").strip() or "Finished.")
        if name in LIMITS and _calls_made(task.get("_transcript") or [], name) > LIMITS[name]:
            return f"Limit reached: at most {LIMITS[name]} {name} calls per task. Finish with what you have.", None
        if name == "read_output":
            want = _clip(args.get("name"), 120).lower()
            for o in await self.store.list_outputs(mid):
                if o["name"].lower() == want:
                    full = await self.store.get_output(o["id"])
                    body = (full or {}).get("content") or ""
                    return body[:20000] + ("\n[cut]" if len(body) > 20000 else ""), None
            return "No file by that name in the outbox.", None
        if name == "web_search" and task["role"] in SEARCHERS:
            return await self._search(task, args)
        if name == "generate_image" and task["role"] in IMAGERS:
            return await self._image(task, mission, args)
        if name == "make_design_files" and task["role"] == "designer":
            return await self._design(task, mission, args)
        if name in ("repo_files", "repo_read", "repo_commit", "request_merge") and task["role"] == "builder":
            return await self._repo(task, mission, name, args)
        if name in ("repo_files", "repo_read") and task["role"] == "qa":
            return await self._repo(task, mission, name, args)
        if name == "check_site" and task["role"] == "qa":
            return await self._check_site(task, args), None
        if name == "business_numbers" and task["role"] in ("analyst", "finance"):
            return await self._numbers(task), None
        if name == "use_connection" and task["role"] in READERS + PUBLISHERS:
            platform, action = _clip(args.get("platform"), 30).lower(), _clip(args.get("action"), 40)
            if conns.risk_of(platform, action) != "read" and not (task["role"] in PUBLISHERS and platform == "marnyx"):
                return f"{task['role'].title()} can only read. Ask for {platform}.{action} to be done by Relay (operator) in the summary.", None
            return await self._connection(task, mission, args)
        if name == "request_changes" and task["role"] == "reviewer":
            return await self._request_changes(task, args)
        if name == "web_research" and task["role"] == "researcher":
            return await self._research(task, mission, args)
        if name == "use_connection" and task["role"] == "operator":
            return await self._connection(task, mission, args)
        return f"Error: there is no tool named '{_clip(name, 40)}' for this role.", None

    async def _site_hosts(self, user_id: str) -> Dict[str, str]:
        """The hosts check_site may open: the owner's product sites, or a subscriber's own business sites."""
        from urllib.parse import urlparse

        if await self.is_owner(user_id):
            urls = list(self.product_urls().values()) or ["https://flipdeckcards.com", "https://marnyx.onrender.com", "https://cybervoid-6tbo.onrender.com", "https://aibussol.com"]
            urls.append("https://aibussol.com")
        else:
            from openjarvis.server.crew import tenant

            urls = [b.get("url") or "" for b in await tenant.businesses(self.store, user_id)]
        return {urlparse(u).hostname.lower(): u for u in urls if u.startswith("https://") and urlparse(u).hostname}

    async def _check_site(self, task: Dict[str, Any], args: Dict[str, Any]) -> str:
        import html as html_mod
        import time
        from urllib.parse import urlparse

        import httpx

        url = str(args.get("url") or "").strip()
        u = urlparse(url)
        hosts = await self._site_hosts(task["user_id"])
        host = (u.hostname or "").lower()
        allowed = any(host == h or host.endswith("." + h.removeprefix("www.")) or host == h.removeprefix("www.") for h in hosts)
        if u.scheme != "https" or not allowed:
            return f"Not allowed: check_site opens only this account's own sites ({', '.join(hosts) or 'none added yet'})."
        cf = self.client_factory or (lambda: httpx.AsyncClient(timeout=15, follow_redirects=True))
        t0 = time.monotonic()
        try:
            async with cf() as c:
                r = await c.get(url)
        except Exception as exc:
            return f"{url}: no answer ({type(exc).__name__}). The site may be down."
        ms = int((time.monotonic() - t0) * 1000)
        body = r.text if hasattr(r, "text") else ""
        m = re.search(r"<title[^>]*>(.*?)</title>", body or "", re.S | re.I)
        title = html_mod.unescape(_clip(m.group(1), 120)) if m else "(none)"
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body or "", flags=re.S | re.I)
        text = html_mod.unescape(_clip(re.sub(r"<[^>]+>", " ", text), 1500))
        await self.store.add_event(task["user_id"], "qa", f"Checked {url}: {r.status_code} in {ms} ms.", mission_id=task["mission_id"], task_id=task["id"])
        return (f"{url}\nstatus {r.status_code} · {ms} ms · {len(body or ''):,} characters · type {r.headers.get('content-type', '?')}\n"
                f"title: {title}\ntext: {text or '(empty)'}")

    async def _numbers(self, task: Dict[str, Any]) -> str:
        """The account's own P&L, the same numbers the Station shows."""
        from openjarvis.server.crew import finance as fz
        from openjarvis.server.crew import tenant

        uid = task["user_id"]
        missions = await self.store.list_missions(uid, 200)
        try:
            if await self.is_owner(uid):
                fin = fz.Finance(self.store, pool=self.pool, env=self.env, client_factory=self.client_factory, now=self.now)
                keys = list(fz.KNOWN) + [fz.factory_key(m["project"], m["title"], m["goal"], m["id"]) for m in missions]
                rep = await fin.report(uid, missions, keys)
            else:
                known = tuple(b["key"] for b in await tenant.businesses(self.store, uid))
                envx = task.get("_env") or await self.env_for(uid)
                fin = fz.Finance(self.store, pool=None, client_factory=self.client_factory, now=self.now,
                                 env={"STRIPE_SECRET_KEY": envx.get("STRIPE_SECRET_KEY", ""), "CREW_STRIPE_UNTAGGED": known[0] if known else "business"})
                keys = list(known) + [fz.factory_key(m["project"], m["title"], m["goal"], m["id"], known) for m in missions]
                rep = await fin.report(uid, missions, keys, owner=False)
        except Exception:
            logger.exception("business_numbers failed")
            return "The numbers could not be read right now. Say so in your work instead of guessing."
        t = rep["total"]
        lines = [f"As of {rep.get('as_of', '')} (USD). TOTAL: today in {t['revenue_today']:.2f} out {t['cost_today']:.2f}; 30 days in {t['revenue_30d']:.2f} "
                 f"out {t['cost_30d']:.2f} net {t['net_30d']:.2f}; MRR {t['mrr']:.2f}; subscribers {t['subscribers']}."]
        for k, row in rep["factories"].items():
            lines.append(f"- {k.removeprefix('new:')}: 30 days in {row['revenue_30d']:.2f}, out {row['cost_30d']:.2f} (crew {row.get('crew_cost_30d', 0):.2f}), "
                         f"net {row['net_30d']:.2f}; MRR {row['mrr']:.2f}; subscribers {row['subscribers']}.")
        lines += [f"Note: {n}" for n in rep.get("notes", [])]
        return "\n".join(lines)

    async def _research(self, task: Dict[str, Any], mission: Dict[str, Any], args: Dict[str, Any]):
        uid, mid, tid = task["user_id"], task["mission_id"], task["id"]
        question = _clip(args.get("question"), 500)
        if len(question) < 8:
            return "Error: ask a full research question.", None
        envx = task.get("_env", self.env)
        if not web.configured(envx):
            return "Web research is not connected (" + web.SETUP + "). Work from your own knowledge and say what needs checking.", None
        fresh = await self.store.get_task(tid) or task
        m = await self.store.get_mission(mid) or mission
        if fresh["budget_usd"] - fresh["spent_usd"] < web.RESEARCH_EST_USD or m["budget_usd"] - m["spent_usd"] < web.RESEARCH_EST_USD \
                or await self._day_room(uid) < web.RESEARCH_EST_USD:
            return f"Not enough budget left for web research (about ${web.RESEARCH_EST_USD:.2f}). Finish with what you have, or ask Michael.", None
        await self.store.add_event(uid, "research", f"Scout is researching the web: {question}", mission_id=mid, task_id=tid)
        try:
            out = await self.research_fn(question, envx)
        except web.ResearchUnavailable as exc:
            return f"Web research unavailable: {exc} Work from your own knowledge.", None
        except asyncio.TimeoutError:
            await self._charge_usd(uid, mid, tid, web.RESEARCH_EST_USD)
            return "Web research timed out. Try a narrower question or finish with what you have.", None
        except Exception:
            logger.exception("web research failed")
            await self._charge_usd(uid, mid, tid, web.RESEARCH_EST_USD)
            return "Web research failed. Work from your own knowledge and say so.", None
        cost = out.get("cost_usd")
        cost = web.RESEARCH_EST_USD if cost is None or cost < 0 else float(cost)
        await self._charge_usd(uid, mid, tid, cost)
        sources = out.get("sources") or []
        body = (out.get("report") or "").strip() or "(empty report)"
        doc = f"# {question}\n\n{body}\n\n## Sources\n" + ("\n".join(f"- {u}" for u in sources) if sources else "- none listed")
        slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:40] or "research"
        await self.store.add_output(mid, tid, uid, f"research-{slug}.md", doc[:MAX_OUTPUT_CHARS])
        await self.store.add_event(uid, "research", f"Research done ({len(sources)} sources), saved research-{slug}.md.", mission_id=mid, task_id=tid, cost_usd=cost)
        return (f"Report (saved to the outbox as research-{slug}.md):\n{body[:web.MAX_REPORT_TO_AGENT]}\n\nSources:\n" + "\n".join(sources[:15])), None

    async def _search(self, task: Dict[str, Any], args: Dict[str, Any]):
        query = _clip(args.get("query"), 300)
        if len(query) < 3:
            return "Error: give a search query.", None
        try:
            text = await asyncio.wait_for(asyncio.to_thread(self.search_fn, query), timeout=45)
        except Exception:
            logger.exception("web search failed")
            return "Web search failed just now. Try another query or work from what you have.", None
        await self.store.add_event(task["user_id"], "research", f"{ROLES[task['role']]['name']} searched the web: {query}", mission_id=task["mission_id"], task_id=task["id"])
        text = str(text or "").strip() or "No results."
        return text[:8000], None

    async def _room_for(self, task: Dict[str, Any], mission: Dict[str, Any], usd: float) -> bool:
        fresh = await self.store.get_task(task["id"]) or task
        m = await self.store.get_mission(task["mission_id"]) or mission
        return (fresh["budget_usd"] - fresh["spent_usd"] >= usd and m["budget_usd"] - m["spent_usd"] >= usd
                and await self._day_room(task["user_id"]) >= usd)

    async def _image(self, task: Dict[str, Any], mission: Dict[str, Any], args: Dict[str, Any]):
        uid, mid, tid = task["user_id"], task["mission_id"], task["id"]
        prompt = _clip(args.get("prompt"), 500)
        if len(prompt) < 8:
            return "Error: describe the image in detail.", None
        ctx = None
        try:
            ctx = self.media_ctx_fn(uid) if self.media_ctx_fn else None
        except Exception:
            logger.exception("crew media context failed")
        if ctx is None:
            return "Image generation is not set up on the server (media is off or has no budget). Save the prompt with save_output instead.", None
        from openjarvis.server.media import service as media

        try:
            est = float(media.estimate_cost("image", steps=4))
        except Exception:
            est = 0.05
        if not await self._room_for(task, mission, est):
            return f"Not enough budget left for an image (about ${est:.2f}). Save the prompt instead.", None
        try:
            item = await media.generate_and_save(ctx, "image", prompt=prompt)
        except media.MediaServiceError as exc:
            return f"The image was not made: {exc.detail}", None
        except Exception:
            logger.exception("crew image failed")
            return "The image could not be made just now. Save the prompt instead.", None
        await self._charge_usd(uid, mid, tid, est)
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:36] or "image"
        await self.store.add_output(mid, tid, uid, f"image-{slug}.md",
                                    f"# {item.title}\n\nSaved to your Aibussol media library (item {item.id}).\n\nPrompt: {prompt}\n")
        await self.store.add_event(uid, "output", f'Prism made an image: "{item.title}" (in your media library).', mission_id=mid, task_id=tid, cost_usd=est)
        return f'Saved the image "{item.title}" to Michael\'s media library (item {item.id}); a note is in the outbox.', None

    async def _design(self, task: Dict[str, Any], mission: Dict[str, Any], args: Dict[str, Any]):
        """A design pack from one library artwork: four images to the library, SVG and DXF cut files and a pack file to the outbox."""
        import asyncio
        import json

        from openjarvis.server.crew import designs

        uid, mid, tid = task["user_id"], task["mission_id"], task["id"]
        item_id = _clip(args.get("media_item_id"), 80)
        slug = re.sub(r"[^a-z0-9]+", "-", str(args.get("name") or "").lower()).strip("-")[:40]
        if not item_id or not slug:
            return "Error: give the media_item_id of the artwork and a short pack name.", None
        ctx = None
        try:
            ctx = self.media_ctx_fn(uid) if self.media_ctx_fn else None
        except Exception:
            logger.exception("crew media context failed")
        if ctx is None:
            return "Image saving is not set up on the server, so the design pack cannot be made.", None
        try:
            data, _mime = await self._media_fetcher(uid)(item_id)
            versions = await asyncio.to_thread(designs.make_versions, data)
        except conns.ConnectionError_ as exc:
            return f"The artwork could not be read: {exc}", None
        except ValueError as exc:
            return f"The artwork could not be traced: {exc} Make a new artwork with generate_image and try again.", None
        except Exception:
            logger.exception("design pack failed")
            return "The design pack could not be made just now. Try again.", None
        from openjarvis.server.media import service as media

        ids: Dict[str, str] = {}
        try:
            for key, mime, ext in (("color_png", "image/png", "png"), ("color_jpg", "image/jpeg", "jpg"),
                                   ("bw_png", "image/png", "png"), ("bw_jpg", "image/jpeg", "jpg")):
                variant = "color" if key.startswith("color") else "bw"
                item = await media.save_item(ctx, kind="image", data=versions[key], mime=mime, title=f"{slug} {variant}",
                                             prompt=None, provider="aibussol-design")
                ids[key] = item.id
        except media.MediaServiceError as exc:
            return f"The images could not be saved to the library: {exc.detail}", None
        await self.store.add_output(mid, tid, uid, f"{slug}.svg", versions["svg"])
        await self.store.add_output(mid, tid, uid, f"{slug}.dxf", versions["dxf"])
        pack = {"name": slug, **ids, "svg": f"{slug}.svg", "dxf": f"{slug}.dxf"}
        await self.store.add_output(mid, tid, uid, f"{slug}.pack.json", json.dumps(pack, indent=2))
        await self.store.add_event(uid, "output", f'Prism made the design pack "{slug}" ({versions["shapes"]} cut shapes): PNG, JPG, SVG and DXF.',
                                   mission_id=mid, task_id=tid)
        return (f'Design pack "{slug}" is ready: color and black-and-white PNG and JPG are in Michael\'s media library, and the SVG and DXF '
                f'cut files are in the outbox. Attach it to Etsy with add_listing_file, pack="{slug}.pack.json".'), None

    async def _repo(self, task: Dict[str, Any], mission: Dict[str, Any], name: str, args: Dict[str, Any]):
        uid, mid, tid = task["user_id"], task["mission_id"], task["id"]
        envx = task.get("_env", self.env)
        if not gh.configured(envx) or not gh.repos(envx):
            return "GitHub is not connected (" + gh.SETUP + "). Save code with save_output instead and say so in your summary.", None
        hub = gh.GitHub(envx, self.client_factory)
        try:
            if name == "repo_files":
                return await hub.list_files(_clip(args.get("repo"), 100), _clip(args.get("path"), 200), _clip(args.get("branch"), 80)), None
            if name == "repo_read":
                return await hub.read_file(_clip(args.get("repo"), 100), _clip(args.get("path"), 300), _clip(args.get("branch"), 80)), None
            if name == "repo_commit":
                out = await hub.commit_files(_clip(args.get("repo"), 100), _clip(args.get("branch"), 80) or mission.get("title") or "work",
                                             args.get("files"), _clip(args.get("message"), 200))
                await self.store.add_event(uid, "code", f"Forge committed {len(out['files'])} file(s) to {out['branch']} in {out['repo']}: {_clip(args.get('message'), 120)}",
                                           mission_id=mid, task_id=tid)
                return f"Committed to {out['branch']} ({out['sha'][:7]}): {', '.join(out['files'])}. Compare: {out['url']}", None
            repo, branch = gh.check_repo(_clip(args.get("repo"), 100), envx), gh.crew_branch(_clip(args.get("branch"), 80))
        except gh.GitHubError as exc:
            return f"GitHub: {exc}", None
        except Exception:
            logger.exception("crew github call failed")
            return "GitHub could not be reached just now. Try again once, or finish and say so.", None
        title, summary = _clip(args.get("title"), 120) or branch, str(args.get("summary") or "").strip()[:3000]
        live = " Atlas deploys on merge, so this goes live." if repo.lower().endswith("/atlas") else ""
        what = f"Merge Forge's branch {branch} into main of {repo}: {title}.{live} Review it first: https://github.com/{repo}/compare/{branch}"
        await self.store.add_question(mid, tid, uid, "checkpoint", what, ["Approve", "Decline"],
                                      {"platform": "github", "action": "merge", "args": {"repo": repo, "branch": branch, "title": title, "body": summary}})
        await self.store.add_event(uid, "checkpoint", f"Checkpoint for Michael: {what}", mission_id=mid, task_id=tid)
        await self._notify(uid, "Merge approval needed", what + "\nApprove or decline it in Aibussol.")
        return "Sent to Michael as a merge checkpoint. Nothing is merged yet. Stop here; you will continue with the result.", "waiting"

    async def _request_changes(self, task: Dict[str, Any], args: Dict[str, Any]):
        try:
            n = int(args.get("task_number"))
        except (TypeError, ValueError):
            return "Error: give the task number from the crew list.", None
        notes = str(args.get("notes") or "").strip()[:2000]
        if len(notes) < 10:
            return "Error: say exactly what to fix.", None
        target = next((t for t in await self.store.list_tasks(task["mission_id"]) if t["idx"] == n - 1), None)
        if not target or target["id"] == task["id"] or target["role"] == "reviewer":
            return "Error: no such task to send back.", None
        if target["status"] != "done":
            return f'Task {n} is {target["status"]}, not done; only finished work can be sent back.', None
        tr = list(target["transcript"])
        if any(REVIEW_MARK in str(m.get("content") or "") for m in tr if m.get("role") == "user"):
            return f"Task {n} was already sent back once. Note the remaining problem in your review instead.", None
        tr.append({"role": "user", "content": f"{REVIEW_MARK} Lens (the reviewer) says: {notes}\nFix this: save corrected deliverables, then finish again."})
        await self.store.update_task(target["id"], status="queued", transcript=tr)
        await self.store.add_event(task["user_id"], "review", f'Lens sent "{target["title"]}" back: {_clip(notes, 200)}', mission_id=task["mission_id"], task_id=target["id"])
        return f'Sent task {n} back for fixes.', ("rework" if n - 1 in task["depends_on"] else None)

    async def _charge_usd(self, user_id: str, mission_id: str, task_id: str, usd: float) -> None:
        await self.store.add_day_spend(user_id, self._day(), usd)
        await self.store.add_mission_spend(mission_id, usd)
        await self.store.add_task_spend(task_id, usd)
        await self._after_spend(user_id)

    async def _connection(self, task: Dict[str, Any], mission: Dict[str, Any], args: Dict[str, Any]):
        uid, mid, tid = task["user_id"], task["mission_id"], task["id"]
        platform, action = _clip(args.get("platform"), 30).lower(), _clip(args.get("action"), 40)
        params = args.get("args") if isinstance(args.get("args"), dict) else {}
        risk = conns.risk_of(platform, action)
        if risk is None:
            return f"Error: {platform}.{action} is not an available action.", None
        if platform == "etsy" and risk != "read" and await self._art_blocks(uid, mid):
            return "Blocked: Michael has not approved the artwork yet. Do not create or publish listings until he does.", None
        envx = task.get("_env", self.env)
        st = (await conns.status(self.store, uid, envx)).get(platform, {})
        if not st.get("connected"):
            return f"Not connected: {st.get('setup') or 'the owner has not connected it'}. Do not retry; note it in your summary.", None
        if risk in ("read", "draft"):
            try:
                result = await conns.run(platform, action, params, store=self.store, user_id=uid, mission_id=mid, env=envx, client_factory=self.client_factory,
                                         media_fetch=self._media_fetcher(uid))
            except conns.ConnectionError_ as exc:
                await self.store.add_event(uid, "connection", f"{platform}.{action} refused: {exc}", mission_id=mid, task_id=tid)
                return f"Refused: {exc}", None
            await self.store.add_event(uid, "connection", result, mission_id=mid, task_id=tid)
            return result, None
        try:
            what = conns.describe_action(platform, action, params)
        except conns.ConnectionError_ as exc:
            return f"Error: {exc}", None
        await self.store.add_question(mid, tid, uid, "checkpoint", what, ["Approve", "Decline"], {"platform": platform, "action": action, "args": params})
        await self.store.add_event(uid, "checkpoint", f"Checkpoint for Michael: {what}", mission_id=mid, task_id=tid)
        await self._notify(uid, "Approval needed", what + "\nApprove or decline it in Aibussol.")
        return "Sent to Michael as a checkpoint. It has NOT run. Stop here; you will continue with the result.", "waiting"

    # ── answers from the owner ───────────────────────────────────────────────────────────────────

    async def answer(self, user_id: str, question_id: str, *, answer: str = "", approve: Optional[bool] = None) -> Dict[str, Any]:
        """The owner's reply to a question, checkpoint or budget request. Each can be answered once."""
        self.poke()                                         # the loop goes back to quick ticks
        q = await self.store.get_question(question_id)
        if not q or q["user_id"] != user_id:
            raise LookupError("No such question.")
        if q["status"] != "open":
            raise ValueError("That was already answered.")
        task = await self.store.get_task(q["task_id"])
        if not task:
            raise LookupError("The task is gone.")
        answer = str(answer or "").strip()[:2000]
        transcript = list(task["transcript"])
        envx = await self.env_for(user_id)
        mid, tid = q["mission_id"], q["task_id"]

        if q["kind"] == "question":
            if not answer:
                raise ValueError("Type an answer or pick an option.")
            if not await self.store.close_question(question_id, "answered", answer):
                raise ValueError("That was already answered.")
            transcript.append({"role": "user", "content": f'Michael answered your question "{q["text"]}": {answer}'})
            await self.store.add_event(user_id, "answer", f"Michael answered: {_clip(answer, 200)}", mission_id=mid, task_id=tid)
            result = "Answer sent to the crew."
        elif q["kind"] == "checkpoint":
            if approve is None:
                raise ValueError("Approve or decline it.")
            if not await self.store.close_question(question_id, "approved" if approve else "declined", answer):
                raise ValueError("That was already answered.")
            if (q["action"] or {}).get("action") == "approve_art":
                await self._settle_art(mid, user_id, bool(approve))
                return {"ok": True, "message": "Artwork approved. The crew will draft the listings now." if approve else "Artwork declined. Nothing was drafted or published."}
            if approve:
                act = q["action"] or {}
                if act.get("platform") == "github":
                    a = act.get("args") or {}
                    try:
                        out = await gh.GitHub(envx, self.client_factory).merge_branch(a.get("repo", ""), a.get("branch", ""), a.get("title", ""), a.get("body", ""))
                        transcript.append({"role": "user", "content": f"Michael approved the merge and the server ran it. Result: {out}"})
                        result = out
                    except gh.GitHubError as exc:
                        transcript.append({"role": "user", "content": f"Michael approved the merge but it failed: {exc}. Do not retry blindly."})
                        result = f"It failed: {exc}"
                    except Exception:
                        logger.exception("crew merge failed")
                        transcript.append({"role": "user", "content": "Michael approved the merge but GitHub could not be reached. Say so in your summary."})
                        result = "It failed: GitHub could not be reached."
                    await self.store.add_event(user_id, "code", f"Approved: {q['text']} -> {result}", mission_id=mid, task_id=tid)
                    act = None
                try:
                    if act is None:
                        raise _Done
                    out = await conns.run(act.get("platform", ""), act.get("action", ""), act.get("args") or {}, store=self.store, user_id=user_id,
                                          mission_id=mid, env=envx, client_factory=self.client_factory, media_fetch=self._media_fetcher(user_id))
                    transcript.append({"role": "user", "content": f"Michael approved the checkpoint and the server ran it. Result: {out}"})
                    result = out
                except _Done:
                    pass
                except conns.ConnectionError_ as exc:
                    transcript.append({"role": "user", "content": f"Michael approved the checkpoint but it failed: {exc}. Do not retry blindly."})
                    result = f"It failed: {exc}"
                if act is not None:
                    await self.store.add_event(user_id, "connection", f"Approved: {q['text']} -> {result}", mission_id=mid, task_id=tid)
            else:
                note = f" His note: {answer}" if answer else ""
                transcript.append({"role": "user", "content": f"Michael declined this action; it did not run.{note} Do not retry it. Finish another way or explain in your summary."})
                await self.store.add_event(user_id, "checkpoint", f"Declined: {q['text']}", mission_id=mid, task_id=tid)
                result = "Declined. The crew was told."
        elif q["kind"] == "budget":
            if approve is None:
                raise ValueError("Approve or decline it.")
            if not await self.store.close_question(question_id, "approved" if approve else "declined", answer):
                raise ValueError("That was already answered.")
            if approve:
                add = float((q["action"] or {}).get("add_usd") or 0.0)
                m = await self.store.get_mission(mid)
                add = max(0.0, min(add, max_mission_budget(self.env) - (m["budget_usd"] if m else 0.0)))
                await self.store.update_task(tid, budget_usd=round(task["budget_usd"] + add, 6))
                if m and m["budget_usd"] - m["spent_usd"] < add:
                    await self.store.update_mission(mid, budget_usd=round(m["budget_usd"] + add, 6))
                await self.store.add_event(user_id, "budget", f'Added ${add:.2f} for "{task["title"]}".', mission_id=mid, task_id=tid)
                result = f"Added ${add:.2f}."
            else:
                await self.store.update_task(tid, status="failed", result="Stopped: Michael did not add budget.", transcript=transcript)
                await self.store.add_event(user_id, "budget", f'"{task["title"]}" stopped: budget not raised.', mission_id=mid, task_id=tid)
                await self._check_mission(mid)
                return {"ok": True, "message": "Declined. That task stopped."}
        else:
            raise ValueError("Unknown question type.")

        if task["status"] == "waiting":
            await self.store.update_task(tid, status="queued", transcript=transcript)
        else:
            await self.store.update_task(tid, transcript=transcript)
        return {"ok": True, "message": result}

    # ── what the Station and Helix see ───────────────────────────────────────────────────────────

    async def state(self, user_id: str, missions_limit: int = 12) -> Dict[str, Any]:
        missions = await self.store.list_missions(user_id, missions_limit)
        out = []
        for m in missions:
            tasks = await self.store.list_tasks(m["id"])
            out.append(dict(m, tasks=[{k: v for k, v in t.items() if k != "transcript"} for t in tasks],
                            outputs=await self.store.list_outputs(m["id"])))
        settings = await self.store.get_settings(user_id)
        spent = float(await self.store.get_day_spend(user_id, self._day()))
        uncapped = await self.is_owner(user_id)
        roster = []
        for key, r in ROLES.items():
            mine = [t for m in out for t in m["tasks"] if t["role"] == key]
            if key == "workbench":
                pc = self.presence.status(user_id)
                roster.append({"role": key, "name": r["name"], "model": "YOUR PC · CLAUDE CODE", "can": r["can"], "cannot": r["cannot"],
                               "working": sum(1 for t in mine if t["status"] == "running"), "waiting": 0, "done": sum(1 for t in mine if t["status"] == "done"),
                               "status_text": ("PC online" + (" · " + pc["host"] if pc.get("host") else "") if pc.get("online") else "PC offline")
                               + (" · " + str(sum(1 for t in mine if t["status"] == "queued")) + " job(s) waiting" if any(t["status"] == "queued" for t in mine) else ""),
                               "workbench": pc})
                continue
            roster.append({"role": key, "name": r["name"], "model": hardening.display_name(self.models.get(r["model"], "")),
                           "can": r["can"], "cannot": r["cannot"],
                           "working": sum(1 for t in mine if t["status"] == "running"), "waiting": sum(1 for t in mine if t["status"] == "waiting"),
                           "done": sum(1 for t in mine if t["status"] == "done")})
        if self.herald is not None and not uncapped:
            roster.append({"role": "briefer", "name": "Herald", "model": "LIVE DATA, NO MODEL",
                           "can": "Makes a brief from your live numbers (money per business, sites, the crew) and sends it to your phone",
                           "cannot": "Guess: anything it cannot read is marked unchecked", "working": 0, "waiting": 0, "done": 0,
                           "status_text": "send a brief any time", "herald": True})
        elif self.herald is not None:
            hs = self.herald.status()
            last = hs["last"]
            roster.append({"role": "briefer", "name": "Herald", "model": "LIVE DATA, NO MODEL",
                           "can": "Makes your morning, afternoon and evening briefs inside Aibussol from live data (money per business, MRR, sites, "
                                  "deploys, errors, commits, users, the crew) and sends them to your phone",
                           "cannot": "Guess: anything it cannot read is marked unchecked",
                           "working": 1 if hs["busy"] else 0, "waiting": 0, "done": 0,
                           "status_text": ("making a brief" if hs["busy"] else ("next brief " + hs["next_label"] if hs["enabled"] else "schedule off")) +
                                          (" · last " + last["period"] + (" sent" if last["ok"] else " failed") if last else ""),
                           "herald": True})
        roster.insert(0, {"role": "lead", "name": "Helix", "model": hardening.display_name(self.models.get("writing", "")),
                          "can": "Talks with you, plans each goal into tasks, assigns the crew, brings you their questions and approvals",
                          "cannot": "Approve checkpoints for you or spend past your limits", "working": len(self._plans), "waiting": 0, "done": 0})
        return {
            "roster": roster,
            "products": self.products,
            "missions": out,
            "questions": await self.store.list_questions(user_id, "open"),
            "events": await self.store.list_events(user_id, limit=60),
            "settings": settings,
            "spend": {"today_usd": round(spent, 4), "daily_cap_usd": None if uncapped else daily_cap_usd(self.env), "auto_topup": uncapped,
                      "default_mission_budget_usd": default_mission_budget(self.env), "max_mission_budget_usd": max_mission_budget(self.env)},
            "limits": {"max_concurrent": max_concurrent(self.env), "max_tasks": max_tasks(self.env)},
            "running": len([t for t in self.running.values() if not t.done()]),
            "blocked": await self.may_run(user_id) if not settings["paused"] else "paused",
            "quiet_now": in_quiet_hours(self.now(), self.env),
        }
