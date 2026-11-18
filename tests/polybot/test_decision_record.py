from openjarvis.polybot.brokers import BookView
from openjarvis.polybot.config import from_env
from openjarvis.polybot.desk import Desk


def test_bet_records_the_price_and_book_age_it_was_decided_on():
    d = Desk(from_env({}, asset="btc"), None, None)
    d.fair_model = 0.95
    d.book = BookView(bids=[(0.78, 10)], offers=[(0.80, 10)], last_trade_px=0.79, last_trade_ts=990.0, ts=998.5)
    d._place("w1", "directional", "up", 5.0, 6.0, price=0.80, now=1000.0)
    rec = d._decision[("w1", "up")]
    assert rec["decision_price"] == 0.8 and rec["decision_book_age_s"] == 1.5
    assert rec["decision_last_trade"] == 0.79 and rec["decision_last_trade_age_s"] == 10.0
    assert rec["decision_method"] == "directional" and rec["decision_raw"] == 0.95



def test_fill_records_seconds_from_decision_to_fill():
    from openjarvis.polybot.brokers import Fill
    d = Desk(from_env({}, asset="btc"), None, None)
    d.fair_model = 0.95
    d.book = BookView(bids=[(0.78, 10)], offers=[(0.80, 10)], last_trade_px=0.79, last_trade_ts=990.0, ts=998.5)
    d._place("w1", "directional", "up", 5.0, 6.0, price=0.80, now=1000.0)
    feats = d._decision_feats(Fill("w1", "up", 0.67, 6.0, 1002.4))
    assert feats["decision_ts"] == 1000.0 and feats["decision_to_fill_s"] == 2.4
    assert d._decision_feats(Fill("w2", "up", 0.67, 6.0, 1002.4)) == {}      # a fill with no decision record adds nothing



def test_decision_records_how_much_was_on_offer():
    # 2026-11-18: contracts at the best price and within 2c, for both sides (Down buys hit the Up bids at 1 - bid)
    d = Desk(from_env({}, asset="xrp"), None, None)
    d.fair_model = 0.95
    d.book = BookView(bids=[(0.20, 30), (0.19, 50), (0.15, 99)], offers=[(0.75, 12), (0.76, 8), (0.80, 40)],
                      last_trade_px=0.75, last_trade_ts=990.0, ts=999.0)
    d._place("w1", "directional", "up", 7.5, 10.0, price=0.75, now=1000.0)
    rec = d._decision[("w1", "up")]
    assert rec["decision_qty"] == 10.0 and rec["decision_ask_size"] == 12.0
    assert rec["decision_depth_2c"] == 20.0 and rec["decision_depth_2c_usd"] == 15.08
    d._place("w2", "directional", "down", 8.0, 10.0, price=0.80, now=1000.0)
    rec = d._decision[("w2", "down")]
    assert rec["decision_ask_size"] == 30.0 and rec["decision_depth_2c"] == 80.0    # Down at 80c and 81c; 85c is past 2c
