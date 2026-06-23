from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import Settings
from .domain import Candle, Side, Signal
from .indicators import closes_interval, resample_candles
from .strategies import TrendPullbackStrategy, VolatilitySqueezeStrategy
from .strategy import BreakoutRetestStrategy
from .scanner import market_regime, regime_allows_side


@dataclass(frozen=True)
class BacktestTrade:
    strategy_id: str
    side: Side
    entry_price: float
    exit_price: float
    net_pnl: float
    realized_r: float
    reason: str
    holding_bars: int
    fees: float
    funding: float
    ambiguous_bar: bool = False
    signal_score: float = 0.0


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    initial_equity: float
    final_equity: float
    maximum_drawdown: float
    trades: tuple[BacktestTrade, ...]

    def summary(self) -> dict:
        pnls = [trade.net_pnl for trade in self.trades]
        wins = [value for value in pnls if value > 0]
        losses = [value for value in pnls if value < 0]
        return {
            "symbol": self.symbol,
            "initial_equity": self.initial_equity,
            "final_equity": self.final_equity,
            "return_pct": (
                self.final_equity / self.initial_equity - 1
                if self.initial_equity else 0
            ),
            "maximum_drawdown": self.maximum_drawdown,
            "trades": len(self.trades),
            "win_rate": len(wins) / len(pnls) if pnls else 0,
            "profit_factor": (
                sum(wins) / -sum(losses) if losses else None
            ),
            "average_r": (
                sum(trade.realized_r for trade in self.trades) / len(self.trades)
                if self.trades else 0
            ),
            "fees": sum(trade.fees for trade in self.trades),
            "funding": sum(trade.funding for trade in self.trades),
            "exit_reasons": self._exit_reasons(),
            "ambiguous_bars": sum(
                trade.ambiguous_bar for trade in self.trades
            ),
            "by_strategy": self._by_strategy(),
        }

    def _by_strategy(self) -> dict:
        result: dict[str, dict] = {}
        for strategy_id in {
            trade.strategy_id for trade in self.trades
        }:
            trades = [
                trade for trade in self.trades
                if trade.strategy_id == strategy_id
            ]
            result[strategy_id] = {
                "trades": len(trades),
                "net_pnl": sum(trade.net_pnl for trade in trades),
                "average_r": sum(t.realized_r for t in trades) / len(trades),
                "win_rate": sum(t.net_pnl > 0 for t in trades) / len(trades),
                "fees": sum(t.fees for t in trades),
                "funding": sum(t.funding for t in trades),
                "exit_reasons": {
                    reason: sum(t.reason == reason for t in trades)
                    for reason in {t.reason for t in trades}
                },
            }
        return result

    def _exit_reasons(self) -> dict[str, int]:
        return {
            reason: sum(trade.reason == reason for trade in self.trades)
            for reason in {trade.reason for trade in self.trades}
        }


