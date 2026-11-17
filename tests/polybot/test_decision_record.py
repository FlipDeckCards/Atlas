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
