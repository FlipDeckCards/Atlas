"""The crew engine, offline: planning, parallel tasks, questions through Helix, checkpoints, budgets, the daily cap, quiet hours,
the Helix confirm-before-start rule. A scripted fake model plays the planner and the agents; a fake HTTP client plays Stripe."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import httpx
import pytest

from openjarvis.server.crew import engine as eng_mod
from openjarvis.server.crew import helix as crew_helix
from openjarvis.server.crew.engine import CrewEngine, in_quiet_hours, parse_plan
from openjarvis.server.crew.store import MemoryCrewStore

UID = "owner-1"
NOON_LA = dt.datetime(2026, 10, 21, 19, 0, tzinfo=dt.timezone.utc)      # 12:00 in Los Angeles
NIGHT_LA = dt.datetime(2026, 10, 22, 6, 0, tzinfo=dt.timezone.utc)      # 23:00 in Los Angeles
ENV = {"CREW_ENABLED": "true", "CREW_DAILY_CAP_USD": "5", "CREW_MISSION_BUDGET_USD": "1"}


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


def world(plan, agents, env=None, now=NOON_LA, http=None, store=None):
    store = store or MemoryCrewStore(clock=lambda: now)
    notes, model = Notes(), Model(plan, agents)
    e = CrewEngine(store, model, notifier=notes, env=dict(ENV, **(env or {})), cost_fn=lambda r: 0.01, now=lambda: now,
                   models={"writing": "claude-x", "lookup": "gemini-x", "default": "gpt-x"}, client_factory=http)
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
        assert m["status"] == "done" and [t["status"] for t in tasks] == ["done", "done"]
        assert tasks[1]["result"].startswith("Listing written")
        assert [o["name"] for o in run(w.store.list_outputs(m["id"]))] == ["buyers.md"]
        # the writer saw the researcher's result, and Gemini did research while Claude wrote
        writer_call = next(c for c in w.model.calls if c["tools"] and "(Write listing)" in c["messages"][1]["content"])
        assert "Collectors who track sets." in writer_call["messages"][1]["content"]
        assert {c["model"] for c in w.model.calls if c["tools"]} == {"gemini-x", "claude-x"}
        assert m["spent_usd"] == pytest.approx(0.04)                 # planner + 2 research rounds + 1 writer round
        assert run(w.store.get_day_spend(UID, "2026-10-21")) == pytest.approx(0.04)
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

class TestBudgets:
    def test_a_task_out_of_budget_asks_and_continues_only_if_approved(self):
        def writer(turn, msgs):
            return resp(calls=[("save_output", {"name": f"part{turn}.md", "content": "x"})]) if turn < 4 else resp("done")
        w = world({"tasks": [{"title": "Write listing", "role": "writer", "brief": "b"}]}, {"Write listing": writer})
        m = run(start(w, budget=0.06))                       # planner 0.01, then the task gets 0.05: two calls of 0.02 estimate
        q = run(w.store.list_questions(UID))
        assert len(q) == 1 and q[0]["kind"] == "budget" and run(w.store.list_tasks(m["id"]))[0]["status"] == "waiting"
        run(w.engine.answer(UID, q[0]["id"], approve=True))
        task = run(w.store.list_tasks(m["id"]))[0]
        assert task["budget_usd"] > 0.05 and task["status"] == "queued"

    def test_declining_more_budget_stops_the_task_and_the_mission_says_so(self):
        w = world({"tasks": [{"title": "Write listing", "role": "writer", "brief": "b"}]},
                  {"Write listing": lambda turn, msgs: resp(calls=[("save_output", {"name": "a.md", "content": "x"})])})
        m = run(start(w, budget=0.06))
        q = run(w.store.list_questions(UID))[0]
        run(w.engine.answer(UID, q["id"], approve=False))
        assert run(w.store.list_tasks(m["id"]))[0]["status"] == "failed"
        assert run(w.store.get_mission(m["id"]))["status"] == "stalled"

    def test_the_daily_cap_stops_new_work_and_is_reported_once(self):
        w = world(two_task_plan(), {"Research buyers": [resp("r")], "Write listing": [resp("w")]}, env={"CREW_DAILY_CAP_USD": "0.02"})
        m = run(start(w))                                    # planning (0.01) leaves 0.01: below one call's estimate
        assert [t["status"] for t in run(w.store.list_tasks(m["id"]))] == ["queued", "queued"]
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
