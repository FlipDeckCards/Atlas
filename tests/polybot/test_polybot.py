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
    assert "Daily loss" in _blocked(cfg, risk.RiskState(day_pnl=-7.5))
    assert "stale" in _blocked(cfg, risk.RiskState(), data_age_s=10)
    assert "close" in _blocked(cfg, risk.RiskState(), secs_to_end=10)
    assert "not open" in _blocked(cfg, risk.RiskState(), market_open=False)


def test_budget_never_allows_losing_past_the_daily_limit():
    cfg = from_env({"POLYBOT_DAILY_LOSS_PCT": "0"})                          # flat $10
    assert risk.budget_left(cfg, risk.RiskState(day_pnl=-8.0, at_risk=1.0), None) == pytest.approx(1.0)
    assert risk.budget_left(cfg, risk.RiskState(), None) == pytest.approx(10.0)


def test_daily_stop_is_15_percent_of_the_days_starting_balance():
    cfg = from_env({})
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

    cfg = from_env({"POLYBOT_FLUSH_SECONDS": "30"})
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


def test_v11_defaults_are_small():
    lim = from_env({}).limits
    assert (lim.max_window_usd, lim.max_order_usd, lim.daily_loss_pct) == (1.0, 0.5, 15.0)
