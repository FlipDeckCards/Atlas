"""Live web research for Scout, through GPT Researcher (github.com/assafelovic/gpt-researcher, Apache-2.0).

GPT Researcher searches the web (Tavily), reads the pages and writes a sourced report. It is imported only when a research run
starts, so Aibussol keeps working if the package is missing; the tool then says so instead of failing.

Needs on Render:  TAVILY_API_KEY (free tier at tavily.com) and OPENAI_API_KEY (already set for Aibussol).
Optional:         GPTR_FAST_LLM / GPTR_SMART_LLM / GPTR_STRATEGIC_LLM to pick its models (GPT Researcher's own defaults otherwise).
Limits:           one run at most RESEARCH_TIMEOUT_S; its cost is charged to the task, the mission and the crew's day like any
                  model call (GPT Researcher's own cost figure when it reports one, otherwise RESEARCH_EST_USD).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

RESEARCH_EST_USD = 0.25          # assumed before a run; deliberately on the high side
RESEARCH_TIMEOUT_S = 300
MAX_REPORT_TO_AGENT = 6000


class ResearchUnavailable(Exception):
    """Research cannot run here (not configured or not installed); the message is safe to show."""


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def configured(env=None) -> bool:
    e = _env(env)
    return bool((e.get("TAVILY_API_KEY") or "").strip() and (e.get("OPENAI_API_KEY") or "").strip())


SETUP = ("Get a free API key at tavily.com and set TAVILY_API_KEY on Render (Atlas service). Aibussol's OPENAI_API_KEY is used for the "
         "research models.")


async def gpt_researcher_run(question: str, env=None) -> Dict[str, Any]:
    """-> {'report': markdown, 'sources': [urls], 'cost_usd': float or None}. Raises ResearchUnavailable or asyncio.TimeoutError."""
    if not configured(env):
        raise ResearchUnavailable("Web research is not connected. " + SETUP)
    try:
        from gpt_researcher import (
            GPTResearcher,  # noqa: PLC0415 (imported only when used)
        )
    except Exception:
        raise ResearchUnavailable("GPT Researcher is not installed on the server.")
    e = _env(env)
    for ours, theirs in (("GPTR_FAST_LLM", "FAST_LLM"), ("GPTR_SMART_LLM", "SMART_LLM"), ("GPTR_STRATEGIC_LLM", "STRATEGIC_LLM")):
        if (e.get(ours) or "").strip() and not os.environ.get(theirs):
            os.environ[theirs] = e[ours].strip()
    researcher = GPTResearcher(query=question, report_type="research_report")

    async def go():
        await researcher.conduct_research()
        return await researcher.write_report()

    report = await asyncio.wait_for(go(), timeout=RESEARCH_TIMEOUT_S)
    sources, cost = [], None
    try:
        sources = list(researcher.get_source_urls() or [])
    except Exception:
        pass
    try:
        c = researcher.get_costs()
        cost = float(c) if c is not None else None
    except Exception:
        pass
    return {"report": str(report or ""), "sources": [str(u) for u in sources][:30], "cost_usd": cost}
