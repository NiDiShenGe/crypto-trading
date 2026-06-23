from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from bisect import bisect_right
from datetime import timedelta
from itertools import product
import json
from pathlib import Path
from math import ceil
from statistics import median

from .backtest import MultiStrategyBacktester
from .config import Settings
from .domain import Candle
from .indicators import (
    closes_interval,
    efficiency_ratio,
    ema,
    resample_candles,
)
from .strategies import VolatilitySqueezeStrategy


@dataclass(frozen=True)
class Dataset:
    symbol: str
    candles: list[Candle]
    funding: list[tuple]
    benchmark_candles: list[Candle] | None = None


@dataclass(frozen=True)
class CandidateResult:
    strategy_id: str
    parameters: dict
    train_score: float
    validation_score: float
    train_metrics: dict
    validation_metrics: dict


class StrategyOptimizer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def optimize(
        self,
        strategy_id: str,
        datasets: list[Dataset],
        output_dir: str | Path = "data/optimization",
    ) -> list[CandidateResult]:
        candidates: list[CandidateResult] = []
        for parameters in self.parameter_grid(strategy_id):
            configured = self.apply_parameters(strategy_id, parameters)
            train_metrics: list[dict] = []
            validation_metrics: list[dict] = []
            train_datasets = [
                Dataset(
                    item.symbol,
                    item.candles[
                        :max(int(len(item.candles) * 0.7), 250)
                    ],
                    item.funding,
                    item.benchmark_candles,
                )
                for item in datasets
            ]
            validation_datasets = [
                Dataset(
                    item.symbol,
                    item.candles[
                        max(int(len(item.candles) * 0.7), 700) - 600:
                    ],
                    item.funding,
                    item.benchmark_candles,
                )
                for item in datasets
            ]
            train_allowed_by_symbol = build_candidate_times(
                strategy_id, train_datasets, configured
            )
            validation_allowed_by_symbol = build_candidate_times(
                strategy_id, validation_datasets, configured
            )
            train_breadth = build_breadth_regimes(
                train_datasets,
                configured.strategy.minimum_market_breadth_return,
            )
            validation_breadth = build_breadth_regimes(
                validation_datasets,
                configured.strategy.minimum_market_breadth_return,
            )
            for dataset in datasets:
                split = max(int(len(dataset.candles) * 0.7), 700)
                train = dataset.candles[:split]
                validation_start = max(split - 600, 0)
                validation = dataset.candles[validation_start:]
                split_time = dataset.candles[split].timestamp
                train_funding = [
                    item for item in dataset.funding
                    if item[0] < split_time
                ]
                validation_funding = [
                    item for item in dataset.funding
                    if item[0] >= dataset.candles[validation_start].timestamp
                ]
                benchmark = dataset.benchmark_candles or []
                train_end = train[-1].timestamp if train else split_time
                validation_start_time = validation[0].timestamp
                train_benchmark = [
                    candle for candle in benchmark
                    if candle.timestamp <= train_end
                ]
                validation_benchmark = [
                    candle for candle in benchmark
                    if candle.timestamp >= validation_start_time
                ]
                train_allowed = train_allowed_by_symbol.get(
                    dataset.symbol, set()
                )
                validation_allowed = validation_allowed_by_symbol.get(
                    dataset.symbol, set()
                )
                backtester = MultiStrategyBacktester(
                    configured, {strategy_id}
                )
                train_metrics.append(
                    backtester.run(
                        dataset.symbol,
                        train,
                        train_funding,
                        benchmark_candles=train_benchmark,
                        allowed_entry_times=train_allowed,
                        breadth_regimes=train_breadth,
                    ).summary()
                )
                validation_metrics.append(
                    backtester.run(
                        dataset.symbol,
                        validation,
                        validation_funding,
                        benchmark_candles=validation_benchmark,
                        allowed_entry_times=validation_allowed,
                        trade_start_time=split_time,
                        breadth_regimes=validation_breadth,
                    ).summary()
                )
            train_aggregate = aggregate_metrics(train_metrics)
            validation_aggregate = aggregate_metrics(validation_metrics)
            candidates.append(CandidateResult(
                strategy_id,
                parameters,
                robust_score(train_aggregate),
                robust_score(validation_aggregate),
                train_aggregate,
                validation_aggregate,
            ))
        candidates.sort(
            key=lambda item: (
                min(item.train_score, item.validation_score),
                (item.train_score + item.validation_score) / 2,
            ),
            reverse=True,
        )
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        with (output / f"{strategy_id}.json").open("w", encoding="utf-8") as file:
            json.dump(
                [asdict(item) for item in candidates[:25]],
                file,
                ensure_ascii=False,
                indent=2,
            )
        return candidates

    def parameter_grid(self, strategy_id: str) -> list[dict]:
        if strategy_id == "breakout_retest":
            return [
                {
                    "breakout_lookback": lookback,
                    "volume_multiplier": volume,
                    "stop_atr_multiple": stop,
                    "first_take_profit_at_r": take_profit,
                    "trailing_atr_multiple": trail,
                    "no_progress_bars": max_bars,
                    "minimum_trend_efficiency": efficiency,
                    "minimum_breakout_body_atr": body,
                    "minimum_atr_ratio": atr_ratio,
                }
                for lookback, volume, stop, take_profit, trail, max_bars, efficiency, body, atr_ratio
                in product(
                    (20, 40),
                    (1.5, 2.0),
                    (2.0, 3.0),
                    (2.0,),
                    (3.0,),
                    (48, 72),
                    (0.25, 0.45),
                    (0.50,),
                    (0.0025, 0.004),
                )
            ]
        if strategy_id == "trend_pullback":
            return [
                {
                    "volume_contraction_ratio": volume,
                    "atr_buffer": buffer,
                    "confirmation_lookback": confirmation,
                    "first_take_profit_at_r": take_profit,
                    "trailing_atr_multiple": trail,
                    "no_progress_bars": max_bars,
                    "minimum_trend_efficiency": efficiency,
                    "confirmation_volume_multiplier": confirmation_volume,
                    "stop_atr_multiple": stop,
                    "minimum_atr_ratio": atr_ratio,
                }
                for volume, buffer, confirmation, take_profit, trail, max_bars, efficiency, confirmation_volume, stop, atr_ratio
                in product(
                    (0.90, 1.10),
                    (0.50,),
                    (2,),
                    (2.0,),
                    (3.0,),
                    (6, 12),
                    (0.15, 0.30),
                    (0.80, 1.0),
                    (2.0, 3.0),
                    (0.0025, 0.004),
                )
            ]
        if strategy_id == "volatility_squeeze":
            return [
                {
                    "bandwidth_percentile": bandwidth,
                    "atr_percentile": atr_rank,
                    "breakout_volume_multiplier": volume,
                    "atr_buffer": buffer,
                    "first_take_profit_at_r": take_profit,
                    "trailing_atr_multiple": trail,
                    "no_progress_bars": max_bars,
                    "minimum_trend_efficiency": efficiency,
                    "minimum_breakout_body_atr": body,
                    "stop_atr_multiple": stop,
                    "minimum_atr_ratio": atr_ratio,
                }
                for bandwidth, atr_rank, volume, buffer, take_profit, trail, max_bars, efficiency, body, stop, atr_ratio
                in product(
                    (0.10, 0.20),
                    (0.15, 0.30),
                    (1.3,),
                    (0.25,),
                    (2.0,),
                    (3.0,),
                    (48, 72),
                    (0.10,),
                    (0.50,),
                    (2.0,),
                    (0.0025,),
                )
            ]
        raise ValueError(f"unknown strategy: {strategy_id}")

    def apply_parameters(
        self, strategy_id: str, parameters: dict
    ) -> Settings:
        strategy_fields = {
            key: value for key, value in parameters.items()
            if hasattr(self.settings.strategy, key)
        }
        runtime_fields = {
            key: value for key, value in parameters.items()
            if hasattr(self.settings.strategies[strategy_id], key)
        }
        strategies = dict(self.settings.strategies)
        strategies[strategy_id] = replace(
            strategies[strategy_id], **runtime_fields
        )
        return replace(
            self.settings,
            strategy=replace(
                self.settings.strategy, **strategy_fields
            ),
            strategies=strategies,
        )


