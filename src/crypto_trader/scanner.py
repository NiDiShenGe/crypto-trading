from __future__ import annotations

from dataclasses import dataclass, replace
from dataclasses import field
from datetime import UTC, datetime, timedelta

from .config import Settings
from .domain import Market, Signal
from .exchange.bitget import BitgetClient
from .strategy import BreakoutRetestStrategy
from .universe import select_markets


@dataclass(frozen=True)
class ScanResult:
    scanned_at: datetime
    total_markets: int
    eligible_markets: int
    scanned_candidates: int
    signals: tuple[Signal, ...]
    prices: dict[str, float]
    maximum_leverages: dict[str, int] = field(default_factory=dict)


class MarketScanner:
    def __init__(self, client: BitgetClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self.strategy = BreakoutRetestStrategy(settings.strategy)
        self._confirmed_listing_symbols: set[str] = set()
        self._listing_age_checked: set[str] = set()

    def scan_once(self) -> ScanResult:
        markets = self.client.markets()
        markets = self._resolve_unknown_listing_ages(markets)
        eligible = select_markets(markets, self.settings.universe)
        candidates = self._high_volatility_candidates(eligible)
        signals: list[Signal] = []
        for market in candidates:
            signal_candles = closed_candles(
                self.client.candles(market.symbol, "5m", limit=100), 300
            )
            trend_candles = closed_candles(
                self.client.candles(market.symbol, "1H", limit=100), 3600
            )
            signal = self.strategy.evaluate(market.symbol, signal_candles, trend_candles)
            if signal is not None:
                signals.append(signal)
        return ScanResult(
            scanned_at=datetime.now(UTC),
            total_markets=len(markets),
            eligible_markets=len(eligible),
            scanned_candidates=len(candidates),
            signals=tuple(signals),
            prices={
                market.symbol: (market.bid + market.ask) / 2
                for market in markets
                if market.bid > 0 and market.ask > 0
            },
            maximum_leverages={
                market.symbol: market.maximum_leverage for market in markets
            },
        )

    def _resolve_unknown_listing_ages(self, markets: list[Market]) -> list[Market]:
        config = self.settings.universe
        liquid_unknown = [
            market
            for market in markets
            if market.listing_days == 0
            and market.symbol not in self._listing_age_checked
            and market.quote_asset == "USDT"
            and market.base_asset not in config.excluded_base_assets
            and market.quote_volume_24h >= config.minimum_quote_volume_24h
            and market.spread_ratio <= config.maximum_spread_ratio
            and not market.abnormal
        ]
        # Bound API usage. The most liquid unknown-age contracts are the ones most
        # likely to enter the candidate set.
        liquid_unknown.sort(key=lambda market: market.quote_volume_24h, reverse=True)
        check_limit = config.maximum_scan_candidates * 3
        confirmed_symbols: set[str] = set()
        cutoff = datetime.now(UTC) - timedelta(days=config.minimum_listing_days)
        for market in liquid_unknown[:check_limit]:
            daily = self.client.candles(market.symbol, "1D", limit=config.minimum_listing_days + 1)
            if len(daily) >= config.minimum_listing_days + 1 and daily[0].timestamp <= cutoff:
                confirmed_symbols.add(market.symbol)
            self._listing_age_checked.add(market.symbol)
        self._confirmed_listing_symbols.update(confirmed_symbols)
        return [
            replace(market, listing_days=config.minimum_listing_days)
            if market.symbol in self._confirmed_listing_symbols
            else market
            for market in markets
        ]

    def _high_volatility_candidates(self, markets: list[Market]) -> list[Market]:
        ranked = sorted(
            markets,
            key=lambda market: (
                market.intraday_range_ratio,
                abs(market.change_24h),
                market.quote_volume_24h,
            ),
            reverse=True,
        )
        return ranked[: self.settings.universe.maximum_scan_candidates]


def closed_candles(
    candles: list,
    interval_seconds: int,
    now: datetime | None = None,
) -> list:
    """Exclude the currently forming candle from signal evaluation."""
    current = now or datetime.now(UTC)
    return [
        candle
        for candle in candles
        if candle.timestamp + timedelta(seconds=interval_seconds) <= current
    ]
