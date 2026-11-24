import time
from pathlib import Path

from fastapi.testclient import TestClient

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.api.app import create_app
from opentradeledger.core.ai_reviewer import build_ai_context_payload
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


def test_okx_and_bybit_plugin_snapshots_respect_range_filter_and_populate_summary_curve_calendar_and_attribution(
    tmp_path: Path,
):
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    now_ms = int(time.time() * 1000)
    recent_close_ms = now_ms - 2 * 86400 * 1000  # 2 days ago (inside 7d)
    recent_open_ms = recent_close_ms - 3600 * 1000
    old_close_ms = now_ms - 45 * 86400 * 1000  # 45 days ago (outside 7d & 30d)
    old_open_ms = old_close_ms - 3600 * 1000

    store.upsert_plugin_snapshot(
        "okx",
        {
            "balances": [{"totalEq": "2500.00", "details": [{"ccy": "USDT", "availBal": "2000.0"}]}],
            "deposits": [{"ccy": "USDT", "amt": "2000.0"}],
            "withdrawals": [],
            "trade_fee": [{"makerU": "-0.0002", "takerU": "-0.0005"}],
            "positions": [],
            "positions_history": [
                {
                    "posId": "okx_recent",
                    "instId": "BTC-USDT-SWAP",
                    "direction": "long",
                    "openAvgPx": "80000",
                    "closeAvgPx": "82000",
                    "closeTotalPos": "0.25",
                    "cTime": str(recent_open_ms),
                    "uTime": str(recent_close_ms),
                    "pnl": "500.0",
                    "fee": "-14.0",
                    "realizedPnl": "486.0",
                },
                {
                    "posId": "okx_old",
                    "instId": "SOL-USDT-SWAP",
                    "direction": "short",
                    "openAvgPx": "200",
                    "closeAvgPx": "220",
                    "closeTotalPos": "5.0",
                    "cTime": str(old_open_ms),
                    "uTime": str(old_close_ms),
                    "pnl": "-100.0",
                    "fee": "-6.0",
                    "realizedPnl": "-106.0",
                },
            ],
            "fills": [],
            "observed_rebate_ratio": 0.3,
        },
    )

    svc = LedgerService(store, vault, reg, default_demo_mode=False)

    # 1. All-time dashboard must compute win_rate, fees, curve, calendar, and symbol_attribution for OKX!
    dash_all = svc.get_dashboard(range_key="all")
    assert len(dash_all["trades"]) == 2
    assert dash_all["summary"]["win_rate_pct"] == 50.0
    assert dash_all["summary"]["total_fee_usd"] == 20.0
    assert dash_all["summary"]["total_rebate_usd"] == 6.0
    assert len(dash_all["equity_curve"]) >= 2
    assert len(dash_all["calendar_days"]) >= 2
    syms = {r["symbol"] for r in dash_all["symbol_attribution"]}
    assert {"BTC_USDT", "SOL_USDT"}.issubset(syms)

    # 2. 7d range filter MUST filter OKX plugin trades down to only the 2-day-old trade!
    dash_7d = svc.get_dashboard(range_key="7d")
    assert len(dash_7d["trades"]) == 1
    assert dash_7d["trades"][0]["symbol"] == "BTC_USDT"
    assert dash_7d["summary"]["trade_count"] == 1
    assert dash_7d["summary"]["win_rate_pct"] == 100.0
    # Exchange trade_count & AI context under 7d must match filtered 7d window (1 trade, not 2!)
    okx_ex = next(e for e in dash_7d["exchanges"] if e["venue_id"] == "okx")
    assert okx_ex["trade_count"] == 1
    ai_ctx = build_ai_context_payload(dash_7d)
    assert len(ai_ctx["recent_20_trades"]) == 1
    assert ai_ctx["exchanges"][0]["trade_count"] == 1


