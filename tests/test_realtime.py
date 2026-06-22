from crypto_trader.realtime import BitgetPositionMonitor


def test_realtime_price_prefers_bid_ask_midpoint() -> None:
    assert BitgetPositionMonitor._price(
        {"bidPr": "99", "askPr": "101", "lastPr": "102"}
    ) == 100


def test_realtime_price_falls_back_to_last() -> None:
    assert BitgetPositionMonitor._price({"lastPr": "12.5"}) == 12.5
