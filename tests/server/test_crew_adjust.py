"""Helix can change a mission after it starts, and read documents and outbox files: attached Word/text, add/edit/retry tasks, any mission's files."""

from __future__ import annotations

import base64
import io
import zipfile

import pytest

from openjarvis.server import documents
from openjarvis.server.crew import helix
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
