"""What goes into a brief and what must never be in it. Pure functions: no network, no database, easy to test.

  * greeting_period / greeting_line: the greeting comes from the CURRENT time in America/Los_Angeles, never from the name of the scheduled slot
    (a "morning" brief that is run at 10:30 PM still says "Good evening").
  * sanitize_brief_body / sanitize_spoken: applied to every brief that is posted (briefs.py), whoever or whatever wrote it:
      - a greeting the writer put at the top is removed (the page adds the right one from the time),
      - the Meta Ads section and any Meta Ads / Meta access-token error line are removed (Meta ads have stopped),
      - a section, bullet or paragraph that appears twice in the same brief appears once,
      - items already reported in the same product's previous brief are not repeated under Completed or Errors.
  * unchecked_line: how a source that could not be read is written down. Nothing is ever filled in for it.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

LA = ZoneInfo("America/Los_Angeles")

# morning 05:00 to 11:59, afternoon 12:00 to 16:59, evening 17:00 to 04:59 (local Los Angeles time)
MORNING_START, AFTERNOON_START, EVENING_START = 5, 12, 17

# the scheduled slots (Los Angeles time): the brief for each period is written at this time
SLOTS = {"morning": (6, 52), "afternoon": (12, 52), "evening": (21, 52)}


def la_time(now: Optional[dt.datetime] = None) -> dt.datetime:
    """Now (or the given moment) as a Los Angeles time. A naive datetime is taken as UTC."""
    n = now or dt.datetime.now(dt.timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=dt.timezone.utc)
    return n.astimezone(LA)


def greeting_period(now: Optional[dt.datetime] = None) -> str:
    h = la_time(now).hour
    if MORNING_START <= h < AFTERNOON_START:
        return "morning"
    if AFTERNOON_START <= h < EVENING_START:
        return "afternoon"
    return "evening"


def greeting_line(now: Optional[dt.datetime] = None, name: str = "Michael") -> str:
    return f"Good {greeting_period(now)}, {name}."


# ── text clean-up ───────────────────────────────────────────────────────────

_GREETING_LINE = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?good\s+(?:morning|afternoon|evening|night)\b[^.!?\n]*(?:\*\*)?[.!?]*[ \t]*(?:\n|$)?", re.I)      # the greeting sentence only
_META_HEADING = re.compile(r"\bmeta\s+ads?\b", re.I)
_META_LINE = [
    re.compile(r"\[meta ads?\]", re.I),
    re.compile(r"\bmeta\s+ads?\b", re.I),
    re.compile(r"meta not connected", re.I),
    re.compile(r"\b(?:meta|facebook)\b[^.\n]{0,100}(?:access token|oauth|token (?:was |is )?rejected|rejected[^.\n]{0,30}token|error code 190|invalid token)", re.I),
    re.compile(r"(?:invalid|expired)\s+oauth\s+access\s+token", re.I),
    re.compile(r"cannot parse access token", re.I),
]
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_REPEAT_SECTIONS = {"completed", "completed today", "errors", "error"}


def strip_leading_greeting(text: str) -> str:
    """Removes 'Good morning ...' lines from the top (a greeting in the wrong part of the day is the bug this prevents)."""
    s = text.lstrip("\n")
    while True:
        m = _GREETING_LINE.match(s)
        if not m:
            return s
        s = s[m.end():].lstrip("\n")


def _is_meta_line(line: str) -> bool:
    return any(p.search(line) for p in _META_LINE)


def _norm(line: str) -> str:
    t = _BULLET.sub("", line).lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return " ".join(t.split())


def _sections(lines: List[str]) -> List[Tuple[int, str, List[str]]]:
    """[(level, heading text or '', its lines)]: level 0 is the text before the first heading."""
    out: List[Tuple[int, str, List[str]]] = [(0, "", [])]
    for ln in lines:
        m = _HEADING.match(ln)
        if m:
            out.append((len(m.group(1)), m.group(2).strip(), [ln]))
        else:
            out[-1][2].append(ln)
    return out


def remove_meta_ads(text: str) -> str:
    """Drops every section whose heading is about Meta Ads, and every line that mentions Meta Ads or a rejected Meta / Facebook token."""
    kept: List[str] = []
    skip_level: Optional[int] = None
    for ln in text.split("\n"):
        m = _HEADING.match(ln)
        if m:
            level = len(m.group(1))
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and _META_HEADING.search(m.group(2)):
                skip_level = level
                continue
        if skip_level is not None:
            continue
        if _is_meta_line(ln):
            continue
        kept.append(ln)
    return _tidy("\n".join(kept))


def _tidy(text: str) -> str:
    out = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    return out + "\n" if text.endswith("\n") and out else out


def dedupe_within(text: str) -> str:
    """Each section (same heading under the same parent) once, and each bullet or paragraph once. Headings left empty are removed."""
    lines = text.split("\n")
    seen_lines = set()
    seen_sections = set()
    parent = ""
    out: List[str] = []
    skip_level: Optional[int] = None
    for ln in lines:
        m = _HEADING.match(ln)
        if m:
            level, title = len(m.group(1)), _norm(m.group(2))
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is not None:
                continue
            if level <= 2:
                parent = title if level == 2 else ""
            key = (level, parent if level > 2 else "", title)
            if key in seen_sections:
                skip_level = level                                   # the repeated section, and everything in it, is dropped
                continue
            seen_sections.add(key)
            out.append(ln)
            continue
        if skip_level is not None:
            continue
        n = _norm(ln)
        if len(n.split()) >= 4:                                      # short lines ("None.", labels) may legitimately repeat
            if n in seen_lines:
                continue
            seen_lines.add(n)
        out.append(ln)
    return _drop_empty_headings(_tidy("\n".join(out)))


def _drop_empty_headings(text: str) -> str:
    """A heading whose whole section (including deeper headings) has no text left is removed."""
    lines = text.split("\n")
    keep = [True] * len(lines)
    for i, ln in enumerate(lines):
        m = _HEADING.match(ln)
        if not m:
            continue
        level, has_text = len(m.group(1)), False
        for nxt in lines[i + 1:]:
            m2 = _HEADING.match(nxt)
            if m2 and len(m2.group(1)) <= level:
                break
            if not m2 and nxt.strip():
                has_text = True
                break
        keep[i] = has_text
    return _tidy("\n".join(l for l, k in zip(lines, keep) if k))


def drop_already_reported(text: str, previous: Optional[str]) -> str:
    """Under 'Completed' and 'Errors', leaves out bullets the previous brief already listed under the same heading."""
    if not previous:
        return text
    old = {}
    for level, title, body in _sections(previous.split("\n")):
        if _norm(title) in _REPEAT_SECTIONS:
            old.setdefault(_norm(title), set()).update(_norm(l) for l in body[1:] if _BULLET.match(l) or len(_norm(l).split()) >= 4)
    if not old:
        return text
    out: List[str] = []
    section_norm: Optional[str] = None
    pending_empty: Optional[int] = None
    removed_here = 0
    kept_here = 0
    for ln in text.split("\n"):
        m = _HEADING.match(ln)
        if m:
            if section_norm in old and removed_here and not kept_here and pending_empty is not None:
                out.insert(pending_empty + 1, "- Nothing new since the last brief.")
            section_norm = _norm(m.group(2))
            pending_empty = len(out)
            removed_here = kept_here = 0
            out.append(ln)
            continue
        if section_norm in old and ln.strip():
            n = _norm(ln)
            if n and n in old[section_norm]:
                removed_here += 1
                continue
            kept_here += 1
        out.append(ln)
    if section_norm in old and removed_here and not kept_here and pending_empty is not None:
        out.insert(pending_empty + 1, "- Nothing new since the last brief.")
    return _tidy("\n".join(out))


def sanitize_brief_body(body: str, previous_body: Optional[str] = None) -> str:
    t = strip_leading_greeting(body)
    t = remove_meta_ads(t)
    t = dedupe_within(t)
    t = drop_already_reported(t, previous_body)
    return t if t.strip() else body.strip()


def sanitize_spoken(spoken: str) -> str:
    """The spoken version: no wrong-time greeting, no sentence about Meta Ads or a Meta token, no repeated sentence."""
    s = strip_leading_greeting(spoken)
    parts = re.split(r"(?<=[.!?])\s+", s.strip())
    seen, out = set(), []
    for p in parts:
        if not p or _is_meta_line(p):
            continue
        n = _norm(p)
        if n in seen:
            continue
        seen.add(n)
        out.append(p)
    res = " ".join(out).strip()
    return res if res else spoken.strip()


def unchecked_line(source: str, reason: str) -> str:
    """How a source that could not be read appears in a brief. No number, no guess."""
    reason = " ".join(str(reason or "could not be read").split())[:120]
    return f"- {source}: unchecked ({reason})."
