"""Helix's eyes on the code and the servers: read-only GitHub and Render, so she can find out what broke and why.

Nothing here changes anything. Fixing goes through the crew (Forge commits to a crew/ branch and a merge waits for Michael's Approve).

  GitHub: list and read files (also workflow files, never secrets files), search code, recent commits, pull requests, CI results and failed
          workflow runs. Only the repos the crew may use (CREW_GITHUB_REPOS), with the token from the Connections panel.
  Render: the services, their deploys, and their logs (secrets are masked before anyone reads a line).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Callable, Dict, List, Optional

import httpx

from openjarvis.server.crew import github as gh

RENDER_API = "https://api.render.com/v1"
MAX_LOG_LINES = 80
SECRET_FILE = re.compile(r"(^|/)(\.env[^/]*|.*\.(pem|key|p12|pfx|p8)|id_rsa[^/]*|.*secrets?[^/]*\.(json|ya?ml|txt))$", re.I)
SECRET_TEXT = [
    (re.compile(r"\b(sk|rk|pk)_(live|test)_[A-Za-z0-9]{8,}"), r"\1_\2_***"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"), "sk-ant-***"),
    (re.compile(r"\b(ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{10,}"), r"\1_***"),
    (re.compile(r"\brnd_[A-Za-z0-9]{10,}"), "rnd_***"),
    (re.compile(r"(?i)\b(bearer|authorization:?)\s+[A-Za-z0-9._\-]{12,}"), r"\1 ***"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd)(=|:\s*)[^\s&\"',]{6,}"), r"\1\2***"),
    (re.compile(r"(postgres(?:ql)?|mysql|redis)://[^\s:@]+:[^\s@]+@"), r"\1://***:***@"),
]


def redact(text: Any) -> str:
    """Mask keys, tokens and passwords before a log line or a file is shown to anyone (model included)."""
    s = str(text if text is not None else "")
    for pat, rep in SECRET_TEXT:
        s = pat.sub(rep, s)
    return s


def _clip(v: Any, n: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:n]


class DevError(Exception):
    """A refusal or failure with a message safe to show."""


class Reader(gh.GitHub):
    """The crew's GitHub helper, with read-only extras for investigating."""

    def _read_path(self, path: str) -> str:
        p = str(path or "").strip().lstrip("/")
        if not p or ".." in p.split("/") or p.startswith(".git/") or "\\" in p or "\x00" in p:
            raise gh.GitHubError(f"Not an allowed file path: {path!r}.")
        if SECRET_FILE.search(p):
            raise gh.GitHubError("That looks like a secrets file; it is not read.")
        return p

    async def read(self, repo: str, path: str, branch: str = "", start: int = 0) -> str:
        repo, path = gh.check_repo(repo, self.env), self._read_path(path)
        import base64

        async with self.cf() as c:
            body = await self._call(c, "GET", f"/repos/{repo}/contents/{path}", params={"ref": branch} if branch else {})
        if isinstance(body, list):
            raise gh.GitHubError(f"{path} is a folder; list it instead.")
        text = base64.b64decode(body.get("content") or "").decode("utf-8", errors="replace")
        start = max(0, int(start or 0))
        part = redact(text[start:start + gh.MAX_READ_CHARS])
        return part + (f"\n\n[cut: {len(text):,} characters in all; call again with start={start + gh.MAX_READ_CHARS} for the rest]" if len(text) > start + gh.MAX_READ_CHARS else "")

    async def search(self, repo: str, query: str) -> str:
        repo = gh.check_repo(repo, self.env)
        q = _clip(query, 120)
        if len(q) < 2:
            raise gh.GitHubError("Give something to search for.")
        async with self.cf() as c:
            body = await self._call(c, "GET", "/search/code", params={"q": f"{q} repo:{repo}", "per_page": 12})
        items = body.get("items") or []
        return (f"{len(items)} file(s) in {repo} mention \"{q}\": " + ", ".join(i.get("path", "?") for i in items)) if items else f'No file in {repo} mentions "{q}" (GitHub search can lag a few minutes behind new commits).'

    async def commits(self, repo: str, branch: str = "", limit: int = 10) -> str:
        repo = gh.check_repo(repo, self.env)
        async with self.cf() as c:
            rows = await self._call(c, "GET", f"/repos/{repo}/commits", params={"per_page": max(1, min(int(limit or 10), 25)), **({"sha": branch} if branch else {})})
        return "\n".join(f'{str(r.get("sha", ""))[:7]} {(r.get("commit") or {}).get("author", {}).get("date", "")[:16]} {_clip((r.get("commit") or {}).get("message"), 100)}' for r in rows) or "No commits."

    async def pulls(self, repo: str, state: str = "open") -> str:
        repo = gh.check_repo(repo, self.env)
        async with self.cf() as c:
            rows = await self._call(c, "GET", f"/repos/{repo}/pulls", params={"state": state if state in ("open", "closed", "all") else "open", "per_page": 10})
        return "\n".join(f'#{r["number"]} [{r.get("state")}{", merged" if r.get("merged_at") else ""}] {_clip(r.get("title"), 90)} ({(r.get("head") or {}).get("ref")})' for r in rows) or "No pull requests."

    async def ci(self, repo: str, ref: str = "") -> str:
        """Check runs on a commit (the default branch's latest unless a ref is given): which passed, which failed and what they said."""
        repo = gh.check_repo(repo, self.env)
        async with self.cf() as c:
            ref = ref or await self.default_branch(c, repo)
            body = await self._call(c, "GET", f"/repos/{repo}/commits/{ref}/check-runs", params={"per_page": 30})
        runs = body.get("check_runs") or []
        if not runs:
            return f"No CI checks reported on {ref} of {repo}."
        out = []
        for r in runs:
            out.append(f'{r.get("name")}: {r.get("conclusion") or r.get("status")}' +
                       (f' - {_clip((r.get("output") or {}).get("title"), 80)}: {_clip((r.get("output") or {}).get("summary"), 300)}' if r.get("conclusion") == "failure" else ""))
        return redact("\n".join(out))

    async def failed_runs(self, repo: str, limit: int = 3) -> str:
        """Recent failed GitHub Actions runs and the steps that failed in them."""
        repo = gh.check_repo(repo, self.env)
        async with self.cf() as c:
            body = await self._call(c, "GET", f"/repos/{repo}/actions/runs", params={"status": "failure", "per_page": max(1, min(int(limit or 3), 6))})
            lines = []
            for run in (body.get("workflow_runs") or [])[:limit]:
                jobs = await self._call(c, "GET", f"/repos/{repo}/actions/runs/{run['id']}/jobs")
                bad = [f'{j.get("name")} > ' + ", ".join(s.get("name", "?") for s in j.get("steps") or [] if s.get("conclusion") == "failure") for j in jobs.get("jobs") or [] if j.get("conclusion") == "failure"]
                lines.append(f'Run {run["id"]} "{_clip(run.get("display_title") or run.get("name"), 80)}" on {run.get("head_branch")} ({str(run.get("head_sha", ""))[:7]}), {str(run.get("created_at", ""))[:16]}: failed in ' + ("; ".join(bad) or "an unknown step"))
        return "\n".join(lines) or f"No failed workflow runs in {repo}."


