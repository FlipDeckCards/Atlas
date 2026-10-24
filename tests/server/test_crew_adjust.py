"""Helix can change a mission after it starts, and read documents and outbox files: attached Word/text, add/edit/retry tasks, any mission's files."""

from __future__ import annotations

import base64
import io
import zipfile
from types import SimpleNamespace

import pytest

from openjarvis.server import documents
from openjarvis.server.crew import helix
from openjarvis.server.crew import engine as eng_mod
from tests.server.test_crew import UID, resp, run, start, two_task_plan, world

AGENTS = {"Research buyers": [resp("r")], "Write listing": [resp("w")], "Extra research": [resp("e")]}


def docx_bytes(*paras):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paras)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


class TestDocuments:
    def test_reads_word_and_text_and_refuses_the_rest(self):
        assert documents.extract_text("a.docx", docx_bytes("Hello", "World")) == "Hello\nWorld"
        assert documents.extract_text("n.md", b"# Title\r\n\r\n\r\n\r\nbody") == "# Title\n\nbody"
        for name, data in (("a.doc", b"x"), ("a.exe", b"x"), ("a.txt", b"  "), ("a.docx", b"not a zip")):
            with pytest.raises(ValueError):
                documents.extract_text(name, data)
        with pytest.raises(ValueError):
            documents.extract_text("big.txt", b"x" * (documents.MAX_DOC_BYTES + 1))

    def test_a_word_file_is_saved_to_the_outbox_as_text(self):
        assert documents.outbox_name("My Plan.docx") == ("My Plan.docx", "attached-My Plan.txt")
        assert documents.outbox_name("../x/notes.md")[1] == "attached-notes.md"


class TestAttachedDocumentsReachTheCrew:
    def test_a_mission_started_with_a_document_gives_agents_the_file(self):
        w = world(two_task_plan(), AGENTS)
        helix.RECENT_DOCS.clear()
        helix.remember_document(UID, "brief.docx", "Sell planners to card collectors")
        ctx = helix.CrewCtx(UID, w.engine)
        out = run(helix.execute(ctx, "start_mission", {"goal": "Build a planner product from my document"}))
        assert "NOT STARTED YET" in out
        helix.CREW_PENDING.arm(UID)
        early = run(helix.pre_turn(ctx, "yes"))
        assert "Mission started" in early["reply"]
        m = run(w.store.list_missions(UID))[0]
        run(w.engine.run_until_idle())
        names = [o["name"] for o in run(w.store.list_outputs(m["id"]))]
        assert "attached-brief.txt" in names
        assert "Sell planners" in json_of(w.model.calls[0]["messages"])         # the planner saw the document's start
        assert helix.recent_documents(UID) == []                                  # given to the mission once

    def test_attach_document_to_an_existing_mission(self):
        w = world(two_task_plan(), AGENTS)
        m = run(start(w))
        helix.remember_document(UID, "more.txt", "extra notes")
        ctx = helix.CrewCtx(UID, w.engine)
        assert "attached-more.txt" in run(helix.execute(ctx, "attach_document", {"mission_id": m["id"]}))
        assert "extra notes" in run(helix.execute(ctx, "read_outbox_file", {"name": "attached-more.txt"}))


def json_of(x):
    import json
    return json.dumps(x)


class TestAdjustingAMission:
    def test_add_a_task_reopens_a_finished_mission_and_the_crew_runs_it(self):
        w = world(two_task_plan(), AGENTS)
        m = run(start(w))
        assert run(w.store.get_mission(m["id"]))["status"] == "done"
        ctx = helix.CrewCtx(UID, w.engine)
        out = run(helix.execute(ctx, "add_task", {"mission_id": m["id"], "title": "Extra research", "role": "researcher",
                                                  "brief": "Look at competitors", "depends_on": [1]}))
        assert out.startswith('Added task 4 "Extra research"')            # 2 planned + the reviewer + this one
        run(w.engine.run_until_idle())
        tasks = run(w.store.list_tasks(m["id"]))
        assert tasks[-1]["title"] == "Extra research" and tasks[-1]["status"] == "done" and tasks[-1]["depends_on"] == [0]
        assert run(w.store.get_mission(m["id"]))["status"] == "done"

    def test_add_task_refuses_bad_input_and_other_peoples_missions(self):
        w = world(two_task_plan(), AGENTS)
        m = run(start(w))
        ctx = helix.CrewCtx(UID, w.engine)
        assert "Unknown agent role" in run(helix.execute(ctx, "add_task", {"mission_id": m["id"], "title": "X", "role": "wizard", "brief": "b"}))
        assert "no task number 99" in run(helix.execute(ctx, "add_task", {"mission_id": m["id"], "title": "X", "role": "writer", "brief": "b", "depends_on": [99]}))
        assert "No such mission" in run(helix.execute(helix.CrewCtx("someone-else", w.engine), "add_task",
                                                      {"mission_id": m["id"], "title": "X", "role": "writer", "brief": "b"}))
        run(w.store.update_mission(m["id"], status="cancelled"))
        assert "cancelled" in run(helix.execute(ctx, "add_task", {"mission_id": m["id"], "title": "X", "role": "writer", "brief": "b"}))

    def test_a_paused_mission_keeps_a_new_task_until_resumed(self):
        w = world(two_task_plan(), AGENTS)
        m = run(start(w))
        run(w.engine.pause_mission(UID, m["id"]))
        run(w.store.update_mission(m["id"], status="paused"))
        ctx = helix.CrewCtx(UID, w.engine)
        out = run(helix.execute(ctx, "add_task", {"mission_id": m["id"], "title": "Extra research", "role": "researcher", "brief": "more"}))
        assert "paused" in out
        run(w.engine.run_until_idle())
        assert run(w.store.list_tasks(m["id"]))[-1]["status"] == "queued"
        run(helix.execute(ctx, "resume_mission", {"mission_id": m["id"]}))
        run(w.engine.run_until_idle())
        assert run(w.store.list_tasks(m["id"]))[-1]["status"] == "done"

    def test_resume_reopens_a_done_mission_that_still_has_a_queued_task(self):
        w = world(two_task_plan(), AGENTS)
        m = run(start(w))
        run(w.engine.run_until_idle())
        t = run(w.store.list_tasks(m["id"]))[0]
        run(w.store.update_task(t["id"], status="queued"))
        run(w.store.update_mission(m["id"], status="done"))
        run(helix.execute(helix.CrewCtx(UID, w.engine), "resume_mission", {"mission_id": m["id"]}))
        assert run(w.store.get_mission(m["id"]))["status"] == "active"

    def test_edit_only_a_task_that_has_not_started_and_retry_reruns_with_a_note(self):
        w = world(two_task_plan(), AGENTS)
        m = run(start(w))
        ctx = helix.CrewCtx(UID, w.engine)
        assert "is done" in run(helix.execute(ctx, "edit_task", {"mission_id": m["id"], "task_number": 1, "brief": "new"}))
        run(helix.execute(ctx, "add_task", {"mission_id": m["id"], "title": "Extra research", "role": "researcher", "brief": "old"}))
        assert "updated" in run(helix.execute(ctx, "edit_task", {"mission_id": m["id"], "task_number": 4, "brief": "new brief"}))
        assert run(w.store.list_tasks(m["id"]))[-1]["brief"] == "new brief"
        out = run(helix.execute(ctx, "retry_task", {"mission_id": m["id"], "task_number": 1, "note": "focus on collectors"}))
        assert "queued to run again" in out
        t = run(w.store.list_tasks(m["id"]))[0]
        assert t["status"] == "queued" and "focus on collectors" in t["transcript"][-1]["content"]
        assert "no task number 9" in run(helix.execute(ctx, "retry_task", {"mission_id": m["id"], "task_number": 9}))

    def test_status_lists_every_task_with_its_number(self):
        w = world(two_task_plan(), AGENTS)
        run(start(w))
        text = run(helix.status_text(helix.CrewCtx(UID, w.engine)))
        assert "task 1: Research buyers" in text and "task 2: Write listing" in text


