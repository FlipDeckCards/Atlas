"""The Polymarket Desk business brief: one Word document describing the whole operation, built fresh on every download.

The narrative (what the business is, how the bots think, why the rules exist) is written here. Every number in the tables (sizing
steps, hours, caps, which desks are live, results, family accounts) is read at download time from the same settings and ledger the
desks themselves use, so the brief never goes out of date. Owner only.
"""

from __future__ import annotations

import datetime as dt
import io
import os
from typing import Any, Dict, List, Optional

ACCENT = "0B6E8A"
GREEN, RED = "1B7F3B", "B42318"


def _hour(h: int) -> str:
    return f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}"


def _money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return ("-" if v < 0 else "") + f"${abs(v):,.2f}"


def _signed(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return ("+" if v >= 0 else "-") + f"${abs(v):,.2f}"


def _steps(tiers) -> str:
    return ", ".join(f"{f}%+ → {lot:g}%" for f, lot in sorted(tiers or (), key=lambda t: t[0]))


def _hours_text(hs) -> str:
    half = [_hour(h) for h, f in hs if 0 < f < 1]
    skip = [_hour(h) for h, f in hs if f <= 0]
    parts = []
    if skip:
        parts.append("skip " + ", ".join(skip))
    if half:
        parts.append("half size " + ", ".join(half))
    return "; ".join(parts) + ("; every other hour full size" if parts else "full size every hour")


async def _desk_facts(pool, account: str = "") -> List[Dict[str, Any]]:
    """For each desk: its live settings (as the running desk would load them), its mode and its results."""
    from openjarvis.polybot import directional
    from openjarvis.polybot.config import ASSETS, apply_control, desk_label, from_env
    from openjarvis.polybot.state import kv_key
    from openjarvis.server.crew import desk

    now = dt.datetime.now(dt.timezone.utc)
    async with pool.acquire() as c:
        rows = await c.fetch(
            f"SELECT mode, {desk.ASSET_SQL} AS asset, count(*) AS n, count(*) FILTER (WHERE pnl > 0) AS wins, "
            f"coalesce(sum(pnl), 0) AS pnl, coalesce(sum(coalesce(up_cost, 0) + coalesce(down_cost, 0)), 0) AS staked, "
            f"count(*) FILTER (WHERE settled_at >= $1) AS n7, coalesce(sum(pnl) FILTER (WHERE settled_at >= $1), 0) AS pnl7 "
            f"FROM polybot_windows WHERE settled_at IS NOT NULL GROUP BY 1, 2", now - dt.timedelta(days=7))
    res = {(r["mode"], r["asset"]): r for r in rows}
    out = []
    for a in ASSETS:
        ctl = await desk.get_kv(pool, kv_key("control", a, account))
        st = await desk.get_kv(pool, kv_key("status", a, account))
        try:
            cfg = apply_control(from_env(os.environ, a), ctl)
        except Exception:
            cfg = from_env(os.environ, a)
        mode = str(ctl.get("mode") or "paper").lower()
        methods = [m for m in cfg.methods if m == "pairs" or cfg.directional] + (["sweep"] if cfg.sweep_pct > 0 and not cfg.pairs_only else [])
        tiers = directional._tiers(a) or ()
        calibrated = "raw" if a in directional.RAW_HOURS else "calibrated"

        def r(label: str) -> Dict[str, Any]:
            x = res.get((label, a))
            if not x:
                return {"n": 0, "wins": 0, "pnl": 0.0, "staked": 0.0, "n7": 0, "pnl7": 0.0}
            return {"n": int(x["n"]), "wins": int(x["wins"]), "pnl": float(x["pnl"]), "staked": float(x["staked"]),
                    "n7": int(x["n7"]), "pnl7": float(x["pnl7"])}

        from openjarvis.polybot.config import label_for
        live = r(label_for(desk.LIVE_OF.get(a, "live"), account))
        paper = r(label_for(desk.LEDGER_OF.get(a, "paper"), account))
        out.append({
            "asset": a, "label": desk_label(a), "coin": cfg.coin.upper(), "horizon": cfg.horizon, "mode": mode,
            "running": bool(st.get("heartbeat")) and not st.get("stopped"),
            "floor": min((f for f, _ in tiers), default=None), "tiers": tiers, "calibration": calibrated,
            "raw_hours": sorted(directional.RAW_HOURS.get(a, ())),
            "window_total": directional.window_total_pct(a), "pair_pct": cfg.window_pct,
            "sweep_pct": cfg.sweep_pct if "sweep" in methods else 0.0, "methods": methods,
            "hour_size": tuple(cfg.hour_size), "daily_loss_pct": cfg.limits.daily_loss_pct,
            "directional_secs": (cfg.directional_min_secs, cfg.directional_secs_left), "late_split": cfg.late_split_s,
            "sweep_secs": cfg.sweep_secs_left, "live": live, "paper": paper,
            "balance": st.get("account_value") if st.get("account_value") is not None else st.get("balance"),
        })
    return out


async def _family(engine, pool) -> List[Dict[str, Any]]:
    from openjarvis.server.crew import desk, tenant_desks

    out = []
    for uid in await tenant_desks.uids_with_desks(pool):
        name = uid
        if getattr(engine, "name_fn", None):
            try:
                name = await engine.name_fn(uid) or uid
            except Exception:
                pass
        tag = tenant_desks.account_tag(uid)
        try:
            pnl = await desk.live_pnl_all(pool, tag)
        except Exception:
            pnl = {}
        modes = {}
        from openjarvis.polybot.state import kv_key
        for a in desk.TENANT_ASSETS:
            modes[a] = str((await desk.get_kv(pool, kv_key("control", a, tag))).get("mode") or "paper")
        out.append({"name": name, "pnl": pnl, "modes": modes})
    return out


# ---------------------------------------------------------------------------------------------------------------- the document

def _doc():
    from docx import Document
    from docx.shared import Pt, RGBColor

    d = Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    for name, size in (("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 11.5)):
        s = d.styles[name]
        s.font.name, s.font.size, s.font.color.rgb = "Calibri", Pt(size), RGBColor.from_string(ACCENT)
    for sec in d.sections:
        from docx.shared import Inches
        sec.left_margin = sec.right_margin = Inches(0.8)
        sec.top_margin = sec.bottom_margin = Inches(0.7)
    return d


def _shade(cell, hex_fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def _table(d, header: List[str], rows: List[List[Any]], colors: Optional[Dict[tuple, str]] = None, size: float = 9.0):
    from docx.shared import Pt, RGBColor

    t = d.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    for i, h in enumerate(header):
        cell = t.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(str(h))
        run.bold, run.font.size = True, Pt(size)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        _shade(cell, ACCENT)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for ci, v in enumerate(row):
            cells[ci].text = ""
            run = cells[ci].paragraphs[0].add_run("" if v is None else str(v))
            run.font.size = Pt(size)
            col = (colors or {}).get((ri, ci))
            if col:
                run.font.color.rgb = RGBColor.from_string(col)
        if ri % 2 == 1:
            for c in cells:
                _shade(c, "EEF5F8")
    d.add_paragraph()
    return t


def _bullets(d, items: List[str]) -> None:
    for it in items:
        p = d.add_paragraph(style="List Bullet")
        if ": " in it and it.index(": ") < 60:
            head, tail = it.split(": ", 1)
            p.add_run(head + ": ").bold = True
            p.add_run(tail)
        else:
            p.add_run(it)


def _para(d, text: str) -> None:
    d.add_paragraph(text)


def _pnl_color(v: float) -> Optional[str]:
    return GREEN if v > 0 else RED if v < 0 else None


def build_doc(facts: List[Dict[str, Any]], family: List[Dict[str, Any]], now: dt.datetime) -> bytes:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    from openjarvis.polybot import directional, exposure
    from openjarvis.polybot.brain import STRATEGY_VERSION
    from openjarvis.polybot.config import CAUTION_HOURS_5M, CAUTION_MIN_CONF, CAUTION_MIN_PRICE, HOUR_SIZE_5M
    from openjarvis.polybot.desk import CORRECTION_STRENGTH
    from openjarvis.server.crew.finance import LA

    la = now.astimezone(LA)
    live = [f for f in facts if f["mode"] == "live"]
    paper = [f for f in facts if f["mode"] != "live"]
    live_all = sum(f["live"]["pnl"] for f in facts)
    live_7 = sum(f["live"]["pnl7"] for f in facts)
    live_n = sum(f["live"]["n"] for f in facts)
    live_w = sum(f["live"]["wins"] for f in facts)
    bal = next((f["balance"] for f in facts if f["asset"] == "btc" and f["balance"] is not None), None)
    d = _doc()

    # ---- cover
    t = d.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.LEFT
    r = t.add_run("Aibussol Polymarket Desks")
    r.bold, r.font.size, r.font.color.rgb = True, Pt(24), RGBColor.from_string(ACCENT)
    s = d.add_paragraph()
    r = s.add_run("Business brief: the trading operation as a whole and desk by desk")
    r.font.size = Pt(13)
    m = d.add_paragraph()
    r = m.add_run(f"Generated {la.strftime('%A, %B %d, %Y at %I:%M %p').replace(' 0', ' ')} Pacific · strategy version {STRATEGY_VERSION} · "
                  "every table is read from the live settings and ledger at download time")
    r.italic, r.font.size = True, Pt(9)

    # ---- 1. executive summary
    d.add_heading("1. Executive summary", level=1)
    _para(d, "Aibussol runs a fleet of automated trading bots (\"desks\") on Polymarket US crypto Up/Down markets. Each market asks one "
             "question: will the coin's price be higher at the end of a short window (5 minutes, 15 minutes or 1 hour) than at its start? "
             "Each side pays $1 if it is right and nothing if it is wrong, so its price is the market's guess at its odds. The desks "
             "earn money by buying a side only when their own price model says it is more likely to win than its price implies, by "
             "sizing every bet to how confident the model is, and by refusing to trade in conditions their record shows are unprofitable.")
    _bullets(d, [
        f"Fleet: {len(facts)} desks covering BTC, ETH, SOL and XRP on 5-minute, 15-minute and 1-hour windows. "
        f"{len(live)} trade real money right now ({', '.join(f['label'] for f in live) or 'none'}); the other {len(paper)} trade in paper "
        "(simulated) to build a record before they are trusted with money.",
        f"Live results: {live_n} settled live windows, {live_w} winners ({(100.0 * live_w / live_n) if live_n else 0:.1f}%), "
        f"{_signed(live_all)} all time and {_signed(live_7)} in the last 7 days.",
        f"Account: {_money(bal)} on the BTC 5m desk's latest reading." if bal is not None else "Account: balance not reported yet.",
        f"Risk ceiling: never more than {exposure.CROSS_DESK_PCT:.0f}% of the account at risk at once across every desk, a daily stop "
        "for the whole account, and per-window limits on each desk.",
        f"Family accounts: {len(family)} brother account(s) run their own isolated copy of BTC 5m on their own "
        "Polymarket keys; you can view them read-only from ACCOUNTS.",
    ])

    # ---- 2. the business model
    d.add_heading("2. The business model", level=1)
    d.add_heading("2.1 The markets", level=2)
    _para(d, "Polymarket US lists a new Up/Down market for each coin every 5 minutes, every 15 minutes and every hour. The opening price "
             "(\"price to beat\") and the closing price are each the average of the settlement index over the final 60 seconds of their "
             "minute. If the close is at or above the open, Up pays $1; otherwise Down pays $1. A side bought at 62¢ makes 38¢ per "
             "contract if it wins and loses 62¢ if it does not, so it must win more than 62% of the time to make money. Every desk "
             "decision comes back to that one comparison: the model's odds against the price.")
    d.add_heading("2.2 How the desks make money", level=2)
    _bullets(d, [
        "Price model: each desk computes a fair probability for Up from the live index: how far the price is from the price to beat, "
        "how much time is left, and how much the coin is moving (volatility measured from one-minute moves over the last hour, "
        "widened for sudden jumps). The less time left and the further the price is from the line, the more certain the outcome.",
        "Edge: a bet is only worth placing when the model's probability beats the price plus Polymarket's taker fee. The desks "
        "demand a margin (at least 4¢ of edge for edge bets, 3¢ for sweeps), not just a win rate above the price.",
        "Confidence sizing: bets are bigger when the model is more certain, through fixed sizing steps per coin (section 4).",
        "Discipline from data: every trade is recorded with the conditions it happened in (hour, price, time left, confidence). "
        "Hours, prices and methods that lose over enough trades are cut back or switched off.",
    ])
    d.add_heading("2.3 Raw and calibrated confidence", level=2)
    _para(d, "The model's number (raw confidence) is not trusted blindly. Each desk keeps a tally of how often each confidence level "
             "has actually won in live trading. Calibrated confidence moves the raw number toward that record (a level with few trades "
             f"counts for little). Every desk trades on calibrated confidence at every hour. The correction is applied at "
             f"{CORRECTION_STRENGTH:.0%} strength (owner, 2026-11-11; it was 50%, briefly 100% and 75%), and it only ever lowers confidence: a level that has "
             "won more than the model said is left at the model's number, so a short lucky streak can never lift a bet over the floor. "
             "On BTC 5m the live record shows the model overstating by about 10-12 points above 80% (84% said, 74% won; 94% said, "
             "83% won), so the 80% calibrated floor needs a raw reading of roughly 88-90% or more.")

    # ---- 3. the methods
    d.add_heading("3. How a desk bets: the methods", level=1)
    _para(d, "Each desk can run up to five entry methods. Each method may place at most one bet per window, and no method may bet "
             "against a side already bet in that window, so a desk never ends up paying for both sides of a directional call.")
    _table(d, ["Method", "When (5-minute desks)", "What it does"], [
        ["Pairs", "Through the window", "Buys Up and Down together when both cost under $1 combined: the difference is locked profit whichever way the price goes. Sized by the per-window pair limit."],
        ["Directional", "From 4 minutes to 30 s before the close", "Buys the side the model favours when its confidence clears the coin's floor and it has enough edge over the price after fees."],
        ["Edge", "From 4 minutes to 60 s before the close", "Extra trigger: buys a side the model prices at least 4¢ above its ask, with the same floor and sizing steps."],
        ["Late", "From 60 s to 30 s before the close", "Extra trigger: buys a side the model is at least 90% sure of, costing 95¢ or less."],
        ["Sweep", "The final 60 s (90 s overnight)", "Buys the likely winner at 75-97¢ when the model beats the price by at least 3¢ after fees, the market leans the same way and the index is clearly on that side of the line."],
    ])
    _para(d, "The 1-hour desks use 1-hour timing: directional and edge bets from 15 minutes to 5 minutes before the close, late bets "
             "from 5 to 2 minutes out, sweeps from 5 minutes out. The 15-minute desks use the 5-minute timing.")
    d.add_heading("3.1 Entry filters on every method", level=2)
    _bullets(d, [
        "Last-minute block: on every 5-minute desk, no bet in the final 60 seconds when the side costs between 30¢ and 70¢ "
        "(a coin-flip price that late has historically lost).",
        "Price limits: the desks never buy a side under 15¢ or over 80¢ outside sweeps. ETH 5m skips buys under 85¢ and SOL 5m "
        "skips buys under 75¢ (owner, 2026-11-10).",
        f"Caution hours (owner, 2026-11-11): the hours that used to be skipped trade at half size, and a bet in one needs at least "
        f"{CAUTION_MIN_CONF:.0%} calibrated confidence and a buy price of at least {CAUTION_MIN_PRICE * 100:.0f}¢ (section 4.3).",
        "Cushion and momentum: a directional bet also needs the index to be far enough on its side of the line for the time left, "
        "and recent movement not running hard against it.",
    ])

    # ---- 4. sizing and risk
    d.add_heading("4. Sizing and risk controls", level=1)
    d.add_heading("4.1 Sizing steps by coin", level=2)
    cal = directional.CAL_TIERS
    rows = []
    for coin in ("btc", "eth", "sol", "xrp"):
        tiers = directional._tiers(coin)
        rows.append([coin.upper(), f"{min(f for f, _ in tiers)}%", _steps(tiers),
                     _steps(cal) if coin == "btc" else "— (uses the steps at left on calibrated confidence)"])
    _table(d, ["Coin", "Lowest confidence traded", "Bet size (% of balance) by confidence", "Calibrated-hour steps"], rows)
    _para(d, "Confidence is rounded down (84.6% counts as 84%). The 15-minute and 1-hour desks use their coin's steps. In a half-size "
             "hour every bet is halved; in a caution hour bets are halved and must also clear the caution floors; in a skipped hour "
             "(only if the daily review switches one off) the desk does not trade.")
    d.add_heading("4.2 Limits on how much is at risk", level=2)
    btc = next((f for f in facts if f["asset"] == "btc"), facts[0])
    small = next((f for f in facts if f["asset"] == "xrp"), btc)
    _table(d, ["Limit", "Setting", "What it means"], [
        ["All-desk cap", f"{exposure.CROSS_DESK_PCT:.0f}% of the account", "Everything open at once, across every live desk and every method including sweeps. Bets that would go over wait until earlier ones settle. Paper desks keep their own tally."],
        ["Per-window total, BTC", f"{btc['window_total']:.0f}%", "All directional, edge and late bets in one BTC window together."],
        ["Per-window total, ETH/SOL/XRP", f"{small['window_total']:.0f}%", "All directional, edge and late bets in one window on these coins together."],
        ["Sweeps, ETH/SOL/XRP", f"{small['sweep_pct'] or 1:.0f}% of the balance", "The closing sweep's own limit on these coins."],
        ["Daily stop", f"{btc['daily_loss_pct']:.0f}% of the day's starting balance", "When the account has lost this much since midnight Pacific, every desk stops for the rest of the day. The starting balance is shared by all desks (BTC 5m sets it)."],
        ["Live switch", "One LIVE/PAPER switch per desk", "Real money only on desks you take live; new desks always start in paper."],
    ])
    d.add_heading("4.3 Trading hours by desk", level=2)
    _para(d, "Each 5-minute desk is sized by the Pacific hour its window opens in, set from its own profit-by-hour table (built on "
             "older strategy versions): an hour losing over 8+ trades is halved and a winning hour trades at full size. The hours that "
             "used to be skipped (7 AM, 2 PM and 4 PM, plus 9 AM on ETH) are caution hours since 2026-11-11: half size, and a bet needs "
             f"at least {CAUTION_MIN_CONF:.0%} calibrated confidence and a {CAUTION_MIN_PRICE * 100:.0f}¢ or higher buy price. They are "
             "traded so the current version builds its own record in them. The 15-minute and 1-hour desks trade 7 AM at half size.")
    hours = sorted({h for hs in HOUR_SIZE_5M.values() for h, _ in hs})
    look = {c: dict(hs) for c, hs in HOUR_SIZE_5M.items()}
    for f in facts:                                        # approved overrides on the live desks win over the defaults
        if f["asset"] in look:
            look[f["asset"]] = dict(f["hour_size"])
            hours = sorted(set(hours) | set(look[f["asset"]]))
    rows = [[_hour(h)] + [("skip" if look[c].get(h, 1.0) <= 0 else "caution" if h in CAUTION_HOURS_5M.get(c, ())
                           else "half" if look[c].get(h, 1.0) < 1 else "full") for c in ("btc", "eth", "sol", "xrp")]
            for h in hours]
    colors = {(ri, ci): (RED if v == "skip" else "B35C00" if v == "caution" else "9A6700" if v == "half" else None)
              for ri, row in enumerate(rows) for ci, v in enumerate(row)}
    _table(d, ["Hour (PT)", "BTC 5m", "ETH 5m", "SOL 5m", "XRP 5m"], rows, {k: v for k, v in colors.items() if v})
    _para(d, "Every hour not listed trades at full size on all four. The daily 8:15 AM review checks the 7 AM hour on BTC 5m and 15m "
             "(and your brothers' copies) and sends an Approve card when the numbers say it should change.")

    # ---- 5. the desks
    d.add_heading("5. The desks", level=1)
    rows, colors = [], {}
    for i, f in enumerate(facts):
        L = f["live"]
        rows.append([f["label"], "LIVE" if f["mode"] == "live" else "paper", f"{f['floor']}%" if f["floor"] else "—",
                     f"{f['window_total']:.0f}%", L["n"], f"{100.0 * L['wins'] / L['n']:.0f}%" if L["n"] else "—", _signed(L["pnl"]) if L["n"] else "—",
                     f["paper"]["n"], _signed(f["paper"]["pnl"]) if f["paper"]["n"] else "—"])
        if f["mode"] == "live":
            colors[(i, 1)] = GREEN
        if L["n"]:
            colors[(i, 6)] = _pnl_color(L["pnl"])
        if f["paper"]["n"]:
            colors[(i, 8)] = _pnl_color(f["paper"]["pnl"])
    _table(d, ["Desk", "Mode", "Floor", "Window cap", "Live trades", "Won", "Live P&L", "Paper trades", "Paper P&L"], rows,
           {k: v for k, v in colors.items() if v}, size=8.5)
    _para(d, "Trades and P&L are settled windows over every strategy version (a window can hold several bets). The scoreboards on the "
             "Station count only the current version, so they can show smaller numbers.")

    roles = {
        "btc": "The flagship. Live since the start and the source of most of the data behind the rules. Full method set, the widest sizing "
               "(5-10%) and its own calibrated steps. It sets the shared daily starting balance for the whole account.",
        "eth": "ETH 5m: calibrated confidence only, 85% confidence floor, 1-5% steps, its own hour table (9 AM is a caution hour). Since "
               "2026-11-10 it also skips any buy priced under 85c (on v1.21 its 75-84c buys lost money). Moved to paper by the owner on "
               "2026-11-10: even at 85c+ its floored record was about break-even, and its first two 1.22 fills (93-95c) both lost.",
        "sol": "SOL 5m: calibrated confidence only, 85% confidence floor, 1-5% steps, its own hour table (10 AM and 10 PM halved). Since "
               "2026-11-10 it also skips any buy priced under 75c (on v1.21 its 60-74c buys lost money). Moved to paper by the owner on "
               "2026-11-10 after a losing lifetime record (-6.9% on 258 live fills).",
        "xrp": "XRP 5m: calibrated confidence only, 85% floor, 1-5% steps; its losses cluster overnight, so 3-5 AM is halved.",
    }
    for f in facts:
        d.add_heading(f"5.{facts.index(f) + 1} {f['label']}", level=2)
        _para(d, roles.get(f["asset"], f"{f['coin']} on {f['horizon']} windows with {f['coin']}'s sizing steps"
                                       + (" and 1-hour timing." if f["horizon"] == "1h" else ".")
                                       + " New in v1.26; paper until it earns a live trial."))
        lo, hi = f["directional_secs"]
        _bullets(d, [
            f"Status: {'LIVE (real money)' if f['mode'] == 'live' else 'paper (simulated)'}" + ("" if f["running"] else "; not reporting a heartbeat"),
            f"Methods: {', '.join(f['methods'])}",
            f"Confidence: {'raw in its green hours (' + ', '.join(_hour(h) for h in f['raw_hours']) + '), calibrated otherwise' if f['raw_hours'] else 'calibrated at all hours'}; "
            f"lowest traded {f['floor']}%; steps {_steps(f['tiers'])}",
            f"Window: all bets in one window up to {f['window_total']:.0f}% of the balance; pairs up to {f['pair_pct']:g}%"
            + (f"; sweeps up to {f['sweep_pct']:g}%" if f["sweep_pct"] else ""),
            f"Timing: bets from {hi / 60:g} min to {lo:g} s before the close (late bets inside the final {f['late_split']:g} s)",
            f"Hours: {_hours_text(f['hour_size'])}",
            f"Record: live {f['live']['n']} windows, {_signed(f['live']['pnl'])} ({f['live']['n7']} in the last 7 days, {_signed(f['live']['pnl7'])}); "
            f"paper {f['paper']['n']} windows, {_signed(f['paper']['pnl'])}",
        ])

    # ---- 6. family accounts
    d.add_heading("6. Family accounts", level=1)
    _para(d, "Each brother has his own free Aibussol account with a blank Station, the Polymarket Desk business, and his own copies of "
             "one bot: BTC 5m. They trade on his own Polymarket keys, with his own ledger, his own daily stop and "
             f"his own {exposure.CROSS_DESK_PCT:.0f}% all-desk cap, so nothing he does touches your account and his downloads show only "
             "his trades. The bot logic, sizing steps and hours are the same as yours. You can open any of them read-only from the "
             "ACCOUNTS menu.")
    if family:
        rows, colors = [], {}
        for i, a in enumerate(family):
            tot = sum(v.get("all", 0.0) for v in a["pnl"].values()) if a["pnl"] else 0.0
            rows.append([a["name"], a["modes"].get("btc", "paper").upper(), _signed(tot)])
            colors[(i, 3)] = _pnl_color(tot)
        _table(d, ["Account", "BTC 5m", "Live P&L (current version)"], rows, {k: v for k, v in colors.items() if v})
    else:
        _para(d, "No family accounts are connected right now.")

    # ---- 7. operations
    d.add_heading("7. Operations and tools", level=1)
    _bullets(d, [
        "Hosting: every desk (yours and your brothers') runs inside the Aibussol server on Render, which redeploys on every update.",
        "Rate limits: Polymarket US allows 20 requests per second per server address. All desks share one pace of 14 per second, paper "
        "desks get a small lane of their own, and market data is fetched once and shared by every desk and account. A 429 (too many "
        "requests) error triggers a short cool-down instead of lost trades.",
        "Untracked trades: if Polymarket shows a position the desk did not record, it raises an \"Unrecorded trade\" alert.",
        "Station tools: one LIVE/PAPER switch at the top; profit by hour on every desk; what works and what doesn't per method; "
        "combined all-desk trade and shadow CSVs; the daily P&L Excel with every bot; this brief.",
        "Shadow strategies: fixed rulebooks on each 5-minute desk (shrunk model, 85¢ floor, 75¢ floor, maker pair and gate; rebuilt "
        "2026-11-10 with a fresh log) record what they would have done without trading, so new ideas are tested on real markets "
        "before they risk money.",
        "Market data (2026-11-11): Polymarket now publishes each window as an event (btc-updown-5m-...) with the tradable market "
        "inside it (cpc-btc-updown-5m-...). The desks look the event up to find the market. Since about 7:30 AM Pacific that day the "
        "public data gateway answers \"market not found\" for live Up/Down order books, so the desks read the book from the trading "
        "host with the account's keys. Trading was down from about 7:30 AM to 3 PM that day while this was found and fixed.",
        "Decision records: every bet now stores its method and the raw and calibrated confidence at the moment it was decided "
        "(decision_method, decision_raw, decision_conf in the trades CSV), next to the reading at fill time.",
        "Blocked-trade log: every candidate a rule stopped is recorded with the window's result, so each rule can be checked against "
        "the trades it passed up (Station download).",
        "Daily review: at 8:15 AM Pacific the 7 AM hour is reviewed on each BTC desk and an Approve card arrives when a change is due.",
    ])

    # ---- 8. decisions log
    d.add_heading("8. Key decisions so far", level=1)
    _table(d, ["Version", "Decision"], [
        ["v1.18-1.23", "Confident directional bets, then edge and late triggers (one bet per method per window, never against a side already bet), per-coin confidence steps, a late-only test desk."],
        ["v1.25", "Sizing by hour of day; 7 AM halved first, then reviewed every morning."],
        ["v1.26-1.27", "Grew from 2 to 12 desks (5m, 15m and 1h for BTC, ETH, SOL and XRP); the new 10 start in paper; 1-hour timing for the 1h desks."],
        ["v1.28", "Sweeps count under the all-desk cap; BTC 5m's last-minute 30-70¢ block extended to every 5-minute desk."],
        ["v1.29-1.30", "Rate-limit fix after lost XRP and SOL trades: one shared pace per server, a paper lane, shared market data."],
        ["Nov 7 (v1.31)", "Every 5-minute desk gets its own hour table from its profit-by-hour results."],
        ["Nov 7 (v1.32)", "ETH, SOL and XRP trade from 85% at 1-5% of the balance in four steps (were 1% flat at 90-95%)."],
        ["Nov 7 (v1.33-1.34)", "All-desk cap lowered to 20%; ETH/SOL/XRP keep every bet in one window under 5%."],
        ["Nov 8 (v1.36-1.37)", "Every desk trades on live-calibrated confidence at every hour; the correction set to half strength; BTC calibrated floor 80%."],
        ["Nov 10", "Retired BTC 15m, BTC 1h, SOL 1h, XRP 15m, XRP 1h and ETH 15m; family accounts run BTC 5m only. ETH 5m 85¢ and SOL 5m 75¢ entry floors. Shadow strategies rebuilt. Strategy version 1.22 (clean scoreboards)."],
        ["Nov 10", "ETH 5m, SOL 5m and SOL 15m moved to paper; BTC 5m, XRP 5m and ETH 1h stay live."],
        ["Nov 11", "Calibration made lower-only (a lucky streak can no longer lift a bet over the floor) and set to 60% strength; 75% left BTC 5m almost no trades."],
        ["Nov 11", "Polymarket market-data change handled: event lookup to find each window's market; order books read from the trading host."],
        ["Nov 11", "Skipped hours become half-size caution hours with an 85% calibrated floor and a 70¢ entry floor."],
    ])

    # ---- 9. watch list
    d.add_heading("9. What to watch next", level=1)
    _bullets(d, [
        "ETH, SOL and XRP at the new 85% floor: if their live win rate holds above about 85% over 30+ trades, the bigger steps are earning "
        "their keep; if it slips, raise the floor to 88% before cutting the sizes.",
        "Hour tables: most hours still have fewer than 10 trades. Re-run the profit-by-hour tables after a week and tighten or relax them.",
        "Methods per coin: once a method has 15-20 trades on one coin, switch off any that keep losing (what works and what doesn't).",
        "Paper desks: a desk earns a live trial when its paper record is clearly profitable over a meaningful sample; start it small.",
        "Rate limits: any new \"HTTP 429\" in the desk log means the server is near Polymarket's limit and should be looked at.",
        "Caution hours: after two weeks, check each one's live record on the current version; keep, halve or skip it on that, not on "
        "the older versions' tables.",
        "Calibration at 60%: watch BTC 5m trade count and profit per dollar staked. 75% cut trades by about half on past fills "
        "without a clear gain; 50% (still lower-only) is the fallback if volume stays too thin.",
        "Decision-time confidence: compare decision_conf with the fill-time reading on any bet that looks like it should not have "
        "passed the floor.",
        "Market data: if Polymarket fixes its public order-book gateway, the trading-host fallback simply stops being used; if the "
        "trading host also starts refusing, the desks stop trading and the log says so.",
        "Scaling: raise position size or the balance only after the current version shows a high win rate and positive profit per "
        "dollar staked over a meaningful sample.",
    ])

    foot = d.add_paragraph()
    r = foot.add_run("Trading carries real risk of loss; past results do not guarantee future ones. This brief describes the system as "
                     "configured at the time it was generated.")
    r.italic, r.font.size = True, Pt(8.5)

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


async def export_brief(engine, pool) -> bytes:
    from openjarvis.server.crew import desk

    await desk._ensure(pool)
    facts = await _desk_facts(pool)
    try:
        family = await _family(engine, pool)
    except Exception:
        family = []
    return build_doc(facts, family, dt.datetime.now(dt.timezone.utc))
