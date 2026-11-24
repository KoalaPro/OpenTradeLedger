"""Cross-module isolation & schema contract tests preventing adapter/filter/purge interference."""

from __future__ import annotations

from pathlib import Path

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.sync_manager import _extract_snapshot_activity
from opentradeledger.security.vault import CredentialVault


def test_plugin_venue_zero_interference_with_legacy_venues(tmp_path: Path) -> None:
    """Connecting or removing OKX/Bybit must not alter Gate/EasiCoin/Binance metrics by even 0.01 U."""
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()
    svc = LedgerService(store, vault, reg, default_demo_mode=False)

    # Seed realistic Gate + EasiCoin legacy data with fractional 4-decimal fees
    store.legacy.set_meta(
        "live_bundle",
        {
            "unified": {"total_equity_usd": 4689.06, "balances": {"USDT": {"available": 3600.0, "equity": 3600.0}}},
            "futures_account": {"total": "3600.0", "available": "3200.0", "unrealised_pnl": "12.5"},
            "futures_positions": [
                {
                    "contract": "ETH_USDT",
                    "size": 2,
                    "leverage": 5,
                    "entry_price": "3000",
                    "mark_price": "3006.25",
                    "unrealised_pnl": "12.5",
                }
            ],
        },
    )
    store.legacy.upsert_futures_closes(
        [
            {
                "contract": "ETH_USDT",
                "side": "long",
                "first_open_time": 1732280000,
                "time": 1732290000,
                "pnl": "25.0",
                "pnl_fee": "-1.2346",
                "pnl_fund": "0",
            },
            {
                "contract": "SOL_USDT",
                "side": "short",
                "first_open_time": 1732260000,
                "time": 1732270000,
                "pnl": "-8.0",
                "pnl_fee": "-1.2346",
                "pnl_fund": "0",
            },
        ]
    )

    baseline = svc.get_dashboard(range_key="all")
    base_gate_ex = next(ex for ex in baseline["exchanges"] if ex["venue_id"] == "gate")
    base_summary = dict(baseline["summary"])

    # 1. Connect an empty OKX snapshot (0 trades, 0 equity) -> legacy summary & Gate exchange must stay 100% identical
    store.upsert_plugin_snapshot("okx", {"balances": [{"totalEq": "0", "details": []}], "positions_history": [], "fills": []})
    with_empty_okx = svc.get_dashboard(range_key="all")
    assert with_empty_okx["summary"]["total_fee_usd"] == base_summary["total_fee_usd"]
    assert with_empty_okx["summary"]["net_fee_usd"] == base_summary["net_fee_usd"]
    assert with_empty_okx["summary"]["win_rate_pct"] == base_summary["win_rate_pct"]

    # 2. Connect a larger Bybit account (10,000 U) -> Gate's equity_usd & Bybit's 0.00 reconciliation must not cross-contaminate
    store.upsert_plugin_snapshot(
        "bybit",
        {
            "wallet": {"list": [{"totalEquity": "10000.00", "totalAvailableBalance": "9500.00", "coin": []}]},
            "deposits": [{"coin": "USDT", "amount": "9000.00"}],
            "withdrawals": [],
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "size": "0.1",
                    "leverage": "5",
                    "positionValue": "9000",
                    "avgPrice": "89000",
                    "markPrice": "90000",
                    "unrealisedPnl": "100.00",
                }
            ],
            "closed_pnl": [],
            "executions": [],
        },
    )
    with_bybit = svc.get_dashboard(range_key="all")
    gate_after = next(ex for ex in with_bybit["exchanges"] if ex["venue_id"] == "gate")
    bybit_after = next(ex for ex in with_bybit["exchanges"] if ex["venue_id"] == "bybit")
    assert gate_after["equity_usd"] == base_gate_ex["equity_usd"]
    assert bybit_after["equity_usd"] == 10000.00
    assert bybit_after["net_pnl_usd"] == 1000.00

    # 3. Filtering by venue_filter="bybit" must NOT leak Gate's open_positions, equity_curve, or symbol_attribution
    bybit_only = svc.get_dashboard(range_key="all", venue_filter="bybit")
    assert all(p["venue_id"] == "bybit" for p in bybit_only["open_positions"])
    assert all(t["venue_id"] == "bybit" for t in bybit_only["trades"])
    assert all(s["venue_id"] == "bybit" for s in bybit_only["symbol_attribution"])
    assert bybit_only["equity_curve"] == []

    # 4. Purging OKX & Bybit restores exact pre-connection dashboard
    store.purge_venue_data("okx")
    store.purge_venue_data("bybit")
    restored = svc.get_dashboard(range_key="all")
    assert restored["summary"] == base_summary


