"""Where the AI money went: by role, kind, mission and day; and the double counts that must stay out of the money total."""

import asyncio
import datetime as dt

from openjarvis.server.crew import finance, spend
from openjarvis.server.crew.store import MemoryCrewStore

NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def test_the_crew_spend_is_split_by_role_kind_and_mission():
    st = MemoryCrewStore(clock=lambda: NOW - dt.timedelta(hours=2))

    async def go():
        m = await st.create_mission("u1", "grow the shop", 5, title="Shop growth")
        t = await st.create_tasks(m["id"], "u1", [{"idx": 0, "title": "Write copy", "role": "writer", "brief": "b", "depends_on": [], "budget_usd": 2}])
        task_id = t[0]["id"]
        await st.add_event("u1", "plan", "planned", mission_id=m["id"], cost_usd=0.40)
        await st.add_event("u1", "step", "wrote", mission_id=m["id"], task_id=task_id, cost_usd=1.10)
        await st.add_event("u1", "step", "wrote more", mission_id=m["id"], task_id=task_id, cost_usd=0.50)
        await st.add_event("u1", "step", "free", mission_id=m["id"], cost_usd=0.0)
        await st.add_event("u2", "step", "someone else", cost_usd=9.0)
        return await spend.report(st, "u1", pool=None, role_names={"writer": "Quill"}, now=NOW)

    rep = _run(go())
    assert rep["crew"]["total"] == 2.0 and rep["total_ai"] == 2.0
    roles = {r["name"]: r["usd"] for r in rep["crew"]["by_role"]}
    assert roles == {"Quill": 1.6, "Helix planning": 0.4}
    kinds = {k["name"]: k for k in rep["crew"]["by_kind"]}
    assert kinds["step"]["usd"] == 1.6 and kinds["plan"]["usd"] == 0.4 and kinds["step"]["n"] == 2
    assert rep["crew"]["by_mission"][0]["name"] == "Shop growth" and rep["crew"]["by_mission"][0]["usd"] == 2.0
    assert rep["by_day"][0] == {"day": "2026-10-27", "usd": 2.0}


def test_events_older_than_the_window_are_left_out():
    st = MemoryCrewStore(clock=lambda: NOW - dt.timedelta(days=40))
    _run(st.add_event("u1", "step", "old", cost_usd=5.0))
    assert _run(spend.report(st, "u1", now=NOW))["total_ai"] == 0.0


def test_the_money_total_leaves_out_the_second_media_copy_and_the_listen_counters():
    sql = finance.REAL_SPEND_ROWS
    assert "media:__all__" in sql and "brief_listen:" in sql
