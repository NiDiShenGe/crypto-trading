from datetime import UTC, datetime, timedelta

from crypto_trader.config import RiskConfig, Settings, StrategyConfig, UniverseConfig
from crypto_trader.domain import Candle, Market, Side, Signal
from crypto_trader.scanner import (
    MarketScanner,
    closed_candles,
    market_breadth_regime,
    market_regime,
    regime_allows_side,
)


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


def test_market_breadth_requires_directional_median() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rising = [
        Candle(
            start + timedelta(minutes=5 * index),
            100,
            101,
            99,
            100 + index * 0.01,
            100,
        )
        for index in range(290)
    ]
    flat = [
        Candle(
            start + timedelta(minutes=5 * index),
            100,
            101,
            99,
            100,
            100,
        )
        for index in range(290)
    ]
    assert market_breadth_regime([rising, rising, flat]) == "long"
    assert market_breadth_regime([flat, flat]) == "neutral"


def test_current_forming_candle_is_excluded() -> None:
    now = datetime(2026, 1, 1, 12, 7, tzinfo=UTC)
    candles = [
        Candle(datetime(2026, 1, 1, 11, 55, tzinfo=UTC), 1, 1, 1, 1, 1),
        Candle(datetime(2026, 1, 1, 12, 0, tzinfo=UTC), 1, 1, 1, 1, 1),
        Candle(datetime(2026, 1, 1, 12, 5, tzinfo=UTC), 1, 1, 1, 1, 1),
    ]
    result = closed_candles(candles, 300, now)
    assert [c.timestamp.minute for c in result] == [55, 0]


def test_same_symbol_signal_arbitration_uses_score_then_priority() -> None:
    scanner = MarketScanner(FakeClient(), settings())
    signals = [
        Signal(
            "ALTUSDT", Side.LONG, 10, 9, 0.6, "breakout",
            strategy_id="breakout_retest", score=0.6,
        ),
        Signal(
            "ALTUSDT", Side.LONG, 10, 9, 0.6, "squeeze",
            strategy_id="volatility_squeeze", score=0.6,
        ),
        Signal(
            "OTHERUSDT", Side.SHORT, 10, 11, 0.8, "pullback",
            strategy_id="trend_pullback", score=0.8,
        ),
    ]
    winners = scanner._arbitrate(signals)
    by_symbol = {signal.symbol: signal for signal in winners}
    assert by_symbol["ALTUSDT"].strategy_id == "volatility_squeeze"
    assert by_symbol["OTHERUSDT"].strategy_id == "trend_pullback"


def test_market_regime_detects_direction() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rising = [
        Candle(
            start + timedelta(hours=index),
            100 + index,
            101 + index,
            99 + index,
            100 + index,
            100,
        )
        for index in range(60)
    ]
    assert market_regime(rising) == "long"
    falling = list(reversed([
        Candle(
            start + timedelta(hours=index),
            100 + index,
            101 + index,
            99 + index,
            100 + index,
            100,
        )
        for index in range(60)
    ]))
    falling = [
        Candle(
            start + timedelta(hours=index),
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        )
        for index, candle in enumerate(falling)
    ]
    assert market_regime(falling) == "short"


def test_neutral_btc_regime_does_not_block_signals() -> None:
    assert regime_allows_side("neutral", Side.LONG)
    assert regime_allows_side("neutral", Side.SHORT)
    assert regime_allows_side("long", Side.LONG)
    assert not regime_allows_side("long", Side.SHORT)
