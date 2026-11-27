"""Aibussol's side of the Polymarket Desk (the trading worker in openjarvis/polybot): its scoreboard, trade log and switches.

The worker writes status, fills, windows and events to the shared database; this module only reads them, and writes the
'control' row the worker obeys (mode, paused, kill, limits). Owner accounts only.

Switches and who may flip them:
  pause / resume / paper / flatten (pull every quote) / tighter limits   -> at once, from the Station or Helix
  go live / looser limits                                               -> only after Michael's Approve tap (an ops card;
                                                                           engine._answer_ops calls apply_approved)
"""

from __future__ import annotations

import datetime as dt
import json
import math
import time
from typing import Any, Dict, List, Optional

from openjarvis.polybot.brain import STRATEGY_VERSION
from openjarvis.polybot.config import Limits, from_env
from openjarvis.polybot.config import label_for
from openjarvis.polybot.state import DDL, desk_sql, event_asset, kv_key

KEY = "polymarket"                                   # the BTC desk keeps the original factory key
import logging
logger = logging.getLogger("crew.desk")

KEYS = {"btc": "polymarket", "btc15": "polymarket-btc15", "eth": "polymarket-eth", "sol": "polymarket-sol", "xrp": "polymarket-xrp",
        "btclate": "polymarket-btclate"}
from openjarvis.polybot.config import COINS as _COINS, desk_label as _label
for _c in _COINS:                                    # v1.26: 1-hour desks for every coin, 15-minute for ETH, SOL and XRP
    for _s in ("15", "1h"):
        KEYS.setdefault(_c + _s, f"polymarket-{_c}{_s}")
for _c in ("gold", "silver", "wti", "natgas"):        # 2026-11-19 (owner): commodity 15m desks
    KEYS.setdefault(_c, f"polymarket-{_c}")
# v1.23: the late-only paper desk trades BTC 5m markets, so its rows are read as BTC with its own paper label
LEDGER_OF = {"btclate": "paper-late"}
LIVE_OF = {"btclate": "live-late"}                  # the late desk's real-money rows: its own live scoreboard
ASSET_ROWS = {"btclate": "btc"}
ASSET_OF = {v: k for k, v in KEYS.items()}
NAMES = {"btc": "Polymarket Desk", "btc15": "Polymarket BTC 15m Desk", "eth": "Polymarket ETH Desk", "sol": "Polymarket SOL Desk",
         "xrp": "Polymarket XRP Desk", "btclate": "Polymarket BTC Late Desk (paper)"}
COLORS = {"btc": "#00e5ff", "btc15": "#4fc3f7", "eth": "#8c8cff", "sol": "#14f195", "xrp": "#ffb347", "btclate": "#ff7ad9",
          "gold": "#ffd54f", "silver": "#cfd8dc", "wti": "#bcaaa4", "natgas": "#64b5f6"}
for _a in KEYS:
    NAMES.setdefault(_a, f"Polymarket {_label(_a)} Desk")
    COLORS.setdefault(_a, COLORS.get(_a[:3], "#00e5ff"))


def business(asset: str = "btc") -> Dict[str, str]:
    a = asset.lower()
    return {"key": KEYS[a], "name": NAMES[a], "url": "https://polymarket.us", "color": COLORS[a],
            "description": ("Polymarket Desk runs 8 live trading bots in Polymarket US's Up/Down contracts: 5-minute windows for BTC, "
                            "ETH, SOL and XRP, and 15-minute windows for gold, silver, oil and natural gas." if a == "btc" else
                            f"{NAMES[a]} is a trading bot in Polymarket US's "
                            f"{'BTC 5-minute, late method only (paper)' if a == 'btclate' else _label(a)} Up/Down contracts.")}


BUSINESS = business("btc")
# owner, 2026-11-19: subscribers' Stations match the owner's: the four 5-minute crypto desks and the four commodity 15m desks, the same
# bots and rules on their own keys. A new copy starts in paper; the account holder taps LIVE. (BTC 5m only from v1.39; XRP added 2026-11-18.)
TENANT_ASSETS = ("btc", "xrp", "sol", "eth", "gold", "silver", "wti", "natgas")
TENANT_BUSINESS = {"key": "polymarket", "name": "Polymarket Desk", "url": "https://polymarket.us", "color": "#00e5ff",
                   "description": "Polymarket Desk runs eight trading bots on your own Polymarket US account: BTC, XRP, SOL and ETH on 5-minute Up/Down windows, and gold, silver, WTI oil and natural gas on 15-minute windows."}
ASSET_SQL = desk_sql("slug")                     # cpc-eth-updown-5m-... -> eth; cpc-btc-updown-15m-... -> btc15
LIMIT_FIELDS = ("bankroll_usd", "max_window_usd", "max_order_usd", "daily_loss_usd", "daily_loss_pct", "min_edge")
_READY: Dict[int, bool] = {}


async def _ensure(pool) -> None:
    if not _READY.get(id(pool)):
        async with pool.acquire() as c:
            await c.execute(DDL)
        _READY[id(pool)] = True


def _j(v: Any) -> Dict[str, Any]:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


async def get_kv(pool, key: str) -> Dict[str, Any]:
    await _ensure(pool)
    async with pool.acquire() as c:
        return _j(await c.fetchval("SELECT value FROM polybot_kv WHERE key = $1", key))


async def paper_first(pool, assets) -> list:
    """v1.26: the 10 new desks start in PAPER. ETH, SOL and XRP 5m once traded live, so their old control rows may still say live:
    each new desk is set to paper once (marked v126_paper), after which only the owner's Go live tap changes it. A desk whose row
    cannot be checked is not started (never risk it going live by accident)."""
    from openjarvis.polybot.config import NEW_DESKS

    ok = []
    for a in assets:
        if a not in NEW_DESKS:
            ok.append(a)
            continue
        try:
            await _ensure(pool)
            ctl = await get_kv(pool, kv_key("control", a))
            if not ctl.get("v126_paper"):
                ctl.update(mode="paper", v126_paper=True, paused=False, kill=False)
                await put_control(pool, ctl, a)
            ok.append(a)
        except Exception:
            logger.exception("could not check the %s desk's control row; not starting it", a)
    return ok


async def commodity_live_once(pool, assets) -> None:
    """Owner, 2026-11-19: the commodity desks trade real money from the start at 1% ("paper trading doesn't work"). Each one's
    control row is set to LIVE once (marked commodity_live_v1); after that only the owner's Station taps (Back to paper, Pause,
    Kill switch, Go live) change it. Live still needs the worker's POLYBOT_ALLOW_LIVE and its keys, as for every desk."""
    from openjarvis.polybot.config import COMMODITIES

    for a in assets:
        if a not in COMMODITIES:
            continue
        try:
            await _ensure(pool)
            ctl = await get_kv(pool, kv_key("control", a))
            if not ctl.get("commodity_live_v1"):
                ctl.update(mode="live", commodity_live_v1=True, paused=False, kill=False)
                await put_control(pool, ctl, a)
                logger.warning("Commodity desk %s set to LIVE at 1%% (owner's instruction, 2026-11-19)", a)
        except Exception:
            logger.exception("could not set the %s commodity desk live; it stays as it was", a)


async def fees_backfill_once(pool) -> None:
    """Owner, 2026-11-20: P&L is after Polymarket's taker fee (0.0695 x p x (1 - p) per contract). The desks book it from now on;
    this subtracts it once from every live window settled since v1.22 (Nov 10, when every method crossed the spread) that was
    booked without it, so the Station, scoreboards and daily sheet match the account. Marked fees_backfill_v1; runs once."""
    try:
        await _ensure(pool)
        flag = await get_kv(pool, "fees_backfill_v1")
        if flag.get("done"):
            return
        async with pool.acquire() as c:
            async with c.transaction():
                n = await c.fetchval(
                    "WITH f AS (SELECT mode, slug, sum(0.0695 * price * (1 - price) * qty) AS fee, "
                    "sum(CASE WHEN side = 'up' THEN 0.0695 * price * (1 - price) * qty ELSE 0 END) AS fu, "
                    "sum(CASE WHEN side = 'down' THEN 0.0695 * price * (1 - price) * qty ELSE 0 END) AS fd "
                    "FROM polybot_fills WHERE mode LIKE 'live%' AND (features->>'fee') IS NULL GROUP BY mode, slug), "
                    "u AS (UPDATE polybot_windows w SET pnl = w.pnl - f.fee, up_cost = coalesce(w.up_cost, 0) + f.fu, "
                    "down_cost = coalesce(w.down_cost, 0) + f.fd, fees = f.fee, fees_in_pnl = TRUE FROM f "
                    "WHERE w.mode = f.mode AND w.slug = f.slug AND w.fees_in_pnl IS NOT TRUE AND w.pnl IS NOT NULL "
                    "AND w.settled_at >= '2026-11-10' RETURNING 1) SELECT count(*) FROM u")
                await c.execute("INSERT INTO polybot_kv (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
                                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                                "fees_backfill_v1", json.dumps({"done": True, "windows": int(n or 0)}))
        logger.warning("Fees backfill: %s live windows since Nov 10 now booked after Polymarket's taker fee", n)
    except Exception:
        logger.exception("fees backfill failed; P&L stays as booked")


EARLY_FEES_CUTOFF = "2026-11-10"


