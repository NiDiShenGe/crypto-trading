from __future__ import annotations

import json
from threading import Event, Lock, Thread
import time

import websocket

from .execution import PaperTradingEngine
from .storage import EventStore


class BitgetPositionMonitor:
    URL = "wss://ws.bitget.com/v2/ws/public"

    def __init__(self, engine: PaperTradingEngine, store: EventStore) -> None:
        self.engine = engine
        self.store = store
        self._stop = Event()
        self._connected = Event()
        self._thread: Thread | None = None
        self._heartbeat: Thread | None = None
        self._ws: websocket.WebSocketApp | None = None
        self._subscribed: set[str] = set()
        self._send_lock = Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = Thread(target=self._run, name="bitget-position-monitor", daemon=True)
        self._thread.start()
        self._heartbeat = Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            self._ws.close()

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            self._connected.clear()
            self._subscribed.clear()
            self._ws = websocket.WebSocketApp(
                self.URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._ws.run_forever(ping_interval=0)
            if self._stop.is_set():
                break
            time.sleep(delay)
            delay = min(delay * 2, 30)

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        self._connected.set()
        self.store.append("realtime_connected", {"channel": "ticker"})
        self._sync_subscriptions()

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        if message == "pong":
            return
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        if payload.get("event") == "error":
            self.store.append("realtime_error", payload)
            return
        for item in payload.get("data", []):
            symbol = item.get("instId") or item.get("symbol")
            price = self._price(item)
            if not symbol or price <= 0:
                continue
            fills = self.engine.process_realtime_price(symbol, price)
            for fill in fills:
                print(
                    f"REALTIME FILL {fill.symbol} {fill.reason} "
                    f"price={fill.price:g} pnl={fill.realized_pnl:.4f}"
                )
        self._sync_subscriptions()

    def _on_error(self, ws: websocket.WebSocketApp, error: object) -> None:
        if not self._stop.is_set():
            self.store.append("realtime_error", {"error": str(error)})

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        status_code: int | None,
        message: str | None,
    ) -> None:
        self._connected.clear()
        if not self._stop.is_set():
            self.store.append(
                "realtime_disconnected",
                {"status_code": status_code, "message": message or ""},
            )

    def _heartbeat_loop(self) -> None:
        last_ping = time.monotonic()
        while not self._stop.wait(1):
            if not self._connected.is_set() or not self._ws:
                continue
            self._sync_subscriptions()
            if time.monotonic() - last_ping >= 25:
                self._send_text("ping")
                last_ping = time.monotonic()

    def _sync_subscriptions(self) -> None:
        if not self._connected.is_set():
            return
        desired = self.engine.position_symbols()
        added = desired - self._subscribed
        removed = self._subscribed - desired
        if added:
            self._send_operation("subscribe", added)
            self._subscribed.update(added)
        if removed:
            self._send_operation("unsubscribe", removed)
            self._subscribed.difference_update(removed)

    def _send_operation(self, operation: str, symbols: set[str]) -> None:
        self._send_text(json.dumps({
            "op": operation,
            "args": [
                {
                    "instType": "USDT-FUTURES",
                    "channel": "ticker",
                    "instId": symbol,
                }
                for symbol in sorted(symbols)
            ],
        }))

    def _send_text(self, message: str) -> None:
        with self._send_lock:
            if self._ws and self._connected.is_set():
                try:
                    self._ws.send(message)
                except Exception as exc:
                    if not self._stop.is_set():
                        self.store.append("realtime_error", {"error": str(exc)})

    @staticmethod
    def _price(item: dict) -> float:
        bid = float(item.get("bidPr") or 0)
        ask = float(item.get("askPr") or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return float(item.get("lastPr") or item.get("markPrice") or 0)
