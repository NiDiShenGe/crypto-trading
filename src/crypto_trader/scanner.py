from __future__ import annotations

from dataclasses import dataclass, replace
from dataclasses import field
from datetime import UTC, datetime, timedelta
from statistics import median

from .config import Settings, StrategyRuntimeConfig
from .domain import Market, Signal
from .exchange.bitget import BitgetClient
from .indicators import ema
from .strategies import (
    AdaptiveLiangyiSixiangStrategy,
    VolatilitySqueezeStrategy,
)
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
    closed_prices: dict[str, float] = field(default_factory=dict)
    closed_candle_times: dict[str, datetime] = field(default_factory=dict)
    liangyi_directions: dict[str, str] = field(default_factory=dict)
    liangyi_candle_times: dict[str, datetime] = field(default_factory=dict)
    all_signals: tuple[Signal, ...] = ()
    strategy_candidates: dict[str, tuple[str, ...]] = field(default_factory=dict)


class MarketScanner:
    def __init__(self, client: BitgetClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self.strategy = BreakoutRetestStrategy(settings.strategy)
        self._strategy_configs = {
            "breakout_retest": settings.strategies.get(
                "breakout_retest",
                StrategyRuntimeConfig(candidate_limit=20, priority=1),
            ),
            "volatility_squeeze": settings.strategies.get(
                "volatility_squeeze",
                StrategyRuntimeConfig(candidate_limit=30, priority=3),
            ),
            "adaptive_liangyi_sixiang": settings.strategies.get(
                "adaptive_liangyi_sixiang",
                StrategyRuntimeConfig(candidate_limit=20, priority=4),
            ),
        }
        self.volatility_squeeze = VolatilitySqueezeStrategy(
            settings.strategy,
            self._strategy_configs["volatility_squeeze"],
        )
        self.adaptive_liangyi_sixiang = AdaptiveLiangyiSixiangStrategy(
            settings.strategy,
            self._strategy_configs["adaptive_liangyi_sixiang"],
        )
        self._confirmed_listing_symbols: set[str] = set()
        self._listing_age_checked: set[str] = set()

    def scan_once(self, monitored_symbols: set[str] | None = None) -> ScanResult:
        markets = self.client.markets()
        markets = self._resolve_unknown_listing_ages(markets)
        eligible = select_markets(markets, self.settings.universe)
        breakout_config = self._strategy_configs["breakout_retest"]
        squeeze_config = self._strategy_configs["volatility_squeeze"]
        liangyi_config = self._strategy_configs["adaptive_liangyi_sixiang"]
        breakout_candidates = self._high_volatility_candidates(eligible)[
            :breakout_config.candidate_limit
        ]
        analysis_by_symbol = {
            market.symbol: market
            for market in (
                breakout_candidates
                + eligible[: self.settings.universe.maximum_analysis_markets]
            )
        }
        btc_market = next(
            (market for market in markets if market.symbol == "BTCUSDT"),
            None,
        )
        if btc_market is not None:
            analysis_by_symbol.setdefault("BTCUSDT", btc_market)
        all_signals: list[Signal] = []
        closed_prices: dict[str, float] = {}
        closed_candle_times: dict[str, datetime] = {}
        liangyi_directions: dict[str, str] = {}
        liangyi_candle_times: dict[str, datetime] = {}
        candle_cache: dict[str, tuple[list, list]] = {}
        for market in analysis_by_symbol.values():
            signal_candles = closed_candles(
                self.client.candles(market.symbol, "5m", limit=500), 300
            )
            trend_candles = closed_candles(
                self.client.candles(market.symbol, "1H", limit=320), 3600
            )
            candle_cache[market.symbol] = (signal_candles, trend_candles)
            if signal_candles:
                closed_prices[market.symbol] = signal_candles[-1].close
                closed_candle_times[market.symbol] = (
                    signal_candles[-1].timestamp + timedelta(seconds=300)
                )

        squeeze_candidates = sorted(
            analysis_by_symbol.values(),
            key=lambda market: (
                self.volatility_squeeze.continuation_setup_score(
                    candle_cache[market.symbol][1]
                )
                if squeeze_config.squeeze_use_trend_continuation
                else self.volatility_squeeze.setup_score(
                    candle_cache[market.symbol][0]
                )
            ),
            reverse=True,
        )[:squeeze_config.candidate_limit]
        liangyi_candidates = sorted(
            analysis_by_symbol.values(),
            key=lambda market: self.adaptive_liangyi_sixiang.setup_score(
                candle_cache[market.symbol][0],
                candle_cache[market.symbol][1],
            ),
            reverse=True,
        )[:liangyi_config.candidate_limit]

        if breakout_config.enabled:
            for market in breakout_candidates:
                signal = self.strategy.evaluate(
                    market.symbol, *candle_cache[market.symbol]
                )
                if signal:
                    signal = self._liangyi_quality_filter(
                        signal, candle_cache[market.symbol]
                    )
                if signal:
                    all_signals.append(signal)
        if squeeze_config.enabled:
            for market in squeeze_candidates:
                signal = self.volatility_squeeze.evaluate(
                    market.symbol, *candle_cache[market.symbol]
                )
                if signal:
                    signal = self._liangyi_quality_filter(
                        signal, candle_cache[market.symbol]
                    )
                if signal:
                    all_signals.append(signal)
        if liangyi_config.enabled:
            for market in liangyi_candidates:
                signal = self.adaptive_liangyi_sixiang.evaluate(
                    market.symbol, *candle_cache[market.symbol]
                )
                if signal:
                    all_signals.append(signal)

        if self.settings.strategy.use_btc_market_regime:
            regime = market_regime(
                candle_cache.get("BTCUSDT", ([], []))[1],
                self.settings.strategy.btc_regime_ema_fast,
                self.settings.strategy.btc_regime_ema_slow,
                self.settings.strategy.minimum_btc_regime_return,
            )
            all_signals = [
                signal for signal in all_signals
                if regime_allows_side(regime, signal.side)
            ]
        if self.settings.strategy.use_market_breadth_regime:
            breadth = market_breadth_regime(
                [
                    candles
                    for symbol, (candles, _) in candle_cache.items()
                    if symbol != "BTCUSDT"
                ],
                self.settings.strategy.minimum_market_breadth_return,
            )
            all_signals = [
                signal for signal in all_signals
                if signal.side.value == breadth
            ]
        selected = self._arbitrate(all_signals)
        for symbol in (monitored_symbols or set()) - set(candle_cache):
            candles = closed_candles(
                self.client.candles(symbol, "5m", limit=10), 300
            )
            trend_candles = closed_candles(
                self.client.candles(symbol, "1H", limit=320), 3600
            )
            if candles:
                closed_prices[symbol] = candles[-1].close
                closed_candle_times[symbol] = (
                    candles[-1].timestamp + timedelta(seconds=300)
                )
            candle_cache[symbol] = (candles, trend_candles)
        for symbol, candles in candle_cache.items():
            direction = self._liangyi_direction(candles)
            if direction is not None:
                side, candle_time = direction
                liangyi_directions[symbol] = side.value
                liangyi_candle_times[symbol] = candle_time
        return ScanResult(
            scanned_at=datetime.now(UTC),
            total_markets=len(markets),
            eligible_markets=len(eligible),
            scanned_candidates=len(analysis_by_symbol),
            signals=tuple(selected),
            prices={
                market.symbol: (market.bid + market.ask) / 2
                for market in markets
                if market.bid > 0 and market.ask > 0
            },
            maximum_leverages={
                market.symbol: market.maximum_leverage for market in markets
            },
            closed_prices=closed_prices,
            closed_candle_times=closed_candle_times,
            liangyi_directions=liangyi_directions,
            liangyi_candle_times=liangyi_candle_times,
            all_signals=tuple(all_signals),
            strategy_candidates={
                "breakout_retest": tuple(m.symbol for m in breakout_candidates),
                "volatility_squeeze": tuple(m.symbol for m in squeeze_candidates),
                "adaptive_liangyi_sixiang": tuple(
                    m.symbol for m in liangyi_candidates
                ),
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
            and not market.is_rwa
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
                market.intraday_range_ratio
                + abs(market.change_24h) * 2,
                abs(market.change_24h),
                market.quote_volume_24h,
            ),
            reverse=True,
        )
        return ranked

    def _rank_trend_candidates(
        self,
        markets: list[Market],
        candle_cache: dict[str, tuple[list, list]],
    ) -> list[Market]:
        def score(market: Market) -> float:
            trend = candle_cache[market.symbol][1]
            period = self.settings.strategy.ema_slow_period
            if len(trend) < period + 4:
                return 0
            closes = [c.close for c in trend]
            fast = ema(closes, self.settings.strategy.ema_fast_period)
            slow = ema(closes, period)
            previous_fast = ema(closes[:-3], self.settings.strategy.ema_fast_period)
            separation = abs(fast - slow) / max(abs(slow), 1e-12)
            slope = abs(fast - previous_fast) / max(abs(previous_fast), 1e-12)
            return separation * 10 + slope * 20

        return sorted(markets, key=score, reverse=True)

    def _arbitrate(self, signals: list[Signal]) -> list[Signal]:
        winners: dict[str, Signal] = {}
        for signal in signals:
            current = winners.get(signal.symbol)
            if current is None:
                winners[signal.symbol] = signal
                continue
            current_priority = self._strategy_configs[current.strategy_id].priority
            signal_priority = self._strategy_configs[signal.strategy_id].priority
            if (signal.score, signal_priority) > (
                current.score,
                current_priority,
            ):
                winners[signal.symbol] = signal
        return list(winners.values())

    def _liangyi_quality_filter(
        self,
        signal: Signal,
        candles: tuple[list, list],
    ) -> Signal | None:
        filter_candles = (
            candles[1]
            if self._strategy_configs[
                "adaptive_liangyi_sixiang"
            ].adaptive_timeframe_minutes >= 60
            else candles[0]
        )
        return self.adaptive_liangyi_sixiang.score_signal(
            signal,
            filter_candles,
            candles[1],
        )

    def _liangyi_direction(
        self,
        candles: tuple[list, list],
    ) -> tuple[Side, datetime] | None:
        filter_candles = (
            candles[1]
            if self._strategy_configs[
                "adaptive_liangyi_sixiang"
            ].adaptive_timeframe_minutes >= 60
            else candles[0]
        )
        return self.adaptive_liangyi_sixiang.momentum_direction_with_time(
            filter_candles,
            candles[1],
        )


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


def regime_allows_side(regime: str, side: Side) -> bool:
    """A neutral benchmark is non-directional, not a trading halt."""
    return regime == "neutral" or side.value == regime


def market_regime(
    trend_candles: list,
    fast_period: int = 20,
    slow_period: int = 50,
    minimum_return: float = 0.02,
) -> str:
    if len(trend_candles) < slow_period + 4:
        return "neutral"
    closes = [c.close for c in trend_candles]
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    previous_fast = ema(closes[:-3], fast_period)
    previous_slow = ema(closes[:-3], slow_period)
    anchor = closes[-min(240, len(closes))]
    momentum = closes[-1] / anchor - 1 if anchor > 0 else 0
    if (
        closes[-1] > fast > slow
        and fast > previous_fast
        and slow >= previous_slow
        and momentum >= minimum_return
    ):
        return "long"
    if (
        closes[-1] < fast < slow
        and fast < previous_fast
        and slow <= previous_slow
        and momentum <= -minimum_return
    ):
        return "short"
    return "neutral"


def market_breadth_regime(
    markets: list[list],
    minimum_return: float = 0.005,
    lookback_bars: int = 288,
) -> str:
    returns = [
        candles[-1].close / candles[-(lookback_bars + 1)].close - 1
        for candles in markets
        if len(candles) > lookback_bars
        and candles[-(lookback_bars + 1)].close > 0
    ]
    if not returns:
        return "neutral"
    breadth_return = median(returns)
    if breadth_return >= minimum_return:
        return "long"
    if breadth_return <= -minimum_return:
        return "short"
    return "neutral"
