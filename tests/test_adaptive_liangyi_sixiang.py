from datetime import UTC, datetime, timedelta
from dataclasses import replace

from crypto_trader.config import load_settings
from crypto_trader.domain import Candle, Side
from crypto_trader.indicators import resample_candles
from crypto_trader.strategies import (
    AdaptiveLiangyiSixiangStrategy,
    _market_efficiency_series,
    _normalized_liangyi_series,
)


def _trend_candles(direction: int = 1) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    price = 100.0
    candles: list[Candle] = []
    for index in range(260):
        step = 0.35 * direction
        open_price = price
        close = max(1.0, open_price + step)
        high = max(open_price, close) + 0.08
        low = min(open_price, close) - 0.08
        candles.append(Candle(
            start + timedelta(hours=index),
            open_price,
            high,
            low,
            close,
            10_000,
        ))
        price = close
    return candles


def _signal_candles(direction: int = 1) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    price = 100.0
    candles: list[Candle] = []
    for index in range(900):
        drift = 0.08 * direction
        wave = 0.015 * direction if index % 3 else -0.005 * direction
        open_price = price
        close = max(1.0, open_price + drift + wave)
        high = max(open_price, close) + 0.20
        low = min(open_price, close) - 0.20
        volume = 1_000 + index % 20 * 20
        candles.append(Candle(
            start + timedelta(minutes=5 * index),
            open_price,
            high,
            low,
            close,
            volume,
        ))
        price = close
    return candles


def test_liangyi_components_are_normalized_and_directional() -> None:
    candles = resample_candles(_signal_candles(), 15)
    efficiency = _market_efficiency_series(candles, 20)
    liangyi = _normalized_liangyi_series(candles, 14, 20)
    assert efficiency[-1] > 0.8
    assert liangyi[-1] > 0


def test_adaptive_liangyi_sixiang_emits_long_signal() -> None:
    settings = load_settings()
    runtime = replace(
        settings.strategies["adaptive_liangyi_sixiang"],
        require_pullback_structure=False,
        adaptive_timeframe_minutes=15,
        momentum_lookback=160,
        minimum_efficiency_ma=0.45,
        minimum_momentum=0.0008,
        minimum_momentum_z=0.35,
        minimum_signal_score=0.75,
    )
    strategy = AdaptiveLiangyiSixiangStrategy(settings.strategy, runtime)
    signal = strategy.evaluate(
        "TESTUSDT",
        _signal_candles(direction=1),
        _trend_candles(direction=1),
    )
    assert signal is not None
    assert signal.side is Side.LONG
    assert signal.strategy_id == "adaptive_liangyi_sixiang"
    assert signal.stop < signal.entry


def test_adaptive_liangyi_sixiang_emits_short_signal() -> None:
    settings = load_settings()
    runtime = replace(
        settings.strategies["adaptive_liangyi_sixiang"],
        require_pullback_structure=False,
        adaptive_timeframe_minutes=15,
        momentum_lookback=160,
        minimum_efficiency_ma=0.45,
        minimum_momentum=0.0008,
        minimum_momentum_z=0.35,
        minimum_signal_score=0.75,
    )
    strategy = AdaptiveLiangyiSixiangStrategy(settings.strategy, runtime)
    signal = strategy.evaluate(
        "TESTUSDT",
        _signal_candles(direction=-1),
        _trend_candles(direction=-1),
    )
    assert signal is not None
    assert signal.side is Side.SHORT
    assert signal.stop > signal.entry
