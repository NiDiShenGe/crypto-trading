from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from .config import StrategyConfig, StrategyRuntimeConfig
from .domain import Candle, Side, Signal
from .indicators import (
    atr,
    closes_interval,
    efficiency_ratio,
    ema,
    percentile_rank,
    resample_candles,
)


class TrendPullbackStrategy:
    strategy_id = "trend_pullback"

    def __init__(
        self,
        base: StrategyConfig,
        runtime: StrategyRuntimeConfig,
    ) -> None:
        self.base = base
        self.runtime = runtime

    def evaluate(
        self,
        symbol: str,
        signal_candles: Sequence[Candle],
        trend_candles: Sequence[Candle],
    ) -> Signal | None:
        if self.runtime.use_four_hour_pullback:
            return self._evaluate_four_hour_pullback(
                symbol, signal_candles, trend_candles
            )
        if self.runtime.use_breakout_level_retest:
            return self._evaluate_breakout_level_retest(
                symbol, signal_candles, trend_candles
            )
        if not signal_candles:
            return None
        spacing = (
            (
                signal_candles[-1].timestamp
                - signal_candles[-2].timestamp
            ).total_seconds()
            if len(signal_candles) >= 2 else 0
        )
        if spacing < 900:
            if not closes_interval(signal_candles[-1], 15):
                return None
            signal_candles = resample_candles(signal_candles, 15)
        signal_required = max(
            self.runtime.pullback_max_bars * 2 + 5,
            self.base.atr_period + 2,
        )
        trend_required = self.base.ema_slow_period + 5
        if (
            len(signal_candles) < signal_required
            or len(trend_candles) < trend_required
        ):
            return None

        trend_closes = [c.close for c in trend_candles]
        fast = ema(trend_closes, self.base.ema_fast_period)
        slow = ema(trend_closes, self.base.ema_slow_period)
        fast_series = _ema_series(
            trend_closes, self.base.ema_fast_period
        )
        slow_series = _ema_series(
            trend_closes, self.base.ema_slow_period
        )
        fast_previous = ema(trend_closes[:-2], self.base.ema_fast_period)
        slow_previous = ema(trend_closes[:-2], self.base.ema_slow_period)
        confirmation = signal_candles[-1]
        volatility = atr(signal_candles, self.base.atr_period)
        trend_volatility = atr(trend_candles, self.base.atr_period)
        if (
            volatility / max(confirmation.close, 1e-12)
            < self.base.minimum_atr_ratio
        ):
            return None
        trend_efficiency = efficiency_ratio(trend_closes, 10)
        four_hour = resample_candles(trend_candles, 240)
        four_hour_ready = len(four_hour) >= 32
        if self.runtime.use_four_hour_confirmation and not four_hour_ready:
            return None
        if four_hour_ready:
            four_closes = [c.close for c in four_hour]
            four_fast = ema(four_closes, 10)
            four_slow = ema(four_closes, 30)
            four_previous_fast = ema(four_closes[:-2], 10)
        else:
            four_fast = four_slow = 0.0
            four_previous_fast = 0.0
        signal_closes = [c.close for c in signal_candles]
        signal_fast = ema(
            signal_closes, self.base.ema_fast_period
        )
        signal_fast_series = _ema_series(
            signal_closes, self.base.ema_fast_period
        )
        previous_signal_fast = ema(
            signal_closes[:-1], self.base.ema_fast_period
        )

        pullbacks = list(signal_candles[-(self.runtime.pullback_max_bars + 1):-1])
        recent = pullbacks[-self.runtime.confirmation_lookback:]
        prior = signal_candles[
            -(self.runtime.pullback_max_bars * 2 + 1):
            -(self.runtime.pullback_max_bars + 1)
        ]
        pullback_volume = sum(c.volume for c in pullbacks) / len(pullbacks)
        prior_volume = sum(c.volume for c in prior) / len(prior) if prior else pullback_volume
        contracted = pullback_volume <= prior_volume * self.runtime.volume_contraction_ratio
        prior_high = max(c.high for c in prior) if prior else max(
            c.high for c in pullbacks
        )
        prior_low = min(c.low for c in prior) if prior else min(
            c.low for c in pullbacks
        )
        structure_low = min(c.low for c in pullbacks)
        structure_high = max(c.high for c in pullbacks)
        bullish_age = _trend_alignment_age(
            fast_series, slow_series, above=True
        )
        bearish_age = _trend_alignment_age(
            fast_series, slow_series, above=False
        )
        bullish_impulse = self._recent_impulse(
            trend_candles,
            fast_series,
            slow_series,
            volatility=trend_volatility,
            above=True,
        )
        bearish_impulse = self._recent_impulse(
            trend_candles,
            fast_series,
            slow_series,
            volatility=trend_volatility,
            above=False,
        )
        pullback_start = len(signal_candles) - len(pullbacks) - 1
        first_bullish_pullback = self._is_first_pullback(
            signal_candles,
            signal_fast_series,
            bullish_impulse,
            pullback_start,
            above=True,
        )
        first_bearish_pullback = self._is_first_pullback(
            signal_candles,
            signal_fast_series,
            bearish_impulse,
            pullback_start,
            above=False,
        )

        bullish_trend = (
            bullish_impulse is not None
            and fast > slow
            and fast > fast_previous
            and slow >= slow_previous
            and trend_closes[-1] > fast
            and (fast - slow) / max(trend_volatility, 1e-12) >= 0.50
            and trend_efficiency >= self.runtime.minimum_trend_efficiency
            and 2 <= bullish_age <= self.runtime.maximum_trend_age_bars
            and (
                not self.runtime.use_four_hour_confirmation
                or (
                    four_fast > four_slow
                    and four_fast > four_previous_fast
                    and four_closes[-1] > four_fast
                )
            )
        )
        bullish_pullback = (
            any(c.low <= previous_signal_fast for c in pullbacks)
            and first_bullish_pullback
            and structure_low > slow
            and 0.50
            <= (prior_high - structure_low) / max(volatility, 1e-12)
            <= 3.0
        )
        bullish_confirm = (
            confirmation.close > max(c.high for c in recent)
            and confirmation.close > confirmation.open
            and confirmation.close > signal_fast
            and (
                confirmation.close - confirmation.open
            ) / max(volatility, 1e-12)
            >= self.runtime.minimum_breakout_body_atr
            and confirmation.volume
            >= pullback_volume * self.runtime.confirmation_volume_multiplier
        )
        if (
            bullish_trend
            and bullish_pullback
            and contracted
            and bullish_confirm
        ):
            stop = min(
                structure_low - volatility * self.runtime.atr_buffer,
                confirmation.close
                - volatility * self.base.stop_atr_multiple,
            )
            score = self._score(fast, slow, fast_previous, contracted, confirmation, volatility)
            if score < self.runtime.minimum_signal_score:
                return None
            return Signal(
                symbol,
                Side.LONG,
                confirmation.close,
                stop,
                score,
                "hourly uptrend, pullback into hourly EMA zone, bullish restart",
                breakout_level=max(c.high for c in recent),
                strategy_id=self.strategy_id,
                score=score,
                invalidation_level=structure_low,
            )

        bearish_trend = (
            bearish_impulse is not None
            and fast < slow
            and fast < fast_previous
            and slow <= slow_previous
            and trend_closes[-1] < fast
            and (slow - fast) / max(trend_volatility, 1e-12) >= 0.50
            and trend_efficiency >= self.runtime.minimum_trend_efficiency
            and 2 <= bearish_age <= self.runtime.maximum_trend_age_bars
            and (
                not self.runtime.use_four_hour_confirmation
                or (
                    four_fast < four_slow
                    and four_fast < four_previous_fast
                    and four_closes[-1] < four_fast
                )
            )
        )
        bearish_pullback = (
            any(c.high >= previous_signal_fast for c in pullbacks)
            and first_bearish_pullback
            and structure_high < slow
            and 0.50
            <= (structure_high - prior_low) / max(volatility, 1e-12)
            <= 3.0
        )
        bearish_confirm = (
            confirmation.close < min(c.low for c in recent)
            and confirmation.close < confirmation.open
            and confirmation.close < signal_fast
            and (
                confirmation.open - confirmation.close
            ) / max(volatility, 1e-12)
            >= self.runtime.minimum_breakout_body_atr
            and confirmation.volume
            >= pullback_volume * self.runtime.confirmation_volume_multiplier
        )
        if (
            bearish_trend
            and bearish_pullback
            and contracted
            and bearish_confirm
        ):
            stop = max(
                structure_high + volatility * self.runtime.atr_buffer,
                confirmation.close
                + volatility * self.base.stop_atr_multiple,
            )
            score = self._score(slow, fast, fast_previous, contracted, confirmation, volatility)
            if score < self.runtime.minimum_signal_score:
                return None
            return Signal(
                symbol,
                Side.SHORT,
                confirmation.close,
                stop,
                score,
                "hourly downtrend, pullback into hourly EMA zone, bearish restart",
                breakout_level=min(c.low for c in recent),
                strategy_id=self.strategy_id,
                score=score,
                invalidation_level=structure_high,
            )
        return None

    def _evaluate_four_hour_pullback(
        self,
        symbol: str,
        signal_candles: Sequence[Candle],
        hourly: Sequence[Candle],
    ) -> Signal | None:
        if len(signal_candles) < 2 or len(hourly) < 80:
            return None
        signal_close = signal_candles[-1].timestamp + timedelta(
            minutes=15
        )
        if signal_close.minute != 0:
            return None
        four_hour = resample_candles(hourly, 240)
        if len(four_hour) < 40:
            return None

        pullback_bars = self.runtime.higher_timeframe_pullback_bars
        confirmation = hourly[-1]
        pullbacks = list(hourly[-(pullback_bars + 1):-1])
        if len(pullbacks) < pullback_bars:
            return None
        recent = pullbacks[-min(
            self.runtime.confirmation_lookback,
            len(pullbacks),
        ):]
        hourly_closes = [c.close for c in hourly]
        hourly_fast = ema(
            hourly_closes, self.base.ema_fast_period
        )
        hourly_previous_fast = ema(
            hourly_closes[:-1], self.base.ema_fast_period
        )
        hourly_volatility = atr(hourly, self.base.atr_period)
        four_closes = [c.close for c in four_hour]
        four_fast_series = _ema_series(four_closes, 10)
        four_slow_series = _ema_series(four_closes, 30)
        four_fast = four_fast_series[-1]
        four_slow = four_slow_series[-1]
        four_volatility = atr(four_hour, self.base.atr_period)
        four_efficiency = efficiency_ratio(four_closes, 10)
        bullish_age = _trend_alignment_age(
            four_fast_series, four_slow_series, above=True
        )
        bearish_age = _trend_alignment_age(
            four_fast_series, four_slow_series, above=False
        )
        pullback_volume = (
            sum(c.volume for c in pullbacks) / len(pullbacks)
        )
        body = abs(
            confirmation.close - confirmation.open
        ) / max(hourly_volatility, 1e-12)
        volume_ratio = confirmation.volume / max(
            pullback_volume, 1e-12
        )
        separation = abs(
            four_fast - four_slow
        ) / max(four_volatility, 1e-12)
        score = min(
            0.50
            + min(separation / 3 * 0.20, 0.20)
            + min(body / 2 * 0.15, 0.15)
            + min(volume_ratio / 3 * 0.15, 0.15),
            1.0,
        )
        if (
            body < self.runtime.minimum_breakout_body_atr
            or volume_ratio
            < self.runtime.confirmation_volume_multiplier
            or score < self.runtime.minimum_signal_score
        ):
            return None

        bullish = (
            four_fast > four_slow
            and four_fast > four_fast_series[-3]
            and four_slow >= four_slow_series[-3]
            and four_closes[-1] > four_fast
            and separation >= 0.50
            and four_efficiency
            >= self.runtime.minimum_trend_efficiency
            and 2 <= bullish_age
            <= self.runtime.maximum_higher_timeframe_trend_age
            and any(
                c.low <= hourly_previous_fast
                for c in pullbacks
            )
            and min(c.low for c in pullbacks) > four_slow
            and confirmation.close > max(c.high for c in recent)
            and confirmation.close > confirmation.open
            and confirmation.close > hourly_fast
        )
        bearish = (
            four_fast < four_slow
            and four_fast < four_fast_series[-3]
            and four_slow <= four_slow_series[-3]
            and four_closes[-1] < four_fast
            and separation >= 0.50
            and four_efficiency
            >= self.runtime.minimum_trend_efficiency
            and 2 <= bearish_age
            <= self.runtime.maximum_higher_timeframe_trend_age
            and any(
                c.high >= hourly_previous_fast
                for c in pullbacks
            )
            and max(c.high for c in pullbacks) < four_slow
            and confirmation.close < min(c.low for c in recent)
            and confirmation.close < confirmation.open
            and confirmation.close < hourly_fast
        )
        if not bullish and not bearish:
            return None
        side = Side.LONG if bullish else Side.SHORT
        if side is Side.LONG:
            structure = min(c.low for c in pullbacks)
            stop = min(
                structure
                - hourly_volatility * self.runtime.atr_buffer,
                confirmation.close
                - hourly_volatility
                * self.base.stop_atr_multiple,
            )
        else:
            structure = max(c.high for c in pullbacks)
            stop = max(
                structure
                + hourly_volatility * self.runtime.atr_buffer,
                confirmation.close
                + hourly_volatility
                * self.base.stop_atr_multiple,
            )
        return Signal(
            symbol,
            side,
            confirmation.close,
            stop,
            round(score, 4),
            "4h trend with a confirmed 1h pullback continuation",
            breakout_level=(
                max(c.high for c in recent)
                if side is Side.LONG
                else min(c.low for c in recent)
            ),
            strategy_id=self.strategy_id,
            score=round(score, 4),
            invalidation_level=structure,
        )

    def _evaluate_breakout_level_retest(
        self,
        symbol: str,
        signal_candles: Sequence[Candle],
        trend_candles: Sequence[Candle],
    ) -> Signal | None:
        if len(signal_candles) < 60:
            return None
        spacing = (
            signal_candles[-1].timestamp
            - signal_candles[-2].timestamp
        ).total_seconds()
        if spacing < 900:
            if not closes_interval(signal_candles[-1], 15):
                return None
            signal_candles = resample_candles(signal_candles, 15)
        two_hour = resample_candles(trend_candles, 120)
        required = max(
            self.runtime.impulse_breakout_lookback
            + self.runtime.impulse_lookback_bars
            + 2,
            35,
        )
        if len(two_hour) < required or len(signal_candles) < 35:
            return None

        signal_volatility = atr(
            signal_candles, self.base.atr_period
        )
        trend_volatility = atr(two_hour, self.base.atr_period)
        confirmation = signal_candles[-1]
        pullbacks = list(
            signal_candles[
                -(self.runtime.pullback_max_bars + 1):-1
            ]
        )
        recent = pullbacks[-self.runtime.confirmation_lookback:]
        pullback_volume = (
            sum(c.volume for c in pullbacks) / len(pullbacks)
        )
        closes = [c.close for c in two_hour]
        fast = _ema_series(closes, 10)
        slow = _ema_series(closes, 30)

        for above, side in (
            (True, Side.LONG),
            (False, Side.SHORT),
        ):
            impulse = self._find_breakout_impulse(
                two_hour,
                fast,
                slow,
                trend_volatility,
                above=above,
            )
            if impulse is None:
                continue
            event, level, volume_ratio, body_atr = impulse
            event_close = event.timestamp.replace(
                minute=0, second=0, microsecond=0
            )
            event_close = event_close + timedelta(hours=2)
            if above:
                pullback_depth = (
                    max(c.high for c in pullbacks)
                    - min(c.low for c in pullbacks)
                ) / max(signal_volatility, 1e-12)
                held_level = min(
                    candle.low for candle in pullbacks
                ) > level + signal_volatility * 0.10
                counter_move = any(
                    candle.close < candle.open
                    for candle in pullbacks
                )
                confirmed = (
                    confirmation.close > max(c.high for c in recent)
                    and confirmation.close > confirmation.open
                    and confirmation.close > level
                )
                body = (
                    confirmation.close - confirmation.open
                ) / max(signal_volatility, 1e-12)
            else:
                pullback_depth = (
                    max(c.high for c in pullbacks)
                    - min(c.low for c in pullbacks)
                ) / max(signal_volatility, 1e-12)
                held_level = max(
                    candle.high for candle in pullbacks
                ) < level - signal_volatility * 0.10
                counter_move = any(
                    candle.close > candle.open
                    for candle in pullbacks
                )
                confirmed = (
                    confirmation.close < min(c.low for c in recent)
                    and confirmation.close < confirmation.open
                    and confirmation.close < level
                )
                body = (
                    confirmation.open - confirmation.close
                ) / max(signal_volatility, 1e-12)
            if (
                not 0.50 <= pullback_depth <= 2.50
                or not held_level
                or not counter_move
                or not confirmed
                or body < self.runtime.minimum_breakout_body_atr
                or confirmation.volume
                < pullback_volume
                * self.runtime.confirmation_volume_multiplier
            ):
                continue

            trend_separation = abs(
                fast[-1] - slow[-1]
            ) / max(trend_volatility, 1e-12)
            score = min(
                0.50
                + min(volume_ratio / 3 * 0.20, 0.20)
                + min(body_atr / 2 * 0.15, 0.15)
                + min(trend_separation / 3 * 0.10, 0.10)
                + min(body / 2 * 0.15, 0.15),
                1.0,
            )
            if score < self.runtime.minimum_signal_score:
                continue
            if side is Side.LONG:
                structure = min(c.low for c in pullbacks)
                stop = min(
                    structure
                    - signal_volatility * self.runtime.atr_buffer,
                    confirmation.close
                    - signal_volatility
                    * self.base.stop_atr_multiple,
                )
                invalidation = level - signal_volatility * 0.20
            else:
                structure = max(c.high for c in pullbacks)
                stop = max(
                    structure
                    + signal_volatility * self.runtime.atr_buffer,
                    confirmation.close
                    + signal_volatility
                    * self.base.stop_atr_multiple,
                )
                invalidation = level + signal_volatility * 0.20
            return Signal(
                symbol,
                side,
                confirmation.close,
                stop,
                round(score, 4),
                "first 15m retest of a confirmed 2h volume breakout",
                breakout_level=level,
                strategy_id=self.strategy_id,
                score=round(score, 4),
                invalidation_level=invalidation,
            )
        return None

    def _find_breakout_impulse(
        self,
        candles: Sequence[Candle],
        fast: Sequence[float],
        slow: Sequence[float],
        volatility: float,
        *,
        above: bool,
    ) -> tuple[Candle, float, float, float] | None:
        lookback = self.runtime.impulse_breakout_lookback
        maximum_events = self.runtime.impulse_lookback_bars
        start = max(lookback, len(candles) - maximum_events - 1)
        for index in range(len(candles) - 2, start - 1, -1):
            history = candles[index - lookback:index]
            if len(history) < lookback:
                continue
            candle = candles[index]
            level = (
                max(c.high for c in history)
                if above else min(c.low for c in history)
            )
            average_volume = sum(c.volume for c in history) / len(history)
            volume_ratio = candle.volume / max(
                average_volume, 1e-12
            )
            body_atr = abs(
                candle.close - candle.open
            ) / max(volatility, 1e-12)
            candle_range = max(candle.high - candle.low, 1e-12)
            close_location = (
                candle.close - candle.low
            ) / candle_range
            aligned = (
                fast[index] > slow[index]
                if above else fast[index] < slow[index]
            )
            broke = (
                candle.close > level
                and close_location >= 0.70
                if above
                else candle.close < level
                and close_location <= 0.30
            )
            if (
                aligned
                and broke
                and volume_ratio
                >= self.runtime.impulse_volume_multiplier
                and body_atr
                >= self.runtime.minimum_impulse_body_atr
            ):
                return candle, level, volume_ratio, body_atr
        return None

    def _recent_impulse(
        self,
        candles: Sequence[Candle],
        fast: Sequence[float],
        slow: Sequence[float],
        *,
        volatility: float,
        above: bool,
    ) -> datetime | None:
        breakout_lookback = self.runtime.impulse_breakout_lookback
        start = max(
            breakout_lookback,
            len(candles) - self.runtime.impulse_lookback_bars,
        )
        for index in range(len(candles) - 2, start - 1, -1):
            history = candles[index - breakout_lookback:index]
            if len(history) < breakout_lookback:
                continue
            candle = candles[index]
            average_volume = sum(c.volume for c in history) / len(history)
            body = abs(candle.close - candle.open) / max(
                volatility, 1e-12
            )
            candle_range = max(candle.high - candle.low, 1e-12)
            close_location = (
                candle.close - candle.low
            ) / candle_range
            aligned = (
                fast[index] > slow[index]
                if above else fast[index] < slow[index]
            )
            broke = (
                (
                    candle.close > max(c.high for c in history)
                    and close_location >= 0.70
                )
                if above
                else (
                    candle.close < min(c.low for c in history)
                    and close_location <= 0.30
                )
            )
            if (
                aligned
                and broke
                and body >= self.runtime.minimum_impulse_body_atr
                and candle.volume
                >= average_volume
                * self.runtime.impulse_volume_multiplier
            ):
                return candle.timestamp
        return None

    @staticmethod
    def _is_first_pullback(
        candles: Sequence[Candle],
        fast: Sequence[float],
        impulse_time: datetime | None,
        pullback_start: int,
        *,
        above: bool,
    ) -> bool:
        if impulse_time is None:
            return False
        for index, candle in enumerate(candles[:pullback_start]):
            if candle.timestamp <= impulse_time:
                continue
            touched = (
                candle.low <= fast[index]
                if above else candle.high >= fast[index]
            )
            if touched:
                return False
        return True

    @staticmethod
    def _score(
        leading: float,
        lagging: float,
        previous: float,
        contracted: bool,
        candle: Candle,
        volatility: float,
    ) -> float:
        separation = min(abs(leading - lagging) / max(abs(lagging), 1e-12) * 15, 0.45)
        slope = min(abs(leading - previous) / max(abs(previous), 1e-12) * 20, 0.25)
        impulse = min(abs(candle.close - candle.open) / max(volatility, 1e-12) * 0.2, 0.2)
        return round(min(separation + slope + impulse + (0.1 if contracted else 0), 1), 4)


