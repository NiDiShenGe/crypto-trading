from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from .domain import Side, Signal


@dataclass
class PaperPosition:
    symbol: str
    side: Side
    quantity: float
    entry_price: float
    stop_price: float
    leverage: int
    opened_at: datetime
    entry_fee: float
    initial_stop_price: float
    original_quantity: float
    best_price: float
    first_take_profit_done: bool = False
    margin_mode: str = "crossed"
    current_price: float = 0.0
    price_updated_at: str = ""
    breakout_level: float = 0.0
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    peak_r: float = 0.0
    cumulative_fees: float = 0.0
    trade_id: str = ""
    strategy_id: str = "breakout_retest"
    invalidation_level: float = 0.0
    failure_close_count: int = 0
    liangyi_exit_count: int = 0
    last_liangyi_exit_candle_time: str = ""


@dataclass(frozen=True)
class PaperFill:
    symbol: str
    side: Side
    quantity: float
    price: float
    fee: float
    realized_pnl: float
    occurred_at: datetime
    reason: str
    initial_risk: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    peak_r: float = 0.0
    cumulative_fees: float = 0.0
    holding_minutes: float = 0.0
    trade_id: str = ""
    risk_per_unit: float = 0.0
    trade_initial_risk: float = 0.0
    strategy_id: str = "breakout_retest"


