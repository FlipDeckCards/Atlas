"""The crew engine, offline: planning, parallel tasks, questions through Helix, checkpoints, budgets, the daily cap, quiet hours,
the Helix confirm-before-start rule. A scripted fake model plays the planner and the agents; a fake HTTP client plays Stripe."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import httpx
import pytest

from openjarvis.server.crew import connections as conns
from openjarvis.server.crew import engine as eng_mod
from openjarvis.server.crew import helix as crew_helix
from openjarvis.server.crew.engine import CrewEngine, in_quiet_hours, parse_plan
from openjarvis.server.crew.store import MemoryCrewStore

UID = "owner-1"
NOON_LA = dt.datetime(2026, 10, 21, 19, 0, tzinfo=dt.timezone.utc)      # 12:00 in Los Angeles
NIGHT_LA = dt.datetime(2026, 10, 22, 6, 0, tzinfo=dt.timezone.utc)      # 23:00 in Los Angeles
ENV = {"CREW_ENABLED": "true", "CREW_DAILY_CAP_USD": "5", "CREW_MISSION_BUDGET_USD": "1", "CREW_MISSION_REPORTS": "off"}


def run(coro):
    return asyncio.run(coro)


def resp(content=None, calls=()):
    tcs = [SimpleNamespace(id=f"call_{i}_{name}", function=SimpleNamespace(name=name, arguments=json.dumps(args))) for i, (name, args) in enumerate(calls)]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tcs or None))])


class Model:
    """Plays the planner (when there are no tools) and each agent, chosen by the task title in the conversation."""

    def __init__(self, plan, agents):
        self.plan, self.agents, self.calls = plan, agents, []

    async def __call__(self, model, messages, max_tokens=None, tools=None):
        self.calls.append({"model": model, "messages": messages, "tools": tools})
        if not tools:
            return resp(json.dumps(self.plan))
        convo = json.dumps(messages)
        for title, script in self.agents.items():
            if f"({title})" in messages[1]["content"]:
                turn = sum(1 for m in messages if m["role"] == "assistant")
                return script(turn, messages) if callable(script) else script[min(turn, len(script) - 1)]
        if messages[0]["content"].startswith("You are Lens"):          # the reviewer every multi-task plan now ends with
            return resp(None, [("finish", {"summary": "Reviewed: all good."})])
        raise AssertionError("no script for " + convo[:200])


class FakeHttp:
    def __init__(self, replies=None):
        self.requests, self.replies = [], replies or {}

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.requests.append(("GET", url, kw))
        return httpx.Response(200, json=self.replies.get(("GET", url), {"data": []}))

    async def post(self, url, **kw):
        self.requests.append(("POST", url, kw))
        return httpx.Response(200, json=self.replies.get(("POST", url), {"id": "prod_123", "default_price": "price_456", "url": "https://buy.stripe.com/x"}))


class Notes:
    def __init__(self):
        self.sent = []

    async def notify(self, user_id, title, text):
        self.sent.append((user_id, title, text))


def world(plan, agents, env=None, now=NOON_LA, http=None, store=None, owner=UID):
    store = store or MemoryCrewStore(clock=lambda: now)
    notes, model = Notes(), Model(plan, agents)
    e = CrewEngine(store, model, notifier=notes, env=dict(ENV, **(env or {})), cost_fn=lambda r: 0.01, now=lambda: now,
                   models={"writing": "claude-x", "lookup": "gemini-x", "default": "gpt-x"}, client_factory=http)

    async def is_owner(uid):                       # UID is the owner (Michael) unless a test says otherwise; anyone else subscribes
        return uid == owner

    e.owner_fn = is_owner
    return SimpleNamespace(store=store, engine=e, notes=notes, model=model, http=http)


def two_task_plan():
    return {"title": "Planner pack", "project": "New", "tasks": [
        {"title": "Research buyers", "role": "researcher", "brief": "Who buys digital planners", "depends_on": []},
        {"title": "Write listing", "role": "writer", "brief": "Write the listing", "depends_on": [0]}]}


async def start(w, goal="Build me a digital planner product for card collectors", budget=None):
    m = await w.engine.start_mission(UID, goal, budget, background=False)
    await w.engine.run_until_idle()
    return await w.store.get_mission(m["id"])


# ── the plan ─────────────────────────────────────────────────────────────────────────────────────

class TestParsePlan:
    def test_reads_fenced_json_and_keeps_only_backward_dependencies(self):
        p = parse_plan('```json\n{"title":"T","tasks":[{"title":"a","role":"writer","brief":"x","depends_on":[1]},'
                       '{"title":"b","role":"hacker","brief":"y","depends_on":[0,0,5,"z"]}]}\n```', 8)
        assert [t["depends_on"] for t in p["tasks"]] == [[], [0]]
        assert p["tasks"][1]["role"] == "builder"           # an unknown role becomes a builder

    def test_rejects_garbage_and_caps_the_task_count(self):
        assert parse_plan("no json here", 8) is None
        assert parse_plan('{"tasks": []}', 8) is None
        many = {"tasks": [{"title": f"t{i}", "role": "writer", "brief": "b"} for i in range(20)]}
        assert len(parse_plan(json.dumps(many), 5)["tasks"]) == 5


# ── a whole mission ──────────────────────────────────────────────────────────────────────────────

class TestMission:
    def test_planned_worked_in_order_finished_and_charged(self):
        w = world(two_task_plan(), {
            "Research buyers": [resp(calls=[("save_output", {"name": "buyers.md", "content": "# Buyers\n- collectors"})]),
                                resp(calls=[("finish", {"summary": "Collectors who track sets."})])],
            "Write listing": [resp("Listing written: Collector Planner 2026.")]})
        m = run(start(w))
        tasks = run(w.store.list_tasks(m["id"]))
        assert m["status"] == "done" and [t["status"] for t in tasks] == ["done", "done", "done"] and tasks[2]["role"] == "reviewer"
        assert tasks[1]["result"].startswith("Listing written")
        assert [o["name"] for o in run(w.store.list_outputs(m["id"]))] == ["buyers.md"]
        # the writer saw the researcher's result, and Gemini did research while Claude wrote
        writer_call = next(c for c in w.model.calls if c["tools"] and "(Write listing)" in c["messages"][1]["content"])
        assert "Collectors who track sets." in writer_call["messages"][1]["content"]
        assert {c["model"] for c in w.model.calls if c["tools"]} == {"gemini-x", "claude-x"}
        assert m["spent_usd"] == pytest.approx(0.05)                 # planner + 2 research rounds + 1 writer round + 1 review round
        assert run(w.store.get_day_spend(UID, "2026-10-21")) == pytest.approx(0.05)
        assert any("Mission complete" in t for _, t, _ in w.notes.sent)

    def test_a_task_waits_for_the_tasks_it_depends_on(self):
        order = []

        def researcher(turn, msgs):
            order.append("research")
            return resp("done researching")

        def writer(turn, msgs):
            order.append("write")
            return resp("done writing")

        w = world(two_task_plan(), {"Research buyers": researcher, "Write listing": writer})
        run(start(w))
        assert order == ["research", "write"]

    def test_an_unusable_plan_fails_the_mission_and_says_so(self):
        w = world({"nope": True}, {})
        m = run(start(w))
        assert m["status"] == "failed" and any("could not be planned" in t for _, t, _ in w.notes.sent)

    def test_tool_results_never_claim_work_that_did_not_happen(self):
        w = world({"tasks": [{"title": "Solo", "role": "writer", "brief": "b"}]},
                  {"Solo": [resp(calls=[("use_connection", {"platform": "stripe", "action": "create_product", "args": {}})]), resp("ok")]})
        run(start(w))
        tool_msgs = [m for c in w.model.calls for m in c["messages"] if m.get("role") == "tool"]
        assert any("no tool named 'use_connection'" in m["content"] for m in tool_msgs)     # only operators may use platforms


# ── Michael in the loop ─────────────────────────────────────────────────────────────────────────

class TestQuestions:
    def test_a_question_pauses_only_that_task_and_the_answer_resumes_it(self):
        def writer(turn, msgs):
            if turn == 0:
                return resp(calls=[("ask_michael", {"question": "Which price?", "options": ["$4.99", "$9.99"]})])
            assert "Michael answered" in json.dumps(msgs) and "$9.99" in json.dumps(msgs)
            return resp("Priced at $9.99.")

        plan = {"tasks": [{"title": "Write listing", "role": "writer", "brief": "b"}, {"title": "Research buyers", "role": "researcher", "brief": "r"}]}
        w = world(plan, {"Write listing": writer, "Research buyers": [resp("researched")]})
        m = run(start(w))
        tasks = {t["title"]: t for t in run(w.store.list_tasks(m["id"]))}
        assert tasks["Write listing"]["status"] == "waiting" and tasks["Research buyers"]["status"] == "done"
        q = run(w.store.list_questions(UID))[0]
        assert q["kind"] == "question" and q["options"] == ["$4.99", "$9.99"]
        assert any("Question from the crew" in t for _, t, _ in w.notes.sent)
        out = run(w.engine.answer(UID, q["id"], answer="$9.99"))
        assert out["ok"]
        run(w.engine.run_until_idle())
        assert run(w.store.get_mission(m["id"]))["status"] == "done"
        with pytest.raises(ValueError):
            run(w.engine.answer(UID, q["id"], answer="again"))           # answered once only

    def test_another_user_cannot_answer(self):
        w = world({"tasks": [{"title": "Write listing", "role": "writer", "brief": "b"}]},
                  {"Write listing": [resp(calls=[("ask_michael", {"question": "Q?"})])]})
        run(start(w))
        q = run(w.store.list_questions(UID))[0]
        with pytest.raises(LookupError):
            run(w.engine.answer("someone-else", q["id"], answer="hi"))


def operator_plan():
    return {"tasks": [{"title": "Set up checkout", "role": "operator", "brief": "Create the product in Stripe"}]}


class TestCheckpoints:
    def _world(self, http, after=None):
        def op(turn, msgs):
            if turn == 0:
                return resp(calls=[("use_connection", {"platform": "stripe", "action": "create_product",
                                                       "args": {"name": "Collector Planner", "price_usd": 9.99, "interval": "once"}})])
            return resp(after or "done")
        return world(operator_plan(), {"Set up checkout": op}, env={"STRIPE_SECRET_KEY": "sk_test_x"}, http=http)

    def test_a_risky_action_waits_for_approval_and_runs_once_by_the_server(self):
        http = FakeHttp()
        w = self._world(http)
        m = run(start(w))
        q = run(w.store.list_questions(UID))[0]
        assert q["kind"] == "checkpoint" and "$9.99" in q["text"] and http.requests == []        # nothing ran yet
        assert run(w.store.list_tasks(m["id"]))[0]["status"] == "waiting"
        out = run(w.engine.answer(UID, q["id"], approve=True))
        assert "prod_123" in out["message"] and len(http.requests) == 1
        method, url, kw = http.requests[0]
        assert url.endswith("/products") and kw["data"]["metadata[app]"] == "crew" and kw["data"]["default_price_data[unit_amount]"] == "999"
        with pytest.raises(ValueError):
            run(w.engine.answer(UID, q["id"], approve=True))           # a double tap does not run it twice
        assert len(http.requests) == 1
        run(w.engine.run_until_idle())
        assert run(w.store.get_mission(m["id"]))["status"] == "done"

    def test_declined_means_it_never_runs_and_the_agent_is_told(self):
        http = FakeHttp()
        w = self._world(http)
        run(start(w))
        q = run(w.store.list_questions(UID))[0]
        run(w.engine.answer(UID, q["id"], approve=False, answer="not yet"))
        run(w.engine.run_until_idle())
        assert http.requests == []
        assert "declined" in json.dumps(w.model.calls[-1]["messages"])

    def test_a_platform_that_is_not_connected_is_refused_without_bothering_michael(self):
        def op(turn, msgs):
            if turn == 0:
                return resp(calls=[("use_connection", {"platform": "stripe", "action": "create_product", "args": {"name": "x", "price_usd": 5}})])
            return resp("Stripe is not connected; noted.")
        w = world(operator_plan(), {"Set up checkout": op})              # no STRIPE_SECRET_KEY
        run(start(w))
        assert run(w.store.list_questions(UID)) == []
        assert "Not connected" in json.dumps(w.model.calls[-1]["messages"])

    def test_a_read_action_runs_straight_away(self):
        http = FakeHttp({("GET", "https://api.stripe.com/v1/products"): {"data": [{"id": "prod_1", "name": "Old", "default_price": {"unit_amount": 499}}]}})

        def op(turn, msgs):
            if turn == 0:
                return resp(calls=[("use_connection", {"platform": "stripe", "action": "list_products", "args": {}})])
            assert "Old (prod_1, $4.99)" in json.dumps(msgs)
            return resp("listed")
        w = world(operator_plan(), {"Set up checkout": op}, env={"STRIPE_SECRET_KEY": "sk_test_x"}, http=http)
        assert run(start(w))["status"] == "done"


# ── money limits ─────────────────────────────────────────────────────────────────────────────────

class TestUnfinishedMissions:
    def test_a_reviewer_still_waiting_on_rework_keeps_the_mission_from_done(self):
        # the failed FlipDeck run: Lens said it was still waiting on a rework, and the mission was marked done anyway
        plan = {"tasks": [{"title": "Write guide", "role": "writer", "brief": "b"},
                          {"title": "Review work", "role": "reviewer", "brief": "b"}]}
        lens = "Lens began reviewing the work but is waiting for the Baby Shower product guide rework to complete."
        w = world(plan, {"Write guide": [resp("guide written")], "Review work": [resp(lens)]}, owner=UID)
        m = run(start(w))
        assert run(w.store.get_mission(m["id"]))["status"] == "stalled"

    def test_an_etsy_draft_mission_without_drafts_is_not_done(self):
        plan = {"tasks": [{"title": "Write guide", "role": "writer", "brief": "b"}]}
        w = world(plan, {"Write guide": [resp("guide written")]}, owner=UID)
        m = run(start(w, goal="Make 3 Etsy draft listings with mockups"))
        assert run(w.store.get_mission(m["id"]))["status"] == "stalled"

    def test_a_clean_review_still_finishes_the_mission(self):
        plan = {"tasks": [{"title": "Write guide", "role": "writer", "brief": "b"},
                          {"title": "Review work", "role": "reviewer", "brief": "b"}]}
        w = world(plan, {"Write guide": [resp("guide written")], "Review work": [resp("All three products are complete.")]}, owner=UID)
        m = run(start(w))
        assert run(w.store.get_mission(m["id"]))["status"] == "done"


def connect_subscriber(w):
    """A subscriber with their own GitHub and Stripe connected, so their missions may run."""
    from openjarvis.server.crew import tenant
    run(tenant.put(w.store, UID, "stripe_key", "sk_test_x"))
    run(tenant.put(w.store, UID, "github_token", "ghp_test"))


class TestSubscriberGate:
    def test_a_subscriber_cannot_start_a_mission_until_github_and_stripe_are_connected(self):
        w = world(two_task_plan(), {"Research buyers": [resp("r")], "Write listing": [resp("w")]}, owner=None)
        try:
            run(w.engine.start_mission(UID, "Make a planner product for card collectors", background=False))
            raise AssertionError("the mission should not start")
        except ValueError as exc:
            assert "GitHub" in str(exc) and "Stripe" in str(exc)
        assert run(w.store.list_missions(UID)) == [] if hasattr(w.store, "list_missions") else True

class TestHelixBusinesses:
    def test_helix_adds_and_removes_a_business_on_the_station(self):
        from openjarvis.server.crew import helix, tenant
        w = world(two_task_plan(), {"Research buyers": [resp("r")], "Write listing": [resp("w")]}, owner=None)
        ctx = helix.CrewCtx(UID, w.engine)
        out = run(helix.execute(ctx, "add_business", {"name": "Helix Business", "url": "helixbusiness.com"}))
        assert out.startswith('Added "Helix Business"')
        assert [b["name"] for b in run(tenant.businesses(w.store, UID))] == ["Helix Business"]
        assert "already" in run(helix.execute(ctx, "add_business", {"name": "FlipDeck"}))
        assert "stay" in run(helix.execute(ctx, "remove_business", {"name": "FlipDeck"}))
        run(helix.execute(ctx, "remove_business", {"name": "Helix Business"}))
        assert run(tenant.businesses(w.store, UID)) == []


class TestBudgets:
    def test_a_task_out_of_budget_asks_and_continues_only_if_approved(self):
        def writer(turn, msgs):
            return resp(calls=[("save_output", {"name": f"part{turn}.md", "content": "x"})]) if turn < 4 else resp("done")
        w = world({"tasks": [{"title": "Write listing", "role": "writer", "brief": "b"}]}, {"Write listing": writer}, owner=None)
        connect_subscriber(w)
        m = run(start(w, budget=0.06))                       # planner 0.01, then the task gets 0.05: two calls of 0.02 estimate
        q = run(w.store.list_questions(UID))
        assert len(q) == 1 and q[0]["kind"] == "budget" and run(w.store.list_tasks(m["id"]))[0]["status"] == "waiting"
        run(w.engine.answer(UID, q[0]["id"], approve=True))
        task = run(w.store.list_tasks(m["id"]))[0]
        assert task["budget_usd"] > 0.05 and task["status"] == "queued"

    def test_declining_more_budget_stops_the_task_and_the_mission_says_so(self):
        w = world({"tasks": [{"title": "Write listing", "role": "writer", "brief": "b"}]},
                  {"Write listing": lambda turn, msgs: resp(calls=[("save_output", {"name": "a.md", "content": "x"})])}, owner=None)
        connect_subscriber(w)
        m = run(start(w, budget=0.06))
        q = run(w.store.list_questions(UID))[0]
        run(w.engine.answer(UID, q["id"], approve=False))
        assert run(w.store.list_tasks(m["id"]))[0]["status"] == "failed"
        assert run(w.store.get_mission(m["id"]))["status"] == "stalled"

    def test_the_daily_cap_stops_new_work_and_is_reported_once(self):
        w = world(two_task_plan(), {"Research buyers": [resp("r")], "Write listing": [resp("w")]}, env={"CREW_DAILY_CAP_USD": "0.02"}, owner=None)
        connect_subscriber(w)
        m = run(start(w))                                    # planning (0.01) leaves 0.01: below one call's estimate
        assert [t["status"] for t in run(w.store.list_tasks(m["id"]))] == ["queued", "queued", "queued"]
        run(w.engine.tick())
        caps = [e for e in w.store.events if "Daily crew limit" in e["text"]]
        assert len(caps) == 1

    def test_mission_budget_is_clamped(self):
        w = world(two_task_plan(), {}, env={"CREW_MAX_MISSION_BUDGET_USD": "10"})
        assert w.engine.clamp_budget(500) == 10 and w.engine.clamp_budget(-3) == 0.05 and w.engine.clamp_budget("x") == 1.0


# ── when the crew may work ───────────────────────────────────────────────────────────────────────

class TestSchedule:
    def test_quiet_hours_hold_new_work_unless_night_shift_is_on(self):
        w = world(two_task_plan(), {"Research buyers": [resp("r")], "Write listing": [resp("w")]}, now=NIGHT_LA)
        assert in_quiet_hours(NIGHT_LA) and not in_quiet_hours(NOON_LA)
        m = run(start(w))
        assert all(t["status"] == "queued" for t in run(w.store.list_tasks(m["id"])))
        run(w.store.set_settings(UID, night_shift=True, paused=False))
        run(w.engine.run_until_idle())
        assert run(w.store.get_mission(m["id"]))["status"] == "done"

    def test_pause_all_holds_everything(self):
        w = world(two_task_plan(), {"Research buyers": [resp("r")], "Write listing": [resp("w")]})
        run(w.store.set_settings(UID, night_shift=False, paused=True))
        m = run(start(w))
        assert all(t["status"] == "queued" for t in run(w.store.list_tasks(m["id"])))
        assert run(w.engine.may_run(UID)) == "paused"

    def test_cancel_stops_tasks_and_closes_questions(self):
        w = world({"tasks": [{"title": "Write listing", "role": "writer", "brief": "b"}]}, {"Write listing": [resp(calls=[("ask_michael", {"question": "Q?"})])]})
        m = run(start(w))
        run(w.engine.cancel_mission(UID, m["id"]))
        assert run(w.store.list_tasks(m["id"]))[0]["status"] == "cancelled" and run(w.store.list_questions(UID)) == []

    def test_after_a_restart_interrupted_tasks_are_queued_again(self):
        store = MemoryCrewStore(clock=lambda: NOON_LA)
        w = world(two_task_plan(), {}, store=store)
        m = run(store.create_mission(UID, "goal goal goal", 1.0, status="active"))
        t = run(store.create_tasks(m["id"], UID, [{"idx": 0, "title": "a", "role": "writer", "brief": "b", "depends_on": [], "budget_usd": 0.5}]))[0]
        run(store.claim(t["id"]))

        async def go():
            await w.engine.start()
            await w.engine.stop()
        run(go())
        assert run(store.get_task(t["id"]))["status"] == "queued"


# ── Helix: confirm before a mission starts ───────────────────────────────────────────────────────

class TestHelix:
    def setup_method(self):
        crew_helix.CREW_PENDING.clear(UID)

    def test_start_mission_is_read_back_and_needs_a_plain_yes_on_the_next_message(self):
        w = world(two_task_plan(), {"Research buyers": [resp("r")], "Write listing": [resp("w")]})
        ctx = crew_helix.CrewCtx(UID, w.engine)

        async def go():
            before = crew_helix.CREW_PENDING.get(UID)
            text = await crew_helix.execute(ctx, "start_mission", {"goal": "Build me a planner product", "budget_usd": 2})
            assert "NOT STARTED" in text and await w.store.list_missions(UID) == []
            reply = crew_helix.after_turn(ctx, "Sure.", before)
            assert "Reply 'yes'" in reply and "$2.00" in reply
            assert await crew_helix.pre_turn(ctx, "yes but cheaper") is None          # not a plain yes: cancelled
            assert await w.store.list_missions(UID) == []
            await crew_helix.execute(ctx, "start_mission", {"goal": "Build me a planner product"})
            crew_helix.after_turn(ctx, "", None)
            out = await crew_helix.pre_turn(ctx, "yes")
            assert "Mission started" in out["reply"]
            await w.engine.run_until_idle()
            ms = await w.store.list_missions(UID)
            assert len(ms) == 1 and ms[0]["status"] == "done"
        run(go())

    def test_a_yes_in_the_same_turn_cannot_confirm(self):
        w = world(two_task_plan(), {})
        ctx = crew_helix.CrewCtx(UID, w.engine)

        async def go():
            await crew_helix.execute(ctx, "start_mission", {"goal": "Build me a planner product"})
            assert await crew_helix.pre_turn(ctx, "yes") is None                  # not armed yet: no read-back happened
            assert await w.store.list_missions(UID) == []
        run(go())

    def test_helix_cannot_approve_a_checkpoint(self):
        http = FakeHttp()
        w = TestCheckpoints()._world(http)
        run(start(w))
        q = run(w.store.list_questions(UID))[0]
        text = run(crew_helix.execute(crew_helix.CrewCtx(UID, w.engine), "answer_crew_question", {"question_id": q["id"], "answer": "yes approve"}))
        assert "tap" in text and http.requests == [] and run(w.store.get_question(q["id"]))["status"] == "open"

    def test_status_text_lists_open_items(self):
        w = world({"tasks": [{"title": "Write listing", "role": "writer", "brief": "b"}]}, {"Write listing": [resp(calls=[("ask_michael", {"question": "Blue or green?"})])]})
        run(start(w))
        text = run(crew_helix.status_text(crew_helix.CrewCtx(UID, w.engine)))
        assert "Blue or green?" in text and "Crew spend today" in text


def test_role_tools():
    assert "use_connection" in [t["function"]["name"] for t in eng_mod.tools_for("operator")]
    assert "use_connection" not in [t["function"]["name"] for t in eng_mod.tools_for("writer")]


# ── Scout's web research (GPT Researcher, faked) ─────────────────────────────────────────────────

class TestWebResearch:
    def _world(self, env, fn):
        def scout(turn, msgs):
            if turn == 0:
                return resp(calls=[("web_research", {"question": "Who buys digital planners for card collectors?"})])
            return resp("Researched.")
        w = world({"tasks": [{"title": "Research buyers", "role": "researcher", "brief": "b"}]}, {"Research buyers": scout}, env=env)
        w.engine.research_fn = fn
        return w

    def test_runs_saves_a_sourced_report_and_charges_its_cost(self):
        calls = []

        async def fake(question, env):
            calls.append(question)
            return {"report": "Collectors aged 25-45.", "sources": ["https://example.com/a"], "cost_usd": 0.07}
        w = self._world({"TAVILY_API_KEY": "t", "OPENAI_API_KEY": "o"}, fake)
        m = run(start(w, budget=2))
        assert calls == ["Who buys digital planners for card collectors?"]
        outs = run(w.store.list_outputs(m["id"]))
        assert outs[0]["name"].startswith("research-")
        doc = run(w.store.get_output(outs[0]["id"]))["content"]
        assert "https://example.com/a" in doc and "Collectors aged 25-45." in doc
        assert m["spent_usd"] == pytest.approx(0.01 + 0.01 + 0.07 + 0.01)      # planner, scout x2, research
        assert "https://example.com/a" in json.dumps(w.model.calls[-1]["messages"])

    def test_not_connected_means_no_run_and_the_agent_is_told(self):
        async def fake(question, env):
            raise AssertionError("must not run")
        w = self._world({}, fake)
        run(start(w, budget=2))
        assert "not connected" in json.dumps(w.model.calls[-1]["messages"])

    def test_too_little_budget_means_no_run(self):
        async def fake(question, env):
            raise AssertionError("must not run")
        w = self._world({"TAVILY_API_KEY": "t", "OPENAI_API_KEY": "o"}, fake)
        run(start(w, budget=0.2))
        assert "Not enough budget" in json.dumps(w.model.calls[-1]["messages"])

    def test_only_scout_has_the_tool(self):
        assert "web_research" in [t["function"]["name"] for t in eng_mod.tools_for("researcher")]
        assert "web_research" not in [t["function"]["name"] for t in eng_mod.tools_for("writer")]


# ── finance: P&L per factory ──

def test_finance_attributes_stripe_and_costs():
    import datetime as dt

    from openjarvis.server.crew.finance import Finance, factory_key
    from openjarvis.server.crew.store import MemoryCrewStore

    now = dt.datetime(2026, 10, 21, 20, 0, tzinfo=dt.timezone.utc)  # 1pm in Los Angeles
    today = int(now.timestamp()) - 3600
    old = int((now - dt.timedelta(days=5)).timestamp())
    subs = [{"id": "s1", "customer": "c_m", "status": "active", "metadata": {"app": "marnyx"},
             "items": {"data": [{"quantity": 1, "price": {"unit_amount": 1200, "recurring": {"interval": "month"}}}]}},
            {"id": "s2", "customer": "c_a", "status": "active", "metadata": {"app": "aibussol"},
             "items": {"data": [{"quantity": 1, "price": {"unit_amount": 12000, "recurring": {"interval": "year"}}}]}}]
    charges = [{"id": "ch1", "status": "succeeded", "paid": True, "currency": "usd", "amount": 1200, "customer": "c_m", "created": today},
               {"id": "ch2", "status": "succeeded", "paid": True, "currency": "usd", "amount": 500, "amount_refunded": 100, "customer": "c_x", "created": old},
               {"id": "ch3", "status": "succeeded", "paid": True, "currency": "usd", "amount": 900, "metadata": {"app": "aibussol"}, "created": today},
               {"id": "ch4", "status": "failed", "currency": "usd", "amount": 9999, "created": today},
               {"id": "ch5", "status": "succeeded", "paid": True, "currency": "eur", "amount": 700, "created": today}]

    class R:
        def __init__(self, data):
            self.status_code, self._d = 200, data

        def json(self):
            return {"data": self._d, "has_more": False}

    class C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            return R(subs if url.endswith("subscriptions") else charges)

    async def go():
        store = MemoryCrewStore()
        m = await store.create_mission("u1", "Grow Marnyx signups", 1.0)
        await store.add_event("u1", "task", "x", mission_id=m["id"], cost_usd=0.40)
        fin = Finance(store, env={"STRIPE_SECRET_KEY": "sk_test_x", "CREW_FIXED_COSTS": "marnyx=30"}, client_factory=C, now=lambda: now)
        missions = [dict(m, project="Marnyx", title="Grow", goal=m.get("goal", ""))]
        return await fin.report("u1", missions, ["flipdeck", "marnyx", "deadzone", "aibussol"])

    out = run(go())
    f = out["factories"]
    assert f["marnyx"]["revenue_today"] == 12.0 and f["marnyx"]["mrr"] == 12.0 and f["marnyx"]["subscribers"] == 1
    assert f["marnyx"]["crew_cost_today"] == 0.4 and f["marnyx"]["fixed_cost_today"] == 1.0
    assert f["marnyx"]["net_today"] == round(12.0 - 1.4, 2)
    assert f["flipdeck"]["revenue_30d"] == 4.0 and f["flipdeck"]["revenue_today"] == 0.0  # untagged, refund subtracted
    assert f["aibussol"]["revenue_today"] == 9.0 and f["aibussol"]["mrr"] == 10.0
    assert out["total"]["revenue_today"] == 21.0 and out["stripe_ok"]
    assert any("non-USD" in n for n in out["notes"])
    assert factory_key("Dead Zone") == "deadzone" and factory_key("", "Candle shop").startswith("new:")


# ── the crew's new hands: web search, GitHub with a merge checkpoint, images, the reviewer ──

class FakeGitHub:
    """Just enough of GitHub's REST API for one branch commit and one merge."""

    def __init__(self):
        self.calls, self.branches = [], {"main": "base0"}

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, headers=None, params=None, json=None):
        path = url.replace("https://api.github.com", "")
        self.calls.append((method, path, json))

        def r(code, body):
            return SimpleNamespace(status_code=code, content=b"x", json=lambda: body)

        if path == "/repos/FlipDeckCards/Atlas":
            return r(200, {"default_branch": "main"})
        if path.startswith("/repos/FlipDeckCards/Atlas/git/ref/heads/"):
            b = path.rsplit("heads/", 1)[1]
            return r(200, {"object": {"sha": self.branches[b]}}) if b in self.branches else r(404, {"message": "Not Found"})
        if path.endswith("/git/refs") and method == "POST":
            self.branches[json["ref"].removeprefix("refs/heads/")] = json["sha"]
            return r(201, {})
        if "/git/commits/" in path:
            return r(200, {"tree": {"sha": "tree0"}})
        if path.endswith("/git/trees"):
            return r(201, {"sha": "tree1"})
        if path.endswith("/git/commits"):
            return r(201, {"sha": "commit1234567"})
        if "/git/refs/heads/" in path and method == "PATCH":
            self.branches[path.rsplit("heads/", 1)[1]] = json["sha"]
            return r(200, {})
        if path.endswith("/pulls") and method == "GET":
            return r(200, [])
        if path.endswith("/pulls") and method == "POST":
            return r(201, {"number": 7})
        if path.endswith("/pulls/7/merge"):
            return r(200, {"merged": True, "sha": "merged99"})
        return r(404, {"message": "unexpected " + path})


