from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import socket
import time
from datetime import UTC, datetime
from urllib import error, parse, request

from ..domain import Candle, Market


class UnsafeTradingConfiguration(RuntimeError):
    pass


@dataclass(frozen=True)
class BitgetCredentials:
    api_key: str
    api_secret: str
    passphrase: str


class BitgetClient:
    BASE_URL = "https://api.bitget.com"

    def __init__(
        self,
        credentials: BitgetCredentials | None = None,
        *,
        demo_mode: bool = True,
        live_trading_enabled: bool = False,
        timeout: float = 10.0,
    ) -> None:
        if not demo_mode and not live_trading_enabled:
            raise UnsafeTradingConfiguration(
                "live endpoint selected while LIVE_TRADING_ENABLED is false"
            )
        self.credentials = credentials
        self.demo_mode = demo_mode
        self.live_trading_enabled = live_trading_enabled
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> "BitgetClient":
        execution_mode = os.getenv("EXECUTION_MODE", "paper").lower()
        demo = execution_mode == "demo"
        live = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
        values = (
            os.getenv("BITGET_API_KEY", ""),
            os.getenv("BITGET_API_SECRET", ""),
            os.getenv("BITGET_API_PASSPHRASE", ""),
        )
        credentials = BitgetCredentials(*values) if all(values) else None
        return cls(credentials, demo_mode=demo, live_trading_enabled=live)

    @staticmethod
    def signature(
        secret: str,
        timestamp_ms: str,
        method: str,
        request_path: str,
        body: str = "",
    ) -> str:
        payload = f"{timestamp_ms}{method.upper()}{request_path}{body}"
        digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def _headers(self, method: str, request_path: str, body: str) -> dict[str, str]:
        if self.credentials is None:
            raise ValueError("private endpoint requires API credentials")
        timestamp = str(int(time.time() * 1000))
        headers = {
            "ACCESS-KEY": self.credentials.api_key,
            "ACCESS-SIGN": self.signature(
                self.credentials.api_secret, timestamp, method, request_path, body
            ),
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.credentials.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }
        if self.demo_mode:
            headers["paptrading"] = "1"
        return headers

    def public_get(self, path: str, params: dict[str, str] | None = None) -> dict:
        query = parse.urlencode(params or {})
        url = f"{self.BASE_URL}{path}" + (f"?{query}" if query else "")
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                with request.urlopen(url, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                break
            except error.HTTPError as exc:
                last_error = exc
                if exc.code != 429 or attempt == 3:
                    raise
            except (TimeoutError, socket.timeout, error.URLError) as exc:
                last_error = exc
                if attempt == 3:
                    raise
            time.sleep(1.5 * (2 ** attempt))
        else:
            raise RuntimeError(f"Bitget request failed: {last_error}")
        if payload.get("code") != "00000":
            raise RuntimeError(f"Bitget API error: {payload.get('code')} {payload.get('msg')}")
        return payload

    def contracts(self, product_type: str = "USDT-FUTURES") -> list[dict]:
        return self.public_get(
            "/api/v2/mix/market/contracts",
            {"productType": product_type},
        )["data"]

    def tickers(self, product_type: str = "USDT-FUTURES") -> list[dict]:
        return self.public_get(
            "/api/v2/mix/market/tickers",
            {"productType": product_type},
        )["data"]

    def candles(
        self,
        symbol: str,
        granularity: str,
        *,
        product_type: str = "USDT-FUTURES",
        limit: int = 100,
    ) -> list[Candle]:
        payload = self.public_get(
            "/api/v2/mix/market/candles",
            {
                "symbol": symbol,
                "productType": product_type,
                "granularity": granularity,
                "limit": str(limit),
            },
        )
        result = [
            Candle(
                timestamp=datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in payload["data"]
        ]
        return sorted(result, key=lambda candle: candle.timestamp)

    def markets(self, product_type: str = "USDT-FUTURES") -> list[Market]:
        now_ms = int(time.time() * 1000)
        contracts = {
            item["symbol"]: item
            for item in self.contracts(product_type)
            if item.get("symbolType") == "perpetual"
            and item.get("symbolStatus") == "normal"
        }
        result: list[Market] = []
        for ticker in self.tickers(product_type):
            contract = contracts.get(ticker.get("symbol"))
            if not contract:
                continue
            # Bitget currently often leaves launchTime blank while still returning
            # the deprecated openTime field. Prefer launchTime, then fall back.
            launch_ms = self._number(
                contract.get("launchTime") or contract.get("openTime")
            )
            listing_days = int((now_ms - launch_ms) / 86_400_000) if launch_ms > 0 else 0
            result.append(
                Market(
                    symbol=contract["symbol"],
                    base_asset=contract["baseCoin"],
                    quote_asset=contract["quoteCoin"],
                    listing_days=max(listing_days, 0),
                    quote_volume_24h=self._number(
                        ticker.get("usdtVolume") or ticker.get("quoteVolume")
                    ),
                    bid=self._number(ticker.get("bidPr")),
                    ask=self._number(ticker.get("askPr")),
                    abnormal=False,
                    change_24h=self._number(ticker.get("change24h")),
                    high_24h=self._number(ticker.get("high24h")),
                    low_24h=self._number(ticker.get("low24h")),
                    maximum_leverage=max(
                        1, int(self._number(contract.get("maxLever")) or 1)
                    ),
                )
            )
        return result

    @staticmethod
    def _number(value: object) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def private_request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
    ) -> dict:
        if not self.demo_mode and not self.live_trading_enabled:
            raise UnsafeTradingConfiguration("private Bitget requests are disabled")
        method = method.upper()
        body = json.dumps(payload or {}, separators=(",", ":")) if method != "GET" else ""
        encoded_query = parse.urlencode(payload or {}) if method == "GET" else ""
        request_path = path + (f"?{encoded_query}" if encoded_query else "")
        headers = self._headers(method, request_path, body)
        req = request.Request(
            f"{self.BASE_URL}{request_path}",
            data=body.encode() if body else None,
            headers=headers,
            method=method,
        )
        with request.urlopen(req, timeout=self.timeout) as response:
            return json.loads(response.read())
