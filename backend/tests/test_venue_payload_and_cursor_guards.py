"""Partial spot 503 snapshot guards, Hyperliquid spotMeta pricing/subaccount reports, and KuCoin/BingX/HTX strict contract pagination tests."""

from __future__ import annotations

import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import httpx

from opentradeledger.adapters.bingx.client import BingxReadOnlyClient
from opentradeledger.adapters.htx.client import HtxReadOnlyClient
from opentradeledger.adapters.hyperliquid.client import HyperliquidReadOnlyClient
from opentradeledger.adapters.kucoin.client import KucoinReadOnlyClient
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.api.app import create_app
from opentradeledger.core.store import LedgerStore


def test_partial_spot_503_never_wipes_cached_balances_across_four_new_venues(tmp_path: Path) -> None:
    spot_fail = {"on": False}
    reg = build_default_registry()
    hl_addr = "0x" + "1" * 40
    now_ms = int(time.time() * 1000)

    def hl_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        tp = body.get("type")
        if tp == "clearinghouseState":
            return httpx.Response(200, json={"marginSummary": {"accountValue": "100.0", "withdrawable": "100.0"}, "assetPositions": []})
        if tp == "spotClearinghouseState":
            if spot_fail["on"]:
                return httpx.Response(503, json={"msg": "spot unavailable"})
            return httpx.Response(200, json={"balances": [{"coin": "USDC", "token": 0, "total": "20.0"}]})
        if tp == "allMids":
            return httpx.Response(200, json={"BTC": "95000.0"})
        if tp == "spotMeta":
            return httpx.Response(200, json={"tokens": [{"name": "USDC", "index": 0}], "universe": []})
        if tp == "userNonFundingLedgerUpdates":
            return httpx.Response(200, json=[{"hash": "0x1", "time": now_ms - 10_000, "delta": {"type": "deposit", "usdc": "120.0"}}])
        return httpx.Response(200, json=[])

    def kc_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/v1/accounts":
            if spot_fail["on"]:
                return httpx.Response(503, json={"msg": "spot unavailable"})
            return httpx.Response(200, json={"code": "200000", "data": [{"type": "main", "currency": "USDT", "balance": "20.0"}]})
        if p == "/api/v1/account-overview":
            return httpx.Response(200, json={"code": "200000", "data": {"accountEquity": 100.0, "availableBalance": 100.0}})
        if p == "/api/v1/user/api-key":
            return httpx.Response(200, json={"code": "200000", "data": {"uid": 6001, "isMaster": True, "permission": "General"}})
        if p == "/api/v1/deposits":
            return httpx.Response(200, json={"code": "200000", "data": {"items": [{"walletTxId": "kd1", "currency": "USDT", "amount": "120.0", "createdAt": now_ms}]}})
        return httpx.Response(200, json={"code": "200000", "data": {"items": [], "dataList": [], "ticker": []}})

    def bx_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/openApi/spot/v1/account/balance":
            if spot_fail["on"]:
                return httpx.Response(503, json={"msg": "spot unavailable"})
            return httpx.Response(200, json={"code": 0, "data": {"balances": [{"asset": "USDT", "free": "20.0", "locked": "0"}]}})
        if p == "/openApi/swap/v3/user/balance":
            return httpx.Response(200, json={"code": 0, "data": [{"asset": "USDT", "equity": "100.0", "availableMargin": "100.0"}]})
        if p == "/openApi/account/v1/uid":
            return httpx.Response(200, json={"code": 0, "data": {"uid": 7001}})
        if p == "/openApi/wallets/v1/capital/deposit/hisrec":
            return httpx.Response(200, json={"code": 0, "data": [{"id": "bd1", "coin": "USDT", "amount": "120.0", "status": 6, "insertTime": now_ms}]})
        return httpx.Response(200, json={"code": 0, "data": []})

    def htx_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/v1/account/accounts":
            if spot_fail["on"]:
                return httpx.Response(503, json={"msg": "spot unavailable"})
            return httpx.Response(200, json={"status": "ok", "data": [{"id": 4, "type": "spot"}]})
        if p == "/v1/account/accounts/4/balance":
            return httpx.Response(200, json={"status": "ok", "data": {"list": [{"currency": "usdt", "balance": "20.0"}]}})
        if p == "/linear-swap-api/v1/swap_cross_account_info":
            return httpx.Response(200, json={"status": "ok", "data": [{"margin_balance": "100.0", "margin_available": "100.0"}]})
        if p == "/v2/user/uid":
            return httpx.Response(200, json={"code": 200, "data": 8001})
        if p == "/v1/query/deposit-withdraw":
            if req.url.params.get("type") == "deposit":
                return httpx.Response(200, json={"status": "ok", "data": [{"id": "hd1", "currency": "usdt", "amount": "120.0", "state": "safe", "created-at": now_ms}]})
            return httpx.Response(200, json={"status": "ok", "data": []})
        return httpx.Response(200, json={"status": "ok", "data": []})

    reg.get("hyperliquid").create_client = lambda cred: HyperliquidReadOnlyClient(wallet_address=cred.api_key, client=httpx.AsyncClient(transport=httpx.MockTransport(hl_handler)))  # type: ignore[union-attr, method-assign]
    reg.get("kucoin").create_client = lambda cred: KucoinReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret, passphrase=cred.passphrase, client=httpx.AsyncClient(transport=httpx.MockTransport(kc_handler)))  # type: ignore[union-attr, method-assign]
    reg.get("bingx").create_client = lambda cred: BingxReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret, client=httpx.AsyncClient(transport=httpx.MockTransport(bx_handler)))  # type: ignore[union-attr, method-assign]
    reg.get("htx").create_client = lambda cred: HtxReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret, client=httpx.AsyncClient(transport=httpx.MockTransport(htx_handler)))  # type: ignore[union-attr, method-assign]

    http = TestClient(create_app(data_dir=tmp_path, registry=reg, auto_prompt_setup=False, enable_background_sync=False))
    http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "hyperliquid", "wallet_address": hl_addr}}).raise_for_status()
    http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "kucoin", "api_key": "k", "api_secret": "s", "passphrase": "p"}}).raise_for_status()
    http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "bingx", "api_key": "k", "api_secret": "s"}}).raise_for_status()
    http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "htx", "api_key": "k", "api_secret": "s"}}).raise_for_status()

    dash_ok = http.get("/api/v1/dashboard").json()
    assert dash_ok["summary"]["total_equity_usd"] == 480.00

    # Turn on Spot 503 across all 4 exchanges!
    spot_fail["on"] = True
    partial_res = http.post("/api/v1/sync", json={"force_all": True}).json()
    for vid in ("hyperliquid", "kucoin", "bingx", "htx"):
        assert partial_res["venues"][vid]["ok"] is False

    # Total equity MUST remain 480.00 U (never dropping to 400.00 U!) and health reflects error!
    dash_during_outage = http.get("/api/v1/dashboard").json()
    assert dash_during_outage["summary"]["total_equity_usd"] == 480.00
    assert all(ex["health_status"] == "error" for ex in dash_during_outage["exchanges"])


