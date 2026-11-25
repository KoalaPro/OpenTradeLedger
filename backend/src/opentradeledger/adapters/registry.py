"""Dynamic exchange adapter registry — all 7 exchanges are 100% independent first-class modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence

from opentradeledger.adapters.base import (
    AdapterMeta,
    BaseExchangeAdapter,
    ExchangeCapabilities,
    VenueLedgerBundle,
    VenueSyncOutcome,
)
from opentradeledger.adapters.binance.accounting import (
    build_binance_fee_matrix_row,
    build_binance_venue_ledger,
    extract_binance_spot_prices,
)
from opentradeledger.adapters.binance.client import BinanceReadOnlyClient
from opentradeledger.adapters.binance.sync import sync_binance_venue
from opentradeledger.adapters.bitget.accounting import (
    build_bitget_fee_matrix_row,
    build_bitget_venue_ledger,
    extract_bitget_spot_prices,
)
from opentradeledger.adapters.bitget.client import BitgetReadOnlyClient
from opentradeledger.adapters.bitget.sync import sync_bitget_venue
from opentradeledger.adapters.bybit.accounting import (
    build_bybit_fee_matrix_row,
    build_bybit_venue_ledger,
    extract_bybit_spot_prices,
)
from opentradeledger.adapters.bybit.client import BybitReadOnlyClient
from opentradeledger.adapters.bybit.sync import sync_bybit_venue
from opentradeledger.adapters.compat_creds import (
    BinanceCredentials,
    BitgetCredentials,
    EasiCoinCredentials,
    GateManualCredentials,
    MexcCredentials,
)
from opentradeledger.adapters.easicoin.accounting import (
    build_easicoin_fee_matrix_row,
    build_easicoin_venue_ledger,
    extract_easicoin_spot_prices,
)
from opentradeledger.adapters.easicoin.client import EasiCoinReadOnlyClient
from opentradeledger.adapters.easicoin.sync import sync_easicoin_venue
from opentradeledger.adapters.gate.accounting import (
    build_gate_fee_matrix_row,
    build_gate_venue_ledger,
    extract_gate_spot_prices,
)
from opentradeledger.adapters.gate.client import GateReadOnlyClient
from opentradeledger.adapters.gate.sync import sync_gate_venue
from opentradeledger.adapters.mexc.accounting import (
    build_mexc_fee_matrix_row,
    build_mexc_venue_ledger,
    extract_mexc_spot_prices,
)
from opentradeledger.adapters.mexc.client import MexcReadOnlyClient
from opentradeledger.adapters.mexc.sync import sync_mexc_venue
from opentradeledger.adapters.okx.accounting import (
    build_okx_fee_matrix_row,
    build_okx_venue_ledger,
    extract_okx_spot_prices,
)
from opentradeledger.adapters.okx.client import OkxReadOnlyClient
from opentradeledger.adapters.okx.sync import sync_okx_venue
from opentradeledger.security.vault import ExchangeCredentialRecord

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore


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
        return BinanceReadOnlyClient(BinanceCredentials(api_key=cred.api_key, api_secret=cred.api_secret))

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        try:
            account = await client.raw_get("/api/v3/account", [("omitZeroBalances", "true")])
            acct_type = account.get("accountType", "SPOT") if isinstance(account, dict) else "SPOT"
            non_readonly_perms: list[str] = []
            try:
                restr = await client.raw_get("/sapi/v1/account/apiRestrictions")
                if isinstance(restr, dict):
                    if restr.get("enableWithdrawals"):
                        non_readonly_perms.append("withdrawals")
                    if restr.get("enableFutures"):
                        non_readonly_perms.append("futures_trading")
                    if restr.get("enableSpotAndMarginTrading"):
                        non_readonly_perms.append("spot_trading")
            except Exception:
                pass
            out: dict[str, Any] = {
                "ok": True,
                "venue_id": self.meta.venue_id,
                "read_only": len(non_readonly_perms) == 0,
                "account_type": acct_type,
            }
            if non_readonly_perms:
                out["permission_warning"] = (
                    f"API key has write permissions enabled ({', '.join(non_readonly_perms)}); "
                    "OpenTradeLedger enforces GET-only locally, but restricting the key to Read-Only on Binance is recommended."
                )
                out["permission_warning_zh"] = (
                    f"检测到该 API Key 开启了非只读权限（{', '.join(non_readonly_perms)}）。"
                    "虽然本程序本地强制拦截一切非 GET 请求，仍建议在交易所后台仅勾选「只读」权限。"
                )
            return out
        finally:
            await client.aclose()

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_binance_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        sc = store.venue_scope("binance")
        return bool(sc.get_kv("live_bundle") or sc.count_records("fills") > 0 or sc.count_records("income") > 0)

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_binance_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_binance_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_binance_fee_matrix_row(bundle, range_trades)


class OkxAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="okx",
        display_name="OKX",
        display_name_zh="OKX",
        color_token="okx",
        requires_passphrase=True,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=True, copy_trading=True, stocks=True, tokenized_stocks=True
        ),
        api_base_url="https://www.okx.com",
        docs_url="https://www.okx.com/docs-v5/en/",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> OkxReadOnlyClient:
        return OkxReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret, passphrase=cred.passphrase)

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        balances = await client.get_account_balance()
        return {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "balances_count": len(balances)}

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_okx_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        return isinstance(store.venue_scope("okx").get_kv("snapshot"), dict)

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_okx_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_okx_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_okx_fee_matrix_row(bundle, range_trades)


class BybitAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="bybit",
        display_name="Bybit",
        display_name_zh="Bybit",
        color_token="bybit",
        requires_passphrase=False,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=True, copy_trading=True, stocks=True, tokenized_stocks=True
        ),
        api_base_url="https://api.bybit.com",
        docs_url="https://bybit-exchange.github.io/docs/v5/intro",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> BybitReadOnlyClient:
        return BybitReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret)

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        wallet = await client.get_wallet_balance("UNIFIED")
        out: dict[str, Any] = {
            "ok": True,
            "venue_id": self.meta.venue_id,
            "read_only": True,
            "accounts": len(wallet.get("list") or []),
        }
        try:
            key_info = await client.get_api_key_info()
            if isinstance(key_info, dict) and str(key_info.get("readOnly", "1")) == "0":
                out["read_only"] = False
                out["permission_warning"] = (
                    "API key has Read-Write permissions on Bybit; "
                    "OpenTradeLedger enforces GET-only locally, but Read-Only keys are recommended."
                )
                out["permission_warning_zh"] = (
                    "检测到该 Bybit API Key 为读写权限。虽然本程序本地强制拦截一切非 GET 请求，仍建议仅开启只读权限。"
                )
        except Exception:
            pass
        return out

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_bybit_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        return isinstance(store.venue_scope("bybit").get_kv("snapshot"), dict)

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_bybit_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_bybit_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_bybit_fee_matrix_row(bundle, range_trades)


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

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_gate_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        sc = store.venue_scope("gate")
        return bool(sc.get_kv("live_bundle") or sc.count_records("futures_closes") > 0 or sc.count_records("spot_account_book") > 0)

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_gate_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_gate_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_gate_fee_matrix_row(bundle, range_trades)


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

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_bitget_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        sc = store.venue_scope("bitget")
        return bool(sc.get_kv("live_bundle") or sc.count_records("positions_history") > 0 or sc.count_records("bills") > 0)

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_bitget_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_bitget_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_bitget_fee_matrix_row(bundle, range_trades)


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

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_mexc_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        sc = store.venue_scope("mexc")
        return bool(sc.get_kv("live_bundle") or sc.count_records("positions_history") > 0 or sc.count_records("transfers") > 0)

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_mexc_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_mexc_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_mexc_fee_matrix_row(bundle, range_trades)


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

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_easicoin_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        sc = store.venue_scope("easicoin")
        return bool(
            sc.get_kv("live_bundle") or sc.get_kv("account_uid")
            or any(sc.count_records(stream) for stream in ("closed_pnl", "fills", "orders", "transfers"))
        )

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_easicoin_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_easicoin_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_easicoin_fee_matrix_row(bundle, range_trades)


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, BaseExchangeAdapter] = {}

    def register(self, adapter: BaseExchangeAdapter) -> None:
        self._adapters[adapter.meta.venue_id.lower()] = adapter

    def get(self, venue_id: str) -> BaseExchangeAdapter | None:
        return self._adapters.get((venue_id or "").strip().lower())

    def iter_adapters(self) -> list[BaseExchangeAdapter]:
        return list(self._adapters.values())

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
