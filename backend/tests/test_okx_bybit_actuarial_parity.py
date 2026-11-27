"""TDD suite verifying OKX v5 & Bybit v5 full actuarial parity with Binance/Gate/EasiCoin."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import httpx
import pytest

from opentradeledger.adapters.bybit.client import BybitReadOnlyClient, normalize_bybit_closed_trades
from opentradeledger.adapters.okx.client import OkxReadOnlyClient, normalize_okx_closed_trades
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


@pytest.mark.asyncio
async def test_okx_cursor_pagination_c2c_bills_copy_and_per_trade_rebate_matching() -> None:
    """OKX client paginates >1 page via `after`, fetches bills-archive + asset/bills + copytrading, and matches rebates."""
    calls: list[tuple[str, dict[str, str]]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        q = dict(req.url.params)
        calls.append((path, q))

        if path == "/api/v5/account/balance":
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        {
                            "totalEq": "12500.00",
                            "details": [
                                {"ccy": "USDT", "availBal": "9800.00", "eq": "10500.00", "eqUsd": "10500.00"},
                                {"ccy": "XNVDA", "availBal": "10", "eq": "10", "eqUsd": "1400.00"},
                                {"ccy": "ETH", "availBal": "0.2", "eq": "0.2", "eqUsd": "600.00"},
                            ],
                        }
                    ],
                },
            )
        if path == "/api/v5/asset/balances":
            return httpx.Response(200, json={"code": "0", "data": [{"ccy": "USDT", "bal": "500.00"}]})
        if path == "/api/v5/account/positions":
            return httpx.Response(200, json={"code": "0", "data": []})
        if path == "/api/v5/account/positions-history":
            # Simulate 2 pages using `after` cursor when page_limit=2
            if not q.get("after"):
                return httpx.Response(
                    200,
                    json={
                        "code": "0",
                        "data": [
                            {
                                "posId": "p1",
                                "instId": "ETH-USDT-SWAP",
                                "direction": "short",
                                "type": "1",  # OKX partial close
                                "cTime": "1732280000000",
                                "uTime": "1732290000000",
                                "openAvgPx": "3000",
                                "closeAvgPx": "2950",
                                "closeTotalPos": "2",
                                "pnl": "100.0",
                                "fee": "-5.0",
                                "fundingFee": "1.25",
                                "realizedPnl": "96.25",
                            },
                            {
                                "posId": "p2",
                                "instId": "TSLA-USDT-SWAP",
                                "direction": "long",
                                "type": "3",
                                "cTime": "1732180000000",
                                "uTime": "1732190000000",
                                "openAvgPx": "340",
                                "closeAvgPx": "355",
                                "closeTotalPos": "10",
                                "pnl": "150.0",
                                "fee": "-3.4",
                                "fundingFee": "-0.60",
                                "realizedPnl": "146.0",
                            },
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        {
                            "posId": "p3",
                            "instId": "BTC-USDT-SWAP",
                            "direction": "long",
                            "type": "3",
                            "cTime": "1732080000000",
                            "uTime": "1732090000000",
                            "openAvgPx": "90000",
                            "closeAvgPx": "91000",
                            "closeTotalPos": "0.1",
                            "pnl": "100.0",
                            "fee": "-4.0",
                            "fundingFee": "0.0",
                            "realizedPnl": "96.0",
                        }
                    ],
                },
            )
        if path == "/api/v5/trade/fills-history":
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        {"billId": "f1", "instId": "ETH-USDT-SWAP", "side": "sell", "execType": "M", "fee": "-1.5", "feeCcy": "USDT", "ts": "1732280001000"},
                        {"billId": "f2", "instId": "ETH-USDT-SWAP", "side": "buy", "execType": "T", "fee": "-3.5", "feeCcy": "USDT", "ts": "1732289999000"},
                        {"billId": "f3", "instId": "TSLA-USDT-SWAP", "side": "buy", "execType": "M", "fee": "-1.0", "feeCcy": "USDT", "ts": "1732180001000"},
                        {"billId": "f4", "instId": "TSLA-USDT-SWAP", "side": "sell", "execType": "T", "fee": "-2.4", "feeCcy": "USDT", "ts": "1732189999000"},
                    ],
                },
            )
        if path in ("/api/v5/account/bills", "/api/v5/account/bills-archive"):
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        # Direct rebate bills + Earn interest bill
                        {"billId": "b_reb_1", "instId": "ETH-USDT-SWAP", "type": "15", "subType": "173", "balChg": "2.00", "ts": "1732290500000"},
                        {"billId": "b_reb_2", "instId": "TSLA-USDT-SWAP", "type": "15", "subType": "173", "balChg": "1.36", "ts": "1732190500000"},
                        {"billId": "b_reb_3", "instId": "BTC-USDT-SWAP", "type": "15", "subType": "173", "balChg": "1.60", "ts": "1732090500000"},
                        {"billId": "b_earn_1", "ccy": "USDT", "type": "13", "subType": "earn_interest", "balChg": "18.50", "ts": "1732291000000"},
                    ],
                },
            )
        if path == "/api/v5/asset/bills":
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        # C2C fiat purchase of 8,000 USDT + internal transfer (type 6, must NOT double count!)
                        {"billId": "ab_c2c_1", "ccy": "USDT", "type": "c2c_buy", "balChg": "8000.00", "ts": "1731900000000"},
                        {"billId": "ab_internal", "ccy": "USDT", "type": "6", "balChg": "-5000.00", "ts": "1731900100000"},
                    ],
                },
            )
        if path == "/api/v5/asset/deposit-history":
            return httpx.Response(200, json={"code": "0", "data": [{"depId": "d1", "ccy": "USDT", "amt": "4000.00", "state": "2", "ts": "1731800000000"}]})
        if path == "/api/v5/asset/withdrawal-history":
            return httpx.Response(200, json={"code": "0", "data": []})
        if path == "/api/v5/copytrading/current-subpositions":
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        {"subPosId": "cp1", "instId": "SOL-USDT-SWAP", "margin": "300.00", "upl": "24.50"},
                    ],
                },
            )
        if path == "/api/v5/market/tickers":
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        {"instId": "XNVDA-USDT", "last": "140.00"},
                        {"instId": "ETH-USDT", "last": "3000.00"},
                    ],
                },
            )
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OkxReadOnlyClient(
            api_key="k",
            api_secret="s",
            passphrase="p",
            client=http,
            page_limit=2,
        )
        snap = await client.fetch_full_snapshot()

    # All 3 closed trades across 2 paginated pages were fetched!
    assert len(snap["positions_history"]) == 3
    trades = normalize_okx_closed_trades(
        snap["positions_history"],
        snap["fills"],
        bills=snap["bills"],
        rebate_ratio=snap["observed_rebate_ratio"],
    )
    assert len(trades) == 3
    eth_tr = next(t for t in trades if t["symbol"] == "ETH_USDT")
    assert eth_tr["is_partial_close"] is True
    assert eth_tr["funding_pnl_usd"] == 1.25
    assert eth_tr["total_rebate_usd"] == pytest.approx(2.0, abs=0.01)
    assert eth_tr["entry_leg"]["settled_at"] != ""


@pytest.mark.asyncio
async def test_bybit_cursor_pagination_funding_rebates_mnt_payment_and_incremental_archive(tmp_path: Path) -> None:
    """Bybit client follows `nextPageCursor`, retains funding, rebates and MNT payment denomination, and merges monotonically in SQLite."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        q = dict(req.url.params)

        if path == "/v5/account/wallet-balance":
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "list": [
                            {
                                "totalEquity": "6200.00",
                                "totalAvailableBalance": "5100.00",
                                "coin": [
                                    {"coin": "USDT", "walletBalance": "5500.00", "usdValue": "5500.00"},
                                    {"coin": "AAPLX", "walletBalance": "3", "usdValue": "700.00"},
                                ],
                            }
                        ]
                    },
                },
            )
        if path == "/v5/asset/transfer/query-account-coins-balance":
            return httpx.Response(200, json={"retCode": 0, "result": {"balance": [{"coin": "USDT", "walletBalance": "300.00"}]}})
        if path == "/v5/position/closed-pnl":
            if not q.get("cursor"):
                return httpx.Response(
                    200,
                    json={
                        "retCode": 0,
                        "result": {
                            "nextPageCursor": "cur_page_2",
                            "list": [
                                {
                                    "orderId": "ord_1",
                                    "symbol": "XAUUSDT",
                                    "side": "Sell",  # closes LONG
                                    "closedSize": "2",
                                    "qty": "2",
                                    "avgEntryPrice": "2600",
                                    "avgExitPrice": "2650",
                                    "cumEntryValue": "5200",
                                    "cumExitValue": "5300",
                                    "openFee": "1.04",
                                    "closeFee": "2.65",
                                    "closedPnl": "96.31",
                                    "createdTime": "1732270000000",
                                    "updatedTime": "1732290000000",
                                }
                            ],
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "nextPageCursor": "",
                        "list": [
                            {
                                "orderId": "ord_2",
                                "symbol": "BTCUSDT",
                                "side": "Buy",  # closes SHORT
                                "closedSize": "0.1",
                                "qty": "0.2",  # partial close!
                                "avgEntryPrice": "92000",
                                "avgExitPrice": "91000",
                                "cumEntryValue": "9200",
                                "cumExitValue": "9100",
                                "openFee": "1.84",
                                "closeFee": "4.55",
                                "closedPnl": "93.61",
                                "createdTime": "1732170000000",
                                "updatedTime": "1732190000000",
                            }
                        ],
                    },
                },
            )
        if path == "/v5/execution/list":
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "list": [
                            {"execId": "e1", "orderId": "ord_open_1", "symbol": "XAUUSDT", "side": "Buy", "isMaker": True, "execFee": "1.04", "feeCurrency": "MNT", "closedSize": "0", "execTime": "1732270001000"},
                            {"execId": "e2", "orderId": "ord_1", "symbol": "XAUUSDT", "side": "Sell", "isMaker": False, "execFee": "2.65", "feeCurrency": "USDT", "closedSize": "2", "execTime": "1732290000000"},
                        ]
                    },
                },
            )
        if path == "/v5/account/transaction-log":
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "list": [
                            # Funding settlement during XAUUSDT hold window + affiliate rebate!
                            {"id": "tx_fund_1", "symbol": "XAUUSDT", "type": "SETTLEMENT", "funding": "-1.50", "cashFlow": "-1.50", "transactionTime": "1732280000000"},
                            {"id": "tx_reb_1", "symbol": "XAUUSDT", "type": "REBATE", "cashFlow": "1.11", "transactionTime": "1732290600000"},
                            {"id": "tx_reb_2", "symbol": "BTCUSDT", "type": "REBATE", "cashFlow": "1.92", "transactionTime": "1732190600000"},
                        ]
                    },
                },
            )
        if path == "/v5/asset/deposit/query-record":
            return httpx.Response(200, json={"retCode": 0, "result": {"rows": [{"id": "dep1", "coin": "USDT", "amount": "5000.00", "successAt": "1731000000000"}]}})
        if path == "/v5/asset/deposit/query-internal-record":
            return httpx.Response(200, json={"retCode": 0, "result": {"rows": [{"id": "int1", "coin": "USDT", "amount": "1000.00", "createdTime": "1731100000"}]}})
        if path == "/v5/asset/withdraw/query-record":
            return httpx.Response(200, json={"retCode": 0, "result": {"rows": []}})
        if path == "/v5/account/fee-rate":
            return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"makerFeeRate": "0.0002", "takerFeeRate": "0.00055"}]}})
        return httpx.Response(200, json={"retCode": 0, "result": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BybitReadOnlyClient(api_key="k", api_secret="s", client=http, max_windows=1)
        snap = await client.fetch_full_snapshot()

    assert len(snap["closed_pnl"]) == 2
    trades = normalize_bybit_closed_trades(
        snap["closed_pnl"],
        snap["executions"],
        tx_logs=snap["tx_logs"],
        rebate_ratio=snap["observed_rebate_ratio"],
    )
    assert len(trades) == 2
    xau = next(t for t in trades if t["symbol"] == "XAU_USDT")
    # Funding (-1.50) and Rebate (+1.11) and MNT payment currency must be preserved.
    assert xau["funding_pnl_usd"] == pytest.approx(-1.50, abs=0.01)
    assert xau["total_rebate_usd"] == pytest.approx(1.11, abs=0.01)
    assert xau["entry_leg"]["discount_asset"] == "MNT"
    assert xau["entry_leg"]["discount_saved_usd"] == 0
    # Waterfall closes cleanly: gross_pnl + funding_pnl - net_fee == net_pnl
    assert round(xau["gross_pnl_usd"] + xau["funding_pnl_usd"] - xau["net_fee_usd"], 4) == pytest.approx(xau["net_pnl_usd"], abs=0.01)

    # Incremental SQLite archival: if a later poll returns ONLY a newer trade `ord_3` (older `ord_1`, `ord_2` aged out),
    # `upsert_plugin_snapshot` must retain `ord_1` and `ord_2` instead of wiping them!
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("bybit", snap)
    newer_partial_snap: dict[str, Any] = {
        **snap,
        "closed_pnl": [
            {
                "orderId": "ord_3",
                "symbol": "ETHUSDT",
                "side": "Sell",
                "closedSize": "1",
                "qty": "1",
                "avgEntryPrice": "3000",
                "avgExitPrice": "3100",
                "cumEntryValue": "3000",
                "cumExitValue": "3100",
                "openFee": "0.60",
                "closeFee": "1.55",
                "closedPnl": "97.85",
                "createdTime": "1732370000000",
                "updatedTime": "1732390000000",
            }
        ],
    }
    store.upsert_plugin_snapshot("bybit", newer_partial_snap)
    merged = store.load_plugin_snapshots()["bybit"]
    assert len(merged["closed_pnl"]) == 3


def test_okx_and_bybit_dashboard_decomposes_reconcile_streams_and_populates_copy_and_stocks_extensions(tmp_path: Path) -> None:
    """Dashboard decomposes OKX & Bybit into multi-stream reconciliation (0.00 residual) + copy trading + tokenized stocks & stock/commodity perps."""
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()
    svc = LedgerService(store, vault, reg, default_demo_mode=False)

    store.upsert_plugin_snapshot(
        "okx",
        {
            "venue_id": "okx",
            "balances": [
                {
                    "totalEq": "12500.00",
                    "details": [
                        {"ccy": "USDT", "availBal": "9800.00", "eq": "10500.00", "eqUsd": "10500.00"},
                        {"ccy": "XNVDA", "availBal": "10", "eq": "10", "eqUsd": "1400.00"},
                    ],
                }
            ],
            "funding": [{"ccy": "USDT", "bal": "500.00"}],
            "positions": [],
            "positions_history": [
                {
                    "posId": "p2",
                    "instId": "TSLA-USDT-SWAP",
                    "direction": "long",
                    "type": "3",
                    "cTime": "1732180000000",
                    "uTime": "1732190000000",
                    "openAvgPx": "340",
                    "closeAvgPx": "355",
                    "closeTotalPos": "10",
                    "pnl": "150.0",
                    "fee": "-3.4",
                    "fundingFee": "-0.60",
                    "realizedPnl": "146.0",
                }
            ],
            "fills": [
                {"billId": "f3", "instId": "TSLA-USDT-SWAP", "side": "buy", "execType": "M", "fee": "-1.0", "feeCcy": "USDT", "ts": "1732180001000"},
                {"billId": "f4", "instId": "TSLA-USDT-SWAP", "side": "sell", "execType": "T", "fee": "-2.4", "feeCcy": "USDT", "ts": "1732189999000"},
            ],
            "trade_fee": [{"makerU": "-0.0002", "takerU": "-0.0005"}],
            "bills": [
                {"billId": "b_reb_2", "instId": "TSLA-USDT-SWAP", "type": "15", "subType": "173", "balChg": "1.36", "ts": "1732190500000"},
                {"billId": "b_earn_1", "ccy": "USDT", "type": "13", "subType": "earn_interest", "balChg": "18.50", "ts": "1732291000000"},
            ],
            "asset_bills": [
                {"billId": "ab_c2c_1", "ccy": "USDT", "type": "c2c_buy", "balChg": "8000.00", "ts": "1731900000000"},
            ],
            "deposits": [{"depId": "d1", "ccy": "USDT", "amt": "4000.00", "state": "2", "ts": "1731800000000"}],
            "withdrawals": [],
            "copy_positions": [
                {"subPosId": "cp1", "instId": "SOL-USDT-SWAP", "margin": "300.00", "upl": "24.50"},
            ],
            "observed_rebate_ratio": 0.40,
        },
    )

    store.upsert_plugin_snapshot(
        "bybit",
        {
            "venue_id": "bybit",
            "wallet": {
                "list": [
                    {
                        "totalEquity": "6200.00",
                        "totalAvailableBalance": "5100.00",
                        "coin": [
                            {"coin": "USDT", "walletBalance": "5500.00", "usdValue": "5500.00"},
                            {"coin": "AAPLX", "walletBalance": "3", "usdValue": "700.00"},
                        ],
                    }
                ]
            },
            "funding": {"balance": [{"coin": "USDT", "walletBalance": "300.00"}]},
            "positions": [],
            "closed_pnl": [
                {
                    "orderId": "ord_1",
                    "symbol": "XAUUSDT",
                    "side": "Sell",
                    "closedSize": "2",
                    "qty": "2",
                    "avgEntryPrice": "2600",
                    "avgExitPrice": "2650",
                    "cumEntryValue": "5200",
                    "cumExitValue": "5300",
                    "openFee": "1.04",
                    "closeFee": "2.65",
                    "closedPnl": "96.31",
                    "createdTime": "1732270000000",
                    "updatedTime": "1732290000000",
                }
            ],
            "executions": [
                {"execId": "e1", "orderId": "ord_open_1", "symbol": "XAUUSDT", "side": "Buy", "isMaker": True, "execFee": "1.04", "feeCurrency": "MNT", "closedSize": "0", "execTime": "1732270001000"},
                {"execId": "e2", "orderId": "ord_1", "symbol": "XAUUSDT", "side": "Sell", "isMaker": False, "execFee": "2.65", "feeCurrency": "USDT", "closedSize": "2", "execTime": "1732290000000"},
            ],
            "tx_logs": [
                {"id": "tx_fund_1", "symbol": "XAUUSDT", "type": "SETTLEMENT", "funding": "-1.50", "cashFlow": "-1.50", "transactionTime": "1732280000000"},
                {"id": "tx_reb_1", "symbol": "XAUUSDT", "type": "REBATE", "cashFlow": "1.11", "transactionTime": "1732290600000"},
            ],
            "fee_rates": [{"makerFeeRate": "0.0002", "takerFeeRate": "0.00055"}],
            "deposits": [{"id": "dep1", "coin": "USDT", "amount": "5000.00", "successAt": "1731000000000"}],
            "internal_deposits": [{"id": "int1", "coin": "USDT", "amount": "1000.00", "createdTime": "1731100000"}],
            "withdrawals": [],
            "observed_rebate_ratio": 0.30,
        },
    )

    dash = svc.get_dashboard(range_key="all")

    # 1. Principal includes C2C (OKX: 4000 on-chain + 8000 C2C = 12000) and Internal Deposits (Bybit: 5000 + 1000 = 6000)
    assert dash["summary"]["net_principal_usd"] == pytest.approx(18000.0, abs=0.01)
    assert dash["summary"]["total_equity_usd"] == pytest.approx(19500.0, abs=0.01)  # OKX 13000 + Bybit 6500
    assert dash["reconciliation"]["residual_usd"] == 0.0

    # 2. Decomposed reconciliation streams (futures + copy + earn/spot, NOT just 1 monolithic row per venue!)
    rec_keys = {it["key"] for it in dash["reconciliation"]["items"]}
    assert "okx_futures" in rec_keys
    assert "bybit_futures" in rec_keys
    assert "okx_copy_trading" in rec_keys
    rec_sum = round(sum(it["amount_usd"] for it in dash["reconciliation"]["items"]), 2)
    assert rec_sum == pytest.approx(dash["reconciliation"]["net_pnl_usd"], abs=0.01)

    # 3. Copy trading extension includes OKX active copy subposition
    cp = dash["extensions"]["copy_trading"]
    assert any(p["venue_id"] == "okx" for p in cp["platforms"])
    assert cp["active_equity_usd"] >= 324.50

    # 4. Stocks & Commodities extension includes XNVDA + AAPLX tokenized stocks AND TSLA_USDT + XAU_USDT perps!
    st = dash["extensions"]["stocks"]
    holding_syms = {h["symbol"] for h in st["holdings"]}
    assert {"XNVDA", "AAPLX"}.issubset(holding_syms)
    assert st["equity_usd"] >= 2100.0
    perp_syms = {(p["venue_id"], p["symbol"]) for p in st["perpetuals"]}
    assert ("okx", "TSLA_USDT") in perp_syms
    assert ("bybit", "XAU_USDT") in perp_syms

    # 5. Only verified savings surface in the fee matrix.
    bybit_fee = next(r for r in dash["fee_matrix"] if r["venue_id"] == "bybit")
    assert bybit_fee["discount_saved_usd"] == 0
    assert bybit_fee["discount_label"] == ""
    assert dash["summary"]["potential_maker_savings_usd"] > 0
