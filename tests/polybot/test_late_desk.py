"""v1.23: the late-only paper desk on BTC 5m markets: its own store, lock, paper label and method set."""

from openjarvis.polybot import directional
from openjarvis.polybot.config import ASSETS, apply_control, from_env
from openjarvis.polybot.runtime import lock_id
from openjarvis.polybot.state import kv_key


def test_late_desk_runs_only_late_on_btc_5m_and_writes_its_own_paper_label():
    c = from_env({}, asset="btclate")
    assert (c.asset, c.coin, c.horizon) == ("btclate", "btc", "5m")
    assert c.methods == ("late",)
    assert c.ledger == "paper-late"
    assert c.mode == "paper"


def test_late_desk_stays_paper_even_when_the_control_row_says_live():
    from dataclasses import replace
    late = replace(from_env({}, asset="btclate"), allow_live=True, key_id="k", secret_key="s")
    assert apply_control(late, {"mode": "live"}).mode == "paper"
    btc = replace(from_env({}, asset="btc"), allow_live=True, key_id="k", secret_key="s")
    assert apply_control(btc, {"mode": "live"}).mode == "live"          # the live BTC desk is unchanged


def test_the_other_desks_keep_all_four_methods_and_the_paper_label():
    for a in ("btc", "btc15", "eth", "sol", "xrp"):
        c = from_env({}, asset=a)
        assert c.methods == ("pairs", "directional", "edge", "late") and c.ledger == "paper"


def test_late_desk_has_its_own_store_and_lock():
    assert kv_key("status", "btclate") == "status:btclate"
    assert len({lock_id(a) for a in ASSETS}) == len(ASSETS)


def test_late_desk_gets_the_same_tiers_as_btc():
    assert directional.size_pct(0.93, "btclate") == directional.size_pct(0.93, "btc") == 8.0
    assert directional.method_pick("late", 0.93, 0.90, 0.10, asset="btclate")[0] == "up"