def aggregate_metrics(metrics: list[dict]) -> dict:
    trades = sum(item["trades"] for item in metrics)
    final_return = sum(item["return_pct"] for item in metrics)
    positive_markets = sum(item["return_pct"] > 0 for item in metrics)
    weighted_r = sum(
        item["average_r"] * item["trades"] for item in metrics
    )
    gross_profit = 0.0
    gross_loss = 0.0
    for item in metrics:
        factor = item["profit_factor"]
        if factor is not None and factor > 0:
            # Aggregate scoring relies primarily on return/R because the
            # summary does not expose gross legs separately.
            if item["return_pct"] > 0:
                gross_profit += item["return_pct"]
            else:
                gross_loss += -item["return_pct"]
        elif item["return_pct"] < 0:
            gross_loss += -item["return_pct"]
    return {
        "markets": len(metrics),
        "positive_markets": positive_markets,
        "positive_market_ratio": (
            positive_markets / len(metrics) if metrics else 0
        ),
        "trades": trades,
        "average_r": weighted_r / trades if trades else 0,
        "average_return": final_return / len(metrics) if metrics else 0,
        "worst_return": min(
            (item["return_pct"] for item in metrics), default=0
        ),
        "maximum_drawdown": max(
            (item["maximum_drawdown"] for item in metrics), default=0
        ),
        "pseudo_profit_factor": (
            gross_profit / gross_loss if gross_loss > 0 else None
        ),
        "symbols": {
            item["symbol"]: {
                "return_pct": item["return_pct"],
                "trades": item["trades"],
                "average_r": item["average_r"],
                "profit_factor": item["profit_factor"],
                "maximum_drawdown": item["maximum_drawdown"],
            }
            for item in metrics
        },
    }