def early_fee_plan(windows: List[Dict[str, Any]], fills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """For live windows settled before Nov 10: which ones were booked WITHOUT Polymarket's taker fee, and what to subtract.
    A window's P&L is payout - cost; the fee is 0.0695 x p x (1 - p) per contract (or the fee the fill recorded). A window whose stored
    P&L sits closer to payout - cost than to payout - cost - fee was booked without the fee. Windows that already carry it (the BTC 15m
    desk's rows, for one) are only marked, never charged twice."""
    by: Dict[Any, List[Dict[str, Any]]] = {}
    for f in fills:
        by.setdefault((f["mode"], f["slug"]), []).append(f)
    plan = []
    for w in windows:
        fl = by.get((w["mode"], w["slug"]))
        if not fl or w.get("up") is None or w.get("pnl") is None:
            continue
        win_side = "up" if w["up"] else "down"
        cost = sum(float(f["cost"]) for f in fl)
        payout = sum(float(f["qty"]) for f in fl if f["side"] == win_side)
        fee_up = fee_dn = 0.0
        for f in fl:
            feat = f.get("features")
            if isinstance(feat, str):
                try:
                    feat = json.loads(feat)
                except Exception:
                    feat = None
            rec = feat.get("fee") if isinstance(feat, dict) else None
            p = float(f["price"])
            fee = float(rec) if isinstance(rec, (int, float)) else 0.0695 * p * (1.0 - p) * float(f["qty"])
            if f["side"] == "up":
                fee_up += fee
            else:
                fee_dn += fee
        fee = fee_up + fee_dn
        pnl = float(w["pnl"])
        exp = payout - cost
        missing = fee > 0.005 and abs(pnl - exp) <= abs(pnl - (exp - fee))
        plan.append({"mode": w["mode"], "slug": w["slug"], "fee": fee, "fee_up": fee_up, "fee_dn": fee_dn, "charge": missing})
    return plan


async def fees_backfill_early_once(pool) -> None:
    """Owner, 2026-11-27: the Nov 20 backfill only reached windows settled since Nov 10, so the Oct 29 - Nov 10 row of the daily sheet
    (and the "Settled trades total") was about $43 better than the account. The account's all-time result matches the raw fills WITH the
    taker fee. This charges the fee once to the live windows settled before Nov 10 that were booked without it (decided window by window
    from the fills, see early_fee_plan) and marks all of them. Marked fees_backfill_v2; runs once."""
    try:
        await _ensure(pool)
        if (await get_kv(pool, "fees_backfill_v2")).get("done"):
            return
        async with pool.acquire() as c:
            wins = [dict(r) for r in await c.fetch(
                f"SELECT slug, mode, up, pnl FROM polybot_windows WHERE mode LIKE 'live%' AND settled_at < '{EARLY_FEES_CUTOFF}' "
                "AND pnl IS NOT NULL AND up IS NOT NULL AND fees_in_pnl IS NOT TRUE")]
            slugs = sorted({w["slug"] for w in wins})
            fills = [dict(r) for r in await c.fetch(
                "SELECT mode, slug, side, price, qty, cost, features FROM polybot_fills WHERE mode LIKE 'live%' AND slug = ANY($1::text[])", slugs)] if slugs else []
            plan = early_fee_plan(wins, fills)
            charged = [p for p in plan if p["charge"]]
            async with c.transaction():
                if charged:
                    await c.executemany(
                        "UPDATE polybot_windows SET pnl = pnl - $3, up_cost = coalesce(up_cost, 0) + $4, down_cost = coalesce(down_cost, 0) + $5, "
                        "fees = $3, fees_in_pnl = TRUE WHERE mode = $1 AND slug = $2 AND fees_in_pnl IS NOT TRUE",
                        [(p["mode"], p["slug"], p["fee"], p["fee_up"], p["fee_dn"]) for p in charged])
                marked = [p for p in plan if not p["charge"]]
                if marked:
                    await c.executemany("UPDATE polybot_windows SET fees = $3, fees_in_pnl = TRUE WHERE mode = $1 AND slug = $2 AND fees_in_pnl IS NOT TRUE",
                                        [(p["mode"], p["slug"], p["fee"]) for p in marked])
                await c.execute("INSERT INTO polybot_kv (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
                                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                                "fees_backfill_v2", json.dumps({"done": True, "charged": len(charged), "already_had_fee": len(marked),
                                                                "fee_total": round(sum(p["fee"] for p in charged), 2)}))
        logger.warning("Early fees backfill: %s windows before Nov 10 charged $%.2f in taker fees; %s already had them",
                       len(charged), sum(p["fee"] for p in charged), len(marked))
    except Exception:
        logger.exception("early fees backfill failed; P&L stays as booked")


async def put_control(pool, control: Dict[str, Any], asset: str = "btc", account: str = "") -> None:
    await _ensure(pool)
    async with pool.acquire() as c:
        await c.execute("INSERT INTO polybot_kv (key, value, updated_at) VALUES ($2, $1::jsonb, now()) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", json.dumps(control), kv_key("control", asset, account))


def current_limits(control: Dict[str, Any]) -> Limits:
    base = from_env().limits.__dict__.copy()
    for k, v in (control.get("limits") or {}).items():
        if k in base:
            try:
                base[k] = float(v)
            except (TypeError, ValueError):
                pass
    return Limits(**base).clean()


VER = "coalesce(version, '1.0')"


async def scoreboard(pool, mode: str, now: Optional[dt.datetime] = None, version: str = STRATEGY_VERSION, asset: str = "btc",
                     account: str = "") -> Dict[str, Any]:
    """Win rate and profit for one mode (paper or live), over all time, the last 7 days and today (Los Angeles)."""
    await _ensure(pool)
    from openjarvis.server.crew.finance import la_midnight

    now = now or dt.datetime.now(dt.timezone.utc)
    today, week = la_midnight(now), now - dt.timedelta(days=7)
    q = ("SELECT count(*) AS n, count(*) FILTER (WHERE pnl > 0) AS wins, count(*) FILTER (WHERE pnl < 0) AS losses, "
         "coalesce(sum(pnl), 0) AS pnl, coalesce(sum(up_cost + down_cost), 0) AS staked, coalesce(max(pnl), 0) AS best, "
         "coalesce(min(pnl), 0) AS worst FROM polybot_windows WHERE mode = $1 AND settled_at IS NOT NULL AND settled_at >= $2 "
         f"AND {VER} = $3 AND {ASSET_SQL} = $4")
    out: Dict[str, Any] = {"mode": mode, "version": version, "asset": asset}
    async with pool.acquire() as c:
        for name, since in (("all", dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)), ("d7", week), ("today", today)):
            r = await c.fetchrow(q, mode, since, version, asset)
            n, wins = int(r["n"]), int(r["wins"])
            staked = float(r["staked"])
            out[name] = {"trades": n, "wins": wins, "losses": int(r["losses"]), "win_rate": round(100.0 * wins / n, 1) if n else None,
                         "profit": round(float(r["pnl"]), 2), "staked": round(staked, 2),
                         "return_pct": round(100.0 * float(r["pnl"]) / staked, 1) if staked else None,
                         "best": round(float(r["best"]), 2), "worst": round(float(r["worst"]), 2)}
        fills = await c.fetchval(f"SELECT count(*) FROM polybot_fills WHERE mode = $1 AND {VER} = $2 AND {ASSET_SQL} = $3", mode, version, asset)
        # the running total for a small chart: cumulative profit after each settled window (last 200)
        rows = await c.fetch(f"SELECT settled_at, pnl FROM polybot_windows WHERE mode = $1 AND settled_at IS NOT NULL AND {VER} = $2 "
                             f"AND {ASSET_SQL} = $3 ORDER BY settled_at DESC LIMIT 200", mode, version, asset)
        older = await c.fetch(f"SELECT {VER} AS v, count(*) AS n, count(*) FILTER (WHERE pnl > 0) AS wins, coalesce(sum(pnl),0) AS pnl, "
                              f"min(settled_at) AS first FROM polybot_windows WHERE mode = $1 AND settled_at IS NOT NULL AND {VER} <> $2 "
                              f"AND {ASSET_SQL} = $3 GROUP BY 1 ORDER BY first", mode, version, asset)
    run, curve = 0.0, []
    for r in reversed(rows):
        run += float(r["pnl"] or 0)
        curve.append([r["settled_at"].isoformat(), round(run, 2)])
    out["fills"] = int(fills or 0)
    out["sweeps"] = await sweep_stats(pool, mode, version, asset)
    out["shadow"] = await shadow_stats(pool, mode, version, asset)
    out["shadow_detail"] = await shadow_detail(pool, asset, account)
    out["curve"] = curve
    out["older"] = [{"version": r["v"], "trades": int(r["n"]), "wins": int(r["wins"]), "profit": round(float(r["pnl"]), 2)} for r in older]
    return out


SHADOW_RULES = {
    "s5_shrink": "5-minute: the model shrunk 25% toward 50% (it overstates its confidence); buys the side at least 5c under that view, 30-240 s before the close",
    "s5_floor85": "5-minute: the model's side at an ask of 85c or more, 3c under fair, 10-240 s before the close (ETH 5m's floor)",
    "s5_floor75": "5-minute: the model's side at an ask of 75c or more, 3c under fair, 10-240 s before the close (SOL 5m's floor)",
    "s15_trend": "15-minute: buys the side recent spot movement favours, when its ask is 5c under fair and at most 80c, 180-600 s before the close",
    "s15_distance": "15-minute: the model is 80%+ sure and the side is at least 7c under fair, 120-300 s before the close",
    "s15_vol": "15-minute: the model is 70%+ sure under both the tighter and wider volatility, and the ask is 5c under both, 120-600 s before the close",
    "m5_pair": "5-minute maker: rests a bid on both sides at fair value minus 3c, 150-240 s before the close; fills when the market sells down to it, pulled in the last minute",
    "m15_maker": "15-minute maker: rests a bid on the side the model favours (60%+) at fair value minus 6c, 300-600 s before the close; pulled in the last minute",
    "s5_gate": "5-minute gate: buys a side only when its ask is at least 5c under the measured win rate of its confidence bucket (30+ settled trades), 60-240 s before the close",
    "s15_gate": "15-minute gate: buys a side only when its ask is at least 5c under the measured win rate of its confidence bucket (30+ settled trades), 120-600 s before the close",
}


async def export_candidates(pool, account: str = "") -> str:
    """v1.42: every entry the bots considered, with the rule that stopped it (blank when allowed), the price, the model's view,
    and how the window settled (polybot_window_results, which records every settled window, traded or not), so each blocked buy
    can be checked against what it would have made. An account's export holds only its own copies (asset tagged with that account)."""
    import csv
    import io

    cols = ["time_utc", "desk", "slug", "side", "buy_price", "fair", "momentum_z", "sigma", "vol_tag", "secs_left",
            "blocked_by", "window_result", "would_have_won"]
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(cols)
    tag = "@" + account if account else ""
    async with pool.acquire() as c:
        # owner 2026-11-21: an account's own copies carry its tag in the asset ("btc@u123..."); the owner's rows carry none
        rows = [r for r in await c.fetch("SELECT ts, asset, slug, side, price, fair, momentum_z, sigma, vol_tag, secs_left, blocked_by "
                                         "FROM polybot_candidates ORDER BY ts")
                if (str(r["asset"]).endswith(tag) if tag else "@" not in str(r["asset"]))]
        slugs = sorted({r["slug"] for r in rows})
        results = {}
        if slugs:
            for r in await c.fetch("SELECT slug, asset, up FROM polybot_window_results WHERE slug = ANY($1::text[])", slugs):
                results[(r["asset"], r["slug"])] = "UP" if r["up"] else "DOWN"
    from openjarvis.polybot.config import desk_label
    for r in rows:
        base = str(r["asset"]).split("@")[0]                       # the window's result is the market's, the same for every account
        res = results.get((r["asset"], r["slug"])) or results.get((base, r["slug"]), "")
        won = "" if not res else (1 if res == str(r["side"]).upper() else 0)
        wr.writerow([r["ts"], desk_label(base), r["slug"], r["side"], r["price"], r["fair"], r["momentum_z"], r["sigma"],
                     r["vol_tag"], r["secs_left"], r["blocked_by"] or "", res or "unknown", won])
    return buf.getvalue()


