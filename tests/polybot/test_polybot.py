"""Tests for the Polymarket desk: pricing model, quoting rules, risk stops, paper fills, signing, and a whole simulated window."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import math
import random

import pytest

from openjarvis.polybot import model, risk, strategy
from openjarvis.polybot.brokers import BookView, PaperBroker, parse_ts
from dataclasses import replace

from openjarvis.polybot.config import Config, Limits, apply_control, from_env
from openjarvis.polybot.strategy import Book, Inventory, Quote


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── model ───────────────────────────────────────────────────────────────────────────────

def tape_from(prices, start=1_000_000):
    t = model.PriceTape()
    for i, p in enumerate(prices):
        t.add(start + i, p)
    return t, start


def test_fair_is_half_when_at_the_strike_with_time_left():
    t, s = tape_from([80000.0] * 3700)
    now = s + 3699
    assert model.fair_up(t, now, now + 240, 80000.0, sigma=2.0) == pytest.approx(0.5, abs=1e-6)


def test_fair_moves_with_distance_and_shrinks_with_time():
    t, s = tape_from([80050.0] * 3700)
    now = s + 3699
    far = model.fair_up(t, now, now + 240, 80000.0, sigma=2.0)
    near = model.fair_up(t, now, now + 70, 80000.0, sigma=2.0)
    assert 0.5 < far < near < 1.0
    below = model.fair_up(t, now, now + 240, 80100.0, sigma=2.0)
    assert below < 0.5


def test_inside_the_closing_minute_known_seconds_count():
    # the first 50 seconds of the closing average were far above the strike; the last 10 can barely change it
    prices = [80000.0] * 3640 + [80200.0] * 50
    t, s = tape_from(prices)
    now = s + len(prices)            # 50 seconds into the averaging minute (k = 50, m = 10)
    t.add(now, 79990.0)              # even a drop right now cannot pull the average below 80000
    end = now + 10
    assert model.fair_up(t, now, end, 80000.0, sigma=5.0) > 0.99


def test_sigma_uses_minute_returns():
    rnd = random.Random(1)
    p, prices = 80000.0, []
    for _ in range(3700):
        p *= math.exp(rnd.gauss(0, 0.0002))
        prices.append(p)
    t, s = tape_from(prices)
    sig = t.sigma_per_s(s + 3699)
    assert sig == pytest.approx(80000 * 0.0002, rel=0.35)


def test_settle_uses_the_last_60_seconds_before_the_end():
    prices = [100.0] * 60 + [101.0] * 60
    t, s = tape_from(prices)
    end = s + 120
    assert model.settle(t, end, 100.5) == (101.0, True)
    assert model.settle(t, end, 101.0) == (101.0, True)        # a flat print settles Up
    assert model.settle(t, end, 101.01)[1] is False


def test_model_is_calibrated_on_a_random_walk():
    """Across many simulated windows the predicted probabilities match how often Up actually happened."""
    rnd = random.Random(7)
    buckets = {}
    for _ in range(400):
        p, prices = 80000.0, []
        for _ in range(3600 + 360):
            p += rnd.gauss(0, 3.0)
            prices.append(p)
        t, s = tape_from(prices)
        ws = s + 3600
        k = t.average(ws - 60, ws - 1)
        now = ws + 120
        f = model.fair_up(t, now, ws + 300, k, sigma=3.0)
        up = model.settle(t, ws + 300, k)[1]
        b = min(4, int(f * 5))
        n, u, fs = buckets.get(b, (0, 0, 0.0))
        buckets[b] = (n + 1, u + up, fs + f)
    for b, (n, u, fs) in buckets.items():
        if n >= 40:
            assert abs(u / n - fs / n) < 0.12, (b, n, u / n, fs / n)


# ── strategy ────────────────────────────────────────────────────────────────────────────

LIM = Limits(bankroll_usd=50, max_window_usd=5, max_order_usd=2, daily_loss_usd=10, min_edge=0.04)


def test_quotes_keep_the_edge_and_never_cross():
    qs = strategy.quotes(0.60, Book(0.55, 0.57), Inventory(), LIM, budget_left=50)
    up = next(q for q in qs if q.side == "up")
    down = next(q for q in qs if q.side == "down")
    assert up.price <= 0.56 and up.price < 0.57             # at most fair - edge, below the ask
    assert down.price >= 0.64 and down.price > 0.55         # Down costs at most 1 - fair - edge
    assert up.cost <= 2.0 + 1e-9 and down.cost <= 2.0 + 1e-9


def test_quotes_join_one_tick_inside_when_the_edge_allows():
    qs = strategy.quotes(0.50, Book(0.40, 0.60), Inventory(), LIM, budget_left=50)
    assert {q.side: q.price for q in qs} == {"up": 0.41, "down": 0.59}


def test_no_quote_when_the_market_is_already_past_fair():
    qs = strategy.quotes(0.50, Book(0.49, 0.50), Inventory(), LIM, budget_left=50)
    up = [q for q in qs if q.side == "up"]
    assert up and up[0].price <= 0.46                     # it rests deeper, never at the market's 0.49
    assert all(q.price < 0.50 for q in up)


def test_window_cap_and_budget_limit_size():
    inv = Inventory(up_qty=8, up_cost=4.8)
    qs = strategy.quotes(0.6, Book(0.3, 0.9), inv, LIM, budget_left=50)
    assert sum(q.cost for q in qs) <= 0.2 + 1e-9
    assert strategy.quotes(0.6, Book(0.3, 0.9), Inventory(), LIM, budget_left=0.01) == []


def test_skew_demands_more_edge_on_the_heavy_side():
    heavy = Inventory(up_qty=8, up_cost=3.0)
    assert strategy.skew_edge(heavy, "up", LIM) > 0
    assert strategy.skew_edge(heavy, "down", LIM) == 0


def test_inventory_pnl():
    inv = Inventory(up_qty=4, up_cost=1.8, down_qty=4, down_cost=1.9)
    assert inv.pnl_if(True) == pytest.approx(0.3)
    assert inv.worst_case == pytest.approx(0.3)            # a matched pair under $1 locks profit


# ── config and risk ─────────────────────────────────────────────────────────────────────

def test_live_needs_both_keys():
    base = from_env({"POLYMARKET_KEY_ID": "k", "POLYMARKET_SECRET_KEY": "s"})
    assert apply_control(base, {"mode": "live"}).mode == "paper"                 # worker does not allow live
    base = from_env({"POLYMARKET_KEY_ID": "k", "POLYMARKET_SECRET_KEY": "s", "POLYBOT_ALLOW_LIVE": "true"})
    assert apply_control(base, {}).mode == "paper"                               # owner has not approved
    assert apply_control(base, {"mode": "live"}).mode == "live"
    assert apply_control(from_env({"POLYBOT_ALLOW_LIVE": "true"}), {"mode": "live"}).mode == "paper"   # no keys


def test_control_limits_apply_and_are_clamped():
    base = from_env({})
    cfg = apply_control(base, {"limits": {"bankroll_usd": 20, "max_window_usd": 50, "min_edge": 0.001}})
    assert cfg.limits.bankroll_usd == 20 and cfg.limits.max_window_usd == 20 and cfg.limits.min_edge == 0.01
    assert Limits(bankroll_usd=20).tighter_than(Limits(bankroll_usd=50))
    assert not Limits(min_edge=0.02).tighter_than(Limits(min_edge=0.04))


def test_maintenance_window_is_thursday_morning_eastern():
    thu = dt.datetime(2026, 10, 29, 6, 30, tzinfo=risk.ET).timestamp()
    wed = dt.datetime(2026, 10, 28, 6, 30, tzinfo=risk.ET).timestamp()
    assert risk.in_maintenance(thu) and not risk.in_maintenance(wed)


def _blocked(cfg, rs, **kw):
    args = dict(now=dt.datetime(2026, 10, 28, 12, tzinfo=risk.ET).timestamp(), data_age_s=1, book_age_s=1, market_open=True,
                secs_since_open=60, secs_to_end=120, balance=None)
    args.update(kw)
    return risk.blocked(cfg, rs, **args)


def test_risk_stops():
    cfg = from_env({})
    assert _blocked(cfg, risk.RiskState()) is None
    assert "Kill" in _blocked(cfg, risk.RiskState(kill=True))
    assert _blocked(cfg, risk.RiskState(day_pnl=-7.5)) is None                         # paper keeps collecting data
    assert "Daily loss" in _blocked(replace(cfg, mode="live"), risk.RiskState(day_pnl=-7.5))
    assert "stale" in _blocked(cfg, risk.RiskState(), data_age_s=10)
    assert "close" in _blocked(cfg, risk.RiskState(), secs_to_end=10)
    assert "not open" in _blocked(cfg, risk.RiskState(), market_open=False)


def test_budget_never_allows_losing_past_the_daily_limit():
    cfg = replace(from_env({"POLYBOT_DAILY_LOSS_PCT": "0"}), mode="live")     # flat $10, live
    assert risk.budget_left(cfg, risk.RiskState(day_pnl=-8.0, at_risk=1.0), None) == pytest.approx(1.0)
    assert risk.budget_left(cfg, risk.RiskState(), None) == pytest.approx(10.0)


def test_daily_stop_is_15_percent_of_the_days_starting_balance():
    cfg = replace(from_env({}), mode="live")
    assert risk.day_limit(cfg, risk.RiskState(day_start=50.0)) == pytest.approx(7.5)
    assert risk.day_limit(cfg, risk.RiskState(day_start=80.0)) == pytest.approx(12.0)      # grows with the account
    assert risk.budget_left(cfg, risk.RiskState(day_start=50.0), None) == pytest.approx(7.5)
    assert "Daily loss" in _blocked(cfg, risk.RiskState(day_start=50.0, day_pnl=-7.5))
    assert _blocked(cfg, risk.RiskState(day_start=50.0, day_pnl=-7.0)) is None
    rs = risk.RiskState(day="2000-01-01", day_pnl=-3, day_start=40)
    rs.roll(dt.datetime(2026, 10, 29, 12, tzinfo=risk.LA).timestamp())                    # a new day starts fresh
    assert rs.day_pnl == 0 and rs.day_start is None


# ── paper fills ─────────────────────────────────────────────────────────────────────────

def bv(bids, offers, ts=0.0, last=None, last_ts=0.0, qty=None):
    return BookView(bids=bids, offers=offers, last_trade_px=last, last_trade_qty=qty, last_trade_ts=last_ts, state="MARKET_STATE_OPEN", ts=ts)


def test_paper_fills_only_when_the_market_goes_through():
    b = PaperBroker()
    run(b.set_quotes("w1", [Quote("up", 0.45, 4), Quote("down", 0.60, 4)], now=100))
    assert run(b.sync(bv([(0.46, 10)], [(0.55, 10)], ts=101), now=101)) == []
    assert run(b.sync(bv([(0.46, 10)], [(0.55, 10)], ts=102, last=0.45, last_ts=102, qty=3), now=102)) == []    # a trade AT our price: someone was ahead
    f = run(b.sync(bv([(0.40, 10)], [(0.45, 1.5), (0.50, 10)], ts=103), now=103))
    assert [(x.side, x.qty, x.price) for x in f] == [("up", 1.5, 0.45)]
    f = run(b.sync(bv([(0.61, 20)], [(0.70, 10)], ts=104), now=104))
    assert [(x.side, x.qty) for x in f] == [("down", 4)]
    assert f[0].cost == pytest.approx(1.6)


def test_paper_reprice_is_a_new_order():
    b = PaperBroker()
    run(b.set_quotes("w1", [Quote("up", 0.45, 4)], now=100))
    first = b.resting["up"].order_id
    run(b.set_quotes("w1", [Quote("up", 0.44, 4)], now=101))
    assert b.resting["up"].order_id != first
    run(b.set_quotes("w2", [], now=102))
    assert b.resting == {}


def test_parse_ts_handles_nanoseconds():
    assert parse_ts("2026-10-29T15:36:26.938352977Z") == pytest.approx(dt.datetime(2026, 10, 29, 15, 36, 26, 938352, tzinfo=dt.timezone.utc).timestamp())
    assert parse_ts("") == 0.0


def test_book_view_parses_the_live_shape():
    md = {"marketSlug": "s", "state": "MARKET_STATE_OPEN",
          "bids": [{"px": {"value": "0.6300", "currency": "USD"}, "qty": "901.0000"}, {"px": {"value": "0.6400", "currency": "USD"}, "qty": "5"}],
          "offers": [{"px": {"value": "0.6600", "currency": "USD"}, "qty": "1823.0000"}],
          "stats": {"lastTradePx": {"value": "0.6400", "currency": "USD"}, "lastTradeQty": "7.6", "lastTradeSetTime": "2026-10-29T15:36:26.938352977Z"}}
    b = BookView.parse(md, 5.0)
    assert (b.best_bid, b.best_ask, b.last_trade_px, b.last_trade_qty) == (0.64, 0.66, 0.64, 7.6)


# ── signing and the HTTP client ─────────────────────────────────────────────────────────

def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    k = Ed25519PrivateKey.generate()
    seed = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return k, seed, pub


def test_signature_is_timestamp_method_path():
    from openjarvis.polybot.client import auth_headers

    k, seed, pub = _keypair()
    for secret in (base64.b64encode(seed).decode(), base64.b64encode(seed + pub).decode()):   # 32-byte seed or 64-byte seed+public
        h = auth_headers("key-1", secret, "post", "/v1/orders", now_ms=1700000000123)
        assert h["X-PM-Access-Key"] == "key-1" and h["X-PM-Timestamp"] == "1700000000123"
        k.public_key().verify(base64.b64decode(h["X-PM-Signature"]), b"1700000000123POST/v1/orders")


httpx = pytest.importorskip("httpx")


def test_place_sends_a_post_only_order_with_the_long_price():
    from openjarvis.polybot.client import PolymarketUS

    seen = {}

    def handler(req):
        seen["url"], seen["body"], seen["headers"] = str(req.url), json.loads(req.content), req.headers
        return httpx.Response(200, json={"id": "ord-9"})

    _, seed, _ = _keypair()
    c = PolymarketUS("kid", base64.b64encode(seed).decode(), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert run(c.place("cpc-btc-updown-5m-x", "down", 0.62, 3.0)) == "ord-9"
    b = seen["body"]
    assert seen["url"] == "https://api.polymarket.us/v1/orders"
    assert b["intent"] == "ORDER_INTENT_BUY_SHORT" and b["price"] == {"value": "0.62", "currency": "USD"}
    assert b["participateDontInitiate"] is True and b["quantity"] == 3.0
    assert "x-pm-signature" in {k.lower() for k in seen["headers"].keys()}


def test_order_post_is_not_retried_on_a_server_error():
    from openjarvis.polybot.client import PMError, PolymarketUS

    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503, text="busy")

    _, seed, _ = _keypair()
    c = PolymarketUS("kid", base64.b64encode(seed).decode(), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PMError):
        run(c.place("s", "up", 0.4, 1))
    assert len(calls) == 1


# ── a whole window, end to end, in paper mode ───────────────────────────────────────────

class FakeExchange:
    """Plays one 5-minute window: BTC drifts up, a seller later sweeps the Up offers below our bid."""

    def __init__(self, t0):
        self.t0 = t0                     # window start
        self.now = t0 - 3700
        self.ws, self.we = t0, t0 + 300

    def price(self, ts):
        return 80000.0 + (0.0 if ts < self.ws else min(150.0, (ts - self.ws) * 0.6)) + (2.0 if ts % 2 else -2.0)

    async def index_prices(self, asset, start, end):
        stop = int(self.now) if not end else end
        return [(t, self.price(t)) for t in range(int(start), stop + 1)]

    def _market(self):
        iso = lambda ts: dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"slug": "cpc-btc-updown-5m-test", "title": "BTC Up or Down 5m", "orderPriceMinTickSize": 0.01, "minimumTradeQty": 0.01,
                "assetPriceTerms": {"asset": {"symbol": "btc"}, "marketType": "ASSET_PRICE_MARKET_TYPE_UP_DOWN", "horizon": "5m",
                                    "windowStart": iso(self.ws), "windowEnd": iso(self.we), "priceToBeat": {"value": "80000.00"}}}

    async def markets(self, **kw):
        return [self._market()] if self.ws <= self.now < self.we else []

    async def market(self, slug):
        return self._market()

    async def book(self, slug):
        # the market lags the index: Up offered cheap until a seller dumps at 0.30 for a moment around t0+90
        lvl = lambda p, q: {"px": {"value": f"{p:.2f}"}, "qty": str(q)}
        if self.ws + 88 <= self.now <= self.ws + 92:
            return {"state": "MARKET_STATE_OPEN", "bids": [lvl(0.29, 50)], "offers": [lvl(0.30, 50)], "stats": {}}
        return {"state": "MARKET_STATE_OPEN", "bids": [lvl(0.48, 50)], "offers": [lvl(0.52, 50)], "stats": {}}

    async def settlement(self, slug):
        return None


class MemStore:
    def __init__(self, control=None):
        self.kv = {"control": control or {}}
        self.fills, self.windows, self.events = [], [], []

    async def get(self, k):
        return dict(self.kv.get(k) or {})

    async def put(self, k, v):
        self.kv[k] = v

    async def flush(self, ledger):
        f, w, e = ledger.take()
        self.fills += f
        self.windows += w
        self.events += e

    async def close(self):
        pass


def test_a_full_paper_window_fills_settles_and_records():
    from openjarvis.polybot.desk import Desk

    t0 = int(dt.datetime(2026, 10, 28, 18, 0, tzinfo=dt.timezone.utc).timestamp())     # a Wednesday: no maintenance
    ex = FakeExchange(t0)
    store = MemStore()
    pushes = []

    async def notify(title, text):
        pushes.append((title, text))

    async def sleep(_):
        ex.now += 1

    cfg = from_env({"POLYBOT_FLUSH_SECONDS": "30", "POLYBOT_HEDGE_MAX_LOSS": "0"})     # this one checks fills and settlement only
    ex.now = t0 - 5
    desk = Desk(cfg, ex, store, notify=notify, clock=lambda: float(ex.now), sleep=sleep)
    run(desk.run(max_steps=330))

    assert store.fills, "the seller sweeping below fair should have filled the Up bid"
    assert all(f["side"] == "up" and f["price"] <= 0.5 for f in store.fills)
    assert len(store.windows) == 1
    w = store.windows[0]
    assert w["up"] is True and w["pnl"] > 0
    assert w["up_cost"] <= cfg.limits.max_window_usd + 1e-9
    assert store.kv["status"]["mode"] == "paper"
    texts = " ".join(e["text"] for e in store.events)
    assert "ENTRY PAPER" in texts and "EXIT (settled) PAPER" in texts
    assert not [p for p in pushes if p[0] in ("Desk entry", "Desk settled")]        # paper trades are not pushed one by one


def test_pause_from_aibussol_stops_quoting():
    from openjarvis.polybot.desk import Desk

    t0 = int(dt.datetime(2026, 10, 28, 18, 0, tzinfo=dt.timezone.utc).timestamp())
    ex = FakeExchange(t0)
    store = MemStore({"paused": True})

    async def sleep(_):
        ex.now += 1

    ex.now = t0 - 5
    desk = Desk(from_env({}), ex, store, clock=lambda: float(ex.now), sleep=sleep)
    run(desk.run(max_steps=200))
    assert store.fills == [] and desk.reason.startswith("Paused")


# ── strategy v1.1 (brain) ───────────────────────────────────────────────────────────────

from openjarvis.polybot import brain


def test_blend_leans_on_the_market():
    assert brain.blend(0.80, 0.50, 0.4) == pytest.approx(0.62)
    assert brain.blend(0.80, None, 0.4) is None              # no market price: do not trust the model alone
    assert brain.blend(0.80, None, 1.0) == 0.80


def test_no_buying_up_into_a_falling_btc():
    prices = [80000.0] * 3600 + [80000.0 - 4 * i for i in range(60)]      # a steady drop over the last minute
    t, s = tape_from(prices)
    now = s + len(prices) - 1
    z = brain.momentum_z(t, now, 45, sigma_per_s=3.0)
    assert z < -0.6
    can_up, can_down, why = brain.side_filters(z, 0.6)
    assert (can_up, can_down) == (False, True) and "falling" in why
    assert brain.side_filters(0.1, 0.6)[:2] == (True, True)


def test_one_bite_per_side():
    assert brain.can_add({}, "up", 1) and not brain.can_add({"up": 1}, "up", 1) and brain.can_add({"up": 1}, "down", 1)
    assert brain.can_add({"up": 9}, "up", 0)


def test_learner_demands_more_edge_while_losing():
    lr = brain.Learner()
    for _ in range(4):
        lr.add(-1.0, 1.0)
    assert lr.extra_edge == 0.0                                  # too few windows to judge
    lr.add(-1.0, 1.0)
    assert lr.extra_edge == 0.10                                 # -100% return -> capped at +0.10
    for _ in range(20):
        lr.add(0.2, 1.0)
    assert lr.extra_edge == 0.0 and len(lr.history) == 20         # winning again: back to normal
    lr2 = brain.Learner()
    lr2.load(lr.dump())
    assert lr2.rolling_return == pytest.approx(lr.rolling_return)


def test_bet_size_follows_the_edge():
    lim = from_env({}).limits
    sz = strategy.Sizing(equity=50.0)
    assert sz.window_cap(lim) == 2.5                                       # 5% of $50
    small = sz.order_usd(0.45, 0.40, lim)                                  # 5c edge on a 40c contract
    big = sz.order_usd(0.60, 0.40, lim)                                    # 20c edge
    assert 0.25 <= small < big <= 2.5
    assert sz.order_usd(0.40, 0.40, lim) == 0.0                            # no edge, no bet
    assert strategy.Sizing(equity=500.0).window_cap(lim) == 25.0           # grows with the account
    assert strategy.Sizing(equity=20.0).window_cap(lim) == 1.0             # and shrinks after losses


def test_quotes_size_by_confidence():
    lim = from_env({}).limits
    sz = strategy.Sizing(equity=100.0, window_pct=7.5)                        # the live account: $7.50 a window
    weak = strategy.quotes(0.45, Book(0.40, 0.60), Inventory(), lim, budget_left=50, can_down=False, sizing=sz)[0]
    strong = strategy.quotes(0.75, Book(0.40, 0.60), Inventory(), lim, budget_left=50, can_down=False, sizing=sz)[0]
    assert weak.price == strong.price == 0.41 and strong.cost > weak.cost
    assert strong.cost <= sz.window_cap(lim) + 1e-9



def test_drift_moves_fair_value_with_the_recent_run():
    prices = [80000.0] * 3600 + [80000.0 - 1.0 * i for i in range(31)]      # BTC slid $30 in the last 30 seconds
    t, s = tape_from(prices)
    now = s + len(prices) - 1
    plain = model.fair_up(t, now, now + 200, 79975.0, sigma=2.0)
    drift = model.fair_up(t, now, now + 200, 79975.0, sigma=2.0, drift_beta=0.7, drift_lookback_s=30)
    assert drift < plain                                                  # a falling BTC makes Up worth less
    assert model.fair_up(t, now, now + 200, 79975.0, sigma=2.0, drift_beta=0.0) == plain



def test_second_side_only_when_the_pair_costs_under_a_dollar():
    held_up = Inventory(up_qty=2.0, up_cost=1.20)            # Up bought at 60c
    # the model has turned bearish: on its own it would buy Down at long 0.45 (Down costs 55c): pair 1.15, a locked loss
    qs = strategy.quotes(0.40, Book(0.40, 0.70), held_up, LIM, budget_left=50)
    downs = [q for q in qs if q.side == "down"]
    assert all(1.0 - q.price + 0.60 <= 0.97 + 1e-9 for q in downs)
    held_down = Inventory(down_qty=2.0, down_cost=1.30)      # Down bought at 65c
    ups = [q for q in strategy.quotes(0.80, Book(0.20, 0.60), held_down, LIM, budget_left=50) if q.side == "up"]
    assert all(q.price + 0.65 <= 0.97 + 1e-9 for q in ups)
    pair = Inventory(up_qty=2.0, up_cost=1.20, down_qty=2.0, down_cost=0.70)   # 60c + 35c = 95c per pair
    assert pair.worst_case > 0                                 # a completed pair under $1 profits whichever way BTC goes


# ── v1.5 ────────────────────────────────────────────────────────────────────────────────

def test_second_side_matches_the_first_sides_size():
    held = Inventory(up_qty=3.0, up_cost=1.20)                 # 3 Up at 40c
    downs = [q for q in strategy.quotes(0.40, Book(0.40, 0.70), held, LIM, budget_left=50) if q.side == "down"]
    assert downs and downs[0].qty == 3.0                       # exactly 3 Down: a complete pair, no leftover
    pair = Inventory(up_qty=3.0, up_cost=1.20, down_qty=3.0, down_cost=3.0 * (1 - downs[0].price))
    assert pair.pnl_if(True) > 0 and pair.pnl_if(False) > 0


def test_no_long_shots_and_no_expensive_favourites():
    lim = LIM
    assert [q for q in strategy.quotes(0.20, Book(0.05, 0.15), Inventory(), lim, budget_left=50, min_cost=0.15, max_cost=0.80)
            if q.side == "up"] == []                           # Up would cost ~10c
    downs = [q for q in strategy.quotes(0.10, Book(0.05, 0.15), Inventory(), lim, budget_left=50, min_cost=0.15, max_cost=0.80)
             if q.side == "down"]
    assert downs == []                                         # Down would cost ~85c


def test_orders_are_not_churned_for_one_tick():
    from openjarvis.polybot.brokers import Resting, keep

    r = Resting(Quote("up", 0.45, 2.0), "o1", 0.0)
    assert keep(r, Quote("up", 0.46, 2.0))                     # 1 tick better available: stay put
    assert not keep(r, Quote("up", 0.47, 2.0))                 # 2 ticks: move
    assert not keep(r, Quote("up", 0.44, 2.0))                 # now past the edge: move at once
    d = Resting(Quote("down", 0.60, 2.0), "o2", 0.0)
    assert keep(d, Quote("down", 0.59, 2.0)) and not keep(d, Quote("down", 0.61, 2.0))
    assert not keep(r, None)


def test_a_rejected_live_order_does_not_stop_the_desk():
    from openjarvis.polybot.brokers import LiveBroker
    from openjarvis.polybot.client import PMError

    class C:
        async def place(self, *a, **kw):
            raise PMError("would cross", 400)

    b = LiveBroker(C())
    run(b.set_quotes("s", [Quote("up", 0.45, 2.0)], now=1.0))
    assert b.resting == {} and "would cross" in b.rejects[0]


class LiveFake(FakeExchange):
    """The same window, answering the trading API like Polymarket US: orders rest, fill when the market sweeps through them."""

    async def settlement(self, slug):            # v1.14: live windows settle from the exchange's answer (this window ends Up)
        return 1.0

    def __init__(self, t0):
        super().__init__(t0)
        self.orders, self.n, self.cancels, self.cash = {}, 0, 0, 50.0

    async def place(self, slug, side, price, qty, post_only=True):
        self.n += 1
        oid = f"o{self.n}"
        self.orders[oid] = {"id": oid, "marketSlug": slug, "side": side, "price": price, "quantity": qty, "cumQuantity": 0.0, "state": "ORDER_STATE_NEW"}
        return oid

    def _match(self):
        if self.ws + 88 <= self.now <= self.ws + 92:                  # the seller sweeping Up down to 0.30
            for o in self.orders.values():
                if o["state"] == "ORDER_STATE_NEW" and o["side"] == "up" and o["price"] >= 0.30:
                    o["cumQuantity"], o["state"], o["avgPx"] = o["quantity"], "ORDER_STATE_FILLED", {"value": f'{o["price"]:.2f}'}

    async def open_orders(self, slugs=None):
        self._match()
        return [o for o in self.orders.values() if o["state"] == "ORDER_STATE_NEW"]

    async def order(self, oid):
        return self.orders[oid]

    async def cancel(self, oid, slug):
        self.cancels += 1
        if self.orders[oid]["state"] == "ORDER_STATE_NEW":
            self.orders[oid]["state"] = "ORDER_STATE_CANCELED"

    async def cancel_all(self, slugs=None):
        for o in self.orders.values():
            if o["state"] == "ORDER_STATE_NEW":
                o["state"] = "ORDER_STATE_CANCELED"
        return []

    async def buying_power(self):
        return self.cash

    async def positions(self):
        return {}


def test_a_full_live_window_places_fills_settles_and_alerts():
    from openjarvis.polybot.desk import Desk

    t0 = int(dt.datetime(2026, 10, 28, 18, 0, tzinfo=dt.timezone.utc).timestamp())
    ex = LiveFake(t0)
    store = MemStore({"mode": "live"})
    pushes = []

    async def notify(title, text):
        pushes.append((title, text))

    async def sleep(_):
        ex.now += 1

    cfg = from_env({"POLYBOT_ALLOW_LIVE": "true", "POLYMARKET_KEY_ID": "k", "POLYMARKET_SECRET_KEY": "s"})
    ex.now = t0 - 5
    desk = Desk(cfg, ex, store, notify=notify, clock=lambda: float(ex.now), sleep=sleep)
    run(desk.run(max_steps=330))

    assert store.kv["status"]["mode"] == "live"
    assert ex.n >= 1 and store.fills and all(f["mode"] == "live" for f in store.fills)
    w = [x for x in store.windows if x["mode"] == "live"]
    assert len(w) == 1 and w[0]["up"] is True and w[0]["pnl"] > 0
    assert w[0]["up_cost"] + w[0]["down_cost"] <= 5.0 + 1e-9                   # 10% of the $50 balance (v1.14)
    assert store.kv["status"]["window_pct"] == 10.0
    titles = [t for t, _ in pushes]
    assert "BTC · Desk entry" in titles and "BTC · Desk settled" in titles                 # an alert for the entry and for the exit
    entry = next(x for t, x in pushes if t == "BTC · Desk entry")
    assert "strike" in entry and "expires" in entry and "max loss" in entry and "target" in entry
    assert ex.cancels < 60                                                     # no cancel/replace every second
    assert store.kv["status"]["day_start"] == 50.0 and store.kv["status"]["day_limit"] == 7.5



def test_bet_cap_follows_the_current_balance():
    lim = from_env({}).limits
    assert from_env({}).window_pct == 10.0
    assert strategy.Sizing(equity=50.0, window_pct=7.5).window_cap(lim) == 3.75
    assert strategy.Sizing(equity=80.0, window_pct=7.5).window_cap(lim) == 6.0      # after a good run
    assert strategy.Sizing(equity=40.0, window_pct=7.5).window_cap(lim) == 3.0      # after a bad one



def test_live_sizing_uses_buying_power_plus_money_in_open_windows():
    from openjarvis.polybot.desk import Desk

    from openjarvis.polybot.desk import Window

    d = Desk(replace(from_env({}), mode="live"), None, MemStore())
    w = Window(slug="cpc-btc-updown-5m-z", start=0, end=300, price_to_beat=1.0)
    w.inv.up_qty, w.inv.up_cost, w.inv.down_qty, w.inv.down_cost = 18.0, 9.0, 8.0, 4.0    # 8 pairs (netted by Polymarket) + 10 Up at 50c
    d.open_windows[w.slug] = w
    d.balance = 60.0
    assert d.equity() == 65.0                                                  # only the unpaired $5 is still out
    p = Desk(from_env({}), None, MemStore())                                  # paper: $50 plus today's simulated P&L
    p.rs.day_start, p.rs.day_pnl = 50.0, 4.0
    assert p.equity() == 54.0



def test_the_first_side_always_leaves_room_to_complete_the_pair():
    """Live 2:05 / 2:15: Down filled big first, the budget left could only buy half as many Up; BTC went Up; -$2.69 and -$2.00."""
    lim = from_env({}).limits
    sz = strategy.Sizing(equity=100.0, window_pct=7.5)                        # $7.50 a window
    first = [q for q in strategy.quotes(0.30, Book(0.40, 0.56), Inventory(), lim, budget_left=100, sizing=sz) if q.side == "down"][0]
    assert first.qty <= 7.5                                                    # at most as many contracts as the window has dollars
    held = Inventory(down_qty=first.qty, down_cost=first.cost)
    second = [q for q in strategy.quotes(0.30, Book(0.20, 0.56), held, lim, budget_left=100, sizing=sz) if q.side == "up"]
    assert second and second[0].qty == first.qty                               # the matching side is always affordable
    total = first.cost + second[0].cost
    assert total <= 7.5 + 1e-9 and first.qty - total > 0                       # a full pair: profit whichever way BTC goes



def test_a_replaced_order_that_already_filled_is_not_doubled():
    """Live 2:40 / 2:45: the bot replaced an order that had already filled (it had not seen the fill yet), the new order filled too."""
    from openjarvis.polybot.brokers import LiveBroker

    class C:
        def __init__(self):
            self.orders, self.n = {}, 0

        async def place(self, slug, side, price, qty, post_only=True):
            self.n += 1
            self.orders[f"o{self.n}"] = {"id": f"o{self.n}", "state": "ORDER_STATE_NEW", "cumQuantity": 0.0, "quantity": qty}
            return f"o{self.n}"

        async def cancel(self, oid, slug):
            pass                                             # too late: it already filled

        async def open_orders(self, slugs=None):
            return [o for o in self.orders.values() if o["state"] == "ORDER_STATE_NEW"]

        async def order(self, oid):
            return self.orders[oid]

    c = C()
    b = LiveBroker(c)
    run(b.set_quotes("s", [Quote("up", 0.45, 7.0)], now=1.0))
    c.orders["o1"].update(state="ORDER_STATE_FILLED", cumQuantity=7.0)          # fills on the exchange, unseen by the bot
    run(b.set_quotes("s", [Quote("up", 0.48, 7.0)], now=1.5))                     # the bot re-prices (2+ ticks)
    assert c.n == 1                                                                # no second order while the cancel is unresolved
    fills = run(b.sync(None, now=2.0))
    assert sum(f.qty for f in fills) == 7.0 and b.closing == {}                    # the fill is found; now the side is known to be full


def test_a_closed_market_is_not_an_api_error():
    """Live 3:20-3:30: the desk kept asking for the book of the market that had just closed; 404 x5 pulled every quote."""
    from openjarvis.polybot.client import PMError
    from openjarvis.polybot.desk import Desk

    t0 = int(dt.datetime(2026, 10, 28, 18, 0, tzinfo=dt.timezone.utc).timestamp())
    ex = FakeExchange(t0)
    real_book = ex.book

    async def book(slug):
        if ex.now >= ex.we:
            raise PMError(f'market with slug "{slug}" not found', 404)
        return await real_book(slug)
    ex.book = book

    async def sleep(_):
        ex.now += 1

    store = MemStore()
    ex.now = t0 - 5
    d = Desk(from_env({}), ex, store, clock=lambda: float(ex.now), sleep=sleep)
    run(d.run(max_steps=330))
    texts = " ".join(e["text"] for e in store.events)
    assert "API errors in a row" not in texts and "EXIT (settled)" in texts



def test_one_sided_risk_is_small_and_pairs_are_built_in_steps():
    """Losses were ~3x the wins: a one-sided bet loses its whole stake, a pair wins a few cents a contract. Cap the one-sided part."""
    lim = from_env({}).limits
    sz = strategy.Sizing(equity=100.0, window_pct=7.5)                        # $7.50 a window, $1.50 unpaired
    first = [q for q in strategy.quotes(0.62, Book(0.40, 0.60), Inventory(), lim, budget_left=100, sizing=sz, unpaired_usd=1.5)
             if q.side == "up"][0]
    assert first.cost <= 1.5 + 1e-9                                             # one side alone: at most $1.50 at risk
    held = Inventory(up_qty=first.qty, up_cost=first.cost)
    downs = [q for q in strategy.quotes(0.40, Book(0.30, 0.45), held, lim, budget_left=100, sizing=sz, unpaired_usd=1.5) if q.side == "down"]
    assert downs and downs[0].qty >= first.qty - 1e-9                           # completing the pair is always allowed
    paired = Inventory(up_qty=first.qty, up_cost=first.cost, down_qty=first.qty, down_cost=first.qty * 0.4)
    more = [q for q in strategy.quotes(0.62, Book(0.40, 0.60), paired, lim, budget_left=100, sizing=sz, unpaired_usd=1.5) if q.side == "up"]
    assert more and more[0].cost <= 1.5 + 1e-9                                  # once paired, the next step opens again
    assert strategy._unpaired_room(1.5, 0.5, 5.0, 2.0, 0.01) == 0.0              # already $1.50 unpaired: no more on that side



def test_pair_first_defaults():
    cfg = from_env({})
    assert cfg.unpaired_pct == 10.0 and cfg.open_cutoff_s == 90.0 and cfg.max_fills_per_side == 8


def test_position_mismatch_stops_the_desk():
    """Earlier versions held far more than they recorded (a +$16.23 window on a $7.50 budget). Two checks in a row showing
    Polymarket holding more than the desk booked stop trading until the owner pauses and resumes."""
    from openjarvis.polybot.desk import Desk, Window

    d = Desk(replace(from_env({}), mode="live"), None, MemStore())
    pushed = []

    async def push(t, x):
        pushed.append(t)
    d.push = push
    w = Window(slug="cpc-btc-updown-5m-x", start=0, end=300, price_to_beat=1.0)
    w.inv.up_qty, w.inv.up_cost = 5.0, 2.5
    d.open_windows[w.slug] = w
    ok = {w.slug: {"cost": {"value": "2.60", "currency": "USD"}}}
    big = {w.slug: {"cost": {"value": "9.00", "currency": "USD"}}}
    run(d.check_positions(ok, 1.0))
    assert d.guard is None
    run(d.check_positions(big, 2.0))
    assert d.guard is None                                                    # once could be a fill mid-sync
    run(d.check_positions(big, 3.0))
    assert d.guard and "$9.00" in d.guard and pushed


def test_a_restart_mid_window_keeps_the_window():
    """A deploy at 4:19 PM stopped the desk with contracts in the 4:20 window; the next copy never settled it."""
    from openjarvis.polybot.desk import Desk, Window

    a = Desk(replace(from_env({}), mode="live"), None, MemStore())
    w = Window(slug="cpc-btc-updown-5m-y", start=0.0, end=300.0, price_to_beat=78698.77, mode="live")
    w.inv.up_qty, w.inv.up_cost, w.inv.down_qty, w.inv.down_cost = 2.0, 1.36, 1.5, 0.25
    a.open_windows[w.slug] = w
    rows = a.status(250.0)["open_windows"]
    b = Desk(replace(from_env({}), mode="live"), None, MemStore())
    b.restore_windows(json.loads(json.dumps(rows)), 260.0)
    got = b.open_windows[w.slug]
    assert got.inv.up_cost == 1.36 and got.inv.down_qty == 1.5 and got.price_to_beat == 78698.77
    p = Desk(from_env({}), None, MemStore())                                  # a paper copy leaves live windows alone
    p.restore_windows(rows, 260.0)
    assert not p.open_windows


def test_v19_no_new_pair_on_a_long_shot_but_completing_is_fine():
    """Live 4:40 and 5:25: Up bought at 15-20c as the first leg, Down never got cheap enough, both lost ~$0.93."""
    lim = from_env({}).limits
    sz = strategy.Sizing(equity=118.0, window_pct=7.5)
    qs = strategy.quotes(0.25, Book(0.18, 0.22), Inventory(), lim, budget_left=100, sizing=sz,
                         min_cost=0.15, max_cost=0.80, open_min=0.30, open_max=0.70, unpaired_usd=0.9)
    assert not [q for q in qs if q.side == "up"]                              # 18c Up as a first leg: not any more
    inv = Inventory(down_qty=2.0, down_cost=0.60)                              # holding Down at 30c: completing with cheap Up is fine
    qs = strategy.quotes(0.40, Book(0.30, 0.50), inv, lim, budget_left=100, sizing=sz,
                         min_cost=0.15, max_cost=0.80, open_min=0.30, open_max=0.70, unpaired_usd=0.9)
    assert [q for q in qs if q.side == "up"]


def test_three_desks_keep_apart():
    from openjarvis.polybot.config import assets_from_env
    from openjarvis.polybot.runtime import lock_id
    from openjarvis.polybot.state import ScopedStore, asset_of, kv_key

    assert assets_from_env({}) == ["btc", "eth", "sol", "xrp"] and assets_from_env({"POLYBOT_ASSETS": "eth, doge"}) == ["eth"]
    assert kv_key("status", "btc") == "status" and kv_key("control", "sol") == "control:sol"
    assert len({lock_id("btc"), lock_id("eth"), lock_id("sol"), lock_id("xrp")}) == 4
    assert asset_of("cpc-eth-updown-5m-2026-10-30-0100z") == "eth"
    mem = MemStore()
    run(ScopedStore(mem, "eth").put("status", {"x": 1}))
    assert mem.kv["status:eth"] == {"x": 1} and "status" not in mem.kv
    assert from_env({}, asset="sol").asset == "sol"


def test_market_address_pattern_is_learned():
    from openjarvis.polybot.desk import Desk

    d = Desk(from_env({}, asset="eth"), None, MemStore())
    assert d.slug_for(1761791100).startswith("cpc-eth-updown-5m-")
    d.learn_prefix("cpceth-eth-updown-5m-2026-10-30-0225z")
    assert d.slug_for(1761791100) == "cpceth-eth-updown-5m-" + __import__("time").strftime("%Y-%m-%d-%H%M", __import__("time").gmtime(1761791100)) + "z"
    assert d.status(0.0)["slug_prefix"] == "cpceth-eth-updown-5m-"


def test_each_desk_can_have_its_own_window_and_size():
    env = {"POLYBOT_WINDOW_PCT": "10", "POLYBOT_WINDOW_PCT_BTC": "7.5", "POLYBOT_WINDOW_PCT_ETH": "15", "POLYBOT_HORIZON_BTC": "15m"}
    b, e, s = from_env(env, asset="btc"), from_env(env, asset="eth"), from_env(env, asset="sol")
    assert (b.horizon, b.window_pct) == ("15m", 7.5) and (e.horizon, e.window_pct) == ("5m", 15.0) and s.window_pct == 10.0
    from openjarvis.polybot.desk import Desk
    d = Desk(b, None, MemStore())
    assert d.span == 900 and d.slug_for(1761791400).startswith("cpc-btc-updown-15m-")



def test_v112_an_unpaired_leg_is_hedged_crossing_if_needed():
    """Tonight's 9:45 SOL loss: 3.97 Up at 58c never got its Down and lost $2.30. A hedge buys the Down at the bid (crossing)
    when Up + Down costs at most $1.05."""
    lim = from_env({}).limits
    sz = strategy.Sizing(equity=120.0, window_pct=15)
    inv = Inventory(up_qty=3.97, up_cost=2.30)
    qs = strategy.quotes(0.40, Book(0.55, 0.58), inv, lim, budget_left=100, sizing=sz, hedge_loss=0.05)
    assert len(qs) == 1 and qs[0].side == "down" and qs[0].qty == 3.97 and qs[0].taker
    assert (1 - qs[0].price) + 2.30 / 3.97 <= 1.05 + 1e-9                     # the pair costs at most $1.05
    far = strategy.quotes(0.10, Book(0.15, 0.18), inv, lim, budget_left=100, sizing=sz, hedge_loss=0.05)
    assert far and far[0].side == "down" and not far[0].taker                    # too dear to cross: rests at the $1.05 price instead
    win = strategy.quotes(0.90, Book(0.85, 0.88), inv, lim, budget_left=100, sizing=sz, hedge_loss=0.05)
    assert win[0].taker and (1 - win[0].price) + 2.30 / 3.97 < 1.0               # the leg is winning: the hedge locks a profit


def test_trading_hours_pacific():
    import datetime as dt
    from zoneinfo import ZoneInfo
    LA = ZoneInfo("America/Los_Angeles")
    at = lambda h, m: dt.datetime(2026, 10, 30, h, m, tzinfo=LA).timestamp()
    assert risk.in_hours(at(6, 30), "06:30", "20:30") and risk.in_hours(at(20, 29), "06:30", "20:30")
    assert not risk.in_hours(at(20, 30), "06:30", "20:30") and not risk.in_hours(at(2, 0), "06:30", "20:30")
    assert risk.in_hours(at(2, 0), "", "")


def test_v113_closing_sweep():
    lim = from_env({}).limits
    inv = Inventory()
    b = Book(0.84, 0.86)
    q = strategy.sweep(0.95, 0.85, b, inv, cap_usd=6.0, budget_left=50)        # model 95%, ask 86c: edge 9c minus ~0.8c fee
    assert q and q.side == "up" and q.taker and q.price == 0.86 and q.qty * 0.86 <= 6.0 + 1e-9
    assert strategy.sweep(0.88, 0.85, b, inv, cap_usd=6.0, budget_left=50) is None     # 88% vs 86c: not enough edge
    assert strategy.sweep(0.95, 0.40, b, inv, cap_usd=6.0, budget_left=50) is None     # the market does not agree
    d = strategy.sweep(0.05, 0.15, Book(0.14, 0.16), inv, cap_usd=6.0, budget_left=50)  # Down at 86c
    assert d and d.side == "down" and abs(d.cost_per - 0.86) < 1e-9
    assert strategy.sweep(0.99, 0.6, Book(0.58, 0.60), inv, cap_usd=6.0, budget_left=50) is None   # 60c: below the 75c floor


def test_btc_15m_by_day_5m_at_night():
    import datetime as dt
    from zoneinfo import ZoneInfo
    from openjarvis.polybot.desk import Desk
    LA = ZoneInfo("America/Los_Angeles")
    cfg = from_env({"POLYBOT_HORIZON_BTC": "15m", "POLYBOT_HORIZON_NIGHT_BTC": "5m"}, asset="btc")
    t = {"now": dt.datetime(2026, 10, 30, 12, 0, tzinfo=LA).timestamp()}
    d = Desk(cfg, None, MemStore(), clock=lambda: t["now"])
    assert d.horizon == "15m" and d.span == 900
    t["now"] = dt.datetime(2026, 10, 30, 23, 0, tzinfo=LA).timestamp()
    assert d.horizon == "5m" and d.span == 300 and d.slug_for(0).startswith("cpc-btc-updown-5m-")
    d.learn_prefix("cpc-btc-updown-15m-2026-10-30-0530z")
    assert d._prefixes["15m"] == "cpc-btc-updown-15m-" and d.slug_prefix == ""


def test_owner_reset_clears_the_daily_stop():
    from openjarvis.polybot.desk import Desk
    st = MemStore()
    d = Desk(replace(from_env({}), mode="live"), None, st)
    d._ready, d.rs.day_start, d.rs.day_pnl, d.rs.real_pnl = True, 113.0, -5.0, -23.0
    st.kv["control"] = {"mode": "paper", "day_reset_at": "2026-10-30T08:00:00Z"}
    run(d.read_control(1.0))
    assert d.rs.day_start is None and d.rs.real_pnl is None and d.rs.day_pnl == 0.0
    d.rs.day_start = 90.27
    run(d.read_control(2.0))                                                  # the same reset is not applied twice
    assert d.rs.day_start == 90.27


def test_v114_settle_full_precision_and_sweep_gap():
    t = model.PriceTape()
    end = 1_000_000
    for s in range(end - 60, end):
        t.add(s, 1.2296)                       # XRP closes a hair below a $1.2300 beat: Down, not a "tie" at $1.23
    assert model.settle(t, end, 1.2300)[1] is False
    assert model.settle(t, end, 1.2296)[1] is True        # an exact tie still settles Up
    inv = strategy.Inventory()
    b = strategy.Book(0.14, 0.16)                                 # Down asks 86c
    assert strategy.sweep(0.05, 0.15, b, inv, cap_usd=6.0, budget_left=50, gap_bp=-2.0, min_gap_bp=5.0) is None   # too close
    assert strategy.sweep(0.05, 0.15, b, inv, cap_usd=6.0, budget_left=50, gap_bp=0.0, min_gap_bp=5.0) is None    # tie: never Down
    assert strategy.sweep(0.05, 0.15, b, inv, cap_usd=6.0, budget_left=50, gap_bp=-8.0, min_gap_bp=5.0).side == "down"
    assert strategy.sweep(0.05, 0.15, b, inv, cap_usd=6.0, budget_left=50, gap_bp=None, min_gap_bp=5.0) is None


def test_v114_sweep_seconds_day_and_night():
    c = from_env({})
    assert c.sweep_secs_left == 60.0 and c.sweep_secs_left_night == 90.0
    c = from_env({"POLYBOT_SWEEP_SECONDS_DAY_SOL": "45"}, asset="sol")
    assert c.sweep_secs_left == 45.0


def test_v114_learned_pair_sizing():
    from openjarvis.polybot import setups
    assert setups.wilson_lb(3, 3) < 0.6                     # three wins prove little
    assert setups.wilson_lb(30, 30) > 0.90                  # thirty do
    f = {"spread": 0.01, "momentum_z": 0.1, "secs_left": 240}
    key = setups.pair_setup("up", f)
    assert key == "tight/flat/early"
    fills = [{"side": "up", "qty": 5, "cost": 2.40, "features": f}, {"side": "down", "qty": 5, "cost": 2.45, "features": f},
             {"side": "up", "qty": 2, "cost": 1.80, "features": {"sweep": True}}]
    k, pnl = setups.pair_outcome(fills, up=True)
    assert k == key and abs(pnl - 0.15) < 1e-9             # the sweep is left out of the pair's result
    book = setups.SetupBook()
    assert book.size(key, 10.0) == (10.0, "unproven")
    for _ in range(30):
        book.add(key, 0.12)
    assert book.size(key, 10.0) == (15.0, "proven")
    for _ in range(12):
        book.add("wide/against/late", -0.30)
    assert book.size("wide/against/late", 10.0) == (5.0, "weak")
    assert setups.version_at_least("1.13", "1.12") and not setups.version_at_least("1.9", "1.12")


def test_v115_shadow_strategies():
    from openjarvis.polybot import shadow
    assert shadow.decide("edge", 120, 0.60, 0.50, 0.45) == [("up", 0.50)]          # 10c above the ask
    assert shadow.decide("edge", 30, 0.60, 0.50, 0.45) == []                       # too late for edge
    assert shadow.decide("pairs", 120, 0.5, 0.45, 0.50) == [("up", 0.45), ("down", 0.50)]   # 95c for the pair
    assert shadow.decide("pairs", 120, 0.5, 0.50, 0.50) == []                      # 100c: no pair
    assert shadow.decide("late", 30, 0.93, 0.90, 0.10) == [("up", 0.90)]
    assert shadow.decide("late", 30, 0.93, 0.97, 0.10) == []                       # 97c: too expensive
    s = shadow.Shadow()
    s.tick("w1", 120, 0.60, 0.50, 0.45)                                            # edge fires
    s.tick("w1", 50, 0.95, 0.90, 0.10)                                             # late fires; edge does not fire twice
    assert set(s.open["w1"]) == {"edge", "pairs", "late"}          # pairs also qualifies at 95c on the first tick
    s.settle("w1", up=True)
    assert s.rec["edge"]["windows"] == 1 and s.rec["edge"]["wins"] == 1 and s.rec["edge"]["profit"] > 0
    assert s.rec["late"]["wins"] == 1 and s.rec["pairs"]["windows"] == 1 and s.rec["pairs"]["wins"] == 1
    s2 = shadow.Shadow(); s2.load(s.to_dict()); assert s2.rec["edge"]["wins"] == 1


def test_v115_book_view_and_calibration():
    from openjarvis.polybot import shadow
    assert shadow.book_up([(0.5, 30)], [(0.51, 10)]) == 0.75          # heavy bid side: Up looks likelier
    assert shadow.book_up([], [(0.51, 10)]) is None                    # empty side: no reading
    s = shadow.Shadow()
    s.calib_add(0.85, 0.60, True)
    s.calib_add(0.85, None, False)
    s.calib_add(0.42, 0.42, False)
    t = s.calib_table()
    assert t["model"][4] == {"bucket": "80-89", "trades": 2, "won": 1, "win_rate": 50.0}
    assert t["book"][2]["trades"] == 1 and t["book"][2]["won"] == 1      # 60% -> the 60-69 bucket, won
    s2 = shadow.Shadow(); s2.load(s.to_dict()); assert s2.calib_table() == t
