"""Unified SQLite WAL store with strict per-venue partitioned archives (`otl_venue_kv` & `otl_venue_records`)."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping, Sequence

from opentradeledger.config import UserPreferences
from opentradeledger.core.legacy_store import GateManualStore
from opentradeledger.core.notifier import parse_bark_input
from opentradeledger.security.vault import CredentialVault


def _chmod_private(path: Path) -> None:
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except OSError:
        pass


_LEGACY_KV_TO_VENUE: tuple[tuple[str, str, str], ...] = (
    ("live_bundle", "gate", "live_bundle"),
    ("contract_specs", "gate", "contract_specs"),
    ("backfill_completed", "gate", "backfill_completed"),
    ("spot_window_backfill_v2", "gate", "spot_window_backfill_v2"),
    ("gate_decimal_trades_v2", "gate", "gate_decimal_trades_v2"),
    ("easicoin_live_bundle", "easicoin", "live_bundle"),
    ("easicoin_backfill_v1", "easicoin", "backfill_completed"),
    ("binance_live_bundle", "binance", "live_bundle"),
    ("binance_commission_rates", "binance", "commission_rates"),
    ("binance_cashflow_sync_sec", "binance", "cashflow_sync_sec"),
    ("binance_copy_cf_v1", "binance", "copy_cf_v1"),
    ("binance_bnb_prices", "binance", "bnb_prices"),
    ("binance_backfill_v2", "binance", "backfill_completed"),
    ("bitget_live_bundle", "bitget", "live_bundle"),
    ("bitget_fee_rates", "bitget", "fee_rates"),
    ("bitget_backfill_v3", "bitget", "backfill_completed"),
    ("mexc_live_bundle", "mexc", "live_bundle"),
    ("mexc_cashflow_sync_sec", "mexc", "cashflow_sync_sec"),
    ("mexc_contract_specs", "mexc", "contract_specs"),
    ("mexc_backfill_v1", "mexc", "backfill_completed"),
)

_VENUE_KV_FALLBACK: dict[tuple[str, str], str] = {
    (vid, vkey): lkey for lkey, vid, vkey in _LEGACY_KV_TO_VENUE
}

# (legacy_table, id_col, ts_col, venue_id, stream_name)
_LEGACY_TABLE_TO_VENUE_STREAM: tuple[tuple[str, str, str, str, str], ...] = (
    ("futures_closes", "close_key", "close_time", "gate", "futures_closes"),
    ("futures_trades", "trade_key", "create_time", "gate", "futures_trades"),
    ("futures_orders", "order_id", "create_time", "gate", "futures_orders"),
    ("partner_rebates", "rebate_uid", "commission_time", "gate", "partner_rebates"),
    ("spot_account_book", "entry_id", "entry_time", "gate", "spot_account_book"),
    ("stock_orders", "order_id", "create_time", "gate", "stock_orders"),
    ("stock_deals", "deal_key", "rowid", "gate", "stock_deals"),
    ("stock_transactions", "ref_key", "tx_time", "gate", "stock_transactions"),
    ("easicoin_closed_pnl", "close_key", "close_time", "easicoin", "closed_pnl"),
    ("easicoin_fills", "exec_id", "exec_time", "easicoin", "fills"),
    ("easicoin_orders", "order_id", "created_time", "easicoin", "orders"),
    ("easicoin_transfers", "tx_key", "tx_time", "easicoin", "transfers"),
    ("binance_income", "inc_key", "inc_time", "binance", "income"),
    ("binance_fills", "fill_key", "fill_time", "binance", "fills"),
    ("binance_orders", "ord_key", "ord_time", "binance", "orders"),
    ("binance_cashflows", "cf_key", "cf_time", "binance", "cashflows"),
    ("bitget_positions_history", "pos_key", "close_time", "bitget", "positions_history"),
    ("bitget_fills", "fill_key", "fill_time", "bitget", "fills"),
    ("bitget_orders", "ord_key", "ord_time", "bitget", "orders"),
    ("bitget_bills", "bill_key", "bill_time", "bitget", "bills"),
    ("mexc_positions_history", "pos_key", "close_time", "mexc", "positions_history"),
    ("mexc_deals", "deal_key", "deal_time", "mexc", "deals"),
    ("mexc_orders", "ord_key", "ord_time", "mexc", "orders"),
    ("mexc_funding", "fund_key", "settle_time", "mexc", "funding"),
    ("mexc_transfers", "tx_key", "tx_time", "mexc", "transfers"),
)


class VenueDataScope:
    """Isolated per-venue SQLite read/write partition (`venue_id`-scoped)."""

    def __init__(self, store: "LedgerStore", venue_id: str) -> None:
        self._store = store
        self.venue_id = venue_id.strip().lower()

    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._store._connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM otl_venue_kv WHERE venue_id = ? AND key = ?",
                (self.venue_id, key),
            ).fetchone()
        if row is not None:
            try:
                return json.loads(row["value_json"])
            except Exception:
                return default
        if key == "snapshot":
            snap = self._store.load_plugin_snapshots().get(self.venue_id)
            return snap if snap is not None else default
        legacy_key = _VENUE_KV_FALLBACK.get((self.venue_id, key))
        if legacy_key:
            return self._store.legacy.get_meta(legacy_key, default)
        return default

    def set_kv(self, key: str, value: Any) -> None:
        now = int(time.time())
        raw = json.dumps(value, ensure_ascii=False)
        with self._store._connect() as conn:
            conn.execute(
                """
                INSERT INTO otl_venue_kv (venue_id, key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(venue_id, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (self.venue_id, key, raw, now),
            )

    def upsert_records(
        self,
        stream: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        key_fn: Callable[[Mapping[str, Any], int], str] | Callable[[Mapping[str, Any]], str],
        ts_fn: Callable[[Mapping[str, Any]], float],
    ) -> int:
        if not rows:
            return 0
        batch: list[tuple[str, str, str, float, str]] = []
        for idx, r in enumerate(rows):
            try:
                rec_id = str(key_fn(r, idx)).strip()  # type: ignore[call-arg]
            except TypeError:
                rec_id = str(key_fn(r)).strip()  # type: ignore[call-arg]
            if not rec_id:
                continue
            sort_ts = float(ts_fn(r) or 0.0)
            batch.append((self.venue_id, stream, rec_id, sort_ts, json.dumps(dict(r), ensure_ascii=False)))
        if not batch:
            return 0
        with self._store._connect() as conn:
            conn.executemany(
                """
                INSERT INTO otl_venue_records (venue_id, stream, record_id, sort_ts, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(venue_id, stream, record_id) DO UPDATE SET
                    sort_ts = excluded.sort_ts,
                    payload_json = excluded.payload_json
                """,
                batch,
            )
        return len(batch)

    def count_records(self, stream: str) -> int:
        with self._store._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM otl_venue_records WHERE venue_id = ? AND stream = ?",
                (self.venue_id, stream),
            ).fetchone()
        cnt = int(row["cnt"]) if row else 0
        if cnt > 0:
            return cnt
        return len(self._load_legacy_stream_fallback(stream))

    def load_records(self, stream: str, *, desc: bool = False) -> list[dict[str, Any]]:
        order_sql = "DESC" if desc else "ASC"
        with self._store._connect() as conn:
            rows = conn.execute(
                f"SELECT payload_json FROM otl_venue_records WHERE venue_id = ? AND stream = ? ORDER BY sort_ts {order_sql}, record_id {order_sql}",
                (self.venue_id, stream),
            ).fetchall()
        if rows:
            return [json.loads(r["payload_json"]) for r in rows]
        return self._load_legacy_stream_fallback(stream)

    def _load_legacy_stream_fallback(self, stream: str) -> list[dict[str, Any]]:
        ls = self._store.legacy
        if self.venue_id == "gate":
            if stream == "futures_closes":
                return ls.load_futures_closes()
            if stream == "futures_trades":
                return ls.load_futures_trades()
            if stream == "futures_orders":
                return ls.load_futures_orders()
            if stream == "partner_rebates":
                return ls.load_partner_rebates()
            if stream == "spot_account_book":
                return ls.load_spot_account_book()
            if stream in ("stock_orders", "stock_deals", "stock_transactions"):
                so, sd, st = ls.load_stock_records()
                return so if stream == "stock_orders" else (sd if stream == "stock_deals" else st)
            if stream == "eod_snapshots":
                return [
                    s
                    for s in ls.load_equity_snapshots()
                    if abs(float(s.get("total_equity") or 0)) > 1e-9
                    or abs(float(s.get("quant_equity") or 0)) > 1e-9
                    or abs(float(s.get("stock_equity") or 0)) > 1e-9
                ]
        elif self.venue_id == "easicoin":
            if stream == "closed_pnl":
                return ls.load_easicoin_closed_pnl()
            if stream == "fills":
                return ls.load_easicoin_fills()
            if stream == "orders":
                return ls.load_easicoin_orders()
            if stream == "transfers":
                return ls.load_easicoin_transfers()
        elif self.venue_id == "binance":
            if stream == "income":
                return ls.load_binance_income()
            if stream == "fills":
                return ls.load_binance_fills()
            if stream == "orders":
                return ls.load_binance_orders()
            if stream == "cashflows":
                return ls.load_binance_cashflows()
        elif self.venue_id == "bitget":
            if stream == "positions_history":
                return ls.load_bitget_positions_history()
            if stream == "fills":
                return ls.load_bitget_fills()
            if stream == "orders":
                return ls.load_bitget_orders()
            if stream == "bills":
                return ls.load_bitget_bills()
        elif self.venue_id == "mexc":
            if stream == "positions_history":
                return ls.load_mexc_positions_history()
            if stream == "deals":
                return ls.load_mexc_deals()
            if stream == "orders":
                return ls.load_mexc_orders()
            if stream == "funding":
                return ls.load_mexc_funding()
            if stream == "transfers":
                return ls.load_mexc_transfers()
        return []