class TestNewHands:
    def test_scout_searches_the_web(self):
        plan = {"title": "Look", "project": "New", "tasks": [{"title": "Find planners", "role": "researcher", "brief": "Find", "depends_on": []}]}
        script = [resp(None, [("web_search", {"query": "best digital planners 2026"})]), resp(None, [("finish", {"summary": "Found three."})])]
        w = world(plan, {"Find planners": script})
        seen = []
        w.engine.search_fn = lambda q: seen.append(q) or "### Planner A\nSource: https://a.example\nSummary: popular"
        m = run(start(w))
        assert m["status"] == "done" and seen == ["best digital planners 2026"]
        call = [c for c in w.model.calls if c["tools"]][-1]
        assert any("Planner A" in str(x.get("content")) for x in call["messages"] if x["role"] == "tool")
        assert {t["function"]["name"] for t in call["tools"]} >= {"web_search", "web_research"}

    def test_forge_commits_to_a_crew_branch_and_merge_waits_for_approve(self):
        plan = {"title": "Fix", "project": "Aibussol", "tasks": [{"title": "Fix footer", "role": "builder", "brief": "Fix it", "depends_on": []}]}
        script = [resp(None, [("repo_commit", {"repo": "FlipDeckCards/Atlas", "branch": "main", "message": "Fix footer",
                                               "files": [{"path": "src/footer.txt", "content": "hi"}]})]),
                  resp(None, [("request_merge", {"repo": "FlipDeckCards/Atlas", "branch": "main", "title": "Fix footer", "summary": "Small fix"})]),
                  resp(None, [("finish", {"summary": "Merged."})])]
        hub = FakeGitHub()
        w = world(plan, {"Fix footer": script}, env={"CREW_GITHUB_TOKEN": "ghp_test"}, http=hub)
        run(start(w))
        assert "crew/main" in hub.branches and hub.branches["main"] == "base0"            # 'main' became crew/main; main untouched
        assert not any(p.endswith("/merge") for _, p, _ in hub.calls)                       # nothing merged before the Approve
        q = run(w.store.list_questions(UID, "open"))[0]
        assert q["kind"] == "checkpoint" and "deploys on merge" in q["text"]
        out = run(w.engine.answer(UID, q["id"], approve=True))
        assert "Merged crew/main into main" in out["message"]
        run(w.engine.run_until_idle())
        assert run(w.store.list_missions(UID, 5))[0]["status"] == "done"

    def test_forge_refuses_unknown_repos_and_workflows(self):
        from openjarvis.server.crew import github as gh

        with pytest.raises(gh.GitHubError):
            gh.check_repo("someone/else", {})
        with pytest.raises(gh.GitHubError):
            gh.check_path(".github/workflows/deploy.yml")
        with pytest.raises(gh.GitHubError):
            gh.check_path("../etc/passwd")
        assert gh.crew_branch("Main Fix!") == "crew/main-fix"

    def test_prism_without_media_says_so(self):
        plan = {"title": "Art", "project": "New", "tasks": [{"title": "Make art", "role": "designer", "brief": "Art", "depends_on": []}]}
        script = [resp(None, [("generate_image", {"prompt": "a neon card binder on a desk"})]), resp(None, [("finish", {"summary": "Prompts saved."})])]
        w = world(plan, {"Make art": script})
        run(start(w))
        call = [c for c in w.model.calls if c["tools"]][-1]
        assert any("not set up" in str(x.get("content")) for x in call["messages"] if x["role"] == "tool")

    def test_lens_sends_work_back_once_and_reviews_again(self):
        plan = two_task_plan()
        writes = {"n": 0}

        def writer(turn, messages):
            writes["n"] += 1
            return resp(None, [("finish", {"summary": f"Listing written v{writes['n']}"})])

        def lens(turn, messages):
            if turn == 0:
                return resp(None, [("request_changes", {"task_number": 2, "notes": "Add the price and the size."})])
            return resp(None, [("finish", {"summary": "Reviewed after fixes."})])

        w = world(plan, {"Research buyers": [resp(None, [("finish", {"summary": "Collectors."})])], "Write listing": writer,
                         "Review the crew's work": lens})
        m = run(start(w))
        tasks = run(w.store.list_tasks(m["id"]))
        assert m["status"] == "done" and writes["n"] == 2
        assert tasks[1]["result"] == "Listing written v2" and tasks[2]["result"] == "Reviewed after fixes."


