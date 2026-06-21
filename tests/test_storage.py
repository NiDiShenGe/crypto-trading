from pathlib import Path
import tempfile

from crypto_trader.storage import EventStore


def test_event_round_trip() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "events.sqlite3")
        store.initialize()
        event_id = store.append("risk_halt", {"reason": "daily loss"}, "BTCUSDT")
        events = store.recent()
        assert event_id == 1
        assert events[0]["event_type"] == "risk_halt"
        assert events[0]["symbol"] == "BTCUSDT"
        assert events[0]["payload"]["reason"] == "daily loss"


def test_state_round_trip() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "events.sqlite3")
        store.initialize()
        store.save_state("paper", {"cash": 99.5})
        assert store.load_state("paper") == {"cash": 99.5}
