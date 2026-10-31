"""Robinhood crypto bot: risk limits, paper book and the live gate."""

import pytest

from openjarvis.server.crew import robinhood_bot as bot


def test_per_order_cap_is_three_percent_of_account():
    s = bot.size_order(1000.0, {}, "BTC")
    assert s.notional == 30.0 and s.reason == "per-order cap"


def test_exposure_cap_is_twelve_percent_across_all_coins():
    open_ = {"BTC": 20.0, "ETH": 20.0, "SOL": 0.0, "XRP": 0.0}   # 40 open of 12% of 1000 = 120: plenty of room
    assert bot.size_order(1000.0, open_, "SOL").notional == 30.0
    near_cap = {"BTC": 100.0, "ETH": 15.0}                        # 115 open, cap 120: 5 left
    s = bot.size_order(1000.0, near_cap, "SOL")
    assert s.notional == 5.0 and s.reason == "exposure headroom"


def test_no_order_when_cap_reached_or_coin_unknown_or_no_account():
    assert bot.size_order(1000.0, {"BTC": 120.0}, "ETH").notional == 0.0
    assert bot.size_order(1000.0, {}, "DOGE").notional == 0.0
    assert bot.size_order(0.0, {}, "BTC").notional == 0.0


def test_paper_book_records_and_tracks_exposure_without_network():
    book = bot.PaperBook()
    book.place("BTC", "buy", 30.0, 77000.0)
    book.place("BTC", "sell", 10.0, 77100.0)
    assert book.fills[0]["mode"] == "paper"
    assert book.exposure()["BTC"] == 20.0


def test_paper_book_rejects_bad_orders():
    book = bot.PaperBook()
    with pytest.raises(ValueError):
        book.place("BTC", "hold", 10.0, 1.0)
    with pytest.raises(ValueError):
        book.place("BTC", "buy", 0.0, 1.0)


def test_live_order_refused_in_paper_mode_even_if_approved():
    with pytest.raises(bot.LiveTradingNotEnabled, match="paper mode"):
        bot.place_live({"coin": "BTC"}, owner_approved=True, live_enabled=False)


def test_live_order_refused_without_owner_approval():
    with pytest.raises(bot.LiveTradingNotEnabled, match="approved"):
        bot.place_live({"coin": "BTC"}, owner_approved=False, live_enabled=True)


def test_live_order_refused_until_robinhood_path_is_built():
    with pytest.raises(bot.LiveTradingNotEnabled, match="not built yet"):
        bot.place_live({"coin": "BTC"}, owner_approved=True, live_enabled=True)