class TestReadingAnyMissionsOutbox:
    def make(self):
        w = world(two_task_plan(), AGENTS)
        a, b = run(start(w)), run(start(w, goal="Another planner mission for gamers"))
        run(w.store.add_output(a["id"], "", UID, "plan.md", "alpha plan"))
        run(w.store.add_output(b["id"], "", UID, "plan.md", "beta plan"))
        run(w.store.add_output(b["id"], "", UID, "only-b.md", "x" * 25000))
        return w, a, b

    def test_helix_reads_files_of_cancelled_and_other_missions(self):
        w, a, b = self.make()
        run(w.store.update_mission(a["id"], status="cancelled"))
        run(w.store.update_mission(b["id"], status="stalled"))
        ctx = helix.CrewCtx(UID, w.engine)
        listing = run(helix.execute(ctx, "list_outbox", {}))
        assert "[cancelled]" in listing and "[stalled]" in listing
        assert run(helix.execute(ctx, "read_outbox_file", {"name": "plan.md", "mission_id": a["id"]})) == "alpha plan"
        assert "Several missions" in run(helix.execute(ctx, "read_outbox_file", {"name": "plan.md"}))
        long = run(helix.execute(ctx, "read_outbox_file", {"name": "only-b.md"}))
        assert "offset 20000" in long
        assert run(helix.execute(ctx, "read_outbox_file", {"name": "only-b.md", "offset": 20000})).startswith("x")

    def test_other_users_files_stay_private(self):
        w, a, b = self.make()
        other = helix.CrewCtx("someone-else", w.engine)
        assert run(helix.execute(other, "list_outbox", {})) == "No files in any outbox yet."
        assert "No file by that name" in run(helix.execute(other, "read_outbox_file", {"name": "plan.md", "mission_id": a["id"]}))

    def test_an_agent_reads_another_missions_file_and_lists_them(self):
        w, a, b = self.make()
        task = run(w.store.list_tasks(b["id"]))[0]
        mission = run(w.store.get_mission(b["id"]))
        text, _ = run(w.engine._tool(task, mission, "read_output", {"name": "plan.md"}))
        assert text == "beta plan"                                              # its own mission's file wins
        text, _ = run(w.engine._tool(task, mission, "read_output", {"name": "plan.md", "mission_id": a["id"]}))
        assert text == "alpha plan"
        text, _ = run(w.engine._tool(task, mission, "list_outbox", {}))
        assert "only-b.md" in text and a["id"] in text


class TestChatRequestAndStation:
    def test_chat_request_carries_a_document_and_it_is_read(self):
        from openjarvis.server.atlas_ui import ChatRequest
        req = ChatRequest(message="read this", document=base64.b64encode(docx_bytes("Hello Helix")).decode(), document_name="plan.docx")
        assert documents.from_b64(req.document_name, req.document) == "Hello Helix"
        with pytest.raises(ValueError):
            documents.from_b64("x.exe", base64.b64encode(b"MZ").decode())

    def test_state_changing_tools_make_the_station_redraw(self):
        assert {"add_business", "add_task", "resume_mission"} <= helix.CHANGES_STATION
        assert "crew_status" not in helix.CHANGES_STATION

    def test_the_station_ring_grows_with_each_business(self):
        from openjarvis.server.crew import station
        page = station.__dict__.get("PAGE") or ""
        src = open(station.__file__).read()
        assert "pl.slice(4)" in src and "2 * Math.PI / N" in src          # businesses past the 4th get a branch; the ring is spaced by N


class TestRemovingFactories:
    def test_a_mission_factory_is_removed_by_cancelling_its_missions_and_never_falsely_claimed(self):
        w = world(two_task_plan(), AGENTS)
        a = run(start(w, goal="AI: The User Experience - Going from Defense to Offense"))
        run(w.store.update_mission(a["id"], title="AI: The User Experience - Going from Defense to Offense"))
        ctx = helix.CrewCtx(UID, w.engine)
        assert "Nothing was removed" in run(helix.execute(ctx, "remove_business", {"name": "No Such Factory"}))
        out = run(helix.execute(ctx, "remove_business", {"name": "AI: THE USER EXPERIENCE - GOING FROM DEFENSE TO OFFENSE"}))
        assert out.startswith("Removed") and "cancelling its 1 mission" in out
        assert run(w.store.get_mission(a["id"]))["status"] == "cancelled"           # a finished mission leaves the Station too
        assert "plan.md" not in run(helix.execute(ctx, "list_outbox", {}))            # (nothing saved here: just no error)
        assert "Nothing was removed" in run(helix.execute(ctx, "remove_business", {"name": "AI: The User Experience"}))

    def test_a_business_is_removed_and_the_fixed_rows_stay(self):
        from openjarvis.server.crew import tenant
        w = world(two_task_plan(), AGENTS, owner=None)
        ctx = helix.CrewCtx(UID, w.engine)
        run(helix.execute(ctx, "add_business", {"name": "Books"}))
        assert run(helix.execute(ctx, "remove_business", {"name": "Books"})).startswith('Removed "Books"')
        assert run(tenant.businesses(w.store, UID)) == []
        assert "stay" in run(helix.execute(ctx, "remove_business", {"name": "Marnyx"}))

    def test_cancel_mission_tool(self):
        w = world(two_task_plan(), AGENTS)
        m = run(start(w))
        ctx = helix.CrewCtx(UID, w.engine)
        assert "cancelled" in run(helix.execute(ctx, "cancel_mission", {"mission_id": m["id"]}))
        assert run(w.store.get_mission(m["id"]))["status"] == "cancelled"
        assert "No such mission" in run(helix.execute(ctx, "cancel_mission", {"mission_id": "nope"}))


from tests.server.test_atlas_hardening import _bearer, _env, client, model, store  # noqa: E402,F401  (the chat endpoint's own fixtures)


class TestSeveralAttachments:
    def post(self, client, atts, **extra):
        return client.post("/api/chat", json=dict({"message": "look at these", "attachments": atts}, **extra), headers=_bearer())

    def att(self, name, data=b"hello", mime=None):
        return {"name": name, "data": base64.b64encode(data).decode(), "mime": mime}

    def test_more_than_five_files_are_refused_before_any_model_call(self, client, model):
        r = self.post(client, [self.att(f"n{i}.txt") for i in range(6)])
        assert r.status_code == 400 and "up to 5" in r.json()["detail"].lower() and model.await_count == 0
        r = self.post(client, [self.att(f"n{i}.txt") for i in range(5)], image="AAAA")           # the old single image counts too
        assert r.status_code == 400 and model.await_count == 0

    def test_a_bad_file_names_itself_and_nothing_is_sent_to_the_model(self, client, model):
        r = self.post(client, [self.att("ok.txt"), self.att("virus.exe", b"MZ")])
        assert r.status_code == 400 and r.json()["detail"].startswith("virus.exe:") and model.await_count == 0
        r = self.post(client, [self.att("old.doc", b"x")])
        assert r.status_code == 400 and ".docx" in r.json()["detail"]

    def test_the_total_size_is_capped(self, client, model):
        from openjarvis.server import hardening
        big = "A" * (hardening.MAX_IMAGE_B64_CHARS - 8)
        r = client.post("/api/chat", json={"message": "x", "attachments": [{"name": f"p{i}.png", "data": big, "mime": "image/png"} for i in range(5)]}, headers=_bearer())
        assert r.status_code == 413 and model.await_count == 0


