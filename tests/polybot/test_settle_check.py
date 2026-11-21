from openjarvis.polybot.state import Ledger


def test_result_carries_the_index_view():
    led = Ledger("btc")
    led.result("s", True, None, beat=58790.92, index_close=58776.68, index_up=False, sigma=2.0)
    r = led.take_results()[0]
    assert r["up"] is True and r["index_up"] is False and r["beat"] == 58790.92 and r["index_close"] == 58776.68
    led.result("t", False, None)                      # old callers still work
    assert led.take_results()[0]["beat"] is None
