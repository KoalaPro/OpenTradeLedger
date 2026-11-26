"""Dynamic exchange adapter registry — all 7 exchanges are 100% independent first-class modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence

from opentradeledger.adapters.base import (
    AdapterMeta,
    BaseExchangeAdapter,
    ExchangeCapabilities,
    VenueLedgerBundle,
    VenueSyncOutcome,
    _clean_uid_candidate,
)
from opentradeledger.adapters.binance.accounting import (
    build_binance_fee_matrix_row,
    build_binance_venue_ledger,
    extract_binance_spot_prices,
)
from opentradeledger.adapters.binance.client import BinanceReadOnlyClient
from opentradeledger.adapters.binance.sync import sync_binance_venue
from opentradeledger.adapters.bingx.accounting import (
    build_bingx_fee_matrix_row,
    build_bingx_venue_ledger,
    extract_bingx_spot_prices,
)
from opentradeledger.adapters.bingx.client import BingxReadOnlyClient
from opentradeledger.adapters.bingx.sync import sync_bingx_venue
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
from opentradeledger.adapters.htx.accounting import (
    build_htx_fee_matrix_row,
    build_htx_venue_ledger,
    extract_htx_spot_prices,
)
from opentradeledger.adapters.htx.client import HtxReadOnlyClient
from opentradeledger.adapters.htx.sync import sync_htx_venue
from opentradeledger.adapters.hyperliquid.accounting import (
    build_hyperliquid_fee_matrix_row,
    build_hyperliquid_venue_ledger,
    extract_hyperliquid_spot_prices,
)
from opentradeledger.adapters.hyperliquid.client import HyperliquidReadOnlyClient
from opentradeledger.adapters.hyperliquid.sync import sync_hyperliquid_venue
from opentradeledger.adapters.kucoin.accounting import (
    build_kucoin_fee_matrix_row,
    build_kucoin_venue_ledger,
    extract_kucoin_spot_prices,
)
from opentradeledger.adapters.kucoin.client import KucoinReadOnlyClient
from opentradeledger.adapters.kucoin.sync import sync_kucoin_venue
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
from opentradeledger.core.venue_health import IdentityProbeTransientError, is_transient_upstream_error
from opentradeledger.security.vault import ExchangeCredentialRecord

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore


class BinanceAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="binance",
        display_name="Binance",
        display_name_zh="币安",
        color_token="binance",
        identity_mode="probe",
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
            if isinstance(account, dict):
                uid = _clean_uid_candidate(account.get("uid"))
                if uid:
                    out["remote_uid"] = uid
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
            if hasattr(client, "aclose"):
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
        identity_mode="probe",
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
        try:
            balances = await client.get_account_balance()
            out: dict[str, Any] = {
                "ok": True,
                "venue_id": self.meta.venue_id,
                "read_only": True,
                "balances_count": len(balances),
            }
            try:
                cfg_rows = await client.request_readonly("GET", "/api/v5/account/config")
                if isinstance(cfg_rows, list) and cfg_rows and isinstance(cfg_rows[0], dict):
                    cfg = cfg_rows[0]
                    uid = _clean_uid_candidate(cfg.get("uid"))
                    main_uid = _clean_uid_candidate(cfg.get("mainUid"))
                    if uid:
                        out["remote_uid"] = uid
                        if main_uid:
                            out["account_role"] = "subaccount" if uid != main_uid else "master"
            except Exception as cfg_exc:
                if is_transient_upstream_error(cfg_exc):
                    raise IdentityProbeTransientError(str(cfg_exc)) from cfg_exc
            return out
        finally:
            if hasattr(client, "aclose"):
                await client.aclose()

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
        identity_mode="probe",
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
        try:
            wallet = await client.get_wallet_balance("UNIFIED")
            out: dict[str, Any] = {
                "ok": True,
                "venue_id": self.meta.venue_id,
                "read_only": True,
                "accounts": len(wallet.get("list") or []),
            }
            try:
                key_info = await client.get_api_key_info()
                if isinstance(key_info, dict):
                    uid = _clean_uid_candidate(key_info.get("userID"))
                    if uid:
                        out["remote_uid"] = uid
                    is_master = key_info.get("isMaster")
                    if isinstance(is_master, bool):
                        out["account_role"] = "master" if is_master else "subaccount"
                    if str(key_info.get("readOnly", "1")) == "0":
                        out["read_only"] = False
                        out["permission_warning"] = (
                            "API key has Read-Write permissions on Bybit; "
                            "OpenTradeLedger enforces GET-only locally, but Read-Only keys are recommended."
                        )
                        out["permission_warning_zh"] = (
                            "检测到该 Bybit API Key 为读写权限。虽然本程序本地强制拦截一切非 GET 请求，仍建议仅开启只读权限。"
                        )
            except Exception as key_exc:
                if is_transient_upstream_error(key_exc):
                    raise IdentityProbeTransientError(str(key_exc)) from key_exc
            return out
        finally:
            if hasattr(client, "aclose"):
                await client.aclose()

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
        identity_mode="probe",
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
            out: dict[str, Any] = {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "has_balance": total_amt is not None}
            try:
                det = await client.raw_get("/account/detail")
                if isinstance(det, dict):
                    uid = _clean_uid_candidate(det.get("user_id"))
                    if uid:
                        out["remote_uid"] = uid
            except Exception as det_exc:
                if is_transient_upstream_error(det_exc):
                    raise IdentityProbeTransientError(str(det_exc)) from det_exc
            if not out.get("remote_uid"):
                try:
                    fut = await client.raw_get("/futures/usdt/accounts")
                    if isinstance(fut, dict):
                        f_uid = _clean_uid_candidate(fut.get("user"))
                        if f_uid:
                            out["remote_uid"] = f_uid
                except Exception as fut_exc:
                    if is_transient_upstream_error(fut_exc):
                        raise IdentityProbeTransientError(str(fut_exc)) from fut_exc
            try:
                subs = await client.raw_get("/sub_accounts")
                if isinstance(subs, list):
                    out["account_role"] = "master"
            except Exception as sub_exc:
                if is_transient_upstream_error(sub_exc):
                    raise IdentityProbeTransientError(str(sub_exc)) from sub_exc
                msg = str(sub_exc).lower()
                if "sub_account" in msg or "sub-account" in msg or "subaccount" in msg:
                    out["account_role"] = "subaccount"
            return out
        finally:
            if hasattr(client, "aclose"):
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
        identity_mode="probe",
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
            except Exception as uta_exc:
                uta_low = str(uta_exc).lower()
                if is_transient_upstream_error(uta_exc) or any(
                    tok in uta_low
                    for tok in (
                        "40012",
                        "40009",
                        "400172",
                        "40014",
                        "40002",
                        "40006",
                        "40008",
                        "passphrase",
                        "signature",
                        "http 401",
                    )
                ):
                    raise
                try:
                    assets = await client.get("/api/v2/spot/account/assets")
                except Exception as v2_exc:
                    if "read-only violation" in str(v2_exc).lower():
                        assets = await client.get("/api/v2/account/all-account-balance")
                    else:
                        raise
                mode = "CLASSIC"
            count = len(assets) if isinstance(assets, list) else (1 if isinstance(assets, dict) else 0)
            out: dict[str, Any] = {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "account_mode": mode, "assets": count}
            try:
                info = await client.get("/api/v2/spot/account/info")
                if isinstance(info, dict):
                    uid = _clean_uid_candidate(info.get("userId"))
                    parent_id = _clean_uid_candidate(info.get("parentId"))
                    if uid:
                        out["remote_uid"] = uid
                        out["account_role"] = "subaccount" if (parent_id and parent_id != uid) else "master"
            except Exception as info_exc:
                if is_transient_upstream_error(info_exc):
                    raise IdentityProbeTransientError(str(info_exc)) from info_exc
            return out
        finally:
            if hasattr(client, "aclose"):
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
        identity_mode="probe",
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
            out: dict[str, Any] = {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "balances": len(balances or [])}
            if isinstance(account, dict):
                uid = _clean_uid_candidate(account.get("uid"))
                if uid:
                    out["remote_uid"] = uid
            return out
        finally:
            if hasattr(client, "aclose"):
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
        identity_mode="sync_bound",
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
            if hasattr(client, "aclose"):
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


class HyperliquidAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="hyperliquid",
        display_name="Hyperliquid",
        display_name_zh="Hyperliquid",
        color_token="hyperliquid",
        auth_mode="wallet_address",
        identity_mode="probe",
        requires_passphrase=False,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=False, copy_trading=False, stocks=True, tokenized_stocks=True
        ),
        api_base_url="https://api.hyperliquid.xyz",
        docs_url="https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> HyperliquidReadOnlyClient:
        return HyperliquidReadOnlyClient(wallet_address=cred.api_key)

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        try:
            ch = await client.get_clearinghouse_state()
            ms = ch.get("marginSummary") if isinstance(ch, dict) and isinstance(ch.get("marginSummary"), dict) else {}
            subs: list[dict[str, str]] = []
            try:
                subs = await client.get_subaccounts()
            except Exception as sub_exc:
                if is_transient_upstream_error(sub_exc):
                    raise IdentityProbeTransientError(str(sub_exc)) from sub_exc
            return {
                "ok": True,
                "venue_id": self.meta.venue_id,
                "read_only": True,
                "remote_uid": client.wallet_address,
                "account_role": cred.account_role or ("master" if subs else "wallet"),
                "account_value": float(ms.get("accountValue") or 0.0),
                "subaccounts": subs,
            }
        finally:
            if hasattr(client, "aclose"):
                await client.aclose()

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_hyperliquid_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        return isinstance(store.venue_scope("hyperliquid").get_kv("snapshot"), dict)

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_hyperliquid_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_hyperliquid_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_hyperliquid_fee_matrix_row(bundle, range_trades)


class KucoinAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="kucoin",
        display_name="KuCoin",
        display_name_zh="KuCoin",
        color_token="kucoin",
        identity_mode="probe",
        requires_passphrase=True,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=True, copy_trading=False, stocks=False, tokenized_stocks=False
        ),
        api_base_url="https://api-futures.kucoin.com",
        docs_url="https://www.kucoin.com/docs-new/introduction",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> KucoinReadOnlyClient:
        return KucoinReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret, passphrase=cred.passphrase)

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        try:
            try:
                ov = await client.get_futures_overview("USDT")
                has_eq = ov.get("accountEquity") is not None
            except Exception as fut_exc:
                fut_low = str(fut_exc).lower()
                if is_transient_upstream_error(fut_exc) or any(
                    c in fut_low for c in ("400001", "400003", "400004", "400005", "400006", "400007", "http 401")
                ):
                    raise
                accts = await client.get_spot_accounts()
                has_eq = isinstance(accts, list)
            out: dict[str, Any] = {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "has_balance": has_eq}
            key_info: dict[str, Any] = {}
            try:
                key_info = await client.get_api_key_info()
            except Exception as k_exc:
                if is_transient_upstream_error(k_exc):
                    raise IdentityProbeTransientError(str(k_exc)) from k_exc
            uinfo: dict[str, Any] = {}
            try:
                uinfo = await client.get_user_info()
            except Exception as u_exc:
                if is_transient_upstream_error(u_exc):
                    raise IdentityProbeTransientError(str(u_exc)) from u_exc
            raw_uid = (
                _clean_uid_candidate(key_info.get("uid"))
                or _clean_uid_candidate(key_info.get("userId"))
                or _clean_uid_candidate(uinfo.get("uid"))
                or _clean_uid_candidate(uinfo.get("userId"))
            )
            if raw_uid:
                out["remote_uid"] = raw_uid
                if isinstance(key_info.get("isMaster"), bool):
                    out["account_role"] = "master" if key_info["isMaster"] else "subaccount"
                elif key_info.get("subName") or uinfo.get("isSub") or uinfo.get("parentUid"):
                    out["account_role"] = "subaccount"
                else:
                    out["account_role"] = "master"
            perms = str(key_info.get("permission") or "")
            if any(tok in perms for tok in ("Trade", "Transfer", "Withdraw", "Earn")):
                out["read_only"] = False
                out["permission_warning"] = (
                    f"API key has write permissions enabled ({perms}); "
                    "OpenTradeLedger enforces GET-only locally, but restricting to General/Read-Only on KuCoin is recommended."
                )
                out["permission_warning_zh"] = (
                    f"检测到该 KuCoin API Key 含非只读权限（{perms}）。"
                    "虽然本程序本地强制拦截一切非 GET 请求，仍建议仅保留 General 查询权限。"
                )
            return out
        finally:
            if hasattr(client, "aclose"):
                await client.aclose()

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_kucoin_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        return isinstance(store.venue_scope("kucoin").get_kv("snapshot"), dict)

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_kucoin_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_kucoin_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_kucoin_fee_matrix_row(bundle, range_trades)


class BingxAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="bingx",
        display_name="BingX",
        display_name_zh="BingX",
        color_token="bingx",
        identity_mode="probe",
        requires_passphrase=False,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=False, copy_trading=True, stocks=True, tokenized_stocks=False
        ),
        api_base_url="https://open-api.bingx.com",
        docs_url="https://bingx-api.github.io/docs/",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> BingxReadOnlyClient:
        return BingxReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret)

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        try:
            bals = await client.get_swap_balance()
            out: dict[str, Any] = {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "accounts": len(bals)}
            try:
                uid = _clean_uid_candidate(await client.get_uid())
                if uid:
                    out["remote_uid"] = uid
            except Exception as uid_exc:
                if is_transient_upstream_error(uid_exc):
                    raise IdentityProbeTransientError(str(uid_exc)) from uid_exc
            return out
        finally:
            if hasattr(client, "aclose"):
                await client.aclose()

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_bingx_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        return isinstance(store.venue_scope("bingx").get_kv("snapshot"), dict)

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_bingx_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_bingx_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_bingx_fee_matrix_row(bundle, range_trades)


class HtxAdapter(BaseExchangeAdapter):
    meta = AdapterMeta(
        venue_id="htx",
        display_name="HTX",
        display_name_zh="火币 HTX",
        color_token="htx",
        identity_mode="probe",
        requires_passphrase=False,
        capabilities=ExchangeCapabilities(
            futures=True, rebates=True, token_discount=True, copy_trading=True, stocks=False, tokenized_stocks=False
        ),
        api_base_url="https://api.hbdm.com",
        docs_url="https://www.htx.com/en-us/opend/newApiPages/",
    )

    def create_client(self, cred: ExchangeCredentialRecord) -> HtxReadOnlyClient:
        return HtxReadOnlyClient(api_key=cred.api_key, api_secret=cred.api_secret)

    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        client = self.create_client(cred)
        try:
            try:
                sw = await client.get_swap_accounts()
                count = len(sw)
            except Exception as sw_exc:
                if is_transient_upstream_error(sw_exc):
                    raise
                sp = await client.get_spot_balances()
                count = len(sp)
            ident: dict[str, str] = {}
            try:
                ident = await client.get_identity()
            except Exception as id_exc:
                if is_transient_upstream_error(id_exc):
                    raise IdentityProbeTransientError(str(id_exc)) from id_exc
            out: dict[str, Any] = {"ok": True, "venue_id": self.meta.venue_id, "read_only": True, "accounts": count}
            h_uid = _clean_uid_candidate(ident.get("remote_uid"))
            if h_uid:
                out["remote_uid"] = h_uid
            if ident.get("account_role"):
                out["account_role"] = ident["account_role"]
            return out
        finally:
            if hasattr(client, "aclose"):
                await client.aclose()

    async def sync_venue(self, client: Any, store: "LedgerStore", *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
        return await sync_htx_venue(client, store, now_s=now_s, force_full=force_full)

    def has_venue_data(self, store: "LedgerStore") -> bool:
        return isinstance(store.venue_scope("htx").get_kv("snapshot"), dict)

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        return extract_htx_spot_prices(store)

    def build_venue_ledger(self, store: "LedgerStore", *, journals: Mapping[str, Mapping[str, Any]], now_s: float, global_spot_prices: Mapping[str, float] | None = None) -> VenueLedgerBundle | None:
        return build_htx_venue_ledger(store, self.meta, journals=journals, now_s=now_s, global_spot_prices=global_spot_prices)

    def build_fee_matrix_row(self, bundle: VenueLedgerBundle, range_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        return build_htx_fee_matrix_row(bundle, range_trades)


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
        HyperliquidAdapter(),
        KucoinAdapter(),
        BingxAdapter(),
        HtxAdapter(),
        EasiCoinAdapter(),
    ):
        registry.register(adapter)
    return registry