class TestStationSpin:
    def test_the_ring_spins_on_a_click_on_helix_and_when_a_mission_starts_or_finishes(self):
        from openjarvis.server.crew.station import STATION_PAGE
        for needle in ("function startSpin", "startSpin();      // click Helix", "spinOnMissionChange(S.missions", "stepMotion(t);", "const DRIFT", "turns: 2"):
            assert needle in STATION_PAGE


class TestMissionsWithoutABusiness:
    def test_a_mission_no_business_owns_gets_no_factory_of_its_own(self):
        from openjarvis.server.crew.station import STATION_PAGE
        assert "return 'aibussol';" in STATION_PAGE and "return 'new:' + String(m.title" not in STATION_PAGE


class TestDeepSpace:
    def test_the_station_sky_has_a_milky_way_shooting_stars_and_a_drifting_station(self):
        from openjarvis.server.crew.station import STATION_PAGE
        for needle in ("function drawMilkyWay", "function spawnMeteor", "function drawMiniStation", "drawMeteors(t);", "drawMilkyWay(s, W, H);"):
            assert needle in STATION_PAGE


class TestWhyAMissionStalled:
    def test_errors_are_explained_in_plain_words(self):
        from openjarvis.server.crew.engine import explain_error
        credit = Exception('AnthropicError: {"error":{"message":"Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing"}}')
        assert "credit balance is too low" in explain_error(credit) and "console.anthropic.com" in explain_error(credit)
        assert "rate limiting" in explain_error(Exception("429 Too Many Requests"))
        assert "ValueError: odd" in explain_error(ValueError("odd"))

    def test_a_task_that_keeps_erroring_says_why_and_helix_reports_it(self):
        def boom(turn, messages):
            raise RuntimeError("Your credit balance is too low to access the Anthropic API")
        w = world(two_task_plan(), {"Research buyers": boom, "Write listing": [resp("w")]})
        m = run(start(w))
        for _ in range(6):
            run(w.engine.run_until_idle())
        m = run(w.store.get_mission(m["id"]))
        assert m["status"] == "stalled"
        ctx = helix.CrewCtx(UID, w.engine)
        out = run(helix.execute(ctx, "mission_details", {"mission_id": m["id"]}))
        assert "STALLED" in out and "credit balance is too low" in out and "resume_mission" in out
        assert "credit balance is too low" in run(helix.status_text(ctx))
        assert "no such mission" in run(helix.execute(helix.CrewCtx("other", w.engine), "mission_details", {"mission_id": m["id"]}))


class TestDeckAndAsteroids:
    def test_one_walker_per_crew_member_wandering_and_asteroids_drift_by(self):
        from openjarvis.server.crew.station import STATION_PAGE
        for needle in ("function spawnRock", "drawRocks(t);", "coreRunning", "const lane = ring *", "* 1.35;"):
            assert needle in STATION_PAGE
        assert "core.bay.tasks.filter(k => k.status !== 'waiting')" not in STATION_PAGE      # no more one walker per task


