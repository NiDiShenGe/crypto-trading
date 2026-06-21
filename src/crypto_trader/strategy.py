from __future__ import annotations

from collections.abc import Sequence

from .config import StrategyConfig
from .domain import Candle, Side, Signal
from .indicators import atr, ema


class BreakoutRetestStrategy:
    """Volume breakout with higher-timeframe trend confirmation and retest."""

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def evaluate(
        self,
        symbol: str,
        signal_candles: Sequence[Candle],
        trend_candles: Sequence[Candle],
    ) -> Signal | None:
        minimum_signal = max(
            self.config.breakout_lookback + 2,
            self.config.volume_lookback + 2,
            self.config.atr_period + 2,
        )
        minimum_trend = self.config.ema_slow_period + 1
        if len(signal_candles) < minimum_signal or len(trend_candles) < minimum_trend:
            return None

        breakout = signal_candles[-2]
        retest = signal_candles[-1]
        history = signal_candles[-(self.config.breakout_lookback + 2):-2]
        volume_history = signal_candles[-(self.config.volume_lookback + 2):-2]
        average_volume = sum(c.volume for c in volume_history) / len(volume_history)
        volume_confirmed = breakout.volume >= average_volume * self.config.volume_multiplier

        trend_closes = [c.close for c in trend_candles]
        fast = ema(trend_closes, self.config.ema_fast_period)
        slow = ema(trend_closes, self.config.ema_slow_period)
        volatility = atr(signal_candles, self.config.atr_period)

        previous_high = max(c.high for c in history)
        long_breakout = breakout.close > previous_high
        long_retest = retest.low <= previous_high and retest.close > previous_high
        if volume_confirmed and fast > slow and long_breakout and long_retest:
            return Signal(
                symbol=symbol,
                side=Side.LONG,
                entry=retest.close,
                stop=retest.close - volatility * self.config.stop_atr_multiple,
                confidence=self._confidence(breakout.volume, average_volume, fast, slow),
                reason="volume breakout, bullish higher timeframe, successful retest",
            )

        previous_low = min(c.low for c in history)
        short_breakout = breakout.close < previous_low
        short_retest = retest.high >= previous_low and retest.close < previous_low
        if volume_confirmed and fast < slow and short_breakout and short_retest:
            return Signal(
                symbol=symbol,
                side=Side.SHORT,
                entry=retest.close,
                stop=retest.close + volatility * self.config.stop_atr_multiple,
                confidence=self._confidence(breakout.volume, average_volume, slow, fast),
                reason="volume breakdown, bearish higher timeframe, successful retest",
            )
        return None

    @staticmethod
    def _confidence(volume: float, average_volume: float, leading: float, lagging: float) -> float:
        volume_score = min(volume / average_volume / 3, 0.6) if average_volume else 0
        trend_score = min(abs(leading - lagging) / max(abs(lagging), 1e-12) * 10, 0.4)
        return round(min(volume_score + trend_score, 1.0), 4)

