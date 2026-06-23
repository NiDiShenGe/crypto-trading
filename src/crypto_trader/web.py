from __future__ import annotations

from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

from .config import load_settings
from .storage import EventStore


STATIC_DIR = Path(__file__).with_name("web_static")
STRATEGY_NAMES = {
    "breakout_retest": "突破回踩",
    "trend_pullback": "趋势回调延续",
    "volatility_squeeze": "波动压缩爆发",
}


def dashboard_data(store: EventStore) -> dict:
    settings = load_settings()
    broker = store.load_state("paper_broker") or {
        "initial_equity": 100.0,
        "cash": 100.0,
        "positions": [],
    }
    runtime = store.load_state("runtime_risk") or {}
    fills = store.recent_by_type("paper_fill", 100)
    scans = store.recent_by_type("market_scan", 50)
    signals = store.recent_by_type("strategy_signal", 50)
    errors = (
        store.recent_by_type("scanner_error", 20)
        + store.recent_by_type("notification_error", 20)
        + store.recent_by_type("realtime_error", 20)
        + store.recent_by_type("realtime_disconnected", 20)
    )

    realized_pnl = sum(float(item["payload"].get("realized_pnl", 0)) for item in fills)
    closed_fills = [
        item for item in fills if item["payload"].get("reason") != "paper entry"
    ]
    wins = sum(
        1 for item in closed_fills
        if float(item["payload"].get("realized_pnl", 0)) > 0
    )
    losses = sum(
        1 for item in closed_fills
        if float(item["payload"].get("realized_pnl", 0)) < 0
    )
    initial_equity = float(broker.get("initial_equity", 100))
    latest_scan = scans[0] if scans else None
    cash = float(broker.get("cash", initial_equity))
    positions = broker.get("positions", [])
    enriched_positions: list[dict] = []
    unrealized_pnl = 0.0
    open_margin = 0.0
    for raw in positions:
        position = dict(raw)
        current_price = float(position.get("current_price") or position["entry_price"])
        entry_price = float(position["entry_price"])
        quantity = float(position["quantity"])
        direction = 1 if position["side"] == "long" else -1
        position_pnl = (current_price - entry_price) * quantity * direction
        margin = entry_price * quantity / max(float(position["leverage"]), 1)
        position["current_price"] = current_price
        position["unrealized_pnl"] = position_pnl
        position["unrealized_return"] = position_pnl / margin if margin else 0
        position["margin"] = margin
        enriched_positions.append(position)
        unrealized_pnl += position_pnl
        open_margin += margin
    current_equity = cash + open_margin + unrealized_pnl
    maximum_positions = (
        settings.risk.test_maximum_positions
        if initial_equity < settings.risk.test_equity_threshold
        else settings.risk.production_maximum_positions
    )
    strategy_performance: dict[str, dict] = {}
    for strategy_id, name in STRATEGY_NAMES.items():
        strategy_signals = [
            item for item in signals
            if item["payload"].get("strategy_id", "breakout_retest") == strategy_id
        ]
        exits = [
            item for item in fills
            if item["payload"].get("strategy_id", "breakout_retest") == strategy_id
            and item["payload"].get("reason") != "paper entry"
        ]
        pnls = [float(item["payload"].get("realized_pnl", 0)) for item in exits]
        positive = sum(value for value in pnls if value > 0)
        negative = -sum(value for value in pnls if value < 0)
        realized_rs = [
            float(item["payload"].get("realized_r", 0))
            for item in exits
            if item["payload"].get("realized_r") is not None
        ]
        candidates = (
            (latest_scan or {}).get("payload", {})
            .get("strategy_candidates", {})
            .get(strategy_id, [])
        )
        if not isinstance(candidates, list):
            candidates = []
        strategy_performance[strategy_id] = {
            "name": name,
            "automatic_trading": settings.strategies.get(
                strategy_id
            ).automatic_trading if strategy_id in settings.strategies else True,
            "signals": len(strategy_signals),
            "selected_signals": sum(
                bool(item["payload"].get("selected", True))
                for item in strategy_signals
            ),
            "exits": len(exits),
            "net_pnl": sum(pnls),
            "win_rate": (
                sum(value > 0 for value in pnls) / len(pnls) if pnls else 0
            ),
            "average_r": (
                sum(realized_rs) / len(realized_rs) if realized_rs else 0
            ),
            "profit_factor": (
                positive / negative if negative > 0 else (None if positive == 0 else 999)
            ),
            "candidates": candidates,
        }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "PAPER",
        "account": {
            "initial_equity": initial_equity,
            "cash": cash,
            "current_equity": current_equity,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "return_pct": realized_pnl / initial_equity if initial_equity else 0,
            "open_positions": len(positions),
            "maximum_positions": maximum_positions,
            "high_watermark": runtime.get("equity_high_watermark", initial_equity),
            "consecutive_losses": runtime.get("consecutive_losses", 0),
        },
        "positions": enriched_positions,
        "latest_scan": latest_scan,
        "fills": fills[:30],
        "signals": signals[:30],
        "scans": scans,
        "errors": sorted(errors, key=lambda item: item["id"], reverse=True)[:20],
        "performance": {
            "wins": wins,
            "losses": losses,
            "win_rate": wins / (wins + losses) if wins + losses else 0,
        },
        "strategy_performance": strategy_performance,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    store = EventStore()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            self._json(dashboard_data(self.store))
        elif path in {"/", "/index.html"}:
            self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._file(STATIC_DIR / "app.js", "text/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
        elif path == "/health":
            self._json({"status": "ok", "mode": "PAPER"})
        else:
            self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    DashboardHandler.store.initialize()
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"web dashboard: http://{host}:{port}")
    print("local access only | press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("web dashboard stopped")
    finally:
        server.server_close()
