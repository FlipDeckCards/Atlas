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
import time
from typing import Any, Dict, List, Optional

from openjarvis.polybot.brain import STRATEGY_VERSION
from openjarvis.polybot.config import Limits, from_env
from openjarvis.polybot.state import DDL

KEY = "polymarket"
BUSINESS = {"key": KEY, "name": "Polymarket Desk", "url": "https://polymarket.us", "color": "#00e5ff",
            "description": "Polymarket Desk is a trading bot that makes markets in Polymarket US's 5-minute BTC Up/Down contracts."}
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


async def put_control(pool, control: Dict[str, Any]) -> None:
    await _ensure(pool)
    async with pool.acquire() as c:
        await c.execute("INSERT INTO polybot_kv (key, value, updated_at) VALUES ('control', $1::jsonb, now()) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", json.dumps(control))


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


async def scoreboard(pool, mode: str, now: Optional[dt.datetime] = None, version: str = STRATEGY_VERSION) -> Dict[str, Any]:
    """Win rate and profit for one mode (paper or live), over all time, the last 7 days and today (Los Angeles)."""
    await _ensure(pool)
    from openjarvis.server.crew.finance import la_midnight

    now = now or dt.datetime.now(dt.timezone.utc)
    today, week = la_midnight(now), now - dt.timedelta(days=7)
    q = ("SELECT count(*) AS n, count(*) FILTER (WHERE pnl > 0) AS wins, count(*) FILTER (WHERE pnl < 0) AS losses, "
         "coalesce(sum(pnl), 0) AS pnl, coalesce(sum(up_cost + down_cost), 0) AS staked, coalesce(max(pnl), 0) AS best, "
         "coalesce(min(pnl), 0) AS worst FROM polybot_windows WHERE mode = $1 AND settled_at IS NOT NULL AND settled_at >= $2 "
         f"AND {VER} = $3")
    out: Dict[str, Any] = {"mode": mode, "version": version}
    async with pool.acquire() as c:
        for name, since in (("all", dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)), ("d7", week), ("today", today)):
            r = await c.fetchrow(q, mode, since, version)
            n, wins = int(r["n"]), int(r["wins"])
            staked = float(r["staked"])
            out[name] = {"trades": n, "wins": wins, "losses": int(r["losses"]), "win_rate": round(100.0 * wins / n, 1) if n else None,
                         "profit": round(float(r["pnl"]), 2), "staked": round(staked, 2),
                         "return_pct": round(100.0 * float(r["pnl"]) / staked, 1) if staked else None,
                         "best": round(float(r["best"]), 2), "worst": round(float(r["worst"]), 2)}
        fills = await c.fetchval(f"SELECT count(*) FROM polybot_fills WHERE mode = $1 AND {VER} = $2", mode, version)
        # the running total for a small chart: cumulative profit after each settled window (last 200)
        rows = await c.fetch(f"SELECT settled_at, pnl FROM polybot_windows WHERE mode = $1 AND settled_at IS NOT NULL AND {VER} = $2 "
                             "ORDER BY settled_at DESC LIMIT 200", mode, version)
        older = await c.fetch(f"SELECT {VER} AS v, count(*) AS n, count(*) FILTER (WHERE pnl > 0) AS wins, coalesce(sum(pnl),0) AS pnl "
                              f"FROM polybot_windows WHERE mode = $1 AND settled_at IS NOT NULL AND {VER} <> $2 GROUP BY 1 ORDER BY 1", mode, version)
    run, curve = 0.0, []
    for r in reversed(rows):
        run += float(r["pnl"] or 0)
        curve.append([r["settled_at"].isoformat(), round(run, 2)])
    out["fills"] = int(fills or 0)
    out["curve"] = curve
    out["older"] = [{"version": r["v"], "trades": int(r["n"]), "wins": int(r["wins"]), "profit": round(float(r["pnl"]), 2)} for r in older]
    return out