class TestHelixInvestigates:
    """Helix reads GitHub and Render herself (read-only) to find out what broke."""

    def dev(self, handler, env=None):
        import httpx
        w = world(two_task_plan(), AGENTS, env=dict({"CREW_GITHUB_TOKEN": "t", "RENDER_API_KEY": "rk"}, **(env or {})),
                  http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return w, helix.CrewCtx(UID, w.engine)

    def test_why_did_the_deploy_fail_deploys_then_the_error_in_the_logs_with_secrets_masked(self):
        import httpx
        seen = []

        def handler(req):
            seen.append(str(req.url))
            if req.url.path == "/v1/services":
                return httpx.Response(200, json=[{"service": {"id": "srv-1", "name": "Atlas", "type": "web_service", "ownerId": "tea-1", "repo": "https://github.com/FlipDeckCards/Atlas", "branch": "main", "autoDeploy": "yes"}}])
            if req.url.path == "/v1/services/srv-1/deploys":
                return httpx.Response(200, json=[{"deploy": {"id": "d1", "status": "build_failed", "createdAt": "2026-10-24T01:00:00Z", "commit": {"id": "abcdef123", "message": "Add thing"}}}])
            if req.url.path == "/v1/logs":
                return httpx.Response(200, json={"logs": [{"timestamp": "2026-10-24T01:00:05Z", "message": "ModuleNotFoundError: No module named 'foo' key=sk-ant-abcdefghijklmnop"},
                                                          {"timestamp": "2026-10-24T01:00:04Z", "message": "Traceback (most recent call last):"}]})
            return httpx.Response(404, json={})
        w, ctx = self.dev(handler)
        assert "Atlas (srv-1)" in run(helix.execute(ctx, "render_services", {}))
        assert "build_failed: Add thing (abcdef1)" in run(helix.execute(ctx, "render_deploys", {"service": "atlas"}))
        out = run(helix.execute(ctx, "render_logs", {"service": "Atlas", "level": "error"}))
        assert out.index("Traceback") < out.index("ModuleNotFoundError")                  # oldest first, so a traceback reads in order
        assert "sk-ant-***" in out and "abcdefghijklmnop" not in out
        assert any("ownerId=tea-1" in u and "level=error" in u for u in seen)

    def test_github_reads_are_limited_to_allowed_repos_and_never_return_secrets(self):
        import base64
        import httpx

        def handler(req):
            p = req.url.path
            if p == "/repos/FlipDeckCards/Atlas/contents/app.py":
                return httpx.Response(200, json={"content": base64.b64encode(b"KEY = 'ghp_abcdefghijklmnop12345'\nprint(1)").decode()})
            if p == "/repos/FlipDeckCards/Atlas/commits/main/check-runs":
                return httpx.Response(200, json={"check_runs": [{"name": "tests", "conclusion": "failure", "output": {"title": "2 failed", "summary": "test_x failed"}}, {"name": "lint", "conclusion": "success"}]})
            if p == "/repos/FlipDeckCards/Atlas":
                return httpx.Response(200, json={"default_branch": "main"})
            if p.endswith("/commits"):
                return httpx.Response(200, json=[{"sha": "1234567890", "commit": {"message": "Fix it", "author": {"date": "2026-10-24T01:00:00Z"}}}])
            if p.endswith("/pulls"):
                return httpx.Response(200, json=[{"number": 7, "state": "open", "title": "A change", "head": {"ref": "crew/x"}}])
            return httpx.Response(404, json={})
        w, ctx = self.dev(handler)
        text = run(helix.execute(ctx, "github_read", {"repo": "FlipDeckCards/Atlas", "path": "app.py"}))
        assert "print(1)" in text and "ghp_***" in text and "abcdefghijklmnop" not in text
        assert "secrets file" in run(helix.execute(ctx, "github_read", {"repo": "FlipDeckCards/Atlas", "path": "config/.env.production"}))
        assert "not one the crew may use" in run(helix.execute(ctx, "github_read", {"repo": "someone/else", "path": "a.py"}))
        act = run(helix.execute(ctx, "github_activity", {"repo": "FlipDeckCards/Atlas"}))
        assert "1234567 " in act and "#7 [open] A change" in act and "tests: failure - 2 failed: test_x failed" in act and "lint: success" in act

    def test_not_connected_says_how_to_connect_and_nothing_is_called(self):
        import httpx
        calls = []
        w, ctx = self.dev(lambda r: calls.append(r) or httpx.Response(200, json=[]), env={"RENDER_API_KEY": "", "CREW_GITHUB_TOKEN": ""})
        assert "not connected" in run(helix.execute(ctx, "render_services", {})).lower()
        assert "not connected" in run(helix.execute(ctx, "github_files", {"repo": "FlipDeckCards/Atlas"})).lower()
        assert calls == []

    def test_crew_turns_get_room_to_investigate(self):
        from openjarvis.server import hardening
        assert hardening.CREW_TOOL_ROUNDS >= 8 and hardening.CREW_CHAT_MAX_TOKENS > hardening.CHAT_MAX_TOKENS and hardening.CREW_HISTORY_MESSAGES > 10
        names = {t["function"]["name"] for t in helix.CREW_TOOLS}
        assert {"github_read", "github_search", "github_activity", "github_failed_runs", "render_logs", "render_deploys", "render_services", "github_files"} <= names


# ── Forge checks its own work; Helix acts only on Approve; the sentinel; memory ─────────────────────────────────────────

import httpx  # noqa: E402


def gh_handler(state):
    """A fake GitHub: the branch's check runs come from state['runs'] (a list that tests change between calls)."""
    def handler(req):
        p, m = req.url.path, req.method
        if p == "/repos/FlipDeckCards/Atlas" and m == "GET":
            return httpx.Response(200, json={"default_branch": "main"})
        if p.endswith("/pulls") and m == "GET":
            return httpx.Response(200, json=[{"number": 9}] if state.get("pr") else [])
        if p.endswith("/pulls") and m == "POST":
            state["pr"] = True
            return httpx.Response(201, json={"number": 9})
        if p.endswith("/check-runs") and m == "GET":
            return httpx.Response(200, json={"check_runs": state["runs"]})
        if "/annotations" in p:
            return httpx.Response(200, json=[{"path": "tests/test_x.py", "start_line": 12, "message": "assert 1 == 2"}])
        return httpx.Response(404, json={})
    return handler


def forge(w):
    task = run(w.store.list_tasks(run(start(w))["id"]))[0]
    mission = run(w.store.get_mission(task["mission_id"]))
    return dict(task, role="builder"), mission


class TestForgeChecksItsWork:
    def world(self, state):
        w = world(two_task_plan(), AGENTS, env={"CREW_GITHUB_TOKEN": "t"}, http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(gh_handler(state))))

        async def instant(_):
            return None
        w.engine.sleep = instant
        return w

    def test_failing_checks_are_reported_with_the_failing_line_and_block_the_merge_request(self):
        state = {"runs": [{"id": 1, "name": "tests", "status": "completed", "conclusion": "failure", "output": {"title": "1 failed", "summary": "test_x"}}]}
        w = self.world(state)
        task, mission = forge(w)
        text, _ = run(w.engine._tool(task, mission, "repo_checks", {"repo": "FlipDeckCards/Atlas", "branch": "crew/fix"}))
        assert "FAILED" in text and "tests/test_x.py:12 assert 1 == 2" in text and "repo_commit again" in text
        out, outcome = run(w.engine._tool(task, mission, "request_merge", {"repo": "FlipDeckCards/Atlas", "branch": "crew/fix", "title": "T", "summary": "S"}))
        assert out.startswith("NOT sent to Michael") and outcome is None
        assert run(w.store.list_questions(UID, "open")) == []                                 # no merge checkpoint was raised

    def test_passed_checks_let_the_merge_request_through_and_say_so(self):
        state = {"runs": [{"id": 1, "name": "tests", "status": "completed", "conclusion": "success"}, {"id": 2, "name": "lint", "status": "completed", "conclusion": "success"}]}
        w = self.world(state)
        task, mission = forge(w)
        text, _ = run(w.engine._tool(task, mission, "repo_checks", {"repo": "FlipDeckCards/Atlas", "branch": "crew/fix"}))
        assert "PASSED" in text and "request_merge" in text and state["pr"] is True
        out, outcome = run(w.engine._tool(task, mission, "request_merge", {"repo": "FlipDeckCards/Atlas", "branch": "crew/fix", "title": "T", "summary": "S"}))
        assert outcome == "waiting"
        q = run(w.store.list_questions(UID, "open"))[0]
        assert q["kind"] == "checkpoint" and "Checks: passed" in q["text"]

    def test_checks_still_running_are_waited_for_then_reported_pending(self):
        state = {"runs": [{"id": 1, "name": "tests", "status": "in_progress", "conclusion": None}]}
        w = self.world(state)
        task, mission = forge(w)
        text, _ = run(w.engine._tool(task, mission, "repo_checks", {"repo": "FlipDeckCards/Atlas", "branch": "crew/fix"}))
        assert "PENDING" in text and "call repo_checks again" in text
        out, outcome = run(w.engine._tool(task, mission, "request_merge", {"repo": "FlipDeckCards/Atlas", "branch": "crew/fix", "title": "T", "summary": "S"}))
        assert "still running" in out and outcome is None


def render_handler(log, deploy_status="build_failed"):
    def handler(req):
        p, m = req.url.path, req.method
        log.append((m, p))
        if p == "/v1/services":
            return httpx.Response(200, json=[{"service": {"id": "srv-1", "name": "Atlas", "type": "web_service", "ownerId": "tea-1", "repo": "https://github.com/FlipDeckCards/Atlas", "branch": "main", "autoDeploy": "yes"}}])
        if p == "/v1/services/srv-1/deploys" and m == "GET":
            return httpx.Response(200, json=[{"deploy": {"id": "dep-new123456789", "status": deploy_status, "createdAt": "2026-10-24T01:00:00Z", "commit": {"id": "abcdef1234", "message": "Add thing"}}},
                                              {"deploy": {"id": "dep-old123456789", "status": "live", "createdAt": "2026-10-23T01:00:00Z", "commit": {"id": "1111111222", "message": "Older"}}}])
        if p in ("/v1/services/srv-1/deploys", "/v1/services/srv-1/restart", "/v1/services/srv-1/rollback") and m == "POST":
            return httpx.Response(201, json={"id": "dep-started1"})
        if p == "/v1/logs":
            return httpx.Response(200, json={"logs": [{"timestamp": "2026-10-24T01:00:05Z", "message": "ModuleNotFoundError: No module named 'foo'"}]})
        return httpx.Response(404, json={})
    return handler


