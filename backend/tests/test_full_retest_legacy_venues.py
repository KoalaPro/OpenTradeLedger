"""Full re-test regression suite for the 7 legacy exchange adapters:
`easicoin`, `bybit`, `okx`, `bitget`, `mexc`, `binance`, `gate`.

Covers:
1. Sub-endpoint 5xx / 429 / network timeout & rate-limit errors MUST raise (never silently wipe balances to 0),
   leaving pre-existing SQLite snapshots 100% intact; 401/403 optional sub-module permission errors retain prior
   non-empty fields via `_failed_fields` and surface `_warnings`.
2. Deposit / withdrawal status filtering (Bybit, MEXC) & multi-currency principal valuation (Gate, EasiCoin).
3. Cursor / rolling-window pagination fixes (OKX `ts` cursor, Bybit 30d/1y asset windows + 52w trade windows,
   Binance `userTrades`/`allOrders` incremental 7d windows + saturated full-sync recent-tail catchup).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import httpx
import pytest

from opentradeledger.adapters.binance.client import BinanceApiError, BinanceReadOnlyClient
from opentradeledger.adapters.binance.sync import sync_binance_venue
from opentradeledger.adapters.bitget.client import BitgetApiError, BitgetReadOnlyClient
from opentradeledger.adapters.bitget.sync import sync_bitget_venue
from opentradeledger.adapters.bybit.client import BybitApiError, BybitReadOnlyClient
from opentradeledger.adapters.compat_creds import (
    BinanceCredentials,
    BitgetCredentials,
    EasiCoinCredentials,
    GateManualCredentials,
    MexcCredentials,
)
from opentradeledger.adapters.easicoin.client import EasiCoinApiError, EasiCoinReadOnlyClient
from opentradeledger.adapters.easicoin.sync import sync_easicoin_venue
from opentradeledger.adapters.gate.client import GateApiHttpError, GateReadOnlyClient
from opentradeledger.adapters.gate.sync import sync_gate_venue
from opentradeledger.adapters.mexc.client import MexcApiError, MexcReadOnlyClient
from opentradeledger.adapters.mexc.sync import sync_mexc_venue
from opentradeledger.adapters.okx.client import OkxApiError, OkxReadOnlyClient
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


# ══════════════════════════════════════════════════════════════════════════════
# 1. Transient 5xx / 429 / Timeout Must Raise & Preserve Existing Snapshots
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_easicoin_subendpoint_503_raises_preserves_130u_spot_and_counts_all_wallets_equity(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    state = {"spot_mode": "ok"}

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/asset-api/account/private/v1/userid":
            return httpx.Response(200, json={"code": 0, "data": {"userid": "ec_u1"}})
        if p == "/futures/private/v1/account/balance":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"list": [{"coin": "USDT", "equity": "100.00", "available_balance": "100.00"}]}},
            )
        if p == "/asset-api/account/private/v1/get-funding-account-balance":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"list": [{"coin": "USDC", "total_equity": "20.00"}]}},
            )
        if p == "/spot-api/account/private/v1/balance":
            if state["spot_mode"] == "503":
                return httpx.Response(503, json={"code": 503, "msg": "Service Unavailable"})
            if state["spot_mode"] == "403":
                return httpx.Response(403, json={"code": 403, "msg": "Spot permission not enabled"})
            return httpx.Response(
                200,
                json={"code": 0, "data": [{"token_name": "USDT", "balance": "130.00"}]},
            )
        return httpx.Response(200, json={"code": 0, "data": {"list": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = EasiCoinReadOnlyClient(
            EasiCoinCredentials(api_key="k", api_secret="s"),
            client=http,
            clock_ms=lambda: 1_732_000_000_000,
        )
        # 1. Healthy sync includes futures (100) + funding (20) + spot (130) = 250 U in VenueSyncOutcome!
        out_ok = await sync_easicoin_venue(client, store, now_s=1_732_000_000)
        assert out_ok.equity_usd == pytest.approx(250.0, abs=0.01)
        assert store.venue_scope("easicoin").get_kv("live_bundle")["spot_balances"][0]["balance"] == "130.00"

        # 2. Spot returns 503 -> MUST raise instead of wiping 130 U spot balance to []!
        state["spot_mode"] = "503"
        with pytest.raises(EasiCoinApiError):
            await sync_easicoin_venue(client, store, now_s=1_732_000_060)
        saved_after_503 = store.venue_scope("easicoin").get_kv("live_bundle")
        assert saved_after_503["spot_balances"][0]["balance"] == "130.00"

        # 3. Spot returns 403 (restricted key lacking spot scope) -> retains prior non-empty 130 U + emits warning!
        state["spot_mode"] = "403"
        out_deg = await sync_easicoin_venue(client, store, now_s=1_732_000_120)
        assert len(out_deg.warnings) >= 1
        assert out_deg.equity_usd == pytest.approx(250.0, abs=0.01)
        assert store.venue_scope("easicoin").get_kv("live_bundle")["spot_balances"][0]["balance"] == "130.00"


@pytest.mark.asyncio
async def test_binance_papi_and_wallet_5xx_429_never_fallback_to_fapi_or_wipe_assets(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    state = {"papi": "ok", "wallet": "ok", "fapi_calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/v3/account":
            return httpx.Response(200, json={"uid": 8801, "balances": []})
        if p == "/sapi/v1/asset/wallet/balance":
            if state["wallet"] == "429":
                return httpx.Response(429, json={"code": -1003, "msg": "Too many requests"})
            if state["wallet"] == "403":
                return httpx.Response(403, json={"code": -2015, "msg": "No margin permission"})
            return httpx.Response(200, json=[{"walletName": "Portfolio Margin", "balance": "500.00", "activate": True}])
        if p == "/papi/v1/account":
            if state["papi"] == "502":
                return httpx.Response(502, text="Bad Gateway")
            if state["papi"] == "timeout":
                raise httpx.ReadTimeout("papi read timed out")
            return httpx.Response(200, json={"accountEquity": "500.00", "totalAvailableBalance": "500.00"})
        if p.startswith("/fapi/"):
            state["fapi_calls"] += 1
            return httpx.Response(200, json={"totalMarginBalance": "0", "availableBalance": "0", "assets": []})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BinanceReadOnlyClient(
            BinanceCredentials(api_key="k", api_secret="s"),
            client=http,
            clock_ms=lambda: 1_732_000_000_000,
        )
        out1 = await sync_binance_venue(client, store, now_s=1_732_000_000)
        assert out1.equity_usd == pytest.approx(500.0, abs=0.01)

        # PAPI 502 MUST raise immediately without touching FAPI!
        state["papi"] = "502"
        with pytest.raises(BinanceApiError):
            await sync_binance_venue(client, store, now_s=1_732_000_030)
        assert state["fapi_calls"] == 0

        # PAPI timeout MUST raise immediately without touching FAPI!
        state["papi"] = "timeout"
        with pytest.raises(BinanceApiError):
            await sync_binance_venue(client, store, now_s=1_732_000_060)
        assert state["fapi_calls"] == 0
        state["papi"] = "ok"

        # Wallet 429 (-1003) MUST raise and preserve 500 U snapshot!
        state["wallet"] = "429"
        with pytest.raises(BinanceApiError):
            await sync_binance_venue(client, store, now_s=1_732_000_090)
        assert store.venue_scope("binance").get_kv("live_bundle")["wallet_balances"][0]["balance"] == "500.00"

        # Wallet 403 permission error preserves prior non-empty wallet_balances & reports warning
        state["wallet"] = "403"
        out_deg = await sync_binance_venue(client, store, now_s=1_732_000_120)
        assert len(out_deg.warnings) >= 1
        assert store.venue_scope("binance").get_kv("live_bundle")["wallet_balances"][0]["balance"] == "500.00"


@pytest.mark.asyncio
async def test_bitget_uta_503_429_timeout_never_downgrades_to_classic(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    state = {"uta": "ok", "classic_calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/v3/account/assets":
            if state["uta"] == "503":
                return httpx.Response(503, json={"code": "50001", "msg": "Service unavailable"})
            if state["uta"] == "429_code":
                return httpx.Response(200, json={"code": "429", "msg": "Too frequent"})
            if state["uta"] == "timeout":
                raise httpx.ConnectTimeout("bitget connect timeout")
            return httpx.Response(200, json={"code": "00000", "data": {"accountEquity": "880.00", "available": "880.00"}})
        if p == "/api/v2/spot/account/info" or p == "/api/v2/account/all-account-balance":
            state["classic_calls"] += 1
            return httpx.Response(200, json={"code": "00000", "data": []})
        return httpx.Response(200, json={"code": "00000", "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BitgetReadOnlyClient(
            BitgetCredentials(api_key="k", api_secret="s", passphrase="p"),
            http_client=http,
            clock_ms=lambda: 1_732_000_000_000,
        )
        out_ok = await sync_bitget_venue(client, store, now_s=1_732_000_000)
        assert out_ok.equity_usd == pytest.approx(880.0, abs=0.01)

        for err_mode, expected_exc in (
            ("503", BitgetApiError),
            ("429_code", BitgetApiError),
            ("timeout", httpx.ConnectTimeout),
        ):
            state["uta"] = err_mode
            with pytest.raises(expected_exc):
                await sync_bitget_venue(client, store, now_s=1_732_000_060)
            assert state["classic_calls"] == 0
            assert store.venue_scope("bitget").get_kv("live_bundle")["account_mode"] == "UTA"
            assert store.venue_scope("bitget").get_kv("live_bundle")["uta_assets"]["accountEquity"] == "880.00"


@pytest.mark.asyncio
async def test_mexc_and_gate_5xx_429_timeout_raise_without_wiping_cached_bundle(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    mx_state = {"spot": "ok", "fut": "ok"}

    def mexc_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/v3/account":
            if mx_state["spot"] == "timeout":
                raise httpx.ReadTimeout("mexc spot timeout")
            if mx_state["spot"] == "403":
                return httpx.Response(403, json={"msg": "Spot scope disabled"})
            return httpx.Response(200, json={"balances": [{"asset": "USDT", "free": "200.00", "locked": "0"}]})
        if p == "/api/v1/private/account/assets":
            if mx_state["fut"] == "rate_limit":
                return httpx.Response(200, json={"success": False, "code": 510, "message": "Requests too frequent!"})
            return httpx.Response(200, json={"success": True, "code": 0, "data": [{"currency": "USDT", "equity": "300.00"}]})
        if p.startswith("/api/v1/"):
            return httpx.Response(200, json={"success": True, "code": 0, "data": []})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(mexc_handler)) as mx_http:
        mx_client = MexcReadOnlyClient(
            MexcCredentials(api_key="k", api_secret="s"),
            http_client=mx_http,
            clock_ms=lambda: 1_732_000_000_000,
        )
        out_mx = await sync_mexc_venue(mx_client, store, now_s=1_732_000_000)
        assert out_mx.equity_usd == pytest.approx(500.0, abs=0.01)

        mx_state["spot"] = "timeout"
        with pytest.raises(httpx.ReadTimeout):
            await sync_mexc_venue(mx_client, store, now_s=1_732_000_200)
        mx_state["spot"] = "ok"

        mx_state["fut"] = "rate_limit"
        with pytest.raises(MexcApiError):
            await sync_mexc_venue(mx_client, store, now_s=1_732_000_300)
        mx_state["fut"] = "ok"

        # 403 on spot retains prior 200 U spot_balances!
        mx_state["spot"] = "403"
        out_mx_deg = await sync_mexc_venue(mx_client, store, now_s=1_732_000_400)
        assert len(out_mx_deg.warnings) >= 1
        assert out_mx_deg.equity_usd == pytest.approx(500.0, abs=0.01)

    # Gate sub-endpoint 503 on optional endpoint (/copy_trading/positions or /spot/account_book) MUST raise!
    gate_state = {"copy_503": True}

    def gate_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/v4/copy_trading/positions" and gate_state["copy_503"]:
            return httpx.Response(503, json={"label": "SERVICE_UNAVAILABLE", "message": "Busy"})
        if p in ("/api/v4/wallet/total_balance", "/api/v4/unified/accounts", "/api/v4/futures/usdt/accounts"):
            return httpx.Response(200, json={"total": {"amount": "600.00"}})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(gate_handler)) as g_http:
        g_client = GateReadOnlyClient(
            GateManualCredentials(api_key="k", api_secret="s"),
            client=g_http,
            clock_sec=lambda: 1_732_000_000,
        )
        with pytest.raises(GateApiHttpError):
            await sync_gate_venue(g_client, store, now_s=1_732_000_000)


@pytest.mark.asyncio
async def test_okx_and_bybit_business_rate_limits_and_unified_fallback_guards() -> None:
    # OKX HTTP 200 code="50011" on bills-archive or optional endpoints MUST raise!
    def okx_rate_limit_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v5/account/balance":
            return httpx.Response(200, json={"code": "0", "data": [{"totalEq": "1000.0"}]})
        if req.url.path == "/api/v5/account/bills-archive":
            return httpx.Response(200, json={"code": "50011", "msg": "Rate limit reached"})
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(okx_rate_limit_handler)) as okx_http:
        okx = OkxReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=okx_http)
        with pytest.raises(OkxApiError):
            await okx.fetch_full_snapshot()

    # Bybit: UNIFIED retCode=10006 (rate limit) or 10003 (auth) MUST raise without calling CONTRACT;
    # retCode=10001 (accountType unsupported) cleanly falls back to CONTRACT;
    # positions_usdc retCode=10016 (server busy) MUST raise!
    byb_state = {"unified_code": 10006, "pos_usdc_code": 0, "contract_calls": 0}

    def bybit_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        q = dict(req.url.params)
        if p == "/v5/account/wallet-balance":
            if q.get("accountType") == "UNIFIED" and byb_state["unified_code"] != 0:
                return httpx.Response(
                    200,
                    json={"retCode": byb_state["unified_code"], "retMsg": "accountType only support CONTRACT" if byb_state["unified_code"] == 10001 else "Error"},
                )
            if q.get("accountType") == "CONTRACT":
                byb_state["contract_calls"] += 1
                return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"totalEquity": "900.00"}]}})
            return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"totalEquity": "900.00"}]}})
        if p == "/v5/position/list" and q.get("settleCoin") == "USDC" and byb_state["pos_usdc_code"] != 0:
            return httpx.Response(200, json={"retCode": byb_state["pos_usdc_code"], "retMsg": "Server busy"})
        return httpx.Response(200, json={"retCode": 0, "result": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(bybit_handler)) as byb_http:
        byb = BybitReadOnlyClient(api_key="k", api_secret="s", client=byb_http, max_windows=1)

        with pytest.raises(BybitApiError):
            await byb.fetch_full_snapshot()
        assert byb_state["contract_calls"] == 0

        byb_state["unified_code"] = 10003  # Invalid API key
        with pytest.raises(BybitApiError):
            await byb.fetch_full_snapshot()
        assert byb_state["contract_calls"] == 0

        # Classic non-UTA account (10001) falls back to CONTRACT
        byb_state["unified_code"] = 10001
        snap_classic = await byb.fetch_full_snapshot()
        assert byb_state["contract_calls"] == 1
        assert snap_classic["wallet"]["list"][0]["totalEquity"] == "900.00"

        # positions_usdc retCode=10016 MUST raise!
        byb_state["unified_code"] = 0
        byb_state["pos_usdc_code"] = 10016
        with pytest.raises(BybitApiError):
            await byb.fetch_full_snapshot()


# ══════════════════════════════════════════════════════════════════════════════
# 2. Deposit / Withdrawal Status Filtering & Multi-Currency Principal Valuation
# ══════════════════════════════════════════════════════════════════════════════


def test_bybit_mexc_gate_easicoin_deposit_withdraw_status_and_multicoin_principal(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()
    svc = LedgerService(store, vault, reg, default_demo_mode=False)

    # Provide global ETH=3000 USD ticker via OKX snapshot (0 balance, just spot price feed)
    # Bybit:
    #   deposits: 5000 (no status -> kept) + 1000 (status=3 -> kept) + 9999 (status=4 Failed -> dropped) = 6000
    #   internal_deposits: 500 (status=2 -> kept) + 888 (status=3 Rejected -> dropped) = 500
    #   withdrawals: 1000 + fee 2 (status=BlockchainConfirmed -> 1002) + 3000 (status=CancelByUser -> dropped)
    #   => Bybit net principal = 6000 + 500 - 1002 = 5498.00 U; equity = 5498.00 U -> net_pnl = 0.00 U
    store.upsert_plugin_snapshot(
        "bybit",
        {
            "venue_id": "bybit",
            "wallet": {"list": [{"totalEquity": "5498.00", "totalAvailableBalance": "5498.00", "coin": []}]},
            "funding": {},
            "positions": [],
            "closed_pnl": [],
            "executions": [],
            "deposits": [
                {"id": "d_nostat", "coin": "USDT", "amount": "5000.00"},
                {"id": "d_ok", "coin": "USDT", "amount": "1000.00", "status": 3},
                {"id": "d_fail", "coin": "USDT", "amount": "9999.00", "status": 4},
                {"id": "d_pend", "coin": "USDT", "amount": "2222.00", "status": "Pending"},
            ],
            "internal_deposits": [
                {"id": "id_ok", "coin": "USDT", "amount": "500.00", "status": 2},
                {"id": "id_rej", "coin": "USDT", "amount": "888.00", "status": 3},
            ],
            "withdrawals": [
                {"withdrawId": "w_ok", "coin": "USDT", "amount": "1000.00", "withdrawFee": "2.00", "status": "BlockchainConfirmed"},
                {"withdrawId": "w_cancel", "coin": "USDT", "amount": "3000.00", "withdrawFee": "1.00", "status": "CancelByUser"},
                {"withdrawId": "w_rej", "coin": "USDT", "amount": "500.00", "status": "Reject"},
                {"withdrawId": "w_pend", "coin": "USDT", "amount": "400.00", "status": "SecurityCheck"},
            ],
            "tickers": {"ETH": 3000.0, "BTC": 90000.0},
            "observed_rebate_ratio": 0.0,
        },
    )

    # Gate: deposits 1000 USDC (1:1) + 0.5 ETH (@3000 = 1500 U) = 2500.00 U principal; equity = 2500.00 U -> 0 fake profit!
    g_scope = store.venue_scope("gate")
    g_scope.set_kv(
        "live_bundle",
        {
            "wallet_total": {"total": {"amount": "2500.00", "unrealised_pnl": "0.0"}, "details": {"spot": {"amount": "2500.00"}}},
            "futures_account": {"available": "1000.00"},
            "unified": {
                "unified_account_total_equity": "2500.00",
                "balances": {
                    "USDC": {"available": "1000.00", "equity": "1000.00"},
                    "ETH": {"available": "0.5", "equity": "0.5", "equity_usd": "1500.00"},
                },
            },
        },
    )
    g_scope.upsert_records(
        "spot_account_book",
        [
            {"id": "g_usdc", "time": 1732000000, "currency": "USDC", "type": "deposit", "change": "1000.00"},
            {"id": "g_eth", "time": 1732000100, "currency": "ETH", "type": "deposit", "change": "0.5"},
        ],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["time"]),
    )

    # EasiCoin: transfers 500 USDC + 0.5 ETH (@3000 = 1500 U) into SPOT = 2000.00 U principal; equity = 2000.00 U!
    ec_scope = store.venue_scope("easicoin")
    ec_scope.set_kv(
        "live_bundle",
        {
            "userid": "ec_multicoin",
            "futures_balances": [],
            "funding_balances": [],
            "spot_balances": [
                {"token_name": "USDC", "balance": "500.00"},
                {"token_name": "ETH", "balance": "0.5"},
            ],
            "tickers": [{"symbol": "ETHUSDT", "last_price": "3000.00"}],
        },
    )
    ec_scope.upsert_records(
        "transfers",
        [
            {"time": 1732000000, "coin": "USDC", "amount": "500.00", "from_wallet": "FUNDING", "to_wallet": "SPOT", "transfer_status": "1"},
            {"time": 1732000200, "coin": "ETH", "amount": "0.5", "from_wallet": "FUNDING", "to_wallet": "SPOT", "transfer_status": "1"},
            {"time": 1732000300, "coin": "ETH", "amount": "2.0", "from_wallet": "FUNDING", "to_wallet": "SPOT", "transfer_status": "0"},
        ],
        key_fn=lambda r: f"{r['time']}_{r['coin']}_{r['amount']}",
        ts_fn=lambda r: float(r["time"]),
    )

    # MEXC: spot_deposit 1000 (status=5) - spot_withdraw 200 (status=6) = 800 U principal; status=7 (cancelled/failed) MUST be ignored!
    mx_scope = store.venue_scope("mexc")
    mx_scope.set_kv(
        "live_bundle",
        {
            "refer_code": "mx_wd_test",
            "futures_assets": [],
            "spot_balances": [{"asset": "USDT", "free": "800.00", "locked": "0"}],
            "spot_tickers": {},
        },
    )
    mx_scope.upsert_records(
        "transfers",
        [
            {"_key": "sd1", "_source": "spot_deposit", "coin": "USDT", "amount": "1000.00", "status": 5, "_time_ms": 1732000000000},
            {"_key": "sw_ok", "_source": "spot_withdraw", "coin": "USDT", "amount": "200.00", "status": 6, "_time_ms": 1732001000000},
            {"_key": "sw_cancel_7", "_source": "spot_withdraw", "coin": "USDT", "amount": "500.00", "status": 7, "_time_ms": 1732002000000},
        ],
        key_fn=lambda r: str(r["_key"]),
        ts_fn=lambda r: float(r["_time_ms"]),
    )

    dash = svc.get_dashboard(range_key="all")
    cards = {c["venue_id"]: c for c in dash["exchanges"]}

    assert cards["bybit"]["net_principal_usd"] == pytest.approx(5498.0, abs=0.01)
    assert cards["bybit"]["net_pnl_usd"] == pytest.approx(0.0, abs=0.01)

    assert cards["gate"]["net_principal_usd"] == pytest.approx(2500.0, abs=0.01)
    assert cards["gate"]["net_pnl_usd"] == pytest.approx(0.0, abs=0.01)

    assert cards["easicoin"]["net_principal_usd"] == pytest.approx(2000.0, abs=0.01)
    assert cards["easicoin"]["net_pnl_usd"] == pytest.approx(0.0, abs=0.01)

    assert cards["mexc"]["net_principal_usd"] == pytest.approx(800.0, abs=0.01)
    assert cards["mexc"]["net_pnl_usd"] == pytest.approx(0.0, abs=0.01)

    assert dash["reconciliation"]["residual_usd"] == 0.0


@pytest.mark.asyncio
async def test_mexc_incremental_sync_fetches_recent_spot_deposits_and_withdrawals() -> None:
    called_paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        called_paths.append(req.url.path)
        if req.url.path == "/api/v3/capital/deposit/hisrec":
            return httpx.Response(200, json=[{"id": "d_inc_1", "coin": "USDT", "amount": "350.00", "status": 5, "insertTime": 1732000500000}])
        if req.url.path == "/api/v3/capital/withdraw/history":
            return httpx.Response(200, json=[{"id": "w_inc_1", "coin": "USDT", "amount": "50.00", "status": 6, "applyTime": 1732000600000}])
        if req.url.path.startswith("/api/v1/"):
            return httpx.Response(200, json={"success": True, "code": 0, "data": []})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MexcReadOnlyClient(
            MexcCredentials(api_key="k", api_secret="s"),
            http_client=http,
            clock_ms=lambda: 1_732_050_000_000,
        )
        items = await client.fetch_cashflows_and_rebates(since_sec=1_732_000_000, do_full=False)

    assert "/api/v3/capital/deposit/hisrec" in called_paths
    assert "/api/v3/capital/withdraw/history" in called_paths
    sources = {it["_source"] for it in items}
    assert {"spot_deposit", "spot_withdraw"}.issubset(sources)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Cursor & Historical Window Pagination Fixes (OKX, Bybit, Binance)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_okx_deposit_and_withdrawal_pagination_uses_millisecond_ts_cursor() -> None:
    seen_dep_after: list[str] = []
    seen_wd_after: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        q = dict(req.url.params)
        if p == "/api/v5/asset/deposit-history":
            after = q.get("after", "")
            seen_dep_after.append(after)
            if not after:
                return httpx.Response(
                    200,
                    json={
                        "code": "0",
                        "data": [
                            {"depId": "dep_new_1", "ccy": "USDT", "amt": "1000", "state": "2", "ts": "1732200000000"},
                            {"depId": "dep_new_2", "ccy": "USDT", "amt": "2000", "state": "2", "ts": "1732100000000"},
                        ],
                    },
                )
            assert after == "1732100000000"  # MUST pass millisecond `ts`, NEVER `depId`!
            return httpx.Response(
                200,
                json={"code": "0", "data": [{"depId": "dep_old_3", "ccy": "USDT", "amt": "3000", "state": "2", "ts": "1732000000000"}]},
            )
        if p == "/api/v5/asset/withdrawal-history":
            after = q.get("after", "")
            seen_wd_after.append(after)
            if not after:
                return httpx.Response(
                    200,
                    json={
                        "code": "0",
                        "data": [
                            {"wdId": "wd_1", "ccy": "USDT", "amt": "500", "state": "2", "ts": "1732250000000"},
                            {"wdId": "wd_2", "ccy": "USDT", "amt": "600", "state": "2", "ts": "1732150000000"},
                        ],
                    },
                )
            assert after == "1732150000000"  # MUST pass millisecond `ts`, NEVER `wdId`!
            return httpx.Response(
                200,
                json={"code": "0", "data": [{"wdId": "wd_3", "ccy": "USDT", "amt": "700", "state": "2", "ts": "1732050000000"}]},
            )
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OkxReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=http, page_limit=2)
        deps = await client.get_deposit_history()
        wds = await client.get_withdrawal_history()

    assert [d["depId"] for d in deps] == ["dep_new_1", "dep_new_2", "dep_old_3"]
    assert [w["wdId"] for w in wds] == ["wd_1", "wd_2", "wd_3"]
    assert seen_dep_after == ["", "1732100000000"]
    assert seen_wd_after == ["", "1732150000000"]


@pytest.mark.asyncio
async def test_bybit_30d_asset_windows_backfill_1_year_and_52w_trade_windows_early_stop() -> None:
    now_ms = 1_760_000_000_000
    eight_months_ago_ms = now_ms - 230 * 86_400_000
    closed_pnl_calls = 0
    static_wd_calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal closed_pnl_calls, static_wd_calls
        p = req.url.path
        q = dict(req.url.params)
        if p == "/v5/account/wallet-balance":
            return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"totalEquity": "5000.00"}]}})
        if p == "/v5/asset/deposit/query-record":
            st = int(q.get("startTime") or 0)
            et = int(q.get("endTime") or 0)
            if st and et and st <= eight_months_ago_ms <= et:
                if not q.get("cursor"):
                    return httpx.Response(
                        200,
                        json={
                            "retCode": 0,
                            "result": {
                                "nextPageCursor": "dep_cur_2",
                                "rows": [{"id": "old_dep_p1", "coin": "USDT", "amount": "3000.00", "successAt": str(eight_months_ago_ms)}],
                            },
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "retCode": 0,
                        "result": {
                            "nextPageCursor": "",
                            "rows": [{"id": "old_dep_p2", "coin": "USDT", "amount": "2000.00", "successAt": str(eight_months_ago_ms - 1000)}],
                        },
                    },
                )
            return httpx.Response(200, json={"retCode": 0, "result": {"rows": []}})
        if p == "/v5/asset/withdraw/query-record":
            # Static mock ignoring startTime/endTime -> must break immediately on first duplicate window!
            static_wd_calls += 1
            return httpx.Response(
                200,
                json={"retCode": 0, "result": {"rows": [{"withdrawId": "static_w1", "coin": "USDT", "amount": "100.00", "createTime": str(now_ms - 1000)}]}},
            )
        if p == "/v5/position/closed-pnl":
            closed_pnl_calls += 1
            return httpx.Response(200, json={"retCode": 0, "result": {"list": []}})
        return httpx.Response(200, json={"retCode": 0, "result": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BybitReadOnlyClient(api_key="k", api_secret="s", client=http, clock_ms=lambda: now_ms)
        assert client.max_windows == 52
        snap = await client.fetch_full_snapshot(since_ms=0)

    assert {d["id"] for d in snap["deposits"]} == {"old_dep_p1", "old_dep_p2"}
    assert len(snap["withdrawals"]) == 1
    assert static_wd_calls == 2  # Window 0 + 1st window sees 0 new records -> breaks immediately!
    assert closed_pnl_calls == 9  # Window 0 + 8 consecutive empty windows -> early stop (< 52)!


@pytest.mark.asyncio
async def test_binance_um_trades_and_orders_use_7d_windows_incrementally_and_catch_recent_tail_when_saturated() -> None:
    now_ms = 1_760_000_000_000
    since_sec = (now_ms - 5 * 86_400_000) // 1000
    inc_trade_queries: list[dict[str, str]] = []
    inc_order_queries: list[dict[str, str]] = []

    def inc_handler(req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        if req.url.path == "/papi/v1/um/userTrades":
            inc_trade_queries.append(q)
            return httpx.Response(
                200,
                json=[{"id": 90001, "orderId": 501, "symbol": "BTCUSDT", "time": now_ms - 60_000, "qty": "0.1", "price": "95000"}],
            )
        if req.url.path == "/papi/v1/um/allOrders":
            inc_order_queries.append(q)
            return httpx.Response(
                200,
                json=[{"orderId": 501, "symbol": "BTCUSDT", "time": now_ms - 60_000, "origQty": "0.1"}],
            )
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(inc_handler)) as http:
        client = BinanceReadOnlyClient(
            BinanceCredentials(api_key="k", api_secret="s"),
            client=http,
            clock_ms=lambda: now_ms,
        )
        trades_inc = await client.fetch_um_trades_for_symbols(["BTCUSDT"], since_sec=since_sec, do_full=False)
        orders_inc = await client.fetch_um_orders_for_symbols(["BTCUSDT"], since_sec=since_sec, do_full=False)

    assert len(trades_inc) == 1 and trades_inc[0]["id"] == 90001
    assert len(orders_inc) == 1 and orders_inc[0]["orderId"] == 501
    assert "startTime" in inc_trade_queries[0] and "fromId" not in inc_trade_queries[0]
    assert "startTime" in inc_order_queries[0] and "orderId" not in inc_order_queries[0]

    # Full-sync saturation test: simulate 50,000+ historical trades where `fromId` pages saturate at old trades (1..2000),
    # verifying recent-tail catchup still captures today's trade #999999!
    def saturated_full_handler(req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        if req.url.path == "/papi/v1/um/userTrades":
            if "fromId" in q:
                fid = int(q["fromId"])
                base = 1 if fid == 0 else fid
                return httpx.Response(
                    200,
                    json=[{"id": base + i, "orderId": 100, "symbol": "BTCUSDT", "time": 1_680_000_000_000 + i} for i in range(1000)],
                )
            return httpx.Response(
                200,
                json=[{"id": 999999, "orderId": 8888, "symbol": "BTCUSDT", "time": now_ms - 5_000, "qty": "0.5", "price": "96000"}],
            )
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(saturated_full_handler)) as http_sat:
        client_sat = BinanceReadOnlyClient(
            BinanceCredentials(api_key="k", api_secret="s"),
            client=http_sat,
            clock_ms=lambda: now_ms,
        )
        trades_sat = await client_sat.fetch_um_trades_for_symbols(
            ["BTCUSDT"],
            since_sec=since_sec,
            do_full=True,
            max_pages=2,
        )

    fetched_ids = {int(t["id"]) for t in trades_sat}
    assert 1 in fetched_ids
    assert 999999 in fetched_ids
