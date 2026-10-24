"""Sentinel: Helix watches the owner's Render deploys and does not wait to be asked.

Every few minutes the crew engine's loop calls maybe_run(). When the newest deploy of a service has FAILED (and Sentinel has not looked at
that deploy before), Helix investigates with her read-only tools (deploy list, logs, the code) and tells the owner: in the chat, as a phone
push, and as a card in the Helix inbox. The card carries her proposal, and nothing happens until the owner taps Approve:
  FIX       Approve starts a Forge mission (a crew/ branch, checked by GitHub's tests; merging asks for a second Approve).
  REDEPLOY  the failure looks temporary: Approve redeploys the service.
  NONE      she only reports what she found.

Limits: CREW_SENTINEL=off turns it off; CREW_SENTINEL_MINUTES (default 10) is how often it looks; at most CREW_SENTINEL_PER_DAY (default 3)
investigations a day (each costs a few cents of model time). Only the owner's account is watched.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from openjarvis.server.crew import devtools

logger = logging.getLogger(__name__)

FAILED = {"build_failed", "update_failed", "pre_deploy_failed"}          # Render deploy statuses that mean the deploy did not go live
READ_ONLY = {"github_files", "github_read", "github_search", "github_activity", "github_failed_runs", "render_services", "render_deploys", "render_logs"}
SEEN_KEY, DAY_KEY = "sentinel_seen", "sentinel_day"
ROUNDS = 8

PROMPT = ("You are Helix, the lead of Michael's crew, investigating a failed deploy of his software on Render. You have read-only tools for his "
          "GitHub and Render. Work like a careful engineer: read the error in the deploy logs, find the file and line it comes from in the code, "
          "check the latest commit, and be sure of the cause before you say it. Do not guess. When you know, answer in exactly this format and nothing else:\n"
          "CAUSE: <the root cause in plain words, naming the file and what is wrong>\n"
          "NEXT: FIX or REDEPLOY or NONE   (FIX = a code change is needed; REDEPLOY = the failure looks temporary, for example a network or registry hiccup; "
          "NONE = it needs Michael, for example a missing key)\n"
          "GOAL: <only for FIX: the full instruction for Forge, naming the repo (owner/name), the exact cause and the change wanted>")


def enabled(env=None) -> bool:
    import os

    return str((os.environ if env is None else env).get("CREW_SENTINEL") or "on").strip().lower() not in ("off", "0", "false", "no")


def _num(env, name: str, default: int, lo: int) -> int:
    import os

    try:
        return max(lo, int((os.environ if env is None else env).get(name) or default))
    except (TypeError, ValueError):
        return default


def _repo_name(url: Any) -> str:
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", str(url or ""))
    return m.group(1) if m else ""


def parse_findings(text: str) -> Dict[str, str]:
    t = str(text or "")
    cause = re.search(r"CAUSE:\s*(.+?)(?:\n\s*NEXT:|\Z)", t, re.S | re.I)
    nxt = re.search(r"NEXT:\s*(FIX|REDEPLOY|NONE)", t, re.I)
    goal = re.search(r"GOAL:\s*(.+)\Z", t, re.S | re.I)
    return {"cause": " ".join((cause.group(1) if cause else t).split())[:500], "next": (nxt.group(1).upper() if nxt else "NONE"),
            "goal": " ".join((goal.group(1) if goal else "").split())[:3000]}


class Sentinel:
    def __init__(self, engine, owner_id_fn=None, env=None, clock=time.monotonic) -> None:
        self.engine = engine
        self.owner_id_fn = owner_id_fn
        self.env = env
        self.clock = clock
        self._last = 0.0

    async def maybe_run(self) -> List[Dict[str, Any]]:
        env = self.env if self.env is not None else self.engine.env
        if not enabled(env) or self.owner_id_fn is None:
            return []
        gap = _num(env, "CREW_SENTINEL_MINUTES", 10, 2) * 60
        if self._last and self.clock() - self._last < gap:
            return []
        self._last = self.clock()
        try:
            uid = await self.owner_id_fn()
            return await self.check(uid) if uid else []
        except Exception:
            logger.exception("sentinel check failed")
            return []

    async def _seen(self, uid: str) -> List[str]:
        try:
            return list(json.loads(await self.engine.store.get_secret(uid, SEEN_KEY) or "[]"))
        except Exception:
            return []

    async def check(self, uid: str) -> List[Dict[str, Any]]:
        eng = self.engine
        envx = await eng.env_for(uid)
        if not str((envx or {}).get("RENDER_API_KEY") or "").strip():
            return []
        rd = devtools.Render(envx, eng.client_factory)
        out = []
        for svc in await rd.services():
            deploys = await rd.deploys_raw(svc["id"], 1)
            if not deploys or deploys[0].get("status") not in FAILED:
                continue
            d = deploys[0]
            seen = await self._seen(uid)
            if d.get("id") in seen:
                continue
            await eng.store.set_secret(uid, SEEN_KEY, json.dumps((seen + [d.get("id")])[-60:]))       # marked first: a failure here never loops
            if await self._today(uid) >= _num(self.env if self.env is not None else eng.env, "CREW_SENTINEL_PER_DAY", 3, 1):
                continue
            out.append(await self.investigate(uid, svc, d))
        return out

    async def _today(self, uid: str, bump: bool = False) -> int:
        eng = self.engine
        day = eng._day()
        try:
            d, n = (await eng.store.get_secret(uid, DAY_KEY) or "").split(":")
            n = int(n) if d == day else 0
        except ValueError:
            n = 0
        if bump:
            await eng.store.set_secret(uid, DAY_KEY, f"{day}:{n + 1}")
        return n

    async def investigate(self, uid: str, svc: Dict[str, Any], deploy: Dict[str, Any]) -> Dict[str, Any]:
        from openjarvis.server.crew import helix

        eng = self.engine
        await self._today(uid, bump=True)
        ctx = helix.CrewCtx(uid, eng)
        tools = [t for t in helix.CREW_TOOLS if t["function"]["name"] in READ_ONLY]
        commit = deploy.get("commit") or {}
        repo = _repo_name(svc.get("repo"))
        msgs: List[Dict[str, Any]] = [{"role": "system", "content": PROMPT},
                                      {"role": "user", "content": f'Render service "{svc["name"]}" (id {svc["id"]}) deploy {str(deploy.get("id"))} ended {deploy.get("status")}. '
                                                                  f'Commit {str(commit.get("id", ""))[:7]}: "{" ".join(str(commit.get("message") or "").split())[:160]}". '
                                                                  f'Repo: {repo or "unknown"} (branch {svc.get("branch")}). Investigate and answer in the format.'}]
        text = ""
        try:
            for i in range(ROUNDS + 1):
                resp = await eng.acompletion(model=eng.model_for("helix"), messages=msgs, tools=tools if i < ROUNDS else None, max_tokens=1500)
                await eng.store.add_day_spend(uid, eng._day(), float(eng.cost_fn(resp)))
                msg = resp.choices[0].message
                calls = getattr(msg, "tool_calls", None) or []
                if not calls:
                    text = getattr(msg, "content", None) or ""
                    break
                from openjarvis.server.crew.engine import _msg_dict

                msgs.append(_msg_dict(msg))
                for tc in calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except ValueError:
                        args = {}
                    res = await helix._dev(ctx, tc.function.name, args) if tc.function.name in READ_ONLY else "Error: not available."
                    msgs.append({"role": "tool", "tool_call_id": tc.id, "content": str(res)[:12000]})
        except Exception as exc:
            logger.exception("sentinel investigation failed")
            from openjarvis.server.crew.engine import explain_error

            text = f"CAUSE: I could not finish investigating: {explain_error(exc)}\nNEXT: NONE"
        f = parse_findings(text)
        name = svc["name"]
        await eng.store.add_event(uid, "error", f"Helix investigated the failed deploy of {name}: {f['cause']}")
        await eng.remember(uid, f"{name} deploy {str(deploy.get('id'))[:12]} failed: {f['cause']}")
        action: Optional[Dict[str, Any]] = None
        ask = ""
        if f["next"] == "FIX" and len(f["goal"]) >= 20:
            action = {"type": "start_mission", "goal": f["goal"], "budget_usd": eng.clamp_budget(None)}
            ask = "Fix it? Approve starts a Forge mission; its change is tested by GitHub and merging asks you again."
        elif f["next"] == "REDEPLOY":
            action = {"type": "render_redeploy", "service_id": svc["id"], "clear_cache": False}
            ask = f"It looks temporary. Redeploy {name}?"
        body = f"The {name} deploy failed. {f['cause']}"
        if action:
            await eng.store.add_question("", "", uid, "ops", f"{body} {ask}", ["Approve", "Decline"], action)
        await eng._notify(uid, f"Deploy failed: {name}", body + (f"\n{ask} Approve or decline it in the Helix inbox." if action else "\nNothing for you to approve; it needs you."))
        return {"service": name, "deploy": deploy.get("id"), "cause": f["cause"], "next": f["next"], "asked": bool(action)}