# ── Herald: briefs made inside Aibussol, on schedule, pushed to the phone ──

class TestHerald:
    def _herald(self, now, notes=None):
        from openjarvis.server.briefs import MemoryBriefStore
        from openjarvis.server.crew.herald import Herald

        store = MemoryCrewStore(clock=lambda: now)
        engine = CrewEngine(store, Model({"tasks": []}, {}), env=dict(ENV), cost_fn=lambda r: 0.0, now=lambda: now,
                            models={"writing": "claude-x", "lookup": "gemini-x", "default": "gpt-x"})
        engine.products = {"flipdeck": {"live": True}, "deadzone": {"live": False}}

        async def owner(uid):
            return uid == UID

        engine.owner_fn = owner

        async def make_brief(period, n, env):
            return {"product": "FlipDeck and Marnyx", "period": period, "body_markdown": "Good morning, Michael.\n\n## FlipDeck\n- No deploys.\n\n## Act on first\nNothing urgent.",
                    "spoken_summary": "Good morning, Michael. Nothing urgent."}

        class Fin:
            async def report(self, uid, missions, keys):
                row = {"revenue_today": 12.0, "revenue_30d": 300.0, "cost_today": 1.0, "cost_30d": 20.0, "mrr": 250.0, "subscribers": 18,
                       "net_today": 11.0, "net_30d": 280.0}
                return {"total": dict(row), "factories": {"marnyx": dict(row)}, "stripe_ok": True, "notes": []}

        async def stats():
            return {"users_total": 212, "active_24h": 14, "new_users_7d": 9, "subscribers_active": 3}

        notes = notes if notes is not None else Notes()
        bs = MemoryBriefStore(clock=lambda: now)
        async def numbers(env):
            return ["- FlipDeck: 32 total users, 4 paying now."]

        h = Herald(engine, bs, notifier=notes, env={"HERALD_ENABLED": "true"}, now=lambda: now, make_brief=make_brief, finance=Fin(), stats_fn=stats,
                   numbers_fn=numbers)
        h._owner = UID
        engine.herald = h
        return h, bs, notes

    def test_makes_the_due_brief_once_and_pushes_it(self):
        slot = dt.datetime(2026, 10, 21, 13, 55, tzinfo=dt.timezone.utc)          # 06:55 in Los Angeles: the morning slot
        h, bs, notes = self._herald(slot)
        out = run(h.maybe_run())
        assert out["ok"] and out["period"] == "morning"
        assert run(h.maybe_run()) is None                                        # the same slot is never made twice
        body = bs.rows[0]["body"]
        assert "## At a glance" in body and "in $12.00, out $1.00, net $11.00" in body and "DOWN: Deadzone" in body and "212 users" in body and "32 total users" in body
        assert notes.sent and notes.sent[0][1] == "Morning brief" and "Money today" in notes.sent[0][2]

    def test_nothing_outside_the_slots_but_send_now_works(self):
        noon_ish = dt.datetime(2026, 10, 21, 17, 0, tzinfo=dt.timezone.utc)      # 10:00 in Los Angeles: no slot
        h, bs, notes = self._herald(noon_ish)
        assert run(h.maybe_run()) is None and not bs.rows
        out = run(h.run())
        assert out["ok"] and bs.rows and h.status()["last"]["ok"]
        roster = run(h.engine.state(UID))["roster"]
        assert any(a["name"] == "Herald" and "next brief" in a["status_text"] for a in roster)