class TestHelixChangesTheLiveServiceOnlyOnApprove:
    def setup(self):
        log = []
        w = world(two_task_plan(), AGENTS, env={"RENDER_API_KEY": "rk", "CREW_GITHUB_TOKEN": "t"}, http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(render_handler(log))))
        return w, helix.CrewCtx(UID, w.engine), log

    def test_a_redeploy_is_a_card_and_runs_only_when_approved(self):
        w, ctx, log = self.setup()
        out = run(helix.execute(ctx, "render_redeploy", {"service": "atlas"}))
        assert out.startswith("NOT DONE YET") and not any(m == "POST" for m, _ in log)
        q = run(w.store.list_questions(UID, "open"))[0]
        assert q["kind"] == "ops" and q["action"]["type"] == "render_redeploy"
        assert "Not done" in run(helix.execute(ctx, "answer_crew_question", {"question_id": q["id"], "answer": "yes"}))      # the model cannot approve it
        assert not any(m == "POST" for m, _ in log)
        res = run(w.engine.answer(UID, q["id"], approve=True))
        assert "Started a new deploy" in res["message"] and ("POST", "/v1/services/srv-1/deploys") in log
        assert any("Approved and ran" in n["text"] for n in run(w.engine.memory(UID)))
        with pytest.raises(ValueError):
            run(w.engine.answer(UID, q["id"], approve=True))                                                                 # once only

    def test_declined_or_someone_elses_does_nothing(self):
        w, ctx, log = self.setup()
        run(helix.execute(ctx, "render_restart", {"service": "Atlas"}))
        q = run(w.store.list_questions(UID, "open"))[0]
        with pytest.raises(LookupError):
            run(w.engine.answer("intruder", q["id"], approve=True))
        assert run(w.engine.answer(UID, q["id"], approve=False))["message"].startswith("Declined")
        assert not any(m == "POST" for m, _ in log)

    def test_rollback_needs_a_real_deploy_id(self):
        w, ctx, log = self.setup()
        assert "Not requested" in run(helix.execute(ctx, "render_rollback", {"service": "Atlas", "deploy_id": "dep-nope"}))
        out = run(helix.execute(ctx, "render_rollback", {"service": "Atlas", "deploy_id": "dep-old1"}))
        assert out.startswith("NOT DONE YET")
        q = run(w.store.list_questions(UID, "open"))[0]
        run(w.engine.answer(UID, q["id"], approve=True))
        assert ("POST", "/v1/services/srv-1/rollback") in log


class TestSentinel:
    def make(self, status="build_failed", final=None):
        from openjarvis.server.crew.sentinel import Sentinel
        log = []
        w = world(two_task_plan(), AGENTS, env={"RENDER_API_KEY": "rk", "CREW_GITHUB_TOKEN": "t"}, http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(render_handler(log, status))))
        script = [resp(None, [("render_logs", {"service": "Atlas", "level": "error"})]),
                  resp(final or "CAUSE: app/main.py imports foo, which is not in requirements.txt.\nNEXT: FIX\nGOAL: In FlipDeckCards/Atlas add foo to requirements.txt so the build installs it.")]
        seen = {"tools": [], "i": 0}

        async def model(model, messages, max_tokens=None, tools=None):
            seen["tools"].append([t["function"]["name"] for t in tools or []])
            r = script[min(seen["i"], len(script) - 1)]
            seen["i"] += 1
            return r
        w.engine.acompletion = model

        async def owner():
            return UID
        w.sentinel = Sentinel(w.engine, owner_id_fn=owner, env=w.engine.env)
        w.seen = seen
        w.log = log
        return w

    def test_a_failed_deploy_is_investigated_read_only_and_proposed_for_approval_once(self):
        w = self.make()
        out = run(w.sentinel.check(UID))
        assert len(out) == 1 and out[0]["next"] == "FIX" and "foo" in out[0]["cause"]
        assert all("render_redeploy" not in names and "github_commit" not in names for names in w.seen["tools"])            # only read tools were offered
        assert not any(m == "POST" for m, _ in w.log)
        q = run(w.store.list_questions(UID, "open"))[0]
        assert q["kind"] == "ops" and q["action"]["type"] == "start_mission" and "requirements.txt" in q["action"]["goal"]
        assert any("Deploy failed: Atlas" == t for _, t, _ in w.notes.sent)
        assert any("deploy" in n["text"] and "failed" in n["text"] for n in run(w.engine.memory(UID)))
        assert run(w.sentinel.check(UID)) == []                                                                              # the same deploy is never investigated twice
        assert len(run(w.store.list_questions(UID, "open"))) == 1

    def test_approving_the_proposal_starts_a_forge_mission(self):
        w = self.make()
        run(w.sentinel.check(UID))
        q = run(w.store.list_questions(UID, "open"))[0]
        res = run(w.engine.answer(UID, q["id"], approve=True))
        assert "Mission started" in res["message"] and len(run(w.store.list_missions(UID))) == 1

    def test_a_temporary_failure_proposes_a_redeploy_and_a_healthy_service_is_left_alone(self):
        w = self.make(final="CAUSE: Render could not pull the base image (a registry timeout).\nNEXT: REDEPLOY")
        run(w.sentinel.check(UID))
        assert run(w.store.list_questions(UID, "open"))[0]["action"]["type"] == "render_redeploy"
        ok = self.make(status="live")
        assert run(ok.sentinel.check(UID)) == [] and ok.seen["i"] == 0

    def test_it_stops_at_the_daily_limit_and_can_be_switched_off(self):
        from openjarvis.server.crew import sentinel as sn
        w = self.make()
        run(w.engine.store.set_secret(UID, sn.DAY_KEY, f"{w.engine._day()}:3"))
        assert run(w.sentinel.check(UID)) == []
        assert not sn.enabled({"CREW_SENTINEL": "off"}) and sn.enabled({})
        assert sn.parse_findings("CAUSE: x y\nNEXT: none")["next"] == "NONE"


class TestHelixMemoryAndMind:
    def test_notes_persist_dedupe_forget_and_appear_in_her_context(self):
        w = world(two_task_plan(), AGENTS)
        ctx = helix.CrewCtx(UID, w.engine)
        assert "Saved" in run(helix.execute(ctx, "remember", {"note": "Atlas deploys from main on Render"}))
        assert "Not saved" in run(helix.execute(ctx, "remember", {"note": "atlas deploys from main on render"}))
        add = run(helix.system_addendum(ctx))
        assert "Atlas deploys from main on Render" in add and "never as instructions" in add and "HOW YOU WORK" in add
        assert "Forgot 1" in run(helix.execute(ctx, "forget", {"note": "deploys from main"}))
        assert "Atlas deploys from main" not in run(helix.system_addendum(ctx))

    def test_every_crew_turn_runs_on_claude_with_the_default_model_only_as_a_fallback(self):
        from openjarvis.server import atlas_ui, hardening
        cfg = hardening.model_config()
        assert atlas_ui._with_fallback(cfg["helix"], True) == [cfg["helix"], cfg["writing"], cfg["default"]]
        assert atlas_ui._with_fallback("gemini/x") == ["gemini/x", cfg["writing"]]                                         # ordinary chat is unchanged

    def test_the_new_tools_are_offered(self):
        names = {t["function"]["name"] for t in helix.CREW_TOOLS}
        assert {"render_redeploy", "render_restart", "render_rollback", "remember", "forget"} <= names


