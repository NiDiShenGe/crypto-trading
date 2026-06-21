from crypto_trader.__main__ import _sync_paper_capital, _sync_runtime_capital


def test_capital_increase_preserves_position_and_adds_cash() -> None:
    state = {
        "initial_equity": 100.0,
        "cash": 51.0,
        "positions": [{"symbol": "ZECUSDT"}],
    }
    updated = _sync_paper_capital(state, 1000.0)
    assert updated["initial_equity"] == 1000.0
    assert updated["cash"] == 951.0
    assert updated["positions"] == state["positions"]


def test_runtime_thresholds_move_with_capital() -> None:
    state = {"day_start_equity": 100.0, "equity_high_watermark": 101.0}
    updated = _sync_runtime_capital(state, 900.0)
    assert updated["day_start_equity"] == 1000.0
    assert updated["equity_high_watermark"] == 1001.0