async def export_settle_checks(pool, account: str = "") -> str:
    """2026-11-20 (owner): every settled window with what the bot's own index said next to the venue's answer: the price to beat,
    the index's closing average, the gap (in dollars, basis points and the move expected in a minute), and whether they agreed.
    Measures how far the index and Polymarket's settlement feed disagree near the line. Owner's desks only (subscribers: header)."""
    import csv
    import io
    import math

    cols = ["settled_utc", "desk", "slug", "price_to_beat", "index_close", "gap_usd", "gap_bp", "gap_sd_1min",
            "index_says", "venue_says", "agree"]
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(cols)
    if account:
        return buf.getvalue()
    await _ensure(pool)
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT settled_at, asset, slug, up, price_to_beat, index_close, index_up, sigma FROM polybot_window_results "
                             "WHERE index_close IS NOT NULL AND price_to_beat IS NOT NULL AND position('@' in asset) = 0 ORDER BY settled_at")
    from openjarvis.polybot.config import desk_label
    for r in rows:
        beat, ic = float(r["price_to_beat"]), float(r["index_close"])
        gap = ic - beat
        sig = float(r["sigma"]) if r["sigma"] else None
        sd = round(gap / (sig * math.sqrt(60)), 3) if sig and sig > 0 else ""
        iu, vu = bool(r["index_up"]), bool(r["up"])
        wr.writerow([r["settled_at"].isoformat(), desk_label(r["asset"]), r["slug"], beat, ic, round(gap, 6),
                     round(gap / beat * 1e4, 3) if beat else "", sd, "UP" if iu else "DOWN", "UP" if vu else "DOWN", 1 if iu == vu else 0])
    return buf.getvalue()


async def export_shadow(pool, asset: str = "btc", account: str = "") -> str:
    """The shadow log for this desk as CSV: one row per settled window, with the model's probability that Up wins at its
    standard view (1.0x volatility), tighter (0.7x) and wider (1.4x), the book's view, the seconds left at the last tick, and the result."""
    import csv
    import io

    async with pool.acquire() as c:
        raw = await c.fetchval("SELECT value FROM polybot_kv WHERE key = $1", kv_key("shadow2", asset, account))
    d = {} if raw is None else (json.loads(raw) if isinstance(raw, str) else raw)
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["slug", "result", "up_won", "secs_left_at_last_tick", "p_up_model_1.0x", "p_up_tight_0.7x", "p_up_wide_1.4x", "p_up_book",
                 "entry_sigma", "entry_conf", "entry_cal_conf", "window_return", "sigma_lot_pct", "conf_lot_pct", "cal_lot_pct"])
    for r in (d or {}).get("log") or []:
        wr.writerow([r.get("slug"), "UP" if r.get("up") else "DOWN", 1 if r.get("up") else 0, r.get("secs_left"),
                     r.get("fair"), r.get("lo"), r.get("hi"), r.get("book"),
                     r.get("entry_sigma"), r.get("entry_conf"), r.get("entry_cal_conf"), r.get("window_return"), r.get("sigma_lot_pct"),
                     r.get("conf_lot_pct"), r.get("cal_lot_pct")])
    sz = (d or {}).get("sizing") or {}
    wr.writerow([])
    wr.writerow(["SIZING TOTALS (same trades, % of balance x return, not compounded)", "windows", sz.get("windows", 0),
                 "sigma tiers", round(sz.get("sigma_pts", 0.0), 2), "confidence tiers", round(sz.get("conf_pts", 0.0), 2),
                 "calibrated-confidence tiers", round(sz.get("cal_pts", 0.0), 2)])
    return buf.getvalue()


async def shadow_detail(pool, asset: str, account: str = "") -> Optional[Dict[str, Any]]:
    """Shadow results, readable from the Station and the API: the six shadow strategies for this desk's timeframe (recorded only,
    never traded) and the model's calibration by confidence bucket. Saved by the desk each window under polybot_kv
    (key 'shadow' for BTC, 'shadow:<asset>' for the others)."""
    from openjarvis.polybot import shadow as shadow_mod

    async with pool.acquire() as c:
        raw = await c.fetchval("SELECT value FROM polybot_kv WHERE key = $1", kv_key("shadow2", asset, account))
    if raw is None:
        return None
    d = json.loads(raw) if isinstance(raw, str) else (raw or {})
    calib = d.get("calib") or {}
    rec = d.get("rec") or {}
    out: Dict[str, Any] = {"calib": {}}
    names = shadow_mod.names_for(900 if str(asset).endswith("15") else 300)
    out["strategies"] = []
    for n in names:
        r = rec.get(n) or {}
        w, staked, profit = int(r.get("windows", 0)), float(r.get("staked", 0)), float(r.get("profit", 0))
        out["strategies"].append({"name": n, "rule": SHADOW_RULES.get(n, ""), "windows": w, "wins": int(r.get("wins", 0)),
                                  "win_rate": round(100.0 * int(r.get("wins", 0)) / w, 1) if w else None,
                                  "staked": round(staked, 2), "profit": round(profit, 2),
                                  "return_pct": round(100.0 * profit / staked, 1) if staked else None})
    for kind in ("model", "book"):
        rows = []
        for lab, cell in (calib.get(kind) or {}).items():
            n, wins = int(cell.get("n", 0)), int(cell.get("wins", 0))
            rows.append({"bucket": lab, "trades": n, "won": wins,
                         "win_rate": round(100.0 * wins / n, 1) if n else None})
        out["calib"][kind] = rows
    return out


async def shadow_stats(pool, mode: str, version: str, asset: str) -> Dict[str, Any]:
    """v1.14 learned pair sizing in shadow: the pairs' actual profit next to what they would have made at the size their setup
    had earned (a window's pair P&L scales with its size: shadow = actual x shadow_pct / actual_pct)."""
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT f.slug, f.side, f.qty, f.cost, f.features, w.up FROM polybot_fills f JOIN polybot_windows w ON w.slug = f.slug AND w.mode = f.mode "
            "WHERE f.mode = $1 AND coalesce(f.version, '1.0') = $2 AND " + desk_sql("f.slug") + " = $3 AND w.up IS NOT NULL "
            "AND coalesce(f.features->>'sweep', 'false') <> 'true' AND f.features ? 'shadow_pct' ORDER BY f.ts", mode, version, asset)
    per: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        feat = _j(r["features"])
        won = bool(r["up"]) if r["side"] == "up" else not bool(r["up"])
        w = per.setdefault(r["slug"], {"pnl": 0.0, "shadow": feat.get("shadow_pct"), "actual": feat.get("actual_pct"), "tier": feat.get("shadow_tier")})
        w["pnl"] += float(r["qty"]) * (1.0 if won else 0.0) - float(r["cost"])
    actual = sum(w["pnl"] for w in per.values())
    shadow = sum(w["pnl"] * (float(w["shadow"]) / float(w["actual"]) if w["shadow"] and w["actual"] else 1.0) for w in per.values())
    tiers: Dict[str, int] = {}
    for w in per.values():
        tiers[w["tier"] or "unproven"] = tiers.get(w["tier"] or "unproven", 0) + 1
    return {"windows": len(per), "actual": round(actual, 2), "shadow": round(shadow, 2), "tiers": tiers}


async def sweep_stats(pool, mode: str, version: str, asset: str) -> Dict[str, Any]:
    """Closing sweeps on their own: windows with sweep fills, how many of those sweeps won, and what they made."""
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT f.slug, f.side, f.qty, f.cost, w.up FROM polybot_fills f JOIN polybot_windows w ON w.slug = f.slug AND w.mode = f.mode "
            "WHERE f.mode = $1 AND coalesce(f.version, '1.0') = $2 AND " + desk_sql("f.slug") + " = $3 AND w.up IS NOT NULL "
            "AND (f.features->>'sweep') = 'true'", mode, version, asset)
    per: Dict[str, float] = {}
    for r in rows:
        won = bool(r["up"]) if r["side"] == "up" else not bool(r["up"])
        per[r["slug"]] = per.get(r["slug"], 0.0) + float(r["qty"]) * (1.0 if won else 0.0) - float(r["cost"])
    n, wins = len(per), sum(1 for v in per.values() if v > 0)
    return {"trades": n, "wins": wins, "win_rate": round(100.0 * wins / n, 1) if n else None, "profit": round(sum(per.values()), 2)}


def sd_from_strike(conf) -> Optional[float]:
    """How many standard deviations of remaining move the index sits from the strike, implied by the model's confidence in the side bought:
    the inverse normal of the confidence (0 = a coin flip at the strike; +2 = two sigmas on the bet's side)."""
    if not isinstance(conf, (int, float)):
        return None
    from statistics import NormalDist
    c = min(max(float(conf), 0.001), 0.999)
    return round(NormalDist().inv_cdf(c), 2)


def buy_price(f: Dict[str, Any]) -> float:
    """What a fill actually paid per contract: the Up price for an Up buy, 1 minus it for a Down buy."""
    return float(f["price"]) if f["side"] == "up" else 1.0 - float(f["price"])


