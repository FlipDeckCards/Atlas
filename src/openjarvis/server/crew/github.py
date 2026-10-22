"""Forge's hands on code: GitHub's official REST API, with a branch-first guardrail.

  * Forge can list and read files in the allowed repos (CREW_GITHUB_REPOS) and commit to its own branch, always named crew/<something>.
    It can never write to main (or any branch not starting with crew/), and never touches .github/workflows.
  * Merging into the main branch is a checkpoint: the server opens a pull request and merges it only after Michael taps Approve.
    Repos that auto-deploy from main (Atlas) go live on that merge.

Needs on Render:  CREW_GITHUB_TOKEN (or GITHUB_TOKEN) with contents and pull-request write access to the allowed repos.
Optional:         CREW_GITHUB_REPOS="owner/repo,owner/repo" (defaults to Michael's product repos).
"""

from __future__ import annotations

import base64
import re
from typing import Any, Callable, Dict, List, Mapping, Optional

import httpx

API = "https://api.github.com"
DEFAULT_REPOS = ("FlipDeckCards/Atlas", "FlipDeckCards/FlipDeckCards", "FlipDeckCards/Marnyx", "FlipDeckCards/CyberVoid",
                 "FlipDeckCards/deadzone-backend")
MAX_FILES_PER_COMMIT = 20
MAX_FILE_CHARS = 200_000
MAX_READ_CHARS = 30_000

SETUP = ("Create a GitHub token (fine-grained: Contents and Pull requests, read and write, on the product repos) and set "
         "CREW_GITHUB_TOKEN on Render (Atlas service).")


class GitHubError(Exception):
    """A refusal or failure; the message is safe to show to the agent and the owner."""


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    import os

    return os.environ if env is None else env


def token(env=None) -> str:
    e = _env(env)
    return (e.get("CREW_GITHUB_TOKEN") or e.get("GITHUB_TOKEN") or "").strip()


def configured(env=None) -> bool:
    return bool(token(env))


def repos(env=None) -> List[str]:
    raw = _env(env).get("CREW_GITHUB_REPOS")
    items = [r.strip() for r in str(raw).split(",")] if raw else list(DEFAULT_REPOS)
    return [r for r in items if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r)]


def check_repo(repo: str, env=None) -> str:
    repo = str(repo or "").strip()
    for r in repos(env):
        if r.lower() == repo.lower():
            return r
    raise GitHubError(f"{repo or 'That repo'} is not one the crew may use. Allowed: {', '.join(repos(env))}.")


def crew_branch(name: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(name or "").lower().removeprefix("crew/")).strip("-.")[:50] or "work"
    return "crew/" + slug


def check_path(path: str) -> str:
    p = str(path or "").strip().lstrip("/")
    if not p or ".." in p.split("/") or p.startswith(".git/") or p == ".git" or "\\" in p or "\x00" in p:
        raise GitHubError(f"Not an allowed file path: {path!r}.")
    if p.lower().startswith(".github/workflows"):
        raise GitHubError("The crew does not change GitHub workflow files.")
    return p


ClientFactory = Callable[[], httpx.AsyncClient]


