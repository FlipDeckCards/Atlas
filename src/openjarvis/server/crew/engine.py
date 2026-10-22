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
PLANNER_MAX_TOKENS = 1600
AGENT_MAX_TOKENS = 2400

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
                 "can": "Use connected platforms: Stripe products and payment links, Etsy draft listings",
                 "cannot": "Anything public, paid or permanent without your Approve"},
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
    "description": "Generate one image from a detailed description (500 characters at most) and save it to Michael's Aibussol media library. Costs a few cents; at most 4 per task.",
    "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}}

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

_REVIEW_TOOL = {"type": "function", "function": {
    "name": "request_changes",
    "description": "Send one finished task back to its agent with concrete fixes (each task can be sent back once). You continue after the rework is done.",
    "parameters": {"type": "object", "properties": {"task_number": {"type": "integer"}, "notes": {"type": "string"}}, "required": ["task_number", "notes"]}}}

LIMITS = {"web_search": 12, "generate_image": 4, "repo_commit": 10}
REVIEW_MARK = "REVIEW: changes requested."

AcompletionFn = Callable[..., Awaitable[Any]]


def _clip(v: Any, n: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:n]


def tools_for(role: str) -> List[Dict[str, Any]]:
    extra: List[Dict[str, Any]] = [_READ_OUTPUT_TOOL]
    if role == "operator":
        extra.append(_CONNECTION_TOOL)
    if role == "researcher":
        extra += [_SEARCH_TOOL, _RESEARCH_TOOL]
    if role == "designer":
        extra.append(_IMAGE_TOOL)
    if role == "builder":
        extra += _REPO_TOOLS
    if role == "reviewer":
        extra += [_SEARCH_TOOL, _REVIEW_TOOL]
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
        self._plans: Dict[str, asyncio.Task] = {}
        self._loop: Optional[asyncio.Task] = None
        self._cap_noted: set = set()
        self._ticks = 0
        self.products: Dict[str, Dict[str, Any]] = {}
        self._product_task: Optional[asyncio.Task] = None
        self.herald = None                                  # crew/herald.py, set by the app: makes the briefs on schedule
        self._herald_task: Optional[asyncio.Task] = None

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

    async def _forever(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("crew tick failed")
            await asyncio.sleep(tick_seconds(self.env))

    def _day(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    async def _day_room(self, user_id: str) -> float:
        return daily_cap_usd(self.env) - float(await self.store.get_day_spend(user_id, self._day()))

    async def may_run(self, user_id: str) -> Optional[str]:
        """None when this owner's crew may start work now, otherwise the reason it may not."""
        s = await self.store.get_settings(user_id)
        if s["paused"]:
            return "paused"
        if not s["night_shift"] and in_quiet_hours(self.now(), self.env):
            return "quiet hours (Night Shift is off)"
        if await self._day_room(user_id) < EST_CALL_USD:
            key = (user_id, self._day())
            if key not in self._cap_noted:
                self._cap_noted.add(key)
                await self.store.add_event(user_id, "budget", f"Daily crew limit (${daily_cap_usd(self.env):.2f}) reached. Work resumes after midnight UTC.")
                await self._notify(user_id, "Daily crew limit reached", f"The crew has used today's ${daily_cap_usd(self.env):.2f}. Queued work resumes after midnight UTC.")
            return "daily limit reached"
        return None

    async def tick(self) -> int:
        """Start as many runnable tasks as there are free slots. Returns how many were started."""
        self._ticks += 1
        if self.herald is not None and (self._herald_task is None or self._herald_task.done()):
            self._herald_task = asyncio.create_task(self.herald.maybe_run())
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
            uid = t["user_id"]
            if uid not in blocked:
                blocked[uid] = await self.may_run(uid)
            if blocked[uid]:
                continue
            claimed = await self.store.claim(t["id"])
            if claimed:
                self.running[claimed["id"]] = asyncio.create_task(self._run_guarded(claimed))
                started += 1
        return started

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

    async def start_mission(self, user_id: str, goal: str, budget: Any = None, *, background: bool = True) -> Dict[str, Any]:
        goal = str(goal or "").strip()[:4000]
        if len(goal) < 8:
            raise ValueError("Describe the goal in a sentence or two.")
        m = await self.store.create_mission(user_id, goal, self.clamp_budget(budget) if budget is not None else default_mission_budget(self.env))
        await self.store.add_event(user_id, "mission", f"New mission: {_clip(goal, 200)} (budget ${m['budget_usd']:.2f})", mission_id=m["id"])
        if background:
            self._plans[m["id"]] = asyncio.create_task(self.plan(m["id"]))
        else:
            await self.plan(m["id"])
        return await self.store.get_mission(m["id"])

    def _planner_prompt(self, conn_text: str) -> str:
        roles = "\n".join(f"- {k} ({v['name']}): {v['about']}" for k, v in ROLES.items())
        return (
            "You are the planner of Aibussol's crew, working for Michael (owner of FlipDeck LLC; products: FlipDeck Cards, Marnyx, Deadzone Games). "
            f"Break his goal into 2 to {max_tasks(self.env)} concrete tasks that agents can do in parallel where possible.\n"
            f"Roles:\n{roles}\n"
            f"Connected platforms right now:\n{conn_text or '- none'}\n"
            "Rules: each task produces something concrete (a file in the outbox, a draft, a decision for Michael). Do not plan work on a platform that is "
            "not connected; plan a task that prepares it instead and says what Michael must set up. Account creation, identity checks, payouts and app "
            "store submissions are Michael's; plan around them. Keep briefs specific.\n"
            'Answer with JSON only: {"title": "short mission name", "project": "FlipDeck|Marnyx|Deadzone|Aibussol|New", '
            '"tasks": [{"title": "...", "role": "researcher|builder|writer|designer|operator|reviewer", "brief": "what to do and what to deliver", '
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
            conn_text = conns.describe_for_agent(await conns.status(self.store, uid, self.env))
            resp = await self.acompletion(model=self.models["writing"], max_tokens=PLANNER_MAX_TOKENS, messages=[
                {"role": "system", "content": self._planner_prompt(conn_text)},
                {"role": "user", "content": m["goal"]}])
            cost = await self._charge(uid, mission_id, "", resp)
            plan = parse_plan(resp.choices[0].message.content or "", max_tasks(self.env))
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
        m = await self._owned_mission(user_id, mission_id)
        if m["status"] in ("paused", "stalled"):
            tasks = await self.store.list_tasks(mission_id)
            if not tasks:
                await self.store.update_mission(mission_id, status="planning")
                self._plans[mission_id] = asyncio.create_task(self.plan(mission_id))
            else:
                await self.store.update_mission(mission_id, status="active")
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

    async def _check_mission(self, mission_id: str) -> None:
        m = await self.store.get_mission(mission_id)
        if not m or m["status"] != "active":
            return
        tasks = await self.store.list_tasks(mission_id)
        if tasks and all(t["status"] == "done" for t in tasks):
            summary = "\n".join(f'- {t["title"]}: {_clip(t["result"], 300)}' for t in tasks)
            await self.store.update_mission(mission_id, status="done", summary=summary)
            outs = await self.store.list_outputs(mission_id)
            await self.store.add_event(m["user_id"], "done", f"Mission complete. {len(outs)} file(s) in the outbox. Spent ${m['spent_usd']:.2f}.", mission_id=mission_id)
            await self._notify(m["user_id"], f"Mission complete: {m['title'] or _clip(m['goal'], 40)}",
                               f"{len(tasks)} tasks done, {len(outs)} file(s) in the outbox, ${m['spent_usd']:.2f} spent.\n{_clip(summary, 700)}")
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

    async def _recheck_once(self, mission_id: str, before: List[Dict[str, Any]]) -> None:
        after = await self.store.list_tasks(mission_id)
        if [t["status"] for t in after] != [t["status"] for t in before]:
            await self._check_mission(mission_id)

    # ── running a task ───────────────────────────────────────────────────────────────────────────

    async def _run_guarded(self, task: Dict[str, Any]) -> None:
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

    def _system(self, task: Dict[str, Any], conn_text: str) -> str:
        role = ROLES.get(task["role"], ROLES["builder"])
        s = (f"You are {role['name']}, the {task['role']} agent in Aibussol's crew, working for Michael. {role['about']}\n"
             "Work only on your task. Save every deliverable with save_output (complete files, not outlines). If you truly need Michael's decision, "
             "use ask_michael with one concrete question and short options; do not ask for things you can decide sensibly yourself. "
             "Never claim something was published, paid, uploaded or sent unless a tool result says so. When done, call finish with a short summary.")
        if task["role"] == "operator":
            s += f"\nConnected platforms and actions:\n{conn_text}\nRisky actions become checkpoints: say so in your summary and wait for the result."
        if task["role"] == "builder":
            if gh.configured(self.env):
                s += ("\nRepos you may use: " + ", ".join(gh.repos(self.env)) + ". Read the existing code before changing it, keep changes small and complete, "
                      "commit to one crew/ branch per task with repo_commit, then call request_merge once and stop: Michael approves the merge. Atlas deploys "
                      "on merge, so be careful there. If a change is not ready to merge, finish without request_merge and say why.")
            else:
                s += "\nGitHub is not connected yet, so save code as files with save_output and say in your summary that GitHub needs connecting."
        if task["role"] == "reviewer":
            s += "\nBe strict but fair: send work back only for real problems (wrong, missing, risky, or not what was asked), with exact fixes."
        return s

    async def run_task(self, task: Dict[str, Any]) -> None:
        tid, uid, mid = task["id"], task["user_id"], task["mission_id"]
        mission = await self.store.get_mission(mid)
        if not mission or mission["status"] != "active":
            await self.store.update_task(tid, status="cancelled" if mission and mission["status"] == "cancelled" else "queued")
            return
        task = await self.store.get_task(tid) or task
        conn_text = conns.describe_for_agent(await conns.status(self.store, uid, self.env)) if task["role"] == "operator" else ""
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
            resp = await self.acompletion(model=model, messages=[{"role": "system", "content": self._system(task, conn_text)}] + transcript,
                                          tools=tools, max_tokens=AGENT_MAX_TOKENS)
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
                text, outcome = await self._tool(dict(task, _transcript=transcript), mission, tc.function.name, args if isinstance(args, dict) else None)
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
        if runs >= MAX_RUNS:
            await self.store.update_task(tid, status="failed", runs=runs, transcript=transcript, result="Stopped: it used all its steps without finishing.")
            await self.store.add_event(uid, "error", f'"{task["title"]}" used all its steps without finishing.', mission_id=mid, task_id=tid)
            await self._notify(uid, "Task stopped", f'"{task["title"]}" used all its steps without finishing. Open the Station to look at it.')
            await self._check_mission(mid)
        else:
            transcript.append({"role": "user", "content": "Continue. Wrap up soon: save your deliverables and call finish."})
            await self.store.update_task(tid, status="queued", runs=runs, transcript=transcript)

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
        which = "its own budget" if task_left < EST_CALL_USD else "the mission budget"
        q = await self.store.add_question(mission["id"], task["id"], task["user_id"], "budget",
                                          f'"{task["title"]}" used {which} (task ${task["spent_usd"]:.2f} of ${task["budget_usd"]:.2f}; mission ${mission["spent_usd"]:.2f} of ${mission["budget_usd"]:.2f}). Add ${add:.2f} so it can keep going?',
                                          ["Approve", "Decline"], {"add_usd": add})
        await self.store.update_task(task["id"], status="waiting", transcript=transcript)
        await self.store.add_event(task["user_id"], "budget", f'"{task["title"]}" is waiting for more budget.', mission_id=mission["id"], task_id=task["id"])
        await self._notify(task["user_id"], "Budget needed", q["text"])
        return True

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
        if name == "web_search" and task["role"] in ("researcher", "reviewer"):
            return await self._search(task, args)
        if name == "generate_image" and task["role"] == "designer":
            return await self._image(task, mission, args)
        if name in ("repo_files", "repo_read", "repo_commit", "request_merge") and task["role"] == "builder":
            return await self._repo(task, mission, name, args)
        if name == "request_changes" and task["role"] == "reviewer":
            return await self._request_changes(task, args)
        if name == "web_research" and task["role"] == "researcher":
            return await self._research(task, mission, args)
        if name == "use_connection" and task["role"] == "operator":
            return await self._connection(task, mission, args)
        return f"Error: there is no tool named '{_clip(name, 40)}' for this role.", None

    async def _research(self, task: Dict[str, Any], mission: Dict[str, Any], args: Dict[str, Any]):
        uid, mid, tid = task["user_id"], task["mission_id"], task["id"]
        question = _clip(args.get("question"), 500)
        if len(question) < 8:
            return "Error: ask a full research question.", None
        if not web.configured(self.env):
            return "Web research is not connected (" + web.SETUP + "). Work from your own knowledge and say what needs checking.", None
        fresh = await self.store.get_task(tid) or task
        m = await self.store.get_mission(mid) or mission
        if fresh["budget_usd"] - fresh["spent_usd"] < web.RESEARCH_EST_USD or m["budget_usd"] - m["spent_usd"] < web.RESEARCH_EST_USD \
                or await self._day_room(uid) < web.RESEARCH_EST_USD:
            return f"Not enough budget left for web research (about ${web.RESEARCH_EST_USD:.2f}). Finish with what you have, or ask Michael.", None
        await self.store.add_event(uid, "research", f"Scout is researching the web: {question}", mission_id=mid, task_id=tid)
        try:
            out = await self.research_fn(question, self.env)
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

    async def _repo(self, task: Dict[str, Any], mission: Dict[str, Any], name: str, args: Dict[str, Any]):
        uid, mid, tid = task["user_id"], task["mission_id"], task["id"]
        if not gh.configured(self.env):
            return "GitHub is not connected (" + gh.SETUP + "). Save code with save_output instead and say so in your summary.", None
        hub = gh.GitHub(self.env, self.client_factory)
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
            repo, branch = gh.check_repo(_clip(args.get("repo"), 100), self.env), gh.crew_branch(_clip(args.get("branch"), 80))
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

    async def _connection(self, task: Dict[str, Any], mission: Dict[str, Any], args: Dict[str, Any]):
        uid, mid, tid = task["user_id"], task["mission_id"], task["id"]
        platform, action = _clip(args.get("platform"), 30).lower(), _clip(args.get("action"), 40)
        params = args.get("args") if isinstance(args.get("args"), dict) else {}
        risk = conns.risk_of(platform, action)
        if risk is None:
            return f"Error: {platform}.{action} is not an available action.", None
        st = (await conns.status(self.store, uid, self.env)).get(platform, {})
        if not st.get("connected"):
            return f"Not connected: {st.get('setup') or 'the owner has not connected it'}. Do not retry; note it in your summary.", None
        if risk in ("read", "draft"):
            try:
                result = await conns.run(platform, action, params, store=self.store, user_id=uid, mission_id=mid, env=self.env, client_factory=self.client_factory)
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
            if approve:
                act = q["action"] or {}
                if act.get("platform") == "github":
                    a = act.get("args") or {}
                    try:
                        out = await gh.GitHub(self.env, self.client_factory).merge_branch(a.get("repo", ""), a.get("branch", ""), a.get("title", ""), a.get("body", ""))
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
                                          mission_id=mid, env=self.env, client_factory=self.client_factory)
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
        roster = []
        for key, r in ROLES.items():
            mine = [t for m in out for t in m["tasks"] if t["role"] == key]
            roster.append({"role": key, "name": r["name"], "model": hardening.display_name(self.models.get(r["model"], "")),
                           "can": r["can"], "cannot": r["cannot"],
                           "working": sum(1 for t in mine if t["status"] == "running"), "waiting": sum(1 for t in mine if t["status"] == "waiting"),
                           "done": sum(1 for t in mine if t["status"] == "done")})
        if self.herald is not None:
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
            "spend": {"today_usd": round(spent, 4), "daily_cap_usd": daily_cap_usd(self.env),
                      "default_mission_budget_usd": default_mission_budget(self.env), "max_mission_budget_usd": max_mission_budget(self.env)},
            "limits": {"max_concurrent": max_concurrent(self.env), "max_tasks": max_tasks(self.env)},
            "running": len([t for t in self.running.values() if not t.done()]),
            "blocked": await self.may_run(user_id) if not settings["paused"] else "paused",
            "quiet_now": in_quiet_hours(self.now(), self.env),
        }
