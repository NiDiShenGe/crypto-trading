from __future__ import annotations

from .config import UniverseConfig
from .domain import Market


def is_eligible(market: Market, config: UniverseConfig) -> bool:
    return (
        market.quote_asset == "USDT"
        and market.base_asset not in config.excluded_base_assets
        and market.listing_days >= config.minimum_listing_days
        and market.quote_volume_24h >= config.minimum_quote_volume_24h
        and market.spread_ratio <= config.maximum_spread_ratio
        and not market.abnormal
        and market.bid > 0
        and market.ask >= market.bid
    )


def select_markets(markets: list[Market], config: UniverseConfig) -> list[Market]:
    eligible = [market for market in markets if is_eligible(market, config)]
    return sorted(eligible, key=lambda market: market.quote_volume_24h, reverse=True)