def test_product_stats_say_which_database_is_missing():
    from openjarvis.server.crew.product_stats import ProductStats

    out = run(ProductStats(pool=None, env={}).all())
    assert set(out) == {"aibussol", "flipdeck", "marnyx", "deadzone"}
    assert not out["flipdeck"]["connected"] and "FLIPDECK_READ_DATABASE_URL" in out["flipdeck"]["note"]


# ── the Workbench: Rig's jobs run on Michael's PC ──

class TestWorkbench:
    def test_pairing_tokens_name_their_account_and_can_be_replaced(self):
        from openjarvis.server.crew import workbench

        store = MemoryCrewStore()
        tok = run(workbench.pair(store, UID))
        assert tok.startswith("wb." + UID + ".") and run(workbench.verify(store, tok)) == UID
        assert run(workbench.verify(store, tok[:-2] + "xx")) is None and run(workbench.verify(store, "nonsense")) is None
        tok2 = run(workbench.pair(store, UID))
        assert run(workbench.verify(store, tok)) is None and run(workbench.verify(store, tok2)) == UID
        run(workbench.unpair(store, UID))
        assert run(workbench.verify(store, tok2)) is None

    def test_rig_jobs_wait_for_the_pc_and_finish_from_it(self):
        plan = {"title": "Runner level", "project": "Deadzone", "tasks": [
            {"title": "Design the level", "role": "writer", "brief": "Design", "depends_on": []},
            {"title": "Build the level in Unity", "role": "workbench", "brief": "Implement and test", "depends_on": [0]}]}
        w = world(plan, {"Design the level": [resp(None, [("finish", {"summary": "Level design ready."})])]})
        m = run(start(w))
        tasks = run(w.store.list_tasks(m["id"]))
        assert tasks[1]["status"] == "queued" and m["status"] == "active"           # the server never runs Rig's job itself
        job = run(w.engine.workbench_next(UID))
        assert job and job["project"] == "Deadzone" and "Level design ready." in job["results_so_far"] and "Unity" in job["house_rules"]
        assert run(w.engine.workbench_next(UID)) is None                           # one claim per job
        assert run(w.engine.workbench_next("someone-else")) is None
        run(w.engine.workbench_progress(UID, job["task_id"], "Still working (2 min)."))
        run(w.engine.workbench_done(UID, job["task_id"], True, "Built and play-tested with a controller.", 12))
        tasks = run(w.store.list_tasks(m["id"]))
        assert tasks[1]["status"] == "done"
        outs = [o["name"] for o in run(w.store.list_outputs(m["id"]))]
        assert any(n.startswith("rig-") for n in outs)
        with pytest.raises(ValueError):
            run(w.engine.workbench_done(UID, job["task_id"], True, "again"))

    def test_presence_shows_the_pc_online(self):
        from openjarvis.server.crew.workbench import Presence

        t = [100.0]
        p = Presence(clock=lambda: t[0])
        p.hello(UID, {"host": "MIKE-PC", "projects": ["Deadzone"], "claude": True, "unity": True})
        assert p.status(UID)["online"] and p.status(UID)["host"] == "MIKE-PC"
        t[0] += 1000
        assert not p.status(UID)["online"]