class MultiStrategyBacktester:
    def __init__(
        self,
        settings: Settings,
        enabled_strategy_ids: set[str] | None = None,
    ) -> None:
        self.settings = settings
        strategies = {
            "breakout_retest": BreakoutRetestStrategy(settings.strategy),
            "trend_pullback": TrendPullbackStrategy(
                settings.strategy, settings.strategies["trend_pullback"]
            ),
            "volatility_squeeze": VolatilitySqueezeStrategy(
                settings.strategy, settings.strategies["volatility_squeeze"]
            ),
        }
        self.strategies = {
            strategy_id: strategy
            for strategy_id, strategy in strategies.items()
            if enabled_strategy_ids is None
            or strategy_id in enabled_strategy_ids
        }

    def run(
        self,
        symbol: str,
        candles: list[Candle],
        funding_rates: list[tuple] | None = None,
        benchmark_candles: list[Candle] | None = None,
        allowed_entry_times: set | None = None,
        trade_start_time: datetime | None = None,
        trade_end_time: datetime | None = None,
        breadth_regimes: dict[datetime, str] | None = None,
    ) -> BacktestResult:
        equity = self.settings.paper.initial_equity
        high_watermark = equity
        maximum_drawdown = 0.0
        trades: list[BacktestTrade] = []
        position: dict | None = None
        pending: Signal | None = None
        last_exit_index = -10_000
        current_day: date | None = None
        day_start_equity = equity
        daily_halted = False
        hourly = resample_hourly(candles)
        fifteen_minute = resample_candles(candles, 15)
        fifteen_close_times = [
            candle.timestamp + timedelta(minutes=15)
            for candle in fifteen_minute
        ]
        benchmark_hourly = resample_hourly(
            benchmark_candles or candles
        )
        hourly_close_times = [
            candle.timestamp + timedelta(hours=1)
            for candle in hourly
        ]
        benchmark_close_times = [
            candle.timestamp + timedelta(hours=1)
            for candle in benchmark_hourly
        ]
        funding_rates = funding_rates or []

        for index in range(200, len(candles)):
            candle = candles[index]
            if (
                trade_end_time is not None
                and candle.timestamp >= trade_end_time
            ):
                pending = None
                if position is not None:
                    closed = self._close(
                        position,
                        candle.open,
                        index,
                        "end_of_validation_fold",
                    )
                    trades.append(closed)
                    equity += closed.net_pnl
                    position = None
                break
            candle_day = candle.timestamp.date()
            if candle_day != current_day:
                current_day = candle_day
                day_start_equity = equity
                daily_halted = False
            if pending is not None and position is None:
                position = self._open(pending, candle.open, equity, index)
                pending = None

            if position is not None:
                closed = self._manage(
                    position, candle, index, candles[: index + 1], funding_rates
                )
                if closed is not None:
                    trades.append(closed)
                    equity += closed.net_pnl
                    position = None
                    last_exit_index = index
                    high_watermark = max(high_watermark, equity)
                    maximum_drawdown = max(
                        maximum_drawdown,
                        (high_watermark - equity) / high_watermark,
                    )
                    daily_loss = (
                        (day_start_equity - equity) / day_start_equity
                        if day_start_equity > 0 else 1
                    )
                    if daily_loss >= self.settings.risk.daily_loss_limit:
                        daily_halted = True

            drawdown_halted = (
                (high_watermark - equity) / high_watermark
                >= self.settings.risk.maximum_drawdown
            )
            cooldown_complete = (
                index - last_exit_index
                >= self.settings.risk.reentry_cooldown_bars
            )
            if (
                position is None
                and pending is None
                and equity > 0
                and not daily_halted
                and not drawdown_halted
                and cooldown_complete
                and (
                    trade_start_time is None
                    or candle.timestamp >= trade_start_time
                )
                and (
                    allowed_entry_times is None
                    or candle.timestamp in allowed_entry_times
                )
                and closes_interval(candle, 15)
            ):
                close_time = candle.timestamp + timedelta(minutes=5)
                trend_end = bisect_right(hourly_close_times, close_time)
                benchmark_end = bisect_right(
                    benchmark_close_times, close_time
                )
                trend = hourly[max(0, trend_end - 250):trend_end]
                benchmark_trend = benchmark_hourly[
                    max(0, benchmark_end - 250):benchmark_end
                ]
                regime = market_regime(
                    benchmark_trend,
                    self.settings.strategy.btc_regime_ema_fast,
                    self.settings.strategy.btc_regime_ema_slow,
                    self.settings.strategy.minimum_btc_regime_return,
                )
                signal_window = candles[max(0, index - 499): index + 1]
                fifteen_end = bisect_right(
                    fifteen_close_times, close_time
                )
                fifteen_window = fifteen_minute[
                    max(0, fifteen_end - 170):fifteen_end
                ]
                signals = []
                for strategy_id, strategy in self.strategies.items():
                    strategy_candles = (
                        fifteen_window
                        if strategy_id in {
                            "trend_pullback",
                            "volatility_squeeze",
                        }
                        else signal_window
                    )
                    signals.append(
                        strategy.evaluate(
                            symbol, strategy_candles, trend
                        )
                    )
                valid = [signal for signal in signals if signal is not None]
                if self.settings.strategy.use_btc_market_regime:
                    valid = [
                        signal for signal in valid
                        if regime_allows_side(regime, signal.side)
                    ]
                if (
                    self.settings.strategy.use_market_breadth_regime
                    and breadth_regimes is not None
                ):
                    breadth = breadth_regimes.get(
                        candle.timestamp, "neutral"
                    )
                    valid = [
                        signal for signal in valid
                        if signal.side.value == breadth
                    ]
                if valid:
                    pending = max(
                        valid,
                        key=lambda signal: (
                            signal.score,
                            self.settings.strategies[
                                signal.strategy_id
                            ].priority,
                            ),
                    )
        if position is not None and candles:
            final_index = len(candles) - 1
            closed = self._close(
                position,
                candles[-1].close,
                final_index,
                "end_of_test",
            )
            trades.append(closed)
            equity += closed.net_pnl
            high_watermark = max(high_watermark, equity)
            maximum_drawdown = max(
                maximum_drawdown,
                (high_watermark - equity) / high_watermark,
            )
        return BacktestResult(
            symbol,
            self.settings.paper.initial_equity,
            equity,
            maximum_drawdown,
            tuple(trades),
        )

    def _open(
        self, signal: Signal, market_price: float, equity: float, index: int
    ) -> dict:
        price = market_price * (
            1 + self.settings.paper.slippage_rate
            if signal.side is Side.LONG
            else 1 - self.settings.paper.slippage_rate
        )
        signal_risk_distance = abs(signal.entry - signal.stop)
        stop = (
            price - signal_risk_distance
            if signal.side is Side.LONG
            else price + signal_risk_distance
        )
        risk_budget = (
            equity
            * self.settings.strategies[signal.strategy_id].risk_per_trade
        )
        quantity = risk_budget / signal_risk_distance
        fee = price * quantity * self.settings.paper.taker_fee_rate
        return {
            "signal": signal,
            "entry": price,
            "stop": stop,
            "initial_stop": stop,
            "risk_distance": signal_risk_distance,
            "quantity": quantity,
            "remaining": quantity,
            "initial_risk": risk_budget,
            "fees": fee,
            "funding": 0.0,
            "pnl": -fee,
            "index": index,
            "best": price,
            "partial": False,
            "peak_r": 0.0,
            "last_funding_time": None,
            "failure_close_count": 0,
        }

    def _manage(
        self,
        position: dict,
        candle: Candle,
        index: int,
        history: list[Candle],
        funding_rates: list[tuple],
    ) -> BacktestTrade | None:
        signal: Signal = position["signal"]
        runtime = self.settings.strategies[signal.strategy_id]
        direction = 1 if signal.side is Side.LONG else -1
        candle_end = candle.timestamp + timedelta(minutes=5)
        for funding_time, rate in funding_rates:
            if not (candle.timestamp <= funding_time < candle_end):
                continue
            if position["last_funding_time"] == funding_time:
                continue
            position["funding"] += (
                candle.close * position["remaining"] * rate * direction
            )
            position["last_funding_time"] = funding_time
        risk_distance = position["risk_distance"]
        stop_hit = (
            candle.low <= position["stop"]
            if signal.side is Side.LONG
            else candle.high >= position["stop"]
        )
        target_price = position["entry"] + (
            risk_distance
            * runtime.first_take_profit_at_r
            * direction
        )
        target_hit = (
            candle.high >= target_price
            if signal.side is Side.LONG
            else candle.low <= target_price
        )
        if stop_hit:
            return self._close(
                position,
                position["stop"],
                index,
                "stop",
                ambiguous_bar=(target_hit and not position["partial"]),
            )

        favorable_price = candle.high if signal.side is Side.LONG else candle.low
        favorable = (favorable_price - position["entry"]) * direction
        position["best"] = (
            max(position["best"], favorable_price)
            if signal.side is Side.LONG
            else min(position["best"], favorable_price)
        )
        position["peak_r"] = max(position["peak_r"], favorable / risk_distance)

        if position["peak_r"] >= runtime.breakeven_at_r:
            position["stop"] = (
                max(position["stop"], position["entry"])
                if signal.side is Side.LONG
                else min(position["stop"], position["entry"])
            )
        if (
            not position["partial"]
            and position["peak_r"] >= runtime.first_take_profit_at_r
        ):
            quantity = (
                position["quantity"]
                * runtime.first_take_profit_fraction
            )
            target = position["entry"] + (
                risk_distance
                * runtime.first_take_profit_at_r
                * direction
            )
            self._realize(position, target, quantity)
            position["partial"] = True

        if position["peak_r"] >= runtime.breakeven_at_r:
            trail = (
                risk_distance
                / self.settings.strategy.stop_atr_multiple
                * runtime.trailing_atr_multiple
            )
            position["stop"] = (
                max(position["stop"], position["best"] - trail)
                if signal.side is Side.LONG
                else min(position["stop"], position["best"] + trail)
            )

        failure_level = signal.invalidation_level or signal.breakout_level
        close_back_inside = (
            candle.close <= failure_level
            if signal.side is Side.LONG
            else candle.close >= failure_level
        )
        if close_back_inside:
            position["failure_close_count"] += 1
        else:
            position["failure_close_count"] = 0
        bars = index - position["index"]
        if (
            failure_level > 0
            and position["failure_close_count"]
            >= runtime.failed_breakout_confirmation_bars
        ):
            return self._close(position, candle.close, index, "failed_breakout")
        if (
            bars >= runtime.no_progress_bars
            and position["peak_r"]
            < runtime.minimum_progress_r
        ):
            return self._close(position, candle.close, index, "no_progress_exit")
        return None

    def _realize(self, position: dict, price: float, quantity: float) -> None:
        signal: Signal = position["signal"]
        direction = 1 if signal.side is Side.LONG else -1
        fill = price * (
            1 - self.settings.paper.slippage_rate
            if signal.side is Side.LONG
            else 1 + self.settings.paper.slippage_rate
        )
        fee = fill * quantity * self.settings.paper.taker_fee_rate
        position["pnl"] += (fill - position["entry"]) * quantity * direction - fee
        position["fees"] += fee
        position["remaining"] -= quantity

    def _close(
        self,
        position: dict,
        price: float,
        index: int,
        reason: str,
        ambiguous_bar: bool = False,
    ) -> BacktestTrade:
        self._realize(position, price, position["remaining"])
        signal: Signal = position["signal"]
        return BacktestTrade(
            signal.strategy_id,
            signal.side,
            position["entry"],
            price,
            position["pnl"] - position["funding"],
            (position["pnl"] - position["funding"]) / position["initial_risk"],
            reason,
            index - position["index"],
            position["fees"],
            position["funding"],
            ambiguous_bar,
            signal.score,
        )


def resample_hourly(candles: list[Candle]) -> list[Candle]:
    buckets: dict[int, list[Candle]] = {}
    for candle in candles:
        key = int(candle.timestamp.timestamp()) // 3600 * 3600
        buckets.setdefault(key, []).append(candle)
    result: list[Candle] = []
    for key, group in sorted(buckets.items()):
        if len(group) < 12:
            continue
        result.append(Candle(
            timestamp=group[0].timestamp.replace(
                minute=0, second=0, microsecond=0
            ),
            open=group[0].open,
            high=max(item.high for item in group),
            low=min(item.low for item in group),
            close=group[-1].close,
            volume=sum(item.volume for item in group),
        ))
    return result
