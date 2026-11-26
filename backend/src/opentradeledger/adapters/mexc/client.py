"""Strictly GET-only MEXC Spot v3 & Futures v1 HTTP client (`api.mexc.com` + `contract.mexc.com`)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import urlencode

import httpx

from opentradeledger.adapters.compat_creds import MexcCredentials
from opentradeledger.adapters.stock_channels import (
    _KNOWN_CRYPTO_EXCLUSIONS,
    classify_spot_stock_token,
)

logger = logging.getLogger("gate_manual.mexc")


class MexcReadOnlyViolationError(PermissionError):
    """Raised whenever any caller attempts a non-GET request or unapproved MEXC path."""


class MexcApiError(RuntimeError):
    """Raised when MEXC returns a non-200 HTTP response or non-success business code."""

    def __init__(self, message: str, *, status_code: int = 0, code: Any = None) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(message)


def _is_fatal_mexc_error(exc: BaseException) -> bool:
    if isinstance(exc, MexcReadOnlyViolationError):
        return True
    if isinstance(exc, (httpx.TransportError, TimeoutError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, MexcApiError):
        if exc.status_code == 429 or exc.status_code >= 500:
            return True
        if str(exc.code or "").strip() in {"-1003", "429", "500", "502", "503", "504", "510", "700007"}:
            return True
    low = str(exc).lower()
    return any(
        tok in low
        for tok in (
            "http 5",
            "http 429",
            "(500)",
            "(502)",
            "(503)",
            "(504)",
            "(429)",
            "code=-1003",
            "code=429",
            "code=510",
            "rate limit",
            "too frequent",
            "too many",
            "busy",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "internal server error",
            "timed out",
            "timeout",
        )
    )


_ALLOWED_SPOT_GET_PATHS: frozenset[str] = frozenset(
    {
        "/api/v3/time",
        "/api/v3/exchangeInfo",
        "/api/v3/ticker/price",
        "/api/v3/klines",
        "/api/v3/account",
        "/api/v3/myTrades",
        "/api/v3/mxDeduct/enable",
        "/api/v3/rebate/referCode",
        "/api/v3/rebate/detail",
        "/api/v3/rebate/detail/kickback",
        "/api/v3/rebate/taxQuery",
        "/api/v3/capital/deposit/hisrec",
        "/api/v3/capital/withdraw/history",
        "/api/v3/capital/transfer",
    }
)

_ALLOWED_FUTURES_GET_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/contract/ping",
        "/api/v1/contract/detail",
        "/api/v1/contract/ticker",
        "/api/v1/private/account/assets",
        "/api/v1/private/account/transfer_record",
        "/api/v1/private/account/tiered_fee_rate",
        "/api/v1/private/position/open_positions",
        "/api/v1/private/position/list/history_positions",
        "/api/v1/private/position/funding_records",
        "/api/v1/private/order/list/open_orders",
        "/api/v1/private/order/list/history_orders",
        "/api/v1/private/order/list/order_deals",
    }
)


class MexcReadOnlyClient:
    """Strictly GET-only async client for MEXC Spot v3 and Futures v1 accounts."""

    def __init__(
        self,
        credentials: MexcCredentials,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        clock_ms: Optional[Callable[[], int]] = None,
        recv_window_ms: int = 10_000,
    ) -> None:
        self.credentials = credentials
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            headers={"Accept": "application/json"},
        )
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._recv_window_ms = recv_window_ms
        self._sem = asyncio.Semaphore(5)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    def build_spot_signed_query(self, params: Sequence[tuple[str, str | int | float]] = ()) -> str:
        items: list[tuple[str, str | int | float]] = [
            (str(k), v) for k, v in params if v is not None and str(v) != ""
        ]
        items.append(("timestamp", int(self._clock_ms())))
        items.append(("recvWindow", self._recv_window_ms))
        qs = urlencode(items)
        sig = hmac.new(
            self.credentials.api_secret.encode("utf-8"),
            qs.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{qs}&signature={sig}"

    def build_futures_headers_and_query(
        self,
        params: Sequence[tuple[str, str | int | float]] = (),
        *,
        signed: bool = True,
    ) -> tuple[dict[str, str], str]:
        sorted_items = sorted(
            (str(k), str(v)) for k, v in params if v is not None and str(v) != ""
        )
        qs = urlencode(sorted_items)
        headers: dict[str, str] = {"Accept": "application/json"}
        if signed:
            req_time = str(int(self._clock_ms()))
            prehash = f"{self.credentials.api_key}{req_time}{qs}"
            sig = hmac.new(
                self.credentials.api_secret.encode("utf-8"),
                prehash.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            headers["ApiKey"] = self.credentials.api_key
            headers["Request-Time"] = req_time
            headers["Signature"] = sig
        return headers, qs

    async def spot_request(
        self,
        method: str,
        path: str,
        params: Sequence[tuple[str, str | int | float]] = (),
        *,
        signed: bool = True,
    ) -> Any:
        verb = (method or "").strip().upper()
        if verb != "GET":
            raise MexcReadOnlyViolationError(
                f"MEXC Spot client strictly forbids non-GET HTTP method: {verb!r} ({path})"
            )
        clean_path = (path or "").strip()
        if clean_path not in _ALLOWED_SPOT_GET_PATHS:
            raise MexcReadOnlyViolationError(
                f"Unapproved MEXC Spot GET path blocked by read-only guard: {clean_path!r}"
            )
        headers: dict[str, str] = {"Accept": "application/json"}
        if signed:
            full_qs = self.build_spot_signed_query(params)
            headers["X-MEXC-APIKEY"] = self.credentials.api_key
        else:
            full_qs = urlencode([(str(k), v) for k, v in params if v is not None and str(v) != ""])
        url = f"{self.credentials.spot_base_url}{clean_path}" + (f"?{full_qs}" if full_qs else "")
        async with self._sem:
            resp = await self._http.request("GET", url, headers=headers)
        if resp.status_code != 200:
            raise MexcApiError(
                f"MEXC Spot GET {clean_path} failed ({resp.status_code}): {resp.text[:240]}",
                status_code=resp.status_code,
            )
        body = resp.json()
        if isinstance(body, Mapping):
            b_code = str(body.get("code") or "").strip()
            if b_code in {"-1003", "429", "500", "502", "503", "504", "510", "700007"}:
                raise MexcApiError(
                    f"MEXC Spot GET {clean_path} rejected (code={b_code}): {body.get('msg') or body.get('message')}",
                    status_code=resp.status_code,
                    code=body.get("code"),
                )
        return body

    async def futures_request(
        self,
        method: str,
        path: str,
        params: Sequence[tuple[str, str | int | float]] = (),
        *,
        signed: bool = True,
    ) -> Any:
        verb = (method or "").strip().upper()
        if verb != "GET":
            raise MexcReadOnlyViolationError(
                f"MEXC Futures client strictly forbids non-GET HTTP method: {verb!r} ({path})"
            )
        clean_path = (path or "").strip()
        if clean_path not in _ALLOWED_FUTURES_GET_PATHS:
            raise MexcReadOnlyViolationError(
                f"Unapproved MEXC Futures GET path blocked by read-only guard: {clean_path!r}"
            )
        headers, qs = self.build_futures_headers_and_query(params, signed=signed)
        url = f"{self.credentials.futures_base_url}{clean_path}" + (f"?{qs}" if qs else "")
        async with self._sem:
            resp = await self._http.request("GET", url, headers=headers)
        if resp.status_code != 200:
            raise MexcApiError(
                f"MEXC Futures GET {clean_path} failed ({resp.status_code}): {resp.text[:240]}",
                status_code=resp.status_code,
            )
        body = resp.json()
        if isinstance(body, Mapping) and body.get("success") is False:
            raise MexcApiError(
                f"MEXC Futures GET {clean_path} rejected (code={body.get('code')}): {body.get('message')}",
                status_code=resp.status_code,
                code=body.get("code"),
            )
        return body

    async def fetch_live_overview_bundle(self) -> dict[str, Any]:
        """Fetch live Spot + Futures balances, open positions, open orders, referCode, MX deduct status, and tickers."""
        warnings: list[str] = []
        failed_fields: list[str] = []
        refer_code = ""
        try:
            ref_res = await self.spot_request("GET", "/api/v3/rebate/referCode", signed=True)
            if isinstance(ref_res, Mapping):
                refer_code = str(ref_res.get("referCode") or "").strip()
        except Exception as exc:
            if _is_fatal_mexc_error(exc):
                raise
            logger.debug("MEXC referCode probe skipped: %s", exc)

        mx_deduct: dict[str, Any] = {}
        try:
            mxd_res = await self.spot_request("GET", "/api/v3/mxDeduct/enable", signed=True)
            if isinstance(mxd_res, Mapping):
                data_obj = mxd_res.get("data")
                mx_deduct = dict(data_obj) if isinstance(data_obj, Mapping) else dict(mxd_res)
        except Exception as exc:
            if _is_fatal_mexc_error(exc):
                raise
            logger.debug("MEXC mxDeduct probe skipped: %s", exc)

        spot_balances: list[dict[str, Any]] = []
        non_stable_coins: set[str] = {"MX"}
        spot_err: Optional[Exception] = None
        try:
            sp_acc = await self.spot_request("GET", "/api/v3/account", signed=True)
            if (
                not isinstance(sp_acc, Mapping)
                or "balances" not in sp_acc
                or not isinstance(sp_acc["balances"], list)
                or any(
                    not isinstance(b, Mapping)
                    or "asset" not in b
                    or not any(k in b for k in ("free", "locked", "balance"))
                    for b in sp_acc["balances"]
                )
            ):
                raise MexcApiError("Malformed MEXC spot account payload", status_code=200)
            for b in sp_acc["balances"]:
                free_v = float(b.get("free") if b.get("free") is not None else (b.get("balance") or 0.0))
                lock_v = float(b.get("locked") or 0.0)
                if free_v + lock_v > 0:
                    asset_u = str(b.get("asset") or "").strip().upper()
                    spot_balances.append(dict(b))
                    if asset_u and asset_u not in ("USDT", "USDC", "USDE", "FDUSD", "USD"):
                        non_stable_coins.add(asset_u)
        except Exception as exc:
            if _is_fatal_mexc_error(exc):
                raise
            spot_err = exc
            logger.warning("MEXC Spot /api/v3/account error: %s", exc)
            failed_fields.append("spot_balances")
            warnings.append(f"spot_balances: {exc}")

        fut_assets_res, open_pos_res, open_ord_res, tiered_res = await asyncio.gather(
            self.futures_request("GET", "/api/v1/private/account/assets", signed=True),
            self.futures_request("GET", "/api/v1/private/position/open_positions", signed=True),
            self.futures_request(
                "GET",
                "/api/v1/private/order/list/open_orders",
                [("page_num", 1), ("page_size", 100)],
                signed=True,
            ),
            self.futures_request("GET", "/api/v1/private/account/tiered_fee_rate", signed=True),
            return_exceptions=True,
        )

        raw_fut_list: Optional[list[Any]] = None
        if not isinstance(fut_assets_res, BaseException):
            if isinstance(fut_assets_res, Mapping) and "data" in fut_assets_res and isinstance(fut_assets_res["data"], list):
                raw_fut_list = fut_assets_res["data"]
            elif isinstance(fut_assets_res, list):
                raw_fut_list = fut_assets_res
            if raw_fut_list is None or any(
                not isinstance(a, Mapping)
                or not any(k in a for k in ("currency", "equity", "availableBalance"))
                for a in raw_fut_list
            ):
                fut_assets_res = MexcApiError("Malformed MEXC futures assets payload", status_code=200)
                raw_fut_list = None

        for f_name, f_res in (
            ("futures_assets", fut_assets_res),
            ("futures_positions", open_pos_res),
            ("futures_open_orders", open_ord_res),
            ("tiered_fee_rate", tiered_res),
        ):
            if isinstance(f_res, BaseException):
                if isinstance(f_res, asyncio.CancelledError) or _is_fatal_mexc_error(f_res):
                    raise f_res
                failed_fields.append(f_name)
                warnings.append(f"{f_name}: {f_res}")

        if spot_err is not None and isinstance(fut_assets_res, BaseException):
            raise spot_err

        futures_assets: list[dict[str, Any]] = []
        if raw_fut_list is not None:
            for a in raw_fut_list:
                if any(
                    abs(float(a.get(k) or 0.0)) > 0
                    for k in ("equity", "cashBalance", "availableBalance", "bonus", "unrealized", "positionMargin")
                ):
                    cur_u = str(a.get("currency") or "").strip().upper()
                    futures_assets.append(dict(a))
                    if cur_u and cur_u not in ("USDT", "USDC", "USDE", "FDUSD", "USD"):
                        non_stable_coins.add(cur_u)

        futures_positions: list[dict[str, Any]] = []
        if isinstance(open_pos_res, Mapping) and isinstance(open_pos_res.get("data"), list):
            futures_positions = [dict(p) for p in open_pos_res["data"] if float(p.get("holdVol") or 0.0) > 0]

        futures_open_orders: list[dict[str, Any]] = []
        if isinstance(open_ord_res, Mapping):
            d_ord = open_ord_res.get("data")
            if isinstance(d_ord, list):
                futures_open_orders = [dict(o) for o in d_ord]
            elif isinstance(d_ord, Mapping) and isinstance(d_ord.get("resultList"), list):
                futures_open_orders = [dict(o) for o in d_ord["resultList"]]

        tiered_fee_rate: Any = (
            tiered_res.get("data") if isinstance(tiered_res, Mapping) else {}
        )

        spot_tickers: dict[str, str] = {}
        for coin_u in sorted(non_stable_coins):
            try:
                tk = await self.spot_request(
                    "GET",
                    "/api/v3/ticker/price",
                    [("symbol", f"{coin_u}USDT")],
                    signed=False,
                )
                if isinstance(tk, Mapping) and tk.get("price"):
                    spot_tickers[coin_u] = str(tk["price"])
            except Exception as exc:
                if _is_fatal_mexc_error(exc):
                    raise
                continue

        stock_bundle = await self._fetch_stock_bundle(
            spot_balances,
            failed_fields=failed_fields,
            warnings=warnings,
        )

        out: dict[str, Any] = {
            "refer_code": refer_code,
            "mx_deduct": mx_deduct,
            "spot_balances": spot_balances,
            "spot_tickers": spot_tickers,
            "futures_assets": futures_assets,
            "futures_positions": futures_positions,
            "futures_open_orders": futures_open_orders,
            "tiered_fee_rate": tiered_fee_rate,
            "stock_bundle": stock_bundle,
        }
        if failed_fields:
            out["_failed_fields"] = failed_fields
        if warnings:
            out["_warnings"] = warnings
        return out

    async def _fetch_stock_bundle(
        self,
        spot_balances: Sequence[dict[str, Any]],
        *,
        failed_fields: Optional[list[str]] = None,
        warnings: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Probe MEXC `Tokenized Stocks` conceptPlate, live fee rates & `/api/v3/myTrades` for non-crypto spot balances."""
        tokenized_catalog: dict[str, dict[str, Any]] = {}
        spot_stock_trades: dict[str, list[dict[str, Any]]] = {}
        spot_stock_fee_rate: dict[str, str] = {}

        candidates = [
            str(b.get("asset") or "").strip().upper()
            for b in spot_balances
            if str(b.get("asset") or "").strip().upper() not in _KNOWN_CRYPTO_EXCLUSIONS
        ]
        probe_coins = sorted(set(candidates))[:15]
        if "NVDAX" not in probe_coins:
            probe_coins = ["NVDAX", *probe_coins]

        for coin_u in probe_coins:
            try:
                ex_info = await self.spot_request(
                    "GET",
                    "/api/v3/exchangeInfo",
                    [("symbol", f"{coin_u}USDT")],
                    signed=False,
                )
                if isinstance(ex_info, Mapping):
                    for sym_item in ex_info.get("symbols") or []:
                        if not isinstance(sym_item, Mapping):
                            continue
                        plates = [str(p).lower() for p in (sym_item.get("conceptPlates") or [])]
                        if any("stock" in p or "股票" in p for p in plates):
                            if not spot_stock_fee_rate and (
                                sym_item.get("makerCommission") is not None
                                or sym_item.get("takerCommission") is not None
                            ):
                                spot_stock_fee_rate = {
                                    "makerCommission": str(sym_item.get("makerCommission") or "0"),
                                    "takerCommission": str(sym_item.get("takerCommission") or "0"),
                                }
                            if coin_u in candidates:
                                und = coin_u[:-2] if coin_u.endswith("ON") else (coin_u[:-1] if coin_u.endswith("X") else coin_u)
                                tokenized_catalog[coin_u] = {
                                    "underlying": und,
                                    "name": str(sym_item.get("fullName") or und),
                                    "series": "MEXC 美股代币",
                                }
            except Exception as exc:
                if _is_fatal_mexc_error(exc):
                    raise
                logger.debug("MEXC exchangeInfo check skipped for %s: %s", coin_u, exc)

            if coin_u in candidates and classify_spot_stock_token(coin_u, exchange="MEXC", dynamic_catalog=tokenized_catalog):
                try:
                    my_tr = await self.spot_request(
                        "GET",
                        "/api/v3/myTrades",
                        [("symbol", f"{coin_u}USDT"), ("limit", 500)],
                        signed=True,
                    )
                    if isinstance(my_tr, list):
                        spot_stock_trades[coin_u] = [dict(r) for r in my_tr if isinstance(r, Mapping)]
                except Exception as exc:
                    if _is_fatal_mexc_error(exc):
                        raise
                    logger.debug("MEXC myTrades check skipped for %s: %s", coin_u, exc)
                    if warnings is not None:
                        warnings.append(f"stock_bundle.myTrades({coin_u}): {exc}")
                    if failed_fields is not None and "stock_bundle" not in failed_fields:
                        failed_fields.append("stock_bundle")

        return {
            "tokenized_catalog": tokenized_catalog,
            "spot_stock_trades": spot_stock_trades,
            "spot_stock_fee_rate": spot_stock_fee_rate,
        }

    async def fetch_contract_specs(self, symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Fetch `/api/v1/contract/detail` specs (`contractSize`, `makerFeeRate`, `takerFeeRate`) for traded symbols."""
        out: dict[str, dict[str, Any]] = {}
        clean_syms = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
        for sym in clean_syms:
            try:
                res = await self.futures_request(
                    "GET",
                    "/api/v1/contract/detail",
                    [("symbol", sym)],
                    signed=False,
                )
                if isinstance(res, Mapping) and isinstance(res.get("data"), Mapping):
                    out[sym] = dict(res["data"])
            except Exception as exc:
                if _is_fatal_mexc_error(exc):
                    raise
                logger.warning("MEXC contract detail failed for %s: %s", sym, exc)
        return out

    async def fetch_history_positions(self, *, since_sec: int, max_pages: int = 20) -> list[dict[str, Any]]:
        """Paginate closed positions from `/api/v1/private/position/list/history_positions`."""
        since_ms = int(since_sec) * 1000
        seen: dict[str, dict[str, Any]] = {}
        for page_num in range(1, max_pages + 1):
            res = await self.futures_request(
                "GET",
                "/api/v1/private/position/list/history_positions",
                [("page_num", page_num), ("page_size", 100)],
                signed=True,
            )
            rows = (res.get("data") or []) if isinstance(res, Mapping) else []
            if not isinstance(rows, list) or not rows:
                break
            oldest_ms = None
            for r in rows:
                pid = str(r.get("positionId") or "")
                ut = int(float(r.get("updateTime") or r.get("createTime") or 0))
                if pid:
                    seen[pid] = dict(r)
                if oldest_ms is None or ut < oldest_ms:
                    oldest_ms = ut
            if len(rows) < 100 or (oldest_ms is not None and oldest_ms < since_ms):
                break
        return list(seen.values())

    async def fetch_order_deals(self, *, since_sec: int, max_pages: int = 30) -> list[dict[str, Any]]:
        """Paginate execution fills from `/api/v1/private/order/list/order_deals`."""
        since_ms = int(since_sec) * 1000
        seen: dict[str, dict[str, Any]] = {}
        for page_num in range(1, max_pages + 1):
            res = await self.futures_request(
                "GET",
                "/api/v1/private/order/list/order_deals",
                [("page_num", page_num), ("page_size", 100)],
                signed=True,
            )
            rows = (res.get("data") or []) if isinstance(res, Mapping) else []
            if not isinstance(rows, list) or not rows:
                break
            oldest_ms = None
            for r in rows:
                did = str(r.get("id") or "")
                ts = int(float(r.get("timestamp") or 0))
                if did:
                    seen[did] = dict(r)
                if oldest_ms is None or ts < oldest_ms:
                    oldest_ms = ts
            if len(rows) < 100 or (oldest_ms is not None and oldest_ms < since_ms):
                break
        return list(seen.values())

    async def fetch_history_orders(self, *, since_sec: int, max_pages: int = 30) -> list[dict[str, Any]]:
        """Paginate historical orders (`positionId`, `makerFeeRate`, `takerFeeRate`, `externalOid`)."""
        since_ms = int(since_sec) * 1000
        seen: dict[str, dict[str, Any]] = {}
        for page_num in range(1, max_pages + 1):
            res = await self.futures_request(
                "GET",
                "/api/v1/private/order/list/history_orders",
                [("page_num", page_num), ("page_size", 100)],
                signed=True,
            )
            rows = (res.get("data") or []) if isinstance(res, Mapping) else []
            if not isinstance(rows, list) or not rows:
                break
            oldest_ms = None
            for r in rows:
                oid = str(r.get("orderId") or "")
                ut = int(float(r.get("updateTime") or r.get("createTime") or 0))
                if oid:
                    seen[oid] = dict(r)
                if oldest_ms is None or ut < oldest_ms:
                    oldest_ms = ut
            if len(rows) < 100 or (oldest_ms is not None and oldest_ms < since_ms):
                break
        return list(seen.values())

    async def fetch_funding_records(self, *, since_sec: int, max_pages: int = 15) -> list[dict[str, Any]]:
        """Paginate funding settlements from `/api/v1/private/position/funding_records`."""
        since_ms = int(since_sec) * 1000
        seen: dict[str, dict[str, Any]] = {}
        for page_num in range(1, max_pages + 1):
            res = await self.futures_request(
                "GET",
                "/api/v1/private/position/funding_records",
                [("page_num", page_num), ("page_size", 100)],
                signed=True,
            )
            data_obj = res.get("data") if isinstance(res, Mapping) else None
            rows = (data_obj.get("resultList") if isinstance(data_obj, Mapping) else data_obj) or []
            if not isinstance(rows, list) or not rows:
                break
            oldest_ms = None
            for r in rows:
                fid = str(r.get("id") or f"{r.get('symbol')}_{r.get('settleTime')}")
                st = int(float(r.get("settleTime") or 0))
                seen[fid] = dict(r)
                if oldest_ms is None or st < oldest_ms:
                    oldest_ms = st
            if len(rows) < 100 or (oldest_ms is not None and oldest_ms < since_ms):
                break
        return list(seen.values())

    async def fetch_cashflows_and_rebates(
        self,
        *,
        since_sec: int,
        do_full: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch Futures transfer records (`ContractHold_` daily yield + capital IN/OUT) and Spot rebates/deposits/withdrawals."""
        since_ms = int(since_sec) * 1000
        now_ms = int(self._clock_ms())
        items: dict[str, dict[str, Any]] = {}

        # 1. Futures transfer_record
        max_tr_pages = 20 if do_full else 4
        for page_num in range(1, max_tr_pages + 1):
            res = await self.futures_request(
                "GET",
                "/api/v1/private/account/transfer_record",
                [("page_num", page_num), ("page_size", 100)],
                signed=True,
            )
            data_obj = res.get("data") if isinstance(res, Mapping) else None
            rows = (data_obj.get("resultList") if isinstance(data_obj, Mapping) else data_obj) or []
            if not isinstance(rows, list) or not rows:
                break
            oldest_ms = None
            for r in rows:
                tid = str(r.get("id") or r.get("txid") or "")
                ct = int(float(r.get("createTime") or 0))
                k = f"fut_tr_{tid}"
                items[k] = {**dict(r), "_source": "fut_transfer", "_key": k, "_time_ms": ct}
                if oldest_ms is None or ct < oldest_ms:
                    oldest_ms = ct
            if len(rows) < 100 or (oldest_ms is not None and oldest_ms < since_ms):
                break

        # 2. Check Spot rebate kickback & detail (real-time commission return if user ever activates referral kickback)
        reb_windows = 6 if do_full else 1
        for w in range(reb_windows):
            t1 = now_ms - w * 28 * 86400 * 1000
            t0 = max(since_ms, t1 - 28 * 86400 * 1000)
            if t1 <= since_ms:
                break
            for r_path, r_src in (
                ("/api/v3/rebate/detail/kickback", "spot_kickback"),
                ("/api/v3/rebate/detail", "spot_rebate"),
            ):
                try:
                    rb = await self.spot_request(
                        "GET",
                        r_path,
                        [("startTime", t0), ("endTime", t1)],
                        signed=True,
                    )
                    rows_rb = (rb.get("data") if isinstance(rb, Mapping) else rb) or []
                    if isinstance(rows_rb, list):
                        for idx_r, rr in enumerate(rows_rb):
                            ts_r = int(float(rr.get("createTime") or rr.get("time") or t1))
                            rk = f"{r_src}_{ts_r}_{rr.get('asset') or rr.get('coin')}_{rr.get('amount') or rr.get('rebateAmount')}_{idx_r}"
                            items[rk] = {**dict(rr), "_source": r_src, "_key": rk, "_time_ms": ts_r}
                except Exception as exc:
                    if _is_fatal_mexc_error(exc):
                        raise
                    logger.debug("MEXC %s check skipped: %s", r_path, exc)

        # 2b. Check Universal Transfer (`/api/v3/capital/transfer`) for Spot <-> RealStocks (`STOCK`/`TRADFI`) flows
        for tr_dir in (("SPOT", "STOCK"), ("STOCK", "SPOT")):
            try:
                q_tr: list[tuple[str, str | int | float]] = [
                    ("fromAccountType", tr_dir[0]),
                    ("toAccountType", tr_dir[1]),
                    ("startTime", since_ms),
                    ("endTime", now_ms),
                    ("size", 100),
                ]
                ut_res = await self.spot_request("GET", "/api/v3/capital/transfer", q_tr, signed=True)
                ut_rows = (
                    (ut_res.get("rows") or ut_res.get("data") or [])
                    if isinstance(ut_res, Mapping)
                    else (ut_res if isinstance(ut_res, list) else [])
                )
                if isinstance(ut_rows, list):
                    for ur in ut_rows:
                        if not isinstance(ur, Mapping):
                            continue
                        ts_u = int(float(ur.get("timestamp") or ur.get("createTime") or now_ms))
                        tid_u = str(ur.get("tranId") or ur.get("id") or f"{ts_u}_{ur.get('amount')}")
                        uk = f"uni_tr_{tid_u}"
                        items[uk] = {**dict(ur), "_source": "universal_transfer", "_key": uk, "_time_ms": ts_u}
            except Exception as exc:
                if _is_fatal_mexc_error(exc):
                    raise
                logger.debug("MEXC universal transfer check skipped: %s", exc)

        # 3. Check Spot on-chain deposit/withdraw history (5x 85d windows on full sync, latest window on incremental)
        cap_windows = 5 if do_full else 1
        for w in range(cap_windows):
            t1 = now_ms - w * 85 * 86400 * 1000
            t0 = max(since_ms, t1 - 85 * 86400 * 1000)
            if t1 <= since_ms:
                break
            try:
                deps = await self.spot_request(
                    "GET",
                    "/api/v3/capital/deposit/hisrec",
                    [("startTime", t0), ("endTime", t1), ("limit", 1000)],
                    signed=True,
                )
                if isinstance(deps, list):
                    for dp in deps:
                        ts_d = int(float(dp.get("insertTime") or t1))
                        dk = f"spot_dep_{dp.get('id') or dp.get('txId') or ts_d}"
                        items[dk] = {**dict(dp), "_source": "spot_deposit", "_key": dk, "_time_ms": ts_d}
            except Exception as exc:
                if _is_fatal_mexc_error(exc):
                    raise
                logger.debug("MEXC spot deposit check skipped: %s", exc)
            try:
                wds = await self.spot_request(
                    "GET",
                    "/api/v3/capital/withdraw/history",
                    [("startTime", t0), ("endTime", t1), ("limit", 1000)],
                    signed=True,
                )
                if isinstance(wds, list):
                    for wd in wds:
                        ts_w = int(float(wd.get("applyTime") or t1))
                        wk = f"spot_wd_{wd.get('id') or wd.get('txId') or ts_w}"
                        items[wk] = {**dict(wd), "_source": "spot_withdraw", "_key": wk, "_time_ms": ts_w}
            except Exception as exc:
                if _is_fatal_mexc_error(exc):
                    raise
                logger.debug("MEXC spot withdraw check skipped: %s", exc)

        return list(items.values())
