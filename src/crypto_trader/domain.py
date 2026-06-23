from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class TradingMode(str, Enum):
    NORMAL = "normal"
    REDUCE_ONLY = "reduce_only"
    HALTED = "halted"


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Market:
    symbol: str
    base_asset: str
    quote_asset: str
    listing_days: int
    quote_volume_24h: float
    bid: float
    ask: float
    abnormal: bool = False
    change_24h: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    maximum_leverage: int = 1
    is_rwa: bool = False

    @property
    def spread_ratio(self) -> float:
        midpoint = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / midpoint if midpoint > 0 else float("inf")

    @property
    def intraday_range_ratio(self) -> float:
        midpoint = (self.high_24h + self.low_24h) / 2
        return (self.high_24h - self.low_24h) / midpoint if midpoint > 0 else 0.0


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: Side
    entry: float
    stop: float
    confidence: float
    reason: str
    breakout_level: float = 0.0
    strategy_id: str = "breakout_retest"
    score: float = 0.0
    invalidation_level: float = 0.0

    @property
    def stop_distance(self) -> float:
        return abs(self.entry - self.stop)


@dataclass(frozen=True)
class AccountState:
    equity: float
    day_start_equity: float
    equity_high_watermark: float
    open_positions: int
    consecutive_losses: int = 0