# ── subscribers: their own Station, their own keys, never the owner's ──

class FakePool:
    def __init__(self, customer="cus_sub"):
        self.customer = customer

    def acquire(self):
        pool = self

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def fetchrow(self, sql, *args):
                return {"stripe_customer_id": pool.customer}
        return Conn()


class TestSubscribers:
    OWNER_ENV = {"STRIPE_SECRET_KEY": "sk_live_OWNER", "CREW_GITHUB_TOKEN": "ghp_OWNER", "RENDER_API_KEY": "rnd_OWNER",
                 "CREW_HOUSE_RULES": "Michael's rules", "FLIPDECK_READ_DATABASE_URL": "postgres://owner"}

    def test_a_subscribers_crew_never_sees_the_owners_keys(self):
        w = world(two_task_plan(), {}, env=self.OWNER_ENV)
        mine = run(w.engine.env_for(UID))
        theirs = run(w.engine.env_for("sub-1"))
        assert mine["STRIPE_SECRET_KEY"] == "sk_live_OWNER"
        assert "OWNER" not in json.dumps(theirs) and "postgres://owner" not in json.dumps(theirs) and "Michael" not in json.dumps(theirs)
        assert theirs["STRIPE_SECRET_KEY"] == "" and theirs["CREW_GITHUB_REPOS"] == "" and theirs["CREW_ENABLED"] == "true"
        from openjarvis.server.crew import github

        assert github.repos(theirs) == [] and not github.configured(theirs)

    def test_guided_stripe_is_tested_then_stored_encrypted_and_used(self):
        from openjarvis.server.crew import guide, tenant

        w = world(two_task_plan(), {}, env=self.OWNER_ENV, http=FakeHttp())
        with pytest.raises(guide.GuideError):
            run(guide.test_and_save(w.store, "sub-1", "stripe", "not-a-key", client_factory=w.http))
        msg = run(guide.test_and_save(w.store, "sub-1", "stripe", "rk_test_abcdefghijklmnop", client_factory=w.http))
        assert "test mode" in msg and w.http.requests[-1][1] == "https://api.stripe.com/v1/products"
        raw = run(w.store.get_secret("sub-1", "stripe_key"))
        assert raw.startswith("enc1:") and "abcdefghijklmnop" not in raw
        assert run(tenant.get(w.store, "sub-1", "stripe_key")) == "rk_test_abcdefghijklmnop"
        assert run(w.engine.env_for("sub-1"))["STRIPE_SECRET_KEY"] == "rk_test_abcdefghijklmnop"
        run(guide.disconnect(w.store, "sub-1", "stripe"))
        assert run(w.engine.env_for("sub-1"))["STRIPE_SECRET_KEY"] == ""

    def test_phone_and_github_are_checked(self):
        from openjarvis.server.crew import guide

        http = FakeHttp()
        store = MemoryCrewStore()
        with pytest.raises(guide.GuideError):
            run(guide.test_and_save(store, "sub-1", "phone", "https://evil.example/x", client_factory=http))
        run(guide.test_and_save(store, "sub-1", "phone", "https://ntfy.sh/aibussol-1234abcd", client_factory=http))
        assert http.requests[-1][:2] == ("POST", "https://ntfy.sh/aibussol-1234abcd")
        with pytest.raises(guide.GuideError):
            run(guide.test_and_save(store, "sub-1", "github", "github_pat_" + "x" * 20, "not a repo", client_factory=http))
        run(guide.test_and_save(store, "sub-1", "github", "github_pat_" + "x" * 20, "me/site, me/app", client_factory=http))
        assert run(store.get_secret("sub-1", "github_repos")) == "me/site,me/app"

    def test_businesses_make_the_factories(self):
        from openjarvis.server.crew import tenant

        store = MemoryCrewStore()
        items = run(tenant.add_business(store, "sub-1", "Bloom Candles", "bloomcandles.com"))
        assert items == [{"key": "bloomcandles", "name": "Bloom Candles", "url": "https://bloomcandles.com", "color": tenant.PALETTE[0]}]
        with pytest.raises(ValueError):
            run(tenant.add_business(store, "sub-1", "bloom candles"))
        run(tenant.add_business(store, "sub-1", "Shop Two"))
        assert [b["key"] for b in run(tenant.remove_business(store, "sub-1", "bloomcandles"))] == ["shoptwo"]
        assert run(tenant.businesses(store, "someone-else")) == []

    def test_a_subscribers_pnl_has_only_their_own_numbers(self):
        from openjarvis.server.crew.finance import Finance

        store = MemoryCrewStore()
        fin = Finance(store, env={"STRIPE_SECRET_KEY": "", "CREW_STRIPE_UNTAGGED": "bloom"}, now=lambda: NOON_LA)
        out = run(fin.report("sub-1", [], ["bloom"], owner=False))
        assert "aibussol" not in out["factories"] and "bloom" in out["factories"]
        assert not any("CREW_FIXED_COSTS" in n or "FlipDeck" in n for n in out["notes"])


