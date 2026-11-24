"""Strictly GET-only Bybit API v5 Unified Account signed client and actuarial per-leg trade normalizer.

Signature specification (Bybit v5 GET):
  payload = timestamp_ms + api_key + recv_window + sorted_query_string
  X-BAPI-SIGN = hex(HMAC-SHA256(payload, api_secret))
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx

from opentradeledger.security.readonly_guard import assert_readonly_request

TZ_UTC8 = timezone(timedelta(hours=8))
SEVEN_DAYS_MS = 7 * 86_400 * 1000

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


class BybitApiError(RuntimeError):
    """Raised when Bybit v5 returns retCode != 0 or an HTTP error."""


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
        max_windows: int = 13,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = str(recv_window)
        self._client = client
        self._timeout = timeout_sec
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.page_limit = max(1, min(100, int(page_limit)))
        self.max_pages = max(1, int(max_pages))
        self.max_windows = max(1, int(max_windows))

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

        if self._client is not None:
            resp = await self._client.get(url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.get(url, headers=headers)

        if resp.status_code >= 400:
            raise BybitApiError(f"Bybit GET {path} HTTP {resp.status_code}: {resp.text[:240]}")
        payload = resp.json()
        if int(payload.get("retCode", -1)) != 0:
            raise BybitApiError(
                f"Bybit GET {path} retCode={payload.get('retCode')}: {payload.get('retMsg', '')}"
            )
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    async def _paginate_cursor(
        self,
        path: str,
        base_params: dict[str, Any],
        *,
        list_key: str = "list",
        id_fn: Callable[[dict[str, Any]], str],
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        eff_limit = limit or self.page_limit
        collected: dict[str, dict[str, Any]] = {}
        cursor = ""
        seen_cursors: set[str] = set()

        for _ in range(self.max_pages):
            params = {**base_params, "limit": eff_limit}
            if cursor:
                params["cursor"] = cursor
            res = await self.request_readonly("GET", path, params)
            batch = res.get(list_key) or []
            if not isinstance(batch, list) or not batch:
                break

            new_count = 0
            for row in batch:
                if isinstance(row, dict):
                    rid = id_fn(row)
                    if rid not in collected:
                        collected[rid] = row
                        new_count += 1

            next_cursor = str(res.get("nextPageCursor") or "").strip()
            if not next_cursor or next_cursor in seen_cursors or new_count == 0:
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
        since_ms: int = 0,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Fetches recent default window first, then steps backwards in 7-day chunks up to max_windows."""
        merged: dict[str, dict[str, Any]] = {}

        # Window 0: default recent 7d call (preserves compatibility with simple mocks & catches latest trades)
        first_rows = await self._paginate_cursor(path, base_params, id_fn=id_fn, limit=limit)
        for r in first_rows:
            merged[id_fn(r)] = r

        if self.max_windows <= 1:
            return list(merged.values())

        now_ms = self._clock_ms()
        if since_ms > 0 and now_ms - since_ms <= SEVEN_DAYS_MS - 60_000:
            return list(merged.values())

        # Step backwards from (now - 7d) in 7-day chunks
        w_end = now_ms - SEVEN_DAYS_MS + 60_000
        for _ in range(1, self.max_windows):
            if since_ms > 0 and w_end < since_ms:
                break
            w_start = max(since_ms, w_end - SEVEN_DAYS_MS) if since_ms > 0 else (w_end - SEVEN_DAYS_MS)
            win_rows = await self._paginate_cursor(
                path,
                {**base_params, "startTime": w_start, "endTime": w_end},
                id_fn=id_fn,
                limit=limit,
            )
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
            w_end = w_start - 1

        return list(merged.values())

    async def get_api_key_info(self) -> dict[str, Any]:
        return await self.request_readonly("GET", "/v5/user/query-api")

    async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
        return await self.request_readonly("GET", "/v5/account/wallet-balance", {"accountType": account_type})

    async def get_funding_balance(self) -> dict[str, Any]:
        return await self.request_readonly(
            "GET", "/v5/asset/transfer/query-account-coins-balance", {"accountType": "FUND"}
        )

    async def get_positions(self, category: str = "linear", settle_coin: str = "USDT") -> list[dict[str, Any]]:
        res = await self.request_readonly(
            "GET", "/v5/position/list", {"category": category, "settleCoin": settle_coin}
        )
        return res.get("list") or []

    async def get_closed_pnl(
        self, category: str = "linear", limit: Optional[int] = None, since_ms: int = 0
    ) -> list[dict[str, Any]]:
        return await self._paginate_7d_windows(
            "/v5/position/closed-pnl",
            {"category": category},
            id_fn=lambda r: f"{r.get('orderId')}_{r.get('updatedTime')}_{r.get('symbol')}",
            ts_field="updatedTime",
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

    async def get_deposits(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self._paginate_cursor(
            "/v5/asset/deposit/query-record",
            {},
            list_key="rows",
            id_fn=lambda r: str(r.get("id") or r.get("txID") or f"{r.get('successAt')}_{r.get('amount')}"),
            limit=min(50, limit),
        )

    async def get_internal_deposits(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self._paginate_cursor(
            "/v5/asset/deposit/query-internal-record",
            {},
            list_key="rows",
            id_fn=lambda r: str(r.get("id") or f"{r.get('createdTime')}_{r.get('amount')}"),
            limit=min(50, limit),
        )

    async def get_withdrawals(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self._paginate_cursor(
            "/v5/asset/withdraw/query-record",
            {},
            list_key="rows",
            id_fn=lambda r: str(r.get("withdrawId") or r.get("txID") or f"{r.get('createTime')}_{r.get('amount')}"),
            limit=min(50, limit),
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
        try:
            wallet = await self.get_wallet_balance("UNIFIED")
        except Exception as unified_exc:
            try:
                wallet = await self.get_wallet_balance("CONTRACT")
                warnings.append(f"wallet_unified_fallback: {unified_exc}")
            except Exception:
                raise unified_exc

        async def _opt(name: str, coro: Any, default: Any) -> Any:
            try:
                return await coro
            except Exception as exc:
                warnings.append(f"{name}: {exc}")
                return default

        funding = await _opt("funding", self.get_funding_balance(), {})
        positions_usdt = await _opt("positions_usdt", self.get_positions("linear", "USDT"), [])
        positions_usdc = await _opt("positions_usdc", self.get_positions("linear", "USDC"), [])
        positions = list(positions_usdt) + list(positions_usdc)
        closed_pnl = await _opt("closed_pnl", self.get_closed_pnl("linear", since_ms=since_ms), [])
        executions = await _opt("executions", self.get_executions("linear", since_ms=since_ms), [])
        fee_rates = await _opt("fee_rates", self.get_fee_rate("linear"), [])
        tx_logs = await _opt("tx_logs", self.get_transaction_log("UNIFIED", since_ms=since_ms), [])
        deposits = await _opt("deposits", self.get_deposits(), [])
        internal_deposits = await _opt("internal_deposits", self.get_internal_deposits(), [])
        withdrawals = await _opt("withdrawals", self.get_withdrawals(), [])
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

        return {
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
    execs_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for ex in executions:
        oid = str(ex.get("orderId") or "")
        sym = str(ex.get("symbol") or "").upper()
        if oid:
            execs_by_order.setdefault(oid, []).append(ex)
        if sym:
            execs_by_symbol.setdefault(sym, []).append(ex)

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

    records: list[dict[str, Any]] = []
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
        open_side_str = "buy" if side == "LONG" else "sell"
        entry_execs = [
            e
            for e in execs_by_symbol.get(raw_sym, [])
            if str(e.get("side") or "").lower() == open_side_str
            and int(e.get("execTime") or 0) <= close_ms
            and float(e.get("closedSize") or 0.0) == 0.0
        ]

        exit_gross_fee = sum(abs(float(e.get("execFee") or 0.0)) for e in exit_execs)
        entry_gross_fee = sum(abs(float(e.get("execFee") or 0.0)) for e in entry_execs)
        open_fee_field = abs(float(row.get("openFee") or 0.0))
        close_fee_field = abs(float(row.get("closeFee") or 0.0))
        if open_fee_field > 0:
            entry_gross_fee = open_fee_field
        if close_fee_field > 0:
            exit_gross_fee = close_fee_field

        # Detect MNT token fee discount (25% UTA fee discount -> saved = paid * (0.25 / 0.75))
        entry_mnt = any(str(e.get("feeCurrency") or "").upper() == "MNT" for e in entry_execs)
        exit_mnt = any(str(e.get("feeCurrency") or "").upper() == "MNT" for e in exit_execs)
        entry_disc = round(entry_gross_fee * (0.25 / 0.75), 6) if entry_mnt and entry_gross_fee > 0 else 0.0
        exit_disc = round(exit_gross_fee * (0.25 / 0.75), 6) if exit_mnt and exit_gross_fee > 0 else 0.0

        entry_is_maker = any(bool(e.get("isMaker")) for e in entry_execs)
        exit_is_maker = any(bool(e.get("isMaker")) for e in exit_execs)
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

        raw_closed_pnl = float(row.get("closedPnl") or 0.0)
        total_rebate = round(entry_rebate + exit_rebate, 6)
        gross_pnl = round(raw_closed_pnl + total_fee, 6)
        book_pnl = round(raw_closed_pnl + funding_pnl, 6)
        net_pnl = round(book_pnl + total_rebate, 6)

        sym = _normalize_bybit_symbol(raw_sym)
        records.append(
            {
                "trade_id": f"bybit_{order_id or sym}_{close_ms}",
                "venue_id": "bybit",
                "venue_name": "Bybit",
                "venue_name_zh": "Bybit",
                "symbol": sym,
                "side": side,
                "qty": qty,
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
                "is_partial_close": bool(float(row.get("closedSize") or 0) < float(row.get("qty") or 0)),
                "entry_leg": {
                    "role": "MAKER" if entry_is_maker else "TAKER",
                    "mode_code": "LIMIT_MAKER" if entry_is_maker else "MARKET_TAKER",
                    "fee_bps": round(entry_bps, 2),
                    "time_ms": open_ms,
                    "avg_price": entry_px,
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
                    "mode_code": "LIMIT_MAKER" if exit_is_maker else "MARKET_TAKER",
                    "fee_bps": round(exit_bps, 2),
                    "time_ms": close_ms,
                    "avg_price": exit_px,
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
            }
        )
    records.sort(key=lambda r: (r["close_time_ms"], r["open_time_ms"]), reverse=True)
    return records
