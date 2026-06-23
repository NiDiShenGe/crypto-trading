from pathlib import Path
import tempfile

from crypto_trader.storage import EventStore
from crypto_trader.web import dashboard_data


def test_dashboard_defaults() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "dashboard.sqlite3")
        store.initialize()
        data = dashboard_data(store)
        assert data["mode"] == "PAPER"
        assert data["account"]["cash"] == 100
        assert data["account"]["current_equity"] == 100
        assert data["account"]["unrealized_pnl"] == 0
        assert data["account"]["maximum_positions"] == 3
        assert data["positions"] == []


def test_dashboard_calculates_live_position_pnl() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "dashboard.sqlite3")
        store.initialize()
        store.save_state(
            "paper_broker",
            {
                "initial_equity": 1000,
                "cash": 900,
                "positions": [{
                    "symbol": "ALTUSDT",
                    "side": "long",
                    "quantity": 10,
                    "entry_price": 10,
                    "current_price": 11,
                    "stop_price": 9,
                    "leverage": 1,
                    "price_updated_at": "2026-06-22T00:00:00+00:00",
                }],
            },
        )
        data = dashboard_data(store)
        assert data["account"]["unrealized_pnl"] == 10
        assert data["account"]["current_equity"] == 1010
        assert data["positions"][0]["unrealized_return"] == 0.1