def test_hyperliquid_spot_meta_pricing_cap_warnings_and_partial_subaccount_report(tmp_path: Path) -> None:
    master_addr = "0x" + "1" * 40
    ok_sub = "0x" + "2" * 40
    fail_sub = "0x" + "3" * 40

    def hl_mainnet_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        tp = body.get("type")
        usr = str(body.get("user") or "").lower()
        if usr == fail_sub:
            return httpx.Response(503, json={"msg": "subaccount upstream error"})
        if tp == "clearinghouseState":
            val = "100.0" if usr == master_addr else "600.0"
            return httpx.Response(200, json={"marginSummary": {"accountValue": val, "withdrawable": val}, "assetPositions": []})
        if tp == "spotClearinghouseState":
            if usr == master_addr:
                return httpx.Response(
                    200,
                    json={
                        "balances": [
                            {"coin": "USDC", "token": 0, "total": "20.0"},
                            {"coin": "PURR", "token": 1, "total": "3.0"},
                            {"coin": "TRUMP", "token": 10, "total": "8932.1298151"},
                            {"coin": "ILIENS", "token": 15, "total": "100.0"},
                        ]
                    },
                )
            return httpx.Response(200, json={"balances": []})
        if tp == "spotMeta":
            return httpx.Response(
                200,
                json={
                    "tokens": [
                        {"name": "USDC", "index": 0},
                        {"name": "PURR", "index": 1},
                        {"name": "TRUMP", "index": 10},
                        {"name": "ILIENS", "index": 15},
                    ],
                    "universe": [
                        {"tokens": [1, 0], "name": "PURR/USDC", "index": 0},
                        {"tokens": [10, 0], "name": "@9", "index": 9},
                    ],
                },
            )
        if tp == "allMids":
            # TRUMP perp is 0.90755, while TRUMP spot (@9) is 0.000694!
            return httpx.Response(200, json={"TRUMP": "0.90755", "@9": "0.000694", "PURR/USDC": "2.0"})
        if tp == "userFills":
            if usr == master_addr:
                return httpx.Response(
                    200,
                    json=[
                        {"tid": i + 1, "oid": i + 1, "coin": "ETH", "px": "3000", "sz": "0.01", "side": "B", "dir": "Open Long", "closedPnl": "0", "fee": "0", "time": 1732200000000 + i}
                        for i in range(2000)
                    ],
                )
            return httpx.Response(200, json=[])
        if tp == "userNonFundingLedgerUpdates":
            dep = "120.0" if usr == master_addr else "600.0"
            return httpx.Response(200, json=[{"hash": f"0x{usr[:8]}", "time": 1732100000000, "delta": {"type": "deposit", "usdc": dep}}])
        return httpx.Response(200, json=[])

    reg = build_default_registry()
    reg.get("hyperliquid").create_client = lambda cred: HyperliquidReadOnlyClient(  # type: ignore[union-attr, method-assign]
        wallet_address=cred.api_key,
        client=httpx.AsyncClient(transport=httpx.MockTransport(hl_mainnet_handler)),
    )
    http = TestClient(create_app(data_dir=tmp_path, registry=reg, auto_prompt_setup=False, enable_background_sync=False))

    res = http.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "hyperliquid",
                "label": "HL主地址",
                "wallet_address": master_addr,
                "include_subaccounts": [
                    {"name": "Sub600", "address": ok_sub},
                    {"name": "Sub900", "address": fail_sub},
                ],
            }
        },
    ).json()
    assert len(res["added_subaccounts"]) == 1
    assert len(res["failed_subaccounts"]) == 1
    assert res["failed_subaccounts"][0]["address"] == fail_sub

    # Master equity: 100 perp + 20 USDC + 3*2.0 PURR (6.0) + 8932.1298151*0.000694 TRUMP (6.20) + 0 ILIENS = 132.20 U!
    # Plus Sub600 = 600.00 U -> Total = 732.20 U (NOT 8,106.35 U from TRUMP perp!)
    dash = http.get("/api/v1/dashboard").json()
    assert dash["summary"]["total_equity_usd"] == 732.20
    wb = {
        (w["venue_id"], w["asset"], w["wallet_type"]): w
        for w in dash["spot_wallet_balances"]
    }
    assert round(wb[("hyperliquid", "TRUMP", "SPOT")]["market_value_usd"], 2) == 6.20
    assert round(wb[("hyperliquid", "PURR", "SPOT")]["market_value_usd"], 2) == 6.00
    assert wb[("hyperliquid", "ILIENS", "SPOT")]["market_value_usd"] is None

    snap = LedgerStore(tmp_path).venue_scope("hyperliquid").get_kv("snapshot")
    assert any("2,000" in w for w in snap["warnings"])


