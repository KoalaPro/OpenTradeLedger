import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest

from opentradeledger.adapters.base import AdapterMeta, BaseExchangeAdapter
from opentradeledger.adapters.bybit.client import (
    BybitReadOnlyClient,
    normalize_bybit_closed_trades,
    sign_bybit_get,
)
from opentradeledger.adapters.okx.client import (
    OkxReadOnlyClient,
    normalize_okx_closed_trades,
    sign_okx_get,
)
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.security.readonly_guard import ReadOnlyViolationError
from opentradeledger.security.vault import ExchangeCredentialRecord


def test_default_registry_includes_all_seven_venues_and_supports_custom_plugins():
    reg = build_default_registry()
    ids = [m.venue_id for m in reg.list_all()]
    assert ids == [
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
    ]
    assert reg.get("okx").meta.requires_passphrase is True
    assert reg.get("kucoin").meta.requires_passphrase is True
    assert reg.get("hyperliquid").meta.auth_mode == "wallet_address"
    assert reg.get("bybit").meta.requires_passphrase is False

    class DummyKrakenAdapter(BaseExchangeAdapter):
        meta = AdapterMeta(
            venue_id="kraken",
            display_name="Kraken",
            display_name_zh="Kraken",
            color_token="kraken",
        )

        def create_client(self, cred):
            return None

        async def test_connection(self, cred):
            return {"ok": True}

    reg.register(DummyKrakenAdapter())
    assert reg.get("kraken") is not None
    assert "kraken" in [m.venue_id for m in reg.list_all()]


def test_okx_signature_readonly_guard_and_per_leg_fees():
    ts = "2026-11-23T12:00:00.000Z"
    path = "/api/v5/account/balance"
    secret = "test_okx_secret"
    expected = base64.b64encode(
        hmac.new(secret.encode(), f"{ts}GET{path}".encode(), hashlib.sha256).digest()
    ).decode()
    assert sign_okx_get(ts, path, secret) == expected

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"code": "0", "msg": "", "data": [{"totalEq": "5000.00"}]})

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = OkxReadOnlyClient(
                api_key="test_okx_key",
                api_secret=secret,
                passphrase="test_okx_passphrase",
                client=http,
                clock_iso=lambda: ts,
            )
            res = await client.get_account_balance()
            assert res == [{"totalEq": "5000.00"}]
            assert captured["method"] == "GET"
            assert captured["headers"]["ok-access-sign"] == expected
            assert captured["headers"]["ok-access-passphrase"] == "test_okx_passphrase"

            with pytest.raises(ReadOnlyViolationError):
                await client.request_readonly("POST", "/api/v5/trade/order")

    asyncio.run(_run())

    trades = normalize_okx_closed_trades(
        positions_history=[
            {
                "posId": "p1",
                "instId": "ETH-USDT-SWAP",
                "direction": "long",
                "openAvgPx": "2500",
                "closeAvgPx": "2600",
                "closeTotalPos": "2",
                "cTime": "1732000000000",
                "uTime": "1732003600000",
                "pnl": "200.0",
                "fee": "-3.5",
                "fundingFee": "0.0",
                "realizedPnl": "196.5",
            }
        ],
        fills=[
            {"instId": "ETH-USDT-SWAP", "side": "buy", "fee": "-1.0", "execType": "M", "ts": "1732000001000"},
            {"instId": "ETH-USDT-SWAP", "side": "sell", "fee": "-2.5", "execType": "T", "ts": "1732003599000"},
        ],
        rebate_ratio=0.4,
    )
    assert len(trades) == 1
    tr = trades[0]
    assert tr["symbol"] == "ETH_USDT"
    assert tr["entry_leg"]["gross_fee_usd"] == 1.0
    assert tr["entry_leg"]["rebate_usd"] == 0.4
    assert tr["entry_leg"]["net_fee_usd"] == 0.6
    assert tr["entry_leg"]["role"] == "MAKER"
    assert tr["exit_leg"]["gross_fee_usd"] == 2.5
    assert tr["exit_leg"]["rebate_usd"] == 1.0
    assert tr["exit_leg"]["net_fee_usd"] == 1.5
    assert tr["exit_leg"]["role"] == "TAKER"
    assert round(tr["entry_leg"]["net_fee_usd"] + tr["exit_leg"]["net_fee_usd"], 4) == tr["net_fee_usd"]