def test_delete_exchange_purges_cached_plugin_and_legacy_ledger_data(tmp_path: Path):
    app = create_app(data_dir=tmp_path, demo_mode=False, auto_prompt_setup=False, enable_background_sync=False)
    client = TestClient(app)

    # Seed credential + plugin snapshot + legacy binance bundle
    assert client.post(
        "/api/v1/exchanges",
        json={"venue_id": "bybit", "api_key": "byb_k_12345678", "api_secret": "byb_s_12345678"},
    ).status_code == 200
    assert client.post(
        "/api/v1/exchanges",
        json={"venue_id": "binance", "api_key": "bn_k_12345678", "api_secret": "bn_s_12345678"},
    ).status_code == 200

    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot(
        "bybit",
        {
            "wallet": {"list": [{"totalEquity": "1500.0", "totalAvailableBalance": "1500.0"}]},
            "deposits": [{"coin": "USDT", "amount": "1500.0"}],
        },
    )
    store.legacy.set_meta("binance_live_bundle", {"wallet_balances": [{"balance": "800.0"}]})
    store.legacy.upsert_binance_income([{"tranId": "t1", "symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "50.0", "time": 1732000000000}])

    dash_before = client.get("/api/v1/dashboard").json()
    vids_before = {e["venue_id"] for e in dash_before["exchanges"]}
    assert "bybit" in vids_before
    assert "binance" in vids_before

    # Delete both exchanges -> their cached snapshots/bundles/rows must be purged so ghost assets disappear!
    assert client.delete("/api/v1/exchanges/bybit").status_code == 200
    assert client.delete("/api/v1/exchanges/binance").status_code == 200

    assert store.load_plugin_snapshots() == {}
    assert store.legacy.get_meta("binance_live_bundle", None) is None
    assert store.legacy.load_binance_income() == []


def test_gate_qcom_perpetual_included_in_stock_perpetual_contracts(tmp_path: Path):
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    close_ts = int(time.time()) - 3600
    store.legacy.set_meta("live_bundle", {"wallet_total": {"total": {"amount": "1000.0"}}})
    store.legacy.upsert_futures_closes(
        [
            {
                "contract": "QCOM_USDT",
                "side": "long",
                "pnl": "80.0",
                "pnl_pnl": "80.0",
                "pnl_fee": "-5.04",
                "pnl_fund": "0.0",
                "time": close_ts,
                "first_open_time": close_ts - 600,
                "max_size": "10",
            }
        ]
    )

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard()
    perps = dash["extensions"]["stocks"]["perpetuals"]
    perp_syms = {(p["venue_id"], p["symbol"]) for p in perps}
    assert ("gate", "QCOM_USDT") in perp_syms


def test_english_fields_contain_no_chinese_characters_for_reconcile_and_extensions(tmp_path: Path):
    import re

    cjk_re = re.compile(r"[\u4e00-\u9fff]")
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    store.legacy.set_meta(
        "live_bundle",
        {
            "wallet_total": {"total": {"amount": "4600.0"}, "details": {"quant": {"amount": "880.0"}}},
            "stock_positions": [
                {
                    "symbol": "ANT",
                    "name": "Ant Group",
                    "amount": "0.61",
                    "cost_price": "83.0",
                    "last_price": "87.41",
                    "market_value": "53.32",
                    "profit": "2.69",
                    "profit_rate": "0.0532",
                }
            ],
        },
    )
    store.legacy.upsert_spot_account_book(
        [
            {"id": "1", "time": 1731800000, "currency": "USDT", "change": "4900.0", "type": "deposit"},
            {"id": "2", "time": 1731810000, "currency": "USDT", "change": "-1000.0", "type": "fct_out"},
            {"id": "3", "time": 1731820000, "currency": "USDT", "change": "1.25", "type": "pu_rebate"},
        ]
    )

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard()

    # Every reconciliation item must have genuine English `label` and `detail` (zero Chinese chars!)
    for item in dash["reconciliation"]["items"]:
        assert item["label"] and not cjk_re.search(item["label"]), f"Chinese leaked in label: {item['label']}"
        assert item["detail"] and not cjk_re.search(item["detail"]), f"Chinese leaked in detail: {item['detail']}"

    # Copy trading transfers & rebates English fields must not contain Chinese
    for tr in dash["extensions"]["copy_trading"]["transfers"]:
        assert not cjk_re.search(tr["direction"]), f"Chinese leaked in transfer direction: {tr['direction']}"
    for rb in dash["extensions"]["copy_trading"]["rebates"]:
        assert not cjk_re.search(rb["destination"]), f"Chinese leaked in rebate destination: {rb['destination']}"

    # Stock holdings channel English field must not contain Chinese
    for h in dash["extensions"]["stocks"]["holdings"]:
        assert not cjk_re.search(h["channel"]), f"Chinese leaked in stock channel: {h['channel']}"
