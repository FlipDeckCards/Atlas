"""Google Calendar for Helix, offline: a fake Google Calendar answers; nothing real is called."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from openjarvis.server.crew import connections as conns
from openjarvis.server.crew.store import MemoryCrewStore

START = "2026-10-31T17:00:00-07:00"


def run(c):
    return asyncio.run(c)


def _store():
    s = MemoryCrewStore()
    run(s.set_secret("u", "google_refresh", "r"))
    run(s.set_secret("u", "google_access", "tok"))
    run(s.set_secret("u", "google_expires", str(int(time.time()) + 3000)))
    return s


def _factory(status=200):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.headers["authorization"] == "Bearer tok"
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "insufficient"}})
        if req.method == "POST":
            return httpx.Response(200, json={"id": "ev1", "htmlLink": "https://calendar.google.com/event?eid=ev1"})
        return httpx.Response(200, json={"items": [
            {"summary": "Launch party", "location": "The Loft", "start": {"dateTime": "2026-10-31T17:00:00-07:00"}, "end": {"dateTime": "2026-10-31T19:00:00-07:00"}},
            {"summary": "Holiday", "start": {"date": "2026-11-01"}, "end": {"date": "2026-11-02"}}]})

    return (lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))), seen


def _go(action, args, cf):
    return run(conns.run("google", action, args, store=_store(), user_id="u", env={"GOOGLE_CLIENT_ID": "i", "GOOGLE_CLIENT_SECRET": "s", "GOOGLE_REDIRECT_URI": "https://x/cb"},
                         client_factory=cf))


def test_the_calendar_permission_is_asked_for_and_the_actions_have_the_right_risk():
    assert "calendar.events" in conns.GOOGLE_SCOPES
    assert conns.risk_of("google", "calendar_list") == "read" and conns.risk_of("google", "calendar_add") == "checkpoint"


def test_listing_shows_timed_and_all_day_events_in_los_angeles_time():
    cf, seen = _factory()
    out = _go("calendar_list", {"days": 99}, cf)
    assert "Sat Oct 31 5:00 PM PT | Launch party | The Loft" in out and "2026-11-01 (all day) | Holiday" in out
    assert seen[0].url.params["singleEvents"] == "true" and seen[0].url.params["orderBy"] == "startTime"
    assert len([k for k in seen[0].url.params.multi_items() if k[0] == "timeMax"]) == 1


def test_an_added_event_is_described_for_approval_and_sent_with_no_guests():
    args = {"title": "Launch party", "start": START, "minutes": 90, "location": "The Loft", "notes": "Bring the banner"}
    text = conns.describe_action("google", "calendar_add", args)
    assert 'Add this event to your Google Calendar: "Launch party", Sat Oct 31 5:00 PM to 6:30 PM PT at The Loft.' in text and "Nobody else is invited" in text
    cf, seen = _factory()
    out = _go("calendar_add", args, cf)
    assert "Added to your Google Calendar" in out and "calendar.google.com" in out
    body = json.loads(seen[0].content)
    assert body["summary"] == "Launch party" and body["end"]["dateTime"].startswith("2026-10-31T18:30:00") and "attendees" not in body and body["location"] == "The Loft"


def test_bad_events_are_refused_before_anything_is_sent():
    cf, seen = _factory()
    for bad in ({"title": "", "start": START}, {"title": "x", "start": "tomorrow"}, {"title": "x", "start": "2026-10-31T17:00:00"},
                {"title": "x", "start": START, "end": "2026-10-31T16:00:00-07:00"}):
        with pytest.raises(conns.ConnectionError_):
            _go("calendar_add", bad, cf)
    assert not seen


def test_a_missing_calendar_permission_says_how_to_fix_it():
    with pytest.raises(conns.ConnectionError_, match="Disconnect Google and Connect it again"):
        _go("calendar_list", {}, _factory(403)[0])
