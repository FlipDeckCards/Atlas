"""2026-11-19: commodity desks recognize the daily futures/metals break (5-6 PM New York, Mon-Thu) and the weekend
(Fri 5 PM - Sun 6 PM New York) and stop looking up windows until the market reopens, with one log line."""
import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk, commodity_closed_until

NY = ZoneInfo("America/New_York")


def ts(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=NY).timestamp()


def test_daily_break_and_weekend():
    assert commodity_closed_until(ts(2026, 11, 19, 16, 59)) is None                     # Thu 4:59 PM: open
    assert commodity_closed_until(ts(2026, 11, 19, 17, 5)) == ts(2026, 11, 19, 18)      # Thu 5:05 PM: back at 6 PM
    assert commodity_closed_until(ts(2026, 11, 19, 18, 0)) is None
    assert commodity_closed_until(ts(2026, 11, 20, 17, 30)) == ts(2026, 11, 22, 18)     # Fri 5:30 PM: back Sun 6 PM
    assert commodity_closed_until(ts(2026, 11, 21, 12)) == ts(2026, 11, 22, 18)         # Saturday
    assert commodity_closed_until(ts(2026, 11, 22, 17, 59)) == ts(2026, 11, 22, 18)     # Sun 5:59 PM
    assert commodity_closed_until(ts(2026, 11, 22, 18, 1)) is None                       # Sun 6:01 PM: open


def test_closed_commodity_desk_does_not_look_up_and_logs_once():
    class C:
        async def event_markets(self, slug):
            raise AssertionError("no lookups while closed")

        async def market(self, slug):
            raise AssertionError("no lookups while closed")
    d = Desk(from_env({}, asset="gold"), C(), None)
    logs = []
    d.log = lambda lvl, msg: logs.append(msg)
    asyncio.run(d.find_window(ts(2026, 11, 19, 17, 1)))
    asyncio.run(d.find_window(ts(2026, 11, 19, 17, 2)))
    assert len([m for m in logs if "market closed" in m]) == 1 and d.window is None
    assert "Market closed until" in d.reason
