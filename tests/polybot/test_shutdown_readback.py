import asyncio

from openjarvis.polybot.brokers import Fill
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


class _Broker:
    def __init__(self):
        self.closing = {"o1": object()}
        self._last_sync = 99.0
        self.calls = 0

    async def sync(self, book, now):
        self.calls += 1
        assert self._last_sync == 0.0                  # the throttle is bypassed for the final read
        self.closing = {}
        return [Fill("w1", "up", 0.14, 4.0, now, "o1")]


def test_shutdown_books_a_fill_that_landed_during_the_cancel():
    d = Desk(from_env({}, asset="sol"), None, None)
    d.broker = _Broker()
    got = []

    async def on_fills(fs):
        got.extend(fs)
    d.on_fills = on_fills

    async def nap(s):
        return None
    d.sleep = nap
    asyncio.run(d.final_readback())
    assert d.broker.calls == 1 and len(got) == 1 and got[0].qty == 4.0


def test_nothing_closing_reads_nothing():
    d = Desk(from_env({}, asset="sol"), None, None)
    d.broker.closing = {} if hasattr(d.broker, "closing") else None
    asyncio.run(d.final_readback())