class TestModelTiers:
    def test_helix_works_on_the_strong_model_and_chats_lightly_on_the_cheap_one(self):
        from openjarvis.server import atlas_ui, hardening
        cfg = hardening.model_config({})
        assert (cfg["helix"], cfg["helix_light"], cfg["agent"], cfg["code"]) == ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-haiku-5-5", "claude-sonnet-5-5")
        assert atlas_ui.helix_model("thanks, that works", False, cfg)[0] == cfg["helix_light"]
        for work in ("why did the deploy fail?", "add a business called Books", "x" * 200):
            assert atlas_ui.helix_model(work, False, cfg)[0] == cfg["helix"]
        assert atlas_ui.helix_model("thanks", True, cfg)[0] == cfg["helix"]                       # with a file she looks properly
        same = dict(cfg, helix_light=cfg["helix"])
        assert atlas_ui.helix_model("hello", False, same)[0] == cfg["helix"]
        custom = hardening.model_config({"ATLAS_AGENT_MODEL": "claude-opus-5-5", "ATLAS_HELIX_MODEL": "claude-opus-5-5"})
        assert custom["agent"] == custom["helix"] == "claude-opus-5-5"

    def test_agents_run_on_haiku_except_forge_on_code_and_scout_on_lookup(self):
        from openjarvis.server.crew.engine import ROLES
        tiers = {k: v["model"] for k, v in ROLES.items()}
        assert tiers["builder"] == "code" and tiers["researcher"] == "lookup"
        assert all(t == "agent" for k, t in tiers.items() if k not in ("builder", "researcher"))
        w = world(two_task_plan(), AGENTS)
        w.engine.models = dict(w.engine.models, agent="haiku-x", code="sonnet-x", helix="sonnet-x")
        assert w.engine.model_for("agent") == "haiku-x" and w.engine.model_for("nonsense") == "claude-x"
        assert w.engine._fallback_models("haiku-x") == ["claude-x"] and w.engine._fallback_models("gemini-x") == ["haiku-x", "claude-x"]

    def test_a_task_whose_model_is_not_accepted_continues_on_the_fallback(self):
        calls = []

        def script(turn, messages):
            return resp("done")
        w = world(two_task_plan(), {"Research buyers": script, "Write listing": script})
        w.engine.models = dict(w.engine.models, agent="bad-model", lookup="bad-model")
        orig = w.engine.acompletion

        async def picky(model, messages, max_tokens=None, tools=None):
            calls.append(model)
            if model == "bad-model" and tools:
                raise RuntimeError("404 not_found_error: model: bad-model")
            return await orig(model, messages, max_tokens=max_tokens, tools=tools)
        w.engine.acompletion = picky
        m = run(start(w))
        assert run(w.store.get_mission(m["id"]))["status"] == "done" and "claude-x" in calls


class TestStationLayoutAndSkyStayPut:
    def test_the_canvas_is_redrawn_at_its_real_size_and_the_scale_follows_the_window(self):
        from openjarvis.server.crew.station import STATION_PAGE
        for needle in ("new ResizeObserver", "function designSize", "dw / 1060", "function srand", "mini ? Object.assign(mini"):
            assert needle in STATION_PAGE


# ── the book: a real PDF with the covers, no pictures inside, kept pictures, correct downloads ───────────────────────────

def _png(color, size=(900, 1300)):
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


class TestBookPdf:
    def test_a_6x9_book_has_the_front_cover_first_the_back_cover_last_and_chapters_on_new_pages(self):
        import io
        import pypdf
        from openjarvis.server.crew import pdf
        body = "# One\n\n" + ("Words about design. " * 60 + "\n\n") * 6 + "# Two\n\nMore words here.\n"
        data, n = pdf.make_book_pdf("AI: The User Experience", "Michael Dano", body, _png((200, 30, 30)), _png((30, 30, 200), (2000, 2000)))
        r = pypdf.PdfReader(io.BytesIO(data))
        assert len(r.pages) == n and [round(float(x)) for x in r.pages[0].mediabox[2:]] == [432, 648]
        assert "/XObject" in str(r.pages[0].get("/Resources")) and "/XObject" in str(r.pages[-1].get("/Resources"))      # covers are full-page pictures
        assert "AI: The User Experience" in r.pages[1].extract_text() and "Michael Dano" in r.pages[1].extract_text()
        texts = [p.extract_text() for p in r.pages]
        assert any(t.startswith("One") for t in texts) and any(t.startswith("Two") for t in texts)                       # each chapter opens a page
        assert len(data) < 400_000                                                                                         # big covers are compressed
        plain, _ = pdf.make_book_pdf("T", "", "# A\n\ntext")
        assert b"/DCTDecode" not in plain                                                                                   # no covers, no pictures

    def test_the_existing_guide_maker_is_unchanged(self):
        from openjarvis.server.crew import pdf
        data = pdf.make_pdf("Planner", "# Week\n\n- one\n\n" + "word " * 100)
        assert data.startswith(b"%PDF") and b"/DCTDecode" not in data


class TestMakeBookTool:
    def setup(self):
        w = world(two_task_plan(), AGENTS)
        m = run(start(w))
        run(w.store.add_output(m["id"], "", UID, "part1.md", "# Chapter One\n\nHello world.\n\n![diagram](x.png)\n\n[Image: a graphic of a robot]\n\nMore text."))
        run(w.store.add_output(m["id"], "", UID, "part2.md", "# Chapter Two\n\nSecond part."))

        async def fetch(item_id):
            if item_id == "gone":
                raise eng_mod.conns.ConnectionError_("Could not read media item gone.")
            return _png((10, 120, 40)), "image/png"
        w.engine._media_fetcher = lambda uid: fetch
        task = dict(run(w.store.list_tasks(m["id"]))[1], role="designer")
        return w, m, task

    def test_it_compiles_the_parts_in_order_with_both_covers_and_no_inside_pictures(self):
        import base64
        import io
        import pypdf
        w, m, task = self.setup()
        mission = run(w.store.get_mission(m["id"]))
        out, _ = run(w.engine._tool(task, mission, "make_book", {"title": "My Book", "author": "Michael", "files": ["part1.md", "part2.md"], "cover_item": "c1", "back_cover_item": "b1", "filename": "My Book"}))
        assert 'Saved "My Book.pdf"' in out and "front cover included" in out and "back cover picture included" in out and "no pictures inside" in out
        o = [x for x in run(w.store.list_outputs(m["id"])) if x["name"] == "My Book.pdf"][0]
        raw = base64.b64decode(run(w.store.get_output(o["id"]))["content"][7:])
        r = pypdf.PdfReader(io.BytesIO(raw))
        text = " ".join(p.extract_text() for p in r.pages)
        assert text.index("Chapter One") < text.index("Chapter Two") and "diagram" not in text and "robot" not in text and "Hello world" in text
        assert raw.count(b"/DCTDecode") == 2

    def test_a_rebuild_is_saved_under_a_new_version_name(self):
        w, m, task = self.setup()
        mission = run(w.store.get_mission(m["id"]))
        args = {"title": "My Book", "files": ["part1.md"], "filename": "My Book"}
        run(w.engine._tool(task, mission, "make_book", args))
        out, _ = run(w.engine._tool(task, mission, "make_book", args))
        assert 'Saved "My Book v2.pdf"' in out
        names = [x["name"] for x in run(w.store.list_outputs(m["id"]))]
        assert "My Book.pdf" in names and "My Book v2.pdf" in names

    def test_it_refuses_a_missing_file_or_cover_and_other_roles_do_not_get_it(self):
        w, m, task = self.setup()
        mission = run(w.store.get_mission(m["id"]))
        assert "no outbox file called" in run(w.engine._tool(task, mission, "make_book", {"title": "B", "files": ["nope.md"]}))[0]
        assert "Could not read media item gone" in run(w.engine._tool(task, mission, "make_book", {"title": "B", "files": ["part1.md"], "cover_item": "gone"}))[0]
        assert "no tool named" in run(w.engine._tool(dict(task, role="analyst"), mission, "make_book", {"title": "B", "files": ["part1.md"]}))[0]
        assert "make_book" in [t["function"]["name"] for t in eng_mod.tools_for("designer")] and "make_book" in [t["function"]["name"] for t in eng_mod.tools_for("writer")]

    def test_a_pdf_downloads_as_a_real_pdf_and_agents_are_told_it_is_binary(self):
        import base64
        from openjarvis.server.crew.routes import output_response
        raw = b"%PDF-1.4\n\xe2\xe3\xcf\xd3 binary"
        r = output_response({"name": "Book.pdf", "content": "base64:" + base64.b64encode(raw).decode()})
        assert r.body == raw and r.media_type == "application/pdf"
        assert output_response({"name": "a.md", "content": "# hi"}).media_type.startswith("text/plain")
        w, m, task = self.setup()
        run(w.store.add_output(m["id"], "", UID, "b.pdf", "base64:" + base64.b64encode(raw).decode()))
        assert "binary file" in run(w.engine.read_outbox(UID, "b.pdf"))


