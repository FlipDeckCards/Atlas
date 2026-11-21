from openjarvis.polybot.brokers import Fill, fill_fee
from openjarvis.polybot.strategy import taker_fee


def test_fill_fee_is_the_taker_fee_and_none_for_makers():
    assert abs(fill_fee(0.83, 7.33, True) - taker_fee(0.83) * 7.33) < 1e-5
    assert fill_fee(0.83, 7.33, False) == 0.0
    assert abs(fill_fee(0.17, 10, True) - fill_fee(0.83, 10, True)) < 1e-9      # same either side: p x (1 - p)


def test_book_fill_puts_the_fee_into_the_side_cost():
    from openjarvis.polybot.config import from_env
    from openjarvis.polybot.desk import Desk
    from openjarvis.polybot.strategy import Inventory

    d = Desk(from_env({}, asset="btc"), None, None)
    w = type("W", (), {})()
    w.slug, w.end, w.mode, w.fills, w.inv, w.price_to_beat = "s", 1000.0, "live", {}, Inventory(), 1.0
    d.open_windows["s"] = w
    d.log = lambda *a: None
    f = Fill("s", "down", 0.17, 7.33, 900.0, "o", fee=fill_fee(0.17, 7.33, True))   # Down at 83c
    d._book_fill(f)
    assert abs(w.inv.down_cost - (7.33 * 0.83 + f.fee)) < 1e-5
    assert abs(w.inv.pnl_if(False) - (7.33 - 7.33 * 0.83 - f.fee)) < 1e-5
