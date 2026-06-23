from __future__ import annotations

from collections.abc import Sequence

from .config import StrategyConfig
from .domain import Candle, Side, Signal
from .indicators import atr, efficiency_ratio, ema, resample_candles


class BreakoutRetestStrategy:
    """Hourly Donchian momentum breakout for large directional swings."""

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def evaluate(
        self,
        symbol: str,
        signal_candles: Sequence[Candle],
        trend_candles: Sequence[Candle],
    ) -> Signal | None:
        if not signal_candles:
            return None
        close_time = signal_candles[-1].timestamp
        close_hour = (
            close_time.hour
            if close_time.minute + 5 < 60
            else (close_time.hour + 1) % 24
        )
        if (
            close_time.minute + 5 != 60
            or close_hour % self.config.breakout_timeframe_hours
        ):
            return None
        trend_candles = resample_candles(
            trend_candles,
            self.config.breakout_timeframe_hours * 60,
        )
        required = max(
            self.config.breakout_lookback + 2,
            self.config.volume_lookback + 2,
            self.config.breakout_ema_slow_period + 3,
            self.config.atr_period + 2,
        )
        if len(trend_candles) < required:
            return None

        breakout = trend_candles[-1]
        history = trend_candles[
            -(self.config.breakout_lookback + 1):-1
        ]
        volume_history = trend_candles[
            -(self.config.volume_lookback + 1):-1
        ]
        average_volume = (
            sum(c.volume for c in volume_history) / len(volume_history)
        )
        volume_confirmed = (
            breakout.volume
            >= average_volume * self.config.volume_multiplier
        )
        closes = [c.close for c in trend_candles]
        fast = ema(closes, self.config.breakout_ema_fast_period)
        slow = ema(closes, self.config.breakout_ema_slow_period)
        previous_fast = ema(
            closes[:-2], self.config.breakout_ema_fast_period
        )
        volatility = atr(trend_candles, self.config.atr_period)
        atr_ratio = volatility / max(breakout.close, 1e-12)
        if atr_ratio < self.config.minimum_atr_ratio:
            return None
        trend_efficiency = efficiency_ratio(closes, 10)
        body_atr = abs(
            breakout.close - breakout.open
        ) / max(volatility, 1e-12)
        candle_range = max(breakout.high - breakout.low, 1e-12)
        close_location = (
            breakout.close - breakout.low
        ) / candle_range
        previous_high = max(c.high for c in history)
        previous_low = min(c.low for c in history)
        consolidation = history[
            -self.config.breakout_consolidation_bars:
        ]
        consolidation_high = max(c.high for c in consolidation)
        consolidation_low = min(c.low for c in consolidation)
        consolidation_width_atr = (
            consolidation_high - consolidation_low
        ) / max(volatility, 1e-12)
        consolidated = (
            consolidation_width_atr
            <= self.config.maximum_consolidation_atr
        )
        penetration = (
            volatility
            * self.config.minimum_breakout_penetration_atr
        )

        if (
            volume_confirmed
            and consolidated
            and fast > slow
            and fast > previous_fast
            and breakout.close > previous_high + penetration
            and trend_efficiency
            >= self.config.minimum_trend_efficiency
            and body_atr >= self.config.minimum_breakout_body_atr
            and close_location >= 0.70
        ):
            score = self._confidence(
                breakout.volume, average_volume, fast, slow
            )
            if score < self.config.minimum_breakout_score:
                return None
            return Signal(
                symbol,
                Side.LONG,
                breakout.close,
                breakout.close
                - volatility * self.config.stop_atr_multiple,
                score,
                "hourly Donchian volume breakout",
                breakout_level=previous_high,
                strategy_id="breakout_retest",
                score=score,
                invalidation_level=previous_high
                - volatility * 0.20,
            )

        if (
            volume_confirmed
            and consolidated
            and fast < slow
            and fast < previous_fast
            and breakout.close < previous_low - penetration
            and trend_efficiency
            >= self.config.minimum_trend_efficiency
            and body_atr >= self.config.minimum_breakout_body_atr
            and close_location <= 0.30
        ):
            score = self._confidence(
                breakout.volume, average_volume, slow, fast
            )
            if score < self.config.minimum_breakout_score:
                return None
            return Signal(
                symbol,
                Side.SHORT,
                breakout.close,
                breakout.close
                + volatility * self.config.stop_atr_multiple,
                score,
                "hourly Donchian volume breakdown",
                breakout_level=previous_low,
                strategy_id="breakout_retest",
                score=score,
                invalidation_level=previous_low
                + volatility * 0.20,
            )
        return None

    @staticmethod
    def _confidence(
        volume: float,
        average_volume: float,
        leading: float,
        lagging: float,
    ) -> float:
        volume_score = (
            min(volume / average_volume / 3, 0.6)
            if average_volume else 0
        )
        trend_score = min(
            abs(leading - lagging)
            / max(abs(lagging), 1e-12)
            * 10,
            0.4,
        )
        return round(min(volume_score + trend_score, 1.0), 4)
