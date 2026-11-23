"""Dynamic exchange adapter registry (zero hardcoded venue counts in frontend or core)."""

from __future__ import annotations

from typing import Any

from opentradeledger.adapters.base import AdapterMeta, BaseExchangeAdapter, ExchangeCapabilities
from opentradeledger.adapters.binance.client import BinanceReadOnlyClient
from opentradeledger.adapters.bitget.client import BitgetReadOnlyClient
from opentradeledger.adapters.bybit.client import BybitReadOnlyClient
from opentradeledger.adapters.compat_creds import (
    BinanceCredentials,
    BitgetCredentials,
    EasiCoinCredentials,
    GateManualCredentials,
    MexcCredentials,
)
from opentradeledger.adapters.easicoin.client import EasiCoinReadOnlyClient
from opentradeledger.adapters.gate.client import GateReadOnlyClient
from opentradeledger.adapters.mexc.client import MexcReadOnlyClient
from opentradeledger.adapters.okx.client import OkxReadOnlyClient
from opentradeledger.security.vault import ExchangeCredentialRecord


class BinanceAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="binance",
        display_name="Binance",
        display_name_zh="币安",
        color_token="binance",
        requires_passphrase=False,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=True, copy_trading=True, stocks=True, tokenized_stocks=True
        ),
        api_base_url="https://papi.binance.com",
        docs_url="https://developers.binance.com/",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> BinanceReadOnlyClient:
        return BinanceReadOnlyClient(
            BinanceCredentials(api_key=cred.api_key, api_secret=cred.api_secret)
        )

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        try:
            account = await client.raw_get("/api/v3/account", [("omitZeroBalances", "true")])
            acct_type = account.get("accountType", "SPOT") if isinstance(account, dict) else "SPOT"
            return {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "account_type": acct_type}
        finally:
            await client.aclose()


class OkxAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="okx",
        display_name="OKX",
        display_name_zh="OKX",
        color_token="okx",
        requires_passphrase=True,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=False, copy_trading=True, stocks=False, tokenized_stocks=False
        ),
        api_base_url="https://www.okx.com",
        docs_url="https://www.okx.com/docs-v5/en/",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> OkxReadOnlyClient:
        return OkxReadOnlyClient(
            api_key=cred.api_key,
            api_secret=cred.api_secret,
            passphrase=cred.passphrase,
        )

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        balances = await client.get_account_balance()
        return {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "balances_count": len(balances)}


class BybitAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="bybit",
        display_name="Bybit",
        display_name_zh="Bybit",
        color_token="bybit",
        requires_passphrase=False,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=False, copy_trading=True, stocks=False, tokenized_stocks=False
        ),
        api_base_url="https://api.bybit.com",
        docs_url="https://bybit-exchange.github.io/docs/v5/intro",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> BybitReadOnlyClient:
        return BybitReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret)

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        wallet = await client.get_wallet_balance("UNIFIED")
        return {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "accounts": len(wallet.get("list") or [])}


class GateAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="gate",
        display_name="Gate",
        display_name_zh="Gate",
        color_token="gate",
        requires_passphrase=False,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=False, copy_trading=True, stocks=True, tokenized_stocks=True
        ),
        api_base_url="https://api.gateio.ws/api/v4",
        docs_url="https://www.gate.io/docs/developers/apiv4/",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> GateReadOnlyClient:
        return GateReadOnlyClient(GateManualCredentials(api_key=cred.api_key, api_secret=cred.api_secret))

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        try:
            wt = await client.raw_get("/wallet/total_balance")
            total_amt = ((wt or {}).get("total") or {}).get("amount") if isinstance(wt, dict) else None
            return {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "has_balance": total_amt is not None}
        finally:
            await client.aclose()


class BitgetAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="bitget",
        display_name="Bitget",
        display_name_zh="Bitget",
        color_token="bitget",
        requires_passphrase=True,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=True, copy_trading=True, stocks=True, tokenized_stocks=True
        ),
        api_base_url="https://api.bitget.com",
        docs_url="https://www.bitget.com/api-doc/common/intro",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> BitgetReadOnlyClient:
        return BitgetReadOnlyClient(
            BitgetCredentials(api_key=cred.api_key, api_secret=cred.api_secret, passphrase=cred.passphrase)
        )

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        try:
            try:
                assets = await client.get("/api/v3/account/assets")
                mode = "UTA"
            except Exception:
                assets = await client.get("/api/v2/spot/account/assets")
                mode = "CLASSIC"
            count = len(assets) if isinstance(assets, list) else (1 if isinstance(assets, dict) else 0)
            return {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "account_mode": mode, "assets": count}
        finally:
            await client.aclose()


class MexcAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="mexc",
        display_name="MEXC",
        display_name_zh="MEXC",
        color_token="mexc",
        requires_passphrase=False,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=True, copy_trading=True, stocks=True, tokenized_stocks=True
        ),
        api_base_url="https://api.mexc.com",
        docs_url="https://mexcdevelop.github.io/apidocs/spot_v3_en/",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> MexcReadOnlyClient:
        return MexcReadOnlyClient(MexcCredentials(api_key=cred.api_key, api_secret=cred.api_secret))

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        try:
            account = await client.spot_request("GET", "/api/v3/account", signed=True)
            balances = account.get("balances") if isinstance(account, dict) else []
            return {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "balances": len(balances or [])}
        finally:
            await client.aclose()


class EasiCoinAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="easicoin",
        display_name="EasiCoin",
        display_name_zh="易币",
        color_token="easicoin",
        requires_passphrase=False,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=False, copy_trading=True, stocks=False, tokenized_stocks=True
        ),
        api_base_url="https://api.easicoin.io",
        docs_url="https://www.easicoin.io/",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> EasiCoinReadOnlyClient:
        return EasiCoinReadOnlyClient(EasiCoinCredentials(api_key=cred.api_key, api_secret=cred.api_secret))

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        try:
            bal = await client.raw_get("/futures/private/v1/account/balance")
            return {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "status": "ok" if bal is not None else "empty"}
        finally:
            await client.aclose()


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, BaseExchangeAdapter] = {}

    def register(self, adapter: BaseExchangeAdapter) -> None:
        self._adapters[adapter.meta.venue_id.lower()] = adapter

    def get(self, venue_id: str) -> BaseExchangeAdapter | None:
        return self._adapters.get((venue_id or "").strip().lower())

    def list_all(self) -> list[AdapterMeta]:
        return [adapter.meta for adapter in self._adapters.values()]


def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    for adapter in (
        BinanceAdapter(),
        OkxAdapter(),
        BybitAdapter(),
        GateAdapter(),
        BitgetAdapter(),
        MexcAdapter(),
        EasiCoinAdapter(),
    ):
        registry.register(adapter)
    return registry