class Render:
    """Render's API, read-only. The key is the owner's own (RENDER_API_KEY), from the Connections panel."""

    def __init__(self, env=None, client_factory: Optional[Callable[[], httpx.AsyncClient]] = None) -> None:
        import os

        self.env = os.environ if env is None else env
        self.cf = client_factory or (lambda: httpx.AsyncClient(timeout=30))

    def _h(self) -> Dict[str, str]:
        key = (self.env.get("RENDER_API_KEY") or "").strip()
        if not key:
            raise DevError("Render is not connected. Paste your Render API key in the Connections panel on the Station.")
        return {"Authorization": "Bearer " + key, "Accept": "application/json"}

    async def _get(self, c, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        r = await c.get(RENDER_API + path, headers=self._h(), params=params or {})
        if r.status_code in (401, 403):
            raise DevError("Render refused the API key. Create a new one (Account Settings, API Keys) and paste it in the Connections panel.")
        if r.status_code != 200:
            raise DevError(f"Render answered {r.status_code}.")
        return r.json()

    async def services(self) -> List[Dict[str, Any]]:
        async with self.cf() as c:
            rows = await self._get(c, "/services", {"limit": 30})
        out = []
        for row in rows if isinstance(rows, list) else []:
            s = row.get("service", row)
            d = s.get("serviceDetails") or {}
            out.append({"id": s.get("id"), "name": s.get("name"), "type": s.get("type"), "owner": s.get("ownerId"), "repo": s.get("repo"), "branch": s.get("branch"),
                        "auto": s.get("autoDeploy"), "suspended": s.get("suspended"), "url": d.get("url")})
        return out

    async def find(self, name_or_id: str) -> Dict[str, Any]:
        svcs = await self.services()
        want = _clip(name_or_id, 80).lower()
        hit = [s for s in svcs if s["id"] == name_or_id or (want and (s["name"] or "").lower() == want)] or [s for s in svcs if want and want in (s["name"] or "").lower()]
        if not hit and len(svcs) == 1 and not want:
            hit = svcs
        if not hit:
            raise DevError("No Render service by that name. Services: " + ", ".join(f'{s["name"]} ({s["id"]})' for s in svcs[:12]))
        if len(hit) > 1:
            raise DevError("Several services match: " + ", ".join(f'{s["name"]} ({s["id"]})' for s in hit[:8]))
        return hit[0]

    async def services_text(self) -> str:
        return "\n".join(f'{s["name"]} ({s["id"]}) {s["type"]}{" SUSPENDED" if s["suspended"] not in (None, "not_suspended") else ""}, repo {s["repo"]} branch {s["branch"]}, '
                         f'auto-deploy {s["auto"]}{", " + s["url"] if s["url"] else ""}' for s in await self.services()) or "No Render services."

    async def deploys(self, service: str, limit: int = 5) -> str:
        svc = await self.find(service)
        async with self.cf() as c:
            rows = await self._get(c, f"/services/{svc['id']}/deploys", {"limit": max(1, min(int(limit or 5), 15))})
        out = []
        for row in rows if isinstance(rows, list) else []:
            d = row.get("deploy", row)
            out.append(f'{d.get("status")}: {_clip((d.get("commit") or {}).get("message"), 90)} ({str((d.get("commit") or {}).get("id", ""))[:7]}), '
                       f'started {str(d.get("createdAt", ""))[:16]}' + (f', finished {str(d.get("finishedAt"))[:16]}' if d.get("finishedAt") else ""))
        return f'{svc["name"]} deploys, newest first:\n' + ("\n".join(out) or "none")

    async def logs(self, service: str, text: str = "", level: str = "", minutes: int = 60, limit: int = 60) -> str:
        """Recent log lines, oldest first, so a traceback reads in order. Optional search text and level (error, warning, info)."""
        svc = await self.find(service)
        start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=max(1, min(int(minutes or 60), 1440)))).strftime("%Y-%m-%dT%H:%M:%SZ")
        params: Dict[str, Any] = {"ownerId": svc["owner"], "resource": [svc["id"]], "startTime": start, "direction": "backward",
                                  "limit": max(5, min(int(limit or 60), MAX_LOG_LINES))}
        if text:
            params["text"] = [_clip(text, 80)]
        if level in ("error", "warning", "info", "debug"):
            params["level"] = [level]
        async with self.cf() as c:
            body = await self._get(c, "/logs", params)
        lines = [f'{str(e.get("timestamp", ""))[11:19]} {redact(_clip(e.get("message"), 300))}' for e in reversed((body or {}).get("logs") or []) if str(e.get("message") or "").strip()]
        return (f'{svc["name"]} logs, last {minutes} min' + (f' matching "{text}"' if text else "") + (f", level {level}" if level else "") + ":\n" + "\n".join(lines)) if lines else \
            f'No matching log lines for {svc["name"]} in the last {minutes} minutes.'