class GitHub:
    def __init__(self, env=None, client_factory: Optional[ClientFactory] = None) -> None:
        self.env = env
        self.cf = client_factory or (lambda: httpx.AsyncClient(timeout=30))

    def _h(self) -> Dict[str, str]:
        t = token(self.env)
        if not t:
            raise GitHubError("GitHub is not connected. " + SETUP)
        return {"Authorization": f"Bearer {t}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

    async def _call(self, c, method: str, path: str, *, ok=(200, 201), **kw) -> Any:
        r = await c.request(method, API + path, headers=self._h(), **kw)
        if r.status_code not in ok:
            msg = ""
            try:
                msg = str((r.json() or {}).get("message") or "")[:200]
            except Exception:
                pass
            if r.status_code in (401, 403):
                raise GitHubError(f"GitHub refused ({r.status_code}{': ' + msg if msg else ''}). The token may lack write access to this repo.")
            raise GitHubError(f"GitHub answered {r.status_code}{': ' + msg if msg else ''}.")
        return r.json() if r.content else {}

    async def default_branch(self, c, repo: str) -> str:
        return (await self._call(c, "GET", f"/repos/{repo}"))["default_branch"]

    async def list_files(self, repo: str, path: str = "", branch: str = "") -> str:
        repo = check_repo(repo, self.env)
        async with self.cf() as c:
            ref = branch or await self.default_branch(c, repo)
            tree = await self._call(c, "GET", f"/repos/{repo}/git/trees/{ref}", params={"recursive": "1"})
        prefix = str(path or "").strip().strip("/")
        names = [t["path"] for t in tree.get("tree") or [] if t.get("type") == "blob" and (not prefix or t["path"].startswith(prefix + "/") or t["path"] == prefix)]
        skip = ("node_modules/", "dist/", ".git/", "__pycache__/")
        names = [n for n in names if not any(s in n for s in skip)]
        more = f"\n(and {len(names) - 400} more)" if len(names) > 400 else ""
        return f"{repo}@{ref}: {len(names)} files\n" + "\n".join(names[:400]) + more

    async def read_file(self, repo: str, path: str, branch: str = "") -> str:
        repo, path = check_repo(repo, self.env), check_path(path)
        async with self.cf() as c:
            params = {"ref": branch} if branch else {}
            body = await self._call(c, "GET", f"/repos/{repo}/contents/{path}", params=params)
        if isinstance(body, list):
            raise GitHubError(f"{path} is a folder; list it instead.")
        text = base64.b64decode(body.get("content") or "").decode("utf-8", errors="replace")
        return text if len(text) <= MAX_READ_CHARS else text[:MAX_READ_CHARS] + f"\n\n[cut: the file has {len(text):,} characters]"

    async def commit_files(self, repo: str, branch: str, files: List[Dict[str, Any]], message: str) -> Dict[str, Any]:
        """Commit files to a crew/ branch (created from the default branch if new). -> {branch, sha, url, files}"""
        repo, branch = check_repo(repo, self.env), crew_branch(branch)
        if not isinstance(files, list) or not files:
            raise GitHubError("Give at least one file: [{path, content}].")
        if len(files) > MAX_FILES_PER_COMMIT:
            raise GitHubError(f"At most {MAX_FILES_PER_COMMIT} files per commit.")
        entries = []
        for f in files:
            if not isinstance(f, dict):
                raise GitHubError("Each file is {path, content}.")
            content = str(f.get("content") if f.get("content") is not None else "")
            if len(content) > MAX_FILE_CHARS:
                raise GitHubError(f"{f.get('path')} is too large ({len(content):,} characters).")
            entries.append({"path": check_path(f.get("path")), "mode": "100644", "type": "blob", "content": content})
        message = " ".join(str(message or "Crew changes").split())[:200] or "Crew changes"
        async with self.cf() as c:
            base = await self.default_branch(c, repo)
            ref = await self._call(c, "GET", f"/repos/{repo}/git/ref/heads/{branch}", ok=(200, 404))
            if not ref.get("object"):
                head = await self._call(c, "GET", f"/repos/{repo}/git/ref/heads/{base}")
                parent = head["object"]["sha"]
                await self._call(c, "POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": parent})
            else:
                parent = ref["object"]["sha"]
            pc = await self._call(c, "GET", f"/repos/{repo}/git/commits/{parent}")
            tree = await self._call(c, "POST", f"/repos/{repo}/git/trees", json={"base_tree": pc["tree"]["sha"], "tree": entries})
            commit = await self._call(c, "POST", f"/repos/{repo}/git/commits",
                                      json={"message": message + "\n\nMade by Aibussol's crew (Forge).", "tree": tree["sha"], "parents": [parent]})
            await self._call(c, "PATCH", f"/repos/{repo}/git/refs/heads/{branch}", json={"sha": commit["sha"]})
        return {"repo": repo, "branch": branch, "base": base, "sha": commit["sha"], "files": [e["path"] for e in entries],
                "url": f"https://github.com/{repo}/compare/{base}...{branch}"}

    async def merge_branch(self, repo: str, branch: str, title: str, body: str = "") -> str:
        """Open (or reuse) a pull request from the crew branch and merge it. Runs only after Michael's Approve."""
        repo, branch = check_repo(repo, self.env), crew_branch(branch)
        owner = repo.split("/")[0]
        async with self.cf() as c:
            base = await self.default_branch(c, repo)
            prs = await self._call(c, "GET", f"/repos/{repo}/pulls", params={"head": f"{owner}:{branch}", "state": "open"})
            if prs:
                pr = prs[0]
            else:
                pr = await self._call(c, "POST", f"/repos/{repo}/pulls",
                                      json={"title": " ".join(str(title or branch).split())[:120], "head": branch, "base": base,
                                            "body": (body or "")[:4000] + "\n\nOpened by Aibussol's crew; merged on Michael's Approve."})
            m = await self._call(c, "PUT", f"/repos/{repo}/pulls/{pr['number']}/merge", json={"merge_method": "squash"}, ok=(200,))
        if not m.get("merged"):
            raise GitHubError(f"GitHub did not merge pull request #{pr['number']}: {m.get('message') or 'unknown reason'}.")
        return f"Merged {branch} into {base} of {repo} (pull request #{pr['number']}, {str(m.get('sha', ''))[:7]})."
