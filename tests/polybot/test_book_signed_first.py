import asyncio

from openjarvis.polybot import client as cl
from openjarvis.polybot.client import PMError, PolymarketUS


def _c(key="k", secret="s"):
    c = PolymarketUS.__new__(PolymarketUS)
    c.key_id, c.secret_key = key, secret
    return c


def test_book_reads_the_trading_host_first_and_shares_it():
    cl._SHARED.clear()
    calls = []

    async def req(method, path, params=None, body=None, auth=False, tries=3):
        calls.append(auth)
        return {"marketData": {"offers": [{"px": "0.83", "qty": "5"}]}}
    a, b = _c("k1"), _c("k2")
    a._req = b._req = req
    asyncio.run(a.book("m"))
    asyncio.run(b.book("m"))                   # another account, same market, inside 0.8 s: the shared read
    assert calls == [True]


def test_book_falls_back_to_public_when_signed_fails():
    cl._SHARED.clear()
    calls = []

    async def req(method, path, params=None, body=None, auth=False, tries=3):
        calls.append(auth)
        if auth:
            raise PMError("throttled", status=429)
        return {"marketData": {"bids": []}}
    c = _c()
    c._req = req
    assert asyncio.run(c.book("m2")) == {"bids": []}
    assert calls == [True, False]


def test_no_keys_reads_public():
    cl._SHARED.clear()
    calls = []

    async def req(method, path, params=None, body=None, auth=False, tries=3):
        calls.append(auth)
        return {"marketData": {}}
    c = _c("", "")
    c._req = req
    asyncio.run(c.book("m3"))
    assert calls == [False]
