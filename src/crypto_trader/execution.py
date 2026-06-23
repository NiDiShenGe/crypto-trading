from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from threading import RLock
import time

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
    last_exit_times: dict[str, str] = field(default_factory=dict)


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
        self._lock = RLock()
        self._last_realtime_snapshot = 0.0
        now = datetime.now(UTC).date()
        if runtime_state:
            self.runtime = RuntimeRiskState(
                trading_day=date.fromisoformat(runtime_state["trading_day"]),
                day_start_equity=float(runtime_state["day_start_equity"]),
                equity_high_watermark=float(runtime_state["equity_high_watermark"]),
                consecutive_losses=int(runtime_state.get("consecutive_losses", 0)),
                last_exit_times=dict(runtime_state.get("last_exit_times", {})),
            )
        else:
            self.runtime = RuntimeRiskState(now, broker.initial_equity, broker.initial_equity)

    def process(self, result: ScanResult) -> list[PaperFill]:
        with self._lock:
            fills: list[PaperFill] = []
            prices = result.prices
            fills.extend(self._manage_failed_breakouts(result))
            # The 5-minute loop is now an emergency fallback for exits. Normal
            # position management is driven by the realtime WebSocket monitor.
            fills.extend(self._manage_positions(prices))
            equity = self.broker.equity(prices)
            self._roll_day(equity)
            self.runtime.equity_high_watermark = max(
                self.runtime.equity_high_watermark, equity
            )
            for signal in result.signals:
                if signal.symbol in self.broker.positions:
                    self.store.append(
                        "entry_rejected",
                        {
                            "reason": "existing position owns symbol",
                            "strategy_id": signal.strategy_id,
                        },
                        signal.symbol,
                    )
                    continue
                if signal.symbol not in prices:
                    continue
                strategy_runtime = self.settings.strategies.get(signal.strategy_id)
                if strategy_runtime and not strategy_runtime.automatic_trading:
                    self.store.append(
                        "shadow_signal",
                        {"strategy_id": signal.strategy_id, "score": signal.score},
                        signal.symbol,
                    )
                    continue
                if self._in_reentry_cooldown(signal.symbol, result.scanned_at):
                    self.store.append(
                        "entry_rejected",
                        {
                            "reason": "one-bar reentry cooldown",
                            "strategy_id": signal.strategy_id,
                        },
                        signal.symbol,
                    )
                    continue
                live_signal = Signal(
                    symbol=signal.symbol,
                    side=signal.side,
                    entry=prices[signal.symbol],
                    stop=signal.stop,
                    confidence=signal.confidence,
                    reason=signal.reason,
                    breakout_level=signal.breakout_level,
                    strategy_id=signal.strategy_id,
                    score=signal.score,
                    invalidation_level=signal.invalidation_level,
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
                risk_config = self.settings.risk
                if strategy_runtime is not None:
                    risk_config = replace(
                        risk_config,
                        risk_per_trade=strategy_runtime.risk_per_trade,
                        maximum_symbol_risk=max(
                            risk_config.maximum_symbol_risk,
                            strategy_runtime.risk_per_trade,
                        ),
                    )
                decision = RiskManager(risk_config).evaluate_entry(
                    account,
                    live_signal,
                    symbol_maximum_leverage=result.maximum_leverages.get(signal.symbol),
                )
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
            self._save_state()
            return fills

    def process_realtime_price(self, symbol: str, price: float) -> list[PaperFill]:
        """Manage one open position from a realtime ticker update."""
        if price <= 0:
            return []
        with self._lock:
            if symbol not in self.broker.positions:
                return []
            position = self.broker.positions[symbol]
            position.current_price = price
            position.price_updated_at = datetime.now(UTC).isoformat()
            fills, changed = self._manage_position(symbol, price)
            now = time.monotonic()
            snapshot_due = now - self._last_realtime_snapshot >= 1.0
            if fills or snapshot_due:
                self._save_state()
                self._last_realtime_snapshot = now
            return fills

    def _manage_positions(self, prices: dict[str, float]) -> list[PaperFill]:
        fills: list[PaperFill] = []
        for symbol in list(self.broker.positions):
            if symbol not in prices:
                continue
            position_fills, _ = self._manage_position(symbol, prices[symbol])
            fills.extend(position_fills)
        return fills

    def _manage_position(self, symbol: str, price: float) -> tuple[list[PaperFill], bool]:
        position = self.broker.positions[symbol]
        runtime = self.settings.strategies.get(
            position.strategy_id
        )
        changed = False
        risk = abs(position.entry_price - position.initial_stop_price)
        if risk <= 0:
            return [], False
        favorable = (
            price - position.entry_price
            if position.side is Side.LONG
            else position.entry_price - price
        )
        adverse = max(-favorable, 0.0)
        position.max_favorable_excursion = max(
            position.max_favorable_excursion, max(favorable, 0.0)
        )
        position.max_adverse_excursion = max(
            position.max_adverse_excursion, adverse
        )
        position.peak_r = max(
            position.peak_r, position.max_favorable_excursion / risk
        )
        old_best = position.best_price
        position.best_price = (
            max(position.best_price, price)
            if position.side is Side.LONG
            else min(position.best_price, price)
        )
        changed = position.best_price != old_best
        stop_hit = (
            price <= position.stop_price
            if position.side is Side.LONG
            else price >= position.stop_price
        )
        if stop_hit:
            fill = self.broker.close_position(
                symbol, price, self._stop_exit_reason(position)
            )
            self._after_exit(fill)
            return [fill], True

        old_stop = position.stop_price
        breakeven_at_r = (
            runtime.breakeven_at_r
            if runtime else self.settings.strategy.breakeven_at_r
        )
        first_take_profit_at_r = (
            runtime.first_take_profit_at_r
            if runtime else self.settings.strategy.first_take_profit_at_r
        )
        first_take_profit_fraction = (
            runtime.first_take_profit_fraction
            if runtime
            else self.settings.strategy.first_take_profit_fraction
        )
        trailing_atr_multiple = (
            runtime.trailing_atr_multiple
            if runtime else self.settings.strategy.trailing_atr_multiple
        )
        if favorable >= risk * breakeven_at_r:
            if position.side is Side.LONG:
                position.stop_price = max(position.stop_price, position.entry_price)
            else:
                position.stop_price = min(position.stop_price, position.entry_price)
        changed = changed or position.stop_price != old_stop

        fills: list[PaperFill] = []
        if (
            not position.first_take_profit_done
            and favorable >= risk * first_take_profit_at_r
        ):
            quantity = (
                position.original_quantity
                * first_take_profit_fraction
            )
            fill = self.broker.close_position(symbol, price, "first take profit", quantity)
            fills.append(fill)
            self._after_exit(fill)
            if symbol not in self.broker.positions:
                return fills, True
            self.broker.positions[symbol].first_take_profit_done = True
            position = self.broker.positions[symbol]
            changed = True

        if favorable >= risk * breakeven_at_r:
            old_stop = position.stop_price
            trailing_distance = (
                risk
                / self.settings.strategy.stop_atr_multiple
                * trailing_atr_multiple
            )
            if position.side is Side.LONG:
                position.stop_price = max(
                    position.stop_price, position.best_price - trailing_distance
                )
            else:
                position.stop_price = min(
                    position.stop_price, position.best_price + trailing_distance
                )
            changed = changed or position.stop_price != old_stop
        return fills, changed

    def _manage_failed_breakouts(self, result: ScanResult) -> list[PaperFill]:
        fills: list[PaperFill] = []
        for symbol in list(self.broker.positions):
            position = self.broker.positions.get(symbol)
            if (
                position is None
                or position.breakout_level <= 0
                or symbol not in result.closed_prices
            ):
                continue
            close = result.closed_prices[symbol]
            candle_close_time = result.closed_candle_times.get(symbol)
            failure_level = (
                position.invalidation_level or position.breakout_level
            )
            back_inside = (
                close <= failure_level
                if position.side is Side.LONG
                else close >= failure_level
            )
            runtime = self.settings.strategies.get(
                position.strategy_id
            )
            if back_inside:
                position.failure_close_count += 1
            else:
                position.failure_close_count = 0
            required_failure_closes = (
                runtime.failed_breakout_confirmation_bars
                if runtime
                else 1
            )
            reason: str | None = (
                "failed_breakout"
                if position.failure_close_count >= required_failure_closes
                else None
            )
            if reason is None and candle_close_time is not None:
                no_progress_bars = (
                    runtime.no_progress_bars
                    if runtime
                    else self.settings.strategy.failed_breakout_max_bars
                )
                minimum_progress_r = (
                    runtime.minimum_progress_r
                    if runtime
                    else self.settings.strategy.failed_breakout_min_progress_r
                )
                elapsed_bars = int(
                    max(
                        (candle_close_time - position.opened_at).total_seconds(),
                        0,
                    )
                    // 300
                )
                if (
                    elapsed_bars >= no_progress_bars
                    and position.peak_r
                    < minimum_progress_r
                ):
                    reason = "no_progress_exit"
            if reason:
                fill = self.broker.close_position(symbol, close, reason)
                fills.append(fill)
                self._after_exit(fill)
        return fills

    @staticmethod
    def _stop_exit_reason(position: PaperPosition) -> str:
        epsilon = max(abs(position.entry_price) * 1e-9, 1e-12)
        if position.first_take_profit_done:
            return "trailing_stop"
        if position.side is Side.LONG:
            if position.stop_price > position.entry_price + epsilon:
                return "trailing_stop"
            if position.stop_price >= position.entry_price - epsilon:
                return "breakeven_stop"
        else:
            if position.stop_price < position.entry_price - epsilon:
                return "trailing_stop"
            if position.stop_price <= position.entry_price + epsilon:
                return "breakeven_stop"
        return "initial_stop"

    def position_symbols(self) -> set[str]:
        with self._lock:
            return set(self.broker.positions)

    def _save_state(self) -> None:
        self.store.save_state("paper_broker", self.broker.to_dict())
        self.store.save_state(
            "runtime_risk",
            {
                "trading_day": self.runtime.trading_day.isoformat(),
                "day_start_equity": self.runtime.day_start_equity,
                "equity_high_watermark": self.runtime.equity_high_watermark,
                "consecutive_losses": self.runtime.consecutive_losses,
                "last_exit_times": self.runtime.last_exit_times,
            },
        )

    def _after_exit(self, fill: PaperFill) -> None:
        if fill.symbol not in self.broker.positions:
            self.runtime.last_exit_times[fill.symbol] = fill.occurred_at.isoformat()
        self.runtime.consecutive_losses = (
            self.runtime.consecutive_losses + 1 if fill.realized_pnl < 0 else 0
        )
        self._record_fill(fill)

    def _record_fill(self, fill: PaperFill, leverage: int | None = None) -> None:
        position = self.broker.positions.get(fill.symbol)
        initial_risk = fill.initial_risk
        if position is not None and initial_risk <= 0:
            initial_risk = (
                abs(position.entry_price - position.initial_stop_price)
                * position.original_quantity
            )
        payload = {
            "side": fill.side.value,
            "quantity": fill.quantity,
            "price": fill.price,
            "fee": fill.fee,
            "realized_pnl": fill.realized_pnl,
            "reason": fill.reason,
            "strategy_id": fill.strategy_id,
            "initial_risk": initial_risk,
            "mfe": fill.mfe,
            "mae": fill.mae,
            "peak_r": fill.peak_r,
            "cumulative_fees": fill.cumulative_fees,
            "holding_minutes": fill.holding_minutes,
            "trade_id": (
                fill.trade_id
                or (position.trade_id if position is not None else "")
            ),
            "breakout_level": (
                position.breakout_level if position is not None else 0.0
            ),
        }
        payload["realized_r"] = (
            fill.realized_pnl / initial_risk if initial_risk > 0 else 0.0
        )
        per_unit_risk = fill.risk_per_unit or (
            abs(position.entry_price - position.initial_stop_price)
            if position is not None
            else 0.0
        )
        payload["mfe_r"] = fill.mfe / per_unit_risk if per_unit_risk > 0 else 0.0
        payload["mae_r"] = fill.mae / per_unit_risk if per_unit_risk > 0 else 0.0
        payload["fee_to_risk"] = (
            fill.cumulative_fees / fill.trade_initial_risk
            if fill.trade_initial_risk > 0
            else 0.0
        )
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

    def _in_reentry_cooldown(self, symbol: str, now: datetime) -> bool:
        raw = self.runtime.last_exit_times.get(symbol)
        if not raw:
            return False
        elapsed = (now - datetime.fromisoformat(raw)).total_seconds()
        return elapsed < self.settings.risk.reentry_cooldown_bars * 300
