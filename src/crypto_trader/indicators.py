from __future__ import annotations

from collections.abc import Sequence

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
    true_ranges: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        true_ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return sum(true_ranges[-period:]) / period

