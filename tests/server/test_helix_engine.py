"""The roster's Helix engine choice: auto keeps the existing rule, haiku and sonnet force one model."""

import asyncio

from openjarvis.server import atlas_ui
from openjarvis.server.crew.engine import CrewEngine
from openjarvis.server.crew.store import MemoryCrewStore

CFG = {"helix": "claude-sonnet-5-5", "helix_light": "claude-haiku-5-5", "writing": "w", "default": "d"}


def _run(c):
    return asyncio.run(c)


def test_auto_keeps_the_existing_rule_and_the_choices_force_a_model():
    for chat in ("hey, how is it going", "what time is it", "why is the sky blue", "what's the weather like"):
        assert atlas_ui.helix_model(chat, False, CFG)[0] == "claude-haiku-5-5"                  # everyday chat is light now
    for work in ("fix the failing deploy", "start a mission to write the listing", "x" * 200):
        assert atlas_ui.helix_model(work, False, CFG)[0] == "claude-sonnet-5-5"
    assert atlas_ui.helix_model("good morning", False, CFG)[0] == "claude-haiku-5-5"
    assert atlas_ui.helix_model("good morning", False, CFG, mode="sonnet")[0] == "claude-sonnet-5-5"
    assert atlas_ui.helix_model("start the mission and fix the build", True, CFG, mode="haiku")[0] == "claude-haiku-5-5"


def test_the_choice_is_saved_validated_and_picks_the_planner_model():
    eng = CrewEngine.__new__(CrewEngine)
    eng.store, eng.models = MemoryCrewStore(), CFG
    assert _run(eng.helix_mode("u1")) == "auto"
    assert _run(eng.set_helix_mode("u1", "haiku")) == "haiku" and _run(eng.helix_mode("u1")) == "haiku"
    assert eng.helix_plan_model("haiku") == "claude-haiku-5-5"
    assert eng.helix_plan_model("auto") == eng.helix_plan_model("sonnet") == "claude-sonnet-5-5"
    try:
        _run(eng.set_helix_mode("u1", "opus"))
    except ValueError:
        return
    raise AssertionError("an unknown engine must be refused")
