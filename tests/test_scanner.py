from datetime import UTC, datetime, timedelta

from crypto_trader.config import RiskConfig, Settings, StrategyConfig, UniverseConfig
from crypto_trader.domain import Candle, Market
from crypto_trader.scanner import MarketScanner


class FakeClient:
    def markets(self):
        return [
            Market("ALTUSDT", "ALT", "USDT", 60, 20_000_000, 10, 10.01),
            Market("NEWUSDT", "NEW", "USDT", 2, 50_000_000, 2, 2.01),
        ]

    def candles(self, symbol, granularity, limit=100):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        step = timedelta(days=1) if granularity == "1D" else timedelta(minutes=5)
        return [
            Candle(start + step * i, 10, 10.2, 9.8, 10, 100)
            for i in range(100)
        ]


def settings():
    return Settings(
        UniverseConfig(30, 10_000_000, 0.003, frozenset(), 300, 20),
        StrategyConfig(20, 20, 1.8, 20, 50, 14, 2),
        RiskConfig(0.01, 0.02, 0.05, 0.15, 3, 5, 200, 2, 5, 4),
    )


def test_scan_filters_market_and_fetches_candidate() -> None:
    result = MarketScanner(FakeClient(), settings()).scan_once()
    assert result.total_markets == 2
    assert result.eligible_markets == 1
    assert result.scanned_candidates == 1
    assert result.signals == ()


def test_unknown_listing_age_can_be_confirmed_by_daily_history() -> None:
    client = FakeClient()
    client.markets = lambda: [
        Market("OLDUSDT", "OLD", "USDT", 0, 20_000_000, 10, 10.01)
    ]
    result = MarketScanner(client, settings()).scan_once()
    assert result.eligible_markets == 1
