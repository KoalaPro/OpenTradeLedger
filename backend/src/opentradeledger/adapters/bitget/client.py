"""Strictly read-only Bitget REST client (`GET` only, explicit path whitelist).

Supports both Bitget Unified Account (`UTA /api/v3/*`) and Classic Account (`/api/v2/*`),
including live Funding Account assets (`funding-assets`), real-time fee-rate discovery
(`/api/v3/account/fee-rate`), BGB fee deduction savings, order history (`delegateType`
stop-loss/take-profit), and full real-time partner rebate bills (`funding-financial-records`
+ `tax/spot-record`).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlencode

import httpx

from opentradeledger.adapters.compat_creds import BitgetCredentials
from opentradeledger.adapters.stock_channels import classify_spot_stock_token
from opentradeledger.security.readonly_guard import ReadOnlyGuardError

logger = logging.getLogger("gate_manual.bitget")

BG_WINDOW_MS = 29 * 86400 * 1000
BG_TAX_WINDOW_MS = 7 * 86400 * 1000 - 1000

_ALLOWED_GET_PATHS = frozenset(
    {
        # Public market quotes
        "/api/v2/spot/market/tickers",
        "/api/v2/mix/market/tickers",
        # Unified Account (UTA v3)
        "/api/v3/account/assets",
        "/api/v3/account/funding-assets",
        "/api/v3/account/settings",
        "/api/v3/account/fee-rate",
        "/api/v3/user/fee-rate",
        "/api/v3/position/current-position",
        "/api/v3/position/history-position",
        "/api/v3/trade/unfilled-orders",
        "/api/v3/trade/history-orders",
        "/api/v3/trade/fills",
        "/api/v3/account/financial-records",
        "/api/v3/account/funding-financial-records",
        "/api/v3/account/deposit-records",
        "/api/v3/account/withdrawal-records",
        "/api/v3/tax/records",
        # Full-year tax / rebate ledger (read-only)
        "/api/v2/tax/spot-record",
        "/api/v2/tax/future-record",
        # Classic Account (v2) fallback & wallet deposit/withdraw
        "/api/v2/spot/account/info",
        "/api/v2/account/all-account-balance",
        "/api/v2/common/trade-rate",
        "/api/v2/mix/account/accounts",
        "/api/v2/mix/position/all-position",
        "/api/v2/mix/position/history-position",
        "/api/v2/mix/order/orders-pending",
        "/api/v2/mix/order/orders-history",
        "/api/v2/mix/order/fills",
        "/api/v2/mix/account/bill",
        "/api/v2/spot/account/bills",
        "/api/v2/spot/wallet/deposit-records",
        "/api/v2/spot/wallet/withdrawal-records",
        "/api/v2/spot/trade/fills",
        # Bitget Stock+ (Real US Stocks & ETFs sub-account, strictly read-only GET)
        "/api/v3/stockplus/asset/account",
        "/api/v3/stockplus/asset/stock-position",
        "/api/v3/stockplus/asset/cash-flow",
        "/api/v3/stockplus/asset/transfer-records",
        "/api/v3/stockplus/trade/today-orders",
        "/api/v3/stockplus/trade/history-orders",
        "/api/v3/stockplus/trade/today-executions",
        "/api/v3/stockplus/trade/history-executions",
    }
)


class BitgetApiError(RuntimeError):
    """Raised on HTTP >= 400 or non-00000 Bitget response code."""

    def __init__(self, status_code: int, path: str, code: str, detail: Any) -> None:
        self.status_code = status_code
        self.path = path
        self.code = code
        self.detail = detail
        super().__init__(f"Bitget GET {path} failed ({status_code} / {code}): {detail}")


def _is_fatal_bitget_error(exc: BaseException) -> bool:
    if isinstance(exc, ReadOnlyGuardError):
        return True
    if isinstance(exc, (httpx.TransportError, TimeoutError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, BitgetApiError):
        if exc.status_code == 0 or exc.status_code == 429 or exc.status_code >= 500:
            return True
        if str(exc.code or "").strip() in {"429", "40015", "40010", "50001", "50004", "50011"}:
            return True
    low = str(exc).lower()
    return any(
        tok in low
        for tok in (
            "http 5",
            "http 429",
            "rate limit",
            "too frequent",
            "too many",
            "busy",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "internal server error",
            "system is abnormal",
            "timed out",
            "timeout",
        )
    )


class BitgetReadOnlyClient:
    """Async read-only Bitget client enforcing `GET`-only requests and path whitelisting."""

    def __init__(
        self,
        credentials: BitgetCredentials,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        clock_ms: Optional[Callable[[], int]] = None,
        timeout_sec: float = 20.0,
    ) -> None:
        self.credentials = credentials
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout_sec)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def build_signed_headers(
        self,
        method: str,
        path: str,
        query_string: str = "",
        timestamp_ms: Optional[int] = None,
    ) -> dict[str, str]:
        verb = (method or "").upper()
        if verb != "GET":
            raise ReadOnlyGuardError(
                f"READ-ONLY VIOLATION: attempted non-GET HTTP method {verb!r} on Bitget {path!r}"
            )
        if path not in _ALLOWED_GET_PATHS:
            raise ReadOnlyGuardError(
                f"READ-ONLY VIOLATION: path {path!r} is not in Bitget read-only whitelist"
            )
        ts = str(int(timestamp_ms if timestamp_ms is not None else self._clock_ms()))
        req_path = f"{path}?{query_string}" if query_string else path
        prehash = f"{ts}GET{req_path}"
        digest = hmac.new(
            self.credentials.api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        sig = base64.b64encode(digest).decode("utf-8")
        return {
            "ACCESS-KEY": self.credentials.api_key,
            "ACCESS-SIGN": sig,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.credentials.api_passphrase,
            "Content-Type": "application/json",
            "locale": "zh-CN",
        }

    async def request(
        self,
        method: str,
        path: str,
        params: Optional[Sequence[tuple[str, Any]] | dict[str, Any]] = None,
        *,
        signed: bool = True,
    ) -> Any:
        verb = (method or "").upper()
        if verb != "GET":
            raise ReadOnlyGuardError(
                f"READ-ONLY VIOLATION: attempted non-GET HTTP method {verb!r} on Bitget {path!r}"
            )
        if path not in _ALLOWED_GET_PATHS:
            raise ReadOnlyGuardError(
                f"READ-ONLY VIOLATION: path {path!r} is not in Bitget read-only whitelist"
            )

        pairs: list[tuple[str, Any]] = []
        if isinstance(params, dict):
            for k in sorted(params.keys()):
                v = params[k]
                if v is not None and v != "":
                    pairs.append((k, v))
        elif params:
            pairs = [(k, v) for k, v in params if v is not None and v != ""]

        query_str = urlencode(pairs)
        url = f"{self.credentials.base_url}{path}" + (f"?{query_str}" if query_str else "")

        for attempt in range(4):
            headers = (
                self.build_signed_headers("GET", path, query_str)
                if signed
                else {"Accept": "application/json"}
            )
            resp = await self._client.request("GET", url, headers=headers)
            try:
                body = resp.json()
            except ValueError:
                body = {"raw": resp.text[:240]}
            code = str(body.get("code") or "") if isinstance(body, dict) else ""
            if code == "429" and attempt < 3:
                await asyncio.sleep((0.65 * (attempt + 1)) if self._owns_client else 0.01)
                continue
            if resp.status_code >= 400 or (signed and code and code != "00000"):
                raise BitgetApiError(resp.status_code, path, code, body)
            return body.get("data") if isinstance(body, dict) and "data" in body else body

        raise BitgetApiError(429, path, "429", "Bitget rate limit exceeded")

    async def get(
        self,
        path: str,
        params: Optional[Sequence[tuple[str, Any]] | dict[str, Any]] = None,
        *,
        signed: bool = True,
    ) -> Any:
        return await self.request("GET", path, params=params, signed=signed)

    async def _safe_get(
        self,
        path: str,
        params: Optional[Sequence[tuple[str, Any]] | dict[str, Any]] = None,
        *,
        signed: bool = True,
        default: Any = None,
        field_name: str = "",
        failed_fields: Optional[list[str]] = None,
        warnings: Optional[list[str]] = None,
    ) -> Any:
        try:
            return await self.get(path, params=params, signed=signed)
        except Exception as exc:
            if _is_fatal_bitget_error(exc):
                raise
            logger.debug("Bitget optional GET %s skipped: %s", path, exc)
            if warnings is not None:
                warnings.append(f"{field_name or path}: {exc}")
            if failed_fields is not None and field_name and field_name not in failed_fields:
                failed_fields.append(field_name)
            return default

    async def fetch_live_overview_bundle(self) -> dict[str, Any]:
        """Auto-detect UTA v3 vs Classic v2 and fetch live account assets (UTA + Funding), positions, and quotes."""
        warnings: list[str] = []
        failed_fields: list[str] = []
        spot_tickers_raw = await self._safe_get(
            "/api/v2/spot/market/tickers",
            signed=False,
            default=[],
            field_name="spot_tickers",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        spot_tickers: dict[str, str] = {"USDT": "1", "USDC": "1", "FDUSD": "1", "USD1": "1"}
        if isinstance(spot_tickers_raw, list):
            for tk in spot_tickers_raw:
                if not isinstance(tk, dict):
                    continue
                sym = str(tk.get("symbol") or "").upper()
                lp = str(tk.get("lastPr") or tk.get("close") or "")
                if sym.endswith("USDT") and lp:
                    spot_tickers[sym[:-4]] = lp

        try:
            uta_assets = await self.get("/api/v3/account/assets")
            account_mode = "UTA"
        except Exception as exc:
            if _is_fatal_bitget_error(exc):
                raise
            if isinstance(exc, BitgetApiError) and (
                exc.status_code == 401
                or exc.code
                in (
                    "40012",
                    "40009",
                    "400172",
                    "40014",
                    "40001",
                    "40002",
                    "40003",
                    "40004",
                    "40005",
                    "40006",
                    "40007",
                    "40008",
                    "40013",
                    "40037",
                )
            ):
                raise
            uta_assets = None
            account_mode = "CLASSIC"

        if account_mode == "UTA":
            settings = await self._safe_get(
                "/api/v3/account/settings",
                default={},
                field_name="settings",
                failed_fields=failed_fields,
                warnings=warnings,
            )
            funding_assets = await self._safe_get(
                "/api/v3/account/funding-assets",
                default=[],
                field_name="funding_assets",
                failed_fields=failed_fields,
                warnings=warnings,
            )
            cur_pos = await self._safe_get(
                "/api/v3/position/current-position",
                [("category", "USDT-FUTURES")],
                default={},
                field_name="futures_positions",
                failed_fields=failed_fields,
                warnings=warnings,
            )
            open_ords_res = await self._safe_get(
                "/api/v3/trade/unfilled-orders",
                [("category", "USDT-FUTURES")],
                default={},
                field_name="futures_open_orders",
                failed_fields=failed_fields,
                warnings=warnings,
            )
            open_ords = (
                (open_ords_res.get("list") or [])
                if isinstance(open_ords_res, dict)
                else (open_ords_res if isinstance(open_ords_res, list) else [])
            )
            pos_list = (
                (cur_pos.get("list") or [])
                if isinstance(cur_pos, dict)
                else (cur_pos if isinstance(cur_pos, list) else [])
            )
            uid_val = ""
            if isinstance(settings, dict):
                uid_val = str(settings.get("uid") or settings.get("userId") or "")
            if not uid_val and isinstance(uta_assets, dict):
                uid_val = str(uta_assets.get("uid") or uta_assets.get("userId") or "")
            funding_list = [dict(a) for a in (funding_assets or []) if isinstance(a, dict)]
            stock_bundle = await self._fetch_stock_bundle(
                account_mode="UTA",
                uta_assets=uta_assets,
                funding_assets=funding_list,
                classic_balances=[],
                failed_fields=failed_fields,
                warnings=warnings,
            )
            out_uta: dict[str, Any] = {
                "uid": uid_val,
                "account_mode": "UTA",
                "settings": dict(settings) if isinstance(settings, dict) else {},
                "uta_assets": uta_assets if isinstance(uta_assets, (dict, list)) else {},
                "funding_assets": funding_list,
                "classic_balances": [],
                "futures_accounts": [],
                "futures_positions": [dict(p) for p in (pos_list or []) if isinstance(p, dict)],
                "futures_open_orders": [dict(o) for o in (open_ords or []) if isinstance(o, dict)],
                "spot_tickers": spot_tickers,
                "stock_bundle": stock_bundle,
            }
            if failed_fields:
                out_uta["_failed_fields"] = failed_fields
            if warnings:
                out_uta["_warnings"] = warnings
            return out_uta

        info = await self._safe_get(
            "/api/v2/spot/account/info",
            default={},
            field_name="settings",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        all_bal = await self._safe_get(
            "/api/v2/account/all-account-balance",
            default=[],
            field_name="classic_balances",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        mix_acc = await self._safe_get(
            "/api/v2/mix/account/accounts",
            [("productType", "USDT-FUTURES")],
            default=[],
            field_name="futures_accounts",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        mix_pos = await self._safe_get(
            "/api/v2/mix/position/all-position",
            [("productType", "USDT-FUTURES"), ("marginCoin", "USDT")],
            default=[],
            field_name="futures_positions",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        mix_open_res = await self._safe_get(
            "/api/v2/mix/order/orders-pending",
            [("productType", "USDT-FUTURES")],
            default={},
            field_name="futures_open_orders",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        mix_open = (
            (mix_open_res.get("entrustedList") or [])
            if isinstance(mix_open_res, dict)
            else (mix_open_res if isinstance(mix_open_res, list) else [])
        )
        classic_list = [dict(b) for b in (all_bal or []) if isinstance(b, dict)]
        stock_bundle_classic = await self._fetch_stock_bundle(
            account_mode="CLASSIC",
            uta_assets={},
            funding_assets=[],
            classic_balances=classic_list,
            failed_fields=failed_fields,
            warnings=warnings,
        )
        out_classic: dict[str, Any] = {
            "uid": str((info or {}).get("userId") or (info or {}).get("uid") or ""),
            "account_mode": "CLASSIC",
            "settings": dict(info) if isinstance(info, dict) else {},
            "uta_assets": {},
            "funding_assets": [],
            "classic_balances": classic_list,
            "futures_accounts": [dict(a) for a in (mix_acc or []) if isinstance(a, dict)],
            "futures_positions": [dict(p) for p in (mix_pos or []) if isinstance(p, dict)],
            "futures_open_orders": [dict(o) for o in (mix_open or []) if isinstance(o, dict)],
            "spot_tickers": spot_tickers,
            "stock_bundle": stock_bundle_classic,
        }
        if failed_fields:
            out_classic["_failed_fields"] = failed_fields
        if warnings:
            out_classic["_warnings"] = warnings
        return out_classic

    async def _fetch_stock_bundle(
        self,
        *,
        account_mode: str,
        uta_assets: Any,
        funding_assets: Sequence[dict[str, Any]],
        classic_balances: Sequence[dict[str, Any]],
        failed_fields: Optional[list[str]] = None,
        warnings: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Fetch Bitget Stock+ real US stock sub-account & Spot rToken fills (graceful degradation if unopened)."""
        sp_acc = await self._safe_get(
            "/api/v3/stockplus/asset/account",
            default=None,
            field_name="stock_bundle",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        sp_pos_raw = await self._safe_get(
            "/api/v3/stockplus/asset/stock-position",
            default=None,
            field_name="stock_bundle",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        sp_positions = (
            (sp_pos_raw.get("list") or [])
            if isinstance(sp_pos_raw, dict)
            else (sp_pos_raw if isinstance(sp_pos_raw, list) else [])
        )
        sp_executions: list[dict[str, Any]] = []
        sp_cashflows: list[dict[str, Any]] = []
        sp_transfers: list[dict[str, Any]] = []

        has_sp_equity = False
        if isinstance(sp_acc, dict):
            has_sp_equity = any(
                float(sp_acc.get(k) or 0.0) > 0
                for k in ("totalEquity", "netLiquidationValue", "usdtEquity", "equity", "cashBalance", "available")
            )
        elif isinstance(sp_acc, list) and sp_acc:
            has_sp_equity = True

        if has_sp_equity or sp_positions:
            td_ex = await self._safe_get("/api/v3/stockplus/trade/today-executions", [("limit", 100)], default={})
            hs_ex = await self._safe_get("/api/v3/stockplus/trade/history-executions", [("limit", 100)], default={})
            cf_res = await self._safe_get("/api/v3/stockplus/asset/cash-flow", [("limit", 100)], default={})
            tr_res = await self._safe_get("/api/v3/stockplus/asset/transfer-records", [("limit", 100)], default={})

            for src_ex in (td_ex, hs_ex):
                rows_ex = (src_ex.get("list") or []) if isinstance(src_ex, dict) else (src_ex if isinstance(src_ex, list) else [])
                for rx in rows_ex or []:
                    if isinstance(rx, dict):
                        sp_executions.append(dict(rx))
            rows_cf = (cf_res.get("list") or []) if isinstance(cf_res, dict) else (cf_res if isinstance(cf_res, list) else [])
            for rcf in rows_cf or []:
                if isinstance(rcf, dict):
                    sp_cashflows.append(dict(rcf))
            rows_tr = (tr_res.get("list") or []) if isinstance(tr_res, dict) else (tr_res if isinstance(tr_res, list) else [])
            for rtr in rows_tr or []:
                if isinstance(rtr, dict):
                    sp_transfers.append(dict(rtr))

        held_rtokens: set[str] = set()
        uta_arr = (
            (uta_assets.get("assets") or uta_assets.get("list") or [])
            if isinstance(uta_assets, dict)
            else (uta_assets if isinstance(uta_assets, list) else [])
        )
        for ca in uta_arr:
            if isinstance(ca, dict) and float(ca.get("equity") or ca.get("available") or 0.0) > 0:
                c_nm = str(ca.get("coin") or "").strip()
                if classify_spot_stock_token(c_nm, exchange="BITGET"):
                    held_rtokens.add(c_nm.upper())
        for fa in funding_assets:
            if float(fa.get("balance") or fa.get("available") or 0.0) > 0:
                c_nm = str(fa.get("coin") or "").strip()
                if classify_spot_stock_token(c_nm, exchange="BITGET"):
                    held_rtokens.add(c_nm.upper())
        for cb in classic_balances:
            c_nm = str(cb.get("coin") or "").strip()
            if float(cb.get("available") or cb.get("usdtBalance") or 0.0) > 0 and classify_spot_stock_token(c_nm, exchange="BITGET"):
                held_rtokens.add(c_nm.upper())

        spot_stock_fills: dict[str, list[dict[str, Any]]] = {}
        for tok in sorted(held_rtokens)[:10]:
            if account_mode == "UTA":
                f_res = await self._safe_get(
                    "/api/v3/trade/fills",
                    [("category", "SPOT"), ("symbol", f"{tok}USDT"), ("limit", 100)],
                    default={},
                )
            else:
                f_res = await self._safe_get(
                    "/api/v2/spot/trade/fills",
                    [("symbol", f"{tok}USDT"), ("limit", 100)],
                    default=[],
                )
            f_rows = (f_res.get("list") or []) if isinstance(f_res, dict) else (f_res if isinstance(f_res, list) else [])
            spot_stock_fills[tok] = [dict(r) for r in (f_rows or []) if isinstance(r, dict)]

        if account_mode == "UTA":
            spot_fr = await self._safe_get(
                "/api/v3/account/fee-rate",
                [("category", "SPOT"), ("symbol", "RNVDAUSDT")],
                default={},
            )
            if not isinstance(spot_fr, dict) or (
                spot_fr.get("makerFeeRate") is None and spot_fr.get("takerFeeRate") is None
            ):
                spot_fr = await self._safe_get(
                    "/api/v3/user/fee-rate",
                    [("category", "SPOT"), ("symbol", "RNVDAUSDT")],
                    default={},
                )
        else:
            spot_fr = await self._safe_get(
                "/api/v2/common/trade-rate",
                [("symbol", "RNVDAUSDT"), ("businessType", "spot")],
                default={},
            )

        return {
            "stockplus_account": dict(sp_acc) if isinstance(sp_acc, dict) else (list(sp_acc) if isinstance(sp_acc, list) else {}),
            "stockplus_positions": [dict(p) for p in (sp_positions or []) if isinstance(p, dict)],
            "stockplus_executions": sp_executions,
            "stockplus_cashflows": sp_cashflows,
            "stockplus_transfers": sp_transfers,
            "spot_stock_fills": spot_stock_fills,
            "spot_stock_fee_rate": dict(spot_fr) if isinstance(spot_fr, dict) else {},
        }

    async def _paginate_uta_cursor(
        self,
        path: str,
        base_params: Sequence[tuple[str, Any]],
        *,
        max_pages: int = 25,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(max_pages):
            q = list(base_params)
            if cursor:
                q.append(("cursor", cursor))
            res = await self._safe_get(path, q, default={})
            if not isinstance(res, dict):
                if isinstance(res, list):
                    out.extend(dict(r) for r in res if isinstance(r, dict))
                break
            rows = res.get("list") or []
            if not rows:
                break
            out.extend(dict(r) for r in rows if isinstance(r, dict))
            cursor = res.get("cursor")
            if not cursor:
                break
        return out

    async def fetch_history_positions(
        self,
        *,
        since_sec: int,
        account_mode: str = "UTA",
    ) -> list[dict[str, Any]]:
        if account_mode == "UTA":
            return await self._paginate_uta_cursor(
                "/api/v3/position/history-position",
                [("category", "USDT-FUTURES"), ("limit", 100)],
            )
        now_ms = self._clock_ms()
        t_cur = max(0, int(since_sec) * 1000)
        out: list[dict[str, Any]] = []
        while t_cur < now_ms:
            t_end = min(now_ms, t_cur + BG_WINDOW_MS)
            res = await self._safe_get(
                "/api/v2/mix/position/history-position",
                [("productType", "USDT-FUTURES"), ("startTime", t_cur), ("endTime", t_end), ("limit", 100)],
                default={},
            )
            rows = (res.get("list") or []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
            out.extend(dict(r) for r in (rows or []) if isinstance(r, dict))
            t_cur = t_end + 1
        return out

    async def fetch_fills(
        self,
        *,
        since_sec: int,
        account_mode: str = "UTA",
    ) -> list[dict[str, Any]]:
        if account_mode == "UTA":
            return await self._paginate_uta_cursor(
                "/api/v3/trade/fills",
                [("category", "USDT-FUTURES"), ("limit", 100)],
            )
        now_ms = self._clock_ms()
        t_cur = max(0, int(since_sec) * 1000)
        out: list[dict[str, Any]] = []
        while t_cur < now_ms:
            t_end = min(now_ms, t_cur + BG_WINDOW_MS)
            res = await self._safe_get(
                "/api/v2/mix/order/fills",
                [("productType", "USDT-FUTURES"), ("startTime", t_cur), ("endTime", t_end), ("limit", 100)],
                default={},
            )
            rows = (res.get("fillList") or res.get("list") or []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
            out.extend(dict(r) for r in (rows or []) if isinstance(r, dict))
            t_cur = t_end + 1
        return out

    async def fetch_orders_history(
        self,
        *,
        since_sec: int,
        account_mode: str = "UTA",
    ) -> list[dict[str, Any]]:
        if account_mode == "UTA":
            return await self._paginate_uta_cursor(
                "/api/v3/trade/history-orders",
                [("category", "USDT-FUTURES"), ("limit", 100)],
            )
        return []

    async def _fetch_uta_tax_windows(
        self,
        *,
        start_ms: int,
        end_ms: int,
        max_empty_windows: int = 5,
    ) -> list[dict[str, Any]]:
        """Fetch Bitget UTA `/api/v3/tax/records?bizType=USDT-FUTURES` in 7-day reverse windows (1 req/s rate limit)."""
        dedup: dict[str, dict[str, Any]] = {}
        w_end = int(end_ms)
        floor_ms = max(0, int(start_ms))
        empty_streak = 0

        while w_end > floor_ms:
            w_start = max(floor_ms, w_end - BG_TAX_WINDOW_MS)
            page_end = w_end
            cursor: Optional[str] = None
            win_count = 0
            for _ in range(15):
                if self._owns_client:
                    await asyncio.sleep(1.02)
                params: list[tuple[str, Any]] = [
                    ("bizType", "USDT-FUTURES"),
                    ("startTime", w_start),
                    ("endTime", page_end),
                    ("limit", 100),
                ]
                if cursor:
                    params.append(("cursor", cursor))
                res = await self._safe_get("/api/v3/tax/records", params, default=None)
                if res is None:
                    break
                rows = (
                    res
                    if isinstance(res, list)
                    else ((res.get("list") or res.get("recordList") or []) if isinstance(res, dict) else [])
                )
                if not rows:
                    break
                for r in rows:
                    if isinstance(r, dict):
                        rid = str(r.get("id") or f"{r.get('ts')}_{r.get('type')}_{r.get('amount')}")
                        dedup[rid] = {**dict(r), "_source": "v3_uta_tax"}
                        win_count += 1
                if len(rows) < 100:
                    break
                if isinstance(res, dict) and res.get("cursor"):
                    cursor = str(res["cursor"])
                    continue
                min_ts = min(int(r.get("ts") or page_end) for r in rows if isinstance(r, dict))
                if w_start < min_ts < page_end:
                    page_end = min_ts - 1
                    cursor = None
                    continue
                break

            if win_count == 0:
                empty_streak += 1
                if empty_streak >= max_empty_windows:
                    break
            else:
                empty_streak = 0
            w_end = w_start - 1

        return list(dedup.values())

    async def fetch_financial_bills(
        self,
        *,
        since_sec: int,
        account_mode: str = "UTA",
        include_tax_ledger: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch Bitget rebates, UTA futures tax ledger, funding records, and external deposits/withdrawals."""
        now_ms = self._clock_ms()
        t_cur = max(0, int(since_sec) * 1000)
        out: list[dict[str, Any]] = []

        if account_mode == "UTA":
            # 1. Recent UTA futures financial records & Funding Account financial records (rebate_rewards)
            uta_fin = await self._paginate_uta_cursor(
                "/api/v3/account/financial-records",
                [("category", "USDT-FUTURES"), ("limit", 100)],
                max_pages=10,
            )
            for r in uta_fin:
                out.append({**r, "_source": "v3_uta_fin"})

            fund_fin_recent = await self._paginate_uta_cursor(
                "/api/v3/account/funding-financial-records",
                [("limit", 100)],
                max_pages=10,
            )
            for r in fund_fin_recent:
                out.append({**r, "_source": "v3_funding_fin"})

            # 2. Fetch UTA futures tax ledger (full history on backfill, or latest 7d window for live Strategy/Copy runs)
            if include_tax_ledger:
                uta_tax_rows = await self._fetch_uta_tax_windows(
                    start_ms=max(t_cur, now_ms - 364 * 86400 * 1000),
                    end_ms=now_ms,
                    max_empty_windows=5,
                )
            else:
                uta_tax_rows = await self._fetch_uta_tax_windows(
                    start_ms=max(t_cur, now_ms - BG_TAX_WINDOW_MS),
                    end_ms=now_ms,
                    max_empty_windows=1,
                )
            out.extend(uta_tax_rows)

            # 3. On-chain deposits & withdrawals (and full-history funding/tax windows on backfill)
            c_start = t_cur
            while c_start < now_ms:
                c_end = min(now_ms, c_start + BG_WINDOW_MS)
                dep_res = await self._safe_get(
                    "/api/v3/account/deposit-records",
                    [("startTime", c_start), ("endTime", c_end), ("limit", 100)],
                    default={},
                )
                dep_rows = (dep_res.get("list") or []) if isinstance(dep_res, dict) else (dep_res if isinstance(dep_res, list) else [])
                for d in dep_rows or []:
                    if isinstance(d, dict):
                        out.append({**dict(d), "_source": "v3_deposit"})

                wd_res = await self._safe_get(
                    "/api/v3/account/withdrawal-records",
                    [("startTime", c_start), ("endTime", c_end), ("limit", 100)],
                    default={},
                )
                wd_rows = (wd_res.get("list") or []) if isinstance(wd_res, dict) else (wd_res if isinstance(wd_res, list) else [])
                for w in wd_rows or []:
                    if isinstance(w, dict):
                        out.append({**dict(w), "_source": "v3_withdraw"})

                if include_tax_ledger:
                    ff_win = await self._paginate_uta_cursor(
                        "/api/v3/account/funding-financial-records",
                        [("startTime", c_start), ("endTime", c_end), ("limit", 100)],
                        max_pages=5,
                    )
                    for r in ff_win:
                        out.append({**r, "_source": "v3_funding_fin"})

                    tax_res = await self._safe_get(
                        "/api/v2/tax/spot-record",
                        [("startTime", c_start), ("endTime", c_end), ("limit", 500)],
                        default={},
                    )
                    tax_rows = (
                        (tax_res.get("recordList") or tax_res.get("list") or [])
                        if isinstance(tax_res, dict)
                        else (tax_res if isinstance(tax_res, list) else [])
                    )
                    for tx in tax_rows or []:
                        if isinstance(tx, dict):
                            out.append({**dict(tx), "_source": "v2_spot_tax"})
                    await asyncio.sleep(0.28)

                c_start = c_end + 1
            return out

        # Classic v2 mode
        c_start = t_cur
        while c_start < now_ms:
            c_end = min(now_ms, c_start + BG_WINDOW_MS)
            res_m = await self._safe_get(
                "/api/v2/mix/account/bill",
                [("productType", "USDT-FUTURES"), ("startTime", c_start), ("endTime", c_end), ("limit", 100)],
                default={},
            )
            rows_m = (res_m.get("bills") or []) if isinstance(res_m, dict) else (res_m if isinstance(res_m, list) else [])
            for r in rows_m or []:
                if isinstance(r, dict):
                    out.append({**dict(r), "_source": "v2_mix_bill"})
            res_s = await self._safe_get(
                "/api/v2/spot/account/bills",
                [("startTime", c_start), ("endTime", c_end), ("limit", 100)],
                default=[],
            )
            for r in res_s or []:
                if isinstance(r, dict):
                    out.append({**dict(r), "_source": "v2_spot_bill"})
            c_start = c_end + 1
        return out

    async def fetch_symbol_fee_rates(
        self,
        symbols: Sequence[str],
        *,
        account_mode: str = "UTA",
    ) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for sym in sorted({s.strip().upper() for s in symbols if s and s.strip()}):
            res = None
            if account_mode == "UTA":
                res = await self._safe_get(
                    "/api/v3/account/fee-rate",
                    [("category", "USDT-FUTURES"), ("symbol", sym)],
                    default=None,
                )
                if not isinstance(res, dict) or (res.get("makerFeeRate") is None and res.get("takerFeeRate") is None):
                    res = await self._safe_get(
                        "/api/v3/user/fee-rate",
                        [("category", "USDT-FUTURES"), ("symbol", sym)],
                        default={},
                    )
            else:
                res = await self._safe_get(
                    "/api/v2/common/trade-rate",
                    [("symbol", sym), ("businessType", "mix")],
                    default={},
                )
            if isinstance(res, dict) and (res.get("makerFeeRate") is not None or res.get("takerFeeRate") is not None):
                out[sym] = {
                    "makerFeeRate": str(res.get("makerFeeRate") or "0"),
                    "takerFeeRate": str(res.get("takerFeeRate") or "0"),
                }
        return out
