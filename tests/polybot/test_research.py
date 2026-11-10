import json
import os
import tempfile

from openjarvis.polybot import research


def test_blackout_covers_fifteen_minutes_each_side_and_names_the_event():
    events = [(1000.0, "CPI")]
    assert research.in_blackout(99.0, events) is None
    assert research.in_blackout(1000.0 - 900, events) == "CPI"
    assert research.in_blackout(1000.0 + 900, events) == "CPI"
    assert research.in_blackout(1000.0 + 901, events) is None


def test_load_events_reads_utc_times_and_empty_calendar_means_no_events():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cal.json")
        with open(p, "w") as f:
            json.dump([{"at": "2026-12-10T13:30:00Z", "name": "CPI"}], f)
        ev = research.load_events(p)
        assert ev == [(research._unix("2026-12-10T13:30:00Z"), "CPI")]
    assert research.load_events("/nonexistent/calendar.json") == []
    shipped = os.path.join(os.path.dirname(research.__file__), "blackout_calendar.json")
    assert len(research.load_events(shipped)) == 5


def test_vol_bucket_uses_the_btc_cutoffs_and_none_without_sigma():
    assert research.vol_bucket(None) is None
    assert research.vol_bucket(2.0) == "low"
    assert research.vol_bucket(3.5) == "mid"
    assert research.vol_bucket(5.5) == "high"


def test_blackout_is_off_by_default_and_the_shipped_calendar_has_the_december_events():
    assert research.BLACKOUT_ON is False
    names = [n for _, n in research.shipped_events()]
    assert any("FOMC" in n for n in names) and any("CPI" in n for n in names)


def test_candidate_row_records_the_blocking_rule_and_the_vol_tag():
    row = research.candidate_row("btc-5m-x", 1790812800.0, 120.0, "down", 0.75, 0.6, -0.1, 0.02, "mid",
                                 "btc down 70-79c")
    assert row["blocked_by"] == "btc down 70-79c"
    assert row["vol_tag"] == "mid"
    assert row["time_utc"].startswith("2026-10-01T00:00:00")
    allowed = research.candidate_row("s", 1790812800.0, 120.0, "up", 0.5, None, None, None, None, None)
    assert allowed["blocked_by"] == "" and allowed["vol_tag"] is None and allowed["fair"] is None


def test_entry_check_names_the_rule_queues_each_candidate_once_and_blackout_blocks_when_on(monkeypatch):
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk

    btc = Desk(from_env({}, asset="btc"), None, None)

    class W:
        slug = "btc-5m-test"
        end = 1000.0

    now = 900.0
    assert btc._entry_check(now, W, 0.75, 0.5, "down") == "btc down 70-79c"
    assert btc._entry_check(now + 1, W, 0.75, 0.5, "down") == "btc down 70-79c"   # same candidate: not queued twice
    assert len(btc.ledger.candidates) == 1
    assert btc.ledger.candidates[0]["blocked_by"] == "btc down 70-79c" and btc.ledger.candidates[0]["asset"] == "btc"
    assert btc._entry_check(now, W, 0.50, 0.5, "up") is None                       # allowed: queued with no rule
    assert len(btc.ledger.candidates) == 2 and btc.ledger.candidates[1]["blocked_by"] == ""
    # the blackout is off by default; when switched on it stops every entry inside the window
    monkeypatch.setattr(research, "shipped_events", lambda: [(now, "CPI")])
    assert btc._entry_check(now, W, 0.50, 0.5, "up") is None
    monkeypatch.setattr(research, "BLACKOUT_ON", True)
    assert btc._entry_check(now, W, 0.50, 0.5, "up") == "blackout: CPI"
