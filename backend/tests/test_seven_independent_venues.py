"""Architectural & actuarial tests enforcing 7 independent exchange modules (zero monolith coupling)."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
import sqlite3

import pytest

from opentradeledger.adapters.base import BaseExchangeAdapter
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault

ALL_SEVEN_VENUES = (
    "binance",
    "okx",
    "bybit",
    "gate",
    "bitget",
    "mexc",
    "hyperliquid",
    "kucoin",
    "bingx",
    "htx",
    "easicoin",
)


def test_all_seven_adapters_have_independent_sync_and_accounting_modules() -> None:
    """Every one of the 7 exchanges must own its own sync.py & accounting.py with zero cross-venue imports."""
    reg = build_default_registry()
    registered_ids = tuple(m.venue_id for m in reg.list_all())
    assert set(registered_ids) == set(ALL_SEVEN_VENUES)

    adapters_root = Path(__file__).resolve().parents[1] / "src" / "opentradeledger" / "adapters"

    for vid in ALL_SEVEN_VENUES:
        adapter = reg.get(vid)
        assert adapter is not None
        for method_name in (
            "sync_venue",
            "has_venue_data",
            "build_venue_ledger",
            "build_fee_matrix_row",
            "extract_spot_prices",
        ):
            assert callable(getattr(adapter, method_name, None)), f"{vid} missing {method_name}"
            assert getattr(type(adapter), method_name) is not getattr(BaseExchangeAdapter, method_name), (
                f"{vid} must override {method_name} on its own adapter class"
            )

        sync_mod = importlib.import_module(f"opentradeledger.adapters.{vid}.sync")
        acc_mod = importlib.import_module(f"opentradeledger.adapters.{vid}.accounting")
        assert sync_mod is not None and acc_mod is not None

        # Verify AST: no file inside adapters/<vid>/ may import another venue adapters/<other>/
        venue_dir = adapters_root / vid
        other_venues = set(ALL_SEVEN_VENUES) - {vid}
        for py_file in venue_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for other in other_venues:
                            assert f"adapters.{other}" not in alias.name, (
                                f"Cross-venue import forbidden: {py_file.name} imports {alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    for other in other_venues:
                        assert f"adapters.{other}" not in node.module, (
                            f"Cross-venue import forbidden: {py_file.name} imports from {node.module}"
                        )


def test_sync_manager_and_ledger_service_have_zero_monolith_or_venue_branching() -> None:
    """SyncManager and LedgerService must orchestrate uniformly through AdapterRegistry without venue if/elif."""
    core_dir = Path(__file__).resolve().parents[1] / "src" / "opentradeledger" / "core"
    sync_src = (core_dir / "sync_manager.py").read_text(encoding="utf-8")
    ledger_src = (core_dir / "ledger_service.py").read_text(encoding="utf-8")

    assert "legacy_venue_sync" not in sync_src
    assert "LEGACY_VENUE_SYNCERS" not in sync_src
    assert "accounting_engine" not in ledger_src
    assert "build_full_accounting_report" not in ledger_src

    for vid in ALL_SEVEN_VENUES:
        assert f'== "{vid}"' not in ledger_src, f"Hardcoded venue branch found in ledger_service.py for {vid}"
        assert f"== '{vid}'" not in ledger_src, f"Hardcoded venue branch found in ledger_service.py for {vid}"
        assert f'== "{vid}"' not in sync_src, f"Hardcoded venue branch found in sync_manager.py for {vid}"


def _seed_all_seven_venues(store: LedgerStore) -> None:
    # 1. Gate
    g_scope = store.venue_scope("gate")
    g_scope.set_kv(
        "live_bundle",
        {
            "wallet_total": {"total": {"amount": "4500.00", "unrealised_pnl": "0.0"}, "details": {"spot": {"amount": "4500.00"}}},
            "futures_account": {"available": "4200.00"},
            "unified": {"unified_account_total_equity": "4500.00", "balances": {"USDT": {"available": "4500.00", "equity": "4500.00"}}},
        },
    )
    g_scope.upsert_records(
        "spot_account_book",
        [{"id": "g_dep_1", "time": 1732000000, "currency": "USDT", "type": "deposit", "change": "4400.00"}],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["time"]),
    )
    g_scope.upsert_records(
        "futures_closes",
        [
            {
                "contract": "BTC_USDT",
                "side": "long",
                "first_open_time": 1732100000,
                "time": 1732103600,
                "max_size": "100",
                "long_price": "90000",
                "short_price": "91010",
                "pnl_pnl": "101.00",
                "pnl_fee": "-1.00",
                "pnl_fund": "0.00",
                "pnl": "100.00",
            }
        ],
        key_fn=lambda r: f"{r['contract']}_{r['first_open_time']}_{r['time']}_{r['side']}",
        ts_fn=lambda r: float(r["time"]),
    )

    # 2. EasiCoin
    ec_scope = store.venue_scope("easicoin")
    ec_scope.set_kv(
        "live_bundle",
        {
            "uid": "ec_1",
            "futures_balances": [{"coin": "USDT", "equity": "1080.00", "available_balance": "1080.00", "unrealised_pnl": "0"}],
            "funding_balances": [],
            "spot_balances": [],
            "tickers": [],
        },
    )
    ec_scope.upsert_records(
        "transfers",
        [{"time": 1732000000, "coin": "USDT", "amount": "1000.00", "from_wallet": "FUNDING", "to_wallet": "TRADING", "transfer_status": "1"}],
        key_fn=lambda r: f"{r['time']}_{r['amount']}",
        ts_fn=lambda r: float(r["time"]),
    )
    ec_scope.upsert_records(
        "closed_pnl",
        [
            {
                "order_id": "ec_ord_1",
                "symbol": "ETHUSDT",
                "side": "Sell",
                "closed_size": "1.0",
                "avg_entry_price": "3000",
                "avg_exit_price": "3082",
                "cum_entry_value": "3000",
                "cum_exit_value": "3082",
                "closed_pnl": "80.00",
                "created_at": 1732110000,
            }
        ],
        key_fn=lambda r: str(r["order_id"]),
        ts_fn=lambda r: float(r["created_at"]),
    )

    # 3. Binance
    bn_scope = store.venue_scope("binance")
    bn_scope.set_kv(
        "live_bundle",
        {
            "uid": "bn_1",
            "pm_account": {"accountEquity": "2150.00", "totalAvailableBalance": "2150.00"},
            "wallet_balances": [{"walletName": "Portfolio Margin", "balance": "2150.00", "activate": True}],
            "spot_balances": [],
            "spot_tickers": {},
        },
    )
    bn_scope.upsert_records(
        "cashflows",
        [{"cf_key": "bn_dep_1", "cf_type": "DEPOSIT", "asset": "USDT", "amount": "2000.00", "time_ms": 1732000000000}],
        key_fn=lambda r: str(r["cf_key"]),
        ts_fn=lambda r: float(r["time_ms"]),
    )
    bn_scope.upsert_records(
        "fills",
        [
            {"id": 101, "orderId": 201, "symbol": "SOLUSDT", "side": "BUY", "positionSide": "LONG", "qty": "10", "price": "200", "quoteQty": "2000", "realizedPnl": "0", "commission": "0.40", "commissionAsset": "USDT", "maker": True, "time": 1732120000000},
            {"id": 102, "orderId": 202, "symbol": "SOLUSDT", "side": "SELL", "positionSide": "LONG", "qty": "10", "price": "215.14", "quoteQty": "2151.4", "realizedPnl": "151.40", "commission": "1.40", "commissionAsset": "USDT", "maker": False, "time": 1732123600000},
        ],
        key_fn=lambda r: f"{r['symbol']}_{r['id']}",
        ts_fn=lambda r: float(r["time"]),
    )

    # 4. Bitget
    bg_scope = store.venue_scope("bitget")
    bg_scope.set_kv(
        "live_bundle",
        {
            "uid": "bg_1",
            "uta_assets": {"accountEquity": "1560.00", "available": "1560.00"},
            "funding_assets": [],
            "spot_tickers": {},
        },
    )
    bg_scope.upsert_records(
        "bills",
        [{"id": "bg_dep_1", "_source": "v3_deposit", "coin": "USDT", "amount": "1500.00", "status": "success", "ts": 1732000000000}],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["ts"]),
    )
    bg_scope.upsert_records(
        "positions_history",
        [
            {
                "positionId": "bg_p1",
                "symbol": "XRPUSDT",
                "posSide": "long",
                "closeTotalPos": "1000",
                "openAvgPrice": "1.00",
                "closeAvgPrice": "1.062",
                "pnl": "62.00",
                "openFee": "-1.00",
                "closeFee": "-1.00",
                "netProfit": "60.00",
                "cTime": "1732130000000",
                "uTime": "1732133600000",
            }
        ],
        key_fn=lambda r: str(r["positionId"]),
        ts_fn=lambda r: float(r["uTime"]),
    )

    # 5. MEXC
    mx_scope = store.venue_scope("mexc")
    mx_scope.set_kv(
        "live_bundle",
        {
            "refer_code": "mx_1",
            "futures_assets": [{"currency": "USDT", "equity": "930.00", "availableBalance": "930.00"}],
            "spot_balances": [],
            "spot_tickers": {},
        },
    )
    mx_scope.upsert_records(
        "transfers",
        [{"id": "mx_dep_1", "_source": "fut_transfer", "currency": "USDT", "type": "IN", "state": "SUCCESS", "amount": "900.00", "createTime": 1732000000000}],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["createTime"]),
    )
    mx_scope.upsert_records(
        "positions_history",
        [
            {
                "positionId": "mx_p1",
                "symbol": "DOGE_USDT",
                "positionType": 1,
                "closeVol": "1000",
                "openAvgPrice": "0.30",
                "closeAvgPrice": "0.331",
                "closeProfitLoss": "31.00",
                "fee": "-1.00",
                "holdFee": "0",
                "createTime": 1732140000000,
                "updateTime": 1732143600000,
            }
        ],
        key_fn=lambda r: str(r["positionId"]),
        ts_fn=lambda r: float(r["updateTime"]),
    )

    # 6. OKX
    store.upsert_plugin_snapshot(
        "okx",
        {
            "balances": [{"totalEq": "3120.00", "details": [{"ccy": "USDT", "availBal": "3120.00"}]}],
            "deposits": [{"depId": "okx_d1", "ccy": "USDT", "amt": "3000.00", "state": "2"}],
            "withdrawals": [],
            "positions_history": [
                {
                    "posId": "okx_p1",
                    "instId": "ADA-USDT-SWAP",
                    "direction": "long",
                    "openAvgPx": "1.00",
                    "closeAvgPx": "1.125",
                    "closeTotalPos": "1000",
                    "cTime": "1732150000000",
                    "uTime": "1732153600000",
                    "pnl": "125.00",
                    "fee": "-5.00",
                    "realizedPnl": "120.00",
                }
            ],
            "fills": [],
            "observed_rebate_ratio": 0.0,
        },
    )

    # 7. Bybit
    store.upsert_plugin_snapshot(
        "bybit",
        {
            "wallet": {"list": [{"totalEquity": "2490.00", "totalAvailableBalance": "2490.00", "coin": []}]},
            "deposits": [{"id": "byb_d1", "coin": "USDT", "amount": "2400.00"}],
            "withdrawals": [],
            "closed_pnl": [
                {
                    "orderId": "byb_p1",
                    "symbol": "AVAXUSDT",
                    "side": "Sell",
                    "qty": "10",
                    "closedSize": "10",
                    "avgEntryPrice": "30.0",
                    "avgExitPrice": "39.2",
                    "cumEntryValue": "300.0",
                    "cumExitValue": "392.0",
                    "openFee": "1.0",
                    "closeFee": "1.0",
                    "closedPnl": "90.00",
                    "createdTime": "1732160000000",
                    "updatedTime": "1732163600000",
                }
            ],
            "executions": [],
            "observed_rebate_ratio": 0.0,
        },
    )

    # 8. Hyperliquid
    store.upsert_plugin_snapshot(
        "hyperliquid",
        {
            "wallet_address": "0x" + "1" * 40,
            "clearinghouse": {"marginSummary": {"accountValue": "2200.00", "withdrawable": "2200.00"}, "assetPositions": []},
            "spot": {"balances": []},
            "tickers": {},
            "fills": [
                {"tid": 1, "oid": 1, "coin": "ETH", "px": "3000.0", "sz": "1.0", "side": "B", "dir": "Open Long", "closedPnl": "0.0", "fee": "0.5", "crossed": True, "time": 1732170000000},
                {"tid": 2, "oid": 2, "coin": "ETH", "px": "3101.0", "sz": "1.0", "side": "A", "dir": "Close Long", "closedPnl": "101.0", "fee": "0.5", "crossed": True, "time": 1732173600000},
            ],
            "funding_history": [],
            "ledger_updates": [{"hash": "0xhl1", "time": 1732000000000, "delta": {"type": "deposit", "usdc": "2100.00"}}],
        },
    )

    # 9. KuCoin
    store.upsert_plugin_snapshot(
        "kucoin",
        {
            "futures_overview": {"accountEquity": 1600.0, "availableBalance": 1600.0},
            "spot_accounts": [],
            "deposits": [{"walletTxId": "kc_d1", "currency": "USDT", "amount": "1500.00", "createdAt": 1732000000000}],
            "withdrawals": [],
            "positions": [],
            "history_positions": [
                {
                    "closeId": "kc_p1",
                    "symbol": "ETHUSDTM",
                    "side": "long",
                    "closeSize": 10,
                    "multiplier": 0.1,
                    "openPrice": 3000,
                    "closePrice": 3101,
                    "openTimestamp": 1732180000000,
                    "closeTimestamp": 1732183600000,
                    "tradeFee": 1.0,
                    "fundingFee": 0.0,
                    "pnl": 100.0,
                }
            ],
            "fills": [],
            "tx_history": [],
            "tickers": {},
        },
    )

    # 10. BingX
    store.upsert_plugin_snapshot(
        "bingx",
        {
            "swap_balances": [{"asset": "USDT", "equity": "1400.00", "availableMargin": "1400.00"}],
            "spot_balances": [],
            "deposits": [{"id": "bx_d1", "coin": "USDT", "amount": "1350.00", "insertTime": 1732000000000}],
            "withdrawals": [],
            "positions": [],
            "fills": [
                {"tradeId": "bx_1", "symbol": "BTC-USDT", "side": "BUY", "positionSide": "LONG", "price": "90000", "qty": "0.1", "commission": "-0.5", "realizedPnl": "0", "role": "MAKER", "filledTm": 1732190000000},
                {"tradeId": "bx_2", "symbol": "BTC-USDT", "side": "SELL", "positionSide": "LONG", "price": "90510", "qty": "0.1", "commission": "-0.5", "realizedPnl": "51.0", "role": "TAKER", "filledTm": 1732193600000},
            ],
            "income": [],
            "tickers": {},
        },
    )

    # 11. HTX
    store.upsert_plugin_snapshot(
        "htx",
        {
            "swap_accounts": [{"margin_balance": "1900.00", "margin_available": "1900.00"}],
            "spot_balances": [],
            "deposits": [{"id": "htx_d1", "currency": "usdt", "amount": "1820.00", "state": "safe", "created-at": 1732000000000}],
            "withdrawals": [],
            "positions": [],
            "matches": [
                {"match_id": "htx_1", "contract_code": "SOL-USDT", "direction": "buy", "offset": "open", "trade_price": "200", "trade_turnover": "2000", "trade_volume": "10", "trade_fee": "-0.5", "offset_profitloss": "0", "role": "maker", "create_date": 1732195000000},
                {"match_id": "htx_2", "contract_code": "SOL-USDT", "direction": "sell", "offset": "close", "trade_price": "208.1", "trade_turnover": "2081", "trade_volume": "10", "trade_fee": "-0.5", "offset_profitloss": "81.0", "role": "taker", "create_date": 1732198600000},
            ],
            "financials": [],
            "tickers": {},
        },
    )


def test_seven_venues_isolated_reconciliation_filtering_and_purge(tmp_path: Path) -> None:
    """All 7 venues reconcile independently to 0.00 residual, filter cleanly with zero curve bleed, and purge in isolation."""
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()
    _seed_all_seven_venues(store)

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash_all = svc.get_dashboard(range_key="all")

    assert {e["venue_id"] for e in dash_all["exchanges"]} == set(ALL_SEVEN_VENUES)
    assert len(dash_all["trades"]) == len(ALL_SEVEN_VENUES)
    assert dash_all["reconciliation"]["residual_usd"] == 0.0
    rec_sum = round(sum(it["amount_usd"] for it in dash_all["reconciliation"]["items"]), 2)
    assert rec_sum == pytest.approx(dash_all["reconciliation"]["net_pnl_usd"], abs=0.01)

    # Every single venue filtered individually must ONLY return its own trades & its own equity_curve (zero cross-venue bleed!)
    per_venue_snapshots: dict[str, dict] = {}
    for vid in ALL_SEVEN_VENUES:
        v_dash = svc.get_dashboard(range_key="all", venue_filter=vid)
        per_venue_snapshots[vid] = v_dash
        assert len(v_dash["trades"]) == 1, f"{vid} expected 1 trade"
        assert v_dash["trades"][0]["venue_id"] == vid
        assert len(v_dash["equity_curve"]) >= 1
        ex_card = next(e for e in v_dash["exchanges"] if e["venue_id"] == vid)
        # Filtered single-venue curve cumulative PnL must reflect ONLY that venue (not all 7 venues!)
        assert abs(v_dash["equity_curve"][-1]["cum_net_usd"] - ex_card["net_pnl_usd"]) <= 0.05

    # Purging 1 venue (e.g. gate) must have exact 0.00 effect on the other 6 venues
    store.purge_venue_data("gate")
    after_purge_all = svc.get_dashboard(range_key="all")
    assert {e["venue_id"] for e in after_purge_all["exchanges"]} == (set(ALL_SEVEN_VENUES) - {"gate"})
    assert after_purge_all["reconciliation"]["residual_usd"] == 0.0
    for other_vid in set(ALL_SEVEN_VENUES) - {"gate"}:
        other_after = svc.get_dashboard(range_key="all", venue_filter=other_vid)
        assert other_after["trades"] == per_venue_snapshots[other_vid]["trades"]
        assert other_after["equity_curve"] == per_venue_snapshots[other_vid]["equity_curve"]


def test_auto_migration_from_legacy_sqlite_tables_preserves_all_cached_records(tmp_path: Path) -> None:
    """Opening an existing opentradeledger.db with legacy tables automatically migrates records into venue partitions."""
    db_path = tmp_path / "opentradeledger.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE kv_meta (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at INTEGER NOT NULL)")
    conn.execute("CREATE TABLE futures_closes (close_key TEXT PRIMARY KEY, close_time REAL NOT NULL, payload_json TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO kv_meta VALUES ('live_bundle', '{\"wallet_total\":{\"total\":{\"amount\":\"1200.0\"}}}', 1732000000)"
    )
    conn.execute(
        "INSERT INTO futures_closes VALUES ('BTC_1_2_long', 1732100000, '{\"contract\":\"BTC_USDT\",\"side\":\"long\",\"first_open_time\":1732090000,\"time\":1732100000,\"pnl\":\"25.0\",\"pnl_fee\":\"-1.0\"}')"
    )
    conn.commit()
    conn.close()

    store = LedgerStore(tmp_path)
    g_scope = store.venue_scope("gate")
    assert g_scope.get_kv("live_bundle")["wallet_total"]["total"]["amount"] == "1200.0"
    closes = g_scope.load_records("futures_closes", desc=True)
    assert len(closes) == 1
    assert closes[0]["contract"] == "BTC_USDT"


def test_unbind_keep_history_archives_venue_and_allows_later_purge_or_rebind(tmp_path: Path) -> None:
    """Deleting an API with keep_data=true keeps local ledger at 0.00 residual, marks archived, and allows later purge."""
    from fastapi.testclient import TestClient
    from opentradeledger.api.app import create_app

    app = create_app(
        data_dir=tmp_path,
        demo_mode=False,
        enable_background_sync=False,
        auto_prompt_setup=False,
    )
    client = TestClient(app)

    store = LedgerStore(tmp_path)
    _seed_all_seven_venues(store)

    # Save credentials for gate and binance
    assert client.post(
        "/api/v1/exchanges",
        json={"venue_id": "gate", "api_key": "gate_key_123456", "api_secret": "gate_sec_123456"},
    ).status_code == 200
    assert client.post(
        "/api/v1/exchanges",
        json={"venue_id": "binance", "api_key": "bn_key_123456", "api_secret": "bn_sec_123456"},
    ).status_code == 200

    # 1. Unbind Gate with keep_data=true -> API key destroyed, local history kept, status='archived'
    r_keep = client.delete("/api/v1/exchanges/gate?keep_data=true")
    assert r_keep.status_code == 200
    assert r_keep.json()["kept_data"] is True
    assert r_keep.json()["has_archived_data"] is True

    ex_after_keep = {a["venue_id"]: a for a in client.get("/api/v1/exchanges").json()["adapters"]}
    assert ex_after_keep["gate"]["configured"] is False
    assert ex_after_keep["gate"]["has_archived_data"] is True
    assert ex_after_keep["gate"]["health"]["status"] == "archived"
    assert ex_after_keep["binance"]["configured"] is True

    dash_after_keep = client.get("/api/v1/dashboard?range=all").json()
    assert dash_after_keep["reconciliation"]["residual_usd"] == 0.0
    assert len(dash_after_keep["trades"]) == len(ALL_SEVEN_VENUES)
    gate_card = next(e for e in dash_after_keep["exchanges"] if e["venue_id"] == "gate")
    bn_card = next(e for e in dash_after_keep["exchanges"] if e["venue_id"] == "binance")
    assert gate_card["connected"] is False
    assert gate_card["health_status"] == "archived"
    assert bn_card["connected"] is True
    assert bn_card["health_status"] == "connected"

    # 2. Subsequent purge of already-archived Gate (keep_data=false, even with no API key) clears Gate cleanly
    r_purge = client.delete("/api/v1/exchanges/gate?keep_data=false")
    assert r_purge.status_code == 200
    assert r_purge.json()["has_archived_data"] is False

    ex_after_purge = {a["venue_id"]: a for a in client.get("/api/v1/exchanges").json()["adapters"]}
    assert ex_after_purge["gate"]["configured"] is False
    assert ex_after_purge["gate"]["has_archived_data"] is False
    assert ex_after_purge["gate"]["health"]["status"] == "not_configured"

    dash_after_purge = client.get("/api/v1/dashboard?range=all").json()
    assert dash_after_purge["reconciliation"]["residual_usd"] == 0.0
    assert len(dash_after_purge["trades"]) == len(ALL_SEVEN_VENUES) - 1
    assert all(e["venue_id"] != "gate" for e in dash_after_purge["exchanges"])


def test_unified_tokenized_stock_promotion_all_venues_and_subaccounts(tmp_path: Path) -> None:
    """All 11 exchanges + sub-accounts share stock_channels.py recognition with 0.00 residual and zero double-counting."""
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    # 1. KuCoin primary + sub-account both holding NVDAX + deceptive crypto SNX (must NOT be misclassified)
    store.upsert_plugin_snapshot(
        "kucoin",
        {
            "futures_overview": {"accountEquity": 1000.0, "availableBalance": 1000.0},
            "spot_accounts": [
                {"type": "trade", "currency": "NVDAX", "balance": "2.0"},
                {"type": "trade", "currency": "SNX", "balance": "10.0"},
            ],
            "deposits": [{"walletTxId": "kc_d1", "currency": "USDT", "amount": "1200.00", "createdAt": 1732000000000}],
            "withdrawals": [],
            "positions": [],
            "history_positions": [],
            "fills": [],
            "tx_history": [],
            "tickers": {"NVDAX": 130.0, "SNX": 2.0},
        },
    )
    store.venue_scope("kucoin__sub1").set_kv("account_role", "subaccount")
    store.upsert_plugin_snapshot(
        "kucoin__sub1",
        {
            "futures_overview": {"accountEquity": 500.0, "availableBalance": 500.0},
            "spot_accounts": [{"type": "trade", "currency": "NVDAX", "balance": "1.5"}],
            "deposits": [{"walletTxId": "kcs_d1", "currency": "USDT", "amount": "695.00", "createdAt": 1732000000000}],
            "withdrawals": [],
            "positions": [],
            "history_positions": [],
            "fills": [],
            "tx_history": [],
            "tickers": {"NVDAX": 130.0},
        },
    )

    # 2. BingX holding TSLAX + deceptive crypto GMX
    store.upsert_plugin_snapshot(
        "bingx",
        {
            "swap_balances": [{"asset": "USDT", "equity": "800.00", "availableMargin": "800.00"}],
            "spot_balances": [
                {"asset": "TSLAX", "free": "1.0", "locked": "0"},
                {"asset": "GMX", "free": "1.0", "locked": "0"},
            ],
            "deposits": [{"id": "bx_d1", "coin": "USDT", "amount": "1000.00", "insertTime": 1732000000000}],
            "withdrawals": [],
            "positions": [],
            "fills": [],
            "income": [],
            "tickers": {"TSLAX": 250.0, "GMX": 30.0},
        },
    )

    # 3. HTX holding AAPLON + deceptive crypto NEON
    store.upsert_plugin_snapshot(
        "htx",
        {
            "swap_accounts": [{"margin_balance": "700.00", "margin_available": "700.00"}],
            "spot_balances": [
                {"currency": "aaplon", "type": "trade", "balance": "2.0"},
                {"currency": "neon", "type": "trade", "balance": "50.0"},
            ],
            "deposits": [{"id": "htx_d1", "currency": "usdt", "amount": "1100.00", "state": "safe", "created-at": 1732000000000}],
            "withdrawals": [],
            "positions": [],
            "matches": [],
            "financials": [],
            "tickers": {"AAPLON": 220.0, "NEON": 1.0},
        },
    )

    # 4. Hyperliquid holding QQQSTOCK in on-chain spot
    store.upsert_plugin_snapshot(
        "hyperliquid",
        {
            "wallet_address": "0x" + "2" * 40,
            "clearinghouse": {"marginSummary": {"accountValue": "600.00", "withdrawable": "600.00"}, "assetPositions": []},
            "spot": {"balances": [{"coin": "QQQSTOCK", "total": "1.0"}]},
            "tickers": {"QQQSTOCK": 500.0},
            "fills": [],
            "funding_history": [],
            "ledger_updates": [{"hash": "0xhl2", "time": 1732000000000, "delta": {"type": "deposit", "usdc": "1100.00"}}],
        },
    )

    # 5. OKX unified COSTX (from 140-ticker dict, preserves openAvgPx PnL) + funding NVDAX (promoted centrally)
    store.upsert_plugin_snapshot(
        "okx",
        {
            "balances": [
                {
                    "totalEq": "1800.00",
                    "details": [
                        {"ccy": "USDT", "availBal": "900.00", "eq": "900.00", "eqUsd": "900.00"},
                        {"ccy": "COSTX", "eq": "1.0", "eqUsd": "900.00", "openAvgPx": "850.00"},
                    ],
                }
            ],
            "funding": [{"ccy": "NVDAX", "bal": "1.0"}],
            "deposits": [{"depId": "okx_d1", "ccy": "USDT", "amt": "1800.00", "state": "2"}],
            "withdrawals": [],
            "positions_history": [],
            "fills": [],
            "tickers": {"COSTX": 900.0, "NVDAX": 130.0},
            "observed_rebate_ratio": 0.0,
        },
    )

    # 6. Bybit unified VOOX (from 140-ticker dict, preserves avgPrice PnL) + funding TSLAX (promoted centrally)
    store.upsert_plugin_snapshot(
        "bybit",
        {
            "wallet": {
                "list": [
                    {
                        "totalEquity": "1500.00",
                        "totalAvailableBalance": "1000.00",
                        "coin": [
                            {"coin": "USDT", "walletBalance": "1000.0", "usdValue": "1000.0"},
                            {"coin": "VOOX", "walletBalance": "1.0", "usdValue": "500.0", "avgPrice": "480.0"},
                        ],
                    }
                ]
            },
            "funding": {"balance": [{"coin": "TSLAX", "walletBalance": "1.0", "usdValue": "250.0"}]},
            "deposits": [{"id": "byb_d1", "coin": "USDT", "amount": "1700.00"}],
            "withdrawals": [],
            "closed_pnl": [],
            "executions": [],
            "tickers": {"VOOX": 500.0, "TSLAX": 250.0},
            "observed_rebate_ratio": 0.0,
        },
    )

    # 7. Bitget sub-account holding exchange-specific Bitget rStock (rAAPL) in funding wallet
    bg_sub = store.venue_scope("bitget__sub1")
    bg_sub.set_kv("account_role", "subaccount")
    bg_sub.set_kv(
        "live_bundle",
        {
            "uid": "bg_sub_uid",
            "uta_assets": {"accountEquity": "400.00", "available": "400.00"},
            "funding_assets": [{"coin": "rAAPL", "available": "2.0"}],
            "spot_tickers": {"RAAPL": 220.0},
        },
    )
    bg_sub.upsert_records(
        "bills",
        [{"id": "bgs_dep_1", "_source": "v3_deposit", "coin": "USDT", "amount": "840.00", "status": "success", "ts": 1732000000000}],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["ts"]),
    )

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")

    assert dash["reconciliation"]["residual_usd"] == 0.0
    holdings_by_key = {(h["venue_id"], h["symbol"]): h for h in dash["extensions"]["stocks"]["holdings"]}

    # All 11 adapters declare tokenized_stocks=True
    assert all(m.capabilities.tokenized_stocks is True for m in reg.list_all())

    # KuCoin primary & sub-account both recognized cleanly without colliding
    assert holdings_by_key[("kucoin", "NVDAX")]["name"] == "NVIDIA 英伟达"
    assert holdings_by_key[("kucoin", "NVDAX")]["market_value_usd"] == pytest.approx(260.0)
    assert holdings_by_key[("kucoin__sub1", "NVDAX")]["market_value_usd"] == pytest.approx(195.0)
    assert "子账户" in holdings_by_key[("kucoin__sub1", "NVDAX")]["venue_name_zh"]

    # BingX, HTX, Hyperliquid promoted with localized names
    assert holdings_by_key[("bingx", "TSLAX")]["name"] == "Tesla 特斯拉"
    assert holdings_by_key[("htx", "AAPLON")]["name"] == "Apple 苹果"
    assert holdings_by_key[("hyperliquid", "QQQSTOCK")]["name"] == "Invesco QQQ 纳指100 ETF"

    # OKX & Bybit unified keep native cost basis & get Chinese names, funding tokens promoted without duplicating unified
    assert holdings_by_key[("okx", "COSTX")]["name"] == "Costco 开市客"
    assert holdings_by_key[("okx", "COSTX")]["pnl_usd"] == pytest.approx(50.0)
    assert holdings_by_key[("okx", "NVDAX")]["market_value_usd"] == pytest.approx(130.0)
    assert holdings_by_key[("bybit", "VOOX")]["name"] == "Vanguard S&P 500 ETF"
    assert holdings_by_key[("bybit", "VOOX")]["pnl_usd"] == pytest.approx(20.0)
    assert holdings_by_key[("bybit", "TSLAX")]["market_value_usd"] == pytest.approx(250.0)
    assert len([h for h in dash["extensions"]["stocks"]["holdings"] if h["venue_id"] == "okx"]) == 2
    assert len([h for h in dash["extensions"]["stocks"]["holdings"] if h["venue_id"] == "bybit"]) == 2

    # Bitget sub-account preserves BITGET exchange code for rStock (rAAPL)
    assert holdings_by_key[("bitget__sub1", "RAAPL")]["name"] == "Apple 苹果"
    assert "子账户" in holdings_by_key[("bitget__sub1", "RAAPL")]["venue_name_zh"]

    # Promoted stock tokens must NOT appear in spot_holdings; deceptive crypto tokens MUST remain in spot_holdings
    spot_syms = {(s["venue_id"], s["asset"]) for s in dash["spot_holdings"]}
    for promoted_key in holdings_by_key:
        assert promoted_key not in spot_syms
    assert ("kucoin", "SNX") in spot_syms
    assert ("bingx", "GMX") in spot_syms
    assert ("htx", "NEON") in spot_syms

    expected_stocks_eq = round(sum(float(h["market_value_usd"]) for h in holdings_by_key.values()), 2)
    assert dash["extensions"]["stocks"]["equity_usd"] == pytest.approx(expected_stocks_eq, abs=0.02)
    assert dash["equity_breakdown"]["stocks_equity_usd"] == pytest.approx(expected_stocks_eq, abs=0.02)


@pytest.mark.asyncio
async def test_bingx_test_connection_detects_master_and_subaccount(monkeypatch: pytest.MonkeyPatch) -> None:
    """BingxAdapter.test_connection probes /openApi/subAccount/v1/list to classify master vs subaccount."""
    from opentradeledger.adapters.registry import BingxAdapter
    from opentradeledger.security.vault import ExchangeCredentialRecord

    adapter = BingxAdapter()
    cred = ExchangeCredentialRecord(venue_id="bingx", api_key="k", api_secret="s")

    class _FakeBingxClient:
        def __init__(self, *, is_sub: bool) -> None:
            self._is_sub = is_sub

        async def get_swap_balance(self) -> list[dict]:
            return [{"asset": "USDT", "equity": "100"}]

        async def get_uid(self) -> str:
            return "99002" if self._is_sub else "99001"

        async def request_readonly(self, method: str, path: str, params=None):
            assert method == "GET" and path == "/openApi/subAccount/v1/list"
            if self._is_sub:
                raise RuntimeError("BingX GET /openApi/subAccount/v1/list code=100400: sub-account has no permission")
            return {"subAccountList": []}

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(adapter, "create_client", lambda _c: _FakeBingxClient(is_sub=False))
    res_master = await adapter.test_connection(cred)
    assert res_master["remote_uid"] == "99001"
    assert res_master["account_role"] == "master"

    monkeypatch.setattr(adapter, "create_client", lambda _c: _FakeBingxClient(is_sub=True))
    res_sub = await adapter.test_connection(cred)
    assert res_sub["remote_uid"] == "99002"
    assert res_sub["account_role"] == "subaccount"


