"""The Workbench: Michael's own computer as a crew member ("Rig").

Game builds (Unity, Unreal) and anything else that needs his machine run there, through Claude Code on his own subscription, exactly
the way Claude desktop built Deadzone Run: in his real project folder, with his Unity / Unreal editors.

How it works
  * Rig's tasks are planned like any other, but the server never runs them: they wait, queued, for the PC.
  * The helper (crew/workbench_helper.py, downloaded from /crew/workbench/aibussol_workbench.py) runs on the PC. It says hello every
    minute (so the Station shows the PC online), asks for the next Rig job, runs Claude Code in the mapped project folder, posts progress,
    and reports the result. Nothing on the PC is reachable from outside: the helper only makes outgoing requests.
  * Pairing: the owner taps "Pair your PC" on the Station and gets a token once. Only a hash is stored. The token names its account
    (wb.<user id>.<secret>), so the helper's routes (/crew/workbench/*, outside /api) check it in constant time.
  * Rules the helper gives Claude Code: work only in the mapped folder, commit to a crew/<name> branch, never push to main, never upload
    to a store or publish anything; builds and tests stay on the PC and Michael ships them.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any, Dict, Optional, Tuple

TOKEN_SECRET = "workbench_token_hash"
ONLINE_S = 180                       # a PC that said hello within 3 minutes counts as online
JOB_TIMEOUT_S = 4 * 3600


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def pair(store, user_id: str) -> str:
    """A fresh token for this account (any older one stops working). Shown to the owner once."""
    token = f"wb.{user_id}.{secrets.token_urlsafe(32)}"
    await store.set_secret(user_id, TOKEN_SECRET, _hash(token))
    return token


async def unpair(store, user_id: str) -> None:
    await store.delete_secret(user_id, TOKEN_SECRET)


async def verify(store, token: str) -> Optional[str]:
    """The account a helper token belongs to, or None."""
    token = str(token or "").strip()
    parts = token.split(".", 2)
    if len(parts) != 3 or parts[0] != "wb" or not parts[1] or len(parts[2]) < 20:
        return None
    stored = await store.get_secret(parts[1], TOKEN_SECRET)
    if not stored or not hmac.compare_digest(stored, _hash(token)):
        return None
    return parts[1]


class Presence:
    """Which PCs said hello recently (in memory: a restart just waits for the next hello)."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.seen: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def hello(self, user_id: str, info: Dict[str, Any]) -> None:
        clean = {"host": str(info.get("host") or "")[:60],
                 "projects": [str(p)[:60] for p in (info.get("projects") or [])][:20],
                 "claude": bool(info.get("claude")), "unity": bool(info.get("unity")), "unreal": bool(info.get("unreal")),
                 "busy": bool(info.get("busy")), "version": str(info.get("version") or "")[:20]}
        self.seen[user_id] = (self.clock(), clean)

    def status(self, user_id: str) -> Dict[str, Any]:
        at, info = self.seen.get(user_id, (None, {}))
        online = at is not None and self.clock() - at < ONLINE_S
        return dict(info, online=online, last_seen_s=None if at is None else int(self.clock() - at))


def job_for(task: Dict[str, Any], mission: Dict[str, Any], dep_results: str, rules: str) -> Dict[str, Any]:
    return {"task_id": task["id"], "mission_id": task["mission_id"], "title": task["title"], "brief": task["brief"],
            "goal": mission.get("goal", ""), "project": mission.get("project", "") or "", "mission_title": mission.get("title", ""),
            "results_so_far": dep_results, "house_rules": rules, "timeout_s": JOB_TIMEOUT_S}