@dataclass
class PaperBroker:
    """Local execution simulator driven by Bitget production public prices."""

    initial_equity: float = 100.0
    taker_fee_rate: float = 0.0006
    slippage_rate: float = 0.0005
    cash: float = field(init=False)
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    fills: list[PaperFill] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.initial_equity <= 0:
            raise ValueError("initial equity must be positive")
        self.cash = self.initial_equity

    def open_position(self, signal: Signal, quantity: float, leverage: int) -> PaperFill:
        if signal.symbol in self.positions:
            raise ValueError("position already exists; pyramiding requires a separate risk decision")
        if quantity <= 0 or leverage <= 0:
            raise ValueError("quantity and leverage must be positive")

        fill_price = self._adverse_price(signal.entry, signal.side, opening=True)
        notional = fill_price * quantity
        margin = notional / leverage
        fee = notional * self.taker_fee_rate
        if margin + fee > self.cash:
            raise ValueError("insufficient paper cash")

        self.cash -= margin + fee
        trade_id = uuid4().hex
        risk_per_unit = abs(fill_price - signal.stop)
        self.positions[signal.symbol] = PaperPosition(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            entry_price=fill_price,
            stop_price=signal.stop,
            leverage=leverage,
            opened_at=datetime.now(UTC),
            entry_fee=fee,
            initial_stop_price=signal.stop,
            original_quantity=quantity,
            best_price=fill_price,
            margin_mode="crossed",
            current_price=fill_price,
            price_updated_at=datetime.now(UTC).isoformat(),
            breakout_level=signal.breakout_level,
            cumulative_fees=fee,
            trade_id=trade_id,
            strategy_id=signal.strategy_id,
            invalidation_level=signal.invalidation_level,
        )
        fill = PaperFill(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            price=fill_price,
            fee=fee,
            realized_pnl=0.0,
            occurred_at=datetime.now(UTC),
            reason="paper entry",
            strategy_id=signal.strategy_id,
            trade_id=trade_id,
            risk_per_unit=risk_per_unit,
            trade_initial_risk=risk_per_unit * quantity,
        )
        self.fills.append(fill)
        return fill

    def close_position(
        self,
        symbol: str,
        market_price: float,
        reason: str,
        quantity: float | None = None,
    ) -> PaperFill:
        position = self.positions[symbol]
        close_quantity = position.quantity if quantity is None else min(quantity, position.quantity)
        risk_per_unit = abs(position.entry_price - position.initial_stop_price)
        trade_initial_risk = risk_per_unit * position.original_quantity
        allocated_initial_risk = risk_per_unit * close_quantity
        if close_quantity <= 0:
            raise ValueError("close quantity must be positive")
        fill_price = self._adverse_price(market_price, position.side, opening=False)
        notional = fill_price * close_quantity
        exit_fee = notional * self.taker_fee_rate
        position.cumulative_fees += exit_fee
        direction = 1 if position.side is Side.LONG else -1
        gross_pnl = (fill_price - position.entry_price) * close_quantity * direction
        released_margin = position.entry_price * close_quantity / position.leverage
        entry_fee_share = position.entry_fee * (close_quantity / position.quantity)
        self.cash += released_margin + gross_pnl - exit_fee
        position.quantity -= close_quantity
        position.entry_fee -= entry_fee_share
        if position.quantity <= 1e-12:
            del self.positions[symbol]
        fill = PaperFill(
            symbol=symbol,
            side=position.side,
            quantity=close_quantity,
            price=fill_price,
            fee=exit_fee,
            realized_pnl=gross_pnl - entry_fee_share - exit_fee,
            occurred_at=datetime.now(UTC),
            reason=reason,
            initial_risk=allocated_initial_risk,
            mfe=position.max_favorable_excursion,
            mae=position.max_adverse_excursion,
            peak_r=position.peak_r,
            cumulative_fees=position.cumulative_fees,
            holding_minutes=(
                datetime.now(UTC) - position.opened_at
            ).total_seconds() / 60,
            trade_id=position.trade_id,
            risk_per_unit=risk_per_unit,
            trade_initial_risk=trade_initial_risk,
            strategy_id=position.strategy_id,
        )
        self.fills.append(fill)
        return fill

    def equity(self, prices: dict[str, float]) -> float:
        value = self.cash
        for position in self.positions.values():
            price = prices[position.symbol]
            direction = 1 if position.side is Side.LONG else -1
            margin = position.entry_price * position.quantity / position.leverage
            unrealized = (price - position.entry_price) * position.quantity * direction
            value += margin + unrealized
        return value

    @staticmethod
    def unrealized_pnl(position: PaperPosition, price: float | None = None) -> float:
        current = price if price is not None else position.current_price
        if current <= 0:
            current = position.entry_price
        direction = 1 if position.side is Side.LONG else -1
        return (current - position.entry_price) * position.quantity * direction

    def to_dict(self) -> dict:
        return {
            "initial_equity": self.initial_equity,
            "taker_fee_rate": self.taker_fee_rate,
            "slippage_rate": self.slippage_rate,
            "cash": self.cash,
            "positions": [
                {
                    **position.__dict__,
                    "side": position.side.value,
                    "opened_at": position.opened_at.isoformat(),
                }
                for position in self.positions.values()
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PaperBroker":
        broker = cls(
            initial_equity=float(payload["initial_equity"]),
            taker_fee_rate=float(payload["taker_fee_rate"]),
            slippage_rate=float(payload["slippage_rate"]),
        )
        broker.cash = float(payload["cash"])
        for raw in payload.get("positions", []):
            raw = dict(raw)
            raw.setdefault("breakout_level", 0.0)
            raw.setdefault("max_favorable_excursion", 0.0)
            raw.setdefault("max_adverse_excursion", 0.0)
            raw.setdefault("peak_r", 0.0)
            raw.setdefault(
                "cumulative_fees", float(raw.get("entry_fee", 0.0))
            )
            raw.setdefault(
                "trade_id",
                f"legacy-{raw.get('symbol', 'unknown')}-{raw.get('opened_at', '')}",
            )
            if not raw.get("strategy_id"):
                raw["strategy_id"] = "breakout_retest"
            if not raw.get("invalidation_level"):
                raw["invalidation_level"] = float(
                    raw.get("breakout_level", 0.0) or 0.0
                )
            raw.setdefault("failure_close_count", 0)
            raw.setdefault("liangyi_exit_count", 0)
            raw.setdefault("last_liangyi_exit_candle_time", "")
            position = PaperPosition(
                **{
                    **raw,
                    "side": Side(raw["side"]),
                    "opened_at": datetime.fromisoformat(raw["opened_at"]),
                }
            )
            broker.positions[position.symbol] = position
        return broker

    def _adverse_price(self, price: float, side: Side, *, opening: bool) -> float:
        buy = (side is Side.LONG and opening) or (side is Side.SHORT and not opening)
        multiplier = 1 + self.slippage_rate if buy else 1 - self.slippage_rate
        return price * multiplier
