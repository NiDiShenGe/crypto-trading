from crypto_trader.validation import _leave_one_out, _statistics


def test_validation_statistics_and_leave_one_out() -> None:
    values = {
        "A": [1.0, -0.2],
        "B": [0.5, -0.1],
        "C": [0.2],
    }
    summary = _statistics(values)
    leave_out = _leave_one_out(values)
    assert summary["trades"] == 5
    assert summary["average_r"] > 0
    assert summary["profit_factor_r"] > 1
    assert leave_out["all_positive"] is True
