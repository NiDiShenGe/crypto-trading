from __future__ import annotations

from datetime import UTC, datetime, timedelta
import gzip
import json
from pathlib import Path

from .domain import Candle
from .exchange.bitget import BitgetClient


class HistoricalDataCache:
    def __init__(self, root: str | Path = "data/history") -> None:
        self.root = Path(root)

    def load_or_fetch(
        self,
        client: BitgetClient,
        symbol: str,
        days: int,
        *,
        refresh: bool = False,
    ) -> tuple[list[Candle], list[tuple[datetime, float]]]:
        path = self.root / f"{symbol.upper()}-{days}d.json.gz"
        if path.exists() and not refresh:
            return self.load(path)
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        candles = client.history_candles(symbol, "5m", start, end)
        funding = client.funding_history(symbol, start, end)
        self.save(path, symbol, days, candles, funding)
        return candles, funding

    def save(
        self,
        path: Path,
        symbol: str,
        days: int,
        candles: list[Candle],
        funding: list[tuple[datetime, float]],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbol": symbol,
            "days": days,
            "saved_at": datetime.now(UTC).isoformat(),
            "candles": [
                [
                    candle.timestamp.isoformat(),
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                ]
                for candle in candles
            ],
            "funding": [
                [timestamp.isoformat(), rate]
                for timestamp, rate in funding
            ],
        }
        with gzip.open(path, "wt", encoding="utf-8") as file:
            json.dump(payload, file, separators=(",", ":"))

    def load(
        self, path: str | Path
    ) -> tuple[list[Candle], list[tuple[datetime, float]]]:
        with gzip.open(path, "rt", encoding="utf-8") as file:
            payload = json.load(file)
        candles = [
            Candle(
                datetime.fromisoformat(row[0]),
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            )
            for row in payload["candles"]
        ]
        funding = [
            (datetime.fromisoformat(row[0]), float(row[1]))
            for row in payload["funding"]
        ]
        return candles, funding
