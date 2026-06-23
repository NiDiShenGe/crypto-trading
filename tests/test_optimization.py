from datetime import UTC, datetime

from crypto_trader.config import load_settings
from crypto_trader.optimization import (
    Dataset,
    build_candidate_times,
)
import crypto_trader.optimization as optimization
from crypto_trader.optimization import aggregate_metrics, robust_score
from crypto_trader.strategies import VolatilitySqueezeStrategy


def test_robust_score_rewards_cross_market_positive_expectancy() -> None:
    good = aggregate_metrics([
        {
            "symbol": "A",
            "return_pct": 0.1,
            "trades": 10,
            "average_r": 0.2,
            "profit_factor": 1.5,
            "maximum_drawdown": 0.1,
        },
        {
            "symbol": "B",
            "return_pct": 0.05,
            "trades": 10,
            "average_r": 0.1,
            "profit_factor": 1.2,
            "maximum_drawdown": 0.12,
        },
    ])
    bad = aggregate_metrics([
        {
            "symbol": "A",
            "return_pct": -0.1,
            "trades": 10,
            "average_r": -0.2,
            "profit_factor": 0.5,
            "maximum_drawdown": 0.3,
        },
        {
            "symbol": "B",
            "return_pct": 0.01,
            "trades": 10,
            "average_r": 0.01,
            "profit_factor": 1.05,
            "maximum_drawdown": 0.2,
        },
    ])
    assert robust_score(good) > robust_score(bad)


def test_candidate_count_scales_to_live_analysis_universe(
    monkeypatch,
) -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    datasets = [
        Dataset(f"S{index}", [index], [])
        for index in range(12)
    ]
    monkeypatch.setattr(
        optimization,
        "_candidate_scores",
        lambda strategy_id, candles, settings: {
            timestamp: candles[0]
        },
    )
    allowed = build_candidate_times(
        "breakout_retest", datasets, load_settings()
    )
    selected = [
        symbol
        for symbol, timestamps in allowed.items()
        if timestamp in timestamps
    ]
    # 8 live candidates out of 60 markets maps to 2 out of 12.
    assert len(selected) == 2


def test_squeeze_continuation_candidate_requires_complete_history() -> None:
    settings = load_settings()
    strategy = VolatilitySqueezeStrategy(
        settings.strategy,
        settings.strategies["volatility_squeeze"],
    )
    assert strategy.continuation_setup_score([]) == 0.0