def test_bybit_signature_readonly_guard_and_per_leg_fees():
    ts = "1732000000000"
    key = "test_bybit_key"
    secret = "test_bybit_secret"
    recv = "5000"
    qs = "accountType=UNIFIED"
    expected = hmac.new(secret.encode(), f"{ts}{key}{recv}{qs}".encode(), hashlib.sha256).hexdigest()
    assert sign_bybit_get(ts, key, recv, qs, secret) == expected

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"retCode": 0, "retMsg": "OK", "result": {"list": [{"totalEquity": "8800"}]}})

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = BybitReadOnlyClient(
                api_key=key,
                api_secret=secret,
                recv_window=recv,
                client=http,
                clock_ms=lambda: int(ts),
            )
            wallet = await client.get_wallet_balance("UNIFIED")
            assert wallet["list"][0]["totalEquity"] == "8800"
            assert captured["headers"]["x-bapi-sign"] == expected

            with pytest.raises(ReadOnlyViolationError):
                await client.request_readonly("POST", "/v5/order/create")

    asyncio.run(_run())

    trades = normalize_bybit_closed_trades(
        closed_pnl_rows=[
            {
                "orderId": "ord-99",
                "symbol": "BTCUSDT",
                "side": "Sell",
                "qty": "0.5",
                "closedSize": "0.5",
                "avgEntryPrice": "80000",
                "avgExitPrice": "81000",
                "cumEntryValue": "40000",
                "cumExitValue": "40500",
                "openFee": "8.0",
                "closeFee": "22.275",
                "closedPnl": "469.725",
                "createdTime": "1732000000000",
                "updatedTime": "1732007200000",
            }
        ],
        executions=[
            {"orderId": "ord-99", "symbol": "BTCUSDT", "side": "Sell", "execFee": "22.275", "isMaker": False, "execTime": "1732007200000"},
            {"orderId": "ord-01", "symbol": "BTCUSDT", "side": "Buy", "execFee": "8.0", "isMaker": True, "closedSize": "0", "execTime": "1732000001000"},
        ],
        rebate_ratio=0.25,
    )
    assert len(trades) == 1
    tr = trades[0]
    assert tr["symbol"] == "BTC_USDT"
    assert tr["side"] == "LONG"
    assert tr["entry_leg"]["gross_fee_usd"] == 8.0
    assert tr["entry_leg"]["rebate_usd"] == 2.0
    assert tr["entry_leg"]["net_fee_usd"] == 6.0
    assert tr["entry_leg"]["role"] == "MAKER"
    assert tr["exit_leg"]["gross_fee_usd"] == 22.275
    assert tr["exit_leg"]["rebate_usd"] == pytest.approx(5.56875, abs=1e-4)
    assert tr["exit_leg"]["net_fee_usd"] == pytest.approx(16.70625, abs=1e-4)
    assert tr["exit_leg"]["role"] == "TAKER"
    assert round(tr["entry_leg"]["net_fee_usd"] + tr["exit_leg"]["net_fee_usd"], 4) == pytest.approx(tr["net_fee_usd"], abs=1e-4)


def test_all_seven_adapter_credentials_and_base_urls_match_client_expectations():
    reg = build_default_registry()

    async def _run():
        for vid in ("binance", "bitget", "gate", "mexc", "easicoin", "okx", "bybit"):
            adapter = reg.get(vid)
            assert adapter is not None
            cred = ExchangeCredentialRecord(
                venue_id=vid,
                api_key="demo_key",
                api_secret="demo_secret",
                passphrase="demo_passphrase" if adapter.meta.requires_passphrase else "",
            )
            client = adapter.create_client(cred)
            try:
                if vid == "gate":
                    assert client.credentials.base_url == "https://api.gateio.ws"
                    assert not client.credentials.base_url.endswith("/api/v4")
                elif vid == "binance":
                    assert client.credentials.base_url == "https://api.binance.com"
                    assert client.credentials.papi_base_url == "https://papi.binance.com"
                    assert client.credentials.fapi_base_url == "https://fapi.binance.com"
                elif vid == "bitget":
                    assert client.credentials.api_passphrase == "demo_passphrase"
                    assert client.credentials.passphrase == "demo_passphrase"
                elif vid == "mexc":
                    assert client.credentials.spot_base_url == "https://api.mexc.com"
                    assert client.credentials.futures_base_url == "https://contract.mexc.com"
                elif vid == "easicoin":
                    assert client.credentials.base_url == "https://api.easicoin.io"
            finally:
                if hasattr(client, "aclose"):
                    await client.aclose()

    asyncio.run(_run())