def robust_score(metrics: dict) -> float:
    if metrics["trades"] < max(metrics["markets"] * 5, 10):
        return -10.0
    score = (
        metrics["average_r"] * 3
        + metrics["average_return"] * 2
        + metrics["positive_market_ratio"]
        - metrics["maximum_drawdown"] * 1.5
        + min(metrics["trades"] / 200, 0.5)
    )
    if metrics["average_r"] <= 0:
        score -= 2
    if metrics["average_return"] <= 0:
        score -= 2
    if metrics["positive_market_ratio"] < 0.5:
        score -= 1
    return score


def build_candidate_times(
    strategy_id: str,
    datasets: list[Dataset],
    settings: Settings,
    reference_universe_size: int | None = None,
) -> dict[str, set]:
    if not datasets:
        return {}
    runtime = settings.strategies[strategy_id]
    universe_size = (
        reference_universe_size
        or settings.universe.maximum_analysis_markets
    )
    top_count = max(
        1,
        ceil(
            len(datasets)
            * runtime.candidate_limit
            / universe_size
        ),
    )
    scores: dict[str, dict] = {
        dataset.symbol: _candidate_scores(
            strategy_id, dataset.candles, settings
        )
        for dataset in datasets
    }
    timestamps = sorted({
        timestamp
        for symbol_scores in scores.values()
        for timestamp in symbol_scores
    })
    allowed = {dataset.symbol: set() for dataset in datasets}
    for timestamp in timestamps:
        ranked = sorted(
            (
                (symbol, symbol_scores.get(timestamp, float("-inf")))
                for symbol, symbol_scores in scores.items()
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        for symbol, score in ranked[:top_count]:
            if score != float("-inf"):
                allowed[symbol].add(timestamp)
    return allowed


def build_breadth_regimes(
    datasets: list[Dataset],
    minimum_return: float = 0.005,
    lookback_bars: int = 288,
) -> dict:
    returns_by_time: dict = {}
    for dataset in datasets:
        candles = dataset.candles
        for index in range(lookback_bars, len(candles)):
            candle = candles[index]
            if not closes_interval(candle, 15):
                continue
            reference = candles[index - lookback_bars]
            if reference.close <= 0:
                continue
            returns_by_time.setdefault(candle.timestamp, []).append(
                candle.close / reference.close - 1
            )
    regimes = {}
    minimum_markets = max(2, len(datasets) // 2)
    for timestamp, returns in returns_by_time.items():
        if len(returns) < minimum_markets:
            regimes[timestamp] = "neutral"
            continue
        value = median(returns)
        regimes[timestamp] = (
            "long" if value >= minimum_return
            else "short" if value <= -minimum_return
            else "neutral"
        )
    return regimes


def _candidate_scores(
    strategy_id: str,
    candles: list[Candle],
    settings: Settings,
) -> dict:
    result: dict = {}
    squeeze = VolatilitySqueezeStrategy(
        settings.strategy,
        settings.strategies["volatility_squeeze"],
    )
    hourly = (
        resample_candles(candles, 60)
        if (
            strategy_id == "trend_pullback"
            or (
                strategy_id == "volatility_squeeze"
                and settings.strategies[
                    "volatility_squeeze"
                ].squeeze_use_trend_continuation
            )
        )
        else []
    )
    hourly_close_times = [
        candle.timestamp + timedelta(hours=1)
        for candle in hourly
    ]
    squeeze_interval = (
        settings.strategies[
            "volatility_squeeze"
        ].squeeze_timeframe_minutes
    )
    squeeze_candles = (
        resample_candles(candles, squeeze_interval)
        if strategy_id == "volatility_squeeze"
        else []
    )
    squeeze_close_times = [
        candle.timestamp + timedelta(minutes=squeeze_interval)
        for candle in squeeze_candles
    ]
    for index in range(200, len(candles)):
        if not closes_interval(candles[index], 15):
            continue
        window = candles[max(0, index - 1499): index + 1]
        timestamp = candles[index].timestamp
        if strategy_id == "breakout_retest":
            recent = candles[max(0, index - 288): index + 1]
            high = max(item.high for item in recent)
            low = min(item.low for item in recent)
            midpoint = (high + low) / 2
            momentum = (
                recent[-1].close / recent[0].close - 1
                if recent[0].close > 0 else 0
            )
            result[timestamp] = (
                (high - low) / midpoint if midpoint > 0 else 0
            ) + abs(momentum) * 5
        elif strategy_id == "trend_pullback":
            trend_end = bisect_right(
                hourly_close_times,
                candles[index].timestamp + timedelta(minutes=5),
            )
            trend = hourly[max(0, trend_end - 250):trend_end]
            closes = [item.close for item in trend]
            if len(closes) < settings.strategy.ema_slow_period + 4:
                continue
            fast = ema(closes, settings.strategy.ema_fast_period)
            slow = ema(closes, settings.strategy.ema_slow_period)
            previous_fast = ema(
                closes[:-3], settings.strategy.ema_fast_period
            )
            result[timestamp] = (
                abs(fast - slow) / max(abs(slow), 1e-12) * 10
                + abs(fast - previous_fast)
                / max(abs(previous_fast), 1e-12)
                * 20
            )
        else:
            if settings.strategies[
                "volatility_squeeze"
            ].squeeze_use_trend_continuation:
                trend_end = bisect_right(
                    hourly_close_times,
                    candles[index].timestamp
                    + timedelta(minutes=5),
                )
                trend = hourly[
                    max(0, trend_end - 250):trend_end
                ]
                result[timestamp] = (
                    squeeze.continuation_setup_score(trend)
                )
                continue
            close_time = candles[index].timestamp + timedelta(
                minutes=5
            )
            if close_time.minute % squeeze_interval:
                continue
            squeeze_end = bisect_right(
                squeeze_close_times, close_time
            )
            setup_window = squeeze_candles[
                max(0, squeeze_end - 180):squeeze_end
            ]
            result[timestamp] = squeeze.setup_score(setup_window)
    return result