async def trades(pool, mode: str, limit: int = 50, version: str = STRATEGY_VERSION, asset: str = "btc") -> List[Dict[str, Any]]:
    """The trade log: one row per window traded, with every fill in it (time, side, price, size, cost, fair value at the time)."""
    await _ensure(pool)
    async with pool.acquire() as c:
        ws = await c.fetch(f"SELECT * FROM polybot_windows WHERE mode = $1 AND {VER} = $3 AND {ASSET_SQL} = $4 ORDER BY window_end DESC LIMIT $2",
                           mode, max(1, min(limit, 500)), version, asset)
        slugs = [w["slug"] for w in ws]
        fs = await c.fetch("SELECT * FROM polybot_fills WHERE mode = $1 AND slug = ANY($2::text[]) ORDER BY ts", mode, slugs) if slugs else []
    by: Dict[str, List[Dict[str, Any]]] = {}
    for f in fs:
        feat = _j(f["features"]) if "features" in f.keys() else {}
        m = feat.get("model")
        # confidence: the model's probability that the side bought wins, at the moment it was bought
        conf = None if not isinstance(m, (int, float)) else round(m if f["side"] == "up" else 1.0 - m, 3)
        by.setdefault(f["slug"], []).append({"ts": f["ts"].isoformat(), "side": f["side"], "price": f["price"], "qty": f["qty"],
                                             "sweep": feat.get("sweep") is True, "conf": conf,
                                             "cost": round(f["cost"], 2), "fair": round(f["fair"], 3) if f["fair"] is not None else None,
                                             "sigma": feat.get("sigma"), "secs_left": feat.get("secs_left")})
    out = []
    for w in ws:
        uq, dq = float(w["up_qty"] or 0), float(w["down_qty"] or 0)
        pair = uq > 0 and dq > 0 and min(uq, dq) / max(uq, dq) >= 0.9          # a matched pair: it wins either way, but still shows the model's view
        main = "up" if uq >= dq else "down"
        wf = [f for f in by.get(w["slug"], []) if f["conf"] is not None and not f["sweep"]]
        wedge = None
        if pair:
            wconf = wf[0]["conf"] if wf else None                                 # the model's odds on the side the pair opened with
        else:
            mf = [f for f in wf if f["side"] == main] or [f for f in by.get(w["slug"], []) if f["side"] == main and f["conf"] is not None]
            wconf = None if not mf else round(sum(f["conf"] * f["cost"] for f in mf) / (sum(f["cost"] for f in mf) or 1), 3)
            qty = sum(f["qty"] for f in mf)
            wedge = None if not mf or not qty else round(wconf - sum(buy_price(f) * f["qty"] for f in mf) / qty, 3)
        mv = next((f for f in wf if f["side"] == main), None) if not pair else None
        sd = sd_from_strike(mv["conf"]) if mv else None
        move = None
        if mv and isinstance(mv.get("sigma"), (int, float)) and isinstance(mv.get("secs_left"), (int, float)):
            move = round(mv["sigma"] * math.sqrt(max(1.0, mv["secs_left"])), 2)     # dollars the index can still travel before the close
        out.append({"slug": w["slug"], "window_start": w["window_start"].isoformat() if w["window_start"] else None,
                    "window_end": w["window_end"].isoformat() if w["window_end"] else None, "price_to_beat": w["price_to_beat"],
                    "close_price": w["close_price"], "result": None if w["up"] is None else ("UP" if w["up"] else "DOWN"),
                    "up_qty": w["up_qty"], "up_cost": w["up_cost"], "down_qty": w["down_qty"], "down_cost": w["down_cost"],
                    "paid": round((w["up_cost"] or 0) + (w["down_cost"] or 0), 2), "pnl": w["pnl"], "fills": by.get(w["slug"], []),
                    "pair": pair, "conf": wconf, "edge": wedge, "move": move, "sd": sd})
    return out


def _acct_mode_sql(col: str, param: str = "$2") -> str:
    """Rows of ONE account only: the owner's ledger labels carry no '@'; a subscriber's end in '@<tag>' (param = the tag, '' for the owner)."""
    return (f"(CASE WHEN {param}::text = '' THEN position('@' in {col}) = 0 "
            f"ELSE {col} LIKE '%@' || {param}::text END)")


async def export_all(pool, kind: str, account: str = "") -> str:
    """Every desk's fills ('fills') or shadow log ('shadow') in ONE CSV, with a desk column first. Fills are in time order across
    desks; shadow rows are grouped by desk. Only this account's desks (a subscriber's two, the owner's twelve)."""
    import csv
    import io

    from openjarvis.polybot.config import ASSETS, desk_label

    assets = list(TENANT_ASSETS) if account else [a for a in ASSETS if a in KEYS]
    header, out = None, []
    for a in assets:
        text = await (export_fills(pool, asset=a, account=account) if kind == "fills" else export_shadow(pool, asset=a, account=account))
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            continue
        header = header or ["desk"] + rows[0]
        out += [[desk_label(a)] + r for r in rows[1:] if r]
    if kind == "fills":
        out.sort(key=lambda r: r[1] if len(r) > 1 else "")      # the fill time, oldest first
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(header or ["desk"])
    wr.writerows(out)
    return buf.getvalue()


async def export_fills(pool, asset: str = "btc", account: str = "") -> str:
    """Every fill this desk ever made (all strategy versions, paper and live) as CSV: one row per fill, with the window's result and P&L."""
    import csv
    import io

    await _ensure(pool)
    sql = ("SELECT f.ts, f.mode, f.slug, f.side, f.price, f.qty, f.cost, f.fair, f.features, "
           "w.up, w.pnl, w.window_end, coalesce(w.version, '1.0') AS version "
           "FROM polybot_fills f LEFT JOIN polybot_windows w ON w.slug = f.slug AND w.mode = f.mode "
           f"WHERE {desk_sql('f.slug')} = $1 AND " + _acct_mode_sql("f.mode") + " ORDER BY f.ts")
    async with pool.acquire() as c:
        rows = await c.fetch(sql, asset, account)
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["time_utc", "mode", "version", "window_end_utc", "slug", "side", "price", "qty", "cost", "fair",
                 "model_prob_side", "edge", "sd_from_strike", "sigma", "secs_left", "move_left_usd", "momentum_z", "trend_z", "mid", "spread",
                 "book_up", "model_vs_market", "sweep", "window_result", "window_pnl", "decision_method", "decision_raw", "decision_conf",
                 "decision_price", "decision_book_age_s", "decision_last_trade", "decision_last_trade_age_s",
                 "decision_to_fill_s", "paid_vs_decision", "decision_qty", "decision_ask_size", "decision_depth_2c",
                 "decision_depth_2c_usd", "fee"])
    for r in rows:
        feat = _j(r["features"])
        m = feat.get("model")
        conf = None if not isinstance(m, (int, float)) else round(m if r["side"] == "up" else 1.0 - m, 3)
        res = None if r["up"] is None else ("UP" if r["up"] else "DOWN")
        bp = r["price"] if r["side"] == "up" else 1.0 - r["price"]
        edge = None if conf is None else round(conf - bp, 3)
        sg, sl = feat.get("sigma"), feat.get("secs_left")
        mv = round(sg * math.sqrt(max(1.0, sl)), 2) if isinstance(sg, (int, float)) and isinstance(sl, (int, float)) else None
        wr.writerow([r["ts"].isoformat(), r["mode"], r["version"],
                     r["window_end"].isoformat() if r["window_end"] else "", r["slug"], r["side"], r["price"], r["qty"],
                     round(r["cost"], 2), r["fair"], conf, edge, sd_from_strike(conf), sg, sl, mv,
                     feat.get("momentum_z"), feat.get("trend_z"), feat.get("mid"), feat.get("spread"), feat.get("book_up"),
                     feat.get("model_vs_market"), feat.get("sweep") is True, res, r["pnl"],
                     feat.get("decision_method"), feat.get("decision_raw"), feat.get("decision_conf"),
                     feat.get("decision_price"), feat.get("decision_book_age_s"), feat.get("decision_last_trade"),
                     feat.get("decision_last_trade_age_s"), feat.get("decision_to_fill_s"),
                     None if not isinstance(feat.get("decision_price"), (int, float)) else round(bp - feat["decision_price"], 4),
                     feat.get("decision_qty"), feat.get("decision_ask_size"), feat.get("decision_depth_2c"),
                     feat.get("decision_depth_2c_usd"),
                     feat.get("fee") if isinstance(feat.get("fee"), (int, float)) else
                     (round(0.0695 * bp * (1.0 - bp) * float(r["qty"]), 6) if str(r["mode"]).startswith("live") else 0.0)])
    return buf.getvalue()


BUCKETS = {
    # feature: [(label, low, high)] - which conditions a fill happened in
    "edge": [("edge under 3c", -9, 0.03), ("edge 3-6c", 0.03, 0.06), ("edge 6-10c", 0.06, 0.10), ("edge over 10c", 0.10, 9)],
    "momentum_with": [("price moving against the bet", -99, -0.3), ("price flat", -0.3, 0.3), ("price moving with the bet", 0.3, 99)],
    "secs_left": [("over 3 min left", 180, 9999), ("1-3 min left", 60, 180), ("last minute", -1, 60)],
    "cost": [("bought under 30c", 0, 0.30), ("bought 30-70c", 0.30, 0.70), ("bought over 70c", 0.70, 1.01)],
    # owner, 2026-11-19: every angle, including when and how sure
    "model_side": [("model under 80% sure", 0, 0.80), ("model 80-85% sure", 0.80, 0.85), ("model 85-90% sure", 0.85, 0.90),
                   ("model 90-95% sure", 0.90, 0.95), ("model 95%+ sure", 0.95, 1.01)],
    "hour_la": [("overnight (12-6 AM)", 0, 6), ("morning (6 AM-12 PM)", 6, 12), ("afternoon (12-6 PM)", 12, 18),
                ("evening (6 PM-12 AM)", 18, 24)],
    "weekday": [("Monday", 0, 1), ("Tuesday", 1, 2), ("Wednesday", 2, 3), ("Thursday", 3, 4), ("Friday", 4, 5),
                ("Saturday", 5, 6), ("Sunday", 6, 7)],
}


SCOPES = ("rules", "version", "all")     # 2026-11-17 (owner): what wins and what loses over three sets of fills
SCOPE_LABELS = {"version": "this version", "all": "all history", "rules": "all history, current rules"}
_RULE_DESKS: Dict[str, Any] = {}
_AN_CACHE: Dict[tuple, tuple] = {}
AN_TTL_S = 60.0