class VolatilitySqueezeStrategy:
    strategy_id = "volatility_squeeze"

    def __init__(
        self,
        base: StrategyConfig,
        runtime: StrategyRuntimeConfig,
    ) -> None:
        self.base = base
        self.runtime = runtime

    def compression_score(self, candles: Sequence[Candle]) -> float:
        candles = _as_interval(
            candles, self.runtime.squeeze_timeframe_minutes
        )
        state = self._compression_state(candles)
        if state is None:
            return 0.0
        bandwidth_rank, atr_rank, volume_ratio, _, _ = state
        return round(
            max(0.0, 1 - bandwidth_rank) * 0.45
            + max(0.0, 1 - atr_rank) * 0.4
            + max(0.0, 1 - min(volume_ratio, 1)) * 0.15,
            4,
        )

    def setup_score(self, candles: Sequence[Candle]) -> float:
        """Rank completed squeeze-breakout-retest setups near relaunch."""
        if self.runtime.squeeze_use_trend_continuation:
            return self.continuation_setup_score(candles)
        setup_candles = _as_interval(
            candles, self.runtime.squeeze_timeframe_minutes
        )
        setup = self._find_retest_setup(setup_candles)
        if setup is None:
            return 0.0
        current = setup_candles[-1]
        volatility = setup["volatility"]
        direction = 1 if setup["side"] is Side.LONG else -1
        distance = (
            (setup["relaunch_level"] - current.close) * direction
            / max(volatility, 1e-12)
        )
        proximity = max(0.0, 1 - max(distance, 0.0) / 2)
        return round(
            min(setup["quality"] * 0.8 + proximity * 0.2, 1),
            4,
        )

    def continuation_setup_score(
        self,
        hourly: Sequence[Candle],
    ) -> float:
        pullback_bars = self.runtime.higher_timeframe_pullback_bars
        prior_bars = max(pullback_bars * 2, 8)
        four_hour = resample_candles(hourly, 240)
        if (
            len(hourly) < pullback_bars + prior_bars + 2
            or len(four_hour) < 32
        ):
            return 0.0
        closes = [candle.close for candle in four_hour]
        fast = ema(closes, 10)
        slow = ema(closes, 30)
        previous_fast = ema(closes[:-2], 10)
        trend_volatility = atr(four_hour, self.base.atr_period)
        efficiency = efficiency_ratio(closes, 10)
        bullish = (
            fast > slow
            and fast > previous_fast
            and closes[-1] > fast
        )
        bearish = (
            self.runtime.squeeze_allow_short
            and fast < slow
            and fast < previous_fast
            and closes[-1] < fast
        )
        if (
            (not bullish and not bearish)
            or efficiency < self.runtime.minimum_trend_efficiency
        ):
            return 0.0
        pullbacks = list(hourly[-(pullback_bars + 1):-1])
        prior = list(
            hourly[
                -(pullback_bars + prior_bars + 1):
                -(pullback_bars + 1)
            ]
        )
        pullback_range = sum(
            candle.high - candle.low for candle in pullbacks
        ) / len(pullbacks)
        prior_range = sum(
            candle.high - candle.low for candle in prior
        ) / len(prior)
        pullback_volume = sum(
            candle.volume for candle in pullbacks
        ) / len(pullbacks)
        prior_volume = sum(
            candle.volume for candle in prior
        ) / len(prior)
        range_ratio = pullback_range / max(prior_range, 1e-12)
        volume_ratio = pullback_volume / max(prior_volume, 1e-12)
        if (
            range_ratio
            > self.runtime.squeeze_range_contraction_ratio
            or volume_ratio
            > self.runtime.squeeze_pullback_volume_ratio
        ):
            return 0.0
        separation = abs(fast - slow) / max(
            trend_volatility, 1e-12
        )
        return round(
            min(
                min(separation / 3, 1) * 0.40
                + min(efficiency / 0.6, 1) * 0.25
                + max(0.0, 1 - range_ratio / 1.2) * 0.15
                + max(0.0, 1 - volume_ratio) * 0.20,
                1.0,
            ),
            4,
        )

    def evaluate(
        self,
        symbol: str,
        signal_candles: Sequence[Candle],
        trend_candles: Sequence[Candle],
    ) -> Signal | None:
        if self.runtime.squeeze_use_trend_continuation:
            return self._evaluate_trend_continuation(
                symbol,
                signal_candles,
                trend_candles,
            )
        if not signal_candles:
            return None
        interval = self.runtime.squeeze_timeframe_minutes
        spacing_seconds = (
            (
                signal_candles[-1].timestamp
                - signal_candles[-2].timestamp
            ).total_seconds()
            if len(signal_candles) >= 2
            else 0
        )
        if (
            spacing_seconds < interval * 60
            and not closes_interval(signal_candles[-1], interval)
        ):
            return None
        setup_candles = _as_interval(signal_candles, interval)
        setup = self._find_retest_setup(setup_candles)
        if setup is None:
            return None
        if (
            setup["side"] is Side.SHORT
            and not self.runtime.squeeze_allow_short
        ):
            return None
        if (
            self.runtime.use_four_hour_confirmation
            and not self._higher_timeframe_trend_allows(
                trend_candles,
                setup["side"],
            )
        ):
            return None
        confirmation = setup_candles[-1]
        volatility = setup["volatility"]
        if (
            volatility / max(confirmation.close, 1e-12)
            < self.base.minimum_atr_ratio
        ):
            return None
        body_atr = abs(
            confirmation.close - confirmation.open
        ) / max(
            volatility, 1e-12
        )
        recent = setup_candles[-21:-1]
        average_volume = sum(c.volume for c in recent) / len(recent)
        second_volume_ratio = confirmation.volume / max(
            average_volume, 1e-12
        )
        candle_range = max(
            confirmation.high - confirmation.low, 1e-12
        )
        close_location = (
            confirmation.close - confirmation.low
        ) / candle_range
        side = setup["side"]
        relaunched = (
            confirmation.close > setup["relaunch_level"]
            and confirmation.close > confirmation.open
            and close_location >= 0.70
            if side is Side.LONG
            else confirmation.close < setup["relaunch_level"]
            and confirmation.close < confirmation.open
            and close_location <= 0.30
        )
        if (
            not relaunched
            or body_atr < self.runtime.squeeze_second_body_atr
            or second_volume_ratio
            < self.runtime.squeeze_second_volume_multiplier
        ):
            return None
        score = round(min(
            setup["quality"] * 0.75
            + min(second_volume_ratio / 3, 1) * 0.15
            + min(body_atr / 2, 1) * 0.10,
            1,
        ), 4)
        if score < self.runtime.minimum_signal_score:
            return None
        if side is Side.LONG:
            stop = min(
                setup["retest_extreme"]
                - volatility * self.runtime.atr_buffer,
                confirmation.close
                - volatility * self.base.stop_atr_multiple,
            )
            invalidation = (
                setup["relaunch_level"]
                - volatility
                * self.runtime.squeeze_relaunch_failure_atr_buffer
            )
        else:
            stop = max(
                setup["retest_extreme"]
                + volatility * self.runtime.atr_buffer,
                confirmation.close
                + volatility * self.base.stop_atr_multiple,
            )
            invalidation = (
                setup["relaunch_level"]
                + volatility
                * self.runtime.squeeze_relaunch_failure_atr_buffer
            )
        return Signal(
            symbol,
            side,
            confirmation.close,
            stop,
            score,
            "squeeze breakout held its retest and relaunched on volume",
            breakout_level=setup["relaunch_level"],
            strategy_id=self.strategy_id,
            score=score,
            invalidation_level=invalidation,
        )

    def _evaluate_trend_continuation(
        self,
        symbol: str,
        signal_candles: Sequence[Candle],
        hourly: Sequence[Candle],
    ) -> Signal | None:
        pullback_bars = self.runtime.higher_timeframe_pullback_bars
        prior_bars = max(pullback_bars * 2, 8)
        if len(hourly) < pullback_bars + prior_bars + 2:
            return None
        pullbacks = list(hourly[-(pullback_bars + 1):-1])
        prior = list(
            hourly[
                -(pullback_bars + prior_bars + 1):
                -(pullback_bars + 1)
            ]
        )
        if len(pullbacks) < pullback_bars or len(prior) < prior_bars:
            return None
        pullback_range = sum(
            candle.high - candle.low for candle in pullbacks
        ) / len(pullbacks)
        prior_range = sum(
            candle.high - candle.low for candle in prior
        ) / len(prior)
        pullback_volume = sum(
            candle.volume for candle in pullbacks
        ) / len(pullbacks)
        prior_volume = sum(
            candle.volume for candle in prior
        ) / len(prior)
        range_ratio = pullback_range / max(prior_range, 1e-12)
        volume_ratio = pullback_volume / max(prior_volume, 1e-12)
        if (
            range_ratio
            > self.runtime.squeeze_range_contraction_ratio
            or volume_ratio
            > self.runtime.squeeze_pullback_volume_ratio
        ):
            return None

        continuation = TrendPullbackStrategy(
            self.base,
            self.runtime,
        )._evaluate_four_hour_pullback(
            symbol,
            signal_candles,
            hourly,
        )
        if continuation is None:
            return None
        if (
            continuation.side is Side.SHORT
            and not self.runtime.squeeze_allow_short
        ):
            return None
        compression_quality = (
            max(0.0, 1 - min(range_ratio, 1.0)) * 0.5
            + max(0.0, 1 - min(volume_ratio, 1.0)) * 0.5
        )
        score = round(
            min(
                continuation.score
                + compression_quality * 0.10,
                1.0,
            ),
            4,
        )
        return Signal(
            symbol=continuation.symbol,
            side=continuation.side,
            entry=continuation.entry,
            stop=continuation.stop,
            confidence=score,
            reason=(
                "4h trend, contracting 1h pullback, "
                "volume-backed continuation"
            ),
            breakout_level=continuation.breakout_level,
            strategy_id=self.strategy_id,
            score=score,
            invalidation_level=continuation.invalidation_level,
        )

    def _find_retest_setup(
        self,
        candles: Sequence[Candle],
    ) -> dict | None:
        if len(candles) < self.runtime.lookback + 30:
            return None
        current_index = len(candles) - 1
        earliest = max(
            21,
            current_index
            - self.runtime.squeeze_breakout_max_age_bars,
        )
        for breakout_index in range(
            current_index - 2,
            earliest - 1,
            -1,
        ):
            prefix = candles[:breakout_index + 1]
            state = self._compression_state(prefix)
            if state is None:
                continue
            (
                bandwidth_rank,
                atr_rank,
                compression_volume,
                range_high,
                range_low,
            ) = state
            if (
                bandwidth_rank > self.runtime.bandwidth_percentile
                or atr_rank > self.runtime.atr_percentile
                or compression_volume
                > self.runtime.maximum_compression_volume_ratio
            ):
                continue
            breakout = candles[breakout_index]
            history = candles[max(0, breakout_index - 20):breakout_index]
            if len(history) < 20:
                continue
            volatility = atr(prefix, self.base.atr_period)
            average_volume = sum(c.volume for c in history) / len(history)
            first_volume_ratio = breakout.volume / max(
                average_volume, 1e-12
            )
            body_atr = abs(
                breakout.close - breakout.open
            ) / max(volatility, 1e-12)
            candle_range = max(
                breakout.high - breakout.low, 1e-12
            )
            close_location = (
                breakout.close - breakout.low
            ) / candle_range
            if (
                first_volume_ratio
                < self.runtime.breakout_volume_multiplier
                or body_atr
                < self.runtime.minimum_breakout_body_atr
            ):
                continue
            if (
                breakout.close > range_high
                and close_location >= 0.70
            ):
                side = Side.LONG
                level = range_high
            elif (
                breakout.close < range_low
                and close_location <= 0.30
            ):
                side = Side.SHORT
                level = range_low
            else:
                continue
            post = list(candles[breakout_index + 1:current_index])
            if (
                len(post) < 1
                or len(post) > self.runtime.squeeze_retest_max_bars
            ):
                continue
            retest_index = None
            failure_buffer = (
                volatility
                * self.runtime.squeeze_failure_atr_buffer
            )
            touch_buffer = (
                volatility
                * self.runtime.squeeze_retest_atr_buffer
            )
            for index, candle in enumerate(post):
                failed = (
                    candle.close < level - failure_buffer
                    if side is Side.LONG
                    else candle.close > level + failure_buffer
                )
                if failed:
                    retest_index = None
                    break
                touched = (
                    candle.low <= level + touch_buffer
                    if side is Side.LONG
                    else candle.high >= level - touch_buffer
                )
                if touched and retest_index is None:
                    retest_index = index
            if retest_index is None:
                continue
            retest_and_hold = post[retest_index:]
            average_retest_volume = sum(
                candle.volume for candle in retest_and_hold
            ) / len(retest_and_hold)
            if (
                average_retest_volume
                > breakout.volume
                * self.runtime.squeeze_retest_volume_ratio
            ):
                continue
            close_buffer = (
                volatility
                * self.runtime.squeeze_retest_close_atr_buffer
            )
            held_on_close = (
                all(
                    candle.close >= level - close_buffer
                    for candle in retest_and_hold
                )
                if side is Side.LONG
                else all(
                    candle.close <= level + close_buffer
                    for candle in retest_and_hold
                )
            )
            if not held_on_close:
                continue
            if side is Side.LONG:
                retest_extreme = min(
                    candle.low for candle in retest_and_hold
                )
                relaunch_level = max(
                    breakout.high,
                    max(candle.high for candle in post),
                )
            else:
                retest_extreme = max(
                    candle.high for candle in retest_and_hold
                )
                relaunch_level = min(
                    breakout.low,
                    min(candle.low for candle in post),
                )
            compression_quality = (
                (1 - bandwidth_rank) * 0.45
                + (1 - atr_rank) * 0.35
                + max(0.0, 1 - min(compression_volume, 1))
                * 0.20
            )
            impulse_quality = min(
                first_volume_ratio / 3, 1
            ) * 0.55 + min(body_atr / 2, 1) * 0.45
            return {
                "side": side,
                "breakout_level": level,
                "relaunch_level": relaunch_level,
                "retest_extreme": retest_extreme,
                "volatility": volatility,
                "quality": min(
                    compression_quality * 0.55
                    + impulse_quality * 0.45,
                    1,
                ),
            }
        return None

    def _higher_timeframe_trend_allows(
        self,
        candles: Sequence[Candle],
        side: Side,
    ) -> bool:
        trend = _as_interval(
            candles,
            self.runtime.squeeze_trend_timeframe_minutes,
        )
        if len(trend) < 32:
            return False
        closes = [c.close for c in trend]
        fast = ema(closes, 10)
        slow = ema(closes, 30)
        previous_fast = ema(closes[:-2], 10)
        efficiency = efficiency_ratio(closes, 10)
        if efficiency < self.runtime.minimum_trend_efficiency:
            return False
        if side is Side.LONG:
            return (
                fast > slow
                and fast > previous_fast
                and closes[-1] > fast
            )
        return (
            fast < slow
            and fast < previous_fast
            and closes[-1] < fast
        )

    def _compression_state(
        self, candles: Sequence[Candle]
    ) -> tuple[float, float, float, float, float] | None:
        period = self.runtime.bollinger_period
        lookback = self.runtime.lookback
        # The latest candle is treated as the potential expansion candle.
        pre = list(candles[:-1])[-(lookback + period + 2):]
        if len(pre) < lookback + period:
            return None
        closes = [c.close for c in pre]
        widths = _rolling_bandwidths(
            closes, period, self.runtime.bollinger_stddev
        )
        atr_values = _rolling_normalized_atr(
            pre, self.base.atr_period
        )
        if not widths or not atr_values:
            return None
        current_width = widths[-1]
        current_atr = atr_values[-1]
        compression_window = pre[-20:]
        range_high = max(c.high for c in compression_window)
        range_low = min(c.low for c in compression_window)
        recent_volume = sum(c.volume for c in pre[-5:]) / 5
        average_volume = sum(c.volume for c in pre[-20:]) / 20
        return (
            percentile_rank(widths[-lookback:], current_width),
            percentile_rank(atr_values[-lookback:], current_atr),
            recent_volume / max(average_volume, 1e-12),
            range_high,
            range_low,
        )


