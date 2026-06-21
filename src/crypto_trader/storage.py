from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterator


class EventStore:
    def __init__(self, database_path: str | Path = "data/trader.sqlite3") -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS state (
                    state_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_time ON events(occurred_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol)"
            )

    def append(self, event_type: str, payload: dict, symbol: str | None = None) -> int:
        occurred_at = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events (occurred_at, event_type, symbol, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (occurred_at, event_type, symbol, json.dumps(payload, ensure_ascii=False)),
            )
            return int(cursor.lastrowid)

    def recent(self, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, occurred_at, event_type, symbol, payload_json
                FROM events ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "occurred_at": row["occurred_at"],
                "event_type": row["event_type"],
                "symbol": row["symbol"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def recent_by_type(self, event_type: str, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, occurred_at, event_type, symbol, payload_json
                FROM events
                WHERE event_type = ?
                ORDER BY id DESC LIMIT ?
                """,
                (event_type, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "occurred_at": row["occurred_at"],
                "event_type": row["event_type"],
                "symbol": row["symbol"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def save_state(self, key: str, payload: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO state (state_key, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(payload, ensure_ascii=False), datetime.now(UTC).isoformat()),
            )

    def load_state(self, key: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM state WHERE state_key = ?",
                (key,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None
