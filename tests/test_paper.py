import pytest

from crypto_trader.domain import Side, Signal
from crypto_trader.paper import PaperBroker


def test_long_round_trip_with_fees_and_slippage() -> None:
    broker = PaperBroker(initial_equity=100, taker_fee_rate=0.0006, slippage_rate=0.0005)
    signal = Signal("ALTUSDT", Side.LONG, 10, 9.5, 0.8, "test")
    entry = broker.open_position(signal, quantity=2, leverage=2)
    exit_fill = broker.close_position("ALTUSDT", 11, "take profit")
    assert entry.price > 10
    assert exit_fill.price < 11
    assert exit_fill.realized_pnl > 0
    assert broker.cash > 100


def test_short_round_trip_profit() -> None:
    broker = PaperBroker()
    signal = Signal("ALTUSDT", Side.SHORT, 10, 10.5, 0.8, "test")
    broker.open_position(signal, quantity=2, leverage=2)
    exit_fill = broker.close_position("ALTUSDT", 9, "take profit")
    assert exit_fill.realized_pnl > 0


def test_rejects_position_beyond_available_margin() -> None:
    broker = PaperBroker(initial_equity=100)
    signal = Signal("ALTUSDT", Side.LONG, 100, 95, 0.8, "test")
    with pytest.raises(ValueError, match="insufficient"):
        broker.open_position(signal, quantity=10, leverage=2)