class LedgerStore:
    """SQLite WAL store with per-venue isolated partitions (`VenueDataScope`) and auto-migration."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        legacy_db = self.data_dir / "gate_manual.db"
        self.db_path = legacy_db if legacy_db.exists() else (self.data_dir / "opentradeledger.db")
        self._vault = CredentialVault(self.data_dir)
        self.legacy = GateManualStore(self.db_path)
        self._init_extra_tables()
        self._migrate_legacy_tables_to_partitions()
        self._harden_db_permissions()

    def venue_scope(self, venue_id: str) -> VenueDataScope:
        return VenueDataScope(self, venue_id)

    def _harden_db_permissions(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            _chmod_private(Path(f"{self.db_path}{suffix}"))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        self._harden_db_permissions()
        return conn

    def _init_extra_tables(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS otl_preferences (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload_json TEXT NOT NULL,
                    updated_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS otl_plugin_snapshots (
                    venue_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    synced_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS otl_ai_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at TEXT NOT NULL,
                    generated_ms INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    equity_snapshot_usd REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS otl_venue_kv (
                    venue_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (venue_id, key)
                );
                CREATE TABLE IF NOT EXISTS otl_venue_records (
                    venue_id TEXT NOT NULL,
                    stream TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    sort_ts REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (venue_id, stream, record_id)
                );
                CREATE INDEX IF NOT EXISTS idx_otl_venue_records_ts
                    ON otl_venue_records(venue_id, stream, sort_ts);
                """
            )

    def _migrate_legacy_tables_to_partitions(self) -> None:
        """Idempotently migrate any pre-existing SQLite records into isolated `venue_id` partitions."""
        with self._connect() as conn:
            existing_tables = {
                r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "kv_meta" in existing_tables:
                for lkey, vid, vkey in _LEGACY_KV_TO_VENUE:
                    row = conn.execute(
                        "SELECT value_json, updated_at FROM kv_meta WHERE key = ?",
                        (lkey,),
                    ).fetchone()
                    if row is not None:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO otl_venue_kv (venue_id, key, value_json, updated_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (vid, vkey, row["value_json"], int(row["updated_at"] or 0)),
                        )

            for tbl, id_col, ts_col, vid, stream in _LEGACY_TABLE_TO_VENUE_STREAM:
                if tbl not in existing_tables:
                    continue
                cnt_row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {tbl}").fetchone()
                if not cnt_row or int(cnt_row["cnt"]) == 0:
                    continue
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO otl_venue_records (venue_id, stream, record_id, sort_ts, payload_json)
                    SELECT ?, ?, CAST({id_col} AS TEXT), CAST({ts_col} AS REAL), payload_json
                    FROM {tbl}
                    """,
                    (vid, stream),
                )

            if "otl_plugin_snapshots" in existing_tables:
                snap_rows = conn.execute(
                    "SELECT venue_id, payload_json, synced_ms FROM otl_plugin_snapshots"
                ).fetchall()
                for srow in snap_rows:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO otl_venue_kv (venue_id, key, value_json, updated_at)
                        VALUES (?, 'snapshot', ?, ?)
                        """,
                        (str(srow["venue_id"]).lower(), srow["payload_json"], int(srow["synced_ms"]) // 1000),
                    )

    def get_preferences(self) -> UserPreferences:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM otl_preferences WHERE id = 1").fetchone()
        if not row:
            return UserPreferences()
        try:
            raw = json.loads(row["payload_json"])
            if isinstance(raw, dict):
                if raw.get("ai_api_key"):
                    raw["ai_api_key"] = self._vault.decrypt_secret_str(str(raw["ai_api_key"]))
                notifs = raw.get("notifications")
                if isinstance(notifs, dict):
                    notifs = dict(notifs)
                    for k in ("webhook_url", "telegram_bot_token", "bark_push_key"):
                        if notifs.get(k):
                            notifs[k] = self._vault.decrypt_secret_str(str(notifs[k]))
                    raw["notifications"] = notifs
            return UserPreferences.model_validate(raw)
        except Exception:
            return UserPreferences()

    def save_preferences(self, prefs: UserPreferences) -> UserPreferences:
        now_ms = int(time.time() * 1000)
        norm_srv, norm_key = parse_bark_input(
            prefs.notifications.bark_server_url,
            prefs.notifications.bark_push_key,
        )
        prefs.notifications.bark_server_url = norm_srv
        prefs.notifications.bark_push_key = norm_key

        disk_dict = prefs.model_dump()
        if disk_dict.get("ai_api_key"):
            disk_dict["ai_api_key"] = self._vault.encrypt_secret_str(str(disk_dict["ai_api_key"]))
        notifs = disk_dict.get("notifications")
        if isinstance(notifs, dict):
            notifs = dict(notifs)
            for k in ("webhook_url", "telegram_bot_token", "bark_push_key"):
                if notifs.get(k):
                    notifs[k] = self._vault.encrypt_secret_str(str(notifs[k]))
            disk_dict["notifications"] = notifs
        payload_str = json.dumps(disk_dict, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO otl_preferences (id, payload_json, updated_ms)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json = excluded.payload_json, updated_ms = excluded.updated_ms
                """,
                (payload_str, now_ms),
            )
        self._harden_db_permissions()
        return prefs

    @staticmethod
    def _merge_record_list(
        old_list: list[dict[str, Any]],
        new_list: list[dict[str, Any]],
        key_fn: Any,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in old_list:
            if isinstance(item, dict):
                merged[str(key_fn(item))] = item
        for item in new_list:
            if isinstance(item, dict):
                merged[str(key_fn(item))] = item
        return list(merged.values())

    def upsert_plugin_snapshot(
        self,
        venue_id: str,
        payload: dict[str, Any],
        *,
        merge_history: bool = True,
        merge_specs: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        vid = venue_id.strip().lower()
        now_ms = int(time.time() * 1000)
        final_payload = dict(payload)

        if merge_history:
            existing = self.venue_scope(vid).get_kv("snapshot")
            if isinstance(existing, dict):
                specs = dict(merge_specs) if merge_specs is not None else self._default_snapshot_merge_specs(vid)
                for field, kfn in specs.items():
                    old_rows = existing.get(field) if isinstance(existing.get(field), list) else []
                    new_rows = final_payload.get(field) if isinstance(final_payload.get(field), list) else []
                    if old_rows or new_rows:
                        final_payload[field] = self._merge_record_list(old_rows, new_rows, kfn)

        final_payload["_last_sync_ms"] = now_ms
        final_payload["_synced_ms"] = now_ms
        raw_json = json.dumps(final_payload, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO otl_plugin_snapshots (venue_id, payload_json, synced_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(venue_id) DO UPDATE SET payload_json = excluded.payload_json, synced_ms = excluded.synced_ms
                """,
                (vid, raw_json, now_ms),
            )
            conn.execute(
                """
                INSERT INTO otl_venue_kv (venue_id, key, value_json, updated_at)
                VALUES (?, 'snapshot', ?, ?)
                ON CONFLICT(venue_id, key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (vid, raw_json, now_ms // 1000),
            )
        return final_payload

    @staticmethod
    def _default_snapshot_merge_specs(vid: str) -> dict[str, Any]:
        if vid == "okx":
            return {
                "positions_history": lambda r: f"{r.get('posId')}_{r.get('uTime')}_{r.get('instId')}",
                "fills": lambda r: str(r.get("billId") or r.get("tradeId") or f"{r.get('ordId')}_{r.get('ts')}"),
                "bills": lambda r: str(r.get("billId") or f"{r.get('ts')}_{r.get('subType')}_{r.get('balChg')}"),
                "asset_bills": lambda r: str(r.get("billId") or f"{r.get('ts')}_{r.get('type')}_{r.get('balChg')}"),
                "deposits": lambda r: str(r.get("depId") or r.get("txId") or f"{r.get('ts')}_{r.get('amt')}"),
                "withdrawals": lambda r: str(r.get("wdId") or r.get("txId") or f"{r.get('ts')}_{r.get('amt')}"),
                "copy_history": lambda r: str(r.get("subPosId") or f"{r.get('instId')}_{r.get('uTime')}"),
            }
        if vid == "bybit":
            return {
                "closed_pnl": lambda r: f"{r.get('orderId')}_{r.get('updatedTime')}_{r.get('symbol')}",
                "executions": lambda r: str(r.get("execId") or f"{r.get('orderId')}_{r.get('execTime')}"),
                "tx_logs": lambda r: str(r.get("id") or f"{r.get('transactionTime')}_{r.get('type')}_{r.get('symbol')}_{r.get('cashFlow')}"),
                "deposits": lambda r: str(r.get("id") or r.get("txID") or f"{r.get('successAt')}_{r.get('amount')}"),
                "internal_deposits": lambda r: str(r.get("id") or f"{r.get('createdTime')}_{r.get('amount')}"),
                "withdrawals": lambda r: str(r.get("withdrawId") or r.get("txID") or f"{r.get('createTime')}_{r.get('amount')}"),
            }
        return {}

    def snapshot_venue_partition(self, venue_id: str) -> dict[str, list[tuple[Any, ...]]]:
        """Capture an in-memory rollback point of a single venue's SQLite partition."""
        vid = (venue_id or "").strip().lower()
        with self._connect() as conn:
            kv_rows = [
                (r["venue_id"], r["key"], r["value_json"], r["updated_at"])
                for r in conn.execute(
                    "SELECT venue_id, key, value_json, updated_at FROM otl_venue_kv WHERE venue_id = ?",
                    (vid,),
                ).fetchall()
            ]
            rec_rows = [
                (r["venue_id"], r["stream"], r["record_id"], r["sort_ts"], r["payload_json"])
                for r in conn.execute(
                    "SELECT venue_id, stream, record_id, sort_ts, payload_json FROM otl_venue_records WHERE venue_id = ?",
                    (vid,),
                ).fetchall()
            ]
            snap_rows = [
                (r["venue_id"], r["payload_json"], r["synced_ms"])
                for r in conn.execute(
                    "SELECT venue_id, payload_json, synced_ms FROM otl_plugin_snapshots WHERE venue_id = ?",
                    (vid,),
                ).fetchall()
            ]
        return {"kv": kv_rows, "records": rec_rows, "snapshots": snap_rows}

    def restore_venue_partition(self, venue_id: str, snap: dict[str, list[tuple[Any, ...]]]) -> None:
        """Atomically restore a single venue's SQLite partition to a prior snapshot."""
        vid = (venue_id or "").strip().lower()
        with self._connect() as conn:
            conn.execute("DELETE FROM otl_venue_kv WHERE venue_id = ?", (vid,))
            conn.execute("DELETE FROM otl_venue_records WHERE venue_id = ?", (vid,))
            conn.execute("DELETE FROM otl_plugin_snapshots WHERE venue_id = ?", (vid,))
            if snap.get("kv"):
                conn.executemany(
                    "INSERT INTO otl_venue_kv (venue_id, key, value_json, updated_at) VALUES (?, ?, ?, ?)",
                    snap["kv"],
                )
            if snap.get("records"):
                conn.executemany(
                    "INSERT INTO otl_venue_records (venue_id, stream, record_id, sort_ts, payload_json) VALUES (?, ?, ?, ?, ?)",
                    snap["records"],
                )
            if snap.get("snapshots"):
                conn.executemany(
                    "INSERT INTO otl_plugin_snapshots (venue_id, payload_json, synced_ms) VALUES (?, ?, ?)",
                    snap["snapshots"],
                )

    def purge_venue_data(self, venue_id: str) -> None:
        vid = (venue_id or "").strip().lower()
        with self._connect() as conn:
            conn.execute("DELETE FROM otl_venue_kv WHERE venue_id = ?", (vid,))
            conn.execute("DELETE FROM otl_venue_records WHERE venue_id = ?", (vid,))
            conn.execute("DELETE FROM otl_plugin_snapshots WHERE venue_id = ?", (vid,))
        self.legacy.purge_venue(vid)
        sched = self.get_meta("sync_schedule", {})
        if isinstance(sched, dict) and vid in sched:
            sched = {k: v for k, v in sched.items() if k != vid}
            self.set_meta("sync_schedule", sched)

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

    def load_latest_ai_review(self, range_key: str | None = None) -> dict[str, Any] | None:
        with self._connect() as conn:
            if range_key is None:
                rows = conn.execute(
                    "SELECT payload_json, generated_ms, equity_snapshot_usd FROM otl_ai_reviews ORDER BY id DESC LIMIT 1"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload_json, generated_ms, equity_snapshot_usd FROM otl_ai_reviews ORDER BY id DESC LIMIT 100"
                ).fetchall()
        if not rows:
            return None
        target_rk = (range_key or "all").strip().lower() if range_key is not None else None
        for row in rows:
            try:
                data = json.loads(row["payload_json"])
                if not isinstance(data, dict):
                    continue
                row_rk = str(data.get("range_key") or "all").strip().lower()
                if target_rk is not None and row_rk != target_rk:
                    continue
                data.setdefault("range_key", row_rk)
                data.setdefault("generated_ms", int(row["generated_ms"]))
                data.setdefault("equity_snapshot_usd", float(row["equity_snapshot_usd"]))
                return data
            except Exception:
                continue
        return None

    def get_meta(self, key: str, default: Any = None) -> Any:
        return self.legacy.get_meta(key, default)

    def set_meta(self, key: str, value: Any) -> None:
        self.legacy.set_meta(key, value)
