from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

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
        if close_quantity <= 0:
            raise ValueError("close quantity must be positive")
        fill_price = self._adverse_price(market_price, position.side, opening=False)
        notional = fill_price * close_quantity
        exit_fee = notional * self.taker_fee_rate
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
