from pathlib import Path

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


def test_demo_dashboard_exposes_six_venues_per_leg_fees_and_zero_residual(tmp_path: Path):
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()
    svc = LedgerService(store, vault, reg, default_demo_mode=True)

    dash = svc.get_dashboard()
    assert dash["demo_mode"] is True
    assert dash["read_only"] is True
    venue_ids = [e["venue_id"] for e in dash["exchanges"]]
    assert "okx" in venue_ids
    assert "bybit" in venue_ids
    assert "binance" in venue_ids
    assert dash["reconciliation"]["residual_usd"] == 0.0

    # Every trade MUST disclose both entry_leg and exit_leg fees explicitly
    assert len(dash["trades"]) >= 10
    for tr in dash["trades"]:
        en = tr["entry_leg"]
        ex = tr["exit_leg"]
        assert round(en["gross_fee_usd"] + ex["gross_fee_usd"], 4) == tr["total_fee_usd"]
        assert round(en["net_fee_usd"] + ex["net_fee_usd"], 4) == tr["net_fee_usd"]

    # Filtering by OKX or Bybit works dynamically
    okx_only = svc.get_dashboard(venue_filter="okx")
    assert len(okx_only["trades"]) >= 2
    assert all(t["venue_id"] == "okx" for t in okx_only["trades"])

    bybit_only = svc.get_dashboard(venue_filter="bybit")
    assert len(bybit_only["trades"]) >= 2
    assert all(t["venue_id"] == "bybit" for t in bybit_only["trades"])


def test_plugin_snapshots_for_okx_and_bybit_merge_into_live_dashboard(tmp_path: Path):
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

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
                    "posId": "okx1",
                    "instId": "BTC-USDT-SWAP",
                    "direction": "long",
                    "openAvgPx": "80000",
                    "closeAvgPx": "82000",
                    "closeTotalPos": "0.25",
                    "cTime": "1732000000000",
                    "uTime": "1732010000000",
                    "pnl": "500.0",
                    "fee": "-14.0",
                    "realizedPnl": "486.0",
                }
            ],
            "fills": [
                {"instId": "BTC-USDT-SWAP", "side": "buy", "fee": "-4.0", "execType": "M", "ts": "1732000001000"},
                {"instId": "BTC-USDT-SWAP", "side": "sell", "fee": "-10.0", "execType": "T", "ts": "1732009999000"},
            ],
            "observed_rebate_ratio": 0.3,
        },
    )
    store.upsert_plugin_snapshot(
        "bybit",
        {
            "wallet": {"list": [{"totalEquity": "3200.0", "totalAvailableBalance": "2800.0"}]},
            "deposits": [{"coin": "USDT", "amount": "3000.0"}],
            "withdrawals": [],
            "fee_rates": [{"makerFeeRate": "0.0002", "takerFeeRate": "0.00055"}],
            "positions": [],
            "closed_pnl": [
                {
                    "orderId": "bb1",
                    "symbol": "ETHUSDT",
                    "side": "Sell",
                    "qty": "2.0",
                    "closedSize": "2.0",
                    "avgEntryPrice": "2500",
                    "avgExitPrice": "2600",
                    "cumEntryValue": "5000",
                    "cumExitValue": "5200",
                    "openFee": "1.0",
                    "closeFee": "2.86",
                    "closedPnl": "196.14",
                    "createdTime": "1732100000000",
                    "updatedTime": "1732105000000",
                }
            ],
            "executions": [
                {"orderId": "bb1", "symbol": "ETHUSDT", "side": "Sell", "execFee": "2.86", "isMaker": False, "execTime": "1732105000000"},
                {"orderId": "bb0", "symbol": "ETHUSDT", "side": "Buy", "execFee": "1.0", "isMaker": True, "closedSize": "0", "execTime": "1732100001000"},
            ],
            "observed_rebate_ratio": 0.25,
        },
    )

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard()
    assert dash["demo_mode"] is False
    vids = {e["venue_id"] for e in dash["exchanges"]}
    assert vids == {"okx", "bybit"}
    assert dash["summary"]["total_equity_usd"] == 5700.0
    assert len(dash["trades"]) == 2
    by_venue = {t["venue_id"]: t for t in dash["trades"]}
    assert by_venue["okx"]["entry_leg"]["gross_fee_usd"] == 4.0
    assert by_venue["okx"]["exit_leg"]["gross_fee_usd"] == 10.0
    assert by_venue["bybit"]["entry_leg"]["gross_fee_usd"] == 1.0
    assert by_venue["bybit"]["exit_leg"]["gross_fee_usd"] == 2.86


def test_preferences_and_journal_persist_in_sqlite(tmp_path: Path):
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    svc = LedgerService(store, vault, build_default_registry(), default_demo_mode=True)

    svc.update_preferences({"locale": "en", "polarity": "global", "goal_enabled": True, "goal_target_usd": 30000})
    prefs = svc.get_preferences()
    assert prefs.locale == "en"
    assert prefs.polarity == "global"
    assert prefs.goal_enabled is True
    assert prefs.goal_target_usd == 30000

    dash = svc.get_dashboard()
    assert dash["goal"]["target_usd"] == 30000

    first_id = dash["trades"][0]["trade_id"]
    svc.save_journal(first_id, ["planned"], "Clean exit")
    dash2 = svc.get_dashboard()
    assert dash2["trades"][0]["note"] == "Clean exit"
    assert dash2["trades"][0]["tags"] == ["planned"]
