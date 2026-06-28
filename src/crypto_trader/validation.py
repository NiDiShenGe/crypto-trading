from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import random
from statistics import mean, median

from .backtest import MultiStrategyBacktester
from .config import Settings
from .history_cache import HistoricalDataCache
from .optimization import (
    Dataset,
    build_breadth_regimes,
    build_candidate_times,
)


STRATEGY_IDS = (
    "breakout_retest",
    "volatility_squeeze",
    "adaptive_liangyi_sixiang",
)


def validate_cached_strategies(
    settings: Settings,
    days: int,
    symbols: list[str],
    *,
    output_path: str | Path = "data/optimization/validation.json",
) -> dict:
    cache = HistoricalDataCache()
    benchmark, _ = cache.load(
        f"data/history/BTCUSDT-{days}d.json.gz"
    )
    datasets = []
    for symbol in symbols:
        candles, funding = cache.load(
            f"data/history/{symbol}-{days}d.json.gz"
        )
        datasets.append(Dataset(
            symbol, candles, funding, benchmark
        ))

    report = {
        "days": days,
        "symbols": symbols,
        "strategies": {},
    }
    for strategy_id in STRATEGY_IDS:
        full = _evaluate(
            settings, strategy_id, datasets, benchmark
        )
        report["strategies"][strategy_id] = {
            "full": _statistics(full),
            "walk_forward": _walk_forward(
                settings, strategy_id, datasets, benchmark
            ),
            "leave_one_symbol_out": _leave_one_out(full),
            "cost_stress": {
                str(factor): _statistics(_evaluate(
                    replace(
                        settings,
                        paper=replace(
                            settings.paper,
                            taker_fee_rate=(
                                settings.paper.taker_fee_rate * factor
                            ),
                            slippage_rate=(
                                settings.paper.slippage_rate * factor
                            ),
                        ),
                    ),
                    strategy_id,
                    datasets,
                    benchmark,
                ))
                for factor in (1.5, 2.0)
            },
        }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def _evaluate(
    settings: Settings,
    strategy_id: str,
    datasets: list[Dataset],
    benchmark: list,
) -> dict[str, list[float]]:
    breadth = build_breadth_regimes(
        datasets,
        settings.strategy.minimum_market_breadth_return,
    )
    allowed = build_candidate_times(
        strategy_id, datasets, settings
    )
    result = {}
    for dataset in datasets:
        trades = MultiStrategyBacktester(
            settings, {strategy_id}
        ).run(
            dataset.symbol,
            dataset.candles,
            dataset.funding,
            benchmark_candles=benchmark,
            breadth_regimes=breadth,
            allowed_entry_times=allowed[dataset.symbol],
        ).trades
        result[dataset.symbol] = [
            trade.realized_r for trade in trades
        ]
    return result


def _walk_forward(
    settings: Settings,
    strategy_id: str,
    datasets: list[Dataset],
    benchmark: list,
) -> list[dict]:
    breadth = build_breadth_regimes(
        datasets,
        settings.strategy.minimum_market_breadth_return,
    )
    allowed = build_candidate_times(
        strategy_id, datasets, settings
    )
    folds = []
    for fold in range(3):
        starts = {}
        ends = {}
        for dataset in datasets:
            length = len(dataset.candles)
            start = int(length * fold / 3)
            end = int(length * (fold + 1) / 3)
            starts[dataset.symbol] = dataset.candles[
                max(start, 600)
            ].timestamp
            ends[dataset.symbol] = (
                dataset.candles[end].timestamp
                if end < length else None
            )
        values = {}
        for dataset in datasets:
            trades = MultiStrategyBacktester(
                settings, {strategy_id}
            ).run(
                dataset.symbol,
                dataset.candles,
                dataset.funding,
                benchmark_candles=benchmark,
                breadth_regimes=breadth,
                allowed_entry_times=allowed[dataset.symbol],
                trade_start_time=starts[dataset.symbol],
                trade_end_time=ends[dataset.symbol],
            ).trades
            values[dataset.symbol] = [
                trade.realized_r for trade in trades
            ]
        folds.append(_statistics(values))
    return folds


def _leave_one_out(values: dict[str, list[float]]) -> dict:
    averages = {}
    for omitted in values:
        remaining = [
            result
            for symbol, results in values.items()
            if symbol != omitted
            for result in results
        ]
        averages[omitted] = (
            mean(remaining) if remaining else 0.0
        )
    return {
        "all_positive": all(value > 0 for value in averages.values()),
        "worst_average_r": min(averages.values(), default=0.0),
        "by_omitted_symbol": averages,
    }


def _statistics(values: dict[str, list[float]]) -> dict:
    trades = [
        result for results in values.values() for result in results
    ]
    if not trades:
        return {
            "trades": 0,
            "average_r": 0.0,
            "profit_factor_r": None,
            "positive_symbols": 0,
        }
    gains = sum(value for value in trades if value > 0)
    losses = -sum(value for value in trades if value < 0)
    rng = random.Random(20260623)
    bootstraps = sorted(
        mean(rng.choice(trades) for _ in trades)
        for _ in range(5000)
    )
    return {
        "trades": len(trades),
        "average_r": mean(trades),
        "median_r": median(trades),
        "win_rate": sum(value > 0 for value in trades) / len(trades),
        "profit_factor_r": gains / losses if losses else None,
        "bootstrap_95": [
            bootstraps[124],
            bootstraps[4874],
        ],
        "positive_symbols": sum(
            bool(results) and mean(results) > 0
            for results in values.values()
        ),
        "by_symbol": {
            symbol: {
                "trades": len(results),
                "average_r": mean(results) if results else 0.0,
            }
            for symbol, results in values.items()
        },
    }
