"""2026-11-18: windows the desk only considered (blocked or not traded) are settled from the venue so the blocked-trades export
shows how each would have done; older candidate windows without a result are filled in slowly."""
import asyncio

from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


class _Client:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    async def settlement(self, slug):
        self.asked.append(slug)
        return self.answers.get(slug)


class _Store:
    def __init__(self, slugs):
        self.slugs = slugs

    async def unsettled_candidates(self, asset, limit=300):
        return list(self.slugs)


def test_a_watched_window_is_settled_after_it_closes_and_only_on_a_final_answer():
    c = _Client({"w-final": 1.0, "w-open": 0.55})
    d = Desk(from_env({}, asset="btc"), c, None)
    d._watched = {"w-final": 1000.0, "w-open": 1000.0}
    asyncio.run(d.settle_watched(1010.0))                  # too soon after the close: nothing asked
    assert c.asked == []
    asyncio.run(d.settle_watched(1030.0))
    res = d.ledger.take_results()
    assert [(r["slug"], r["up"]) for r in res] == [("w-final", True)]
    assert "w-final" not in d._watched and "w-open" in d._watched
    asyncio.run(d.settle_watched(2000.0))                  # still no final answer 15 minutes on: dropped
    assert d._watched == {}


def test_older_candidate_windows_are_filled_in_one_at_a_time():
    c = _Client({"old-1": 0.0, "old-2": 1.0})
    d = Desk(from_env({}, asset="xrp"), c, _Store(["old-1", "old-2"]))
    asyncio.run(d.settle_watched(5000.0))
    asyncio.run(d.settle_watched(5001.0))                  # under 3 s later: waits
    asyncio.run(d.settle_watched(5004.0))
    assert sorted((r["slug"], r["up"]) for r in d.ledger.take_results()) == [("old-1", False), ("old-2", True)]