def test_purge_venue_preserves_other_venues_equity_snapshots_and_journals(tmp_path: Path) -> None:
    """Purging Gate must not wipe Binance/EasiCoin columns in equity_snapshots, and vice versa."""
    store = LedgerStore(tmp_path)
    with store.legacy._lock, store.legacy._connect() as conn:
        conn.execute(
            """
            INSERT INTO equity_snapshots (bucket_sec, ts_sec, total_equity, unified_equity, quant_equity, stock_equity, easicoin_equity, binance_equity)
            VALUES (1732233600, 1732233600, 4689.06, 3800.0, 835.74, 53.32, 1058.61, 520.00)
            """
        )
    store.save_journal("ETH_USDT_1_2_long", ["planned"], "保留复盘笔记")

    # Purging Gate clears Gate's columns but preserves EasiCoin & Binance snapshot history + journals
    store.purge_venue_data("gate")
    snaps_after_gate = store.legacy.load_equity_snapshots()
    assert len(snaps_after_gate) == 1
    assert snaps_after_gate[0]["total_equity"] == 0.0
    assert snaps_after_gate[0]["easicoin_equity"] == 1058.61
    assert snaps_after_gate[0]["binance_equity"] == 520.00
    assert store.load_journals()["ETH_USDT_1_2_long"]["note"] == "保留复盘笔记"

    # Purging Binance zeroes binance_equity while preserving easicoin_equity
    store.purge_venue_data("binance")
    snaps_after_bn = store.legacy.load_equity_snapshots()
    assert len(snaps_after_bn) == 1
    assert snaps_after_bn[0]["binance_equity"] == 0.0
    assert snaps_after_bn[0]["easicoin_equity"] == 1058.61


def test_demo_and_live_dashboard_schema_contract_parity_and_funding_valuation(tmp_path: Path) -> None:
    """Demo and Live dashboards must expose identical top-level keys, and SyncManager must value non-USDT funding."""
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()
    svc = LedgerService(store, vault, reg, default_demo_mode=False)

    demo_dash = svc.get_dashboard(force_demo=True)
    store.upsert_plugin_snapshot("okx", {"balances": [{"totalEq": "100", "details": []}]})
    live_dash = svc.get_dashboard(force_demo=False)

    assert set(demo_dash.keys()) == set(live_dash.keys())
    assert set(demo_dash["equity_breakdown"].keys()) == set(live_dash["equity_breakdown"].keys())

    # SyncManager values non-USDT funding balances via tickers / usdValue so active accounts don't idle-sleep
    okx_eq, _, _ = _extract_snapshot_activity(
        "okx",
        {"balances": [], "funding": [{"ccy": "BTC", "bal": "0.01"}], "tickers": {"BTC": 90000.0}},
    )
    bybit_eq, _, _ = _extract_snapshot_activity(
        "bybit",
        {"wallet": {"list": []}, "funding": {"balance": [{"coin": "ETH", "walletBalance": "0.5", "usdValue": "1500.0"}]}},
    )
    assert okx_eq == 900.0
    assert bybit_eq == 1500.0