class TestHelixKeepsAttachedPictures:
    def test_pictures_are_saved_offered_to_a_new_mission_and_attachable_to_an_existing_one(self, monkeypatch):
        from openjarvis.server.media import service as media
        saved = []

        async def fake_save(ctx, **kw):
            saved.append(kw)
            return SimpleNamespace(id=f"item{len(saved)}")
        monkeypatch.setattr(media, "save_item", fake_save)
        helix.RECENT_IMAGES.clear()
        helix.RECENT_DOCS.clear()
        w = world(two_task_plan(), AGENTS)
        ctx = helix.CrewCtx(UID, w.engine)
        import base64
        out = run(helix.keep_images(ctx, object(), [("cover.png", base64.b64encode(_png((1, 2, 3))).decode(), "image/png")]))
        assert out[0]["item_id"] == "item1" and saved[0]["title"] == "Attached: cover.png" and saved[0]["kind"] == "image"
        assert run(helix.keep_images(ctx, None, [("x.png", "AA==", "image/png")])) == []                                  # media off: nothing kept
        assert "cover.png = item item1" in run(helix.system_addendum(ctx))
        run(helix.execute(ctx, "start_mission", {"goal": "Make the book print ready with my cover"}))
        assert "cover.png = item item1" in helix.CREW_PENDING.get(UID).args["goal"]
        m = run(start(w))
        res = run(helix.execute(ctx, "attach_image", {"mission_id": m["id"], "use": "front cover"}))
        assert "item item1" in res
        note = [o for o in run(w.store.list_outputs(m["id"])) if o["name"].startswith("attached-image-")][0]
        assert "item item1" in run(w.store.get_output(note["id"]))["content"] and "cover_item" in run(w.store.get_output(note["id"]))["content"]
        assert "no recently attached" in run(helix.execute(helix.CrewCtx("other", w.engine), "attach_image", {"mission_id": m["id"]}))


class TestStationArt:
    def test_art_is_served_only_from_the_allowed_names_and_a_missing_file_is_a_plain_404(self):
        from openjarvis.server.crew import routes
        assert {"asteroid1.png", "asteroid2.png", "asteroid3.png", "satellite.png", "helix-walk.png", "helix-views.png"} == routes.ART_FILES
        assert run(routes.station_art("../../etc/passwd")).status_code == 404 and run(routes.station_art("helix-walk.png")).status_code == 404

    def test_the_station_uses_the_art_when_it_loads_and_keeps_its_drawings_otherwise(self):
        from openjarvis.server.crew.station import STATION_PAGE
        for needle in ("const ART = ", "loadArt('helix-walk.png'", "if (ART.rocks.length)", "if (ART.satellite)", "HELIX_ON_DECK && (ART.helix || ART.helixViews)"):
            assert needle in STATION_PAGE


class TestEditingATaskThatAlreadyStarted:
    def test_a_queued_task_with_history_is_edited_and_told_in_its_own_conversation(self):
        w = world(two_task_plan(), AGENTS)
        m = run(start(w))
        ctx = helix.CrewCtx(UID, w.engine)
        run(helix.execute(ctx, "add_task", {"mission_id": m["id"], "title": "Make PDF with graphics", "role": "designer", "brief": "Build the PDF with 7 graphics inside."}))
        t = run(w.store.list_tasks(m["id"]))[-1]
        run(w.store.update_task(t["id"], transcript=[{"role": "user", "content": "Mission: ... Your task: make the PDF with graphics"}]))
        out = run(helix.execute(ctx, "edit_task", {"mission_id": m["id"], "task_number": t["idx"] + 1, "title": "Make the text-only PDF", "brief": "Use make_book; no pictures inside."}))
        assert "updated" in out
        t2 = run(w.store.get_task(t["id"]))
        assert t2["title"] == "Make the text-only PDF" and t2["brief"] == "Use make_book; no pictures inside."
        note = t2["transcript"][-1]["content"]
        assert t2["status"] == "queued" and "Michael changed this task" in note and "no pictures inside" in note and "the new instructions win" in note
        run(w.store.update_task(t["id"], status="waiting"))
        assert "Only a queued task can be edited" in run(helix.execute(ctx, "edit_task", {"mission_id": m["id"], "task_number": t["idx"] + 1, "brief": "x y z"}))

    def test_the_owner_is_not_told_to_expect_a_budget_card(self):
        w = world(two_task_plan(), AGENTS)
        assert "top up their own budget" in run(helix.system_addendum(helix.CrewCtx(UID, w.engine)))


class TestStationNeverGoesBlankWhileDragging:
    def test_the_picture_is_painted_again_in_the_same_step_the_canvas_is_resized(self):
        from openjarvis.server.crew.station import STATION_PAGE
        assert "function paint(t)" in STATION_PAGE and "resize();                                              // resizing the canvas clears it" in STATION_PAGE
        assert "paint(performance.now());" in STATION_PAGE and "requestAnimationFrame(() => { queued = false; last = key; resize(); })" not in STATION_PAGE


