"""What a brief says: greeting from the Los Angeles clock, no Meta Ads, no repeats, 'unchecked' wording. Offline."""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess

import pytest

from openjarvis.server import brief_content as bc
from openjarvis.server.brief_content import LA
from tests.server.test_briefs import _env, brief, ingest_h, post, world  # noqa: F401  (fixtures and helpers of the brief feed tests)


def la(h, m=0, day=(2026, 10, 16)):
    return dt.datetime(*day, h, m, tzinfo=LA)


class TestGreeting:
    @pytest.mark.parametrize("h,m,want", [
        (0, 0, "evening"), (4, 59, "evening"), (5, 0, "morning"), (6, 52, "morning"), (11, 59, "morning"),
        (12, 0, "afternoon"), (12, 52, "afternoon"), (16, 59, "afternoon"), (17, 0, "evening"), (21, 52, "evening"), (22, 30, "evening"), (23, 59, "evening"),
    ])
    def test_the_period_comes_from_the_los_angeles_clock(self, h, m, want):
        assert bc.greeting_period(la(h, m)) == want
        assert bc.greeting_line(la(h, m)) == f"Good {want}, Michael."

    def test_a_brief_made_at_10_30_pm_says_good_evening_whatever_slot_it_belongs_to(self):
        assert bc.greeting_line(la(22, 30)) == "Good evening, Michael."          # the bug: this said "Good morning"

    def test_the_time_is_read_in_los_angeles_not_utc(self):
        utc = dt.datetime(2026, 10, 17, 5, 30, tzinfo=dt.timezone.utc)          # 10:30 PM on Oct 16 in Los Angeles
        assert bc.greeting_period(utc) == "evening"
        assert bc.greeting_period(dt.datetime(2026, 10, 17, 14, 0, tzinfo=dt.timezone.utc)) == "morning"      # 7 AM
        assert bc.greeting_period(dt.datetime(2026, 10, 17, 19, 0)) == "afternoon"                              # a naive time is UTC: noon in LA

    def test_it_follows_daylight_saving_time(self):
        assert bc.greeting_period(dt.datetime(2026, 11, 1, 12, 59, tzinfo=dt.timezone.utc)) == "evening"       # 04:59 PST after the clocks went back
        assert bc.greeting_period(dt.datetime(2026, 11, 1, 13, 0, tzinfo=dt.timezone.utc)) == "morning"        # 05:00 PST
        assert bc.greeting_period(dt.datetime(2026, 10, 31, 12, 0, tzinfo=dt.timezone.utc)) == "morning"       # 05:00 PDT the day before
        assert bc.greeting_period(dt.datetime(2026, 3, 8, 11, 59, tzinfo=dt.timezone.utc)) == "evening"        # spring forward: 04:59 PDT
        assert bc.greeting_period(dt.datetime(2026, 3, 8, 12, 0, tzinfo=dt.timezone.utc)) == "morning"         # 05:00 PDT

    def test_a_greeting_the_writer_put_at_the_top_is_removed(self):
        for first in ("Good morning, Michael.", "Good morning!", "## Good morning", "**Good Morning Michael**", "good evening, Michael"):
            out = bc.strip_leading_greeting(first + "\n\n## FlipDeck\n- Something happened today.")
            assert out.startswith("## FlipDeck"), first
        assert bc.strip_leading_greeting("## FlipDeck\nGood morning to the team.").startswith("## FlipDeck")    # only at the top

    def test_the_ui_uses_the_same_boundaries(self):
        """The page's own JavaScript is run on the same times as the Python function."""
        node = shutil.which("node")
        if not node:
            pytest.skip("node is not installed")
        from pathlib import Path

        from openjarvis.server import atlas_ui

        src = Path(atlas_ui.__file__).read_text(encoding="utf-8")
        start = src.index("function briefGreetingPeriod")
        end = src.index("function addBriefMessage")
        js = src[start:end] + "\nconst out = []; for (const iso of process.argv.slice(1)) out.push(briefGreeting(iso)); console.log(JSON.stringify(out));"
        moments = [dt.datetime(2026, 10, 17, h, m, tzinfo=dt.timezone.utc) for h, m in ((11, 59), (12, 0), (14, 0), (18, 59), (19, 0), (23, 59), (0, 0), (5, 30), (4, 52))]
        r = subprocess.run([node, "-e", js, *[d.isoformat() for d in moments]], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[:300]
        import json

        assert json.loads(r.stdout) == [bc.greeting_line(d) for d in moments]


class TestMetaAds:
    BODY = ("## FlipDeck\n### Completed\n- A deploy went live tonight at eight.\n### Meta Ads\n- Ads spend was zero today.\n- The token expired.\n"
            "### Pending\n- Meta Ads is not connected: Meta rejected the stored access token today.\n- The PriceCharting licence reply is still open.\n"
            "### Errors\n- [Meta Ads] Token rejected by Meta, showing \"Meta not connected\". Detail: Invalid OAuth access token - Cannot parse access token.\n"
            "- The database connection dropped once at nine.\n")

    def test_the_section_and_every_token_error_line_are_gone(self):
        out = bc.remove_meta_ads(self.BODY)
        for gone in ("Meta Ads", "access token", "OAuth", "Meta not connected", "Ads spend"):
            assert gone not in out, gone
        assert "A deploy went live tonight" in out and "PriceCharting licence reply" in out and "database connection dropped" in out

    def test_other_meta_topics_are_kept(self):
        keep = "- The Meta App Review decision for the Marnyx login is still pending.\n"
        assert bc.remove_meta_ads("## Marnyx\n### Pending\n" + keep) .count("Meta App Review") == 1

    def test_a_section_about_ads_is_dropped_until_the_next_heading_of_the_same_level(self):
        out = bc.remove_meta_ads("## A\n### Meta ads\ntext one here\n#### deeper\ntext two here\n### Next\nkept text here\n")
        assert "text one" not in out and "text two" not in out and "kept text here" in out

    def test_the_spoken_version_loses_those_sentences_too(self):
        s = bc.sanitize_spoken("Good morning. FlipDeck has one new subscriber. Meta rejected the access token. Marnyx is quiet.")
        assert s == "FlipDeck has one new subscriber. Marnyx is quiet."


class TestRepeats:
    def test_a_repeated_bullet_or_paragraph_appears_once(self):
        t = "## FlipDeck\n### Completed\n- The website deploy went live at eight tonight.\n- The website deploy went live at eight tonight!\n- A second thing happened too.\n"
        out = bc.dedupe_within(t)
        assert out.count("website deploy went live") == 1 and "second thing" in out

    def test_a_repeated_section_appears_once(self):
        t = "## FlipDeck\n### Completed\n- First item is here today.\n### Errors\n- None at all today.\n### Completed\n- First item is here today.\n- A different third item.\n"
        out = bc.dedupe_within(t)
        assert out.count("### Completed") == 1 and "different third" not in out          # the whole repeated section goes, including its new bullet

    def test_the_same_heading_under_two_products_is_not_a_repeat(self):
        t = "## FlipDeck\n### Completed\n- FlipDeck had a deploy tonight.\n## Marnyx\n### Completed\n- Marnyx had a different deploy tonight.\n"
        out = bc.dedupe_within(t)
        assert out.count("### Completed") == 2 and "Marnyx had" in out and "FlipDeck had" in out

    def test_short_lines_may_repeat(self):
        t = "## A\n### One\n- None.\n### Two\n- None.\n"
        assert bc.dedupe_within(t).count("- None.") == 2

    def test_a_heading_with_nothing_left_is_removed(self):
        t = "## A\n### One\n- This sentence is long enough here.\n### Two\n- This sentence is long enough here.\n"
        out = bc.dedupe_within(t)
        assert "### Two" not in out and "### One" in out

    PREVIOUS = "## FlipDeck\n### Completed\n- The website deploy went live at eight tonight.\n### Pending\n- The PriceCharting licence reply is still open.\n### Errors\n- The database dropped once at nine.\n"

    def test_items_already_reported_are_left_out_of_completed_and_errors(self):
        today = ("## FlipDeck\n### Completed\n- The website deploy went live at eight tonight.\n- A new commit was merged at ten.\n"
                 "### Pending\n- The PriceCharting licence reply is still open.\n### Errors\n- The database dropped once at nine.\n")
        out = bc.drop_already_reported(today, self.PREVIOUS)
        assert "went live at eight" not in out and "new commit was merged" in out
        assert "PriceCharting licence" in out                                       # pending items may stay pending
        assert "- Nothing new since the last brief." in out and out.count("database dropped") == 0

    def test_without_a_previous_brief_nothing_is_dropped(self):
        t = "## FlipDeck\n### Completed\n- The website deploy went live at eight tonight.\n"
        assert bc.drop_already_reported(t, None) == t

    def test_sanitize_never_returns_an_empty_brief(self):
        assert bc.sanitize_brief_body("Good morning, Michael.") == "Good morning, Michael."


class TestUnchecked:
    def test_the_wording_has_no_number_in_it(self):
        line = bc.unchecked_line("Stripe", "access refused")
        assert line == "- Stripe: unchecked (access refused)."
        assert bc.unchecked_line("X", "").endswith("(could not be read).")


class TestIngest:
    DIRTY = {"product": "Marnyx and FlipDeck", "period": "morning",
             "body_markdown": "Good morning, Michael.\n\n## FlipDeck\n### Completed\n- The website deploy went live at eight tonight.\n- The website deploy went live at eight tonight.\n"
                              "### Errors\n- [Meta Ads] Token rejected by Meta, showing \"Meta not connected\".\n## Act on first\nCheck the Stripe dashboard first today.",
             "spoken_summary": "Good morning. One deploy went live. Meta rejected the access token."}

    def test_every_posted_brief_is_cleaned_before_it_is_stored(self, world):
        assert post(world, body=self.DIRTY).status_code == 201
        row = world.store.rows[-1]
        assert not row["body"].startswith("Good") and row["body"].count("website deploy") == 1 and "Meta" not in row["body"] and "Errors" not in row["body"]
        assert row["spoken_summary"] == "One deploy went live."

    def test_items_in_the_previous_brief_of_the_same_product_are_not_repeated(self, world):
        world.clock[0] = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)
        first = dict(self.DIRTY, period="afternoon")
        assert post(world, body=first).status_code == 201
        world.clock[0] = dt.datetime.now(dt.timezone.utc)
        second = dict(self.DIRTY, period="evening", body_markdown=self.DIRTY["body_markdown"].replace("- The website deploy went live at eight tonight.\n- The website deploy went live at eight tonight.\n",
                                                                                                      "- The website deploy went live at eight tonight.\n- A brand new commit was merged at ten.\n"))
        assert post(world, body=second).status_code == 201
        body = world.store.rows[-1]["body"]
        assert "went live at eight" not in body and "brand new commit" in body

    def test_an_old_previous_brief_is_ignored(self, world):
        world.clock[0] = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=40)
        assert post(world, body=dict(self.DIRTY, period="afternoon")).status_code == 201
        world.clock[0] = dt.datetime.now(dt.timezone.utc)
        assert post(world, body=dict(self.DIRTY, period="evening")).status_code == 201
        assert "went live at eight" in world.store.rows[-1]["body"]

    def test_a_clean_brief_is_stored_exactly_as_sent(self, world):
        assert post(world).status_code == 201
        assert world.store.rows[-1]["body"] == "# Morning\n- all quiet" and world.store.rows[-1]["spoken_summary"] == "All quiet this morning."

    def test_the_page_shows_a_greeting_from_the_time_the_brief_was_made(self):
        from pathlib import Path

        from openjarvis.server import atlas_ui

        src = Path(atlas_ui.__file__).read_text(encoding="utf-8")
        block = src[src.index("function addBriefMessage"):src.index("async function checkBriefs")]
        assert "briefGreeting(b.created_at)" in block and "America/Los_Angeles" in src and "b.period" in block      # the slot name is only a label
