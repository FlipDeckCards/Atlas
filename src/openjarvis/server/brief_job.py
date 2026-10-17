"""The brief, made INSIDE the Aibussol service from live data (run by a Render cron job; see docs/brief-cron.md).

    python -m openjarvis.server.brief_job            # run what is due now (a no-op outside the three slots)
    python -m openjarvis.server.brief_job --period evening --dry-run     # make one and print it, post nothing

Rules, each one tested in tests/server/test_brief_job.py:
  * The greeting comes from the CURRENT time in America/Los_Angeles (brief_content.greeting_line), not from the slot name.
  * Every number comes from a source read in THIS run. A source that cannot be read (no key, refused, timed out, bad answer) is written as
    "unchecked (reason)": no cached value, no earlier brief's number, no guess. Nothing is stored between runs except the finished brief.
  * Each section appears once. Meta Ads is not a source and is removed from anything that mentions it (brief_content).
  * Scheduling is decided here, not by the cron's time zone: Render cron runs in UTC, so the job runs every hour at :52 and does something only when
    the Los Angeles clock is inside a slot (06:52, 12:52, 21:52) and that period has no brief yet today. That holds across daylight saving changes.
Credentials come from environment variables only (names in docs/brief-cron.md); nothing is logged or written to the brief.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

from openjarvis.server import brief_content
from openjarvis.server.brief_content import LA, SLOTS

logger = logging.getLogger(__name__)

DUE_WINDOW = dt.timedelta(minutes=45)      # a cron that starts up to 45 minutes after the slot still makes that slot's brief
HTTP_TIMEOUT = 15.0
CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "brief_products.json"


# ── scheduling ──────────────────────────────────────────────────────────────

def slot_start(period: str, day: dt.date) -> dt.datetime:
    h, m = SLOTS[period]
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=LA)


def due_period(now: Optional[dt.datetime] = None) -> Optional[str]:
    """The period whose slot began at most 45 minutes ago in Los Angeles time, or None (the hourly job then does nothing)."""
    n = brief_content.la_time(now)
    for period in SLOTS:
        start = slot_start(period, n.date())
        if start <= n < start + DUE_WINDOW:
            return period
    return None


def window_start(period: str, now: Optional[dt.datetime] = None) -> dt.datetime:
    """The beginning of what this brief covers: the previous slot."""
    n = brief_content.la_time(now)
    order = list(SLOTS)
    prev = order[order.index(period) - 1]
    day = n.date() - dt.timedelta(days=1) if period == order[0] else n.date()
    return slot_start(prev, day)


async def already_made(store, period: str, now: Optional[dt.datetime] = None) -> bool:
    """True when a brief for this period has already been stored today (Los Angeles date), so a repeat run posts nothing."""
    today = brief_content.la_time(now).date()
    rows = await store.latest_per_period()
    for r in rows:
        if r["period"] == period:
            created = dt.datetime.fromisoformat(str(r["created_at"]))
            if brief_content.la_time(created).date() == today:
                return True
    return False


# ── sources ─────────────────────────────────────────────────────────────────

class SourceError(Exception):
    """A source could not be read. The message is safe to print in the brief (no keys, no URLs with secrets)."""


@dataclass
class SourceResult:
    name: str
    lines: List[str] = field(default_factory=list)
    unchecked: Optional[str] = None            # the reason, when the source could not be read

    @property
    def ok(self) -> bool:
        return self.unchecked is None


async def read_source(name: str, reader: Callable[[], Awaitable[List[str]]]) -> SourceResult:
    """Runs one reader. Any failure becomes 'unchecked'. Nothing is cached: every run calls the reader again."""
    try:
        return SourceResult(name, lines=await reader())
    except SourceError as exc:
        return SourceResult(name, unchecked=str(exc))
    except httpx.TimeoutException:
        return SourceResult(name, unchecked="timed out")
    except httpx.HTTPError:
        return SourceResult(name, unchecked="could not connect")
    except Exception:
        logger.exception("source %s failed", name)
        return SourceResult(name, unchecked="unexpected error")


def _need(env: Dict[str, str], key: str) -> str:
    v = (env.get(key) or "").strip()
    if not v:
        raise SourceError("not configured")
    return v


async def _get_json(client: httpx.AsyncClient, url: str, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None) -> Any:
    r = await client.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
    if r.status_code in (401, 403):
        raise SourceError("access refused")
    if r.status_code == 429:
        raise SourceError("rate limited")
    if r.status_code >= 400:
        raise SourceError(f"HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise SourceError("unreadable answer") from None


def _pacific(iso: str) -> str:
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return "an unknown time"
    return brief_content.la_time(t).strftime("%I:%M %p Pacific on %B %d").replace(" 0", " ")


def _first_line(text: Any, limit: int = 110) -> str:
    s = str(text or "").strip().split("\n", 1)[0].strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


async def render_deploys(client, env, services, since: dt.datetime, who: str = "") -> List[str]:
    key = _need(env, "RENDER_API_KEY")
    lines: List[str] = []
    for svc in services:
        data = await _get_json(client, f"https://api.render.com/v1/services/{svc['id']}/deploys", {"Authorization": "Bearer " + key}, {"limit": 20})
        for item in data if isinstance(data, list) else []:
            d = item.get("deploy", item) if isinstance(item, dict) else {}
            created = d.get("createdAt")
            if not created or dt.datetime.fromisoformat(created.replace("Z", "+00:00")) < since:
                continue
            msg = _first_line((d.get("commit") or {}).get("message"))
            lines.append(f"- Render deploy of {svc['name']}: {d.get('status', 'unknown status')}, started {_pacific(created)}" + (f" ({msg})" if msg else "") + ".")
    return lines or [f"- No deploys for {who} in this window."]


async def render_errors(client, env, owner_id, services, since: dt.datetime, who: str = "") -> List[str]:
    key = _need(env, "RENDER_API_KEY")
    params: Dict[str, Any] = {"ownerId": owner_id, "level": "error", "limit": 50, "startTime": since.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    params["resource"] = [s["id"] for s in services]
    data = await _get_json(client, "https://api.render.com/v1/logs", {"Authorization": "Bearer " + key}, params)
    logs = data.get("logs", []) if isinstance(data, dict) else []
    counts: Dict[str, int] = {}
    for entry in logs:
        msg = _first_line(entry.get("message"), 160) if isinstance(entry, dict) else ""
        if msg and not brief_content._is_meta_line(msg):             # Meta Ads has stopped: its token error is not news
            counts[msg] = counts.get(msg, 0) + 1
    out = [f"- {msg.rstrip('. ')}" + (f" (seen {n} times)." if n > 1 else ".") for msg, n in counts.items()]
    return out or [f"- No error-level log lines for {who} in this window."]


async def github_commits(client, env, repos, since: dt.datetime, who: str = "") -> List[str]:
    token = _need(env, "GITHUB_TOKEN")
    out: List[str] = []
    for repo in repos:
        data = await _get_json(client, f"https://api.github.com/repos/{repo}/commits", {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"},
                               {"since": since.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": 30})
        for c in data if isinstance(data, list) else []:
            out.append(f"- {repo.split('/')[-1]}: {str(c.get('sha', ''))[:7]} {_first_line((c.get('commit') or {}).get('message'))}.")
    return out or [f"- No commits for {who} in this window."]


async def stripe_activity(client, env, since: dt.datetime) -> List[str]:
    key = _need(env, "STRIPE_READ_KEY")
    headers = {"Authorization": "Bearer " + key}
    ts = int(since.timestamp())
    charges = await _get_json(client, "https://api.stripe.com/v1/charges", headers, {"created[gte]": ts, "limit": 100})
    subs = await _get_json(client, "https://api.stripe.com/v1/subscriptions", headers, {"created[gte]": ts, "status": "all", "limit": 100})
    ok = [c for c in charges.get("data", []) if c.get("status") == "succeeded"]
    bad = [c for c in charges.get("data", []) if c.get("status") == "failed"]
    total = sum(int(c.get("amount") or 0) for c in ok) / 100
    return [f"- Stripe: {len(ok)} successful charges totalling ${total:,.2f}, {len(bad)} failed payments, {len(subs.get('data', []))} new subscriptions in this window."]


# ── making the brief ───────────────────────────────────────────────────────────────

def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _section(title: str, results: List[SourceResult]) -> List[str]:
    out = [f"### {title}"]
    for r in results:
        out += r.lines if r.ok else [brief_content.unchecked_line(r.name, r.unchecked or "could not be read")]
    return out


async def build_product(client, env, cfg: Dict[str, Any], product: Dict[str, Any], since: dt.datetime) -> Dict[str, Any]:
    services, owner, who = product.get("render_services", []), cfg.get("render_owner_id", ""), product["name"]
    deploys = await read_source(f"Render deploys for {who}", lambda: render_deploys(client, env, services, since, who))
    commits = await read_source(f"GitHub commits for {who}", lambda: github_commits(client, env, product.get("github_repos", []), since, who))
    stripe = await read_source(f"Stripe for {who}", lambda: stripe_activity(client, env, since)) if product.get("stripe") else None
    errors = await read_source(f"Render error logs for {who}", lambda: render_errors(client, env, owner, services, since, who))
    # sources a server cannot read are said to be unchecked, never filled in
    gmail = SourceResult(f"Gmail replies from vendors and platforms for {who}", unchecked="not connected to the Aibussol service")
    notes = SourceResult(f"Open approvals and to-do notes for {who}", unchecked="not connected to the Aibussol service")
    numbers = SourceResult(f"User and visitor numbers for {who}", unchecked="no read-only database connection is set up for this brief")
    lines = [f"## {product['name']}"]
    lines += _section("Completed", [deploys, commits] + ([stripe] if stripe else []))
    lines += _section("Numbers", [numbers])
    lines += _section("Pending", [gmail, notes])
    lines += _section("Errors", [errors])
    return {"lines": lines, "results": [deploys, commits, errors, gmail, notes, numbers] + ([stripe] if stripe else [])}


def act_on_first(results: List[SourceResult]) -> str:
    errors = [r for r in results if r.name.startswith("Render error logs") and r.ok and not r.lines[0].startswith("- No error")]
    failed = [r for r in results if r.name.startswith("Render deploys") and r.ok and any("failed" in l.lower() for l in r.lines)]
    if failed:
        return "A deploy failed in this window. Look at the failed deploy first."
    if errors:
        return "There are error log lines listed above. Read those first."
    n = sum(1 for r in results if not r.ok)
    return "Nothing urgent in the sources that were checked." + (f" {n} sources were unchecked, so check those by hand." if n else "")


async def make_brief(period: str, now: Optional[dt.datetime] = None, env: Optional[Dict[str, str]] = None, cfg: Optional[Dict[str, Any]] = None,
                     client: Optional[httpx.AsyncClient] = None) -> Dict[str, str]:
    """-> {"product", "period", "body_markdown", "spoken_summary"}. The greeting is chosen from the time, not from `period`."""
    n = now or dt.datetime.now(dt.timezone.utc)
    env = dict(os.environ) if env is None else env
    cfg = cfg or load_config()
    since = window_start(period, n)
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        built = [await build_product(client, env, cfg, p, since) for p in cfg["products"]]
    finally:
        if own:
            await client.aclose()
    results = [r for b in built for r in b["results"]]
    lines: List[str] = []
    for b in built:
        lines += b["lines"] + [""]
    lines += ["## Act on first", act_on_first(results)]
    # the greeting goes on AFTER the clean-up (which removes any greeting at the top)
    body = brief_content.greeting_line(n) + "\n\n" + brief_content.sanitize_brief_body("\n".join(lines))
    unchecked = sum(1 for r in results if not r.ok)
    spoken_rest = brief_content.sanitize_spoken(act_on_first(results) + ("" if unchecked else " Every source was checked."))
    spoken = f"{brief_content.greeting_line(n)} {spoken_rest}"                      # the greeting is added after the clean-up, which would remove it
    return {"product": " and ".join(p["name"] for p in cfg["products"]), "period": period, "body_markdown": body[:20_000], "spoken_summary": spoken[:600]}


async def run(store, now: Optional[dt.datetime] = None, period: Optional[str] = None, env: Optional[Dict[str, str]] = None, cfg: Optional[Dict[str, Any]] = None,
              client: Optional[httpx.AsyncClient] = None, dry_run: bool = False) -> Dict[str, Any]:
    """One job run. -> {"action": "skipped" | "dry_run" | "posted", ...}."""
    n = now or dt.datetime.now(dt.timezone.utc)
    p = period or due_period(n)
    if p is None:
        return {"action": "skipped", "reason": "not a brief time"}
    if period is None and await already_made(store, p, n):
        return {"action": "skipped", "reason": f"the {p} brief was already made today"}
    brief = await make_brief(p, n, env, cfg, client)
    if dry_run:
        return {"action": "dry_run", "brief": brief}
    saved = await store.insert(brief["product"], brief["period"], brief["body_markdown"], brief["spoken_summary"])
    return {"action": "posted", "period": p, "id": saved["id"]}


async def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Make and store the Aibussol brief that is due now.")
    ap.add_argument("--period", choices=list(SLOTS), help="make this period's brief now, whatever the time")
    ap.add_argument("--dry-run", action="store_true", help="print the brief, store nothing")
    args = ap.parse_args(argv)
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn and not args.dry_run:
        print("DATABASE_URL is not set")
        return 2
    import asyncpg

    from openjarvis.server.briefs import BRIEFS_DDL, BriefStore, MemoryBriefStore

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2) if dsn else None
    try:
        if pool:
            async with pool.acquire() as conn:
                await conn.execute(BRIEFS_DDL)
        store = BriefStore(pool) if pool else MemoryBriefStore()
        out = await run(store, period=args.period, dry_run=args.dry_run)
        if out["action"] == "dry_run":
            print(out["brief"]["body_markdown"])
        else:
            print(json.dumps({k: v for k, v in out.items() if k != "brief"}))
        return 0
    finally:
        if pool:
            await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
