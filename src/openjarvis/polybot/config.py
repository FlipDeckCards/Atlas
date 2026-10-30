"""Settings for the desk. Environment values are the defaults; the control row in the database (set from Aibussol) can tighten
them at any time and can loosen them only after the owner's Approve tap (see apply_control)."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Mapping, Optional

_TRUE = {"1", "true", "yes", "on"}

GATEWAY = "https://gateway.polymarket.us"
API = "https://api.polymarket.us"



def _f(env: Mapping[str, str], name: str, default: float, lo: float = 0.0, hi: float = 1e12) -> float:
    try:
        v = float((env.get(name) or "").strip() or default)
    except ValueError:
        v = default
    return min(hi, max(lo, v))


@dataclass(frozen=True)
class Limits:
    """The money rules. Every amount is US dollars."""

    bankroll_usd: float = 50.0          # the most the desk may ever have at risk in total (also capped by the account's buying power)
    max_window_usd: float = 50.0        # safety ceiling per window; the real cap is window_pct % of the CURRENT balance
    max_order_usd: float = 50.0         # safety ceiling per order; the real size comes from the edge (quarter-Kelly)
    daily_loss_usd: float = 10.0        # used only when daily_loss_pct is 0: a flat dollar stop for the Los Angeles day
    daily_loss_pct: float = 15.0        # stop for the rest of the day after losing this % of the day's starting balance (0 = use the flat $)
    min_edge: float = 0.04              # only buy at least this far (in dollars per contract) better than fair value

    def tighter_than(self, other: "Limits") -> bool:
        """True when every rule is at least as strict as `other` (so it can apply without an Approve)."""
        return (self.bankroll_usd <= other.bankroll_usd and self.max_window_usd <= other.max_window_usd
                and self.max_order_usd <= other.max_order_usd and self.daily_loss_usd <= other.daily_loss_usd
                and (other.daily_loss_pct == 0 or 0 < self.daily_loss_pct <= other.daily_loss_pct)
                and self.min_edge >= other.min_edge)

    def clean(self) -> "Limits":
        return Limits(bankroll_usd=round(min(max(self.bankroll_usd, 0.0), 100000.0), 2),
                      max_window_usd=round(min(max(self.max_window_usd, 0.0), self.bankroll_usd or 0.0), 2),
                      max_order_usd=round(min(max(self.max_order_usd, 0.0), self.max_window_usd or 0.0), 2),
                      daily_loss_usd=round(min(max(self.daily_loss_usd, 0.0), self.bankroll_usd or 0.0), 2),
                      daily_loss_pct=round(min(max(self.daily_loss_pct, 0.0), 100.0), 2),
                      min_edge=round(min(max(self.min_edge, 0.01), 0.40), 3))


@dataclass(frozen=True)
class Config:
    mode: str = "paper"                 # paper | live (live only when allow_live and the control row says live)
    allow_live: bool = False            # POLYBOT_ALLOW_LIVE: the worker-side half of the two-key switch
    key_id: str = ""
    secret_key: str = ""
    horizon: str = "5m"                 # which Up/Down series: 5m, 15m or 1h (in trading hours)
    horizon_night: str = ""             # outside trading hours, if different (BTC: 15m by day, 5m at night for the sweeps)
    asset: str = "btc"
    limits: Limits = field(default_factory=Limits)
    quote_after_open_s: float = 20.0    # wait this long after a window opens (the price to beat is stamped a few seconds in)
    stop_before_end_s: float = 25.0     # pull every quote this long before the close (the last seconds are where fast bots pick you off)
    jump_usd: float = 25.0              # pull quotes if the index moves this much within jump_window_s
    jump_window_s: float = 3.0
    cooldown_s: float = 10.0            # and stay out this long afterwards
    vol_mult: float = 1.25              # widen the model's uncertainty (fat tails): checked against history in tests/backtests
    model_weight: float = 0.4           # v1.1: fair value = this much model + the rest the market's own mid price
    drift_beta: float = 0.7             # v1.3: the last 30 s of BTC's move is expected to carry on this much (measured 0.6-1.3)
    momentum_s: float = 30.0            # v1.1 (30 s since v1.3, where the effect was measured): look back this far for a run ...
    trend_s: float = 900.0              # v1.10: the trend is BTC's move over the last 15 minutes ...
    trend_z: float = 1.0                # ... and no new pair starts on the side it runs against (0 = off); completing is fine
    momentum_z: float = 0.6             # ... and do not buy against a move bigger than this many standard deviations (0 = off)
    max_fills_per_side: int = 8         # fills per side per window (v1.8: pairs are built in steps; the unpaired cap bounds the risk)
    learn: bool = True                  # v1.1: demand extra edge while the last 20 windows are losing
    kelly_fraction: float = 0.25        # v1.2: bet size grows with the edge: this fraction of the Kelly stake
    window_pct: float = 10.0            # at most this % of the CURRENT account balance in one window (recomputed every window)
    min_order_usd: float = 0.25         # v1.2: smallest order worth placing
    unpaired_pct: float = 10.0          # v1.8: at most this % of the window budget riding on one side without its matching pair
    open_cutoff_s: float = 90.0         # v1.8: in the last this-many seconds, only complete pairs; never open a new one-sided leg
    hedge_after_s: float = 45.0         # v1.12: a leg unpaired this long ...
    hedge_before_end_s: float = 75.0    # ... or this close to the close is finished at once (crossing the spread if needed) ...
    hedge_max_loss: float = 0.05        # ... if the pair costs at most $1.05 a contract (0 = hedging off)
    trade_start: str = "06:30"          # v1.13: new pairs only in these hours (Los Angeles); open legs are still finished and settled
    trade_end: str = "20:30"            # (overnight books are empty, so one side fills and the other never does)
    pairs_only: bool = True             # v1.16 back to basics: pairs only, both sides bought at once; no sweeps, no resting legs
    directional: bool = True           # v1.18: confident directional trades, paper only (see directional.py)
    directional_secs_left: float = 240.0   # ... considered from this many seconds before the close
    directional_min_secs: float = 30.0     # ... and not in the final 30 seconds
    pair_margin: float = 0.02           # v1.17: a pair opens only when both sides plus both taker fees leave this much under $1.00
    sweep_pct: float = 5.0              # v1.13 closing sweep: at most this % of the balance on the likely winner near the close (0 = off)
    sweep_secs_left: float = 60.0       # sweeps run from this many seconds before the close until stop_before_end_s (trading hours;
                                        # v1.14: 60 s, inside the closing average, where the result is mostly locked in)
    sweep_secs_left_night: float = 90.0 # overnight (thin books: the market turns decisive earlier, so start sooner to get filled)
    sweep_min_price: float = 0.75       # only buy the likely winner at 75c-97c ...
    sweep_max_price: float = 0.97
    sweep_min_edge: float = 0.03        # ... and only when the model's probability is at least 3c above the price after the fee
    sweep_min_gap_bp: float = 5.0       # v1.14: ... and the index is at least this many basis points past the price to beat on that side
    open_min_price: float = 0.30        # v1.9: start a new pair only on a side priced 30-70c (the 15-20c long shots never got paired)
    open_max_price: float = 0.70
    min_buy_price: float = 0.15         # v1.5: never pay less than this for a side (long shots lost)
    max_buy_price: float = 0.80         # v1.5: never pay more than this for a side (one miss costs nearly the whole bet)
    stale_s: float = 4.0                # data older than this pulls every quote
    tick_s: float = 1.0                 # how often the desk thinks
    flush_s: float = 60.0               # how often state is written to the database
    control_s: float = 5.0              # how often the control row is read (pause, kill, mode, limits); 5 s so a tap takes effect quickly
    database_url: str = ""
    data_dir: str = "polybot_data"      # used when there is no database (local paper runs)
    notify_url: str = ""                # ntfy topic (CREW_NOTIFY_URL): a push for every live entry and settlement

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("key_id"), d.pop("secret_key"), d.pop("database_url"), d.pop("notify_url")
        d["has_keys"] = bool(self.key_id and self.secret_key)
        return d

    def with_limits(self, limits: Limits) -> "Config":
        return replace(self, limits=limits.clean())


ASSETS = ("btc", "eth", "sol", "xrp")
HORIZON_S = {"5m": 300, "15m": 900, "1h": 3600}


def assets_from_env(env: Optional[Mapping[str, str]] = None) -> list:
    """POLYBOT_ASSETS: which desks run (default btc,eth,sol,xrp). Each desk is its own market, scoreboard and controls."""
    e = os.environ if env is None else env
    raw = (e.get("POLYBOT_ASSETS") or ",".join(ASSETS)).lower()
    out = [a.strip() for a in raw.split(",") if a.strip() in ASSETS]
    return list(dict.fromkeys(out)) or ["btc"]


def from_env(env: Optional[Mapping[str, str]] = None, asset: str = "btc") -> Config:
    e = os.environ if env is None else env
    lim = Limits(bankroll_usd=_f(e, "POLYBOT_BANKROLL_USD", 50.0, 0.0),
                 max_window_usd=_f(e, "POLYBOT_MAX_WINDOW_USD", 50.0, 0.0),
                 max_order_usd=_f(e, "POLYBOT_MAX_ORDER_USD", 50.0, 0.0),
                 daily_loss_usd=_f(e, "POLYBOT_DAILY_LOSS_USD", 10.0, 0.0),
                 daily_loss_pct=_f(e, "POLYBOT_DAILY_LOSS_PCT", 15.0, 0.0, 100.0),
                 min_edge=_f(e, "POLYBOT_MIN_EDGE", 0.04, 0.01, 0.40)).clean()
    a = (asset or "btc").lower()

    def per(name: str) -> str:
        """A per-desk setting (POLYBOT_WINDOW_PCT_ETH) wins over the shared one (POLYBOT_WINDOW_PCT)."""
        return (e.get(f"{name}_{a.upper()}") or e.get(name) or "").strip()

    horizon = (per("POLYBOT_HORIZON") or "5m").lower()
    return Config(
        mode="paper",
        allow_live=(e.get("POLYBOT_ALLOW_LIVE") or "").strip().lower() in _TRUE,
        key_id=(e.get("POLYMARKET_KEY_ID") or "").strip(),
        secret_key=(e.get("POLYMARKET_SECRET_KEY") or "").strip(),
        horizon=horizon if horizon in ("5m", "15m", "1h") else "5m",
        horizon_night=(per("POLYBOT_HORIZON_NIGHT").lower() if per("POLYBOT_HORIZON_NIGHT").lower() in ("5m", "15m", "1h") else ""),
        asset=a,
        limits=lim,
        quote_after_open_s=_f(e, "POLYBOT_QUOTE_AFTER_OPEN_S", 20.0, 5.0, 600.0),
        stop_before_end_s=_f(e, "POLYBOT_STOP_BEFORE_END_S", 25.0, 5.0, 600.0),
        jump_usd=_f(e, "POLYBOT_JUMP_USD", 25.0, 1.0),
        vol_mult=_f(e, "POLYBOT_VOL_MULT", 1.25, 0.5, 5.0),
        model_weight=_f(e, "POLYBOT_MODEL_WEIGHT", 0.4, 0.0, 1.0),
        momentum_s=_f(e, "POLYBOT_MOMENTUM_SECONDS", 30.0, 5.0, 600.0),
        drift_beta=_f(e, "POLYBOT_DRIFT_BETA", 0.7, 0.0, 3.0),
        trend_s=_f(e, "POLYBOT_TREND_SECONDS", 900.0, 60.0, 3000.0),
        trend_z=_f(e, "POLYBOT_TREND_Z", 1.0, 0.0, 10.0),
        momentum_z=_f(e, "POLYBOT_MOMENTUM_Z", 0.6, 0.0, 10.0),
        max_fills_per_side=int(_f(e, "POLYBOT_MAX_FILLS_PER_SIDE", 8, 0, 100)),
        learn=(e.get("POLYBOT_LEARN") or "true").strip().lower() in _TRUE,
        kelly_fraction=_f(e, "POLYBOT_KELLY_FRACTION", 0.25, 0.0, 1.0),
        window_pct=_f({"v": per("POLYBOT_WINDOW_PCT")}, "v", 10.0, 0.1, 50.0),
        min_order_usd=_f(e, "POLYBOT_MIN_ORDER_USD", 0.25, 0.01, 1000.0),
        unpaired_pct=_f(e, "POLYBOT_UNPAIRED_PCT", 10.0, 1.0, 100.0),
        open_cutoff_s=_f(e, "POLYBOT_OPEN_CUTOFF_SECONDS", 90.0, 0.0, 290.0),
        hedge_after_s=_f(e, "POLYBOT_HEDGE_AFTER_SECONDS", 45.0, 5.0, 3600.0),
        hedge_before_end_s=_f(e, "POLYBOT_HEDGE_BEFORE_END_SECONDS", 75.0, 30.0, 600.0),
        hedge_max_loss=_f(e, "POLYBOT_HEDGE_MAX_LOSS", 0.05, 0.0, 0.5),
        trade_start=(per("POLYBOT_TRADE_START") or "06:30"),
        trade_end=(per("POLYBOT_TRADE_END") or "20:30"),
        pairs_only=(e.get("POLYBOT_PAIRS_ONLY") or "true").strip().lower() in _TRUE,
        directional=(e.get("POLYBOT_DIRECTIONAL") or "true").strip().lower() in _TRUE,
        directional_secs_left=_f(e, "POLYBOT_DIRECTIONAL_SECONDS", 240.0, 30.0, 290.0),
        directional_min_secs=_f(e, "POLYBOT_DIRECTIONAL_MIN_SECONDS", 30.0, 5.0, 200.0),
        pair_margin=_f(e, "POLYBOT_PAIR_MARGIN", 0.02, 0.0, 0.5),
        sweep_pct=_f({"v": per("POLYBOT_SWEEP_PCT")}, "v", 5.0, 0.0, 50.0),
        sweep_secs_left=_f({"v": per("POLYBOT_SWEEP_SECONDS_DAY")}, "v", 60.0, 30.0, 600.0),
        sweep_secs_left_night=_f({"v": per("POLYBOT_SWEEP_SECONDS_NIGHT")}, "v", 90.0, 30.0, 600.0),
        sweep_min_price=_f(e, "POLYBOT_SWEEP_MIN_PRICE", 0.75, 0.5, 0.99),
        sweep_max_price=_f(e, "POLYBOT_SWEEP_MAX_PRICE", 0.97, 0.5, 0.99),
        sweep_min_edge=_f(e, "POLYBOT_SWEEP_MIN_EDGE", 0.03, 0.0, 0.5),
        sweep_min_gap_bp=_f({"v": per("POLYBOT_SWEEP_MIN_GAP_BP")}, "v", 5.0, 0.0, 500.0),
        open_min_price=_f(e, "POLYBOT_OPEN_MIN_PRICE", 0.30, 0.01, 0.99),
        open_max_price=_f(e, "POLYBOT_OPEN_MAX_PRICE", 0.70, 0.01, 0.99),
        min_buy_price=_f(e, "POLYBOT_MIN_BUY_PRICE", 0.15, 0.01, 0.99),
        max_buy_price=_f(e, "POLYBOT_MAX_BUY_PRICE", 0.80, 0.01, 0.99),
        flush_s=_f(e, "POLYBOT_FLUSH_SECONDS", 60.0, 10.0, 3600.0),
        control_s=_f(e, "POLYBOT_CONTROL_SECONDS", 5.0, 2.0, 600.0),
        database_url=(e.get("DATABASE_URL") or "").strip(),
        data_dir=(e.get("POLYBOT_DATA_DIR") or "polybot_data").strip(),
        notify_url=(e.get("POLYBOT_NOTIFY_URL") or e.get("CREW_NOTIFY_URL") or "").strip(),
    )


def apply_control(base: Config, control: Dict[str, Any]) -> Config:
    """The control row from Aibussol over the environment's settings. Limits there were approved by the owner (or are tighter);
    mode=live only counts when the worker itself allows live."""
    cfg = base
    lim = control.get("limits") if isinstance(control, dict) else None
    if isinstance(lim, dict):
        cur = asdict(base.limits)
        for k in cur:
            if k in lim:
                try:
                    cur[k] = float(lim[k])
                except (TypeError, ValueError):
                    pass
        cfg = cfg.with_limits(Limits(**cur))
    want = str((control or {}).get("mode") or "paper").lower()
    return replace(cfg, mode="live" if (want == "live" and base.allow_live and base.key_id and base.secret_key) else "paper")
