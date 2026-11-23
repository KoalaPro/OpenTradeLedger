"""Unified SQLite WAL store (compatible with legacy ledger DB + plugin snapshots & preferences)."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from opentradeledger.config import UserPreferences
from opentradeledger.core.legacy_store import GateManualStore


class LedgerStore:
    """Extends the battle-tested SQLite WAL store with plugin snapshots and user preferences."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Seamlessly reuse an existing gate_manual.db if present in data_dir; otherwise use opentradeledger.db
        legacy_db = self.data_dir / "gate_manual.db"
        self.db_path = legacy_db if legacy_db.exists() else (self.data_dir / "opentradeledger.db")
        self.legacy = GateManualStore(self.db_path)
        self._init_extra_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_extra_tables(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS otl_preferences (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload_json TEXT NOT NULL,
                    updated_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS otl_plugin_snapshots (
                    venue_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    synced_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS otl_ai_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at TEXT NOT NULL,
                    generated_ms INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    equity_snapshot_usd REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )

    def get_preferences(self) -> UserPreferences:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM otl_preferences WHERE id = 1").fetchone()
        if not row:
            return UserPreferences()
        try:
            return UserPreferences.model_validate_json(row["payload_json"])
        except Exception:
            return UserPreferences()

    def save_preferences(self, prefs: UserPreferences) -> UserPreferences:
        now_ms = int(time.time() * 1000)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO otl_preferences (id, payload_json, updated_ms)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json = excluded.payload_json, updated_ms = excluded.updated_ms
                """,
                (prefs.model_dump_json(), now_ms),
            )
        return prefs

    def upsert_plugin_snapshot(self, venue_id: str, payload: dict[str, Any]) -> None:
        now_ms = int(time.time() * 1000)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO otl_plugin_snapshots (venue_id, payload_json, synced_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(venue_id) DO UPDATE SET payload_json = excluded.payload_json, synced_ms = excluded.synced_ms
                """,
                (venue_id.strip().lower(), json.dumps(payload, ensure_ascii=False), now_ms),
            )

    def load_plugin_snapshots(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT venue_id, payload_json, synced_ms FROM otl_plugin_snapshots"
            ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                data = json.loads(row["payload_json"])
                data["_synced_ms"] = int(row["synced_ms"])
                out[row["venue_id"]] = data
            except Exception:
                continue
        return out

    def save_journal(self, trade_id: str, tags: list[str], note: str) -> None:
        self.legacy.save_journal(trade_id, tags=tags, note=note)

    def load_journals(self) -> dict[str, dict[str, Any]]:
        return self.legacy.load_journals()

    def save_ai_review(self, review: dict[str, Any], equity_usd: float) -> None:
        now_ms = int(time.time() * 1000)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO otl_ai_reviews (generated_at, generated_ms, model, equity_snapshot_usd, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(review.get("generated_at") or ""),
                    int(review.get("generated_ms") or now_ms),
                    str(review.get("model") or "local-actuarial"),
                    float(equity_usd),
                    json.dumps(review, ensure_ascii=False),
                ),
            )

    def load_latest_ai_review(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json, generated_ms, equity_snapshot_usd FROM otl_ai_reviews ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row["payload_json"])
            data.setdefault("generated_ms", int(row["generated_ms"]))
            data.setdefault("equity_snapshot_usd", float(row["equity_snapshot_usd"]))
            return data
        except Exception:
            return None

    def get_meta(self, key: str, default: Any = None) -> Any:
        return self.legacy.get_meta(key, default)

    def set_meta(self, key: str, value: Any) -> None:
        self.legacy.set_meta(key, value)