def _rule_desk(asset: str):
    """A paper Desk used only to ask today's entry rules about a past fill (never trades)."""
    if asset not in _RULE_DESKS:
        from openjarvis.polybot.desk import Desk
        _RULE_DESKS[asset] = Desk(from_env({}, asset=asset), None, None)
    return _RULE_DESKS[asset]


def still_allowed(asset: str, t: Dict[str, Any]) -> bool:
    """Would today's rules still take this past fill? Replays what the fill record holds: the model's confidence for the side
    against the desk's confidence floor, and the entry rules (price floors, momentum, Up/Down and last-minute blocks).
    Closing sweeps have their own rules and are kept. Calibration and hour sizing at the time cannot be replayed exactly."""
    from openjarvis.polybot import directional

    if t.get("sweep"):
        return True
    conf = t.get("model_side")
    if conf is not None and conf < directional.min_conf(asset, calibrated=True):
        return False
    try:
        d = _rule_desk(asset)
    except Exception:
        return True
    return d._block_rule(float(t.get("secs_left") or 0.0), t["cost"], t.get("momentum_z"), t["side"]) is None


async def analysis(pool, mode: str = "paper", version: str = STRATEGY_VERSION, asset: str = "btc", scope: str = "version") -> Dict[str, Any]:
    """Every settled fill, cut by the conditions it happened in: how often that kind of trade won and what it made per contract.
    This is how the desk's rules get tuned: keep the conditions that make money, cut the ones that lose it.
    scope: "version" = the current strategy version's fills; "all" = every version; "rules" = every version, but only the fills
    today's rules would still take (bigger sample, without trades the bot no longer makes)."""
    scope = scope if scope in SCOPES else "version"
    key = (mode, version, asset, scope)
    hit = _AN_CACHE.get(key)
    if hit is not None and time.monotonic() - hit[0] < AN_TTL_S and scope != "version":
        return hit[1]
    await _ensure(pool)
    ver_sql, args = ("AND coalesce(f.version, '1.0') = $2 ", (mode, version, asset)) if scope == "version" else ("", (mode, asset))
    n = 3 if scope == "version" else 2
    async with pool.acquire() as c:
        rows = await c.fetch(
            f"SELECT f.ts, f.side, f.price, f.qty, f.cost, f.features, w.up FROM polybot_fills f JOIN polybot_windows w ON w.slug = f.slug AND w.mode = f.mode "
            f"WHERE f.mode = $1 {ver_sql}AND w.up IS NOT NULL AND " + desk_sql("f.slug") + f" = ${n}", *args)
    trades = []
    for r in rows:
        feat = _j(r["features"])
        cost_per = r["price"] if r["side"] == "up" else 1.0 - r["price"]
        won = bool(r["up"]) if r["side"] == "up" else not bool(r["up"])
        mz = feat.get("momentum_z")
        m = feat.get("model")
        # 2026-11-20: after Polymarket's taker fee (recorded per fill from today; earlier live fills get the same formula)
        fee = feat.get("fee")
        fee = float(fee) if isinstance(fee, (int, float)) else (0.0695 * cost_per * (1.0 - cost_per) * float(r["qty"]) if str(mode).startswith("live") else 0.0)
        t = {"won": won, "qty": float(r["qty"]), "pnl": float(r["qty"]) * ((1.0 if won else 0.0) - cost_per) - fee,
             "edge": feat.get("edge"), "secs_left": feat.get("secs_left"), "cost": cost_per, "side": r["side"],
             "momentum_z": mz, "sweep": feat.get("sweep") is True,
             "model_side": None if not isinstance(m, (int, float)) else (m if r["side"] == "up" else 1.0 - m),
             "momentum_with": None if mz is None else (mz if r["side"] == "up" else -mz)}
        try:
            from openjarvis.polybot.risk import LA
            lt = r["ts"].astimezone(LA)
            t["hour_la"], t["weekday"] = lt.hour, lt.weekday()          # Pacific time: when in the day and week the fill happened
        except Exception:
            pass
        if scope == "rules" and not still_allowed(asset, t):
            continue
        trades.append(t)
    out: Dict[str, Any] = {"mode": mode, "version": version, "scope": scope, "scope_label": SCOPE_LABELS[scope],
                           "fills": len(trades), "groups": {}}
    for feat, buckets in BUCKETS.items():
        rows_out = []
        for label, lo, hi in buckets:
            g = [t for t in trades if t.get(feat) is not None and lo <= t[feat] < hi]
            if not g:
                continue
            staked = sum(t["qty"] * t["cost"] for t in g)
            rows_out.append({"condition": label, "fills": len(g), "win_rate": round(100.0 * sum(t["won"] for t in g) / len(g), 1),
                             "profit": round(sum(t["pnl"] for t in g), 2),
                             "per_contract": round(sum(t["pnl"] for t in g) / max(1e-9, sum(t["qty"] for t in g)), 3),
                             "return_pct": round(100.0 * sum(t["pnl"] for t in g) / staked, 1) if staked > 0 else None})
        out["groups"][feat] = rows_out
    if scope != "version":
        _AN_CACHE[key] = (time.monotonic(), out)
    return out