async def trades(pool, mode: str, limit: int = 50, version: str = STRATEGY_VERSION) -> List[Dict[str, Any]]:
    """The trade log: one row per window traded, with every fill in it (time, side, price, size, cost, fair value at the time)."""
    await _ensure(pool)
    async with pool.acquire() as c:
        ws = await c.fetch(f"SELECT * FROM polybot_windows WHERE mode = $1 AND {VER} = $3 ORDER BY window_end DESC LIMIT $2", mode, max(1, min(limit, 500)), version)
        slugs = [w["slug"] for w in ws]
        fs = await c.fetch("SELECT * FROM polybot_fills WHERE mode = $1 AND slug = ANY($2::text[]) ORDER BY ts", mode, slugs) if slugs else []
    by: Dict[str, List[Dict[str, Any]]] = {}
    for f in fs:
        by.setdefault(f["slug"], []).append({"ts": f["ts"].isoformat(), "side": f["side"], "price": f["price"], "qty": f["qty"],
                                             "cost": round(f["cost"], 2), "fair": round(f["fair"], 3) if f["fair"] is not None else None})
    out = []
    for w in ws:
        out.append({"slug": w["slug"], "window_start": w["window_start"].isoformat() if w["window_start"] else None,
                    "window_end": w["window_end"].isoformat() if w["window_end"] else None, "price_to_beat": w["price_to_beat"],
                    "close_price": w["close_price"], "result": None if w["up"] is None else ("UP" if w["up"] else "DOWN"),
                    "up_qty": w["up_qty"], "up_cost": w["up_cost"], "down_qty": w["down_qty"], "down_cost": w["down_cost"],
                    "paid": round((w["up_cost"] or 0) + (w["down_cost"] or 0), 2), "pnl": w["pnl"], "fills": by.get(w["slug"], [])})
    return out


async def events(pool, limit: int = 30) -> List[Dict[str, Any]]:
    await _ensure(pool)
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT ts, level, text FROM polybot_events ORDER BY id DESC LIMIT $1", max(1, min(limit, 200)))
    return [{"ts": r["ts"].isoformat(), "level": r["level"], "text": r["text"]} for r in rows]


async def summary(pool, *, trade_limit: int = 50) -> Dict[str, Any]:
    status, control = await get_kv(pool, "status"), await get_kv(pool, "control")
    hb = float(status.get("heartbeat") or 0)
    mode = status.get("mode") or "paper"
    return {"status": status, "control": control, "running": bool(hb) and time.time() - hb < 300, "heartbeat_age_s": round(time.time() - hb) if hb else None,
            "limits": current_limits(control).__dict__,
            "score": {"paper": await scoreboard(pool, "paper"), "live": await scoreboard(pool, "live")},
            "trades": {"paper": await trades(pool, "paper", trade_limit), "live": await trades(pool, "live", trade_limit)},
            "events": await events(pool, 30), "mode": mode, "strategy": STRATEGY_VERSION}


async def live_pnl(pool) -> Dict[str, float]:
    """Live trading profit for the Station's P&L (paper results never count as money)."""
    from openjarvis.server.crew.finance import la_midnight

    now = dt.datetime.now(dt.timezone.utc)
    await _ensure(pool)
    async with pool.acquire() as c:
        today = await c.fetchval("SELECT coalesce(sum(pnl),0) FROM polybot_windows WHERE mode='live' AND settled_at >= $1", la_midnight(now))
        d30 = await c.fetchval("SELECT coalesce(sum(pnl),0) FROM polybot_windows WHERE mode='live' AND settled_at >= $1", now - dt.timedelta(days=30))
    return {"today": round(float(today or 0), 2), "d30": round(float(d30 or 0), 2)}


def status_text(s: Dict[str, Any]) -> str:
    """The desk in a few plain lines, for Helix."""
    st, lim = s["status"], s["limits"]
    lines = []
    if not st:
        lines.append("The desk has never reported in: the Render worker (python -m openjarvis.polybot run) is not running yet.")
    else:
        alive = "running" if s["running"] else f"NOT reporting (last heartbeat {s['heartbeat_age_s']}s ago; the worker may be down)"
        lines.append(f"Desk is {alive}, mode {str(st.get('mode', 'paper')).upper()}{', PAUSED' if st.get('paused') else ''}{', KILL SWITCH ON' if st.get('kill') else ''}. "
                     f"Now: {st.get('reason', '')}")
        if st.get("fair_model") is not None:
            lines.append(f"Thinking: model says Up {st['fair_model']:.2f}, market mid {st.get('market_mid')}, trades on {st.get('fair_up')}; "
                         f"BTC momentum {st.get('momentum_z')} sd; extra edge demanded from recent results {st.get('extra_edge', 0):.2f}"
                         + (f" (last 20 windows returned {st['rolling_return'] * 100:.1f}%)" if st.get("rolling_return") is not None else "") + ".")
        w = st.get("window") or {}
        if w:
            lines.append(f"Window {w.get('slug')}: price to beat {w.get('price_to_beat')}, BTC index {((st.get('index') or {}).get('price'))}, "
                         f"fair value of Up {st.get('fair_up')}, book {st.get('book')}, resting {st.get('resting')}.")
        if st.get("day_start") is not None:
            lines.append(f"Today started with ${st['day_start']:.2f}; today's loss limit is ${st.get('day_limit') or 0:.2f}.")
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