def _rolling_bandwidths(
    values: Sequence[float],
    period: int,
    standard_deviations: float,
) -> list[float]:
    if len(values) < period:
        return []
    result: list[float] = []
    total = sum(values[:period])
    total_squared = sum(value * value for value in values[:period])
    for index in range(period, len(values) + 1):
        if index > period:
            incoming = values[index - 1]
            outgoing = values[index - period - 1]
            total += incoming - outgoing
            total_squared += incoming * incoming - outgoing * outgoing
        mean = total / period
        variance = max(total_squared / period - mean * mean, 0.0)
        deviation = variance ** 0.5
        result.append(
            standard_deviations * 2 * deviation / mean
            if mean > 0 else float("inf")
        )
    return result


def _ema_series(
    values: Sequence[float], period: int
) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def _trend_alignment_age(
    fast: Sequence[float],
    slow: Sequence[float],
    *,
    above: bool,
) -> int:
    age = 0
    for fast_value, slow_value in zip(
        reversed(fast), reversed(slow)
    ):
        if (fast_value > slow_value) is not above:
            break
        age += 1
    return age


def _as_interval(
    candles: Sequence[Candle],
    interval_minutes: int,
) -> list[Candle]:
    if len(candles) < 2:
        return list(candles)
    spacing = (
        candles[-1].timestamp - candles[-2].timestamp
    ).total_seconds()
    return (
        resample_candles(candles, interval_minutes)
        if spacing < interval_minutes * 60
        else list(candles)
    )


def _rolling_normalized_atr(
    candles: Sequence[Candle], period: int
) -> list[float]:
    if len(candles) < period + 1:
        return []
    true_ranges = [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(candles, candles[1:])
    ]
    total = sum(true_ranges[:period])
    result = [total / period / max(candles[period].close, 1e-12)]
    for index in range(period, len(true_ranges)):
        total += true_ranges[index] - true_ranges[index - period]
        candle = candles[index + 1]
        result.append(total / period / max(candle.close, 1e-12))
    return result