class TestUsage:
    ENV = {"STRIPE_SECRET_KEY": "sk_test_platform", "STRIPE_PRICE_USAGE": "price_meter", "CREW_PLAN_INCLUDED_USD": "5",
           "CREW_OVERAGE_MULTIPLIER": "2", "CREW_MONTHLY_HARD_CAP_USD": "20"}

    def test_the_plan_includes_work_then_bills_extra_with_a_cap(self):
        from openjarvis.server.crew import usage

        assert usage.billable_cents(4.99, self.ENV) == 0
        assert usage.billable_cents(6.0, self.ENV) == 200                   # $1 of crew cost past the plan bills $2
        assert usage.billable_cents(100.0, self.ENV) == 2000                # never past the monthly cap
        assert usage.room_usd(0, self.ENV) == 15.0                          # $5 included + $20 cap at 2x = $10 more
        no_payg = dict(self.ENV, STRIPE_PRICE_USAGE="")
        assert usage.billable_cents(6.0, no_payg) == 0 and usage.room_usd(4.0, no_payg) == 1.0
        s = usage.summary(6.0, self.ENV)
        assert s["included_pct"] == 100.0 and s["extra_billed_usd"] == 2.0 and s["payg"]

    def test_extra_usage_is_reported_once_per_cent(self):
        from openjarvis.server.crew import usage

        store, http = MemoryCrewStore(), FakeHttp()
        sent = run(usage.report(store, FakePool(), "sub-1", 6.0, NOON_LA, self.ENV, http))
        assert sent == 200
        method, url, kw = http.requests[-1]
        assert url == "https://api.stripe.com/v1/billing/meter_events" and kw["data"]["payload[stripe_customer_id]"] == "cus_sub"
        assert kw["data"]["payload[value]"] == "200" and kw["data"]["identifier"] == "sub-1-2026-10-200"
        assert run(usage.report(store, FakePool(), "sub-1", 6.0, NOON_LA, self.ENV, http)) == 0       # nothing new
        assert run(usage.report(store, FakePool(), "sub-1", 6.5, NOON_LA, self.ENV, http)) == 100     # only the new cents

    def test_a_subscriber_stops_at_the_month_and_the_owner_never_does(self):
        w = world(two_task_plan(), {"Research buyers": [resp("r")], "Write listing": [resp("w")]},
                  env={"CREW_PLAN_INCLUDED_USD": "0.02", "CREW_DAILY_CAP_USD": "50"})
        run(w.store.add_day_spend("sub-1", "2026-10-02", 0.02))
        assert run(w.engine.may_run("sub-1")) == "monthly usage reached"
        assert any("included crew work is used up" in e["text"] for e in w.store.events)
        run(w.store.add_day_spend(UID, "2026-10-02", 500))
        assert run(w.engine.may_run(UID)) is None and run(w.engine.usage(UID)) is None
        assert run(w.engine.usage("sub-1"))["included_pct"] == 100.0


# ── the specialists: an agent for every kind of request ──

class TestSpecialists:
    def names(self, role):
        return [t["function"]["name"] for t in eng_mod.tools_for(role)]

    def test_every_specialist_has_the_right_hands_and_no_more(self):
        for role in ("marketer", "social", "support", "analyst", "qa", "finance"):
            assert role in eng_mod.ROLES and "web_search" in self.names(role)
            assert "repo_commit" not in self.names(role) and "request_merge" not in self.names(role)
        assert "generate_image" in self.names("marketer") and "generate_image" in self.names("social")
        assert "business_numbers" in self.names("analyst") and "business_numbers" in self.names("finance")
        assert "check_site" in self.names("qa") and "repo_read" in self.names("qa")
        assert "use_connection" in self.names("support") and "use_connection" not in self.names("qa")
        w = world(two_task_plan(), {})
        prompt = w.engine._planner_prompt("")
        assert all(r in prompt for r in ("marketer", "social", "support", "analyst", "qa", "finance"))

    def test_readers_cannot_run_risky_actions(self):
        def sage(turn, msgs):
            if turn == 0:
                return resp(calls=[("use_connection", {"platform": "stripe", "action": "create_product", "args": {"name": "x", "price_usd": 5}})])
            assert "can only read" in json.dumps(msgs)
            return resp(calls=[("finish", {"summary": "ok"})])
        w = world({"tasks": [{"title": "Check pricing", "role": "analyst", "brief": "b"}]}, {"Check pricing": sage},
                  env={"STRIPE_SECRET_KEY": "sk_test_x"}, http=FakeHttp())
        run(start(w))
        assert not run(w.store.list_questions(UID)) and not [r for r in w.http.requests if r[0] == "POST"]

    def test_probe_tests_only_the_accounts_own_sites(self):
        class Http(FakeHttp):
            async def get(self, url, **kw):
                self.requests.append(("GET", url, kw))
                return httpx.Response(200, text="<html><title>Bloom Candles</title><body>Hand-poured</body></html>",
                                      headers={"content-type": "text/html"})
        http = Http()
        w = world(two_task_plan(), {}, http=http)
        from openjarvis.server.crew import tenant

        run(tenant.add_business(w.store, "sub-1", "Bloom Candles", "https://bloomcandles.com"))
        task = {"user_id": "sub-1", "mission_id": "m", "id": "t"}
        out = run(w.engine._check_site(task, {"url": "https://bloomcandles.com/shop"}))
        assert "status 200" in out and "Bloom Candles" in out and "Hand-poured" in out
        assert "Not allowed" in run(w.engine._check_site(task, {"url": "https://flipdeckcards.com"}))       # the owner's site
        assert "Not allowed" in run(w.engine._check_site(task, {"url": "http://169.254.169.254/latest"}))
        assert len(http.requests) == 1

    def test_sage_reads_only_the_subscribers_own_numbers(self):
        w = world(two_task_plan(), {}, env={"STRIPE_SECRET_KEY": "sk_live_OWNER", "CREW_FIXED_COSTS": "flipdeck=10"}, http=FakeHttp())
        from openjarvis.server.crew import tenant

        run(tenant.add_business(w.store, "sub-1", "Bloom Candles"))
        out = run(w.engine._numbers({"user_id": "sub-1", "mission_id": "m", "id": "t"}))
        assert "bloomcandles" in out and "flipdeck" not in out and "aibussol" not in out
        assert not any("sk_live_OWNER" in json.dumps(r[2]) for r in w.http.requests)


def test_rig_works_in_the_pc_project_the_mission_names_not_the_factory():
    from openjarvis.server.crew.engine import pc_project

    names = ["Deadzone", "MergeDungeon", "DeadzoneSite"]
    m2 = {"project": "Deadzone", "goal": "Start a mission: in project DeadzoneSite, have Rig add Merge Dungeon to the Deadzone Games site. "
                                         "Copy C:\\UnityProjects\\MergeDungeon\\Builds\\WebGL into public/mergedungeon"}
    assert pc_project(names, {"title": "Set up Merge Dungeon on Deadzone site", "brief": "Copy the build"}, m2) == "DeadzoneSite"
    m1 = {"project": "Deadzone", "goal": "in project MergeDungeon, have Rig make a WebGL build"}
    assert pc_project(names, {"title": "WebGL build", "brief": "Build it"}, m1) == "MergeDungeon"
    assert pc_project(names, {"title": "Fix the jump", "brief": "In project Deadzone, fix the jump"}, m1) == "Deadzone"
    assert pc_project([], {"title": "x", "brief": "y"}, {"project": "Deadzone", "goal": "z"}) == "Deadzone"


def test_a_task_moves_to_the_writing_model_when_the_lookup_provider_is_out_of_quota():
    calls = []

    class Flaky(Model):
        async def __call__(self, model, messages, max_tokens=None, tools=None):
            calls.append(model)
            if tools and model == "gemini-x":
                raise RuntimeError("RateLimitError: 429 You exceeded your current quota")
            return await super().__call__(model, messages, max_tokens, tools)

    w = world({"tasks": [{"title": "Research buyers", "role": "researcher", "brief": "b"}]},
              {"Research buyers": [resp(None, [("finish", {"summary": "Found them."})])]})
    w.engine.acompletion = Flaky(w.model.plan, w.model.agents)
    m = run(start(w))
    assert run(w.store.list_tasks(m["id"]))[0]["status"] == "done" and "gemini-x" in calls and "claude-x" in calls


# ── Marnyx: Echo's week and Spark's email, each one approval ──

class MarnyxHttp(FakeHttp):
    BIZ = {"businesses": [{"id": 4, "name": "FlipDeck", "connections": [{"platform": "instagram", "status": "active"}, {"platform": "facebook", "status": "active"}],
                           "subscribed_contacts": 12, "mailing_address_set": True},
                          {"id": 5, "name": "Marnyx", "connections": [], "subscribed_contacts": 0, "mailing_address_set": False}]}

    async def get(self, url, **kw):
        self.requests.append(("GET", url, kw))
        if url.endswith("/businesses"):
            return httpx.Response(200, json=self.BIZ)
        return httpx.Response(200, json={"posts": []})

    async def post(self, url, **kw):
        self.requests.append(("POST", url, kw))
        if url.endswith("/posts"):
            return httpx.Response(201, json={"created": len(kw["json"]["posts"]), "results": [{"index": i, "id": 100 + i} for i in range(len(kw["json"]["posts"]))]})
        return httpx.Response(201, json={"id": 9, "status": "queued", "recipients": 12})


def _week(n=2):
    base = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    return [{"body": f"Post {i}: flip smarter.", "hashtags": "#cards", "platforms": ["instagram", "facebook"],
             "scheduled_at": (base + dt.timedelta(days=i)).isoformat()} for i in range(n)]


