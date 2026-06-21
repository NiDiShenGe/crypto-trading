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
        assert data["account"]["maximum_positions"] == 3
        assert data["positions"] == []