class TestArtPipeline:
    def test_the_packer_trims_shrinks_and_builds_a_four_view_helix_sheet(self, tmp_path):
        import importlib.util
        from PIL import Image
        spec = importlib.util.spec_from_file_location("make_station_art", "tools/art/make_station_art.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        src, out = tmp_path / "r", tmp_path / "o"
        src.mkdir()

        def render(name, size, box, color):
            im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            im.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), color), box[:2])
            im.save(src / f"{name}.png")
        for n in ("asteroid1", "asteroid2", "asteroid3"):
            render(n, 512, (40, 40, 440, 440), (150, 140, 130, 255))
        render("satellite", 512, (100, 200, 400, 300), (80, 90, 200, 255))
        for t, c in (("ny", (255, 0, 0, 255)), ("py", (0, 255, 0, 255)), ("px", (0, 0, 255, 255)), ("nx", (255, 255, 0, 255))):
            render(f"helix_{t}", 256, (90, 20, 170, 240), c)
        import sys
        sys.argv = ["x", str(src), "--out", str(out), "--right", "px"]
        mod.main()
        assert Image.open(out / "asteroid1.png").size[0] <= 220 and Image.open(out / "satellite.png").size[0] <= 300
        sheet = Image.open(out / "helix-views.png")
        assert sheet.size == (mod.CELL * 4, mod.CELL)
        cell = lambda k: sheet.crop((k * mod.CELL, 0, (k + 1) * mod.CELL, mod.CELL)).getpixel((mod.CELL // 2, mod.CELL - 20))
        assert cell(0)[:3] == (255, 0, 0) and cell(1)[:3] == (0, 255, 0) and cell(2)[:3] == (0, 0, 255) and cell(3)[:3] == (255, 255, 0)     # front, back, right, left

    def test_the_station_serves_and_draws_the_four_view_helix(self):
        from openjarvis.server.crew import routes
        from openjarvis.server.crew.station import STATION_PAGE
        assert "helix-views.png" in routes.ART_FILES
        for needle in ("if (HELIX_ON_DECK) loadArt('helix-views.png'", "ART.helixViews", "a walk made in code from still pictures"):
            assert needle in STATION_PAGE


class TestBackCoverIsTypesetNotDrawn:
    def test_the_back_cover_words_are_real_text_over_artwork_with_a_barcode_box(self):
        import io
        import pypdf
        from openjarvis.server.crew import pdf
        bt = {"tagline": "Stop defending your interface.", "blurb": "First paragraph of the blurb.\n\nSecond paragraph here.", "author": "Michael Dano", "barcode_space": True}
        data, n = pdf.make_book_pdf("T", "Michael Dano", "# A\n\nbody", _png((8, 20, 46)), _png((8, 20, 46), (1024, 1536)), bt)
        r = pypdf.PdfReader(io.BytesIO(data))
        last = r.pages[-1].extract_text()
        assert n == len(r.pages) and "Stop defending your interface." in last and "Second paragraph here." in last and "Michael Dano" in last
        assert "/XObject" in str(r.pages[-1].get("/Resources")) and "/ExtGState" in str(r.pages[-1].get("/Resources"))
        no_box, _ = pdf.make_book_pdf("T", "", "# A\n\nbody", None, _png((8, 20, 46)), {"tagline": "x", "blurb": "y", "author": "", "barcode_space": False})
        assert len(no_box) < len(data)
        text_only, _ = pdf.make_book_pdf("T", "A", "# A\n\nbody", None, None, {"tagline": "Only words", "blurb": "", "author": "", "barcode_space": False})
        assert "Only words" in pypdf.PdfReader(io.BytesIO(text_only)).pages[-1].extract_text()                    # no picture: a plain dark page with the words

    def test_cover_image_prompts_are_told_to_contain_no_words_and_the_tool_takes_the_words(self):
        w = world(two_task_plan(), AGENTS)
        captured = []

        async def fake_generate(ctx, kind, prompt="", **kw):
            captured.append(prompt)
            raise eng_mod_media_stop()
        from openjarvis.server.media import service as media

        class Stop(Exception):
            pass

        def eng_mod_media_stop():
            return media.MediaServiceError(400, "stop", "stopped for the test")
        w.engine.media_ctx_fn = lambda uid: SimpleNamespace()
        orig = media.generate_and_save
        media.generate_and_save = fake_generate
        try:
            m = run(start(w))
            task = dict(run(w.store.list_tasks(m["id"]))[0], role="designer")
            mission = run(w.store.get_mission(m["id"]))
            run(w.engine._tool(task, mission, "generate_image", {"prompt": "A back cover for the book AI: The User Experience, navy with a cyan spiral"}))
            run(w.engine._tool(task, mission, "generate_image", {"prompt": "A cute fox sitting in a field of flowers"}))
        finally:
            media.generate_and_save = orig
        assert "no text, no letters, no words" in captured[0].lower() and "barcode" in captured[0].lower()
        assert "no text" not in captured[1].lower()
        props = [t for t in eng_mod.tools_for("designer") if t["function"]["name"] == "make_book"][0]["function"]["parameters"]["properties"]
        assert {"back_tagline", "back_blurb", "barcode_space"} <= set(props)


class TestShippedArt:
    def test_michaels_art_is_in_place_small_and_served(self):
        import os
        from PIL import Image
        from openjarvis.server.crew import routes
        for name, max_kb in (("asteroid1.png", 120), ("asteroid2.png", 120), ("asteroid3.png", 120), ("satellite.png", 150), ("helix-views.png", 150)):
            path = os.path.join(routes._ART_DIR, name)
            assert os.path.isfile(path) and os.path.getsize(path) < max_kb * 1024, name
            assert Image.open(path).mode == "RGBA"
            assert run(routes.station_art(name)).status_code == 200
        sheet = Image.open(os.path.join(routes._ART_DIR, "helix-views.png"))
        assert sheet.size[0] == sheet.size[1] * 4                                                    # four square views: front, back, right, left
        for k in range(4):
            assert sheet.crop((k * sheet.size[1], 0, (k + 1) * sheet.size[1], sheet.size[1])).getbbox()   # none of the four views is empty
        assert not any(f.lower().endswith(".png") for f in os.listdir(".")), "raw renders belong in assets/art, not the repo root"


class TestHelixIsOffTheRing:
    def test_helix_does_not_walk_the_deck_and_her_art_is_not_even_downloaded(self):
        from openjarvis.server.crew.station import STATION_PAGE
        assert "const HELIX_ON_DECK = false;" in STATION_PAGE
        assert "if (HELIX_ON_DECK) loadArt('helix-views.png'" in STATION_PAGE and "if (HELIX_ON_DECK) loadArt('helix-walk.png'" in STATION_PAGE


def test_book_typesetter_cleans_markdown_and_placeholders():
    from openjarvis.server.crew import pdf as pdfmod

    md = ("# Part 1: Defense\n\n## From Fear\n\n## Chapter 1: The Fork\n\nThis was *now* and **bold**.\n\n"
          "*(See the Diagram - Media Library Item: d667663d-0913-4da8-ab5c-37bdf1dd1bc1)*\n\n- one\n- two\n")
    data, pages = pdfmod.make_book_pdf("A Book", "Me", md, None, None, {"tagline": "T", "blurb": "B " * 300, "author": "Me"})
    assert data.startswith(b"%PDF") and pages >= 4
    assert b"Media Library" not in data and b"*now*" not in data


def test_tool_call_allowance_restarts_after_a_retry():
    from openjarvis.server.crew.engine import _calls_made

    call = {"role": "assistant", "tool_calls": [{"function": {"name": "make_book"}}]}
    t = [call] * 6
    assert _calls_made(t, "make_book") == 6
    t += [{"role": "user", "content": "Michael asked for this task to be done again."}, call]
    assert _calls_made(t, "make_book") == 1


def test_old_chat_turns_are_clipped_but_recent_ones_stay_whole():
    from openjarvis.server.atlas_ui import clip_old_turn

    long = "x" * 3000
    assert clip_old_turn(long, 2) == long
    assert len(clip_old_turn(long, 9)) < 800 and clip_old_turn("short", 9) == "short"


def test_station_has_a_speech_speed_control_next_to_the_voice_switch():
    from openjarvis.server.crew.station import STATION_PAGE

    assert 'id="speed-tog"' in STATION_PAGE and "playbackRate = voiceSpeed" in STATION_PAGE and "aibussol_voice_speed" in STATION_PAGE