async def control(pool, action: str, limits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Changes that need no Approve. Returns {"done": text} or {"needs_approval": text, "action": {...}} for the caller to turn
    into an Approve card. Never sets live on its own."""
    ctl = await get_kv(pool, "control")
    action = str(action or "").strip().lower()
    if action == "pause":
        ctl["paused"] = True
        await put_control(pool, ctl)
        return {"done": "Desk paused: it pulls its quotes within about 20 seconds and holds what it already owns to settlement."}
    if action == "resume":
        ctl["paused"], ctl["kill"] = False, False
        await put_control(pool, ctl)
        return {"done": "Desk resumed."}
    if action in ("flatten", "kill", "stop"):
        ctl["kill"] = True
        await put_control(pool, ctl)
        return {"done": "Kill switch on: every resting order is cancelled within about 20 seconds. Contracts already bought settle on their own. Say resume to restart."}
    if action == "paper":
        ctl["mode"] = "paper"
        await put_control(pool, ctl)
        return {"done": "Desk switched to PAPER: no more real orders (resting live orders are cancelled)."}
    if action == "go_live":
        lim = current_limits(ctl)
        return {"needs_approval": (f"Switch the Polymarket Desk to LIVE trading with real money: bankroll ${lim.bankroll_usd:.2f}, at most "
                                   f"${lim.max_window_usd:.2f} per 5-minute window, and it stops for the day after losing {_stop_text(lim.__dict__)}."),
                "action": {"type": "desk_control", "set": {"mode": "live"}}}
    if action == "set_limits":
        new = requested_limits(ctl, limits or {})
        if new.tighter_than(current_limits(ctl)):
            ctl["limits"] = new.__dict__
            await put_control(pool, ctl)
            return {"done": "Limits tightened: " + _lim_text(new)}
        return {"needs_approval": "Loosen the Polymarket Desk's limits to: " + _lim_text(new) + ".",
                "action": {"type": "desk_control", "set": {"limits": new.__dict__}}}
    raise ValueError("Unknown desk action. Use pause, resume, flatten, paper, go_live or set_limits.")


def _lim_text(l: Limits) -> str:
    return (f"bankroll ${l.bankroll_usd:.2f}, per window ${l.max_window_usd:.2f}, per order ${l.max_order_usd:.2f}, "
            f"daily stop {_stop_text(l.__dict__)}, min edge {l.min_edge:.2f}")


def _stop_text(lim: Dict[str, Any]) -> str:
    pct = float(lim.get("daily_loss_pct") or 0)
    return f"{pct:g}% of the day's starting balance" if pct > 0 else f"${float(lim.get('daily_loss_usd') or 0):.2f}"


async def apply_approved(pool, setting: Dict[str, Any]) -> str:
    """Runs only from the owner's Approve tap (engine._answer_ops)."""
    ctl = await get_kv(pool, "control")
    if setting.get("mode") == "live":
        ctl["mode"], ctl["kill"], ctl["paused"] = "live", False, False
        ctl["live_approved_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        await put_control(pool, ctl)
        return ("Approved: the desk goes LIVE within about 20 seconds, if the worker has POLYBOT_ALLOW_LIVE=true and its API keys. "
                "Every entry and settlement is pushed to your phone.")
    if isinstance(setting.get("limits"), dict):
        new = Limits(**{k: float(setting["limits"][k]) for k in LIMIT_FIELDS if k in setting["limits"]}).clean()
        ctl["limits"] = new.__dict__
        await put_control(pool, ctl)
        return "Approved: limits are now " + _lim_text(new) + "."
    raise ValueError("Nothing to apply.")
