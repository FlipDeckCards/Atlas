"""Robinhood engine (2026-11-20): client requests, sizing, paper execution, the live gate and approvals; no network."""

import asyncio
import json

import pytest

from openjarvis.server.crew import rh_engine as E
from openjarvis.server.crew import robinhood as rh

ENV = {"ROBINHOOD_API_KEY": "k", "ROBINHOOD_PRIVATE_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}


class Resp:
    def __init__(self, data, code=200):
        self._d, self.status_code, self.text = data, code, json.dumps(data)

    def json(self):
        return self._d


class Http:
    def __init__(self, data):
        self.data, self.calls = data, []

    async def request(self, method, url, headers=None, content=None):
        self.calls.append((method, url, headers, content))
        return Resp(self.data)


def test_market_order_body_and_signed_headers():
    http = Http({"id": "x1", "state": "open"})
    c = rh.RHClient(env=ENV, http=http)
    o = asyncio.run(c.place_order("BTC-USD", "buy", client_order_id="cid", asset_quantity=0.00012))
    method, url, headers, body = http.calls[0]
    assert method == "POST" and url.endswith("/api/v1/crypto/trading/orders/") and o["id"] == "x1"
    b = json.loads(body)
    assert b == {"symbol": "BTC-USD", "client_order_id": "cid", "side": "buy", "type": "market",
                 "market_order_config": {"asset_quantity": "0.00012"}}
    assert {"x-api-key", "x-timestamp", "x-signature"} <= set(headers)


def test_limit_order_and_best_bid_ask_parsing():
    http = Http({"results": [{"symbol": "BTC-USD", "price": "60000", "bid_inclusive_of_sell_spread": "59900",
                              "ask_inclusive_of_buy_spread": "60100"}]})
    c = rh.RHClient(env=ENV, http=http)
    q = asyncio.run(c.best_bid_ask(["BTC-USD"]))
    assert q["BTC-USD"] == {"bid": 59900.0, "ask": 60100.0, "mid": 60000.0}
    assert "symbol=BTC-USD" in http.calls[0][1]
    http.data = {"id": "x2"}
    asyncio.run(c.place_order("ETH-USD", "sell", client_order_id="c2", quote_amount=25, limit_price=2000))
    b = json.loads(http.calls[-1][3])
    assert b["type"] == "limit" and b["limit_order_config"] == {"limit_price": "2000", "time_in_force": "gfd", "quote_amount": "25"}


class Conn:
    def __init__(self, log):
        self.log = log

    async def execute(self, sql, *a):
        self.log.append((sql, a))

    async def fetch(self, sql, *a):
        return []

    async def fetchval(self, sql, *a):
        return None


class Pool:
    def __init__(self):
        self.log = []

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self_):
                return Conn(pool.log)

            async def __aexit__(self_, *x):
                return False
        return Ctx()


def _engine():
    e = E.Engine(Pool(), client=rh.RHClient(env=ENV, http=Http({})), clock=lambda: 1000.0)
    e.prices = {"BTC": {"bid": 59900.0, "ask": 60100.0, "mid": 60000.0}}
    e.pairs = {"BTC-USD": {"asset_increment": "0.00000001", "min_order_size": "0.000001"}}
    return e


def test_no_strategy_means_no_trades():
    e = _engine()
    why = asyncio.run(e.run_strategy(1000.0))
    assert "No strategy" in why and not [x for x in e.pool.log if "rh_orders" in x[0]]


def test_paper_buy_is_sized_by_the_owners_limits_and_fills_at_the_ask():
    e = _engine()
    E.STRATEGIES["t"] = lambda snap: [E.Signal("BTC", "buy", "test")]
    try:
        e.control = {**E.DEFAULT_CONTROL, "strategy": "t"}
        asyncio.run(e.run_strategy(1000.0))
    finally:
        E.STRATEGIES.pop("t")
    p = e.paper.pos["BTC"]
    assert abs(p.qty * 60100.0 - 30.0) < 0.01 and p.avg_cost == 60100.0          # 3% of the $1,000 paper account, at the ask
    assert abs(e.paper.cash - 970.0) < 0.01
    fills = [x for x in e.pool.log if "INSERT INTO rh_fills" in x[0]]
    assert fills and fills[0][1][0] == "paper"


def test_live_needs_a_strategy_and_waits_for_approval():
    e = _engine()
    e.control = {**E.DEFAULT_CONTROL, "mode": "live"}
    assert e.can_go_live() == "no strategy is selected" or e.can_go_live() == "Robinhood is not connected"
    E.STRATEGIES["t"] = lambda snap: [E.Signal("BTC", "buy", "test")]
    import os
    old = {k: os.environ.get(k) for k in ENV}
    os.environ.update(ENV)
    try:
        e.control = {**E.DEFAULT_CONTROL, "mode": "live", "strategy": "t"}
        e.live.cash = 500.0
        assert e.can_go_live() is None
        asyncio.run(e.run_strategy(1000.0))
    finally:
        E.STRATEGIES.pop("t")
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    orders = [x for x in e.pool.log if "INSERT INTO rh_orders" in x[0]]
    assert orders and orders[0][1][8] == "pending_approval"                       # nothing sent to Robinhood yet
    assert not e.c.http.calls


def test_position_average_cost_and_realized_pnl():
    p = E.Position()
    p.buy(1.0, 100.0)
    p.buy(1.0, 200.0)
    assert p.avg_cost == 150.0
    assert p.sell(1.0, 180.0) == 30.0 and p.qty == 1.0


def test_a_paused_desk_skips_its_coin_only():
    e = _engine()
    E.STRATEGIES["t"] = lambda snap: [E.Signal("BTC", "buy", "test")]
    try:
        e.control = E._merge_control({"strategy": "t", "coins": {"BTC": {"paused": True}}})
        asyncio.run(e.run_strategy(1000.0))
    finally:
        E.STRATEGIES.pop("t")
    assert e.paper.pos["BTC"].qty == 0
    assert any("BTC desk is paused" in str(x[1]) for x in e.pool.log)


def test_daily_stop_blocks_buys():
    e = _engine()
    e.control = E._merge_control({"strategy": "t", "daily_stop_pct": 5})
    e.day = {"date": "x", "start": {"paper": 1100.0}}                 # $1,000 now vs $1,100 at the start: -9%
    assert e.stopped_today()
    E.STRATEGIES["t"] = lambda snap: [E.Signal("BTC", "buy", "test")]
    try:
        asyncio.run(e.run_strategy(1000.0))
    finally:
        E.STRATEGIES.pop("t")
    assert e.paper.pos["BTC"].qty == 0


def test_fill_features_and_control_merge():
    e = _engine()
    asyncio.run(e.record_fill("o1", "paper", "BTC", "buy", 0.001, 60100.0, "t"))
    sql, args = [x for x in e.pool.log if "INSERT INTO rh_fills" in x[0]][0]
    f = json.loads(args[-1])
    assert f["spread_pct"] == round(100 * 200 / 60000, 4) and f["mid"] == 60000.0 and "hour_la" in f
    c = E._merge_control({"coins": {"XRP": {"paused": True}}})
    assert c["coins"]["XRP"]["paused"] and not c["coins"]["BTC"]["paused"] and c["daily_stop_pct"] == 8.0
