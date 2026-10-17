"""The brief job: scheduling (Los Angeles clock, daylight saving), live data only, 'unchecked' for anything unreadable, one run per slot. Offline (respx)."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
import respx

from openjarvis.server import brief_content as bc
from openjarvis.server import brief_job as job
from openjarvis.server.brief_content import LA
from openjarvis.server.briefs import MemoryBriefStore

CFG = {
    "render_owner_id": "tea-test",
    "products": [
        {"name": "FlipDeck", "render_services": [{"id": "srv-flip", "name": "FlipDeck site"}], "github_repos": ["acme/flipdeck"], "stripe": True},
        {"name": "Marnyx", "render_services": [{"id": "srv-mx", "name": "Marnyx app"}], "github_repos": ["acme/marnyx"], "stripe": False},
    ],
}
ENV = {"RENDER_API_KEY": "rk-test", "GITHUB_TOKEN": "gh-test", "STRIPE_READ_KEY": "sk-test"}


def la(h, m=0, day=(2026, 10, 16)):
    return dt.datetime(*day, h, m, tzinfo=LA)


def run(coro):
    return asyncio.run(coro)


def mock_world(m, deploys=None, logs=None, commits=None, charges=None):
    now_iso = "2026-10-17T02:00:00Z"                       # 7 PM Pacific on Oct 16: inside the evening window (12:52 PM to 9:52 PM)
    m.get(url__regex=r"https://api\.render\.com/v1/services/srv-flip/deploys.*").respond(200, json=deploys if deploys is not None else [{"deploy": {"id": "d1", "status": "live", "createdAt": now_iso, "commit": {"message": "Merge pull request #7\n\nbody"}}}])
    m.get(url__regex=r"https://api\.render\.com/v1/services/srv-mx/deploys.*").respond(200, json=[])
    m.get(url__regex=r"https://api\.render\.com/v1/logs.*").respond(200, json={"logs": logs if logs is not None else []})
    m.get(url__regex=r"https://api\.github\.com/repos/acme/flipdeck/commits.*").respond(200, json=commits if commits is not None else [{"sha": "abcdef1234567", "commit": {"message": "Fix the thing\n\nmore"}}])
    m.get(url__regex=r"https://api\.github\.com/repos/acme/marnyx/commits.*").respond(200, json=[])
    m.get(url__regex=r"https://api\.stripe\.com/v1/charges.*").respond(200, json=charges if charges is not None else {"data": [{"status": "succeeded", "amount": 499}, {"status": "failed", "amount": 499}]})
    m.get(url__regex=r"https://api\.stripe\.com/v1/subscriptions.*").respond(200, json={"data": [{"id": "sub_1"}]})


async def make(period="evening", now=None, env=ENV):
    async with httpx.AsyncClient() as c:
        return await job.make_brief(period, now or la(21, 52), env, CFG, c)


class TestScheduling:
    @pytest.mark.parametrize("h,m,want", [
        (6, 51, None), (6, 52, "morning"), (7, 36, "morning"), (7, 37, None), (12, 51, None), (12, 52, "afternoon"), (13, 36, "afternoon"), (13, 37, None),
        (21, 51, None), (21, 52, "evening"), (22, 36, "evening"), (22, 37, None), (3, 0, None), (0, 0, None),
    ])
    def test_a_run_makes_a_brief_only_inside_a_slot(self, h, m, want):
        assert job.due_period(la(h, m)) == want

    def test_the_slots_hold_across_daylight_saving_time(self):
        assert job.due_period(dt.datetime(2026, 10, 17, 4, 52, tzinfo=dt.timezone.utc)) == "evening"         # 21:52 PDT
        assert job.due_period(dt.datetime(2026, 11, 2, 5, 52, tzinfo=dt.timezone.utc)) == "evening"          # 21:52 PST, one hour later in UTC
        assert job.due_period(dt.datetime(2026, 11, 2, 4, 52, tzinfo=dt.timezone.utc)) is None              # 20:52 PST: not a slot
        assert job.due_period(dt.datetime(2026, 3, 9, 13, 52, tzinfo=dt.timezone.utc)) == "morning"          # 06:52 PDT after spring forward
        assert job.due_period(dt.datetime(2026, 11, 2, 14, 52, tzinfo=dt.timezone.utc)) == "morning"         # 06:52 PST

    def test_the_hourly_cron_time_is_always_at_minute_52_so_every_slot_is_reached(self):
        for day in (dt.date(2026, 7, 1), dt.date(2026, 12, 1)):             # summer and winter
            for p in job.SLOTS:
                start_utc = job.slot_start(p, day).astimezone(dt.timezone.utc)
                assert start_utc.minute == 52

    def test_each_brief_covers_the_time_since_the_previous_slot(self):
        assert job.window_start("morning", la(6, 52)) == la(21, 52, day=(2026, 10, 15))
        assert job.window_start("afternoon", la(12, 52)) == la(6, 52)
        assert job.window_start("evening", la(21, 52)) == la(12, 52)

    def test_a_second_run_in_the_same_slot_makes_nothing(self):
        store = MemoryBriefStore(clock=lambda: dt.datetime(2026, 10, 17, 4, 53, tzinfo=dt.timezone.utc))
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            now = la(21, 55)
            async def go():
                async with httpx.AsyncClient() as c:
                    a = await job.run(store, now=now, env=ENV, cfg=CFG, client=c)
                    b = await job.run(store, now=now, env=ENV, cfg=CFG, client=c)
                    return a, b
            a, b = run(go())
        assert a["action"] == "posted" and b["action"] == "skipped" and "already" in b["reason"] and len(store.rows) == 1

    def test_yesterdays_brief_does_not_block_todays(self):
        store = MemoryBriefStore(clock=lambda: dt.datetime(2026, 10, 16, 4, 53, tzinfo=dt.timezone.utc))      # 9:53 PM on Oct 15 in LA
        run(store.insert("Marnyx and FlipDeck", "evening", "old", "old"))
        assert run(job.already_made(store, "evening", la(21, 55))) is False

    def test_outside_a_slot_nothing_is_made_or_stored(self):
        store = MemoryBriefStore()
        assert run(job.run(store, now=la(15, 10), env=ENV, cfg=CFG)) == {"action": "skipped", "reason": "not a brief time"}
        assert store.rows == []

    def test_dry_run_stores_nothing_and_a_named_period_runs_at_any_time(self):
        store = MemoryBriefStore()
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            async def go():
                async with httpx.AsyncClient() as c:
                    return await job.run(store, now=la(15, 10), period="afternoon", env=ENV, cfg=CFG, client=c, dry_run=True)
            out = run(go())
        assert out["action"] == "dry_run" and out["brief"]["period"] == "afternoon" and store.rows == []


class TestGreetingInTheJob:
    def test_a_morning_brief_made_at_10_30_pm_says_good_evening(self):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            b = run(make("morning", la(22, 30)))
        assert b["period"] == "morning" and b["body_markdown"].startswith("Good evening, Michael.") and b["spoken_summary"].startswith("Good evening, Michael.")
        assert "Good morning" not in b["body_markdown"]

    @pytest.mark.parametrize("h,m,word", [(6, 52, "morning"), (12, 52, "afternoon"), (21, 52, "evening"), (4, 59, "evening"), (5, 0, "morning"), (17, 0, "evening")])
    def test_the_word_follows_the_clock(self, h, m, word):
        with respx.mock(assert_all_called=False) as m_:
            mock_world(m_)
            b = run(make("evening", la(h, m)))
        assert b["body_markdown"].startswith(f"Good {word}, Michael.")


class TestLiveDataOnly:
    def test_the_numbers_come_from_this_runs_answers(self):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            b = run(make())["body_markdown"]
        assert "FlipDeck site: live" in b and "Merge pull request #7" in b and "abcdef1 Fix the thing" in b
        assert "1 successful charges totalling $4.99, 1 failed payments, 1 new subscriptions" in b

    def test_nothing_is_remembered_between_runs(self):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m, deploys=[{"deploy": {"id": "d1", "status": "live", "createdAt": "2026-10-17T02:00:00Z", "commit": {"message": "First deploy ever"}}}])
            first = run(make())["body_markdown"]
            mock_world(m, deploys=[{"deploy": {"id": "d2", "status": "build_failed", "createdAt": "2026-10-17T03:00:00Z", "commit": {"message": "Second deploy now"}}}])
            second = run(make())["body_markdown"]
        assert "First deploy ever" in first and "First deploy ever" not in second and "Second deploy now" in second

    def test_a_source_without_a_key_is_unchecked_and_no_number_is_invented(self):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            b = run(make(env={}))["body_markdown"]
            assert not m.calls                                                 # no key: nothing was even requested
        assert "- Render deploys for FlipDeck: unchecked (not configured)." in b and "- GitHub commits for Marnyx: unchecked (not configured)." in b
        assert "- Stripe for FlipDeck: unchecked (not configured)." in b
        assert "$" not in b and "successful charges" not in b

    @pytest.mark.parametrize("status,reason", [(401, "access refused"), (403, "access refused"), (429, "rate limited"), (500, "HTTP 500"), (404, "HTTP 404")])
    def test_a_refusal_or_error_is_unchecked_with_the_reason(self, status, reason):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            m.get(url__regex=r"https://api\.stripe\.com/v1/charges.*").respond(status, json={})
            b = run(make())["body_markdown"]
        assert f"- Stripe for FlipDeck: unchecked ({reason})." in b
        assert "FlipDeck site: live" in b                                      # the other sources still show

    def test_a_timeout_and_a_connection_failure_are_unchecked(self):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            m.get(url__regex=r"https://api\.render\.com/v1/services/srv-flip/deploys.*").mock(side_effect=httpx.ConnectTimeout("slow"))
            m.get(url__regex=r"https://api\.github\.com/repos/acme/flipdeck/commits.*").mock(side_effect=httpx.ConnectError("down"))
            b = run(make())["body_markdown"]
        assert "- Render deploys for FlipDeck: unchecked (timed out)." in b and "- GitHub commits for FlipDeck: unchecked (could not connect)." in b

    def test_an_answer_that_is_not_json_is_unchecked(self):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            m.get(url__regex=r"https://api\.stripe\.com/v1/charges.*").respond(200, content=b"<html>oops</html>")
            assert "- Stripe for FlipDeck: unchecked (unreadable answer)." in run(make())["body_markdown"]

    def test_sources_a_server_cannot_read_are_said_to_be_unchecked_for_each_product(self):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            b = run(make())["body_markdown"]
        for product in ("FlipDeck", "Marnyx"):
            assert f"Gmail replies from vendors and platforms for {product}: unchecked (not connected to the Aibussol service)" in b
            assert f"User and visitor numbers for {product}: unchecked" in b

    def test_the_act_on_first_line_says_how_many_sources_were_unchecked(self):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            b = run(make())
        assert "unchecked, so check those by hand" in b["body_markdown"] and "sources were unchecked" in b["spoken_summary"] and b["spoken_summary"].startswith("Good evening, Michael.")


class TestOneOfEverythingAndNoMeta:
    def test_each_section_appears_once_per_product_and_the_brief_ends_once(self):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            b = run(make())["body_markdown"]
        assert b.count("## FlipDeck") == 1 and b.count("## Marnyx") == 1 and b.count("## Act on first") == 1
        for heading in ("### Completed", "### Numbers", "### Pending", "### Errors"):
            assert b.count(heading) == 2, heading                                 # once under each product, never twice under one
        lines = [l for l in b.split("\n") if l.startswith("- ")]
        assert len(lines) == len({bc._norm(l) for l in lines})

    def test_a_meta_ads_error_in_the_logs_never_reaches_the_brief(self):
        logs = [{"message": "[Meta Ads] Token rejected by Meta: Invalid OAuth access token - Cannot parse access token."},
                {"message": "The database connection dropped once."}, {"message": "The database connection dropped once."}]
        with respx.mock(assert_all_called=False) as m:
            mock_world(m, logs=logs)
            b = run(make())
        assert "Meta" not in b["body_markdown"] and "token" not in b["body_markdown"].lower() and "Meta" not in b["spoken_summary"]
        assert "The database connection dropped once (seen 2 times)." in b["body_markdown"]
        assert "Read those first" in b["body_markdown"]

    def test_the_stored_brief_fits_the_feeds_limits(self):
        with respx.mock(assert_all_called=False) as m:
            mock_world(m)
            b = run(make())
        assert set(b) == {"product", "period", "body_markdown", "spoken_summary"} and len(b["spoken_summary"]) <= 600 and len(b["body_markdown"]) <= 20_000
        assert b["product"] == "FlipDeck and Marnyx"


class TestConfigAndDocs:
    def test_the_products_file_is_valid_and_has_no_secrets(self):
        cfg = job.load_config()
        assert [p["name"] for p in cfg["products"]] == ["FlipDeck", "Marnyx"]
        text = job.CONFIG_PATH.read_text(encoding="utf-8")
        for word in ("token", "secret", "password", "sk_", "Bearer"):
            assert word not in text, word
        json.loads(text)

    def test_no_key_is_ever_written_into_the_brief_or_the_module(self):
        src = Path(job.__file__).read_text(encoding="utf-8")
        assert "os.environ" in src and "RENDER_API_KEY" in src
        import re

        assert not re.search(r"(sk_live|rk_live|ghp_|rnd_)[A-Za-z0-9]{8,}", src)

    def test_the_cron_instructions_name_the_hourly_schedule_and_every_variable(self):
        doc = (Path(job.__file__).resolve().parents[3] / "docs" / "brief-cron.md").read_text(encoding="utf-8")
        for needle in ("52 * * * *", "python -m openjarvis.server.brief_job", "DATABASE_URL", "RENDER_API_KEY", "GITHUB_TOKEN", "STRIPE_READ_KEY", "BRIEF_INGEST_TOKEN"):
            assert needle in doc, needle
