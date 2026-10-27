"""Credits left for the System Vitals box: ElevenLabs live, Anthropic and Google from the balance the owner typed."""

import asyncio
import datetime as dt

from openjarvis.server.crew import credits
from openjarvis.server.crew.store import MemoryCrewStore

NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        return self._b


def _client(code=200, body=None, seen=None):
    class C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            if seen is not None:
                seen.append((url, headers))
            return _Resp(code, body or {})

    return lambda: C()


def _run(coro):
    return asyncio.run(coro)


def _by_key(rep):
    return {i["key"]: i for i in rep["items"]}


def test_elevenlabs_is_read_live_and_flagged_low():
    seen = []
    body = {"character_count": 90_000, "character_limit": 100_000, "next_character_count_reset_unix": 1_793_000_000, "tier": "creator"}
    rep = _run(credits.report(MemoryCrewStore(), "u1", {"ELEVENLABS_API_KEY": "k"}, client_factory=_client(200, body, seen), now=NOW))
    e = _by_key(rep)["elevenlabs"]
    assert (e["left"], e["total"], e["low"]) == (10_000, 100_000, True)
    assert seen[0][1] == {"xi-api-key": "k"}


def test_elevenlabs_without_a_key_or_when_it_fails_shows_a_note_not_a_number():
    rep = _run(credits.report(MemoryCrewStore(), "u1", {}, client_factory=_client(), now=NOW))
    assert _by_key(rep)["elevenlabs"]["left"] is None and "key" in _by_key(rep)["elevenlabs"]["note"]
    rep = _run(credits.report(MemoryCrewStore(), "u1", {"ELEVENLABS_API_KEY": "k"}, client_factory=_client(401), now=NOW))
    assert _by_key(rep)["elevenlabs"]["left"] is None


def test_manual_balances_start_unset_then_keep_what_was_typed():
    st = MemoryCrewStore()
    rep = _run(credits.report(st, "u1", {}, client_factory=_client(), now=NOW))
    assert _by_key(rep)["anthropic"]["left"] is None and "Not set" in _by_key(rep)["anthropic"]["note"]
    _run(credits.save_balance(st, "u1", "google", 42.5, 5, NOW))
    g = _by_key(_run(credits.report(st, "u1", {}, client_factory=_client(), now=NOW)))["google"]
    assert (g["left"], g["warn_usd"], g["low"], g["estimated"]) == (42.5, 5.0, False, False)


def test_anthropic_balance_is_an_estimate_minus_what_the_app_tracked_since():
    st = MemoryCrewStore()
    _run(credits.save_balance(st, "u1", "anthropic", 30, None, NOW - dt.timedelta(days=1)))
    _run(st.add_event("u1", "step", "work", mission_id="m1", cost_usd=22.0))
    a = _by_key(_run(credits.report(st, "u1", {}, client_factory=_client(), now=NOW)))["anthropic"]
    assert a["estimated"] is True and a["left"] == 8.0 and a["low"] is True       # 30 - 22 = 8, under the default $10 warning


def test_only_anthropic_and_google_can_be_typed_in():
    try:
        _run(credits.save_balance(MemoryCrewStore(), "u1", "elevenlabs", 5, None, NOW))
    except ValueError:
        return
    raise AssertionError("elevenlabs is read live, not typed")