async def analysis_scopes(pool, mode: str, asset: str, version_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The breakdown over each scope for one ledger (the Station switches between them). version_result reuses a breakdown of
    the current version already computed for this ledger."""
    out = {}
    for sc in SCOPES:
        if sc == "version" and version_result is not None:
            out[sc] = {**version_result, "scope": "version", "scope_label": SCOPE_LABELS["version"]}
        else:
            out[sc] = await analysis(pool, mode, asset=asset, scope=sc)
    return out


def hour_rows(rows) -> List[Dict[str, Any]]:
    """Settled windows grouped by the Los Angeles hour they opened in: trades, wins, staked, profit and return on staked."""
    from openjarvis.server.crew.finance import LA

    per: Dict[int, Dict[str, float]] = {}
    for r in rows:
        t = r["window_start"] or r["window_end"]
        if t is None:
            continue
        h = t.astimezone(LA).hour
        g = per.setdefault(h, {"trades": 0, "wins": 0, "staked": 0.0, "profit": 0.0})
        pnl = float(r["pnl"] or 0)
        g["trades"] += 1
        g["wins"] += 1 if pnl > 0 else 0
        g["staked"] += float(r["up_cost"] or 0) + float(r["down_cost"] or 0)
        g["profit"] += pnl
    out = []
    for h in sorted(per):
        g = per[h]
        label = f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}"
        out.append({"hour": h, "label": label, "trades": int(g["trades"]), "wins": int(g["wins"]),
                    "win_rate": round(100.0 * g["wins"] / g["trades"], 1), "staked": round(g["staked"], 2), "profit": round(g["profit"], 2),
                    "return_pct": round(100.0 * g["profit"] / g["staked"], 1) if g["staked"] else None})
    return out


async def by_hour(pool, mode: str, asset: str = "btc") -> Dict[str, Any]:
    """Profit by hour of day (Los Angeles) for one desk and ledger, over EVERY strategy version (more data than the scoreboard,
    which counts only the current version)."""
    await _ensure(pool)
    async with pool.acquire() as c:
        rows = await c.fetch(f"SELECT window_start, window_end, up_cost, down_cost, pnl FROM polybot_windows WHERE mode = $1 "
                             f"AND settled_at IS NOT NULL AND {ASSET_SQL} = $2", mode, asset)
    hours = hour_rows(rows)
    return {"mode": mode, "windows": sum(h["trades"] for h in hours), "hours": hours}


def analysis_text(a: Dict[str, Any]) -> str:
    lines = [f"Trade analysis ({a['mode']}, v{a['version']}, {a['fills']} settled fills): profit per contract by condition"]
    for rows in a["groups"].values():
        lines.append("  " + "; ".join(f"{r['condition']}: {r['fills']} fills, won {r['win_rate']}%, {r['per_contract']:+.3f}/contract ({r['profit']:+.2f})" for r in rows))
    return "\n".join(lines)


async def export_daily(pool, account: str = "", rh_rows: Optional[list] = None) -> bytes:
    """The owner's live account profit by trading day (Los Angeles date), newest day on top. Every strategy version before the
    current one is one row at the bottom, as a date range. Green when the day made money, red when it lost. A total box shows
    all profit. Below, each bot's profit by the same days. Returns an .xlsx file."""
    import io
    from collections import defaultdict
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openjarvis.polybot.brain import STRATEGY_VERSION
    from openjarvis.server.crew.finance import LA

    await _ensure(pool)
    async with pool.acquire() as c:
        kv = await c.fetch("SELECT key, value FROM polybot_kv WHERE key LIKE $1", "day_start:%")
        rows = await c.fetch(f"SELECT settled_at, window_end, pnl, {VER} AS version, {ASSET_SQL} AS asset FROM polybot_windows "
                             f"WHERE mode = $1 AND settled_at IS NOT NULL AND {ASSET_SQL} = ANY($2::text[])",
                             label_for("live", account), list(TENANT_ASSETS if account else KEYS))
    # the account's balance at midnight each day (written by the desk at the day's rollover)
    suffix = f"@{account}" if account else ""
    starts: Dict[str, float] = {}
    for r in kv:
        rest = r["key"][len("day_start:"):]
        if (suffix and rest.endswith(suffix)) or (not suffix and "@" not in rest):
            starts[rest[: len(rest) - len(suffix)] if suffix else rest] = float(_j(r["value"]).get("start") or 0)
    from openjarvis.polybot.config import ASSETS as _ORDER, desk_label
    names = {a: desk_label(a) for a in KEYS}
    # every bot that has ever traded live, in the Station's order (BTC 5m, BTC 15m, BTC 1h, ETH 5m, ...); BTC 5m and 15m always shown
    have = {r["asset"] for r in rows} | {"btc", "btc15"}
    bots = [names[a] for a in _ORDER if a in have] + [names[a] for a in sorted(have) if a not in _ORDER and a in names]
    per_day: Dict[Any, float] = defaultdict(float)
    per_day_bot: Dict[Any, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    old_bot: Dict[str, float] = defaultdict(float)
    old_days: List[Any] = []
    for r in rows:
        t = r["settled_at"] or r["window_end"]
        if t is None:
            continue
        pnl = float(r["pnl"] or 0)
        bot = names.get(r["asset"], r["asset"])
        if r["version"] == STRATEGY_VERSION:
            day = t.astimezone(LA).date()
            per_day[day] += pnl
            per_day_bot[day][bot] += pnl
        else:
            old_days.append(t.astimezone(LA).date())
            old_bot[bot] += pnl
    old_profit = sum(old_bot.values())
    total_profit = sum(per_day.values()) + old_profit

    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    green = (PatternFill("solid", fgColor="C6EFCE"), Font(color="006100", bold=True))
    red = (PatternFill("solid", fgColor="FFC7CE"), Font(color="9C0006", bold=True))
    head_fill = PatternFill("solid", fgColor="1F3864")
    money = '$#,##0.00;-$#,##0.00'

    def style_for(value):
        return green if value > 0 else red if value < 0 else (None, None)

    def header(row, col, text):
        c = ws.cell(row=row, column=col, value=text)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border

    def value(row, col, v, label=False, fmt=None):
        c = ws.cell(row=row, column=col, value=v if label else round(v, 2))
        c.alignment = Alignment(horizontal="center")
        c.border = border
        if not label:
            c.number_format = fmt or money
            fill, font = style_for(v)
            if fill is not None:
                c.fill = fill
                c.font = font
        return c

    def roi(r, pnl, bal):
        # owner 2026-11-23: the day's profit as a % of that day's starting balance; blank where no balance was recorded
        c = ws.cell(row=r, column=4)
        c.border, c.alignment = border, Alignment(horizontal="center")
        if bal and bal > 0:
            v = pnl / bal
            c.value, c.number_format = round(v, 4), "0.00%;-0.00%"
            fill, font = style_for(v)
            if fill is not None:
                c.fill, c.font = fill, font

    def range_label(days):
        lo, hi = min(days), max(days)
        return f"{lo.month}/{lo.day}/{lo:%y} - {hi.month}/{hi.day}/{hi:%y}"

    wb = Workbook()
    ws = wb.active
    ws.title = "Daily P&L"

    # section 1: date and daily profit, newest on top
    header(1, 1, "Date")
    header(1, 2, "Starting balance")
    header(1, 3, "Daily profit or loss")
    header(1, 4, "Daily ROI %")
    row = 2
    for day in sorted(per_day, reverse=True):
        value(row, 1, day, label=True, fmt="m/d/yyyy").number_format = "m/d/yyyy"
        if day.isoformat() in starts:
            bal = ws.cell(row=row, column=2, value=round(starts[day.isoformat()], 2))   # a balance, not a profit: no green or red
            bal.number_format, bal.alignment, bal.border = money, Alignment(horizontal="center"), border
        else:
            ws.cell(row=row, column=2).border = border           # no balance was recorded that day (before this was tracked)
        value(row, 3, per_day[day])
        roi(row, per_day[day], starts.get(day.isoformat()))
        row += 1
    if old_days:
        value(row, 1, range_label(old_days), label=True)
        ws.cell(row=row, column=2).border = border
        value(row, 3, old_profit)
        roi(row, old_profit, None)
        row += 1
    last_day_row = row - 1

    # the account box, to the right of the list (owner 2026-11-21: the same stack as the Station): the real account value,
    # all-time result (account + withdrawn - cash deposited - referral), what went in and out, then the settled trades' total
    st_acct = (await get_kv(pool, kv_key("status", "btc", account))).get("account_value")
    header(1, 5, "Account")
    header(1, 6, "Amount")
    box_row = 2

    def box(label, v, color=None, pnl=False):
        nonlocal box_row
        lc = ws.cell(row=box_row, column=5, value=label)
        lc.border, lc.font = border, Font(bold=True, color=color or "000000")
        if pnl:
            value(box_row, 6, v)
        else:
            c = ws.cell(row=box_row, column=6, value=round(v, 2))
            c.number_format, c.alignment, c.border = money, Alignment(horizontal="center"), border
            c.font = Font(bold=True, color=color or "000000")
        box_row += 1

    if st_acct is not None:
        box("Polymarket account", float(st_acct), "1F77B4")
    await load_money(pool, account)
    dep, ref, wd = deposits(account) or 0.0, referral(account) or 0.0, withdrawn(account) or 0.0
    if (dep or ref or wd) and st_acct is not None:                 # once that account has entered its amounts
        box("All-time", float(st_acct) + wd - dep - ref, pnl=True)
        box_row += 1
        box("Cash Deposited", dep, "B8860B")
        if ref:
            box("Referral Deposited", ref, "7B3FA0")
        if wd:
            box("Withdrawn", wd, "C71585")
        box_row += 1
    box("Settled trades total", total_profit, pnl=True)

    # section 2: each bot by the same days
    row = max(row, box_row) + 2
    ws.cell(row=row, column=1, value="By bot").font = Font(bold=True, size=12)
    row += 1
    tot_col = 2 + len(bots)
    for col, text in [(1, "Date")] + [(2 + i, b) for i, b in enumerate(bots)] + [(tot_col, "Day total")]:
        header(row, col, text)
    row += 1
    for day in sorted(per_day, reverse=True):
        value(row, 1, day, label=True).number_format = "m/d/yyyy"
        for i, bot in enumerate(bots):
            value(row, 2 + i, per_day_bot[day].get(bot, 0.0))
        value(row, tot_col, per_day[day])
        row += 1
    if old_days:
        value(row, 1, range_label(old_days), label=True)
        for i, bot in enumerate(bots):
            value(row, 2 + i, old_bot.get(bot, 0.0))
        value(row, tot_col, old_profit)
        row += 1
    # owner 2026-11-27: each bot's all-time total (every day plus the older-versions row) under its column; the last column is the
    # settled trades total, the same number as the "Settled trades total" box above
    lab = ws.cell(row=row, column=1, value="Bot All Time Total:")
    lab.font, lab.border, lab.alignment = Font(bold=True), border, Alignment(horizontal="center")
    for i, bot in enumerate(bots):
        bot_total = sum(d.get(bot, 0.0) for d in per_day_bot.values()) + old_bot.get(bot, 0.0)
        value(row, 2 + i, bot_total).font = Font(bold=True, color=("006100" if bot_total > 0 else "9C0006" if bot_total < 0 else "000000"))
    value(row, tot_col, total_profit).font = Font(bold=True, color=("006100" if total_profit > 0 else "9C0006" if total_profit < 0 else "000000"))
    row += 1

    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 20
    from openpyxl.utils import get_column_letter
    for col in range(3, tot_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = 16
    ws.column_dimensions["E"].width = max(ws.column_dimensions["E"].width or 0, 22)
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"
    if rh_rows:
        # 2026-11-20 (owner): the Robinhood crypto desks on their own sheet (a separate account): profit by day and desk, live
        # first, then paper. Realized P&L only (sells); open positions count when they are sold.
        rs = wb.create_sheet("Robinhood")
        coins = ["BTC", "ETH", "SOL", "XRP"]
        row = 1
        for mode in ("live", "paper"):
            mine = [r for r in rh_rows if r["mode"] == mode]
            if not mine:
                continue
            for i, t in enumerate(["Date (" + mode + ")"] + [c + " (Robinhood)" for c in coins] + ["Day total", "Exits"], start=1):
                cell = rs.cell(row=row, column=i, value=t)
                cell.font, cell.fill = Font(color="FFFFFF", bold=True), head_fill
            row += 1
            days: Dict[Any, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
            exits: Dict[Any, int] = defaultdict(int)
            for r in mine:
                days[r["day"]][r["coin"]] += float(r["pnl"] or 0)
                exits[r["day"]] += int(r["exits"] or 0)
            for day in sorted(days, reverse=True):
                vals = [round(days[day].get(c, 0.0), 2) for c in coins]
                tot = round(sum(vals), 2)
                rs.cell(row=row, column=1, value=day)
                for i, v in enumerate(vals + [tot], start=2):
                    cell = rs.cell(row=row, column=i, value=v)
                    cell.number_format = money
                    fill, font = style_for(v)
                    if fill is not None and i == len(vals) + 2:
                        cell.fill, cell.font = fill, font
                rs.cell(row=row, column=len(vals) + 3, value=exits[day])
                row += 1
            row += 1
        for col in range(1, 8):
            rs.column_dimensions[get_column_letter(col)].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def export_events(pool, account: str = "") -> str:
    """The full desk log for every desk as CSV, newest last: time, desk, level and the plain-language line."""
    import csv
    import io

    await _ensure(pool)
    keys = {event_asset(a, account): a for a in dict.fromkeys(("btc", "btc15", "btclate", "eth", "sol", "xrp", *KEYS))}   # every desk, commodities too
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT ts, level, text, coalesce(asset, 'btc') AS asset FROM polybot_events "
                             "WHERE coalesce(asset, 'btc') = ANY($1::text[]) ORDER BY id", list(keys))
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["time_utc", "desk", "level", "text"])
    for r in rows:
        wr.writerow([r["ts"].isoformat(), keys.get(r["asset"], r["asset"]).upper(), r["level"], r["text"]])
    return buf.getvalue()


async def events(pool, limit: int = 30, asset: str = "btc", account: str = "") -> List[Dict[str, Any]]:
    await _ensure(pool)
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT ts, level, text FROM polybot_events WHERE coalesce(asset, 'btc') = $2 ORDER BY id DESC LIMIT $1",
                             max(1, min(limit, 200)), event_asset(asset, account))
    return [{"ts": r["ts"].isoformat(), "level": r["level"], "text": r["text"]} for r in rows]


async def summary(pool, *, trade_limit: int = 50, asset: str = "btc", account: str = "") -> Dict[str, Any]:
    asset = asset if asset in KEYS else "btc"
    await load_money(pool, account)
    status, control = await get_kv(pool, kv_key("status", asset, account)), await get_kv(pool, kv_key("control", asset, account))
    hb = float(status.get("heartbeat") or 0)
    mode = status.get("mode") or "paper"
    rows = ASSET_ROWS.get(asset, asset)                  # v1.23: the late desk's rows are BTC rows under its own paper label
    paper = label_for(LEDGER_OF.get(asset, "paper"), account)       # a subscriber's rows carry their account ("live@<tag>")
    live = label_for(LIVE_OF.get(asset, "live"), account)
    live_rows = rows
    return {"status": status, "control": control, "running": bool(hb) and time.time() - hb < 300, "heartbeat_age_s": round(time.time() - hb) if hb else None,
            "limits": current_limits(control).__dict__,
            "score": {"paper": await scoreboard(pool, paper, asset=rows, account=account), "live": await scoreboard(pool, live, asset=live_rows, account=account)},
            "trades": {"paper": await trades(pool, paper, trade_limit, asset=rows), "live": await trades(pool, live, trade_limit, asset=live_rows)},
            "events": await events(pool, 30, asset, account), "mode": mode, "strategy": STRATEGY_VERSION, "deposits": deposits(account), "referral": referral(account),
            "withdrawn": withdrawn(account),
            "shadow": await shadow_stats(pool, live if mode == "live" else paper, STRATEGY_VERSION, rows), "shadow_detail": await shadow_detail(pool, asset, account),
            "asset": asset, "name": NAMES[asset], "account": account,
            "hours": {"paper": await by_hour(pool, paper, rows), "live": await by_hour(pool, live, live_rows)},
            "analysis": await analysis(pool, live if mode == "live" else paper, asset=rows),
            # both ledgers, so the breakdown shows on whichever tab is open (paper desks too)
            "analysis_by": (an_by := {"paper": await analysis(pool, paper, asset=rows), "live": await analysis(pool, live, asset=live_rows)}),
            # 2026-11-17: the same breakdown over this version, all history, and all history under today's rules
            "analysis_scopes": {"paper": await analysis_scopes(pool, paper, rows, an_by["paper"]),
                                "live": await analysis_scopes(pool, live, live_rows, an_by["live"])}}


# 2026-11-21 (owner): cash deposited, referral deposited and withdrawn are edited on the Station (tap the line), per account:
# the owner's in polybot_kv "owner_money", each brother's in "owner_money@<tag>". The env vars (POLYBOT_DEPOSITS_USD /
# _REFERRAL_USD / _WITHDRAWN_USD) are only the owner's starting point; a brother starts at nothing until he enters his own.
MONEY_KEY = "owner_money"
MONEY_FIELDS = {"deposits": "POLYBOT_DEPOSITS_USD", "referral": "POLYBOT_REFERRAL_USD", "withdrawn": "POLYBOT_WITHDRAWN_USD"}
_MONEY: Dict[str, Dict[str, float]] = {}                 # account ("" = owner) -> field -> amount


def money_key(account: str = "") -> str:
    return kv_key(MONEY_KEY, "btc", account)


def _money(field: str, account: str = "") -> Optional[float]:
    import os
    mine = _MONEY.get(account, {})
    if field in mine:
        v = mine[field]
    elif account:
        return None                                       # a brother's amounts never fall back to the owner's env vars
    else:
        try:
            v = float((os.environ.get(MONEY_FIELDS[field]) or "").strip())
        except ValueError:
            return None
    return v if v > 0 else None


async def load_money(pool, account: str = "") -> Dict[str, Optional[float]]:
    """Refresh one account's edited amounts from the database (every reader calls this first, so all screens and files agree)."""
    saved = await get_kv(pool, money_key(account))
    mine = _MONEY.setdefault(account, {})
    for f in MONEY_FIELDS:
        if saved.get(f) is not None:
            try:
                mine[f] = float(saved[f])
            except (TypeError, ValueError):
                pass
    return {f: _money(f, account) for f in MONEY_FIELDS}


async def set_money(pool, field: str, amount: float, account: str = "") -> Dict[str, Optional[float]]:
    if field not in MONEY_FIELDS:
        raise ValueError("unknown field")
    amount = round(max(0.0, float(amount)), 2)
    saved = await get_kv(pool, money_key(account))
    saved[field] = amount
    await _ensure(pool)
    async with pool.acquire() as c:
        await c.execute("INSERT INTO polybot_kv (key, value, updated_at) VALUES ($2, $1::jsonb, now()) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", json.dumps(saved), money_key(account))
    _MONEY.setdefault(account, {})[field] = amount
    return {f: _money(f, account) for f in MONEY_FIELDS}


def deposits(account: str = "") -> Optional[float]:
    """Cash put into Polymarket; all-time = account + withdrawn - deposits - referral."""
    return _money("deposits", account)


def withdrawn(account: str = "") -> Optional[float]:
    """Money taken out of Polymarket: it counts toward all-time results."""
    return _money("withdrawn", account)


def referral(account: str = "") -> Optional[float]:
    """Referral money Polymarket credited: money in, not trading profit."""
    return _money("referral", account)


async def _ledger_pnl(pool, asset: str, now: dt.datetime, account: str = "") -> Dict[str, float]:
    """Realised live P&L for one desk from its settled windows of the current strategy version: today (since LA midnight),
    last 30 days and all time. pre_today is what earlier strategy versions settled today; it is shown separately, not in the desk
    figures, because the scoreboards only count the current version. Every figure is settled windows only (no plug)."""
    from openjarvis.server.crew.finance import la_midnight

    # the desk's own live label (btclate: live-late) and its market rows (btclate: BTC's 5-minute markets)
    lm, ar = label_for(LIVE_OF.get(asset, "live"), account), ASSET_ROWS.get(asset, asset)
    if not all(ch.isalnum() or ch in "-@_" for ch in lm):
        raise ValueError("bad ledger label")
    cur = f"mode='{lm}' AND {ASSET_SQL} = $2 AND {VER} = $3"
    old = f"mode='{lm}' AND {ASSET_SQL} = $2 AND {VER} <> $3"
    async with pool.acquire() as c:
        today = await c.fetchval(f"SELECT coalesce(sum(pnl),0) FROM polybot_windows WHERE {cur} AND settled_at >= $1",
                                 la_midnight(now), ar, STRATEGY_VERSION)
        d30 = await c.fetchval(f"SELECT coalesce(sum(pnl),0) FROM polybot_windows WHERE {cur} AND settled_at >= $1",
                               now - dt.timedelta(days=30), ar, STRATEGY_VERSION)
        all_time = await c.fetchval(f"SELECT coalesce(sum(pnl),0) FROM polybot_windows WHERE mode='{lm}' AND {ASSET_SQL} = $1 AND {VER} = $2",
                                    ar, STRATEGY_VERSION)
        pre_today = await c.fetchval(f"SELECT coalesce(sum(pnl),0) FROM polybot_windows WHERE {old} AND settled_at >= $1",
                                     la_midnight(now), ar, STRATEGY_VERSION)
        pre_all = await c.fetchval(f"SELECT coalesce(sum(pnl),0) FROM polybot_windows WHERE mode='{lm}' AND {ASSET_SQL} = $1 AND {VER} <> $2",
                                   ar, STRATEGY_VERSION)
    return {"today": round(float(today or 0), 2), "d30": round(float(d30 or 0), 2), "all": round(float(all_time or 0), 2),
            "pre_today": round(float(pre_today or 0), 2), "pre_all": round(float(pre_all or 0), 2)}


async def live_pnl_all(pool, account: str = "") -> Dict[str, Dict[str, float]]:
    """Live realised trading profit per desk (paper never counts as money), from each desk's own settled windows.
    No desk absorbs the difference from the account balance: that gap is reported separately by reconciliation()."""
    now = dt.datetime.now(dt.timezone.utc)
    await _ensure(pool)
    keys = KEYS if not account else TENANT_ASSETS
    return {a: await _ledger_pnl(pool, a, now, account) for a in keys}


async def reconciliation(pool) -> Dict[str, Any]:
    """Compare the ledger with the real Polymarket account. gap = what the account shows that the settled ledger does not explain:
    trades placed by hand on Polymarket outside the desks, open positions marked at cost, fills the ledger missed, withdrawals.
    It is shown as unreconciled, never spread across desks.
    All figures come from one status snapshot (the BTC row), so the account and day-start values are from the same moment."""
    now = dt.datetime.now(dt.timezone.utc)
    await _ensure(pool)
    per = {a: await _ledger_pnl(pool, a, now) for a in KEYS}
    st = await get_kv(pool, kv_key("status", "btc"))
    acct, start = st.get("account_value"), st.get("day_start")
    ledger_all = round(sum(v["all"] + v["pre_all"] for v in per.values()), 2)
    ledger_today = round(sum(v["today"] + v["pre_today"] for v in per.values()), 2)
    await load_money(pool)
    dep = deposits()
    out: Dict[str, Any] = {
        "account_value": None if acct is None else round(float(acct), 2),
        "as_of_day": st.get("day"),
        "deposits": dep, "referral": referral(), "withdrawn": withdrawn(),
        "ledger_all_time": ledger_all,
        "ledger_today": ledger_today,
        "account_change_today": None,
        "unreconciled_today": None,
        "account_change_all_time": None,
        "unreconciled_all_time": None,
    }
    if st.get("mode") == "live" and acct is not None:
        if start is not None and st.get("day") == la_day_str(now):
            out["account_change_today"] = round(float(acct) - float(start), 2)
            out["unreconciled_today"] = round(out["account_change_today"] - ledger_today, 2)
        if dep:
            out["account_change_all_time"] = round(float(acct) + (withdrawn() or 0) - dep - (referral() or 0), 2)
            out["unreconciled_all_time"] = round(out["account_change_all_time"] - ledger_all, 2)
    return out


async def account_all_time(pool, account: str = "") -> Optional[float]:
    """Owner 2026-11-22: the "All-time" the Station shows under the account stack: the Polymarket account value + withdrawn - cash
    deposited - referral. None when the account's amounts are not entered yet or its desk has not reported a value."""
    await load_money(pool, account)
    st = await get_kv(pool, kv_key("status", "btc", account))
    acct = st.get("account_value")
    dep, ref, wd = deposits(account), referral(account), withdrawn(account)
    if acct is None or not (dep or ref or wd):
        return None
    return round(float(acct) + (wd or 0.0) - (dep or 0.0) - (ref or 0.0), 2)


async def live_pnl(pool) -> Dict[str, float]:
    return (await live_pnl_all(pool))["btc"]


def la_day_str(now: dt.datetime) -> str:
    from openjarvis.polybot.risk import la_day
    return la_day(now.timestamp())


def status_text(s: Dict[str, Any]) -> str:
    """The desk in a few plain lines, for Helix."""
    st, lim = s["status"], s["limits"]
    lines = [f"{s.get('name') or 'Polymarket Desk'} ({str(s.get('asset') or 'btc').upper()} 5-minute Up/Down):"]
    if not st:
        lines.append("The desk has never reported in: the Render worker (python -m openjarvis.polybot run) is not running yet.")
    else:
        alive = "running" if s["running"] else f"NOT reporting (last heartbeat {s['heartbeat_age_s']}s ago; the worker may be down)"
        lines.append(f"Desk is {alive}, mode {str(st.get('mode', 'paper')).upper()}{', PAUSED' if st.get('paused') else ''}{', KILL SWITCH ON' if st.get('kill') else ''}. "
                     f"Now: {st.get('reason', '')}")
        if st.get("fair_model") is not None:
            lines.append(f"Thinking: model says Up {st['fair_model']:.2f}, market mid {st.get('market_mid')}, trades on {st.get('fair_up')}; "
                         f"momentum {st.get('momentum_z')} sd; extra edge demanded from recent results {st.get('extra_edge', 0):.2f}"
                         + (f" (last 20 windows returned {st['rolling_return'] * 100:.1f}%)" if st.get("rolling_return") is not None else "") + ".")
        w = st.get("window") or {}
        if w:
            lines.append(f"Window {w.get('slug')}: price to beat {w.get('price_to_beat')}, index {((st.get('index') or {}).get('price'))}, "
                         f"fair value of Up {st.get('fair_up')}, book {st.get('book')}, resting {st.get('resting')}.")
        if st.get("day_start") is not None:
            lines.append(f"Today started with ${st['day_start']:.2f}; today's loss limit is ${st.get('day_limit') or 0:.2f}.")
        if st.get("account_value") is not None:
            vs = st.get("version_start")
            lines.append(f"Real Polymarket account value ${st['account_value']:.2f}" + (f" ({st['account_value'] - vs:+.2f} since strategy v{st.get('strategy')} started at ${vs:.2f})" if vs is not None else "")
                         + (f"; all-time {st['account_value'] + (s.get('withdrawn') or 0) - s['deposits'] - (s.get('referral') or 0):+.2f} on ${s['deposits']:.2f} deposited"
                            + (f" + ${s['referral']:.2f} referral" if s.get("referral") else "")
                            + (f", ${s['withdrawn']:.2f} withdrawn" if s.get("withdrawn") else "") if s.get("deposits") else "")
                         + ". Trust this over the scoreboard if they differ.")
        lines.append(f"At risk now ${st.get('at_risk', 0):.2f}; today's P&L {st.get('day_pnl', 0):+.2f}"
                     + (f"; buying power ${st['balance']:.2f}" if st.get("balance") is not None else "") + ".")
        if str((s["control"] or {}).get("mode")) == "live" and st.get("mode") != "live":
            lines.append("Live was approved, but the worker is not allowed to trade live: set POLYBOT_ALLOW_LIVE=true and the Polymarket API keys on the worker.")
    for mode in ("live", "paper"):
        sc = s["score"][mode]
        a, d7, t = sc["all"], sc["d7"], sc["today"]
        for o in sc.get("older") or []:
            lines.append(f"{mode.upper()} earlier strategy v{o['version']}: {o['trades']} windows, {o['wins']} won, profit {o['profit']:+.2f} (not in the scoreboard below).")
        if a["trades"]:
            lines.append(f"{mode.upper()} scoreboard (strategy v{sc.get('version')}): {a['trades']} windows traded, win rate {a['win_rate']}%, total profit {a['profit']:+.2f} "
                         f"on ${a['staked']:.2f} staked ({a['return_pct']}%), best {a['best']:+.2f}, worst {a['worst']:+.2f}; last 7 days {d7['profit']:+.2f} "
                         f"({d7['trades']} windows); today {t['profit']:+.2f} ({t['trades']}). Fills: {sc['fills']}.")
        else:
            lines.append(f"{mode.upper()} scoreboard: no settled trades yet.")
    lines.append(f"Limits: bankroll ${lim['bankroll_usd']:.2f}, per window ${lim['max_window_usd']:.2f}, per order ${lim['max_order_usd']:.2f}, "
                 f"daily stop {_stop_text(lim)}, min edge {lim['min_edge']:.2f}.")
    if s.get("analysis"):
        lines.append(analysis_text(s["analysis"]))
    recent = [e["text"] for e in s["events"][:6]]
    if recent:
        lines.append("Latest log: " + " | ".join(recent))
    return "\n".join(lines)


def requested_limits(control: Dict[str, Any], changes: Dict[str, Any]) -> Limits:
    cur = current_limits(control).__dict__.copy()
    for k in LIMIT_FIELDS:
        if changes.get(k) is not None:
            try:
                cur[k] = float(changes[k])
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number.")
    return Limits(**cur).clean()


async def control(pool, action: str, limits: Optional[Dict[str, Any]] = None, asset: str = "btc", account: str = "") -> Dict[str, Any]:
    """Changes that need no Approve. Returns {"done": text} or {"needs_approval": text, "action": {...}} for the caller to turn
    into an Approve card. Never sets live on its own."""
    asset = asset if asset in KEYS else "btc"
    name = NAMES[asset]
    ctl = await get_kv(pool, kv_key("control", asset, account))
    action = str(action or "").strip().lower()
    if action == "pause":
        ctl["paused"] = True
        await put_control(pool, ctl, asset, account)
        return {"done": f"{name} paused: it pulls its quotes within a few seconds and holds what it already owns to settlement."}
    if action == "resume":
        ctl["paused"], ctl["kill"] = False, False
        await put_control(pool, ctl, asset, account)
        return {"done": f"{name} resumed."}
    if action in ("flatten", "kill", "stop"):
        ctl["kill"] = True
        await put_control(pool, ctl, asset, account)
        return {"done": f"{name}: kill switch on: every resting order is cancelled within a few seconds. Contracts already bought settle on their own. Say resume to restart."}
    if action == "reset_day":                     # the daily stop counts from the account value now (all desks share one account)
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        for a in (KEYS if not account else TENANT_ASSETS):
            c = ctl if a == asset else await get_kv(pool, kv_key("control", a, account))
            c["day_reset_at"] = stamp
            await put_control(pool, c, a, account)
        return {"done": "Daily stop reset on every desk: today's loss limit now counts from the current account value. Trading resumes within about 20 seconds."}
    if action == "paper":
        ctl["mode"] = "paper"
        await put_control(pool, ctl, asset, account)
        return {"done": f"{name} switched to PAPER: no more real orders (resting live orders are cancelled)."}
    if action == "go_live":
        lim = current_limits(ctl)
        return {"needs_approval": (f"Switch the {name} to LIVE trading with real money: bankroll ${lim.bankroll_usd:.2f}, at most "
                                   f"${lim.max_window_usd:.2f} per 5-minute window, and it stops for the day after losing {_stop_text(lim.__dict__)}."),
                "action": {"type": "desk_control", "asset": asset, "set": {"mode": "live"}}}
    if action == "set_limits":
        new = requested_limits(ctl, limits or {})
        if new.tighter_than(current_limits(ctl)):
            ctl["limits"] = new.__dict__
            await put_control(pool, ctl, asset, account)
            return {"done": "Limits tightened: " + _lim_text(new)}
        return {"needs_approval": f"Loosen the {name}'s limits to: " + _lim_text(new) + ".",
                "action": {"type": "desk_control", "asset": asset, "set": {"limits": new.__dict__}}}
    raise ValueError("Unknown desk action. Use pause, resume, flatten, paper, go_live or set_limits.")


def hour_size_text(hs: Dict[str, Any]) -> str:
    parts = []
    for h, f in sorted(((int(k), float(v)) for k, v in (hs or {}).items()), key=lambda x: x[0]):
        when = f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}"
        parts.append(f"{when} windows " + ("skipped" if f <= 0 else "at full size" if f >= 1 else f"at {f:.0%} size"))
    return ", ".join(parts) or "every hour at full size"


