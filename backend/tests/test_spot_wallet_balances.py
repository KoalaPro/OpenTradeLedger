from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


def test_cash_only_wallets_have_separate_balances_without_increasing_equity(tmp_path):
    store = LedgerStore(tmp_path)
    store.venue_scope("easicoin").set_kv(
        "live_bundle",
        {
            "spot_balances": [{"token_name": "USDT", "balance": "65"}],
            "funding_balances": [{"coin": "USDT", "total_equity": "35"}],
        },
    )
    service = LedgerService(store, CredentialVault(tmp_path), build_default_registry(), default_demo_mode=False)
    dash = service.get_dashboard()
    assert dash["summary"]["total_equity_usd"] == 100
    assert dash["equity_breakdown"]["spot_and_other_usd"] == 100
    assert dash["spot_holdings"] == []
    assert [
        (r["asset"], r["wallet_type"], r["qty"], r["market_value_usd"]) for r in dash["spot_wallet_balances"]
    ] == [("USDT", "SPOT", 65, 65), ("USDT", "FUNDING", 35, 35)]


def test_wallet_detail_preserves_unknown_prices_and_separate_wallet_types():
    from opentradeledger.core.venue_primitives import build_spot_wallet_balances, spot_holdings_from_wallets

    wallets = build_spot_wallet_balances(
        [
            ("USDC", 10, 9.98, "SPOT"),
            ("LDUSDC", 3, 0, "SPOT"),
            ("JPY", 20, 0, "FUNDING"),
            ("BTC", 1, 50, "SPOT"),
            ("BTC", 2, 100, "FUNDING"),
            ("NVDAx", 3, 40, "SPOT"),
        ],
        venue_id="binance", venue_name="Binance", color_token="binance",
        spot_prices={}, excluded_symbols={"NVDAX"},
    )
    rows = {(r["wallet_type"], r["asset"]): r for r in wallets}
    assert rows["SPOT", "USDC"]["market_value_usd"] == 9.98
    assert rows["EARN", "USDC"]["qty"] == 3
    assert rows["FUNDING", "JPY"]["market_value_usd"] is None
    assert ("SPOT", "NVDAX") not in rows
    (coin,) = spot_holdings_from_wallets(wallets)
    assert (coin["asset"], coin["wallet_type"], coin["qty"], coin["market_value_usd"]) == ("BTC", "MULTIPLE", 3, 150)
