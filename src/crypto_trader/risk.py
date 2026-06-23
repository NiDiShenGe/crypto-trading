from __future__ import annotations

from dataclasses import dataclass

from .config import RiskConfig
from .domain import AccountState, Signal, TradingMode


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    mode: TradingMode
    reason: str
    quantity: float = 0.0
    leverage: int = 0


class RiskManager:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def trading_mode(self, account: AccountState, exchange_healthy: bool = True) -> TradingMode:
        if not exchange_healthy:
            return TradingMode.REDUCE_ONLY
        if account.day_start_equity <= 0 or account.equity_high_watermark <= 0:
            return TradingMode.HALTED
        daily_loss = (account.day_start_equity - account.equity) / account.day_start_equity
        drawdown = (account.equity_high_watermark - account.equity) / account.equity_high_watermark
        if drawdown >= self.config.maximum_drawdown:
            return TradingMode.REDUCE_ONLY
        if daily_loss >= self.config.daily_loss_limit:
            return TradingMode.REDUCE_ONLY
        if (
            self.config.enable_consecutive_loss_limit
            and account.consecutive_losses >= self.config.maximum_consecutive_losses
        ):
            return TradingMode.REDUCE_ONLY
        return TradingMode.NORMAL

    def evaluate_entry(
        self,
        account: AccountState,
        signal: Signal,
        existing_symbol_risk: float = 0.0,
        exchange_healthy: bool = True,
        symbol_maximum_leverage: int | None = None,
    ) -> RiskDecision:
        mode = self.trading_mode(account, exchange_healthy)
        if mode is not TradingMode.NORMAL:
            return RiskDecision(False, mode, "account safety gate blocks new positions")
        if account.equity <= 0 or signal.entry <= 0 or signal.stop_distance <= 0:
            return RiskDecision(False, TradingMode.HALTED, "invalid equity or price data")

        maximum_positions = (
            self.config.test_maximum_positions
            if account.equity < self.config.test_equity_threshold
            else self.config.production_maximum_positions
        )
        if account.open_positions >= maximum_positions:
            return RiskDecision(False, mode, "maximum concurrent positions reached")

        risk_budget = account.equity * self.config.risk_per_trade
        remaining_symbol_risk = account.equity * self.config.maximum_symbol_risk - existing_symbol_risk
        risk_budget = min(risk_budget, remaining_symbol_risk)
        if risk_budget <= 0:
            return RiskDecision(False, mode, "symbol risk limit reached")

        quantity = risk_budget / signal.stop_distance
        leverage = self.dynamic_leverage(
            signal.stop_distance / signal.entry,
            symbol_maximum_leverage=symbol_maximum_leverage,
        )
        return RiskDecision(True, mode, "entry approved", quantity=quantity, leverage=leverage)

    def dynamic_leverage(
        self,
        stop_ratio: float,
        *,
        symbol_maximum_leverage: int | None = None,
    ) -> int:
        if self.config.use_exchange_max_leverage and symbol_maximum_leverage:
            return max(1, symbol_maximum_leverage)
        if stop_ratio <= 0:
            return self.config.minimum_leverage
        target = round(0.02 / stop_ratio)
        return max(self.config.minimum_leverage, min(target, self.config.maximum_leverage))
