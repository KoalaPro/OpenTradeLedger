"""Strictly GET-only Bybit API v5 Unified Account signed client and actuarial per-leg trade normalizer.

Signature specification (Bybit v5 GET):
  payload = timestamp_ms + api_key + recv_window + sorted_query_string
  X-BAPI-SIGN = hex(HMAC-SHA256(payload, api_secret))
"""

from __future__ import annotations

from decimal import Decimal

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx

from opentradeledger.adapters.balance_validation import InvalidWalletSnapshot, finite_amount, optional_amounts, wallet_rows
from opentradeledger.security.readonly_guard import ReadOnlyGuardError, assert_readonly_request

TZ_UTC8 = timezone(timedelta(hours=8))
SEVEN_DAYS_MS = 7 * 86_400 * 1000
CLOSED_PNL_RETENTION_MS = 730 * 86_400_000
CLOSED_PNL_CLOCK_MARGIN_MS = 15 * 60_000
THIRTY_DAYS_MS = 30 * 86_400 * 1000 - 1000

BYBIT_ALLOWED_GET_PATHS: tuple[str, ...] = (
    "/v5/market/time",
    "/v5/market/tickers",
    "/v5/market/instruments-info",
    "/v5/user/query-api",
    "/v5/account/wallet-balance",
    "/v5/account/fee-rate",
    "/v5/account/transaction-log",
    "/v5/position/list",
    "/v5/position/closed-pnl",
    "/v5/execution/list",
    "/v5/order/history",
    "/v5/order/realtime",
    "/v5/asset/transfer/query-account-coins-balance",
    "/v5/asset/transfer/query-inter-transfer-list",
    "/v5/asset/deposit/query-record",
    "/v5/asset/deposit/query-internal-record",
    "/v5/asset/withdraw/query-record",
)


def valid_funding_balance(res: Any) -> bool:
    return isinstance(res, dict) and wallet_rows(
        res.get("balance"), names=("coin",), amounts=("walletBalance",), optional=("usdValue",),
    )


def validate_snapshot_funding(snapshot: dict[str, Any]) -> None:
    funding = snapshot.get("funding", {})
    if funding != {} and not valid_funding_balance(funding):
        raise InvalidWalletSnapshot("Invalid funding balance in saved Bybit snapshot")


class BybitApiError(RuntimeError):
    """Raised when Bybit v5 returns retCode != 0 or an HTTP error."""

    def __init__(self, message: str, *, status_code: int = 0, ret_code: Any = None) -> None:
        self.status_code = status_code
        self.ret_code = ret_code
        super().__init__(message)


