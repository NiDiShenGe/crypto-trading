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
    first_take_profit_at_r: float = 1.5
    first_take_profit_fraction: float = 0.5
    trailing_atr_multiple: float = 2.5


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
    )
