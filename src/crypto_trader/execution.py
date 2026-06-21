from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from .config import Settings
from .domain import AccountState, Side, Signal
from .notifications import EmailNotifier
from .paper import PaperBroker, PaperFill, PaperPosition
from .risk import RiskManager
from .scanner import ScanResult
from .storage import EventStore


@dataclass
class RuntimeRiskState:
    trading_day: date
    day_start_equity: float
    equity_high_watermark: float
    consecutive_losses: int = 0


class PaperTradingEngine:
    def __init__(
        self,
        settings: Settings,
        store: EventStore,
        broker: PaperBroker,
        notifier: EmailNotifier | None = None,
        runtime_state: dict | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.broker = broker
        self.notifier = notifier
        now = datetime.now(UTC).date()
        if runtime_state:
            self.runtime = RuntimeRiskState(
                trading_day=date.fromisoformat(runtime_state["trading_day"]),
                day_start_equity=float(runtime_state["day_start_equity"]),
                equity_high_watermark=float(runtime_state["equity_high_watermark"]),
                consecutive_losses=int(runtime_state.get("consecutive_losses", 0)),
            )
        else:
            self.runtime = RuntimeRiskState(now, broker.initial_equity, broker.initial_equity)

    def process(self, result: ScanResult) -> list[PaperFill]:
        fills: list[PaperFill] = []
        prices = result.prices
        fills.extend(self._manage_positions(prices))
        equity = self.broker.equity(prices)
        self._roll_day(equity)
        self.runtime.equity_high_watermark = max(
            self.runtime.equity_high_watermark, equity
        )
        for signal in result.signals:
            if signal.symbol in self.broker.positions or signal.symbol not in prices:
                continue
            live_signal = Signal(
                symbol=signal.symbol,
                side=signal.side,
                entry=prices[signal.symbol],
                stop=signal.stop,
                confidence=signal.confidence,
                reason=signal.reason,
            )
            stop_is_valid = (
                live_signal.stop < live_signal.entry
                if live_signal.side is Side.LONG
                else live_signal.stop > live_signal.entry
            )
            if not stop_is_valid:
                self.store.append(
                    "entry_rejected",
                    {"reason": "stale signal: stop is invalid at current price"},
                    signal.symbol,
                )
                continue
            account = AccountState(
                equity=equity,
                day_start_equity=self.runtime.day_start_equity,
                equity_high_watermark=self.runtime.equity_high_watermark,
                open_positions=len(self.broker.positions),
                consecutive_losses=self.runtime.consecutive_losses,
            )
            decision = RiskManager(self.settings.risk).evaluate_entry(account, live_signal)
            if not decision.approved:
                self.store.append(
                    "entry_rejected",
                    {"reason": decision.reason, "mode": decision.mode.value},
                    signal.symbol,
                )
                continue
            try:
                fill = self.broker.open_position(
                    live_signal, decision.quantity, decision.leverage
                )
            except ValueError as exc:
                self.store.append("entry_rejected", {"reason": str(exc)}, signal.symbol)
                continue
            fills.append(fill)
            equity = self.broker.equity(prices)
            self._record_fill(fill, decision.leverage)
        self.store.save_state("paper_broker", self.broker.to_dict())
        self.store.save_state(
            "runtime_risk",
            {
                "trading_day": self.runtime.trading_day.isoformat(),
                "day_start_equity": self.runtime.day_start_equity,
                "equity_high_watermark": self.runtime.equity_high_watermark,
                "consecutive_losses": self.runtime.consecutive_losses,
            },
        )
        return fills

    def _manage_positions(self, prices: dict[str, float]) -> list[PaperFill]:
        fills: list[PaperFill] = []
        for symbol in list(self.broker.positions):
            if symbol not in prices:
                continue
            position = self.broker.positions[symbol]
            price = prices[symbol]
            risk = abs(position.entry_price - position.initial_stop_price)
            if risk <= 0:
                continue
            position.best_price = (
                max(position.best_price, price)
                if position.side is Side.LONG
                else min(position.best_price, price)
            )
            stop_hit = (
                price <= position.stop_price
                if position.side is Side.LONG
                else price >= position.stop_price
            )
            if stop_hit:
                fill = self.broker.close_position(symbol, price, "stop loss")
                fills.append(fill)
                self._after_exit(fill)
                continue

            favorable = (
                price - position.entry_price
                if position.side is Side.LONG
                else position.entry_price - price
            )
            if favorable >= risk * self.settings.strategy.breakeven_at_r:
                if position.side is Side.LONG:
                    position.stop_price = max(position.stop_price, position.entry_price)
                else:
                    position.stop_price = min(position.stop_price, position.entry_price)

            if (
                not position.first_take_profit_done
                and favorable >= risk * self.settings.strategy.first_take_profit_at_r
            ):
                quantity = position.original_quantity * self.settings.strategy.first_take_profit_fraction
                fill = self.broker.close_position(symbol, price, "first take profit", quantity)
                fills.append(fill)
                self._after_exit(fill)
                if symbol not in self.broker.positions:
                    continue
                self.broker.positions[symbol].first_take_profit_done = True
                position = self.broker.positions[symbol]

            if favorable >= risk * self.settings.strategy.breakeven_at_r:
                trailing_distance = (
                    risk
                    / self.settings.strategy.stop_atr_multiple
                    * self.settings.strategy.trailing_atr_multiple
                )
                if position.side is Side.LONG:
                    position.stop_price = max(
                        position.stop_price, position.best_price - trailing_distance
                    )
                else:
                    position.stop_price = min(
                        position.stop_price, position.best_price + trailing_distance
                    )
        return fills

    def _after_exit(self, fill: PaperFill) -> None:
        self.runtime.consecutive_losses = (
            self.runtime.consecutive_losses + 1 if fill.realized_pnl < 0 else 0
        )
        self._record_fill(fill)

    def _record_fill(self, fill: PaperFill, leverage: int | None = None) -> None:
        payload = {
            "side": fill.side.value,
            "quantity": fill.quantity,
            "price": fill.price,
            "fee": fill.fee,
            "realized_pnl": fill.realized_pnl,
            "reason": fill.reason,
        }
        if leverage is not None:
            payload["leverage"] = leverage
        self.store.append("paper_fill", payload, fill.symbol)
        self._notify(
            f"{fill.symbol} {fill.reason}",
            (
                f"Symbol: {fill.symbol}\nSide: {fill.side.value}\n"
                f"Quantity: {fill.quantity:g}\nPrice: {fill.price:g}\n"
                f"Realized PnL: {fill.realized_pnl:.4f} USDT\n"
                f"Paper equity: {self.broker.cash:.4f} USDT"
            ),
        )

    def _notify(self, subject: str, body: str) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.send(subject, body)
        except Exception as exc:
            self.store.append("notification_error", {"error": str(exc)})

    def _roll_day(self, equity: float) -> None:
        today = datetime.now(UTC).date()
        if today != self.runtime.trading_day:
            self.runtime.trading_day = today
            self.runtime.day_start_equity = equity
