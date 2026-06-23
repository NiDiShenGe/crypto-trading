from crypto_trader.config import RiskConfig
from crypto_trader.domain import AccountState, Side, Signal, TradingMode
from crypto_trader.risk import RiskManager


CONFIG = RiskConfig(
    risk_per_trade=0.01,
    maximum_symbol_risk=0.02,
    daily_loss_limit=0.05,
    maximum_drawdown=0.15,
    test_maximum_positions=3,
    production_maximum_positions=5,
    test_equity_threshold=200,
    minimum_leverage=2,
    maximum_leverage=5,
    maximum_consecutive_losses=4,
)


def account(**changes) -> AccountState:
    values = dict(
        equity=100,
        day_start_equity=100,
        equity_high_watermark=100,
        open_positions=0,
    )
    values.update(changes)
    return AccountState(**values)


def test_position_size_risks_one_percent() -> None:
    decision = RiskManager(CONFIG).evaluate_entry(
        account(),
        Signal("ALTUSDT", Side.LONG, entry=10, stop=9.5, confidence=0.8, reason="test"),
    )
    assert decision.approved
    assert decision.quantity == 2
    assert 2 <= decision.leverage <= 5


def test_daily_loss_switches_to_reduce_only() -> None:
    decision = RiskManager(CONFIG).evaluate_entry(
        account(equity=95),
        Signal("ALTUSDT", Side.LONG, entry=10, stop=9, confidence=0.8, reason="test"),
    )
    assert not decision.approved
    assert decision.mode is TradingMode.REDUCE_ONLY


def test_drawdown_and_exchange_failure_block_entries() -> None:
    manager = RiskManager(CONFIG)
    assert manager.trading_mode(account(equity=84, equity_high_watermark=100)) is TradingMode.REDUCE_ONLY
    assert manager.trading_mode(account(), exchange_healthy=False) is TradingMode.REDUCE_ONLY


def test_test_account_position_limit_is_three() -> None:
    decision = RiskManager(CONFIG).evaluate_entry(
        account(open_positions=3),
        Signal("ALTUSDT", Side.LONG, entry=10, stop=9, confidence=0.8, reason="test"),
    )
    assert not decision.approved


def test_exchange_maximum_leverage_is_used_when_enabled() -> None:
    config = RiskConfig(
        risk_per_trade=0.05,
        maximum_symbol_risk=0.05,
        daily_loss_limit=0.25,
        maximum_drawdown=0.50,
        test_maximum_positions=3,
        production_maximum_positions=5,
        test_equity_threshold=200,
        minimum_leverage=2,
        maximum_leverage=125,
        maximum_consecutive_losses=4,
        use_exchange_max_leverage=True,
    )
    decision = RiskManager(config).evaluate_entry(
        account(equity=1000, day_start_equity=1000, equity_high_watermark=1000),
        Signal("ALTUSDT", Side.LONG, entry=10, stop=9.5, confidence=0.8, reason="test"),
        symbol_maximum_leverage=75,
    )
    assert decision.approved
    assert decision.leverage == 75
    assert decision.quantity == 100


def test_consecutive_loss_gate_can_be_disabled_for_testing() -> None:
    config = RiskConfig(
        risk_per_trade=0.05,
        maximum_symbol_risk=0.05,
        daily_loss_limit=0.25,
        maximum_drawdown=0.50,
        test_maximum_positions=3,
        production_maximum_positions=5,
        test_equity_threshold=200,
        minimum_leverage=2,
        maximum_leverage=125,
        maximum_consecutive_losses=4,
        enable_consecutive_loss_limit=False,
    )
    mode = RiskManager(config).trading_mode(
        account(
            equity=1000,
            day_start_equity=1000,
            equity_high_watermark=1000,
            consecutive_losses=20,
        )
    )
    assert mode is TradingMode.NORMAL
