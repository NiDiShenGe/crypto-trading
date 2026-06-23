from __future__ import annotations

import os
import json
import sys
import time
from datetime import UTC, datetime, timedelta

from .config import load_settings
from .backtest import MultiStrategyBacktester
from .history_cache import HistoricalDataCache
from .optimization import Dataset, StrategyOptimizer
from .validation import validate_cached_strategies
from .env import load_env
from .exchange.bitget import BitgetClient
from .execution import PaperTradingEngine
from .notifications import EmailConfig, EmailNotifier
from .paper import PaperBroker
from .realtime import BitgetPositionMonitor
from .scanner import MarketScanner
from .storage import EventStore
from .web import serve


def main() -> None:
    load_env()
    settings = load_settings()
    store = EventStore()
    store.initialize()
    if os.getenv("EXECUTION_MODE", "paper").lower() != "paper":
        raise RuntimeError("Current version only allows EXECUTION_MODE=paper")

    email_configured = all(
        (
            os.getenv("SMTP_USERNAME"),
            os.getenv("SMTP_PASSWORD"),
            os.getenv("ALERT_EMAIL_FROM"),
            os.getenv("ALERT_EMAIL_TO"),
        )
    )
    print(
        f"crypto-trader ready | exchange=Bitget | mode=PAPER | "
        f"risk/trade={settings.risk.risk_per_trade:.0%} | "
        f"leverage={'EXCHANGE MAX' if settings.risk.use_exchange_max_leverage else str(settings.risk.maximum_leverage) + 'x'}"
    )
    print(f"email alerts: {'configured' if email_configured else 'not configured'}")

    command = sys.argv[1].lower() if len(sys.argv) > 1 else "run"
    if command not in {
        "run", "scan", "test-email", "web", "backtest",
        "fetch-history", "optimize", "validate-strategies",
    }:
        raise SystemExit(
            "Usage: python -m crypto_trader "
            "[run|scan|test-email|web|backtest|fetch-history|"
            "optimize|validate-strategies]"
        )

    if command == "test-email":
        if not email_configured:
            raise SystemExit("Email is not configured in .env")
        _build_notifier().send(
            "Email configuration test",
            "QQ email alerts are configured correctly. This is a test message.",
        )
        print("test email sent")
        return
    if command == "web":
        serve()
        return
    if command == "backtest":
        if len(sys.argv) < 3:
            raise SystemExit(
                "Usage: python -m crypto_trader backtest "
                "SYMBOL [DAYS] [STRATEGY]"
            )
        symbol = sys.argv[2].upper()
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 90
        strategy_ids = (
            {sys.argv[4]} if len(sys.argv) > 4 else None
        )
        client = BitgetClient(demo_mode=True, timeout=20)
        cache = HistoricalDataCache()
        print(f"loading {symbol} 5m history for {days} days...")
        candles, funding = cache.load_or_fetch(client, symbol, days)
        benchmark, _ = (
            (candles, funding)
            if symbol == "BTCUSDT"
            else cache.load_or_fetch(client, "BTCUSDT", days)
        )
        result = MultiStrategyBacktester(settings, strategy_ids).run(
            symbol, candles, funding, benchmark_candles=benchmark
        )
        summary = result.summary()
        store.append("backtest_result", summary, symbol)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if command == "fetch-history":
        if len(sys.argv) < 4:
            raise SystemExit(
                "Usage: python -m crypto_trader fetch-history DAYS SYMBOL..."
            )
        days = int(sys.argv[2])
        symbols = [symbol.upper() for symbol in sys.argv[3:]]
        client = BitgetClient(demo_mode=True, timeout=20)
        cache = HistoricalDataCache()
        for symbol in symbols:
            print(f"fetching {symbol} {days}d...")
            candles, funding = cache.load_or_fetch(
                client, symbol, days
            )
            print(
                f"{symbol}: candles={len(candles)} funding={len(funding)}"
            )
        return
    if command == "optimize":
        if len(sys.argv) < 5:
            raise SystemExit(
                "Usage: python -m crypto_trader optimize "
                "STRATEGY DAYS SYMBOL..."
            )
        strategy_id = sys.argv[2]
        days = int(sys.argv[3])
        symbols = [symbol.upper() for symbol in sys.argv[4:]]
        client = BitgetClient(demo_mode=True, timeout=20)
        cache = HistoricalDataCache()
        datasets = []
        benchmark_candles, _ = cache.load_or_fetch(
            client, "BTCUSDT", days
        )
        for symbol in symbols:
            candles, funding = cache.load_or_fetch(
                client, symbol, days
            )
            datasets.append(Dataset(
                symbol, candles, funding, benchmark_candles
            ))
        results = StrategyOptimizer(settings).optimize(
            strategy_id, datasets
        )
        print(json.dumps(
            [
                {
                    "parameters": item.parameters,
                    "train_score": item.train_score,
                    "validation_score": item.validation_score,
                    "train": item.train_metrics,
                    "validation": item.validation_metrics,
                }
                for item in results[:5]
            ],
            ensure_ascii=False,
            indent=2,
        ))
        return
    if command == "validate-strategies":
        if len(sys.argv) < 4:
            raise SystemExit(
                "Usage: python -m crypto_trader "
                "validate-strategies DAYS SYMBOL..."
            )
        days = int(sys.argv[2])
        symbols = [
            symbol.upper() for symbol in sys.argv[3:]
            if symbol.upper() != "BTCUSDT"
        ]
        report = validate_cached_strategies(
            settings, days, symbols
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    saved_broker = store.load_state("paper_broker")
    old_saved_equity = (
        float(saved_broker.get("initial_equity", settings.paper.initial_equity))
        if saved_broker
        else settings.paper.initial_equity
    )
    saved_broker = _sync_paper_capital(saved_broker, settings.paper.initial_equity)
    runtime_state = _sync_runtime_capital(
        store.load_state("runtime_risk"),
        settings.paper.initial_equity - old_saved_equity,
    )
    broker = (
        PaperBroker.from_dict(saved_broker)
        if saved_broker
        else PaperBroker(
            initial_equity=settings.paper.initial_equity,
            taker_fee_rate=settings.paper.taker_fee_rate,
            slippage_rate=settings.paper.slippage_rate,
        )
    )
    engine = PaperTradingEngine(
        settings,
        store,
        broker,
        _build_notifier() if email_configured else None,
        runtime_state=runtime_state,
    )
    scanner = MarketScanner(BitgetClient(demo_mode=True), settings)
    print(
        f"paper account: cash={broker.cash:.4f} USDT "
        f"open_positions={len(broker.positions)}"
    )

    if command == "scan":
        _scan_and_report(scanner, store, engine, execute_trades=False)
        return

    monitor = BitgetPositionMonitor(engine, store)
    monitor.start()
    print("position monitor: Bitget WebSocket realtime ticker")
    print(
        "signal scanner: after each closed 5-minute candle "
        f"(delay={settings.universe.scan_after_close_delay_seconds}s)"
    )
    while True:
        try:
            wait_seconds, next_scan = seconds_until_next_scan(
                settings.universe.scan_interval_seconds,
                settings.universe.scan_after_close_delay_seconds,
            )
            print(
                "next signal scan: "
                f"{next_scan.astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            time.sleep(wait_seconds)
            _scan_and_report(scanner, store, engine)
        except KeyboardInterrupt:
            monitor.stop()
            print("stopped by user")
            return
        except Exception as exc:
            store.append("scanner_error", {"error": str(exc)})
            print(f"scanner error: {exc}")


def _scan_and_report(
    scanner: MarketScanner,
    store: EventStore,
    engine: PaperTradingEngine,
    execute_trades: bool = True,
) -> None:
    result = scanner.scan_once(engine.position_symbols())
    store.append(
        "market_scan",
        {
            "total_markets": result.total_markets,
            "eligible_markets": result.eligible_markets,
            "scanned_candidates": result.scanned_candidates,
            "signals": len(result.signals),
            "raw_signals": len(result.all_signals),
            "strategy_candidates": {
                name: list(symbols)
                for name, symbols in result.strategy_candidates.items()
            },
        },
    )
    stamp = result.scanned_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"[{stamp}] markets={result.total_markets} eligible={result.eligible_markets} "
        f"scanned={result.scanned_candidates} signals={len(result.signals)}"
    )
    selected_keys = {
        (signal.symbol, signal.strategy_id) for signal in result.signals
    }
    for signal in result.all_signals or result.signals:
        selected = (signal.symbol, signal.strategy_id) in selected_keys
        store.append(
            "strategy_signal",
            {
                "side": signal.side.value,
                "entry": signal.entry,
                "stop": signal.stop,
                "confidence": signal.confidence,
                "reason": signal.reason,
                "strategy_id": signal.strategy_id,
                "score": signal.score,
                "selected": selected,
            },
            signal.symbol,
        )
        if selected:
            print(
                f"SIGNAL {signal.strategy_id} {signal.symbol} "
                f"{signal.side.value.upper()} entry={signal.entry:g} "
                f"stop={signal.stop:g} score={signal.score:.3f}"
            )

    fills = engine.process(result) if execute_trades else []
    for fill in fills:
        print(
            f"PAPER FILL {fill.symbol} {fill.reason} qty={fill.quantity:g} "
            f"price={fill.price:g} pnl={fill.realized_pnl:.4f}"
        )
    equity = engine.broker.equity(result.prices)
    print(
        f"paper equity={equity:.4f} USDT "
        f"cash={engine.broker.cash:.4f} positions={len(engine.broker.positions)}"
    )


def _build_notifier() -> EmailNotifier:
    return EmailNotifier(
        EmailConfig(
            host=os.getenv("SMTP_HOST") or "smtp.qq.com",
            port=int(os.getenv("SMTP_PORT", "465")),
            username=os.environ["SMTP_USERNAME"],
            password=os.environ["SMTP_PASSWORD"],
            sender=os.environ["ALERT_EMAIL_FROM"],
            recipient=os.environ["ALERT_EMAIL_TO"],
        )
    )


def seconds_until_next_scan(
    interval_seconds: int,
    delay_seconds: int,
    now: datetime | None = None,
) -> tuple[float, datetime]:
    """Schedule scans just after the next UTC-aligned candle close."""
    if interval_seconds <= 0 or delay_seconds < 0:
        raise ValueError("invalid scan schedule")
    current = now or datetime.now(UTC)
    timestamp = current.timestamp()
    next_boundary = (int(timestamp // interval_seconds) + 1) * interval_seconds
    target = datetime.fromtimestamp(next_boundary, tz=UTC) + timedelta(
        seconds=delay_seconds
    )
    return max((target - current).total_seconds(), 0), target


def _sync_paper_capital(saved: dict | None, configured_equity: float) -> dict | None:
    """Apply capital config changes without discarding open paper positions."""
    if not saved:
        return None
    old_equity = float(saved.get("initial_equity", configured_equity))
    difference = configured_equity - old_equity
    if abs(difference) < 1e-12:
        return saved
    updated = dict(saved)
    updated["initial_equity"] = configured_equity
    updated["cash"] = float(saved.get("cash", old_equity)) + difference
    return updated


def _sync_runtime_capital(saved: dict | None, difference: float) -> dict | None:
    if not saved or abs(difference) < 1e-12:
        return saved
    updated = dict(saved)
    updated["day_start_equity"] = float(saved["day_start_equity"]) + difference
    updated["equity_high_watermark"] = float(saved["equity_high_watermark"]) + difference
    return updated


if __name__ == "__main__":
    main()