class TestMarnyx:
    def test_the_week_reads_as_one_approval_and_runs_only_when_approved(self):
        posts = _week(3)

        def echo(turn, msgs):
            if turn == 0:
                return resp(calls=[("use_connection", {"platform": "marnyx", "action": "schedule_week", "args": {"business": "FlipDeck", "posts": posts}})])
            return resp(calls=[("finish", {"summary": "Week sent."})])
        http = MarnyxHttp()
        w = world({"tasks": [{"title": "Plan the FlipDeck week", "role": "social", "brief": "b"}]}, {"Plan the FlipDeck week": echo},
                  env={"MARNYX_API_KEY": "mx_" + "a" * 40}, http=http)
        m = run(start(w))
        q = run(w.store.list_questions(UID))
        assert len(q) == 1 and q[0]["kind"] == "checkpoint" and "Schedule 3 post(s) for FlipDeck" in q[0]["text"] and "Instagram, Facebook" in q[0]["text"]
        assert not [r for r in http.requests if r[0] == "POST"]                     # nothing sent before Approve
        run(w.engine.answer(UID, q[0]["id"], approve=True))
        run(w.engine.run_until_idle())
        sent = [r for r in http.requests if r[0] == "POST"]
        assert len(sent) == 1 and sent[0][1].endswith("/businesses/4/posts") and len(sent[0][2]["json"]["posts"]) == 3
        assert sent[0][2]["headers"]["Authorization"] == "Bearer mx_" + "a" * 40
        assert run(w.store.get_mission(m["id"]))["status"] == "done"

    def test_other_crew_cannot_post_and_echo_cannot_charge(self):
        def sage(turn, msgs):
            if turn == 0:
                return resp(calls=[("use_connection", {"platform": "marnyx", "action": "send_email", "args": {"business": "FlipDeck", "subject": "x", "body_text": "y"}})])
            assert "can only read" in json.dumps(msgs)
            return resp(calls=[("use_connection", {"platform": "marnyx", "action": "list_accounts", "args": {}})]) if turn == 1 else resp(calls=[("finish", {"summary": "ok"})])
        http = MarnyxHttp()
        w = world({"tasks": [{"title": "Numbers", "role": "analyst", "brief": "b"}]}, {"Numbers": sage}, env={"MARNYX_API_KEY": "mx_" + "a" * 40}, http=http)
        run(start(w))
        assert not run(w.store.list_questions(UID)) and not [r for r in http.requests if r[0] == "POST"]
        assert "use_connection" in [t["function"]["name"] for t in eng_mod.tools_for("social")]

        def echo(turn, msgs):
            if turn == 0:
                return resp(calls=[("use_connection", {"platform": "stripe", "action": "create_product", "args": {"name": "x", "price_usd": 5}})])
            assert "can only read" in json.dumps(msgs)
            return resp(calls=[("finish", {"summary": "ok"})])
        w2 = world({"tasks": [{"title": "Posts", "role": "social", "brief": "b"}]}, {"Posts": echo}, env={"STRIPE_SECRET_KEY": "sk_test_x"}, http=FakeHttp())
        run(start(w2))
        assert not run(w2.store.list_questions(UID))

    def test_bad_weeks_are_refused_before_they_reach_michael(self):
        from openjarvis.server.crew import connections as conns

        with pytest.raises(conns.ConnectionError_):
            conns.describe_action("marnyx", "schedule_week", {"business": "FlipDeck", "posts": [{"body": "x", "platforms": ["myspace"], "scheduled_at": _week(1)[0]["scheduled_at"]}]})
        with pytest.raises(conns.ConnectionError_):
            conns.describe_action("marnyx", "schedule_week", {"business": "FlipDeck", "posts": [{"body": "x", "platforms": ["x"], "scheduled_at": "2020-01-01T09:00:00-07:00"}]})
        text = conns.describe_action("marnyx", "send_email", {"business": "FlipDeck", "subject": "New sets", "body_text": "Hello collectors"})
        assert 'Subject: "New sets"' in text and "Hello collectors" in text

    def test_the_weekly_plan_starts_once_a_week_while_marnyx_is_connected(self):
        from openjarvis.server.crew.weekly import WeeklySocial

        sunday_6pm = dt.datetime(2026, 10, 26, 1, 0, tzinfo=dt.timezone.utc)      # Sun Oct 25, 6 PM in Los Angeles
        w = world({"tasks": [{"title": "Plan", "role": "social", "brief": "b"}]}, {"Plan": [resp(calls=[("finish", {"summary": "ok"})])]},
                  env={"MARNYX_API_KEY": "mx_" + "a" * 40}, now=sunday_6pm)

        class H:
            async def owner_id(self):
                return UID
        wk = WeeklySocial(w.engine, H(), env={}, now=lambda: sunday_6pm)
        mid = run(wk.maybe_run())
        assert mid and "October 26" in run(w.store.get_mission(mid))["goal"]
        assert run(wk.maybe_run()) is None                                          # once per week
        early = WeeklySocial(w.engine, H(), env={}, now=lambda: sunday_6pm - dt.timedelta(days=1))          # Saturday: not yet
        assert run(early.maybe_run()) is None
        off = world({"tasks": []}, {}, env={}, now=sunday_6pm)
        assert run(WeeklySocial(off.engine, H(), env={}, now=lambda: sunday_6pm).maybe_run()) is None   # Marnyx not connected


def test_a_task_out_of_steps_wraps_up_instead_of_failing():
    def lens(turn, msgs):
        if any("You are out of steps" in str(m.get("content")) for m in msgs if m["role"] == "user"):
            return resp(calls=[("save_output", {"name": "review.md", "content": "All good except pricing."}), ("finish", {"summary": "Reviewed 6 files."})])
        return resp(calls=[("read_output", {"name": "nothing.md"})])
    w = world({"tasks": [{"title": "Review", "role": "reviewer", "brief": "b"}]}, {"Review": lens}, env={"CREW_DAILY_CAP_USD": "50"})
    m = run(start(w, budget=5))
    for _ in range(eng_mod.MAX_RUNS + 1):
        run(w.engine.run_until_idle())
    t = run(w.store.list_tasks(m["id"]))[0]
    assert t["status"] == "done" and "Wrapped up" in t["result"] and "Reviewed 6 files." in t["result"]
    assert [o["name"] for o in run(w.store.list_outputs(m["id"]))] == ["review.md"]

    w2 = world({"tasks": [{"title": "Review", "role": "reviewer", "brief": "b"}]},
               {"Review": lambda turn, msgs: resp(calls=[("read_output", {"name": "x"})])}, env={"CREW_DAILY_CAP_USD": "50"})
    m2 = run(start(w2, budget=5))
    for _ in range(eng_mod.MAX_RUNS + 1):
        run(w2.engine.run_until_idle())
    assert run(w2.store.list_tasks(m2["id"]))[0]["status"] == "failed"                 # still fails if it will not finish


def test_delivery_is_set_only_for_files_in_the_missions_outbox():
    from openjarvis.server.crew import connections as conns
    from openjarvis.server.crew.routes import _delivered_name

    store, http = MemoryCrewStore(), FakeHttp({("POST", "https://api.stripe.com/v1/payment_links/plink_abc"): {"url": "https://buy.stripe.com/x"}})
    m = run(store.create_mission(UID, "Sell a template pack", 1.0))
    run(store.add_output(m["id"], "t1", UID, "Quick_Start_Guide.md", "# Start here"))
    env = {"STRIPE_SECRET_KEY": "sk_test_x"}
    with pytest.raises(conns.ConnectionError_):
        run(conns.run("stripe", "set_delivery", {"payment_link_id": "plink_abc", "deliver_files": ["secret_brief.txt"]},
                      store=store, user_id=UID, mission_id=m["id"], env=env, client_factory=http))
    out = run(conns.run("stripe", "set_delivery", {"payment_link_id": "plink_abc", "deliver_files": ["Quick_Start_Guide.md"]},
                        store=store, user_id=UID, mission_id=m["id"], env=env, client_factory=http))
    assert "Automatic delivery is on" in out
    form = http.requests[-1][2]["data"]
    assert form["after_completion[type]"] == "redirect" and "{CHECKOUT_SESSION_ID}" in form["after_completion[redirect][url]"]
    assert form["metadata[deliver_files]"] == "Quick_Start_Guide.md" and form["metadata[mission]"] == m["id"]
    assert conns.risk_of("stripe", "set_delivery") == "checkpoint"
    assert _delivered_name("Guide.pdf", "# not a pdf") == "Guide.txt" and _delivered_name("Guide.pdf", "%PDF-1.7") == "Guide.pdf"


def test_resume_retries_failed_tasks_with_fresh_steps():
    w = world({"tasks": [{"title": "Review", "role": "reviewer", "brief": "b"}]},
              {"Review": lambda turn, msgs: resp(calls=[("finish", {"summary": "Done on retry."})])})
    m = run(start(w))
    t = run(w.store.list_tasks(m["id"]))[0]
    run(w.store.update_task(t["id"], status="failed", runs=4, result="Stopped: it used all its steps without finishing."))
    run(w.store.update_mission(m["id"], status="stalled"))
    run(w.engine.resume_mission(UID, m["id"]))
    run(w.engine.run_until_idle())
    t = run(w.store.list_tasks(m["id"]))[0]
    assert t["status"] == "done" and "Done on retry." in t["result"] and run(w.store.get_mission(m["id"]))["status"] == "done"


def test_a_new_game_gets_its_own_pc_project():
    from openjarvis.server.crew.engine import pc_project

    m = {"project": "Deadzone", "goal": "Build a trending mobile game"}
    assert pc_project(["Deadzone", "MergeDungeon"], {"title": "Build it", "brief": "In new project NeonDrift, build the game in Unity"}, m) == "NeonDrift"
    assert pc_project(["Deadzone", "MergeDungeon"], {"title": "Build it", "brief": "In project MergeDungeon, fix the menu"}, m) == "MergeDungeon"


def test_a_finished_mission_gets_helix_report():
    class Reporter(Model):
        async def __call__(self, model, messages, max_tokens=None, tools=None):
            if not tools and "mission report" in json.dumps(messages):
                self.calls.append({"model": model, "messages": messages, "tools": tools})
                return resp("## What was done\nScout found buyers.\n## What is live now\nNothing is live.")
            return await super().__call__(model, messages, max_tokens, tools)
    w = world(two_task_plan(), {"Research buyers": [resp(calls=[("finish", {"summary": "Found buyers."})])],
                                "Write listing": [resp(calls=[("finish", {"summary": "Listing written."})])]}, env={"CREW_MISSION_REPORTS": "on"})
    w.engine.acompletion = Reporter(w.model.plan, w.model.agents)
    m = run(start(w))
    m = run(w.store.get_mission(m["id"]))
    assert m["status"] == "done" and "Nothing is live." in m["summary"]
    assert "mission_report.md" in [o["name"] for o in run(w.store.list_outputs(m["id"]))]
    assert any("Mission complete" in n[1] and "What was done" in n[2] for n in w.notes.sent)


