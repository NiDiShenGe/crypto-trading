from datetime import UTC, datetime
from pathlib import Path
import tempfile

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
        fills = engine.process(result(11.6))
        assert len(fills) == 1
        position = broker.positions["ALTUSDT"]
        assert position.quantity == original * 0.5
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
