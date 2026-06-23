from datetime import UTC, datetime
from pathlib import Path
import tempfile
import pytest

from crypto_trader.config import RiskConfig, Settings, StrategyConfig, UniverseConfig
from crypto_trader.domain import Side, Signal
from crypto_trader.execution import PaperTradingEngine
from crypto_trader.paper import PaperBroker
from crypto_trader.scanner import ScanResult
from crypto_trader.storage import EventStore


def settings() -> Settings:
    return Settings(
        UniverseConfig(30, 10_000_000, 0.003, frozenset(), 300, 20),
        StrategyConfig(20, 20, 1.8, 20, 50, 14, 2),
        RiskConfig(0.01, 0.02, 0.05, 0.15, 3, 5, 200, 2, 5, 4),
    )


def result(price: float, signals=()) -> ScanResult:
    return ScanResult(
        datetime.now(UTC), 1, 1, 1, tuple(signals), {"ALTUSDT": price}
    )


def test_signal_opens_paper_position_and_persists_state() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "test.sqlite3")
        store.initialize()
        broker = PaperBroker()
        engine = PaperTradingEngine(settings(), store, broker)
        signal = Signal("ALTUSDT", Side.LONG, 10, 9.5, 0.9, "test")
        fills = engine.process(result(10, [signal]))
        assert len(fills) == 1
        assert "ALTUSDT" in broker.positions
        assert store.load_state("paper_broker") is not None


def test_long_moves_to_breakeven_and_takes_partial_profit() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "test.sqlite3")
        store.initialize()
        broker = PaperBroker(slippage_rate=0)
        engine = PaperTradingEngine(settings(), store, broker)
        signal = Signal("ALTUSDT", Side.LONG, 10, 9, 0.9, "test")
        engine.process(result(10, [signal]))
        original = broker.positions["ALTUSDT"].quantity
        fills = engine.process(result(12.1))
        assert len(fills) == 1
        position = broker.positions["ALTUSDT"]
        assert position.quantity == pytest.approx(original * (2 / 3))
        assert position.first_take_profit_done
        assert position.stop_price >= position.entry_price


def test_stop_closes_position() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "test.sqlite3")
        store.initialize()
        broker = PaperBroker(slippage_rate=0)
        engine = PaperTradingEngine(settings(), store, broker)
        engine.process(result(10, [Signal("ALTUSDT", Side.LONG, 10, 9, 0.9, "test")]))
        fills = engine.process(result(8.9))
        assert len(fills) == 1
        assert not broker.positions
        assert fills[0].realized_pnl < 0


def test_realtime_price_updates_position_snapshot() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "test.sqlite3")
        store.initialize()
        broker = PaperBroker(slippage_rate=0)
        engine = PaperTradingEngine(settings(), store, broker)
        engine.process(result(10, [Signal("ALTUSDT", Side.LONG, 10, 9, 0.9, "test")]))
        engine.process_realtime_price("ALTUSDT", 10.25)
        position = broker.positions["ALTUSDT"]
        assert position.current_price == 10.25
        assert position.price_updated_at


def test_closed_candle_back_inside_breakout_exits_early() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "test.sqlite3")
        store.initialize()
        broker = PaperBroker(slippage_rate=0)
        engine = PaperTradingEngine(settings(), store, broker)
        signal = Signal(
            "ALTUSDT", Side.LONG, 10, 9, 0.9, "test", breakout_level=9.8
        )
        engine.process(result(10, [signal]))
        scan = ScanResult(
            datetime.now(UTC),
            1,
            1,
            1,
            (),
            {"ALTUSDT": 9.7},
            {},
            {"ALTUSDT": 9.7},
            {"ALTUSDT": datetime.now(UTC)},
        )
        fills = engine.process(scan)
        assert fills[0].reason == "failed_breakout"
        assert "ALTUSDT" not in broker.positions


def test_six_bars_without_half_r_progress_exits() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "test.sqlite3")
        store.initialize()
        broker = PaperBroker(slippage_rate=0)
        engine = PaperTradingEngine(settings(), store, broker)
        signal = Signal(
            "ALTUSDT", Side.LONG, 10, 9, 0.9, "test", breakout_level=9.8
        )
        engine.process(result(10, [signal]))
        opened = broker.positions["ALTUSDT"].opened_at
        scan = ScanResult(
            datetime.now(UTC),
            1,
            1,
            1,
            (),
            {"ALTUSDT": 10.1},
            {},
            {"ALTUSDT": 10.1},
            {"ALTUSDT": opened + __import__("datetime").timedelta(minutes=31)},
        )
        fills = engine.process(scan)
        assert fills[0].reason == "no_progress_exit"


def test_profitable_stop_is_classified_as_trailing_stop() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "test.sqlite3")
        store.initialize()
        broker = PaperBroker(slippage_rate=0)
        engine = PaperTradingEngine(settings(), store, broker)
        engine.process(
            result(
                10,
                [Signal("ALTUSDT", Side.LONG, 10, 9, 0.9, "test")],
            )
        )
        engine.process_realtime_price("ALTUSDT", 12.2)
        stop = broker.positions["ALTUSDT"].stop_price
        fills = engine.process_realtime_price("ALTUSDT", stop - 0.01)
        assert fills[0].reason == "trailing_stop"
        assert fills[0].peak_r >= 2