def _is_fatal_bybit_error(exc: BaseException) -> bool:
    if isinstance(exc, ReadOnlyGuardError):
        return True
    if isinstance(exc, (httpx.TransportError, TimeoutError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, BybitApiError):
        if exc.status_code == 429 or exc.status_code >= 500:
            return True
        if str(exc.ret_code or "").strip() in {"10006", "10016", "10018", "-1003", "429"}:
            return True
    low = str(exc).lower()
    return any(
        tok in low
        for tok in (
            "http 5",
            "http 429",
            "retcode=10006",
            "retcode=10016",
            "retcode=10018",
            "rate limit",
            "too many visits",
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


def _is_bybit_auth_error(exc: BaseException) -> bool:
    if isinstance(exc, BybitApiError):
        if exc.status_code in {401, 403}:
            return True
        if str(exc.ret_code or "").strip() in {"10003", "10004", "10005", "10010", "33004"}:
            return True
    low = str(exc).lower()
    return any(
        tok in low
        for tok in (
            "http 401",
            "http 403",
            "retcode=10003",
            "retcode=10004",
            "retcode=10005",
            "retcode=10010",
            "retcode=33004",
            "invalid api",
            "error sign",
            "permission denied",
            "unmatched ip",
            "api key expired",
        )
    )


def _is_bybit_account_type_unsupported(exc: BaseException) -> bool:
    if isinstance(exc, BybitApiError) and str(exc.ret_code or "").strip() in {"10001", "100028", "110066"}:
        return True
    low = str(exc).lower()
    return any(tok in low for tok in ("accounttype", "account type", "unified", "retcode=10001", "retcode=100028"))


def _fmt_bj_ms(ms: int) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ_UTC8).strftime("%m-%d %H:%M")


def sign_bybit_get(
    timestamp_ms: str,
    api_key: str,
    recv_window: str,
    query_string: str,
    api_secret: str,
) -> str:
    payload = f"{timestamp_ms}{api_key}{recv_window}{query_string}"
    return hmac.new(api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


class BybitReadOnlyClient:
    """Strictly GET-only signed REST client for Bybit v5 UTA with cursor + 7-day rolling window pagination."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.bybit.com",
        recv_window: str = "5000",
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 12.0,
        clock_ms: Optional[Callable[[], int]] = None,
        page_limit: int = 100,
        max_pages: int = 10,
        max_windows: Optional[int] = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = str(recv_window)
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout_sec
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.page_limit = max(1, min(100, int(page_limit)))
        self.max_pages = max(1, int(max_pages))
        self.max_windows = 52 if max_windows is None else max(1, int(max_windows))
        self.closed_pnl_windows = 105 if max_windows is None else self.max_windows

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def build_headers(self, query_string: str, timestamp_ms: Optional[str] = None) -> dict[str, str]:
        ts = timestamp_ms or str(self._clock_ms())
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": self.recv_window,
            "X-BAPI-SIGN": sign_bybit_get(ts, self.api_key, self.recv_window, query_string, self.api_secret),
        }

    async def request_readonly(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        assert_readonly_request(method, path, BYBIT_ALLOWED_GET_PATHS)
        clean_params = {k: str(v) for k, v in (params or {}).items() if v is not None and v != ""}
        query_string = urlencode(sorted(clean_params.items())) if clean_params else ""
        headers = self.build_headers(query_string)
        url = f"{self.base_url}{path}" + (f"?{query_string}" if query_string else "")

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        resp = await self._client.get(url, headers=headers)

        if resp.status_code >= 400:
            raise BybitApiError(
                f"Bybit GET {path} HTTP {resp.status_code}: {resp.text[:240]}",
                status_code=resp.status_code,
            )
        payload = resp.json()
        if not isinstance(payload, dict):
            raise BybitApiError(
                f"Malformed Bybit payload on {path}",
                status_code=resp.status_code,
            )
        if int(payload.get("retCode", -1)) != 0:
            raise BybitApiError(
                f"Bybit GET {path} retCode={payload.get('retCode')}: {payload.get('retMsg', '')}",
                status_code=resp.status_code,
                ret_code=payload.get("retCode"),
            )
        if "result" not in payload or not isinstance(payload["result"], dict):
            raise BybitApiError(
                f"Malformed Bybit payload on {path}",
                status_code=resp.status_code,
            )
        return payload["result"]

    async def _paginate_cursor(
        self,
        path: str,
        base_params: dict[str, Any],
        *,
        list_key: str = "list",
        closed_pnl_rows: dict[str, dict[str, Any]] | None = None,
        id_fn: Callable[[dict[str, Any]], str],
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        eff_limit = limit or self.page_limit
        collected: dict[str, dict[str, Any]] = {}
        cursor = ""
        seen_cursors: set[str] = set()

        exhaust_cursor = closed_pnl_rows is not None
        while exhaust_cursor or len(seen_cursors) < self.max_pages:
            params = {**base_params, "limit": eff_limit}
            if cursor:
                params["cursor"] = cursor
            res = await self.request_readonly("GET", path, params)
            batch = res.get(list_key)
            next_cursor = str(res.get("nextPageCursor") or "").strip()
            if exhaust_cursor and (not isinstance(batch, list) or (next_cursor and not batch)):
                raise BybitApiError(f"{path}: incomplete page for history cursor")
            if not isinstance(batch, list) or not batch:
                break

            new_count = 0
            for row in batch:
                if isinstance(row, dict):
                    rid = id_fn(row)
                    if rid not in collected:
                        collected[rid] = row
                        if closed_pnl_rows is not None:
                            closed_pnl_rows[rid] = row
                        new_count += 1

            if not next_cursor:
                break
            if next_cursor in seen_cursors or new_count == 0:
                if exhaust_cursor:
                    raise BybitApiError(f"{path}: history cursor did not advance")
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        return list(collected.values())

    async def _paginate_7d_windows(
        self,
        path: str,
        base_params: dict[str, Any],
        *,
        id_fn: Callable[[dict[str, Any]], str],
        ts_field: str,
        closed_pnl_rows: dict[str, dict[str, Any]] | None = None,
        since_ms: int = 0,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Fetches recent default window first, then steps backwards in 7-day chunks up to max_windows."""
        scan_sparse_history = closed_pnl_rows is not None
        first_rows = await self._paginate_cursor(path, base_params, id_fn=id_fn, limit=limit, closed_pnl_rows=closed_pnl_rows)
        merged = closed_pnl_rows if closed_pnl_rows is not None else {id_fn(r): r for r in first_rows}

        windows = self.closed_pnl_windows if scan_sparse_history else self.max_windows
        if windows <= 1:
            return list(merged.values())

        now_ms = self._clock_ms()
        if since_ms > 0 and now_ms - since_ms <= SEVEN_DAYS_MS - 60_000:
            return list(merged.values())

        # An empty week says nothing about an older closed position. Bound full
        # closed-PnL scans by the supported history horizon, including the last partial week.
        w_end = now_ms - SEVEN_DAYS_MS + 60_000
        empty_streak = 0
        min_windows_before_early_stop = 26 if since_ms <= 0 else 8
        for win_idx in range(1, windows):
            if scan_sparse_history:
                # The exchange checks retention at arrival, including time spent on earlier pages.
                since_ms = max(since_ms, self._clock_ms() - CLOSED_PNL_RETENTION_MS + CLOSED_PNL_CLOCK_MARGIN_MS)
            if since_ms > 0 and w_end < since_ms:
                break
            w_start = max(since_ms, w_end - SEVEN_DAYS_MS) if since_ms > 0 else (w_end - SEVEN_DAYS_MS)
            win_rows = await self._paginate_cursor(
                path,
                {**base_params, "startTime": w_start, "endTime": w_end},
                id_fn=id_fn,
                limit=limit,
                closed_pnl_rows=closed_pnl_rows,
            )
            if scan_sparse_history:
                w_end = w_start - 1
                continue
            if win_rows:
                # Guard against static test mocks that ignore startTime/endTime and return out-of-window rows
                new_in_win = 0
                in_bounds = False
                for r in win_rows:
                    rid = id_fn(r)
                    if rid not in merged:
                        merged[rid] = r
                        new_in_win += 1
                    try:
                        rts = int(r.get(ts_field) or 0)
                        if w_start - 86_400_000 <= rts <= w_end + 86_400_000:
                            in_bounds = True
                    except (TypeError, ValueError):
                        pass
                if new_in_win == 0 and not in_bounds:
                    break
                if new_in_win == 0:
                    empty_streak += 1
                    if win_idx >= min_windows_before_early_stop and empty_streak >= 8:
                        break
                else:
                    empty_streak = 0
            else:
                empty_streak += 1
                if win_idx >= min_windows_before_early_stop and empty_streak >= 8:
                    break
            w_end = w_start - 1

        return list(merged.values())

    async def _paginate_30d_windows(
        self,
        path: str,
        base_params: dict[str, Any],
        *,
        id_fn: Callable[[dict[str, Any]], str],
        limit: int = 50,
        since_ms: int = 0,
    ) -> list[dict[str, Any]]:
        """Paginates Bybit v5 asset deposit/withdrawal history via cursor + 30-day windows up to 1 year."""
        eff_limit = min(50, max(1, int(limit)))
        merged: dict[str, dict[str, Any]] = {}

        first_rows = await self._paginate_cursor(
            path,
            base_params,
            list_key="rows",
            id_fn=id_fn,
            limit=eff_limit,
        )
        for r in first_rows:
            merged[id_fn(r)] = r

        if self.max_windows <= 1:
            return list(merged.values())

        now_ms = self._clock_ms()
        if since_ms > 0 and now_ms - since_ms <= THIRTY_DAYS_MS - 60_000:
            return list(merged.values())

        w_end = now_ms - THIRTY_DAYS_MS + 60_000
        max_30d_windows = min(13, self.max_windows)
        for _ in range(1, max_30d_windows):
            if since_ms > 0 and w_end < since_ms:
                break
            w_start = max(since_ms, w_end - THIRTY_DAYS_MS) if since_ms > 0 else (w_end - THIRTY_DAYS_MS)
            win_rows = await self._paginate_cursor(
                path,
                {**base_params, "startTime": w_start, "endTime": w_end},
                list_key="rows",
                id_fn=id_fn,
                limit=eff_limit,
            )
            if win_rows:
                new_in_win = 0
                for r in win_rows:
                    rid = id_fn(r)
                    if rid not in merged:
                        merged[rid] = r
                        new_in_win += 1
                if new_in_win == 0:
                    break
            w_end = w_start - 1

        return list(merged.values())

    async def get_api_key_info(self) -> dict[str, Any]:
        return await self.request_readonly("GET", "/v5/user/query-api")

    async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
        try:
            res = await self.request_readonly("GET", "/v5/account/wallet-balance", {"accountType": account_type})
        except BybitApiError as exc:
            if "Malformed Bybit payload" in str(exc):
                raise BybitApiError("Malformed Bybit wallet-balance payload", status_code=exc.status_code) from exc
            raise
        if not isinstance(res, dict) or not isinstance(res.get("list"), list):
            raise BybitApiError("Malformed Bybit wallet-balance payload")
        for account in res["list"]:
            if not isinstance(account, dict) or not optional_amounts(account, "totalAvailableBalance"):
                raise BybitApiError("Malformed Bybit wallet-balance payload")
            coins = account.get("coin")
            if "coin" in account and not wallet_rows(
                coins, names=("coin",), amounts=("walletBalance",), optional=("usdValue",),
            ):
                raise BybitApiError("Malformed Bybit wallet-balance payload")
            # Isolated margin may omit the account-wide value with an explicit empty
            # string. An unfiltered response with complete coin USD values is sufficient.
            if account.get("totalEquity") == "" and isinstance(coins, list) and all(
                finite_amount(coin.get("usdValue")) for coin in coins
            ):
                account["totalEquity"] = str(sum((Decimal(str(coin["usdValue"])) for coin in coins), Decimal(0)))
            if not finite_amount(account.get("totalEquity")):
                raise BybitApiError("Malformed Bybit wallet-balance payload")
        return res

    async def get_funding_balance(self) -> dict[str, Any]:
        try:
            res = await self.request_readonly(
                "GET", "/v5/asset/transfer/query-account-coins-balance", {"accountType": "FUND"}
            )
        except BybitApiError as exc:
            if "Malformed Bybit payload" in str(exc):
                raise BybitApiError("Malformed Bybit funding balance payload", status_code=exc.status_code, ret_code="INVALID_WALLET") from exc
            raise
        except ValueError as exc:
            raise BybitApiError("Malformed Bybit funding balance payload", ret_code="INVALID_WALLET") from exc
        if not valid_funding_balance(res):
            raise BybitApiError("Malformed Bybit funding balance payload", ret_code="INVALID_WALLET")
        return res

    async def get_positions(self, category: str = "linear", settle_coin: str = "USDT") -> list[dict[str, Any]]:
        res = await self.request_readonly(
            "GET", "/v5/position/list", {"category": category, "settleCoin": settle_coin}
        )
        return res.get("list") or []

    async def get_closed_pnl(
        self, category: str = "linear", limit: Optional[int] = None, since_ms: int = 0,
        *, received: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return await self._paginate_7d_windows(
            "/v5/position/closed-pnl",
            {"category": category},
            id_fn=lambda r: f"{r.get('orderId')}_{r.get('updatedTime')}_{r.get('symbol')}",
            ts_field="updatedTime",
            closed_pnl_rows=received if received is not None else {},
            since_ms=since_ms,
            limit=limit,
        )

    async def get_executions(
        self, category: str = "linear", limit: Optional[int] = None, since_ms: int = 0
    ) -> list[dict[str, Any]]:
        return await self._paginate_7d_windows(
            "/v5/execution/list",
            {"category": category},
            id_fn=lambda r: str(r.get("execId") or f"{r.get('orderId')}_{r.get('execTime')}"),
            ts_field="execTime",
            since_ms=since_ms,
            limit=limit,
        )

    async def get_fee_rate(self, category: str = "linear") -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/v5/account/fee-rate", {"category": category})
        return res.get("list") or []

    async def get_transaction_log(
        self, account_type: str = "UNIFIED", limit: int = 50, since_ms: int = 0
    ) -> list[dict[str, Any]]:
        return await self._paginate_7d_windows(
            "/v5/account/transaction-log",
            {"accountType": account_type},
            id_fn=lambda r: str(r.get("id") or f"{r.get('transactionTime')}_{r.get('type')}_{r.get('symbol')}_{r.get('cashFlow')}"),
            ts_field="transactionTime",
            since_ms=since_ms,
            limit=min(50, limit),
        )

    async def get_deposits(self, limit: int = 50, since_ms: int = 0) -> list[dict[str, Any]]:
        return await self._paginate_30d_windows(
            "/v5/asset/deposit/query-record",
            {},
            id_fn=lambda r: str(r.get("id") or r.get("txID") or f"{r.get('successAt')}_{r.get('amount')}"),
            limit=min(50, limit),
            since_ms=since_ms,
        )

    async def get_internal_deposits(self, limit: int = 50, since_ms: int = 0) -> list[dict[str, Any]]:
        return await self._paginate_30d_windows(
            "/v5/asset/deposit/query-internal-record",
            {},
            id_fn=lambda r: str(r.get("id") or f"{r.get('createdTime')}_{r.get('amount')}"),
            limit=min(50, limit),
            since_ms=since_ms,
        )

    async def get_withdrawals(self, limit: int = 50, since_ms: int = 0) -> list[dict[str, Any]]:
        return await self._paginate_30d_windows(
            "/v5/asset/withdraw/query-record",
            {},
            id_fn=lambda r: str(r.get("withdrawId") or r.get("txID") or f"{r.get('createTime')}_{r.get('amount')}"),
            limit=min(50, limit),
            since_ms=since_ms,
        )

    async def get_spot_tickers(self) -> dict[str, float]:
        res = await self.request_readonly("GET", "/v5/market/tickers", {"category": "spot"})
        prices: dict[str, float] = {}
        for row in res.get("list") or []:
            sym = str(row.get("symbol") or "").upper()
            for quote in ("USDT", "USDC"):
                if sym.endswith(quote) and len(sym) > len(quote):
                    base = sym[: -len(quote)]
                    try:
                        px = float(row.get("lastPrice") or 0.0)
                        if px > 0:
                            prices[base] = px
                    except (TypeError, ValueError):
                        pass
                    break
        return prices

    async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
        """Collects read-only Unified/Contract account snapshot with multi-page history and graceful degradation."""
        warnings: list[str] = []
        failed_fields: list[str] = []
        invalid_wallet_fields: list[str] = []
        try:
            wallet = await self.get_wallet_balance("UNIFIED")
        except Exception as unified_exc:
            if _is_fatal_bybit_error(unified_exc) or _is_bybit_auth_error(unified_exc):
                raise
            if not _is_bybit_account_type_unsupported(unified_exc):
                raise
            try:
                wallet = await self.get_wallet_balance("CONTRACT")
                warnings.append(f"wallet_unified_fallback: {unified_exc}")
            except Exception as contract_exc:
                if _is_fatal_bybit_error(contract_exc):
                    raise contract_exc
                raise unified_exc

        async def _opt(name: str, coro: Any, default: Any) -> Any:
            try:
                return await coro
            except Exception as exc:
                if _is_fatal_bybit_error(exc):
                    raise
                if isinstance(exc, BybitApiError) and exc.ret_code == "INVALID_WALLET":
                    invalid_wallet_fields.append(name)
                warnings.append(f"{name}: {exc}")
                failed_fields.append(name)
                return default

        funding = await _opt("funding", self.get_funding_balance(), {})
        positions_usdt = await _opt("positions_usdt", self.get_positions("linear", "USDT"), [])
        positions_usdc = await _opt("positions_usdc", self.get_positions("linear", "USDC"), [])
        positions = list(positions_usdt) + list(positions_usdc)
        received_pnl: dict[str, dict[str, Any]] = {}
        closed_pnl = await _opt("closed_pnl", self.get_closed_pnl("linear", since_ms=since_ms, received=received_pnl), None)
        if closed_pnl is None:
            closed_pnl = list(received_pnl.values())
        executions = await _opt("executions", self.get_executions("linear", since_ms=since_ms), [])
        fee_rates = await _opt("fee_rates", self.get_fee_rate("linear"), [])
        tx_logs = await _opt("tx_logs", self.get_transaction_log("UNIFIED", since_ms=since_ms), [])
        deposits = await _opt("deposits", self.get_deposits(since_ms=since_ms), [])
        internal_deposits = await _opt("internal_deposits", self.get_internal_deposits(since_ms=since_ms), [])
        withdrawals = await _opt("withdrawals", self.get_withdrawals(since_ms=since_ms), [])
        tickers = await _opt("tickers", self.get_spot_tickers(), {})

        gross_fee_sum = sum(
            abs(float(r.get("openFee") or 0.0)) + abs(float(r.get("closeFee") or 0.0))
            for r in closed_pnl
        )
        if gross_fee_sum <= 0:
            gross_fee_sum = sum(abs(float(e.get("execFee") or 0.0)) for e in executions)
        rebate_sum = sum(
            max(0.0, float(tx.get("cashFlow") or tx.get("change") or 0.0))
            for tx in tx_logs
            if _is_bybit_rebate_log(tx)
        )
        observed_rebate_ratio = (
            min(0.95, rebate_sum / gross_fee_sum) if gross_fee_sum > 0 and rebate_sum > 0 else 0.0
        )

        if "positions_usdt" in failed_fields and "positions_usdc" in failed_fields:
            failed_fields.append("positions")
        out_snap: dict[str, Any] = {
            "venue_id": "bybit",
            "wallet": wallet,
            "funding": funding,
            "positions": positions,
            "closed_pnl": closed_pnl,
            "executions": executions,
            "fee_rates": fee_rates,
            "tx_logs": tx_logs,
            "deposits": deposits,
            "internal_deposits": internal_deposits,
            "withdrawals": withdrawals,
            "tickers": tickers,
            "observed_rebate_ratio": round(observed_rebate_ratio, 6),
            "warnings": warnings,
        }
        if invalid_wallet_fields:
            out_snap["_invalid_wallet_fields"] = invalid_wallet_fields
        if failed_fields:
            out_snap["_failed_fields"] = failed_fields
        return out_snap


def _is_bybit_rebate_log(tx: dict[str, Any]) -> bool:
    t = str(tx.get("type") or "").strip().upper()
    return t in {"REBATE", "COMMISSION", "CASHBACK", "FEE_REFUND", "AFFILIATE"} or "REBATE" in t


def _normalize_bybit_symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if "_" in s:
        return s
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}_{quote}"
    return s


def normalize_bybit_closed_trades(
    closed_pnl_rows: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    *,
    tx_logs: Optional[list[dict[str, Any]]] = None,
    rebate_ratio: float = 0.0,
) -> list[dict[str, Any]]:
    """Builds normalized closed trades with per-leg fees, SETTLEMENT funding, REBATE logs, and MNT discounts."""
    execs_by_order: dict[str, list[dict[str, Any]]] = {}
    open_pool_by_bucket: dict[tuple[str, str], list[dict[str, Any]]] = {}
    sorted_execs = sorted(
        executions,
        key=lambda x: (int(x.get("execTime") or 0), str(x.get("execId") or "")),
    )
    for ex_idx, ex in enumerate(sorted_execs):
        oid = str(ex.get("orderId") or "")
        sym = str(ex.get("symbol") or "").upper()
        if oid:
            execs_by_order.setdefault(oid, []).append(ex)
        if sym and float(ex.get("closedSize") or 0.0) == 0.0:
            ex_side = str(ex.get("side") or "").lower()
            pos_side_ex = "LONG" if ex_side == "buy" else ("SHORT" if ex_side == "sell" else "")
            if pos_side_ex:
                ex_q = abs(float(ex.get("execQty") or ex.get("qty") or 0.0))
                open_pool_by_bucket.setdefault((sym, pos_side_ex), []).append(
                    {
                        "ex": ex,
                        "orig_qty": ex_q,
                        "rem_share": 1.0,
                        "ts": int(ex.get("execTime") or 0),
                        "anchor": oid or str(ex.get("execId") or f"open_{ex_idx}"),
                    }
                )

    funding_by_sym: dict[str, list[dict[str, Any]]] = {}
    rebates_by_sym: dict[str, list[dict[str, Any]]] = {}
    consumed_fund_ids: set[str] = set()
    consumed_reb_ids: set[str] = set()

    for idx, tx in enumerate(tx_logs or []):
        tx_id = str(tx.get("id") or f"tx_{idx}")
        sym = str(tx.get("symbol") or "").upper()
        tx_type = str(tx.get("type") or "").strip().upper()
        if tx_type == "SETTLEMENT" and sym:
            f_val = float(tx.get("funding") if tx.get("funding") not in (None, "") else (tx.get("cashFlow") or 0.0))
            funding_by_sym.setdefault(sym, []).append({**tx, "_id": tx_id, "_val": f_val})
        elif _is_bybit_rebate_log(tx):
            r_val = max(0.0, float(tx.get("cashFlow") or tx.get("change") or 0.0))
            if r_val > 0:
                rebates_by_sym.setdefault(sym, []).append({**tx, "_id": tx_id, "_val": r_val})

    sorted_rows = sorted(
        closed_pnl_rows,
        key=lambda r: (int(r.get("updatedTime") or 0), int(r.get("createdTime") or 0)),
    )

    row_drafts: list[dict[str, Any]] = []
    for row in sorted_rows:
        raw_sym = str(row.get("symbol") or "").upper()
        if not raw_sym:
            continue
        order_id = str(row.get("orderId") or "")
        close_side = str(row.get("side") or "Sell").lower()
        side = "LONG" if close_side == "sell" else "SHORT"
        qty = abs(float(row.get("closedSize") or row.get("qty") or 0.0))
        entry_px = float(row.get("avgEntryPrice") or 0.0)
        exit_px = float(row.get("avgExitPrice") or 0.0)
        entry_notional = float(row.get("cumEntryValue") or (entry_px * qty))
        exit_notional = float(row.get("cumExitValue") or (exit_px * qty))
        open_ms = int(row.get("createdTime") or 0)
        close_ms = int(row.get("updatedTime") or open_ms)

        exit_execs = execs_by_order.get(order_id, [])
        matched_entry_slices: list[tuple[dict[str, Any], float]] = []
        rem_need = qty
        cycle_anchor = ""
        for slot in open_pool_by_bucket.get((raw_sym, side), []):
            if slot["rem_share"] <= 1e-9 or slot["ts"] > close_ms + 5_000:
                continue
            orig_q = float(slot["orig_qty"])
            rem_sh = float(slot["rem_share"])
            if orig_q > 0 and qty > 0:
                avail_q = orig_q * rem_sh
                if avail_q <= 1e-12:
                    slot["rem_share"] = 0.0
                    continue
                if avail_q > rem_need + 1e-9:
                    take_sh = rem_sh * (rem_need / avail_q)
                    slot["rem_share"] = max(0.0, rem_sh - take_sh)
                    rem_need = 0.0
                else:
                    take_sh = rem_sh
                    slot["rem_share"] = 0.0
                    rem_need = max(0.0, rem_need - avail_q)
            else:
                take_sh = rem_sh
                slot["rem_share"] = 0.0
                rem_need = 0.0
            matched_entry_slices.append((slot["ex"], take_sh))
            if not cycle_anchor:
                cycle_anchor = str(slot["anchor"])
            if rem_need <= 1e-9:
                break

        entry_execs = [ex for ex, _ in matched_entry_slices]
        if matched_entry_slices:
            earliest_en_ts = min(int(ex.get("execTime") or open_ms) for ex in entry_execs)
            if earliest_en_ts > 0 and (open_ms <= 0 or earliest_en_ts < open_ms):
                open_ms = earliest_en_ts

        exit_gross_fee = sum(abs(float(e.get("execFee") or 0.0)) for e in exit_execs)
        entry_gross_fee = sum(abs(float(e.get("execFee") or 0.0)) * sh for e, sh in matched_entry_slices)
        open_fee_field = abs(float(row.get("openFee") or 0.0))
        close_fee_field = abs(float(row.get("closeFee") or 0.0))
        if open_fee_field > 0:
            entry_gross_fee = open_fee_field
        if close_fee_field > 0:
            exit_gross_fee = close_fee_field

        row_drafts.append(
            {
                "raw_sym": raw_sym,
                "order_id": order_id,
                "side": side,
                "qty": qty,
                "row_qty": abs(float(row.get("qty") or 0.0)),
                "entry_px": entry_px,
                "exit_px": exit_px,
                "entry_notional": entry_notional,
                "exit_notional": exit_notional,
                "open_ms": open_ms,
                "close_ms": close_ms,
                "entry_execs": entry_execs,
                "exit_execs": exit_execs,
                "entry_gross_fee": entry_gross_fee,
                "exit_gross_fee": exit_gross_fee,
                "closed_pnl": float(row.get("closedPnl") or 0.0),
                "cycle_anchor": cycle_anchor,
            }
        )

    grouped_drafts: list[list[dict[str, Any]]] = []
    anchor_map: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for dr in row_drafts:
        anc = dr["cycle_anchor"]
        if anc:
            gk = (dr["raw_sym"], dr["side"], anc)
            if gk not in anchor_map:
                grp_list: list[dict[str, Any]] = []
                anchor_map[gk] = grp_list
                grouped_drafts.append(grp_list)
            anchor_map[gk].append(dr)
        else:
            grouped_drafts.append([dr])

    records: list[dict[str, Any]] = []
    for grp in grouped_drafts:
        last_dr = grp[-1]
        raw_sym = last_dr["raw_sym"]
        order_id = last_dr["order_id"]
        side = last_dr["side"]
        qty = sum(d["qty"] for d in grp)
        entry_notional = sum(d["entry_notional"] for d in grp)
        exit_notional = sum(d["exit_notional"] for d in grp)
        entry_px = (entry_notional / qty) if qty > 0 else last_dr["entry_px"]
        exit_px = (exit_notional / qty) if qty > 0 else last_dr["exit_px"]
        open_ms = min(d["open_ms"] for d in grp)
        close_ms = max(d["close_ms"] for d in grp)
        entry_execs = [e for d in grp for e in d["entry_execs"]]
        exit_execs = [e for d in grp for e in d["exit_execs"]]
        entry_gross_fee = sum(d["entry_gross_fee"] for d in grp)
        exit_gross_fee = sum(d["exit_gross_fee"] for d in grp)

        # Detect MNT token fee discount (25% UTA fee discount -> saved = paid * (0.25 / 0.75))
        entry_mnt = any(str(e.get("feeCurrency") or "").upper() == "MNT" for e in entry_execs)
        exit_mnt = any(str(e.get("feeCurrency") or "").upper() == "MNT" for e in exit_execs)
        entry_disc = round(entry_gross_fee * (0.25 / 0.75), 6) if entry_mnt and entry_gross_fee > 0 else 0.0
        exit_disc = round(exit_gross_fee * (0.25 / 0.75), 6) if exit_mnt and exit_gross_fee > 0 else 0.0

        en_makers = any(bool(e.get("isMaker")) for e in entry_execs)
        en_takers = any(not bool(e.get("isMaker")) for e in entry_execs)
        ex_makers = any(bool(e.get("isMaker")) for e in exit_execs)
        ex_takers = any(not bool(e.get("isMaker")) for e in exit_execs)
        entry_is_maker = en_makers and not en_takers
        exit_is_maker = ex_makers and not ex_takers
        entry_mode_code = "MIXED" if (en_makers and en_takers) else ("LIMIT_MAKER" if en_makers else "MARKET_TAKER")
        exit_mode_code = "MIXED" if (ex_makers and ex_takers) else ("LIMIT_MAKER" if ex_makers else "MARKET_TAKER")
        entry_bps = (entry_gross_fee / entry_notional * 10_000) if entry_notional > 0 else 0.0
        exit_bps = (exit_gross_fee / exit_notional * 10_000) if exit_notional > 0 else 0.0

        # Match SETTLEMENT funding logs within [open_ms, close_ms]
        funding_pnl = 0.0
        for fl in funding_by_sym.get(raw_sym, []):
            if fl["_id"] in consumed_fund_ids:
                continue
            fts = int(fl.get("transactionTime") or 0)
            if open_ms - 5_000 <= fts <= close_ms + 5_000:
                consumed_fund_ids.add(fl["_id"])
                funding_pnl += float(fl["_val"])

        total_fee = round(entry_gross_fee + exit_gross_fee, 6)

        # Match explicit REBATE logs on this symbol first; fall back to observed_rebate_ratio
        matched_reb_amt = 0.0
        settled_ts = 0
        for rb in rebates_by_sym.get(raw_sym, []):
            if rb["_id"] in consumed_reb_ids:
                continue
            rts = int(rb.get("transactionTime") or 0)
            if open_ms - 5_000 <= rts <= close_ms + 86_400_000:
                consumed_reb_ids.add(rb["_id"])
                matched_reb_amt += float(rb["_val"])
                settled_ts = max(settled_ts, rts)

        if matched_reb_amt > 0 and total_fee > 0:
            eff_ratio = min(0.95, matched_reb_amt / total_fee)
            entry_rebate = round(matched_reb_amt * (entry_gross_fee / total_fee), 6)
            exit_rebate = round(matched_reb_amt - entry_rebate, 6)
            settled_str = _fmt_bj_ms(settled_ts)
        else:
            eff_ratio = max(0.0, min(1.0, rebate_ratio))
            entry_rebate = round(entry_gross_fee * eff_ratio, 6)
            exit_rebate = round(exit_gross_fee * eff_ratio, 6)
            settled_str = ""

        entry_net_fee = round(max(0.0, entry_gross_fee - entry_rebate), 6)
        exit_net_fee = round(max(0.0, exit_gross_fee - exit_rebate), 6)

        raw_closed_pnl = sum(d["closed_pnl"] for d in grp)
        total_rebate = round(entry_rebate + exit_rebate, 6)
        gross_pnl = round(raw_closed_pnl + total_fee, 6)
        book_pnl = round(raw_closed_pnl + funding_pnl, 6)
        net_pnl = round(book_pnl + total_rebate, 6)
        is_partial_bb = len(grp) > 1 or any(d["qty"] < d["row_qty"] for d in grp)

        bb_exit_batches: list[dict[str, Any]] = []
        if len(grp) > 1:
            ex_reb_rate = (exit_rebate / exit_gross_fee) if exit_gross_fee > 0 else eff_ratio
            rem_bb_gpnl = gross_pnl
            for b_i, d_it in enumerate(grp):
                b_last = b_i == len(grp) - 1
                b_exs = d_it["exit_execs"]
                b_fee = float(d_it["exit_gross_fee"])
                b_mnt = any(str(e.get("feeCurrency") or "").upper() == "MNT" for e in b_exs)
                b_disc = round(b_fee * (0.25 / 0.75), 4) if b_mnt and b_fee > 0 else 0.0
                b_reb = round(b_fee * ex_reb_rate, 4)
                b_net_f = round(max(0.0, b_fee - b_reb), 4)
                b_mk = any(bool(e.get("isMaker")) for e in b_exs)
                b_tk = any(not bool(e.get("isMaker")) for e in b_exs)
                b_role = "MAKER" if (b_mk and not b_tk) else "TAKER"
                b_mode = "MIXED" if (b_mk and b_tk) else ("LIMIT_MAKER" if b_mk else "MARKET_TAKER")
                b_not = float(d_it["exit_notional"])
                b_bps = round(b_fee / b_not * 10_000, 2) if b_not > 0 else 0.0
                b_gpnl = rem_bb_gpnl if b_last else (float(d_it["closed_pnl"]) + float(d_it["entry_gross_fee"]) + b_fee)
                rem_bb_gpnl -= b_gpnl
                b_ts = int(d_it["close_ms"])
                bb_exit_batches.append(
                    {
                        "batch_index": b_i + 1,
                        "qty": round(float(d_it["qty"]), 6),
                        "role": b_role,
                        "mode_code": b_mode,
                        "fee_known": True,
                        "fee_bps": b_bps,
                        "time_ms": b_ts,
                        "time_str": _fmt_bj_ms(b_ts),
                        "avg_price": round(float(d_it["exit_px"]), 6),
                        "notional_usd": round(b_not, 2),
                        "gross_fee_usd": round(b_fee, 4),
                        "discount_saved_usd": b_disc,
                        "discount_asset": "MNT" if b_mnt else "",
                        "rebate_usd": b_reb,
                        "net_fee_usd": b_net_f,
                        "gross_pnl_usd": round(b_gpnl, 4),
                        "net_pnl_usd": round(b_gpnl - b_net_f, 4),
                    }
                )

        sym = _normalize_bybit_symbol(raw_sym)
        records.append(
            {
                "trade_id": f"bybit_{order_id or sym}_{close_ms}",
                "venue_id": "bybit",
                "venue_name": "Bybit",
                "venue_name_zh": "Bybit",
                "symbol": sym,
                "side": side,
                "qty": round(qty, 6),
                "open_time_ms": open_ms,
                "close_time_ms": close_ms,
                "gross_pnl_usd": round(gross_pnl, 4),
                "funding_pnl_usd": round(funding_pnl, 4),
                "book_pnl_usd": round(book_pnl, 4),
                "total_fee_usd": round(total_fee, 4),
                "total_rebate_usd": round(total_rebate, 4),
                "total_discount_saved_usd": round(entry_disc + exit_disc, 4),
                "net_fee_usd": round(entry_net_fee + exit_net_fee, 4),
                "net_pnl_usd": round(net_pnl, 4),
                "rebate_status": "SETTLED",
                "is_partial_close": is_partial_bb,
                "entry_leg": {
                    "role": "MAKER" if entry_is_maker else "TAKER",
                    "mode_code": entry_mode_code,
                    "fee_bps": round(entry_bps, 2),
                    "time_ms": open_ms,
                    "avg_price": round(entry_px, 6),
                    "notional_usd": round(entry_notional, 2),
                    "gross_fee_usd": round(entry_gross_fee, 4),
                    "discount_saved_usd": round(entry_disc, 4),
                    "discount_asset": "MNT" if entry_mnt else "",
                    "rebate_usd": round(entry_rebate, 4),
                    "rebate_pct": round((entry_rebate / entry_gross_fee) * 100.0, 2) if entry_gross_fee > 0 else round(eff_ratio * 100.0, 2),
                    "net_fee_usd": round(entry_net_fee, 4),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
                "exit_leg": {
                    "role": "MAKER" if exit_is_maker else "TAKER",
                    "mode_code": exit_mode_code,
                    "fee_bps": round(exit_bps, 2),
                    "time_ms": close_ms,
                    "avg_price": round(exit_px, 6),
                    "notional_usd": round(exit_notional, 2),
                    "gross_fee_usd": round(exit_gross_fee, 4),
                    "discount_saved_usd": round(exit_disc, 4),
                    "discount_asset": "MNT" if exit_mnt else "",
                    "rebate_usd": round(exit_rebate, 4),
                    "rebate_pct": round((exit_rebate / exit_gross_fee) * 100.0, 2) if exit_gross_fee > 0 else round(eff_ratio * 100.0, 2),
                    "net_fee_usd": round(exit_net_fee, 4),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
                "exit_batches": bb_exit_batches,
            }
        )
    records.sort(key=lambda r: (r["close_time_ms"], r["open_time_ms"]), reverse=True)
    return records