def _lim_text(l: Limits) -> str:
    return (f"bankroll ${l.bankroll_usd:.2f}, per window ${l.max_window_usd:.2f}, per order ${l.max_order_usd:.2f}, "
            f"daily stop {_stop_text(l.__dict__)}, min edge {l.min_edge:.2f}")


def _stop_text(lim: Dict[str, Any]) -> str:
    pct = float(lim.get("daily_loss_pct") or 0)
    return f"{pct:g}% of the day's starting balance" if pct > 0 else f"${float(lim.get('daily_loss_usd') or 0):.2f}"


async def apply_approved(pool, setting: Dict[str, Any], asset: str = "btc", account: str = "") -> str:
    """Runs only from an Approve tap (engine._answer_ops): the owner's for his desks, the account holder's for their own."""
    asset = asset if asset in KEYS else "btc"
    ctl = await get_kv(pool, kv_key("control", asset, account))
    if setting.get("mode") == "live":
        ctl["mode"], ctl["kill"], ctl["paused"] = "live", False, False
        ctl["live_approved_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        await put_control(pool, ctl, asset, account)
        return (f"Approved: the {NAMES[asset]} goes LIVE within about 20 seconds, if the worker has POLYBOT_ALLOW_LIVE=true and its API keys. "
                "Every entry and settlement is pushed to your phone.")
    if isinstance(setting.get("hour_size"), dict):          # from the daily 7 AM review: full size, half size or skip the hour
        from openjarvis.polybot.config import parse_hour_size

        hs = parse_hour_size(setting["hour_size"])
        ctl["hour_size"] = {str(h): f for h, f in hs}
        for h in setting["hour_size"]:                       # an hour set back to 1 (full size) stays in the row as 1.0
            if str(h) not in ctl["hour_size"]:
                ctl["hour_size"][str(h)] = 1.0
        await put_control(pool, ctl, asset, account)
        rev = await get_kv(pool, kv_key("hour_review", asset, account))         # the daily review measures the new setting from now
        rev["since"] = dt.datetime.now(dt.timezone.utc).isoformat()
        async with pool.acquire() as c:
            await c.execute("INSERT INTO polybot_kv (key, value, updated_at) VALUES ($2, $1::jsonb, now()) "
                            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", json.dumps(rev, default=str),
                            kv_key("hour_review", asset, account))
        return "Approved: " + hour_size_text(ctl["hour_size"]) + f" on the {NAMES[asset]}."
    if isinstance(setting.get("limits"), dict):
        new = Limits(**{k: float(setting["limits"][k]) for k in LIMIT_FIELDS if k in setting["limits"]}).clean()
        ctl["limits"] = new.__dict__
        await put_control(pool, ctl, asset, account)
        return "Approved: limits are now " + _lim_text(new) + "."
    raise ValueError("Nothing to apply.")
