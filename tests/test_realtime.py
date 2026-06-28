from datetime import UTC, datetime
from pathlib import Path
import tempfile

from crypto_trader.config import (
    RiskConfig,
    Settings,
    StrategyConfig,
    UniverseConfig,
)
from crypto_trader.domain import Side, Signal
from crypto_trader.execution import PaperTradingEngine
from crypto_trader.paper import PaperBroker
from crypto_trader.realtime import BitgetPositionMonitor
from crypto_trader.scanner import ScanResult
from crypto_trader.storage import EventStore


class FakeTickerClient:
    def tickers(self):
        return [
            {
                "symbol": "ALTUSDT",
                "bidPr": "10.20",
                "askPr": "10.30",
            },
            {
                "symbol": "OTHERUSDT",
                "lastPr": "5",
            },
        ]


def _settings() -> Settings:
    return Settings(
        UniverseConfig(30, 10_000_000, 0.003, frozenset(), 300, 20),
        StrategyConfig(20, 20, 1.8, 20, 50, 14, 2),
        RiskConfig(0.01, 0.02, 0.05, 0.15, 3, 5, 200, 2, 5, 4),
    )


def test_rest_fallback_updates_only_open_position_prices() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "test.sqlite3")
        store.initialize()
        broker = PaperBroker(slippage_rate=0)
        engine = PaperTradingEngine(_settings(), store, broker)
        signal = Signal("ALTUSDT", Side.LONG, 10, 9, 0.9, "test")
        engine.process(ScanResult(
            datetime.now(UTC),
            1,
            1,
            1,
            (signal,),
            {"ALTUSDT": 10},
        ))
        monitor = BitgetPositionMonitor(
            engine,
            store,
            FakeTickerClient(),
        )
        prices = monitor._rest_prices({"ALTUSDT"})
        assert prices == {"ALTUSDT": 10.25}
        engine.process_realtime_price("ALTUSDT", prices["ALTUSDT"])
        assert broker.positions["ALTUSDT"].current_price == 10.25
