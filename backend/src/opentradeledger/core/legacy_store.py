"""SQLite WAL persistent ledger store for Gate Manual Trading Dashboard."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Mapping, Optional, Sequence


class GateManualStore:
    """Thread-safe SQLite WAL store for Gate historical records, snapshots, and user journals."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv_meta (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS futures_closes (
                    close_key TEXT PRIMARY KEY,
                    close_time REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_futures_closes_time ON futures_closes(close_time DESC);

                CREATE TABLE IF NOT EXISTS futures_trades (
                    trade_key TEXT PRIMARY KEY,
                    create_time REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_futures_trades_time ON futures_trades(create_time DESC);

                CREATE TABLE IF NOT EXISTS futures_orders (
                    order_id TEXT PRIMARY KEY,
                    create_time REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS partner_rebates (
                    rebate_uid TEXT PRIMARY KEY,
                    commission_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_partner_rebates_time ON partner_rebates(commission_time DESC);

                CREATE TABLE IF NOT EXISTS spot_account_book (
                    entry_id TEXT PRIMARY KEY,
                    entry_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS stock_orders (
                    order_id TEXT PRIMARY KEY,
                    create_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS stock_deals (
                    deal_key TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS stock_transactions (
                    ref_key TEXT PRIMARY KEY,
                    tx_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trade_journals (
                    record_key TEXT PRIMARY KEY,
                    tags_json TEXT NOT NULL,
                    note TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    bucket_sec INTEGER PRIMARY KEY,
                    ts_sec INTEGER NOT NULL,
                    total_equity REAL NOT NULL,
                    unified_equity REAL NOT NULL,
                    quant_equity REAL NOT NULL,
                    stock_equity REAL NOT NULL,
                    easicoin_equity REAL NOT NULL DEFAULT 0.0,
                    binance_equity REAL NOT NULL DEFAULT 0.0
                );

                CREATE TABLE IF NOT EXISTS easicoin_closed_pnl (
                    close_key TEXT PRIMARY KEY,
                    close_time REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_easicoin_closed_time ON easicoin_closed_pnl(close_time DESC);

                CREATE TABLE IF NOT EXISTS easicoin_fills (
                    exec_id TEXT PRIMARY KEY,
                    exec_time REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_easicoin_fills_time ON easicoin_fills(exec_time DESC);

                CREATE TABLE IF NOT EXISTS easicoin_orders (
                    order_id TEXT PRIMARY KEY,
                    created_time REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS easicoin_transfers (
                    tx_key TEXT PRIMARY KEY,
                    tx_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS binance_income (
                    inc_key TEXT PRIMARY KEY,
                    inc_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_binance_income_time ON binance_income(inc_time DESC);

                CREATE TABLE IF NOT EXISTS binance_fills (
                    fill_key TEXT PRIMARY KEY,
                    fill_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_binance_fills_time ON binance_fills(fill_time DESC);

                CREATE TABLE IF NOT EXISTS binance_orders (
                    ord_key TEXT PRIMARY KEY,
                    ord_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS binance_cashflows (
                    cf_key TEXT PRIMARY KEY,
                    cf_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bitget_positions_history (
                    pos_key TEXT PRIMARY KEY,
                    close_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bitget_pos_hist_time ON bitget_positions_history(close_time DESC);

                CREATE TABLE IF NOT EXISTS bitget_fills (
                    fill_key TEXT PRIMARY KEY,
                    fill_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bitget_orders (
                    ord_key TEXT PRIMARY KEY,
                    ord_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bitget_bills (
                    bill_key TEXT PRIMARY KEY,
                    bill_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mexc_positions_history (
                    pos_key TEXT PRIMARY KEY,
                    close_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mexc_pos_hist_time ON mexc_positions_history(close_time DESC);

                CREATE TABLE IF NOT EXISTS mexc_deals (
                    deal_key TEXT PRIMARY KEY,
                    deal_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mexc_deals_time ON mexc_deals(deal_time ASC);

                CREATE TABLE IF NOT EXISTS mexc_orders (
                    ord_key TEXT PRIMARY KEY,
                    ord_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mexc_funding (
                    fund_key TEXT PRIMARY KEY,
                    settle_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mexc_transfers (
                    tx_key TEXT PRIMARY KEY,
                    tx_time INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ai_diagnostics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(equity_snapshots)").fetchall()}
            if "easicoin_equity" not in cols:
                conn.execute("ALTER TABLE equity_snapshots ADD COLUMN easicoin_equity REAL NOT NULL DEFAULT 0.0")
            if "binance_equity" not in cols:
                conn.execute("ALTER TABLE equity_snapshots ADD COLUMN binance_equity REAL NOT NULL DEFAULT 0.0")
            if "bitget_equity" not in cols:
                conn.execute("ALTER TABLE equity_snapshots ADD COLUMN bitget_equity REAL NOT NULL DEFAULT 0.0")
            if "mexc_equity" not in cols:
                conn.execute("ALTER TABLE equity_snapshots ADD COLUMN mexc_equity REAL NOT NULL DEFAULT 0.0")

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value_json FROM kv_meta WHERE key = ?", (key,)).fetchone()
            if not row:
                return default
            try:
                return json.loads(row["value_json"])
            except ValueError:
                return default

    def set_meta(self, key: str, value: Any) -> None:
        now = int(time.time())
        raw = json.dumps(value, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kv_meta(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (key, raw, now),
            )

    def purge_venue(self, venue_id: str) -> None:
        vid = (venue_id or "").strip().lower()
        venue_tables: dict[str, tuple[str, ...]] = {
            "gate": (
                "futures_closes",
                "futures_trades",
                "futures_orders",
                "partner_rebates",
                "spot_account_book",
                "stock_orders",
                "stock_deals",
                "stock_transactions",
            ),
            "easicoin": (
                "easicoin_closed_pnl",
                "easicoin_fills",
                "easicoin_orders",
                "easicoin_transfers",
            ),
            "binance": (
                "binance_income",
                "binance_fills",
                "binance_orders",
                "binance_cashflows",
            ),
            "bitget": (
                "bitget_positions_history",
                "bitget_fills",
                "bitget_orders",
                "bitget_bills",
            ),
            "mexc": (
                "mexc_positions_history",
                "mexc_deals",
                "mexc_orders",
                "mexc_funding",
                "mexc_transfers",
            ),
        }
        venue_meta_keys: dict[str, tuple[str, ...]] = {
            "gate": (
                "live_bundle",
                "contract_specs",
                "backfill_completed",
                "spot_window_backfill_v2",
                "gate_decimal_trades_v2",
            ),
            "easicoin": ("easicoin_live_bundle", "easicoin_backfill_v1"),
            "binance": (
                "binance_live_bundle",
                "binance_commission_rates",
                "binance_cashflow_sync_sec",
                "binance_copy_cf_v1",
                "binance_bnb_prices",
                "binance_backfill_v2",
            ),
            "bitget": ("bitget_live_bundle", "bitget_fee_rates", "bitget_backfill_v3"),
            "mexc": ("mexc_live_bundle", "mexc_cashflow_sync_sec", "mexc_contract_specs", "mexc_backfill_v1"),
        }
        venue_snapshot_zero_sql: dict[str, str] = {
            "gate": "UPDATE equity_snapshots SET total_equity = 0.0, unified_equity = 0.0, quant_equity = 0.0, stock_equity = 0.0",
            "easicoin": "UPDATE equity_snapshots SET easicoin_equity = 0.0",
            "binance": "UPDATE equity_snapshots SET binance_equity = 0.0",
            "bitget": "UPDATE equity_snapshots SET bitget_equity = 0.0",
            "mexc": "UPDATE equity_snapshots SET mexc_equity = 0.0",
        }
        with self._lock, self._connect() as conn:
            for tbl in venue_tables.get(vid, ()):
                conn.execute(f"DELETE FROM {tbl}")
            for mk in venue_meta_keys.get(vid, ()):
                conn.execute("DELETE FROM kv_meta WHERE key = ?", (mk,))
            if vid in venue_snapshot_zero_sql:
                conn.execute(venue_snapshot_zero_sql[vid])
                conn.execute(
                    """
                    DELETE FROM equity_snapshots
                    WHERE ABS(total_equity) < 1e-9
                      AND ABS(unified_equity) < 1e-9
                      AND ABS(quant_equity) < 1e-9
                      AND ABS(stock_equity) < 1e-9
                      AND ABS(easicoin_equity) < 1e-9
                      AND ABS(binance_equity) < 1e-9
                      AND ABS(bitget_equity) < 1e-9
                      AND ABS(mexc_equity) < 1e-9
                    """
                )

    def upsert_futures_closes(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for r in rows:
            key = f"{r.get('contract')}_{int(float(r.get('first_open_time') or 0))}_{int(float(r.get('time') or 0))}_{r.get('side')}"
            batch.append((key, float(r.get("time") or 0), json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO futures_closes(close_key, close_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(close_key) DO UPDATE SET payload_json=excluded.payload_json, close_time=excluded.close_time",
                batch,
            )
        return len(batch)

    def load_futures_closes(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM futures_closes ORDER BY close_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_futures_trades(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            key = str(r.get("id") or f"{r.get('order_id')}_{r.get('create_time')}_{idx}")
            batch.append((key, float(r.get("create_time") or 0), json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO futures_trades(trade_key, create_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(trade_key) DO UPDATE SET payload_json=excluded.payload_json",
                batch,
            )
        return len(batch)

    def load_futures_trades(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM futures_trades ORDER BY create_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_futures_orders(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for r in rows:
            oid = str(r.get("id") or "").strip()
            if oid:
                batch.append((oid, float(r.get("create_time") or 0), json.dumps(dict(r), ensure_ascii=False)))
        if not batch:
            return 0
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO futures_orders(order_id, create_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(order_id) DO UPDATE SET payload_json=excluded.payload_json",
                batch,
            )
        return len(batch)

    def load_futures_orders(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM futures_orders ORDER BY create_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_partner_rebates(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        per_ts_occ: dict[str, int] = {}
        batch = []
        for r in sorted(rows, key=lambda x: (int(x.get("commission_time") or 0), str(x.get("commission_amount") or ""))):
            uid = str(r.get("_rebate_uid") or "").strip()
            if not uid:
                base_k = f"{r.get('commission_time')}_{r.get('commission_amount')}_{r.get('commission_asset')}_{r.get('source')}"
                occ = per_ts_occ.get(base_k, 0) + 1
                per_ts_occ[base_k] = occ
                uid = f"{base_k}#{occ}"
            payload = {**dict(r), "_rebate_uid": uid}
            batch.append((uid, int(r.get("commission_time") or 0), json.dumps(payload, ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO partner_rebates(rebate_uid, commission_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(rebate_uid) DO UPDATE SET payload_json=excluded.payload_json",
                batch,
            )
        return len(batch)

    def load_partner_rebates(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM partner_rebates ORDER BY commission_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_spot_account_book(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            eid = str(r.get("id") or f"{r.get('time')}_{r.get('type')}_{idx}")
            batch.append((eid, int(r.get("time") or 0), json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO spot_account_book(entry_id, entry_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(entry_id) DO UPDATE SET payload_json=excluded.payload_json",
                batch,
            )
        return len(batch)

    def load_spot_account_book(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM spot_account_book ORDER BY entry_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_stock_records(
        self,
        *,
        orders: Sequence[Mapping[str, Any]],
        deals: Sequence[Mapping[str, Any]],
        transactions: Sequence[Mapping[str, Any]],
    ) -> None:
        with self._lock, self._connect() as conn:
            if orders:
                o_batch = [
                    (str(o.get("id")), int(float(o.get("create_time") or 0)), json.dumps(dict(o), ensure_ascii=False))
                    for o in orders
                    if o.get("id")
                ]
                conn.executemany(
                    "INSERT INTO stock_orders(order_id, create_time, payload_json) VALUES (?, ?, ?) "
                    "ON CONFLICT(order_id) DO UPDATE SET payload_json=excluded.payload_json",
                    o_batch,
                )
            if deals:
                d_batch = [
                    (
                        str(d.get("id") or f"{d.get('order_id')}_{i}"),
                        str(d.get("order_id") or ""),
                        json.dumps(dict(d), ensure_ascii=False),
                    )
                    for i, d in enumerate(deals)
                ]
                conn.executemany(
                    "INSERT INTO stock_deals(deal_key, order_id, payload_json) VALUES (?, ?, ?) "
                    "ON CONFLICT(deal_key) DO UPDATE SET payload_json=excluded.payload_json",
                    d_batch,
                )
            if transactions:
                t_batch = [
                    (
                        str(t.get("ref_id") or f"{t.get('time')}_{t.get('type')}_{i}"),
                        int(float(t.get("time") or 0)),
                        json.dumps(dict(t), ensure_ascii=False),
                    )
                    for i, t in enumerate(transactions)
                ]
                conn.executemany(
                    "INSERT INTO stock_transactions(ref_key, tx_time, payload_json) VALUES (?, ?, ?) "
                    "ON CONFLICT(ref_key) DO UPDATE SET payload_json=excluded.payload_json",
                    t_batch,
                )

    def load_stock_records(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        with self._lock, self._connect() as conn:
            orders = [json.loads(r["payload_json"]) for r in conn.execute("SELECT payload_json FROM stock_orders ORDER BY create_time DESC").fetchall()]
            deals = [json.loads(r["payload_json"]) for r in conn.execute("SELECT payload_json FROM stock_deals").fetchall()]
            txs = [json.loads(r["payload_json"]) for r in conn.execute("SELECT payload_json FROM stock_transactions ORDER BY tx_time DESC").fetchall()]
            return orders, deals, txs

    def save_journal(self, record_key: str, *, tags: Sequence[str], note: str) -> dict[str, Any]:
        clean_tags = [str(t).strip() for t in tags if str(t).strip()][:12]
        clean_note = str(note or "").strip()[:1000]
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trade_journals(record_key, tags_json, note, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(record_key) DO UPDATE SET
                    tags_json=excluded.tags_json,
                    note=excluded.note,
                    updated_at=excluded.updated_at
                """,
                (record_key, json.dumps(clean_tags, ensure_ascii=False), clean_note, now),
            )
        return {"record_key": record_key, "tags": clean_tags, "note": clean_note, "updated_at": now}

    def load_journals(self) -> dict[str, dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT record_key, tags_json, note, updated_at FROM trade_journals").fetchall()
            out: dict[str, dict[str, Any]] = {}
            for r in rows:
                out[r["record_key"]] = {
                    "tags": json.loads(r["tags_json"]),
                    "note": r["note"],
                    "updated_at": r["updated_at"],
                }
            return out

    def upsert_easicoin_closed_pnl(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            oid = str(r.get("order_id") or "").strip()
            ts = float(r.get("created_at") or 0)
            key = oid or f"{r.get('symbol')}_{int(ts)}_{r.get('closed_pnl')}_{idx}"
            batch.append((key, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO easicoin_closed_pnl(close_key, close_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(close_key) DO UPDATE SET payload_json=excluded.payload_json, close_time=excluded.close_time",
                batch,
            )
        return len(batch)

    def load_easicoin_closed_pnl(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM easicoin_closed_pnl ORDER BY close_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_easicoin_fills(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            eid = str(r.get("exec_id") or f"{r.get('order_id')}_{r.get('exec_time')}_{idx}").strip()
            ts = float(r.get("exec_time") or 0)
            batch.append((eid, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO easicoin_fills(exec_id, exec_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(exec_id) DO UPDATE SET payload_json=excluded.payload_json, exec_time=excluded.exec_time",
                batch,
            )
        return len(batch)

    def load_easicoin_fills(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM easicoin_fills ORDER BY exec_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_easicoin_orders(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for r in rows:
            oid = str(r.get("order_id") or "").strip()
            if oid:
                batch.append((oid, float(r.get("created_time") or 0), json.dumps(dict(r), ensure_ascii=False)))
        if not batch:
            return 0
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO easicoin_orders(order_id, created_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(order_id) DO UPDATE SET payload_json=excluded.payload_json",
                batch,
            )
        return len(batch)

    def load_easicoin_orders(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM easicoin_orders ORDER BY created_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_easicoin_transfers(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for r in rows:
            ts = int(float(r.get("time") or 0))
            key = f"{ts}_{r.get('coin')}_{r.get('amount')}_{r.get('from_wallet')}_{r.get('to_wallet')}"
            batch.append((key, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO easicoin_transfers(tx_key, tx_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(tx_key) DO UPDATE SET payload_json=excluded.payload_json",
                batch,
            )
        return len(batch)

    def load_easicoin_transfers(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM easicoin_transfers ORDER BY tx_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_binance_income(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(float(r.get("time") or 0))
            key = (
                f"{r.get('tranId') or idx}_{r.get('incomeType')}_{ts}_"
                f"{r.get('asset')}_{r.get('income')}_{r.get('tradeId') or ''}"
            )
            batch.append((key, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO binance_income(inc_key, inc_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(inc_key) DO UPDATE SET payload_json=excluded.payload_json, inc_time=excluded.inc_time",
                batch,
            )
        return len(batch)

    def load_binance_income(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM binance_income ORDER BY inc_time ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_binance_fills(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(float(r.get("time") or 0))
            sym = str(r.get("symbol") or "").upper()
            tid = str(r.get("id") or f"{r.get('orderId')}_{ts}_{idx}")
            key = f"{sym}_{tid}"
            batch.append((key, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO binance_fills(fill_key, fill_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(fill_key) DO UPDATE SET payload_json=excluded.payload_json, fill_time=excluded.fill_time",
                batch,
            )
        return len(batch)

    def load_binance_fills(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM binance_fills ORDER BY fill_time ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_binance_orders(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for r in rows:
            oid = str(r.get("orderId") or "").strip()
            sym = str(r.get("symbol") or "").upper()
            if oid and sym:
                ts = int(float(r.get("updateTime") or r.get("time") or 0))
                batch.append((f"{sym}_{oid}", ts, json.dumps(dict(r), ensure_ascii=False)))
        if not batch:
            return 0
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO binance_orders(ord_key, ord_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(ord_key) DO UPDATE SET payload_json=excluded.payload_json",
                batch,
            )
        return len(batch)

    def load_binance_orders(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM binance_orders ORDER BY ord_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_binance_cashflows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            key = str(r.get("cf_key") or f"{r.get('cf_type')}_{r.get('time_ms')}_{idx}")
            ts = int(float(r.get("time_ms") or 0))
            batch.append((key, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO binance_cashflows(cf_key, cf_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(cf_key) DO UPDATE SET payload_json=excluded.payload_json, cf_time=excluded.cf_time",
                batch,
            )
        return len(batch)

    def load_binance_cashflows(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM binance_cashflows ORDER BY cf_time ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_bitget_positions_history(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(
                float(
                    r.get("updatedTime")
                    or r.get("uTime")
                    or r.get("utime")
                    or r.get("createdTime")
                    or r.get("cTime")
                    or r.get("ctime")
                    or 0
                )
            )
            pid = str(r.get("positionId") or f"{r.get('symbol')}_{ts}_{idx}")
            batch.append((pid, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO bitget_positions_history(pos_key, close_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(pos_key) DO UPDATE SET payload_json=excluded.payload_json, close_time=excluded.close_time",
                batch,
            )
        return len(batch)

    def load_bitget_positions_history(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM bitget_positions_history ORDER BY close_time DESC"
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_bitget_fills(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(
                float(
                    r.get("createdTime")
                    or r.get("updatedTime")
                    or r.get("cTime")
                    or r.get("ctime")
                    or r.get("execTime")
                    or 0
                )
            )
            fid = str(r.get("execId") or r.get("tradeId") or f"{r.get('orderId')}_{ts}_{idx}")
            batch.append((fid, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO bitget_fills(fill_key, fill_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(fill_key) DO UPDATE SET payload_json=excluded.payload_json, fill_time=excluded.fill_time",
                batch,
            )
        return len(batch)

    def load_bitget_fills(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM bitget_fills ORDER BY fill_time ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_bitget_orders(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(float(r.get("updatedTime") or r.get("createdTime") or r.get("cTime") or 0))
            oid = str(r.get("orderId") or f"{r.get('symbol')}_{ts}_{idx}")
            batch.append((oid, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO bitget_orders(ord_key, ord_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(ord_key) DO UPDATE SET payload_json=excluded.payload_json, ord_time=excluded.ord_time",
                batch,
            )
        return len(batch)

    def load_bitget_orders(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM bitget_orders ORDER BY ord_time ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_bitget_bills(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(
                float(
                    r.get("ts")
                    or r.get("createdTime")
                    or r.get("cTime")
                    or r.get("ctime")
                    or r.get("updatedTime")
                    or 0
                )
            )
            src = str(r.get("_source") or r.get("_category") or "bg")
            raw_id = str(r.get("orderId") or r.get("billId") or r.get("id") or f"{ts}_{idx}")
            bid = f"{src}_{raw_id}"
            batch.append((bid, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO bitget_bills(bill_key, bill_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(bill_key) DO UPDATE SET payload_json=excluded.payload_json, bill_time=excluded.bill_time",
                batch,
            )
        return len(batch)

    def load_bitget_bills(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM bitget_bills ORDER BY bill_time ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_mexc_positions_history(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(float(r.get("updateTime") or r.get("createTime") or 0))
            pid = str(r.get("positionId") or f"{r.get('symbol')}_{ts}_{idx}")
            batch.append((pid, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO mexc_positions_history(pos_key, close_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(pos_key) DO UPDATE SET payload_json=excluded.payload_json, close_time=excluded.close_time",
                batch,
            )
        return len(batch)

    def load_mexc_positions_history(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM mexc_positions_history ORDER BY close_time DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_mexc_deals(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(float(r.get("timestamp") or 0))
            did = str(r.get("id") or f"{r.get('orderId')}_{ts}_{idx}")
            batch.append((did, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO mexc_deals(deal_key, deal_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(deal_key) DO UPDATE SET payload_json=excluded.payload_json, deal_time=excluded.deal_time",
                batch,
            )
        return len(batch)

    def load_mexc_deals(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM mexc_deals ORDER BY deal_time ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_mexc_orders(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(float(r.get("updateTime") or r.get("createTime") or 0))
            oid = str(r.get("orderId") or f"{r.get('symbol')}_{ts}_{idx}")
            batch.append((oid, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO mexc_orders(ord_key, ord_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(ord_key) DO UPDATE SET payload_json=excluded.payload_json, ord_time=excluded.ord_time",
                batch,
            )
        return len(batch)

    def load_mexc_orders(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM mexc_orders ORDER BY ord_time ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_mexc_funding(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(float(r.get("settleTime") or 0))
            fid = str(r.get("id") or f"{r.get('symbol')}_{ts}_{idx}")
            batch.append((fid, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO mexc_funding(fund_key, settle_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(fund_key) DO UPDATE SET payload_json=excluded.payload_json, settle_time=excluded.settle_time",
                batch,
            )
        return len(batch)

    def load_mexc_funding(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM mexc_funding ORDER BY settle_time ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def upsert_mexc_transfers(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        batch = []
        for idx, r in enumerate(rows):
            ts = int(float(r.get("_time_ms") or r.get("createTime") or 0))
            tk = str(r.get("_key") or r.get("id") or r.get("txid") or f"{ts}_{idx}")
            batch.append((tk, ts, json.dumps(dict(r), ensure_ascii=False)))
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO mexc_transfers(tx_key, tx_time, payload_json) VALUES (?, ?, ?) "
                "ON CONFLICT(tx_key) DO UPDATE SET payload_json=excluded.payload_json, tx_time=excluded.tx_time",
                batch,
            )
        return len(batch)

    def load_mexc_transfers(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM mexc_transfers ORDER BY tx_time ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def record_equity_snapshot(
        self,
        *,
        ts_sec: int,
        total_equity: float,
        unified_equity: float,
        quant_equity: float,
        stock_equity: float,
        easicoin_equity: float = 0.0,
        binance_equity: float = 0.0,
        bitget_equity: float = 0.0,
        mexc_equity: float = 0.0,
        bucket_interval_sec: int = 900,
    ) -> None:
        bucket = (int(ts_sec) // bucket_interval_sec) * bucket_interval_sec
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO equity_snapshots(
                    bucket_sec, ts_sec, total_equity, unified_equity, quant_equity, stock_equity, easicoin_equity, binance_equity, bitget_equity, mexc_equity
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_sec) DO UPDATE SET
                    ts_sec=excluded.ts_sec,
                    total_equity=excluded.total_equity,
                    unified_equity=excluded.unified_equity,
                    quant_equity=excluded.quant_equity,
                    stock_equity=excluded.stock_equity,
                    easicoin_equity=CASE
                        WHEN excluded.easicoin_equity > 0 THEN excluded.easicoin_equity
                        ELSE equity_snapshots.easicoin_equity
                    END,
                    binance_equity=CASE
                        WHEN excluded.binance_equity > 0 THEN excluded.binance_equity
                        ELSE equity_snapshots.binance_equity
                    END,
                    bitget_equity=CASE
                        WHEN excluded.bitget_equity > 0 THEN excluded.bitget_equity
                        ELSE equity_snapshots.bitget_equity
                    END,
                    mexc_equity=CASE
                        WHEN excluded.mexc_equity > 0 THEN excluded.mexc_equity
                        ELSE equity_snapshots.mexc_equity
                    END
                """,
                (
                    bucket,
                    int(ts_sec),
                    float(total_equity),
                    float(unified_equity),
                    float(quant_equity),
                    float(stock_equity),
                    float(easicoin_equity),
                    float(binance_equity),
                    float(bitget_equity),
                    float(mexc_equity),
                ),
            )

    def load_equity_snapshots(self, limit: int = 200_000) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT ts_sec, total_equity, unified_equity, quant_equity, stock_equity, easicoin_equity, binance_equity, bitget_equity, mexc_equity "
                "FROM equity_snapshots ORDER BY bucket_sec ASC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_ai_diagnostic(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        now = int(time.time())
        doc = {**dict(payload), "created_at": now}
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO ai_diagnostics(created_at, payload_json) VALUES (?, ?)",
                (now, json.dumps(doc, ensure_ascii=False)),
            )
        return doc

    def load_latest_ai_diagnostic(self) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM ai_diagnostics ORDER BY id DESC LIMIT 1").fetchone()
            return json.loads(row["payload_json"]) if row else None
