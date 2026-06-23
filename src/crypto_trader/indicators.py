from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from math import sqrt

from .domain import Candle


def ema(values: Sequence[float], period: int) -> float:
    if period <= 0 or len(values) < period:
        raise ValueError("insufficient values for EMA")
    seed = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    result = seed
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def atr(candles: Sequence[Candle], period: int) -> float:
    if period <= 0 or len(candles) < period + 1:
        raise ValueError("insufficient candles for ATR")
    window = candles[-(period + 1):]
    true_ranges: list[float] = []
    for previous, current in zip(window, window[1:]):
        true_ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return sum(true_ranges) / period


def sma(values: Sequence[float], period: int) -> float:
    if period <= 0 or len(values) < period:
        raise ValueError("insufficient values for SMA")
    return sum(values[-period:]) / period


def standard_deviation(values: Sequence[float], period: int) -> float:
    window = values[-period:]
    mean = sma(window, period)
    return sqrt(sum((value - mean) ** 2 for value in window) / period)


def bollinger_bandwidth(
    values: Sequence[float], period: int = 20, standard_deviations: float = 2.0
) -> float:
    middle = sma(values, period)
    if middle <= 0:
        return float("inf")
    deviation = standard_deviation(values, period)
    return (standard_deviations * 2 * deviation) / middle


def percentile_rank(values: Sequence[float], value: float) -> float:
    if not values:
        return 1.0
    return sum(item <= value for item in values) / len(values)


def atr_series(candles: Sequence[Candle], period: int) -> list[float]:
    if len(candles) < period + 1:
        return []
    return [
        atr(candles[:index], period)
        for index in range(period + 1, len(candles) + 1)
    ]


def efficiency_ratio(values: Sequence[float], period: int) -> float:
    if period <= 1 or len(values) < period + 1:
        return 0.0
    window = values[-(period + 1):]
    displacement = abs(window[-1] - window[0])
    path = sum(
        abs(current - previous)
        for previous, current in zip(window, window[1:])
    )
    return displacement / path if path > 0 else 0.0


def resample_candles(
    candles: Sequence[Candle], interval_minutes: int
) -> list[Candle]:
    """Aggregate complete base candles into a larger UTC interval."""
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    buckets: dict[int, list[Candle]] = {}
    interval_seconds = interval_minutes * 60
    for candle in candles:
        key = int(candle.timestamp.timestamp()) // interval_seconds
        buckets.setdefault(key, []).append(candle)
    if len(candles) < 2:
        return []
    base_seconds = int(
        (
            candles[-1].timestamp - candles[-2].timestamp
        ).total_seconds()
    )
    if base_seconds <= 0 or interval_seconds % base_seconds:
        return []
    expected = interval_seconds // base_seconds
    result: list[Candle] = []
    for group in buckets.values():
        if len(group) != expected:
            continue
        result.append(Candle(
            timestamp=group[0].timestamp,
            open=group[0].open,
            high=max(item.high for item in group),
            low=min(item.low for item in group),
            close=group[-1].close,
            volume=sum(item.volume for item in group),
        ))
    return result


def closes_interval(candle: Candle, interval_minutes: int) -> bool:
    return (
        candle.timestamp + timedelta(minutes=5)
    ).minute % interval_minutes == 0
