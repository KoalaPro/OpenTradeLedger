"""Multi-account isolation, master/sub-account detection, and duplicate-UID protection tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import httpx
import pytest

from opentradeledger.adapters.bybit.client import BybitReadOnlyClient
from opentradeledger.adapters.okx.client import OkxReadOnlyClient
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.api.app import create_app
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.readonly_guard import ReadOnlyViolationError, assert_readonly_request
from opentradeledger.security.vault import CredentialVault, ExchangeCredentialRecord


def test_readonly_guard_allows_only_whitelisted_unsigned_post_queries() -> None:
    # Default POST without whitelist is strictly blocked
    with pytest.raises(ReadOnlyViolationError):
        assert_readonly_request("POST", "/info", ("/api/v1",))

    # Whitelisted POST /info with allowed type succeeds
    assert_readonly_request(
        "POST",
        "/info",
        (),
        allowed_post_paths=("/info",),
        allowed_post_types=("clearinghouseState", "subAccounts"),
        post_payload={"type": "clearinghouseState", "user": "0x" + "1" * 40},
    )

    # Non-whitelisted path (/exchange) or type or signed action payload is blocked
    with pytest.raises(ReadOnlyViolationError):
        assert_readonly_request(
            "POST",
            "/exchange",
            (),
            allowed_post_paths=("/info",),
            allowed_post_types=("clearinghouseState",),
            post_payload={"type": "clearinghouseState"},
        )
    with pytest.raises(ReadOnlyViolationError):
        assert_readonly_request(
            "POST",
            "/info",
            (),
            allowed_post_paths=("/info",),
            allowed_post_types=("clearinghouseState",),
            post_payload={"type": "order"},
        )
    with pytest.raises(ReadOnlyViolationError):
        assert_readonly_request(
            "POST",
            "/info",
            (),
            allowed_post_paths=("/info",),
            allowed_post_types=("clearinghouseState",),
            post_payload={"type": "clearinghouseState", "signature": "0xdeadbeef"},
        )


def test_okx_and_bybit_detect_master_vs_subaccount_and_uid() -> None:
    reg = build_default_registry()

    def okx_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v5/account/balance":
            return httpx.Response(200, json={"code": "0", "data": [{"totalEq": "1200"}]})
        if req.url.path == "/api/v5/account/config":
            is_sub = req.headers.get("ok-access-key") == "sub-key"
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        {
                            "uid": "990022" if is_sub else "990011",
                            "mainUid": "990011",
                        }
                    ],
                },
            )
        return httpx.Response(404)

    def bybit_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v5/account/wallet-balance":
            return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"totalEquity": "800"}]}})
        if req.url.path == "/v5/user/query-api":
            is_sub = req.headers.get("x-bapi-api-key") == "byb-sub"
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "userID": "770022" if is_sub else "770011",
                        "isMaster": not is_sub,
                        "readOnly": 1,
                    },
                },
            )
        return httpx.Response(404)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(okx_handler)) as okx_http:
            okx_ad = reg.get("okx")
            assert okx_ad is not None
            okx_ad.create_client = lambda cred: OkxReadOnlyClient(  # type: ignore[method-assign]
                api_key=cred.api_key,
                api_secret=cred.api_secret,
                passphrase=cred.passphrase,
                client=okx_http,
            )
            res_main = await okx_ad.test_connection(
                ExchangeCredentialRecord(venue_id="okx", api_key="main-key", api_secret="s", passphrase="p")
            )
            res_sub = await okx_ad.test_connection(
                ExchangeCredentialRecord(venue_id="okx", api_key="sub-key", api_secret="s", passphrase="p")
            )
            assert res_main["remote_uid"] == "990011"
            assert res_main["account_role"] == "master"
            assert res_sub["remote_uid"] == "990022"
            assert res_sub["account_role"] == "subaccount"

        async with httpx.AsyncClient(transport=httpx.MockTransport(bybit_handler)) as byb_http:
            byb_ad = reg.get("bybit")
            assert byb_ad is not None
            byb_ad.create_client = lambda cred: BybitReadOnlyClient(  # type: ignore[method-assign]
                api_key=cred.api_key,
                api_secret=cred.api_secret,
                client=byb_http,
            )
            b_main = await byb_ad.test_connection(
                ExchangeCredentialRecord(venue_id="bybit", api_key="byb-main", api_secret="s")
            )
            b_sub = await byb_ad.test_connection(
                ExchangeCredentialRecord(venue_id="bybit", api_key="byb-sub", api_secret="s")
            )
            assert b_main["remote_uid"] == "770011"
            assert b_main["account_role"] == "master"
            assert b_sub["remote_uid"] == "770022"
            assert b_sub["account_role"] == "subaccount"

    asyncio.run(_run())


def test_same_exchange_multi_account_sync_reconcile_filter_and_duplicate_uid_guard(tmp_path: Path) -> None:
    """Connecting 2 accounts on Bybit isolates partitions, blocks duplicate UID, and reconciles to 0.00 U."""
    reg = build_default_registry()

    class MockBybitMultiClient:
        def __init__(self, cred: ExchangeCredentialRecord) -> None:
            self.key = cred.api_key

        async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
            eq = "5000.00" if self.key.startswith("master") else "2000.00"
            return {"list": [{"totalEquity": eq, "totalAvailableBalance": eq, "coin": []}]}

        async def get_api_key_info(self) -> dict[str, Any]:
            is_master = self.key.startswith("master")
            return {
                "userID": "100001" if is_master else "100002",
                "isMaster": is_master,
                "readOnly": 1,
            }

        async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
            is_master = self.key.startswith("master")
            eq = "5000.00" if is_master else "2000.00"
            dep = "4500.00" if is_master else "1800.00"
            pnl = "500.00" if is_master else "200.00"
            sym = "BTCUSDT" if is_master else "ETHUSDT"
            return {
                "venue_id": "bybit",
                "wallet": {"list": [{"totalEquity": eq, "totalAvailableBalance": eq, "coin": []}]},
                "funding": {},
                "positions": [],
                "closed_pnl": [
                    {
                        "orderId": "ord-1",
                        "symbol": sym,
                        "side": "Sell",
                        "qty": "1",
                        "closedSize": "1",
                        "avgEntryPrice": "1000",
                        "avgExitPrice": "1500",
                        "cumEntryValue": "1000",
                        "cumExitValue": "1500",
                        "openFee": "0.5",
                        "closeFee": "0.5",
                        "closedPnl": pnl,
                        "createdTime": "1732200000000",
                        "updatedTime": "1732203600000",
                    }
                ],
                "executions": [],
                "fee_rates": [],
                "tx_logs": [],
                "deposits": [{"id": "d1", "coin": "USDT", "amount": dep, "successAt": "1732100000000"}],
                "internal_deposits": [],
                "withdrawals": [],
                "tickers": {},
                "observed_rebate_ratio": 0.0,
                "warnings": [],
            }

        async def aclose(self) -> None:
            pass

    byb = reg.get("bybit")
    assert byb is not None
    byb.create_client = MockBybitMultiClient  # type: ignore[method-assign]

    app = create_app(data_dir=tmp_path, registry=reg, auto_prompt_setup=False, enable_background_sync=False)
    http = TestClient(app)

    # 1. Connect primary Bybit Master account
    r1 = http.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "bybit",
                "label": "手工主号",
                "api_key": "master-key-1",
                "api_secret": "sec-1",
            }
        },
    )
    assert r1.status_code == 200
    assert r1.json()["account_id"] == "bybit"
    assert r1.json()["credential"]["account_role"] == "master"
    assert r1.json()["credential"]["masked_uid"] == "10***01"

    # 2. Attempting to add a SECOND key for the SAME master UID (100001) via create_new=True must be blocked (409)!
    dup_verify = http.post(
        "/api/v1/exchanges/verify",
        json={
            "venue_id": "bybit",
            "create_new": True,
            "label": "重复主号Key",
            "api_key": "master-key-2",
            "api_secret": "sec-2",
        },
    )
    assert dup_verify.status_code == 409
    assert dup_verify.json()["error_code"] == "DUPLICATE_ACCOUNT_UID"

    dup_sync = http.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "bybit",
                "create_new": True,
                "label": "重复主号Key",
                "api_key": "master-key-2",
                "api_secret": "sec-2",
            }
        },
    )
    assert dup_sync.status_code == 409
    assert dup_sync.json()["error_code"] == "DUPLICATE_ACCOUNT_UID"

    # 3. Editing primary Bybit account with a SUB-ACCOUNT key (UID 100002) must be blocked as ACCOUNT_UID_MISMATCH!
    mismatch = http.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "bybit",
                "account_id": "bybit",
                "label": "手工主号",
                "api_key": "sub-key-1",
                "api_secret": "sec-sub",
            }
        },
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error_code"] == "ACCOUNT_UID_MISMATCH"

    # 4. Adding the Sub-Account via create_new=True succeeds in its own isolated partition!
    r2 = http.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "bybit",
                "create_new": True,
                "label": "量化子号",
                "api_key": "sub-key-1",
                "api_secret": "sec-sub",
            }
        },
    )
    assert r2.status_code == 200
    sub_acct_id = r2.json()["account_id"]
    assert sub_acct_id.startswith("bybit__")
    assert r2.json()["credential"]["account_role"] == "subaccount"
    assert r2.json()["credential"]["masked_uid"] == "10***02"

    # 5. Dashboard aggregates both accounts cleanly with 0.00 U residual and distinct trade IDs
    dash = http.get("/api/v1/dashboard").json()
    assert dash["summary"]["total_equity_usd"] == 7000.00
    assert dash["summary"]["net_principal_usd"] == 6300.00
    assert dash["summary"]["whole_net_pnl_usd"] == 700.00
    assert dash["reconciliation"]["residual_usd"] == 0.0
    assert len(dash["exchanges"]) == 2
    ex_by_id = {e["venue_id"]: e for e in dash["exchanges"]}
    assert ex_by_id["bybit"]["display_name_zh"] == "Bybit · 手工主号"
    assert ex_by_id[sub_acct_id]["display_name_zh"] == "Bybit · 量化子号"
    assert ex_by_id[sub_acct_id]["color_token"] == "bybit"
    assert len({t["trade_id"] for t in dash["trades"]}) == 2

    # 6. Filtering by sub_acct_id returns ONLY the sub-account's ETH_USDT trade
    sub_dash = http.get(f"/api/v1/dashboard?venue={sub_acct_id}").json()
    assert len(sub_dash["trades"]) == 1
    assert sub_dash["trades"][0]["venue_id"] == sub_acct_id
    assert sub_dash["trades"][0]["symbol"] == "ETH_USDT"

    # 7. GET /api/v1/exchanges lists both accounts under Bybit's `accounts` array
    ex_list = http.get("/api/v1/exchanges").json()
    byb_entry = next(a for a in ex_list["adapters"] if a["venue_id"] == "bybit")
    assert len(byb_entry["accounts"]) == 2

    # 8. Unbinding the sub-account with keep_data=True archives it without affecting the master account
    del_res = http.delete(f"/api/v1/exchanges/{sub_acct_id}?keep_data=true")
    assert del_res.status_code == 200
    assert del_res.json()["kept_data"] is True

    dash_after_archive = http.get("/api/v1/dashboard").json()
    ex_after = {e["venue_id"]: e for e in dash_after_archive["exchanges"]}
    assert ex_after["bybit"]["health_status"] == "connected"
    assert ex_after[sub_acct_id]["health_status"] == "archived"
    assert dash_after_archive["summary"]["total_equity_usd"] == 7000.00

    # 9. Purging the archived sub-account removes only sub_acct_id, keeping master 5000 U intact
    purge_res = http.delete(f"/api/v1/exchanges/{sub_acct_id}?keep_data=false")
    assert purge_res.status_code == 200
    dash_after_purge = http.get("/api/v1/dashboard").json()
    assert dash_after_purge["summary"]["total_equity_usd"] == 5000.00
    assert [e["venue_id"] for e in dash_after_purge["exchanges"]] == ["bybit"]


def test_hyperliquid_wallet_subaccounts_batch_import_and_maker_rebate_accounting(tmp_path: Path) -> None:
    from opentradeledger.adapters.hyperliquid.client import HyperliquidReadOnlyClient

    master_addr = "0x" + "a" * 40
    sub_addr = "0x" + "b" * 40

    def hl_handler(req: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(req.content.decode("utf-8"))
        tp = body.get("type")
        usr = str(body.get("user") or "").lower()
        if tp == "clearinghouseState":
            eq = "3200.00" if usr == master_addr else "1500.00"
            return httpx.Response(
                200,
                json={
                    "marginSummary": {"accountValue": eq, "withdrawable": eq},
                    "assetPositions": [],
                },
            )
        if tp == "spotClearinghouseState":
            return httpx.Response(200, json={"balances": [{"coin": "HYPE", "total": "10"}] if usr == master_addr else []})
        if tp == "allMids":
            return httpx.Response(200, json={"HYPE": "25.0", "ETH": "3000.0"})
        if tp == "subAccounts":
            if usr == master_addr:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "name": "QuantSub1",
                            "subAccountUser": sub_addr,
                            "master": master_addr,
                            "clearinghouseState": {"marginSummary": {"accountValue": "1500.00"}},
                        }
                    ],
                )
            return httpx.Response(200, json=[])
        if tp in {"userFills", "userFillsByTime"}:
            if usr == master_addr:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "tid": 101,
                            "oid": 1,
                            "coin": "ETH",
                            "px": "3000.0",
                            "sz": "1.0",
                            "side": "B",
                            "dir": "Open Long",
                            "closedPnl": "0.0",
                            "fee": "-0.30",
                            "crossed": False,
                            "time": 1732200000000,
                        },
                        {
                            "tid": 102,
                            "oid": 2,
                            "coin": "ETH",
                            "px": "3200.0",
                            "sz": "1.0",
                            "side": "A",
                            "dir": "Close Long",
                            "closedPnl": "200.0",
                            "fee": "1.12",
                            "crossed": True,
                            "time": 1732203600000,
                        },
                    ],
                )
            return httpx.Response(200, json=[])
        if tp == "userFunding":
            return httpx.Response(200, json=[])
        if tp == "userNonFundingLedgerUpdates":
            dep = "3000.00" if usr == master_addr else "1500.00"
            return httpx.Response(
                200,
                json=[{"hash": f"0xh_{usr[:6]}", "time": 1732100000000, "delta": {"type": "deposit", "usdc": dep}}],
            )
        return httpx.Response(200, json=[])

    reg = build_default_registry()
    http_transport = httpx.MockTransport(hl_handler)
    hl_ad = reg.get("hyperliquid")
    assert hl_ad is not None
    hl_ad.create_client = lambda cred: HyperliquidReadOnlyClient(  # type: ignore[method-assign]
        wallet_address=cred.api_key,
        client=httpx.AsyncClient(transport=http_transport),
    )

    # Guard must block /exchange write path
    async def _check_guard() -> None:
        async with httpx.AsyncClient(transport=http_transport) as c:
            cl = HyperliquidReadOnlyClient(wallet_address=master_addr, client=c)
            with pytest.raises(ReadOnlyViolationError):
                await cl.request_info({"type": "clearinghouseState"}, path="/exchange")

    asyncio.run(_check_guard())

    app = create_app(data_dir=tmp_path, registry=reg, auto_prompt_setup=False, enable_background_sync=False)
    client = TestClient(app)

    # Reject invalid wallet address
    bad = client.post("/api/v1/exchanges/verify", json={"venue_id": "hyperliquid", "wallet_address": "not-an-address"})
    assert bad.status_code == 400

    # Verify returns discovered on-chain subaccounts
    ver = client.post("/api/v1/exchanges/verify", json={"venue_id": "hyperliquid", "wallet_address": master_addr})
    assert ver.status_code == 200
    subs = ver.json()["subaccounts"]
    assert len(subs) == 1
    assert subs[0]["name"] == "QuantSub1"
    assert subs[0]["address"] == sub_addr

    # Connect master + include discovered sub-account in one ceremony
    commit = client.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "hyperliquid",
                "label": "HL主钱包",
                "wallet_address": master_addr,
                "include_subaccounts": subs,
            }
        },
    )
    assert commit.status_code == 200
    assert len(commit.json()["added_subaccounts"]) == 1

    dash = client.get("/api/v1/dashboard").json()
    # Master: 3200 perp + 10*25=250 spot = 3450; Sub: 1500 perp => Total = 4950.00!
    assert dash["summary"]["total_equity_usd"] == 4950.00
    assert dash["summary"]["net_principal_usd"] == 4500.00
    assert dash["reconciliation"]["residual_usd"] == 0.0
    assert len(dash["trades"]) == 1
    tr = dash["trades"][0]
    assert tr["symbol"] == "ETH_USDC"
    assert tr["entry_leg"]["role"] == "MAKER"
    assert tr["entry_leg"]["rebate_usd"] == 0.3
    assert tr["exit_leg"]["role"] == "TAKER"
    assert tr["exit_leg"]["gross_fee_usd"] == 1.12


def test_kucoin_bingx_and_htx_signatures_guards_and_closed_loop_ledgers(tmp_path: Path) -> None:
    from opentradeledger.adapters.bingx.client import BingxReadOnlyClient, normalize_bingx_closed_trades
    from opentradeledger.adapters.htx.client import HtxReadOnlyClient, normalize_htx_closed_trades
    from opentradeledger.adapters.kucoin.client import KucoinReadOnlyClient, normalize_kucoin_closed_trades

    async def _verify_clients() -> None:
        # 1. KuCoin
        def kc_handler(req: httpx.Request) -> httpx.Response:
            assert req.headers.get("KC-API-KEY") == "kc_k"
            assert req.headers.get("KC-API-PASSPHRASE")
            assert req.headers.get("KC-API-SIGN")
            return httpx.Response(200, json={"code": "200000", "data": {"accountEquity": 2500.0, "availableBalance": 2000.0}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(kc_handler)) as hc:
            kc = KucoinReadOnlyClient(api_key="kc_k", api_secret="kc_s", passphrase="kc_p", client=hc)
            ov = await kc.get_futures_overview()
            assert ov["accountEquity"] == 2500.0
            with pytest.raises(ReadOnlyViolationError):
                await kc.request_readonly("POST", "/api/v1/orders", futures=True)

        # 2. BingX
        def bx_handler(req: httpx.Request) -> httpx.Response:
            assert req.headers.get("X-BX-APIKEY") == "bx_k"
            assert "signature=" in str(req.url)
            if req.url.path == "/openApi/account/v1/uid":
                return httpx.Response(200, json={"code": 0, "data": {"uid": 88776655}})
            return httpx.Response(200, json={"code": 0, "data": [{"asset": "USDT", "equity": "1800.0", "availableMargin": "1500.0"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(bx_handler)) as hc:
            bx = BingxReadOnlyClient(api_key="bx_k", api_secret="bx_s", client=hc)
            assert await bx.get_uid() == "88776655"
            bals = await bx.get_swap_balance()
            assert float(bals[0]["equity"]) == 1800.0
            with pytest.raises(ReadOnlyViolationError):
                await bx.request_readonly("POST", "/openApi/swap/v2/trade/order")

        # 3. HTX
        def htx_handler(req: httpx.Request) -> httpx.Response:
            assert "Signature=" in str(req.url)
            if req.url.path == "/v2/user/uid":
                return httpx.Response(200, json={"code": 200, "data": 55443322})
            if req.url.path == "/v2/user/api-key":
                return httpx.Response(200, json={"code": 200, "data": [{"parentId": 11000000}]})
            return httpx.Response(200, json={"status": "ok", "data": [{"margin_balance": "3100.0", "margin_available": "2800.0"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(htx_handler)) as hc:
            htx = HtxReadOnlyClient(api_key="htx_k", api_secret="htx_s", client=hc)
            ident = await htx.get_identity()
            assert ident == {"remote_uid": "55443322", "account_role": "subaccount"}
            sw = await htx.get_swap_accounts()
            assert float(sw[0]["margin_balance"]) == 3100.0
            with pytest.raises(ReadOnlyViolationError):
                await htx.swap_query("/linear-swap-api/v1/swap_cross_order", {"contract_code": "BTC-USDT"})

    asyncio.run(_verify_clients())

    # Verify closed-loop accounting across KuCoin, BingX, HTX in LedgerService
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    store.upsert_plugin_snapshot(
        "kucoin",
        {
            "futures_overview": {"accountEquity": 2500.0, "availableBalance": 2200.0},
            "spot_accounts": [{"type": "main", "currency": "USDT", "balance": "500.0"}],
            "deposits": [{"walletTxId": "k1", "currency": "USDT", "amount": "2800.0", "createdAt": 1732100000000}],
            "withdrawals": [],
            "positions": [],
            "history_positions": [
                {
                    "closeId": "kp1",
                    "symbol": "XBTUSDTM",
                    "side": "long",
                    "closeSize": 100,
                    "multiplier": 0.001,
                    "openPrice": 90000,
                    "closePrice": 92000,
                    "openTimestamp": 1732200000000,
                    "closeTimestamp": 1732203600000,
                    "tradeFee": 3.6,
                    "fundingFee": 0.0,
                    "pnl": 196.4,
                }
            ],
            "fills": [
                {"tradeId": "kf1", "symbol": "XBTUSDTM", "side": "buy", "fee": "1.6", "liquidity": "maker", "createdAt": 1732200001000},
                {"tradeId": "kf2", "symbol": "XBTUSDTM", "side": "sell", "fee": "2.0", "liquidity": "taker", "createdAt": 1732203599000},
            ],
            "tx_history": [],
            "tickers": {},
            "base_fee": {"makerFeeRate": "0.0002", "takerFeeRate": "0.0006"},
        },
    )
    store.upsert_plugin_snapshot(
        "bingx",
        {
            "swap_balances": [{"asset": "USDT", "equity": "1800.0", "availableMargin": "1800.0"}],
            "spot_balances": [],
            "deposits": [{"id": "b1", "coin": "USDT", "amount": "1700.0", "insertTime": 1732100000000}],
            "withdrawals": [],
            "positions": [],
            "fills": [
                {"tradeId": "bf1", "symbol": "SOL-USDT", "side": "BUY", "positionSide": "LONG", "price": "200", "qty": "10", "commission": "-0.4", "realizedPnl": "0", "role": "MAKER", "filledTm": 1732200000000},
                {"tradeId": "bf2", "symbol": "SOL-USDT", "side": "SELL", "positionSide": "LONG", "price": "210.1", "qty": "10", "commission": "-0.6", "realizedPnl": "101.0", "role": "TAKER", "filledTm": 1732203600000},
            ],
            "income": [],
            "tickers": {},
            "commission": {"makerCommissionRate": "0.0002", "takerCommissionRate": "0.0005"},
        },
    )
    store.upsert_plugin_snapshot(
        "htx",
        {
            "swap_accounts": [{"margin_balance": "3100.0", "margin_available": "3100.0"}],
            "spot_balances": [],
            "deposits": [{"id": "h1", "currency": "usdt", "amount": "3000.0", "state": "safe", "created-at": 1732100000000}],
            "withdrawals": [],
            "positions": [],
            "matches": [
                {"match_id": "hm1", "contract_code": "ETH-USDT", "direction": "buy", "offset": "open", "trade_price": "3000", "trade_turnover": "3000", "trade_volume": "100", "trade_fee": "-0.6", "offset_profitloss": "0", "role": "maker", "create_date": 1732200000000},
                {"match_id": "hm2", "contract_code": "ETH-USDT", "direction": "sell", "offset": "close", "trade_price": "3101.5", "trade_turnover": "3101.5", "trade_volume": "100", "trade_fee": "-0.9", "offset_profitloss": "101.5", "role": "taker", "create_date": 1732203600000},
            ],
            "financials": [],
            "tickers": {},
        },
    )

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    assert dash["summary"]["total_equity_usd"] == 7900.00  # 3000 + 1800 + 3100
    assert dash["summary"]["net_principal_usd"] == 7500.00  # 2800 + 1700 + 3000
    assert dash["summary"]["whole_net_pnl_usd"] == 400.00
    assert dash["reconciliation"]["residual_usd"] == 0.0
    assert len(dash["trades"]) == 3
    assert {t["venue_id"] for t in dash["trades"]} == {"kucoin", "bingx", "htx"}
