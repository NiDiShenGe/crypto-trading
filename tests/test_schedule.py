from datetime import UTC, datetime

from crypto_trader.__main__ import seconds_until_next_scan


def test_scan_is_aligned_after_next_five_minute_close() -> None:
    now = datetime(2026, 6, 22, 10, 2, 30, tzinfo=UTC)
    wait, target = seconds_until_next_scan(300, 3, now)
    assert target == datetime(2026, 6, 22, 10, 5, 3, tzinfo=UTC)
    assert wait == 153


def test_scan_at_boundary_waits_for_next_closed_candle() -> None:
    now = datetime(2026, 6, 22, 10, 5, 3, tzinfo=UTC)
    wait, target = seconds_until_next_scan(300, 3, now)
    assert target == datetime(2026, 6, 22, 10, 10, 3, tzinfo=UTC)
    assert wait == 300
