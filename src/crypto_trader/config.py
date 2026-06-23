from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class UniverseConfig:
    minimum_listing_days: int
    minimum_quote_volume_24h: float
    maximum_spread_ratio: float
    excluded_base_assets: frozenset[str]
    scan_interval_seconds: int = 300
    maximum_scan_candidates: int = 20
    scan_after_close_delay_seconds: int = 3
    maximum_analysis_markets: int = 60


@dataclass(frozen=True)
class StrategyConfig:
    breakout_lookback: int
    volume_lookback: int
    volume_multiplier: float
    ema_fast_period: int
    ema_slow_period: int
    atr_period: int
    stop_atr_multiple: float
    minimum_reward_risk: float = 2.0
    breakeven_at_r: float = 1.0
    first_take_profit_at_r: float = 2.0
    first_take_profit_fraction: float = 1 / 3
    trailing_atr_multiple: float = 3.0
    failed_breakout_max_bars: int = 6
    failed_breakout_min_progress_r: float = 0.5
    minimum_trend_efficiency: float = 0.25
    maximum_sweep_trend_efficiency: float = 0.55
    maximum_sweep_atr_ratio: float = 0.015
    sweep_lookback: int = 10
    sweep_volume_multiplier: float = 1.0
    minimum_sweep_body_atr: float = 0.10
    minimum_breakout_body_atr: float = 0.50
    breakout_consolidation_bars: int = 8
    maximum_consolidation_atr: float = 4.0
    minimum_breakout_penetration_atr: float = 0.10
    breakout_timeframe_hours: int = 4
    breakout_ema_fast_period: int = 10
    breakout_ema_slow_period: int = 30
    minimum_breakout_score: float = 0.80
    minimum_atr_ratio: float = 0.003
    confirmation_volume_multiplier: float = 1.20
    use_btc_market_regime: bool = True
    btc_regime_ema_fast: int = 20
    btc_regime_ema_slow: int = 50
    minimum_btc_regime_return: float = 0.02
    use_market_breadth_regime: bool = True
    minimum_market_breadth_return: float = 0.005


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float
    maximum_symbol_risk: float
    daily_loss_limit: float
    maximum_drawdown: float
    test_maximum_positions: int
    production_maximum_positions: int
    test_equity_threshold: float
    minimum_leverage: int
    maximum_leverage: int
    maximum_consecutive_losses: int
    use_exchange_max_leverage: bool = False
    enable_consecutive_loss_limit: bool = True
    reentry_cooldown_bars: int = 1


@dataclass(frozen=True)
class StrategyRuntimeConfig:
    enabled: bool = True
    automatic_trading: bool = True
    candidate_limit: int = 20
    risk_per_trade: float = 0.05
    priority: int = 1
    pullback_max_bars: int = 12
    confirmation_lookback: int = 3
    volume_contraction_ratio: float = 0.85
    atr_buffer: float = 0.5
    lookback: int = 100
    bollinger_period: int = 20
    bollinger_stddev: float = 2.0
    bandwidth_percentile: float = 0.20
    atr_percentile: float = 0.25
    breakout_volume_multiplier: float = 1.5
    maximum_compression_volume_ratio: float = 1.2
    minimum_trend_efficiency: float = 0.25
    minimum_breakout_body_atr: float = 0.50
    confirmation_volume_multiplier: float = 1.20
    maximum_trend_age_bars: int = 24
    no_progress_bars: int = 6
    minimum_progress_r: float = 0.25
    failed_breakout_confirmation_bars: int = 1
    breakeven_at_r: float = 1.0
    first_take_profit_at_r: float = 2.0
    first_take_profit_fraction: float = 1 / 3
    trailing_atr_multiple: float = 3.0
    minimum_signal_score: float = 0.0
    use_four_hour_confirmation: bool = False
    impulse_lookback_bars: int = 12
    impulse_breakout_lookback: int = 20
    impulse_volume_multiplier: float = 1.30
    minimum_impulse_body_atr: float = 0.50
    use_breakout_level_retest: bool = True
    use_four_hour_pullback: bool = True
    higher_timeframe_pullback_bars: int = 6
    maximum_higher_timeframe_trend_age: int = 24
    squeeze_breakout_max_age_bars: int = 18
    squeeze_retest_max_bars: int = 12
    squeeze_retest_atr_buffer: float = 0.40
    squeeze_failure_atr_buffer: float = 0.50
    squeeze_second_volume_multiplier: float = 1.20
    squeeze_second_body_atr: float = 0.35
    squeeze_relaunch_failure_atr_buffer: float = 0.15
    squeeze_retest_volume_ratio: float = 0.80
    squeeze_retest_close_atr_buffer: float = 0.10
    squeeze_timeframe_minutes: int = 15
    squeeze_trend_timeframe_minutes: int = 60
    squeeze_allow_short: bool = False
    squeeze_use_trend_continuation: bool = True
    squeeze_range_contraction_ratio: float = 1.0
    squeeze_pullback_volume_ratio: float = 1.0


@dataclass(frozen=True)
class PaperConfig:
    initial_equity: float = 100.0
    taker_fee_rate: float = 0.0006
    slippage_rate: float = 0.0005
    funding_rate_fallback: float = 0.0001


@dataclass(frozen=True)
class Settings:
    universe: UniverseConfig
    strategy: StrategyConfig
    risk: RiskConfig
    paper: PaperConfig = field(default_factory=PaperConfig)
    strategies: dict[str, StrategyRuntimeConfig] = field(default_factory=dict)


def load_settings(path: str | Path = "config/settings.toml") -> Settings:
    with Path(path).open("rb") as file:
        raw = tomllib.load(file)
    universe = raw["universe"]
    return Settings(
        universe=UniverseConfig(
            minimum_listing_days=universe["minimum_listing_days"],
            minimum_quote_volume_24h=universe["minimum_quote_volume_24h"],
            maximum_spread_ratio=universe["maximum_spread_ratio"],
            excluded_base_assets=frozenset(universe["excluded_base_assets"]),
            scan_interval_seconds=universe["scan_interval_seconds"],
            maximum_scan_candidates=universe["maximum_scan_candidates"],
            scan_after_close_delay_seconds=universe.get(
                "scan_after_close_delay_seconds", 3
            ),
            maximum_analysis_markets=universe.get("maximum_analysis_markets", 60),
        ),
        strategy=StrategyConfig(**{
            key: raw["strategy"][key]
            for key in StrategyConfig.__dataclass_fields__
        }),
        risk=RiskConfig(**{
            key: raw["risk"][key]
            for key in RiskConfig.__dataclass_fields__
        }),
        paper=PaperConfig(**raw.get("paper", {})),
        strategies={
            name: StrategyRuntimeConfig(**values)
            for name, values in raw.get("strategies", {}).items()
        },
    )
