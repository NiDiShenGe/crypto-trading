from datetime import UTC, datetime, timedelta
from dataclasses import replace

from crypto_trader.backtest import MultiStrategyBacktester, resample_hourly
from crypto_trader.config import load_settings
from crypto_trader.domain import Candle, Side, Signal


def test_resample_hourly_requires_complete_hour() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [
        Candle(
            start + timedelta(minutes=5 * index),
            10 + index,
            11 + index,
            9 + index,
            10.5 + index,
            100,
        )
        for index in range(13)
    ]
    hourly = resample_hourly(candles)
    assert len(hourly) == 1
    assert hourly[0].open == 10
    assert hourly[0].close == 21.5
    assert hourly[0].volume == 1200


class AlwaysLong:
    def evaluate(self, symbol, signal_candles, trend_candles):
        price = signal_candles[-1].close
        return Signal(
            symbol,
            Side.LONG,
            price,
            price - 10,
            1,
            "test",
            strategy_id="breakout_retest",
            score=1,
        )


def test_validation_warmup_cannot_open_before_trade_start() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [
        Candle(
            start + timedelta(minutes=5 * index),
            100,
            100.1,
            99.9,
            100,
            100,
        )
        for index in range(260)
    ]
    settings = load_settings()
    settings = replace(
        settings,
        strategy=replace(
            settings.strategy,
            use_btc_market_regime=False,
            failed_breakout_max_bars=10_000,
        ),
    )
    backtester = MultiStrategyBacktester(
        settings, {"breakout_retest"}
    )
    backtester.strategies = {"breakout_retest": AlwaysLong()}
    trade_start = candles[240].timestamp
    result = backtester.run(
        "TESTUSDT",
        candles,
        trade_start_time=trade_start,
    )
    assert len(result.trades) == 1
    assert result.trades[0].holding_bars == 16
    assert result.trades[0].reason == "end_of_test"


def test_validation_fold_closes_at_end_without_future_candles() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [
        Candle(
            start + timedelta(minutes=5 * index),
            100,
            100.1,
            99.9,
            100,
            100,
        )
        for index in range(300)
    ]
    settings = load_settings()
    settings = replace(
        settings,
        strategy=replace(
            settings.strategy,
            use_btc_market_regime=False,
            failed_breakout_max_bars=10_000,
        ),
    )
    backtester = MultiStrategyBacktester(
        settings, {"breakout_retest"}
    )
    backtester.strategies = {"breakout_retest": AlwaysLong()}
    result = backtester.run(
        "TESTUSDT",
        candles,
        trade_start_time=candles[240].timestamp,
        trade_end_time=candles[260].timestamp,
    )
    assert len(result.trades) == 1
    assert result.trades[0].holding_bars == 17
    assert result.trades[0].reason == "end_of_validation_fold"
