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
    max_window_usd: float = 1.0         # the most it may pay into one window (Up and Down together)
    max_order_usd: float = 0.5          # the size of one resting order
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
    horizon: str = "5m"                 # which Up/Down series: 5m, 15m or 1h
    asset: str = "btc"
    limits: Limits = field(default_factory=Limits)
    quote_after_open_s: float = 20.0    # wait this long after a window opens (the price to beat is stamped a few seconds in)
    stop_before_end_s: float = 25.0     # pull every quote this long before the close (the last seconds are where fast bots pick you off)
    jump_usd: float = 25.0              # pull quotes if the index moves this much within jump_window_s
    jump_window_s: float = 3.0
    cooldown_s: float = 10.0            # and stay out this long afterwards
    vol_mult: float = 1.25              # widen the model's uncertainty (fat tails): checked against history in tests/backtests
    model_weight: float = 0.4           # v1.1: fair value = this much model + the rest the market's own mid price
    momentum_s: float = 45.0            # v1.1: look back this far for a run in BTC ...
    momentum_z: float = 0.6             # ... and do not buy against a move bigger than this many standard deviations (0 = off)
    max_fills_per_side: int = 1         # v1.1: stop adding to a side in a window after this many fills (0 = no limit)
    learn: bool = True                  # v1.1: demand extra edge while the last 20 windows are losing
    stale_s: float = 4.0                # data older than this pulls every quote
    tick_s: float = 1.0                 # how often the desk thinks
    flush_s: float = 60.0               # how often state is written to the database
    control_s: float = 20.0             # how often the control row is read (pause, kill, mode, limits)
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


def from_env(env: Optional[Mapping[str, str]] = None) -> Config:
    e = os.environ if env is None else env
    lim = Limits(bankroll_usd=_f(e, "POLYBOT_BANKROLL_USD", 50.0, 0.0),
                 max_window_usd=_f(e, "POLYBOT_MAX_WINDOW_USD", 1.0, 0.0),
                 max_order_usd=_f(e, "POLYBOT_MAX_ORDER_USD", 0.5, 0.0),
                 daily_loss_usd=_f(e, "POLYBOT_DAILY_LOSS_USD", 10.0, 0.0),
                 daily_loss_pct=_f(e, "POLYBOT_DAILY_LOSS_PCT", 15.0, 0.0, 100.0),
                 min_edge=_f(e, "POLYBOT_MIN_EDGE", 0.04, 0.01, 0.40)).clean()
    horizon = (e.get("POLYBOT_HORIZON") or "5m").strip().lower()
    return Config(
        mode="paper",
        allow_live=(e.get("POLYBOT_ALLOW_LIVE") or "").strip().lower() in _TRUE,
        key_id=(e.get("POLYMARKET_KEY_ID") or "").strip(),
        secret_key=(e.get("POLYMARKET_SECRET_KEY") or "").strip(),
        horizon=horizon if horizon in ("5m", "15m", "1h") else "5m",
        limits=lim,
        quote_after_open_s=_f(e, "POLYBOT_QUOTE_AFTER_OPEN_S", 20.0, 5.0, 600.0),
        stop_before_end_s=_f(e, "POLYBOT_STOP_BEFORE_END_S", 25.0, 5.0, 600.0),
        jump_usd=_f(e, "POLYBOT_JUMP_USD", 25.0, 1.0),
        vol_mult=_f(e, "POLYBOT_VOL_MULT", 1.25, 0.5, 5.0),
        model_weight=_f(e, "POLYBOT_MODEL_WEIGHT", 0.4, 0.0, 1.0),
        momentum_s=_f(e, "POLYBOT_MOMENTUM_SECONDS", 45.0, 5.0, 600.0),
        momentum_z=_f(e, "POLYBOT_MOMENTUM_Z", 0.6, 0.0, 10.0),
        max_fills_per_side=int(_f(e, "POLYBOT_MAX_FILLS_PER_SIDE", 1, 0, 100)),
        learn=(e.get("POLYBOT_LEARN") or "true").strip().lower() in _TRUE,
        flush_s=_f(e, "POLYBOT_FLUSH_SECONDS", 60.0, 10.0, 3600.0),
        control_s=_f(e, "POLYBOT_CONTROL_SECONDS", 20.0, 5.0, 600.0),
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