def test_kucoin_bingx_htx_strict_contracts_pagination_status_and_rebates(tmp_path: Path) -> None:
    now_ms = int(time.time() * 1000)

    # 1. KuCoin: 60 positions via pageId/limit, Classic openTime/closeTime + inferred qty, /api/v1/user/api-key UID, and Key v3!
    def kc_strict_handler(req: httpx.Request) -> httpx.Response:
        # Require KC-API-KEY-VERSION == "3" to verify v2->v3 auto-negotiation!
        if req.url.path != "/api/v1/market/allTickers" and req.headers.get("KC-API-KEY-VERSION") != "3":
            return httpx.Response(200, json={"code": "400009", "msg": "fixture key version mismatch"})
        p = req.url.path
        if p == "/api/v1/user/api-key":
            return httpx.Response(200, json={"code": "200000", "data": {"uid": 556677, "isMaster": True, "permission": "General"}})
        if p == "/api/v2/user-info":
            return httpx.Response(200, json={"code": "200000", "data": {"level": 1, "subQuantity": 0}})
        if p == "/api/v1/accounts":
            return httpx.Response(200, json={"code": "200000", "data": [{"type": "main", "currency": "USDT", "balance": "20.0"}]})
        if p == "/api/v1/account-overview":
            return httpx.Response(200, json={"code": "200000", "data": {"accountEquity": 100.0, "availableBalance": 100.0}})
        if p == "/api/v1/history-positions":
            # Strictly paginate ONLY by pageId (defaulting to page 1 if pageId missing!)
            page_id = int(req.url.params.get("pageId") or 1)
            all_60 = [
                {
                    "closeId": f"cid-{i}",
                    "symbol": "XBTUSDTM",
                    "type": "Close long",
                    "openPrice": "93859.8",
                    "closePrice": "94443.5",
                    "realisedGrossPnl": "0.5837",
                    "realisedPnl": "0.51214413",
                    "tradeFee": "0.03766066",
                    "fundingFee": "-0.03389521",
                    "openTime": now_ms - 3600_000 - i * 1000,
                    "closeTime": now_ms - 1800_000 - i * 1000,
                }
                for i in range(60)
            ]
            chunk = all_60[(page_id - 1) * 10 : page_id * 10]
            return httpx.Response(200, json={"code": "200000", "data": {"items": chunk, "totalPage": 6}})
        if p == "/api/v1/fills":
            return httpx.Response(
                200,
                json={
                    "code": "200000",
                    "data": {
                        "items": [
                            {"tradeId": "f1", "symbol": "XBTUSDTM", "side": "buy", "fee": "0.01766066", "liquidity": "maker", "createdAt": now_ms - 3600_000},
                            {"tradeId": "f2", "symbol": "XBTUSDTM", "side": "sell", "fee": "0.02000000", "liquidity": "taker", "createdAt": now_ms - 1800_000},
                        ]
                    },
                },
            )
        if p == "/api/v1/deposits":
            return httpx.Response(200, json={"code": "200000", "data": {"items": [{"walletTxId": "kd1", "currency": "USDT", "amount": "89.27", "createdAt": now_ms}]}})
        return httpx.Response(200, json={"code": "200000", "data": {"items": [], "dataList": [], "ticker": []}})

    # 2. BingX: strict tradingUnit/startTs/endTs, ISO filledTm, and deposit/withdrawal status filtering (111 U)!
    iso_open = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((now_ms - 600_000) / 1000.0))
    iso_close = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((now_ms - 300_000) / 1000.0))

    def bx_strict_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/openApi/swap/v2/trade/allFillOrders":
            for req_p in ("tradingUnit", "startTs", "endTs"):
                if req_p not in req.url.params:
                    return httpx.Response(200, json={"code": 100400, "msg": f"parameter missing: {req_p}"})
            s_ts = int(req.url.params["startTs"])
            e_ts = int(req.url.params["endTs"])
            if not (s_ts <= now_ms - 300_000 <= e_ts):
                return httpx.Response(200, json={"code": 0, "data": {"fill_orders": []}})
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "fill_orders": [
                            {"tradeId": "bx1", "orderId": "11", "symbol": "BTC-USDT", "side": "BUY", "positionSide": "LONG", "price": "100", "qty": "1", "commission": "-0.3", "realizedPnl": "0", "role": "MAKER", "filledTm": iso_open},
                            {"tradeId": "bx2", "orderId": "12", "symbol": "BTC-USDT", "side": "SELL", "positionSide": "LONG", "price": "110", "qty": "1", "commission": "-0.7", "realizedPnl": "10", "role": "TAKER", "filledTm": iso_close},
                        ]
                    },
                },
            )
        if p == "/openApi/swap/v3/user/balance":
            return httpx.Response(200, json={"code": 0, "data": [{"asset": "USDT", "equity": "100.0", "availableMargin": "100.0"}]})
        if p == "/openApi/spot/v1/account/balance":
            return httpx.Response(200, json={"code": 0, "data": {"balances": [{"asset": "USDT", "free": "20.0", "locked": "0"}]}})
        if p == "/openApi/account/v1/uid":
            return httpx.Response(200, json={"code": 0, "data": {"uid": 75000123}})
        if p == "/openApi/wallets/v1/capital/deposit/hisrec":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {"id": "dep-ok", "coin": "USDT", "amount": "111.0", "status": 6, "insertTime": now_ms},
                        {"id": "dep-uncredited", "coin": "USDT", "amount": "31.0", "status": 1, "insertTime": now_ms},
                        {"id": "dep-pending", "coin": "USDT", "amount": "20.0", "status": 0, "insertTime": now_ms},
                    ],
                },
            )
        if p == "/openApi/wallets/v1/capital/withdraw/history":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {"id": "wd-failed", "coin": "USDT", "amount": "50.0", "transactionFee": "1.0", "status": 5, "applyTime": now_ms},
                    ],
                },
            )
        return httpx.Response(200, json={"code": 0, "data": []})

    # 3. HTX: strict cross-margin matchresults (no contract="ALL", size<=50), real_profit=10 vs offset_profitloss=7,
    # type 29 rebate (0.2) vs type 9 delivery (0.5 ignored), colliding match_id with distinct id, and 403 api-key role=""!
    def htx_strict_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/v2/user/uid":
            return httpx.Response(200, json={"code": 200, "data": 75000321})
        if p == "/v2/user/api-key":
            return httpx.Response(403, json={"code": 403, "message": "forbidden for sub-user"})
        if p == "/linear-swap-api/v1/swap_cross_account_info":
            return httpx.Response(200, json={"status": "ok", "data": [{"margin_balance": "120.0", "margin_available": "120.0"}]})
        if p == "/v1/account/accounts":
            return httpx.Response(200, json={"status": "ok", "data": []})
        if p == "/linear-swap-api/v1/swap_matchresults_exact":
            return httpx.Response(200, json={"status": "error", "err_code": 1010, "err_msg": "isolated endpoint rejected"})
        if p == "/linear-swap-api/v1/swap_cross_matchresults_exact":
            body = json.loads(req.content.decode("utf-8"))
            if body.get("contract") == "ALL" or int(body.get("size") or 0) > 50:
                return httpx.Response(200, json={"status": "error", "err_code": 1010, "err_msg": "invalid request parameters"})
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "data": {
                        "trades": [
                            {"id": 10, "match_id": 1, "contract_code": "BTC-USDT", "direction": "buy", "offset": "open", "trade_price": "100", "trade_turnover": "200", "trade_volume": "200", "trade_fee": "0.1", "offset_profitloss": "0", "real_profit": "0", "role": "maker", "create_date": now_ms - 600_000},
                            {"id": 11, "match_id": 2, "contract_code": "BTC-USDT", "direction": "sell", "offset": "close", "trade_price": "110", "trade_turnover": "110", "trade_volume": "100", "trade_fee": "-0.25", "offset_profitloss": "3.5", "real_profit": "5.0", "role": "taker", "create_date": now_ms - 300_000},
                            {"id": 12, "match_id": 2, "contract_code": "BTC-USDT", "direction": "sell", "offset": "close", "trade_price": "110", "trade_turnover": "110", "trade_volume": "100", "trade_fee": "-0.25", "offset_profitloss": "3.5", "real_profit": "5.0", "role": "taker", "create_date": now_ms - 300_000},
                        ]
                    },
                },
            )
        if p == "/linear-swap-api/v1/swap_financial_record_exact":
            body = json.loads(req.content.decode("utf-8"))
            if int(body.get("size") or 0) > 50:
                return httpx.Response(200, json={"status": "error", "err_code": 1010, "err_msg": "size > 50"})
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "data": {
                        "financial_record": [
                            {"id": "fr29", "type": 29, "amount": "0.2", "contract_code": "BTC-USDT", "ts": now_ms - 290_000},
                            {"id": "fr9", "type": 9, "amount": "0.5", "contract_code": "BTC-USDT", "ts": now_ms - 280_000},
                        ]
                    },
                },
            )
        if p == "/v1/query/deposit-withdraw":
            if req.url.params.get("type") == "deposit":
                return httpx.Response(200, json={"status": "ok", "data": [{"id": "hd1", "currency": "usdt", "amount": "110.2", "state": "safe", "created-at": now_ms}]})
            return httpx.Response(200, json={"status": "ok", "data": []})
        return httpx.Response(200, json={"status": "ok", "data": []})

    reg = build_default_registry()
    reg.get("kucoin").create_client = lambda cred: KucoinReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret, passphrase=cred.passphrase, client=httpx.AsyncClient(transport=httpx.MockTransport(kc_strict_handler)))  # type: ignore[union-attr, method-assign]
    reg.get("bingx").create_client = lambda cred: BingxReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret, client=httpx.AsyncClient(transport=httpx.MockTransport(bx_strict_handler)), clock_ms=lambda: now_ms)  # type: ignore[union-attr, method-assign]
    reg.get("htx").create_client = lambda cred: HtxReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret, client=httpx.AsyncClient(transport=httpx.MockTransport(htx_strict_handler)))  # type: ignore[union-attr, method-assign]

    http = TestClient(create_app(data_dir=tmp_path, registry=reg, auto_prompt_setup=False, enable_background_sync=False))

    # KuCoin v3 key verify + commit + duplicate UID guard
    kc_ver = http.post("/api/v1/exchanges/verify", json={"venue_id": "kucoin", "api_key": "k1", "api_secret": "s1", "passphrase": "p1"})
    assert kc_ver.status_code == 200
    assert kc_ver.json()["masked_uid"] != ""
    http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "kucoin", "api_key": "k1", "api_secret": "s1", "passphrase": "p1"}}).raise_for_status()
    kc_dup = http.post("/api/v1/exchanges/verify", json={"venue_id": "kucoin", "create_new": True, "api_key": "k2-same-uid", "api_secret": "s2", "passphrase": "p2"})
    assert kc_dup.status_code == 409
    assert kc_dup.json()["error_code"] == "DUPLICATE_ACCOUNT_UID"

    # BingX strict commit
    http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "bingx", "api_key": "bx1", "api_secret": "s"}}).raise_for_status()

    # HTX strict commit + re-sync collision check
    htx_commit = http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "htx", "api_key": "htx1", "api_secret": "s"}})
    assert htx_commit.status_code == 200
    assert htx_commit.json()["credential"]["account_role"] == ""  # NOT falsely "master" when /v2/user/api-key returned 403!
    http.post("/api/v1/sync", json={"venue_id": "htx", "force_all": True}).raise_for_status()

    # Verify 7d dashboard includes all 60 KuCoin trades + 1 BingX trade + 2 HTX trades = 63 unique trades!
    dash_7d = http.get("/api/v1/dashboard?range=7d")
    assert dash_7d.status_code == 200
    d7 = dash_7d.json()
    kc_trades = [t for t in d7["trades"] if t["venue_id"] == "kucoin"]
    bx_trades = [t for t in d7["trades"] if t["venue_id"] == "bingx"]
    htx_trades = [t for t in d7["trades"] if t["venue_id"] == "htx"]

    assert len(kc_trades) == 60
    assert len({t["trade_id"] for t in kc_trades}) == 60
    kc0 = next(t for t in kc_trades if "cid-0" in t["trade_id"])
    assert kc0["qty"] == 0.001
    assert kc0["entry_leg"]["notional_usd"] == 93.86
    assert kc0["exit_leg"]["notional_usd"] == 94.44
    assert kc0["entry_leg"]["role"] == "MAKER"
    assert kc0["exit_leg"]["role"] == "TAKER"

    assert len(bx_trades) == 1
    bx_card = next(e for e in d7["exchanges"] if e["venue_id"] == "bingx")
    assert bx_card["net_principal_usd"] == 111.00
    assert bx_card["net_pnl_usd"] == 9.00

    assert len(htx_trades) == 2
    assert len({t["trade_id"] for t in htx_trades}) == 2
    assert round(sum(t["gross_pnl_usd"] for t in htx_trades), 2) == 10.00  # 5.0 + 5.0 from real_profit (not 7.0 from offset_profitloss)
    assert round(sum(t["total_rebate_usd"] for t in htx_trades), 2) == 0.30  # 0.1 maker + 0.2 type-29 rebate (type-9 delivery excluded)
    assert round(sum(t["net_pnl_usd"] for t in htx_trades), 2) == 9.80  # 10.0 - 0.50 + 0.30 = 9.80
    assert d7["reconciliation"]["residual_usd"] == 0.0
