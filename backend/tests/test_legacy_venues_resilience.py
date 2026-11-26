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
from opentradeledger.adapters.bybit.sync import sync_bybit_venue
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
from opentradeledger.adapters.okx.sync import sync_okx_venue
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.venue_health import VenueHealthTracker
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
async def test_bybit_deposits_scan_a_year_and_closed_pnl_covers_two_years() -> None:
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
        if p == "/v5/asset/transfer/query-account-coins-balance":
            return httpx.Response(200, json={"retCode": 0, "result": {"balance": []}})
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
    assert closed_pnl_calls == 105  # Include the oldest partial week of the 730-day horizon.


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


# ══════════════════════════════════════════════════════════════════════════════
# 4. Malformed HTTP 200 Balance Payload Guards (All 7 Legacy Venues) + True-Zero
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_easicoin_malformed_http200_spot_funding_futures_preserve_130u_and_true_zero_updates_cleanly(
    tmp_path: Path,
) -> None:
    store = LedgerStore(tmp_path)
    health = VenueHealthTracker(store)
    state: dict[str, Any] = {
        "fut": {"code": 0, "data": {"list": [{"coin": "USDT", "equity": "100.00"}]}},
        "fund": {"code": 0, "data": {"list": [{"coin": "USDC", "total_equity": "10.00"}]}},
        "spot": {"code": 0, "data": [{"token_name": "USDT", "balance": "20.00"}]},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/asset-api/account/private/v1/userid":
            return httpx.Response(200, json={"code": 0, "data": {"userid": "ec_strict_1"}})
        if p == "/futures/private/v1/account/balance":
            return httpx.Response(200, json=state["fut"])
        if p == "/asset-api/account/private/v1/get-funding-account-balance":
            return httpx.Response(200, json=state["fund"])
        if p == "/spot-api/account/private/v1/balance":
            return httpx.Response(200, json=state["spot"])
        return httpx.Response(200, json={"code": 0, "data": {"list": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = EasiCoinReadOnlyClient(
            EasiCoinCredentials(api_key="k", api_secret="s"),
            client=http,
            clock_ms=lambda: 1_760_000_000_000,
        )
        out_ok = await sync_easicoin_venue(client, store, now_s=1_760_000_000)
        assert out_ok.equity_usd == pytest.approx(130.0, abs=0.01)
        assert out_ok.warnings == []

        # All 3 retest malformed HTTP 200 spot shapes + malformed item MUST preserve 130 U (never drop to 110 U)!
        for bad_spot in (
            {"code": 0},
            {"code": 0, "data": None},
            {"code": 0, "data": {}},
            {"code": 0, "data": [{"wrong": "x"}]},
        ):
            state["spot"] = bad_spot
            out_bad = await sync_easicoin_venue(client, store, now_s=1_760_000_060)
            health.record_success("easicoin", warnings=out_bad.warnings)
            assert out_bad.equity_usd == pytest.approx(130.0, abs=0.01)
            assert any("spot_balances: malformed response structure" in w for w in out_bad.warnings)
            resolved_h = health.resolve_state("easicoin", configured=True)
            assert resolved_h["status"] == "connected" and len(resolved_h["warnings"]) >= 1

        # Restore spot, test malformed funding shapes -> preserves 130 U + warning
        state["spot"] = {"code": 0, "data": [{"token_name": "USDT", "balance": "20.00"}]}
        for bad_fund in ({"code": 0}, {"code": 0, "data": None}, {"code": 0, "data": {}}):
            state["fund"] = bad_fund
            out_bad_f = await sync_easicoin_venue(client, store, now_s=1_760_000_120)
            assert out_bad_f.equity_usd == pytest.approx(130.0, abs=0.01)
            assert any("funding_balances: malformed response structure" in w for w in out_bad_f.warnings)
        state["fund"] = {"code": 0, "data": {"list": [{"coin": "USDC", "total_equity": "10.00"}]}}

        # Malformed primary futures balance MUST raise EasiCoinApiError!
        state["fut"] = {"code": 0, "data": {}}
        with pytest.raises(EasiCoinApiError):
            await sync_easicoin_venue(client, store, now_s=1_760_000_180)
        assert store.venue_scope("easicoin").get_kv("live_bundle")["spot_balances"][0]["balance"] == "20.00"

        # Legitimate true-zero responses update to 0.00 U with ZERO warnings!
        state["fut"] = {"code": 0, "data": {"list": []}}
        state["fund"] = {"code": 0, "data": {"list": []}}
        state["spot"] = {"code": 0, "data": []}
        out_zero = await sync_easicoin_venue(client, store, now_s=1_760_000_240)
        assert out_zero.equity_usd == 0.0
        assert out_zero.warnings == []


@pytest.mark.asyncio
async def test_gate_malformed_http200_balances_preserve_120u_and_never_clamp_unified_to_zero(
    tmp_path: Path,
) -> None:
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()
    svc = LedgerService(store, vault, reg, default_demo_mode=False)

    state: dict[str, Any] = {
        "wt": {"total": {"amount": "120.00"}, "details": {"spot": {"amount": "120.00"}}},
        "uni": {"unified_account_total_equity": "120.00", "balances": {"USDT": {"available": "120.00", "equity": "120.00"}}},
        "fut": {"total": "120.00", "available": "120.00", "enable_credit": True},
        "spot": [],
        "stock": {},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/v4/wallet/total_balance":
            return httpx.Response(200, json=state["wt"])
        if p == "/api/v4/unified/accounts":
            return httpx.Response(200, json=state["uni"])
        if p == "/api/v4/futures/usdt/accounts":
            return httpx.Response(200, json=state["fut"])
        if p == "/api/v4/spot/accounts":
            return httpx.Response(200, json=state["spot"])
        if p == "/api/v4/stock/users/assets":
            return httpx.Response(200, json=state["stock"])
        if p == "/api/v4/copy_trading/positions":
            return httpx.Response(200, json={"data": {"list": []}})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GateReadOnlyClient(
            GateManualCredentials(api_key="k", api_secret="s"),
            client=http,
            clock_sec=lambda: 1_760_000_000,
        )
        out_ok = await sync_gate_venue(client, store, now_s=1_760_000_000)
        assert out_ok.equity_usd == pytest.approx(120.0, abs=0.01)
        assert out_ok.warnings == []

        # Malformed wallet_total + stock_assets + spot_accounts -> preserves 120 U + emits warnings!
        state["wt"] = {"wrong": []}
        state["spot"] = {"wrong": []}
        state["stock"] = {"wrong": []}
        out_deg = await sync_gate_venue(client, store, now_s=1_760_000_060)
        assert out_deg.equity_usd == pytest.approx(120.0, abs=0.01)
        assert any("wallet_total: malformed response structure" in w for w in out_deg.warnings)
        assert any("spot_accounts: malformed response structure" in w for w in out_deg.warnings)
        assert any("stock_assets: malformed response structure" in w for w in out_deg.warnings)
        gate_card = next(c for c in svc.get_dashboard(range_key="all")["exchanges"] if c["venue_id"] == "gate")
        assert gate_card["equity_usd"] == pytest.approx(120.0, abs=0.01)

        # Fresh store without prior wallet_total: if wallet_total is malformed {"wrong": []} while unified has 120 U,
        # accounting.py & sync.py MUST NOT clamp valid unified 120 U to 0!
        fresh_store = LedgerStore(tmp_path / "fresh_gate")
        fresh_svc = LedgerService(fresh_store, CredentialVault(tmp_path / "fresh_gate"), reg, default_demo_mode=False)
        state["spot"] = []
        state["stock"] = {}
        out_fresh_partial = await sync_gate_venue(client, fresh_store, now_s=1_760_000_120)
        assert out_fresh_partial.equity_usd == pytest.approx(120.0, abs=0.01)
        assert any("wallet_total: malformed" in w for w in out_fresh_partial.warnings)
        fresh_card = next(c for c in fresh_svc.get_dashboard(range_key="all")["exchanges"] if c["venue_id"] == "gate")
        assert fresh_card["equity_usd"] == pytest.approx(120.0, abs=0.01)
        assert fresh_card["futures_equity_usd"] == pytest.approx(120.0, abs=0.01)

        # First-time sync where ALL core Gate balance endpoints return malformed payloads MUST raise!
        empty_store = LedgerStore(tmp_path / "all_bad_gate")
        state["uni"] = {"wrong": []}
        state["fut"] = {"wrong": []}
        with pytest.raises(GateApiHttpError):
            await sync_gate_venue(client, empty_store, now_s=1_760_000_180)

        # True-zero Gate payload updates cleanly to 0.00 U with ZERO warnings!
        state["wt"] = {"total": {"amount": "0"}}
        state["uni"] = {"unified_account_total_equity": "0", "balances": {}}
        state["fut"] = {"total": "0", "available": "0"}
        out_zero = await sync_gate_venue(client, store, now_s=1_760_000_240)
        assert out_zero.equity_usd == 0.0
        assert out_zero.warnings == []


@pytest.mark.asyncio
async def test_okx_bybit_binance_mexc_bitget_malformed_http200_preserve_120u_and_allow_true_zero(
    tmp_path: Path,
) -> None:
    # ── 1) OKX (100 U trading + 20 U funding = 120 U) ──
    okx_store = LedgerStore(tmp_path / "okx")
    okx_state: dict[str, Any] = {
        "bal": {"code": "0", "data": [{"totalEq": "100.00", "details": []}]},
        "fund": {"code": "0", "data": [{"ccy": "USDT", "bal": "20.00"}]},
    }

    def okx_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v5/account/balance":
            return httpx.Response(200, json=okx_state["bal"])
        if req.url.path == "/api/v5/asset/balances":
            return httpx.Response(200, json=okx_state["fund"])
        return httpx.Response(200, json={"code": "0", "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(okx_handler)) as hc:
        okx_c = OkxReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=hc)
        o1 = await sync_okx_venue(okx_c, okx_store, now_s=1_760_000_000)
        assert o1.equity_usd == pytest.approx(120.0, abs=0.01) and o1.warnings == []

        for bad_fund in ({"wrong": []}, {"code": "0", "data": {"wrong": []}}, {"code": "0", "data": [{"wrong": 1}]}):
            okx_state["fund"] = bad_fund
            o_deg = await sync_okx_venue(okx_c, okx_store, now_s=1_760_000_060)
            assert o_deg.equity_usd == pytest.approx(120.0, abs=0.01)
            assert any("Malformed OKX funding balance payload" in w for w in o_deg.warnings)

        okx_state["bal"] = {"code": "0", "data": [{"wrong": 1}]}
        with pytest.raises(OkxApiError, match="Malformed OKX account balance payload"):
            await sync_okx_venue(okx_c, okx_store, now_s=1_760_000_120)

        okx_state["bal"] = {"code": "0", "data": [{"totalEq": "0", "details": []}]}
        okx_state["fund"] = {"code": "0", "data": []}
        o_zero = await sync_okx_venue(okx_c, okx_store, now_s=1_760_000_180)
        assert o_zero.equity_usd == 0.0 and o_zero.warnings == []

    # ── 2) Bybit (100 U UNIFIED + 20 U FUND = 120 U) ──
    byb_store = LedgerStore(tmp_path / "bybit")
    byb_state: dict[str, Any] = {
        "wallet": {"retCode": 0, "result": {"list": [{"totalEquity": "100.00", "coin": []}]}},
        "fund": {"retCode": 0, "result": {"balance": [{"coin": "USDT", "walletBalance": "20.00"}]}},
    }

    def byb_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v5/account/wallet-balance":
            return httpx.Response(200, json=byb_state["wallet"])
        if req.url.path == "/v5/asset/transfer/query-account-coins-balance":
            return httpx.Response(200, json=byb_state["fund"])
        if req.url.path in ("/v5/asset/deposit/query-record", "/v5/asset/deposit/query-internal-record", "/v5/asset/withdraw/query-record"):
            return httpx.Response(200, json={"retCode": 0, "result": {"rows": []}})
        return httpx.Response(200, json={"retCode": 0, "result": {"list": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(byb_handler)) as hc:
        byb_c = BybitReadOnlyClient(api_key="k", api_secret="s", client=hc, max_windows=1)
        b1 = await sync_bybit_venue(byb_c, byb_store, now_s=1_760_000_000)
        assert b1.equity_usd == pytest.approx(120.0, abs=0.01) and b1.warnings == []

        byb_state["fund"] = {"retCode": 0, "result": {"wrong": []}}
        b_deg = await sync_bybit_venue(byb_c, byb_store, now_s=1_760_000_060)
        assert b_deg.equity_usd == pytest.approx(120.0, abs=0.01)
        assert any("Malformed Bybit funding balance payload" in w for w in b_deg.warnings)

        byb_state["wallet"] = {"retCode": 0, "result": {"wrong": []}}
        with pytest.raises(BybitApiError, match="Malformed Bybit wallet-balance payload"):
            await sync_bybit_venue(byb_c, byb_store, now_s=1_760_000_120)

        byb_state["wallet"] = {"retCode": 0, "result": {"list": [{"totalEquity": "0", "coin": []}]}}
        byb_state["fund"] = {"retCode": 0, "result": {"balance": []}}
        b_zero = await sync_bybit_venue(byb_c, byb_store, now_s=1_760_000_180)
        assert b_zero.equity_usd == 0.0 and b_zero.warnings == []

    # ── 3) Binance (120 U wallet_balances + spot + PAPI) ──
    bn_store = LedgerStore(tmp_path / "binance")
    bn_state: dict[str, Any] = {
        "spot": {"uid": 1001, "balances": [{"asset": "USDT", "free": "20.00", "locked": "0"}]},
        "wallet": [{"walletName": "Portfolio Margin", "balance": "120.00", "activate": True}],
        "papi": {"accountEquity": "100.00", "totalAvailableBalance": "100.00"},
        "fapi": {"totalMarginBalance": "0", "assets": []},
    }

    def bn_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/v3/account":
            return httpx.Response(200, json=bn_state["spot"])
        if p == "/sapi/v1/asset/wallet/balance":
            return httpx.Response(200, json=bn_state["wallet"])
        if p == "/papi/v1/account":
            return httpx.Response(200, json=bn_state["papi"])
        if p == "/fapi/v2/account":
            return httpx.Response(200, json=bn_state["fapi"])
        if p in ("/sapi/v1/bnbBurn", "/papi/v1/um/account", "/api/v3/ticker/price"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(bn_handler)) as hc:
        bn_c = BinanceReadOnlyClient(
            BinanceCredentials(api_key="k", api_secret="s"),
            client=hc,
            clock_ms=lambda: 1_760_000_000_000,
        )
        bn1 = await sync_binance_venue(bn_c, bn_store, now_s=1_760_000_000)
        assert bn1.equity_usd == pytest.approx(120.0, abs=0.01) and bn1.warnings == []

        bn_state["wallet"] = {"wrong": []}
        bn_state["spot"] = {"wrong": []}
        bn_state["papi"] = {"wrong": []}
        bn_state["fapi"] = {"wrong": []}
        bn_deg = await sync_binance_venue(bn_c, bn_store, now_s=1_760_000_060)
        assert bn_deg.equity_usd == pytest.approx(120.0, abs=0.01)
        assert any("wallet_balances: malformed" in w for w in bn_deg.warnings)
        assert any("spot_balances: malformed" in w for w in bn_deg.warnings)

        # Fresh store with malformed PAPI + FAPI MUST raise!
        with pytest.raises(BinanceApiError):
            await sync_binance_venue(bn_c, LedgerStore(tmp_path / "bn_fresh_bad"), now_s=1_760_000_060)

        bn_state["wallet"] = []
        bn_state["spot"] = {"uid": 1001, "balances": []}
        bn_state["papi"] = {"accountEquity": "0", "totalAvailableBalance": "0"}
        bn_zero = await sync_binance_venue(bn_c, bn_store, now_s=1_760_000_120)
        assert bn_zero.equity_usd == 0.0 and bn_zero.warnings == []

    # ── 4) MEXC (120 U spot) ──
    mx_store = LedgerStore(tmp_path / "mexc")
    mx_state: dict[str, Any] = {
        "spot": {"balances": [{"asset": "USDT", "free": "120.00", "locked": "0"}]},
        "fut": {"success": True, "code": 0, "data": []},
    }

    def mx_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/account":
            return httpx.Response(200, json=mx_state["spot"])
        if req.url.path == "/api/v1/private/account/assets":
            return httpx.Response(200, json=mx_state["fut"])
        if req.url.path.startswith("/api/v1/"):
            return httpx.Response(200, json={"success": True, "code": 0, "data": []})
        if req.url.path in ("/api/v3/rebate/referCode", "/api/v3/mxDeduct/enable", "/api/v3/exchangeInfo"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(mx_handler)) as hc:
        mx_c = MexcReadOnlyClient(
            MexcCredentials(api_key="k", api_secret="s"),
            http_client=hc,
            clock_ms=lambda: 1_760_000_000_000,
        )
        m1 = await sync_mexc_venue(mx_c, mx_store, now_s=1_760_000_000)
        assert m1.equity_usd == pytest.approx(120.0, abs=0.01) and m1.warnings == []

        mx_state["spot"] = {"wrong": []}
        m_deg = await sync_mexc_venue(mx_c, mx_store, now_s=1_760_000_060)
        assert m_deg.equity_usd == pytest.approx(120.0, abs=0.01)
        assert any("spot_balances" in w for w in m_deg.warnings)

        # Malformed futures_assets alongside valid spot
        mx_state["spot"] = {"balances": [{"asset": "USDT", "free": "120.00", "locked": "0"}]}
        mx_state["fut"] = {"success": True, "code": 0, "data": {"wrong": []}}
        m_deg2 = await sync_mexc_venue(mx_c, mx_store, now_s=1_760_000_090)
        assert m_deg2.equity_usd == pytest.approx(120.0, abs=0.01)
        assert any("futures_assets" in w for w in m_deg2.warnings)

        # Both malformed -> raises MexcApiError!
        mx_state["spot"] = {"wrong": []}
        with pytest.raises(MexcApiError):
            await sync_mexc_venue(mx_c, mx_store, now_s=1_760_000_120)

        mx_state["spot"] = {"balances": []}
        mx_state["fut"] = {"success": True, "code": 0, "data": []}
        m_zero = await sync_mexc_venue(mx_c, mx_store, now_s=1_760_000_180)
        assert m_zero.equity_usd == 0.0 and m_zero.warnings == []

    # ── 5) Bitget (100 U UTA + 20 U Funding = 120 U) ──
    bg_store = LedgerStore(tmp_path / "bitget")
    bg_state: dict[str, Any] = {
        "uta": {"code": "00000", "data": {"accountEquity": "100.00", "available": "100.00"}},
        "fund": {"code": "00000", "data": [{"coin": "USDT", "balance": "20.00", "available": "20.00"}]},
    }

    def bg_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/account/assets":
            return httpx.Response(200, json=bg_state["uta"])
        if req.url.path == "/api/v3/account/funding-assets":
            return httpx.Response(200, json=bg_state["fund"])
        if req.url.path == "/api/v2/spot/account/assets":
            return httpx.Response(200, json={"code": "00000", "data": {"wrong": []}})
        return httpx.Response(200, json={"code": "00000", "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(bg_handler)) as hc:
        bg_c = BitgetReadOnlyClient(
            BitgetCredentials(api_key="k", api_secret="s", passphrase="p"),
            http_client=hc,
            clock_ms=lambda: 1_760_000_000_000,
        )
        bg1 = await sync_bitget_venue(bg_c, bg_store, now_s=1_760_000_000)
        assert bg1.equity_usd == pytest.approx(120.0, abs=0.01) and bg1.warnings == []

        for bad_bg_fund in ({"wrong": []}, {"code": "00000", "data": {"wrong": []}}):
            bg_state["fund"] = bad_bg_fund
            bg_deg = await sync_bitget_venue(bg_c, bg_store, now_s=1_760_000_060)
            assert bg_deg.equity_usd == pytest.approx(120.0, abs=0.01)
            assert any("funding_assets" in w for w in bg_deg.warnings)

        # Malformed UTA /api/v3/account/assets or Classic /api/v2/spot/account/assets MUST raise!
        bg_state["uta"] = {"code": "00000", "data": {"wrong": []}}
        with pytest.raises(BitgetApiError):
            await sync_bitget_venue(bg_c, bg_store, now_s=1_760_000_120)
        with pytest.raises(BitgetApiError):
            await bg_c.get("/api/v2/spot/account/assets")

        bg_state["uta"] = {"code": "00000", "data": {"accountEquity": "0", "assets": []}}
        bg_state["fund"] = {"code": "00000", "data": []}
        bg_zero = await sync_bitget_venue(bg_c, bg_store, now_s=1_760_000_180)
        assert bg_zero.equity_usd == 0.0 and bg_zero.warnings == []


# ══════════════════════════════════════════════════════════════════════════════
# 5. Three Historical Boundary Pagination Fixes (Bybit 98d / EasiCoin 501 / Binance 1002)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bybit_sparse_close_98_days_ago_is_100pct_captured_on_full_sync(tmp_path: Path) -> None:
    now_ms = 1_764_900_000_000
    close_98d_ms = now_ms - 98 * 86_400_000
    open_98d_ms = close_98d_ms - 3_600_000
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()
    svc = LedgerService(store, vault, reg, default_demo_mode=False)

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        q = dict(req.url.params)
        if p == "/v5/account/wallet-balance":
            return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"totalEquity": "1014.50", "coin": []}]}})
        if p == "/v5/asset/transfer/query-account-coins-balance":
            return httpx.Response(200, json={"retCode": 0, "result": {"balance": []}})
        if p == "/v5/asset/deposit/query-record":
            if not q.get("startTime"):
                return httpx.Response(
                    200,
                    json={"retCode": 0, "result": {"rows": [{"id": "d_byb_98", "coin": "USDT", "amount": "1000.00", "status": 3, "successAt": str(open_98d_ms - 60_000)}]}},
                )
            return httpx.Response(200, json={"retCode": 0, "result": {"rows": []}})
        if p in ("/v5/asset/deposit/query-internal-record", "/v5/asset/withdraw/query-record"):
            return httpx.Response(200, json={"retCode": 0, "result": {"rows": []}})
        if p == "/v5/position/closed-pnl":
            st = int(q.get("startTime") or 0)
            et = int(q.get("endTime") or 0)
            if st and et and st <= close_98d_ms <= et:
                return httpx.Response(
                    200,
                    json={
                        "retCode": 0,
                        "result": {
                            "list": [
                                {
                                    "orderId": "ord_98d",
                                    "symbol": "BTCUSDT",
                                    "side": "Sell",
                                    "qty": "0.1",
                                    "closedSize": "0.1",
                                    "avgEntryPrice": "90000",
                                    "avgExitPrice": "90160",
                                    "cumEntryValue": "9000",
                                    "cumExitValue": "9016",
                                    "openFee": "0.75",
                                    "closeFee": "0.75",
                                    "closedPnl": "14.50",
                                    "createdTime": str(open_98d_ms),
                                    "updatedTime": str(close_98d_ms),
                                }
                            ]
                        },
                    },
                )
            return httpx.Response(200, json={"retCode": 0, "result": {"list": []}})
        return httpx.Response(200, json={"retCode": 0, "result": {"list": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        client = BybitReadOnlyClient(api_key="k", api_secret="s", client=hc, clock_ms=lambda: now_ms)
        outcome = await sync_bybit_venue(client, store, now_s=now_ms // 1000, force_full=True)

    assert outcome.warnings == []
    dash = svc.get_dashboard(range_key="all")
    assert len(dash["trades"]) == 1
    assert dash["trades"][0]["net_pnl_usd"] == pytest.approx(14.50, abs=0.01)


@pytest.mark.asyncio
async def test_easicoin_single_window_501_transfers_fetches_all_501_and_warns_on_50_page_cap(
    tmp_path: Path,
) -> None:
    now_s = 1_764_900_000
    now_ms = now_s * 1000
    store = LedgerStore(tmp_path)

    transfers_501 = [
        {
            "id": f"ec_tr_{i}",
            "userid": "ec_501_u",
            "coin": "USDT",
            "amount": "1.00",
            "from_wallet": "FUNDING",
            "to_wallet": "CONTRACT",
            "transfer_status": "1",
            "time": now_s - 3600,  # Even sharing identical coarse second timestamp!
        }
        for i in range(501)
    ]

    def handler_501(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        q = dict(req.url.params)
        if p == "/asset-api/account/private/v1/userid":
            return httpx.Response(200, json={"code": 0, "data": {"userid": "ec_501_u"}})
        if p == "/futures/private/v1/account/balance":
            return httpx.Response(200, json={"code": 0, "data": {"list": [{"coin": "USDT", "equity": "501.00"}]}})
        if p == "/spot-api/account/private/v1/balance":
            return httpx.Response(200, json={"code": 0, "data": []})
        if p == "/asset-api/account/private/v1/user-transfer-rercord/page":
            st = int(q.get("start_time") or 0)
            et = int(q.get("end_time") or 0)
            if st <= (now_s - 3600) * 1000 <= et:
                pg = int(q.get("page_num") or 1)
                sz = int(q.get("page_size") or 100)
                chunk = transfers_501[(pg - 1) * sz : pg * sz]
                return httpx.Response(200, json={"code": 0, "data": {"list": chunk}})
            return httpx.Response(200, json={"code": 0, "data": {"list": []}})
        return httpx.Response(200, json={"code": 0, "data": {"list": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_501)) as hc:
        client = EasiCoinReadOnlyClient(
            EasiCoinCredentials(api_key="k", api_secret="s"),
            client=hc,
            clock_ms=lambda: now_ms,
        )
        out = await sync_easicoin_venue(client, store, now_s=now_s)

    assert out.warnings == []
    saved_transfers = store.venue_scope("easicoin").load_records("transfers")
    assert len(saved_transfers) == 501

    # Also verify 50-page saturation emits explicit truncation warning
    def saturated_50p_handler(req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        pg = int(q.get("page_num") or 1)
        rows = [{"id": f"sat_{pg}_{i}", "coin": "USDT", "amount": "1", "time": now_s - 100} for i in range(100)]
        return httpx.Response(200, json={"code": 0, "data": {"list": rows}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(saturated_50p_handler)) as hc_sat:
        c_sat = EasiCoinReadOnlyClient(
            EasiCoinCredentials(api_key="k", api_secret="s"),
            client=hc_sat,
            clock_ms=lambda: now_ms,
        )
        rows_sat = await c_sat.fetch_transfers(since_sec=now_s - 86400, max_windows=1)
        assert len(rows_sat) == 5000
        assert any("pagination limit (50 pages / 5000 rows)" in w for w in c_sat._transfer_warnings)


@pytest.mark.asyncio
async def test_binance_incremental_same_millisecond_1002_trades_and_502_orders_zero_loss_and_fallback_warning(
    tmp_path: Path,
) -> None:
    since_ms = 1_764_654_400_000
    same_ms = 1_764_827_200_000
    next_sec_ms = same_ms + 1_000
    now_ms = same_ms + 60_000

    # 1,001 trades at exact millisecond `same_ms` + 1 trade at `same_ms + 1000ms` = 1,002 trades
    all_trades = [
        {
            "id": i + 1,
            "orderId": (i // 2) + 1,
            "symbol": "BTCUSDT",
            "side": "BUY" if i % 2 == 0 else "SELL",
            "qty": "0.01",
            "price": "95000",
            "realizedPnl": "0.10",
            "commission": "0.01",
            "commissionAsset": "USDT",
            "time": same_ms if i < 1001 else next_sec_ms,
        }
        for i in range(1002)
    ]
    # 501 orders at exact millisecond `same_ms` + 1 order at `same_ms + 1000ms` = 502 orders
    all_orders = [
        {
            "orderId": i + 1,
            "symbol": "BTCUSDT",
            "origQty": "0.02",
            "time": same_ms if i < 501 else next_sec_ms,
            "updateTime": same_ms if i < 501 else next_sec_ms,
        }
        for i in range(502)
    ]

    support_id_cursor = {"enabled": True}

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        q = dict(req.url.params)
        if p == "/api/v3/account":
            return httpx.Response(200, json={"uid": 77, "balances": []})
        if p == "/sapi/v1/asset/wallet/balance":
            return httpx.Response(200, json=[{"walletName": "Portfolio Margin", "balance": "1000.00"}])
        if p == "/papi/v1/account":
            return httpx.Response(200, json={"accountEquity": "1000.00", "totalAvailableBalance": "1000.00"})
        if p == "/papi/v1/um/income":
            return httpx.Response(
                200,
                json=[{"tranId": 1, "symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "1.0", "asset": "USDT", "time": same_ms}],
            )
        if p == "/papi/v1/um/userTrades":
            lim = int(q.get("limit") or 1000)
            if "fromId" in q:
                if not support_id_cursor["enabled"]:
                    return httpx.Response(200, json=[])
                fid = int(q["fromId"])
                return httpx.Response(200, json=[t for t in all_trades if int(t["id"]) >= fid][:lim])
            st = int(q.get("startTime") or 0)
            et = int(q.get("endTime") or now_ms)
            return httpx.Response(200, json=[t for t in all_trades if st <= int(t["time"]) <= et][:lim])
        if p == "/papi/v1/um/allOrders":
            lim = int(q.get("limit") or 500)
            if "orderId" in q:
                if not support_id_cursor["enabled"]:
                    return httpx.Response(200, json=[])
                oid = int(q["orderId"])
                return httpx.Response(200, json=[o for o in all_orders if int(o["orderId"]) >= oid][:lim])
            st = int(q.get("startTime") or 0)
            et = int(q.get("endTime") or now_ms)
            return httpx.Response(200, json=[o for o in all_orders if st <= int(o["time"]) <= et][:lim])
        if p in ("/sapi/v1/bnbBurn", "/papi/v1/um/account", "/api/v3/ticker/price"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json=[])

    # 1. Standard Binance upstream supporting fromId / orderId -> captures 1002/1002 trades and 502/502 orders!
    store = LedgerStore(tmp_path / "bn_1002")
    store.venue_scope("binance").set_kv("backfill_completed", True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        client = BinanceReadOnlyClient(
            BinanceCredentials(api_key="k", api_secret="s"),
            client=hc,
            clock_ms=lambda: now_ms,
        )
        out = await sync_binance_venue(client, store, now_s=now_ms // 1000, force_full=False)

    assert out.warnings == []
    assert store.venue_scope("binance").count_records("fills") == 1002
    assert store.venue_scope("binance").count_records("orders") == 502
    saved_fill_ids = {int(r["id"]) for r in store.venue_scope("binance").load_records("fills")}
    assert {1, 1000, 1001, 1002}.issubset(saved_fill_ids)

    # 2. Fallback insurance: upstream ignoring fromId/orderId still advances to `same_ms + 1` (recovering #1002 & #502) and emits warning!
    support_id_cursor["enabled"] = False
    store_fb = LedgerStore(tmp_path / "bn_1002_fb")
    store_fb.venue_scope("binance").set_kv("backfill_completed", True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc_fb:
        client_fb = BinanceReadOnlyClient(
            BinanceCredentials(api_key="k", api_secret="s"),
            client=hc_fb,
            clock_ms=lambda: now_ms,
        )
        out_fb = await sync_binance_venue(client_fb, store_fb, now_s=now_ms // 1000, force_full=False)

    fb_fill_ids = {int(r["id"]) for r in store_fb.venue_scope("binance").load_records("fills")}
    fb_order_ids = {int(r["orderId"]) for r in store_fb.venue_scope("binance").load_records("orders")}
    assert 1002 in fb_fill_ids  # T + 1000ms trade recovered!
    assert 502 in fb_order_ids  # T + 1000ms order recovered!
    assert any("same-millisecond page limit" in w for w in out_fb.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["copy", "wallet", "book", "both", "other-copy"])
async def test_gate_optional_copy_permission_is_distinct_from_financial_data(tmp_path, blocked):
    store = LedgerStore(tmp_path)
    svc = LedgerService(store, CredentialVault(tmp_path), build_default_registry(), default_demo_mode=False)
    now = 1_760_000_000
    mode = "healthy"

    def handler(req):
        path = req.url.path
        if path.endswith("/copy_trading/positions") and mode != "healthy":
            message = "Access denied" if mode == "other-copy" else "permission not open yet"
            return httpx.Response(403, json={"label": "FORBIDDEN", "message": message})
        if ((path.endswith("/wallet/total_balance") and mode in ("wallet", "both"))
                or (path.endswith("/spot/account_book") and mode in ("book", "both"))):
            return httpx.Response(403, json={"label": "FORBIDDEN", "message": "permission not open yet"})
        if path.endswith("/wallet/total_balance"):
            payload = {"total": {"amount": "120"}, "details": {"spot": {"amount": "120"}}}
        elif path.endswith("/unified/accounts"):
            payload = {"unified_account_total_equity": "120"}
        elif path.endswith("/futures/usdt/accounts"):
            payload = {"total": "0", "available": "0"}
        elif path.endswith("/spot/account_book"):
            payload = [{"id": "first", "time": now - 100, "type": "deposit", "currency": "USDT", "change": "80"}]
        else:
            payload = []
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GateReadOnlyClient(GateManualCredentials(api_key="k", api_secret="s"), client=http, clock_sec=lambda: now)
        await sync_gate_venue(client, store, now_s=now)
        mode = blocked
        outcome = await sync_gate_venue(client, store, now_s=now + 60)
        dashboard = svc.get_dashboard(range_key="all")
        assert dashboard["summary"]["total_equity_usd"] == 120
        assert dashboard["exchanges"][0]["net_principal_usd"] == 80
        warnings = dashboard["warnings"]
        if blocked == "copy":
            assert not outcome.warnings and not warnings
            assert store.venue_scope("gate").get_kv("live_bundle")["copy_trading_probe"]["accessible"] is False
        else:
            assert len(warnings) == 1
            zh = warnings[0]["summary_zh"]
            assert ("钱包总余额" in zh) == (blocked in ("wallet", "both"))
            assert ("现货资金流水" in zh) == (blocked in ("book", "both"))
            assert ("跟单持仓" in zh) == (blocked == "other-copy")
            assert "总资产或本金可能少算" not in zh
        mode = "healthy"
        outcome = await sync_gate_venue(client, store, now_s=now + 120)
        assert not outcome.warnings and not svc.get_dashboard(range_key="all")["warnings"]


def test_gate_legacy_copy_warnings_filtered_per_account_before_next_sync(tmp_path):
    from opentradeledger.core.ledger_service import _collect_venue_warnings
    from opentradeledger.core.venue_health import VenueHealthTracker

    store = LedgerStore(tmp_path)
    tracker = VenueHealthTracker(store)
    raw = "copy_trading_probe: Gate GET /copy_trading/positions failed with HTTP 403: {'label': 'FORBIDDEN', 'message': 'permission not open yet'}"
    wallet = raw.replace("copy_trading_probe", "wallet_total").replace("/copy_trading/positions", "/wallet/total_balance")
    for account, warnings in [("gate", [raw]), ("gate__day", [raw, wallet])]:
        scope = store.venue_scope(account)
        scope.set_kv("live_bundle", {"_warnings": warnings})
        tracker.record_success(account, warnings=warnings)
        expected = [] if account == "gate" else [wallet]
        recorded = tracker.resolve_state(account, configured=True)
        assert recorded["warnings"] == expected
        assert _collect_venue_warnings(scope, recorded) == expected