class TestEtsyEndToEnd:
    def _setup(self):
        import time as _t

        store = MemoryCrewStore()

        class Http(FakeHttp):
            async def patch(self, url, **kw):
                self.requests.append(("PATCH", url, kw))
                return httpx.Response(200, json={"listing_id": int(url.rsplit("/", 1)[1]), "state": "active"})

        http = Http({("POST", f"{conns.ETSY_API}/shops/77/listings/5/images"): {"listing_image_id": 9},
                     ("POST", f"{conns.ETSY_API}/shops/77/listings/5/files"): {"listing_file_id": 11}})

        async def prep():
            for k, v in (("etsy_shop_id", "77"), ("etsy_access", "tok"), ("etsy_refresh", "r"), ("etsy_expires", str(int(_t.time()) + 3600))):
                await store.set_secret(UID, k, v)
            m = await store.create_mission(UID, "Etsy planners", 2.0)
            await store.add_output(m["id"], "t1", UID, "planner.md", "# Weekly Planner\n\n- Monday\n- Tuesday\n")
            return m["id"]
        return store, http, run(prep())

    def test_photo_file_and_publish(self):
        store, http, mid = self._setup()
        env = {"ETSY_API_KEY": "k", "ETSY_SHARED_SECRET": "s", "ETSY_REDIRECT_URI": "https://x/cb"}

        async def fetch(item_id):
            assert item_id == "img1"
            return b"\x89PNG...", "image/png"
        kw = dict(store=store, user_id=UID, mission_id=mid, env=env, client_factory=http, media_fetch=fetch)
        out = run(conns.run("etsy", "add_listing_image", {"listing_id": "5", "media_item_id": "img1"}, **kw))
        assert "Photo added" in out and http.requests[-1][2]["files"]["image"][2] == "image/png"
        out = run(conns.run("etsy", "add_listing_file", {"listing_id": 5, "file": "planner.md"}, **kw))
        sent = http.requests[-1][2]["files"]["file"]
        assert sent[0] == "planner.pdf" and sent[1].startswith(b"%PDF") and "attached" in out
        try:
            run(conns.run("etsy", "add_listing_file", {"listing_id": 5, "file": "nope.md"}, **kw))
            raise AssertionError("should refuse files outside the outbox")
        except conns.ConnectionError_:
            pass
        assert conns.risk_of("etsy", "publish_listings") == "checkpoint"
        assert "$0.40" in conns.describe_action("etsy", "publish_listings", {"listing_ids": [5, 6]})
        out = run(conns.run("etsy", "publish_listings", {"listing_ids": [5, 6]}, **kw))
        assert "Published on Etsy: 5, 6" in out and http.requests[-1][2]["data"] == {"state": "active"}


def test_pdf_maker_makes_a_pdf():
    from openjarvis.server.crew.pdf import make_pdf

    data = make_pdf("Planner", "# Week\n\n- “Monday” — plan\n1. Two\n\n" + "word " * 2000)
    assert data.startswith(b"%PDF") and data.rstrip().endswith(b"%%EOF") and b"/Count" in data


def test_factory_catalogs():
    from openjarvis.server.crew import catalog

    page = ('<div class="card live" onclick="window.location.href=\'/feral/\'"><img src="x"><div class="card-info">'
            '<div class="card-title">Feral</div><div class="card-status">▶ Play Now</div></div></div>'
            '<div class="card locked"><div class="lock-icon">L</div><div class="card-title">Galactic</div><div class="card-status">Coming Soon</div></div>')
    games = catalog.parse_games(page, "https://dz.example/")
    assert [(g["name"], g["status"], g["url"]) for g in games] == [("Feral", "playable", "https://dz.example/feral/"), ("Galactic", "coming soon", "")]
    store = MemoryCrewStore()
    out = run(catalog.manual_edit(store, UID, "google_play", add={"name": "Deadzone Run", "url": "https://play.google.com/x"}))
    assert out["items"][0]["name"] == "Deadzone Run" and out["editable"]
    assert run(catalog.manual_edit(store, UID, "google_play", remove="Deadzone Run"))["items"] == []
    run(store.set_secret(UID, "etsy_shop_id", "77"))
    run(store.set_secret(UID, "etsy_access", "tok"))
    run(store.set_secret(UID, "etsy_refresh", "r"))
    import time as _t
    run(store.set_secret(UID, "etsy_expires", str(int(_t.time()) + 3600)))

    class Http(FakeHttp):
        async def get(self, url, **kw):
            self.requests.append(("GET", url, kw))
            st = kw["params"]["state"]
            return httpx.Response(200, json={"results": [{"listing_id": 5 if st == "active" else 6, "title": "Planner " + st,
                                                          "price": {"amount": 499, "divisor": 100, "currency_code": "USD"}}]})
    env = {"ETSY_API_KEY": "k", "ETSY_REDIRECT_URI": "https://x/cb"}
    out = run(catalog.for_factory("etsy", store=store, uid=UID, env=env, cf=Http(), businesses=[]))
    assert [(i["name"], i["status"], i["price"]) for i in out["items"]] == [("Planner active", "for sale", "4.99 USD"), ("Planner draft", "draft", "4.99 USD")]
    assert "1 for sale" in out["note"]


def test_etsy_finds_a_shop_opened_after_connecting():
    import time as _t

    store = MemoryCrewStore()
    for k, v in (("etsy_access", "123.tok"), ("etsy_refresh", "r"), ("etsy_expires", str(int(_t.time()) + 3600)), ("etsy_shop_note", "no shop")):
        run(store.set_secret(UID, k, v))
    http = FakeHttp({("GET", f"{conns.ETSY_API}/users/123/shops"): {"shop_id": 77, "shop_name": "DZ"},
                     ("GET", f"{conns.ETSY_API}/shops/77"): {"shop_name": "DZ", "listing_active_count": 0}})
    env = {"ETSY_API_KEY": "k", "ETSY_REDIRECT_URI": "https://x/cb"}
    out = run(conns.run("etsy", "get_shop", {}, store=store, user_id=UID, env=env, client_factory=http))
    assert 'Etsy shop "DZ"' in out and run(store.get_secret(UID, "etsy_shop_id")) == "77" and not run(store.get_secret(UID, "etsy_shop_note"))
    empty = FakeHttp({("GET", f"{conns.ETSY_API}/users/9/shops"): {"count": 0, "results": []}})
    store2 = MemoryCrewStore()
    for k, v in (("etsy_access", "9.tok"), ("etsy_refresh", "r"), ("etsy_expires", str(int(_t.time()) + 3600))):
        run(store2.set_secret(UID, k, v))
    try:
        run(conns.run("etsy", "get_shop", {}, store=store2, user_id=UID, env=env, client_factory=empty))
        raise AssertionError("no shop should refuse")
    except conns.ConnectionError_ as exc:
        assert "etsy.com/sell" in str(exc)


def test_idle_loop_sleeps_long_and_wakes_on_new_work():
    w = world(two_task_plan(), {}, env={"CREW_IDLE_TICK_SECONDS": "600"})
    eng = w["engine"] if isinstance(w, dict) else w.engine
    eng._idle_ticks = 5
    assert eng._sleep_for() == 600
    eng.poke()
    assert eng._idle_ticks == 0 and eng._wake.is_set() and eng._sleep_for() < 600


def test_etsy_file_found_in_another_mission():
    store = MemoryCrewStore()
    a = run(store.create_mission(UID, "Launch 3 Etsy", 1.0))
    b = run(store.create_mission(UID, "Complete FlipDeckStudio", 1.0))
    run(store.add_output(a["id"], "t1", UID, "reels_pack.md", "# 90 Days of Instagram Reels Ideas\n\n- Day 1\n"))
    from openjarvis.server.crew.connections import _find_output
    hit = run(_find_output(store, UID, b["id"], "reels_pack.md"))
    assert hit and "Reels" in hit["content"]
    assert run(_find_output(store, UID, b["id"], "missing.md")) is None


def test_trace_finds_outlines_and_holes():
    from PIL import Image, ImageDraw

    from openjarvis.server.crew import vector

    img = Image.new("RGB", (300, 300), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([30, 30, 270, 270], fill="black")
    d.ellipse([90, 90, 210, 210], fill="white")            # a hole in the square
    outlines, w, h = vector.trace(vector.prepare_bw(img))
    assert (w, h) == (300, 300)
    assert len(outlines) == 2                               # the square and its hole
    svg = vector.to_svg(outlines, w, h)
    assert 'fill-rule="evenodd"' in svg and svg.count("C") > 8
    dxf = vector.to_dxf(outlines, w, h)
    assert dxf.count("LWPOLYLINE") == 2 and dxf.strip().endswith("EOF")


def test_design_pack_makes_every_file():
    import io
    import zipfile

    from PIL import Image, ImageDraw

    from openjarvis.server.crew import designs

    art = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    ImageDraw.Draw(art).ellipse([40, 40, 360, 360], fill=(200, 30, 30, 255))
    buf = io.BytesIO()
    art.save(buf, "PNG")
    v = designs.make_versions(buf.getvalue())
    assert v["shapes"] >= 1 and v["svg"].startswith("<svg") and "LWPOLYLINE" in v["dxf"]
    assert v["color_png"][:4] == b"\x89PNG" and v["color_jpg"][:2] == b"\xff\xd8" and v["bw_jpg"][:2] == b"\xff\xd8"
    z = zipfile.ZipFile(io.BytesIO(designs.pack_zip({"color/a.png": v["color_png"], "bw/a.svg": v["svg"].encode()})))
    assert sorted(z.namelist()) == ["README.txt", "bw/a.svg", "color/a.png"]
