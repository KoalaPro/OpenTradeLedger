"""Strictly read-only Gate.io API v4 signed client with 29-day sliding window backfill."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlencode

import httpx

from opentradeledger.adapters.balance_validation import finite_amount, wallet_rows
from opentradeledger.adapters.compat_creds import GateManualCredentials
from opentradeledger.adapters.stock_channels import classify_spot_stock_token
from opentradeledger.security.readonly_guard import ReadOnlyGuardError

logger = logging.getLogger("gate_manual.client")

_EMPTY_BODY_SHA512 = hashlib.sha512(b"").hexdigest()
WINDOW_DAYS_SEC = 29 * 86400
_MISSING = object()


def _is_valid_gate_wallet_total(wt: Any) -> bool:
    if not isinstance(wt, dict) or not isinstance(wt.get("total"), dict):
        return False
    return finite_amount(wt["total"].get("amount"))


def _is_valid_gate_unified(u: Any) -> bool:
    return isinstance(u, dict) and (
        "unified_account_total_equity" in u
        or "total_equity_usd" in u
        or isinstance(u.get("balances"), dict)
    )


def _is_valid_gate_futures_account(f: Any) -> bool:
    return isinstance(f, dict) and ("total" in f or "available" in f)


def _is_valid_gate_spot_accounts(s: Any) -> bool:
    return wallet_rows(s, names=("currency",), amounts=("available",)) and all(
        "locked" not in row or finite_amount(row["locked"]) for row in s
    )


def _is_valid_gate_stock_assets(sa: Any) -> bool:
    if isinstance(sa, list):
        return True
    if not isinstance(sa, dict):
        return False
    if not sa:
        return True
    if "data" in sa:
        inner = sa["data"]
        if isinstance(inner, list):
            return True
        if not isinstance(inner, dict):
            return False
        if not inner:
            return True
        return any(k in inner for k in ("equity", "total_equity", "cash", "available", "assets"))
    return any(k in sa for k in ("equity", "total_equity", "cash", "available", "assets"))


class GateApiHttpError(RuntimeError):
    def __init__(self, status_code: int, subpath: str, detail: Any) -> None:
        self.status_code = status_code
        self.subpath = subpath
        self.detail = detail
        super().__init__(f"Gate GET {subpath} failed with HTTP {status_code}: {detail}")


def is_unopened_gate_copy_probe(warning: str) -> bool:
    """Recognize the optional feature response, including warnings saved by older releases."""
    msg = warning.lower().replace('"', "'")
    return (
        msg.startswith("copy_trading_probe: gate get /copy_trading/positions failed with http 403:")
        and "'label': 'forbidden'" in msg
        and "'message': 'permission not open yet'" in msg
    )


def _is_fatal_gate_error(exc: BaseException) -> bool:
    if isinstance(exc, ReadOnlyGuardError):
        return True
    if isinstance(exc, (httpx.TransportError, TimeoutError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, GateApiHttpError):
        if exc.status_code == 0 or exc.status_code == 429 or exc.status_code >= 500:
            return True
        if isinstance(exc.detail, dict):
            lbl = str(exc.detail.get("label") or "").strip().upper()
            if lbl in {
                "TOO_MANY_REQUESTS",
                "REQUEST_FREQUENCY_TOO_HIGH",
                "SERVER_ERROR",
                "INTERNAL_ERROR",
                "SERVICE_UNAVAILABLE",
            }:
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
            "timed out",
            "timeout",
        )
    )


class GateReadOnlyClient:
    """HMAC-SHA512 signed read-only REST client for Gate API v4."""

    def __init__(
        self,
        credentials: GateManualCredentials,
        *,
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 12.0,
        clock_sec: Optional[Callable[[], int]] = None,
    ) -> None:
        self.credentials = credentials
        self._clock_sec = clock_sec or (lambda: int(time.time()))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=credentials.base_url,
            timeout=httpx.Timeout(timeout_sec, connect=6.0),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        subpath: str,
        query_params: Sequence[tuple[str, Optional[str | int]]] = (),
        *,
        signed: bool = True,
    ) -> Any:
        if method.upper() != "GET":
            raise ReadOnlyGuardError(
                f"GateReadOnlyClient strictly forbids non-GET method {method!r} on {subpath!r}"
            )
        api_path = f"/api/v4{subpath}"
        filtered: list[tuple[str, str]] = [
            (k, str(v)) for k, v in query_params if v is not None and str(v) != ""
        ]
        query_str = urlencode(filtered)
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Gate-Size-Decimal": "1",
        }
        if signed:
            ts_str = str(int(self._clock_sec()))
            sign_payload = f"GET\n{api_path}\n{query_str}\n{_EMPTY_BODY_SHA512}\n{ts_str}"
            signature = hmac.new(
                self.credentials.api_secret.encode("utf-8"),
                sign_payload.encode("utf-8"),
                hashlib.sha512,
            ).hexdigest()
            headers["KEY"] = self.credentials.api_key
            headers["Timestamp"] = ts_str
            headers["SIGN"] = signature

        rel_url = f"{api_path}?{query_str}" if query_str else api_path
        url = rel_url if self._owns_client else f"{self.credentials.base_url}{rel_url}"
        resp = await self._client.request("GET", url, headers=headers)
        is_json = resp.headers.get("content-type", "").lower().startswith("application/json")
        payload = resp.json() if is_json and resp.content else resp.text
        if resp.status_code >= 400:
            raise GateApiHttpError(resp.status_code, subpath, payload)
        return payload

    async def raw_get(
        self,
        subpath: str,
        query_params: Sequence[tuple[str, Optional[str | int]]] = (),
        *,
        signed: bool = True,
    ) -> Any:
        return await self._request("GET", subpath, query_params, signed=signed)

    # ── Live Account, Fee Tier & Active Positions ──────────────────────────────

    async def fetch_live_overview_bundle(self) -> dict[str, Any]:
        warnings: list[str] = []
        failed_fields: list[str] = []
        wallet_total = await self._safe_get(
            "/wallet/total_balance",
            default=_MISSING,
            field_name="wallet_total",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        if wallet_total is not _MISSING and not _is_valid_gate_wallet_total(wallet_total):
            if "wallet_total" not in failed_fields:
                failed_fields.append("wallet_total")
            warnings.append("wallet_total: malformed response structure")
            wallet_total = {}
        elif wallet_total is _MISSING:
            wallet_total = {}

        unified = await self._safe_get(
            "/unified/accounts",
            default=_MISSING,
            field_name="unified",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        if unified is not _MISSING and not _is_valid_gate_unified(unified):
            if "unified" not in failed_fields:
                failed_fields.append("unified")
            warnings.append("unified: malformed response structure")
            unified = {}
        elif unified is _MISSING:
            unified = {}

        futures_acc = await self._safe_get(
            "/futures/usdt/accounts",
            default=_MISSING,
            field_name="futures_account",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        if futures_acc is not _MISSING and not _is_valid_gate_futures_account(futures_acc):
            if "futures_account" not in failed_fields:
                failed_fields.append("futures_account")
            warnings.append("futures_account: malformed response structure")
            futures_acc = {}
        elif futures_acc is _MISSING:
            futures_acc = {}

        spot_accounts = await self._safe_get(
            "/spot/accounts",
            default=_MISSING,
            field_name="spot_accounts",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        if spot_accounts is not _MISSING and not _is_valid_gate_spot_accounts(spot_accounts):
            if "spot_accounts" not in failed_fields:
                failed_fields.append("spot_accounts")
            warnings.append("spot_accounts: malformed response structure")
            spot_accounts = []
        elif spot_accounts is _MISSING:
            spot_accounts = []

        futures_pos = await self._safe_get(
            "/futures/usdt/positions",
            default=[],
            field_name="futures_positions",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        futures_open_orders = await self._safe_get(
            "/futures/usdt/orders",
            [("status", "open")],
            default=[],
            field_name="futures_open_orders",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        futures_price_orders = await self._safe_get(
            "/futures/usdt/price_orders",
            [("status", "open")],
            default=[],
            field_name="futures_price_orders",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        wallet_fee = await self._safe_get(
            "/wallet/fee",
            default={},
            field_name="wallet_fee",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        account_detail = await self._safe_get(
            "/account/detail",
            default={},
            field_name="account_detail",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        stock_assets = await self._safe_get(
            "/stock/users/assets",
            default=_MISSING,
            field_name="stock_assets",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        if stock_assets is not _MISSING and not _is_valid_gate_stock_assets(stock_assets):
            if "stock_assets" not in failed_fields:
                failed_fields.append("stock_assets")
            warnings.append("stock_assets: malformed response structure")
            stock_assets = {}
        elif stock_assets is _MISSING:
            stock_assets = {}
        stock_positions = await self._safe_get(
            "/stock/positions",
            default={},
            field_name="stock_positions",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        stock_open_orders = await self._safe_get(
            "/stock/orders",
            default={},
            field_name="stock_open_orders",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        stock_fee_rates = await self._safe_get(
            "/stock/fee-rate",
            default={},
            field_name="stock_fee_rates",
            failed_fields=failed_fields,
            warnings=warnings,
        )
        copy_probe = await self.probe_copy_trading_positions(
            failed_fields=failed_fields,
            warnings=warnings,
        )

        active_f_pos = [
            p for p in (futures_pos if isinstance(futures_pos, list) else [])
            if float(p.get("size") or 0) != 0
        ]

        spot_stock_tickers: dict[str, str] = {}
        spot_stock_trades: dict[str, list[dict[str, Any]]] = {}
        uni_balances = unified.get("balances") if isinstance(unified, dict) else None
        if isinstance(uni_balances, dict):
            for ccy_k, b_obj in uni_balances.items():
                if not isinstance(b_obj, dict):
                    continue
                qty_v = float(b_obj.get("available") or 0) + float(b_obj.get("freeze") or b_obj.get("locked") or 0)
                if qty_v <= 0:
                    continue
                t_info = classify_spot_stock_token(str(ccy_k), exchange="GATE")
                if not t_info:
                    continue
                tok_s = t_info["token_symbol"]
                pair_s = f"{tok_s}_USDT"
                tk_res = await self._safe_get(
                    "/spot/tickers",
                    [("currency_pair", pair_s)],
                    default=[],
                    signed=False,
                    field_name="spot_stock_bundle",
                    failed_fields=failed_fields,
                    warnings=warnings,
                )
                if isinstance(tk_res, list) and tk_res and isinstance(tk_res[0], dict) and tk_res[0].get("last"):
                    spot_stock_tickers[tok_s] = str(tk_res[0]["last"])
                tr_res = await self._safe_get(
                    "/spot/my_trades",
                    [("currency_pair", pair_s), ("limit", 100)],
                    default=[],
                    field_name="spot_stock_bundle",
                    failed_fields=failed_fields,
                    warnings=warnings,
                )
                if isinstance(tr_res, list):
                    spot_stock_trades[tok_s] = [dict(r) for r in tr_res if isinstance(r, dict)]

        unwrapped_stock_assets = (
            stock_assets["data"]
            if (isinstance(stock_assets, dict) and "data" in stock_assets)
            else stock_assets
        )
        out: dict[str, Any] = {
            "wallet_total": wallet_total if isinstance(wallet_total, dict) else {},
            "unified": unified if isinstance(unified, dict) else {},
            "futures_account": futures_acc if isinstance(futures_acc, dict) else {},
            "spot_accounts": [dict(r) for r in spot_accounts if isinstance(r, dict)] if isinstance(spot_accounts, list) else [],
            "futures_positions": active_f_pos,
            "futures_open_orders": futures_open_orders if isinstance(futures_open_orders, list) else [],
            "futures_price_orders": futures_price_orders if isinstance(futures_price_orders, list) else [],
            "wallet_fee": wallet_fee if isinstance(wallet_fee, dict) else {},
            "account_detail": account_detail if isinstance(account_detail, dict) else {},
            "stock_assets": dict(unwrapped_stock_assets) if isinstance(unwrapped_stock_assets, dict) else {},
            "stock_positions": ((stock_positions.get("data") or {}).get("list") or [])
            if isinstance(stock_positions, dict)
            else [],
            "stock_open_orders": ((stock_open_orders.get("data") or {}).get("list") or [])
            if isinstance(stock_open_orders, dict)
            else [],
            "stock_fee_rates": ((stock_fee_rates.get("data") or {}).get("list") or [])
            if isinstance(stock_fee_rates, dict)
            else [],
            "spot_stock_bundle": {
                "tickers": spot_stock_tickers,
                "trades": spot_stock_trades,
            },
            "copy_trading_probe": copy_probe,
        }
        if failed_fields:
            out["_failed_fields"] = failed_fields
        if warnings:
            out["_warnings"] = warnings
        return out

    async def fetch_contract_specs(self) -> dict[str, dict[str, Any]]:
        rows = await self._safe_get("/futures/usdt/contracts", default=[], signed=False)
        out: dict[str, dict[str, Any]] = {}
        if isinstance(rows, list):
            for item in rows:
                name = str(item.get("name") or "").strip()
                if name:
                    out[name] = item
        return out

    async def probe_copy_trading_positions(
        self,
        *,
        failed_fields: Optional[list[str]] = None,
        warnings: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        try:
            data = await self.raw_get("/copy_trading/positions")
            items = (data.get("data") or {}).get("list") if isinstance(data, dict) else data
            return {
                "accessible": True,
                "reason": "OK",
                "positions": items if isinstance(items, list) else [],
            }
        except Exception as exc:
            if _is_fatal_gate_error(exc):
                raise
            label = ""
            status_code = getattr(exc, "status_code", 0)
            detail = getattr(exc, "detail", None)
            if isinstance(detail, dict):
                label = str(detail.get("label") or detail.get("message") or "")
            warning = f"copy_trading_probe: {exc}"
            if is_unopened_gate_copy_probe(warning):
                return {"accessible": False, "reason": "permission not open yet", "positions": []}
            if warnings is not None:
                warnings.append(warning)
            if failed_fields is not None and "copy_trading_probe" not in failed_fields:
                failed_fields.append("copy_trading_probe")
            return {
                "accessible": False,
                "reason": label or (f"HTTP_{status_code}" if status_code else str(exc)),
                "positions": [],
            }

    async def _safe_get(
        self,
        subpath: str,
        query_params: Sequence[tuple[str, Optional[str | int]]] = (),
        *,
        default: Any = None,
        signed: bool = True,
        field_name: str = "",
        failed_fields: Optional[list[str]] = None,
        warnings: Optional[list[str]] = None,
    ) -> Any:
        try:
            return await self.raw_get(subpath, query_params, signed=signed)
        except Exception as exc:
            if _is_fatal_gate_error(exc):
                raise
            logger.debug("Optional Gate GET %s skipped: %s", subpath, exc)
            if warnings is not None:
                warnings.append(f"{field_name or subpath}: {exc}")
            if failed_fields is not None and field_name and field_name not in failed_fields:
                failed_fields.append(field_name)
            return default

    # ── Sliding-Window Historical Backfill & Incremental Pulls ─────────────────

    def _iter_windows(self, since_sec: int, until_sec: Optional[int] = None) -> list[tuple[int, int]]:
        end = int(until_sec if until_sec is not None else self._clock_sec())
        start_floor = max(0, int(since_sec))
        windows: list[tuple[int, int]] = []
        cursor = end
        while cursor > start_floor:
            w_from = max(start_floor, cursor - WINDOW_DAYS_SEC)
            windows.append((w_from, cursor))
            cursor = w_from
        return windows

    async def _paginate_futures_window_list(
        self,
        subpath: str,
        *,
        since_sec: int,
        extra_params: Sequence[tuple[str, str | int]] = (),
        max_empty_windows: int = 3,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        empty_streak = 0
        for w_from, w_to in self._iter_windows(since_sec):
            offset = 0
            window_count = 0
            while True:
                params: list[tuple[str, Optional[str | int]]] = [
                    *extra_params,
                    ("from", w_from),
                    ("to", w_to),
                    ("limit", 100),
                    ("offset", offset),
                ]
                rows = await self._safe_get(subpath, params, default=[])
                if not isinstance(rows, list) or not rows:
                    break
                results.extend(rows)
                window_count += len(rows)
                if len(rows) < 100:
                    break
                offset += 100
            if window_count == 0:
                empty_streak += 1
                if empty_streak >= max_empty_windows:
                    break
            else:
                empty_streak = 0
        return results

    async def fetch_futures_closes(self, *, since_sec: int) -> list[dict[str, Any]]:
        rows = await self._paginate_futures_window_list(
            "/futures/usdt/position_close", since_sec=since_sec
        )
        dedup: dict[str, dict[str, Any]] = {}
        for r in rows:
            key = f"{r.get('contract')}_{r.get('first_open_time')}_{r.get('time')}_{r.get('side')}"
            dedup[key] = r
        return sorted(dedup.values(), key=lambda x: float(x.get("time") or 0), reverse=True)

    async def fetch_futures_trades(self, *, since_sec: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        empty_streak = 0
        for w_from, w_to in self._iter_windows(since_sec):
            offset = 0
            window_count = 0
            while True:
                rows = await self._safe_get(
                    "/futures/usdt/my_trades_timerange",
                    [("from", w_from), ("to", w_to), ("limit", 500), ("offset", offset)],
                    default=[],
                )
                if not isinstance(rows, list) or not rows:
                    break
                results.extend(rows)
                window_count += len(rows)
                if len(rows) < 500:
                    break
                offset += 500
            if window_count == 0:
                empty_streak += 1
                if empty_streak >= 3:
                    break
            else:
                empty_streak = 0

        if not results:
            results = await self._paginate_futures_window_list(
                "/futures/usdt/my_trades", since_sec=since_sec
            )

        dedup: dict[str, dict[str, Any]] = {}
        for r in results:
            tid = str(r.get("id") or r.get("trade_id") or f"{r.get('order_id')}_{r.get('create_time')}_{r.get('size')}")
            dedup[tid] = {**r, "id": tid}
        return sorted(dedup.values(), key=lambda x: float(x.get("create_time") or 0), reverse=True)

    async def fetch_futures_finished_orders(self, *, since_sec: int) -> list[dict[str, Any]]:
        rows = await self._paginate_futures_window_list(
            "/futures/usdt/orders",
            since_sec=since_sec,
            extra_params=[("status", "finished")],
        )
        dedup: dict[str, dict[str, Any]] = {}
        for r in rows:
            oid = str(r.get("id") or "").strip()
            if oid:
                dedup[oid] = r
        return sorted(dedup.values(), key=lambda x: float(x.get("create_time") or 0), reverse=True)

    async def fetch_futures_account_book(self, *, since_sec: int) -> list[dict[str, Any]]:
        rows = await self._paginate_futures_window_list(
            "/futures/usdt/account_book", since_sec=since_sec
        )
        dedup: dict[str, dict[str, Any]] = {}
        for r in rows:
            key = str(r.get("id") or f"{r.get('time')}_{r.get('type')}_{r.get('change')}_{r.get('text')}")
            dedup[key] = r
        return sorted(dedup.values(), key=lambda x: float(x.get("time") or 0), reverse=True)

    async def fetch_partner_rebates(self, *, since_sec: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        empty_streak = 0
        for w_from, w_to in self._iter_windows(since_sec):
            offset = 0
            window_rows: list[dict[str, Any]] = []
            while True:
                data = await self._safe_get(
                    "/rebate/partner/commission_history",
                    [("from", w_from), ("to", w_to), ("limit", 100), ("offset", offset)],
                    default={},
                )
                lst = data.get("list", []) if isinstance(data, dict) else []
                if not isinstance(lst, list) or not lst:
                    break
                window_rows.extend(lst)
                total = int(data.get("total") or 0) if isinstance(data, dict) else 0
                offset += len(lst)
                if len(lst) < 100 or (total > 0 and offset >= total):
                    break
            if not window_rows:
                empty_streak += 1
                if empty_streak >= 3:
                    break
            else:
                empty_streak = 0
                per_ts_occ: dict[str, int] = {}
                for r in sorted(window_rows, key=lambda x: (int(x.get("commission_time") or 0), str(x.get("commission_amount") or ""))):
                    base_k = f"{r.get('commission_time')}_{r.get('commission_amount')}_{r.get('commission_asset')}_{r.get('source')}"
                    occ = per_ts_occ.get(base_k, 0) + 1
                    per_ts_occ[base_k] = occ
                    results.append({**r, "_rebate_uid": f"{base_k}#{occ}"})

        dedup: dict[str, dict[str, Any]] = {}
        for r in results:
            dedup[str(r["_rebate_uid"])] = r
        return sorted(dedup.values(), key=lambda x: int(x.get("commission_time") or 0), reverse=True)

    async def fetch_spot_account_book(
        self,
        *,
        since_sec: Optional[int] = None,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        self.spot_book_warnings: list[str] = []
        results: list[dict[str, Any]] = []
        if since_sec is not None:
            empty_streak = 0
            for w_from, w_to in self._iter_windows(since_sec):
                window_count = 0
                for page in range(1, max_pages + 1):
                    rows = await self._safe_get(
                        "/spot/account_book",
                        [("from", w_from), ("to", w_to), ("limit", 100), ("page", page)],
                        default=[], field_name="spot_account_book", warnings=self.spot_book_warnings,
                    )
                    if not isinstance(rows, list) or not rows:
                        break
                    results.extend(rows)
                    window_count += len(rows)
                    if len(rows) < 100:
                        break
                if self.spot_book_warnings:
                    break
                if window_count == 0:
                    empty_streak += 1
                    if empty_streak >= 3:
                        break
                else:
                    empty_streak = 0

        if not results and not self.spot_book_warnings:
            for page in range(1, max_pages + 1):
                rows = await self._safe_get(
                    "/spot/account_book", [("limit", 100), ("page", page)],
                    default=[], field_name="spot_account_book", warnings=self.spot_book_warnings,
                )
                if not isinstance(rows, list) or not rows:
                    break
                results.extend(rows)
                if len(rows) < 100:
                    break

        dedup: dict[str, dict[str, Any]] = {}
        for r in results:
            key = str(r.get("id") or f"{r.get('time')}_{r.get('type')}_{r.get('change')}")
            dedup[key] = r
        return sorted(dedup.values(), key=lambda x: int(x.get("time") or 0), reverse=True)

    async def fetch_stock_orders_and_deals(self, *, max_pages: int = 10) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        orders: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            resp = await self._safe_get(
                "/stock/orders/history", [("page", page), ("limit", 50)], default={}
            )
            data = resp.get("data") if isinstance(resp, dict) else {}
            lst = (data.get("list") or []) if isinstance(data, dict) else []
            if not lst:
                break
            orders.extend(lst)
            if len(lst) < 50:
                break

        deals: list[dict[str, Any]] = []
        seen_orders: set[str] = set()
        for order in orders:
            oid = str(order.get("id") or "").strip()
            filled = float(order.get("filled_total") or order.get("filled_amount") or 0)
            if not oid or oid in seen_orders or filled <= 0:
                continue
            seen_orders.add(oid)
            d_resp = await self._safe_get(
                "/stock/orders/deals", [("order_id", oid), ("page", 1), ("page_size", 50)], default={}
            )
            d_data = d_resp.get("data") if isinstance(d_resp, dict) else {}
            d_lst = (d_data.get("list") or []) if isinstance(d_data, dict) else []
            for d in d_lst:
                if "order_id" not in d:
                    d = {**d, "order_id": oid}
                deals.append(d)
        return orders, deals

    async def fetch_stock_transactions(self, *, max_pages: int = 10) -> list[dict[str, Any]]:
        txs: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            resp = await self._safe_get(
                "/stock/transactions", [("page", page), ("limit", 50)], default={}
            )
            data = resp.get("data") if isinstance(resp, dict) else {}
            lst = (data.get("list") or []) if isinstance(data, dict) else []
            if not lst:
                break
            txs.extend(lst)
            if len(lst) < 50:
                break
        dedup: dict[str, dict[str, Any]] = {}
        for t in txs:
            key = str(t.get("ref_id") or f"{t.get('time')}_{t.get('type')}_{t.get('change')}")
            dedup[key] = t
        return sorted(dedup.values(), key=lambda x: int(x.get("time") or 0), reverse=True)
