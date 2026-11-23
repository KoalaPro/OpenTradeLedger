"""Strictly GET-only Bybit API v5 Unified Account signed client and per-leg trade normalizer.

Signature specification (Bybit v5 GET):
  payload = timestamp_ms + api_key + recv_window + sorted_query_string
  X-BAPI-SIGN = hex(HMAC-SHA256(payload, api_secret))
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx

from opentradeledger.security.readonly_guard import assert_readonly_request

BYBIT_ALLOWED_GET_PATHS: tuple[str, ...] = (
    "/v5/market/time",
    "/v5/market/tickers",
    "/v5/market/instruments-info",
    "/v5/account/wallet-balance",
    "/v5/account/fee-rate",
    "/v5/account/transaction-log",
    "/v5/position/list",
    "/v5/position/closed-pnl",
    "/v5/execution/list",
    "/v5/order/history",
    "/v5/order/realtime",
    "/v5/asset/transfer/query-account-coins-balance",
    "/v5/asset/deposit/query-record",
    "/v5/asset/withdraw/query-record",
)


class BybitApiError(RuntimeError):
    """Raised when Bybit v5 returns retCode != 0 or an HTTP error."""


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
    """Strictly GET-only signed REST client for Bybit v5 Unified Trading Account."""

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
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = str(recv_window)
        self._client = client
        self._timeout = timeout_sec
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

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

    async def get_closed_pnl(self, category: str = "linear", limit: int = 100) -> list[dict[str, Any]]:
        res = await self.request_readonly(
            "GET", "/v5/position/closed-pnl", {"category": category, "limit": limit}
        )
        return res.get("list") or []

    async def get_executions(self, category: str = "linear", limit: int = 100) -> list[dict[str, Any]]:
        res = await self.request_readonly(
            "GET", "/v5/execution/list", {"category": category, "limit": limit}
        )
        return res.get("list") or []

    async def get_fee_rate(self, category: str = "linear") -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/v5/account/fee-rate", {"category": category})
        return res.get("list") or []

    async def get_transaction_log(
        self, account_type: str = "UNIFIED", limit: int = 50
    ) -> list[dict[str, Any]]:
        res = await self.request_readonly(
            "GET", "/v5/account/transaction-log", {"accountType": account_type, "limit": limit}
        )
        return res.get("list") or []

    async def get_deposits(self, limit: int = 50) -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/v5/asset/deposit/query-record", {"limit": limit})
        return res.get("rows") or []

    async def get_withdrawals(self, limit: int = 50) -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/v5/asset/withdraw/query-record", {"limit": limit})
        return res.get("rows") or []

    async def fetch_full_snapshot(self) -> dict[str, Any]:
        """Collects read-only Unified/Contract account snapshot with graceful degradation."""
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
        closed_pnl = await _opt("closed_pnl", self.get_closed_pnl("linear"), [])
        executions = await _opt("executions", self.get_executions("linear"), [])
        fee_rates = await _opt("fee_rates", self.get_fee_rate("linear"), [])
        tx_logs = await _opt("tx_logs", self.get_transaction_log("UNIFIED"), [])
        deposits = await _opt("deposits", self.get_deposits(), [])
        withdrawals = await _opt("withdrawals", self.get_withdrawals(), [])

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
            "withdrawals": withdrawals,
            "warnings": warnings,
        }


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
    rebate_ratio: float = 0.0,
) -> list[dict[str, Any]]:
    """Builds normalized closed trades with explicit entry & exit leg fees from Bybit v5 data."""
    execs_by_order: dict[str, list[dict[str, Any]]] = {}
    execs_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for ex in executions:
        oid = str(ex.get("orderId") or "")
        sym = str(ex.get("symbol") or "").upper()
        if oid:
            execs_by_order.setdefault(oid, []).append(ex)
        if sym:
            execs_by_symbol.setdefault(sym, []).append(ex)

    records: list[dict[str, Any]] = []
    for row in closed_pnl_rows:
        raw_sym = str(row.get("symbol") or "").upper()
        if not raw_sym:
            continue
        order_id = str(row.get("orderId") or "")
        # In Bybit closed-pnl, `side` is the closing order side ("Sell" closes LONG; "Buy" closes SHORT)
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

        entry_is_maker = any(bool(e.get("isMaker")) for e in entry_execs)
        exit_is_maker = any(bool(e.get("isMaker")) for e in exit_execs)
        entry_bps = (entry_gross_fee / entry_notional * 10_000) if entry_notional > 0 else 0.0
        exit_bps = (exit_gross_fee / exit_notional * 10_000) if exit_notional > 0 else 0.0

        eff_ratio = max(0.0, min(1.0, rebate_ratio))
        entry_rebate = round(entry_gross_fee * eff_ratio, 6)
        exit_rebate = round(exit_gross_fee * eff_ratio, 6)
        entry_net_fee = round(max(0.0, entry_gross_fee - entry_rebate), 6)
        exit_net_fee = round(max(0.0, exit_gross_fee - exit_rebate), 6)

        book_pnl = float(row.get("closedPnl") or 0.0)
        total_fee = round(entry_gross_fee + exit_gross_fee, 6)
        total_rebate = round(entry_rebate + exit_rebate, 6)
        gross_pnl = round(book_pnl + total_fee, 6)
        net_pnl = round(book_pnl + total_rebate, 6)

        sym = _normalize_bybit_symbol(raw_sym)
        records.append(
            {
                "trade_id": f"bybit_{order_id or sym}_{close_ms}",
                "venue_id": "bybit",
                "venue_name": "Bybit",
                "symbol": sym,
                "side": side,
                "qty": qty,
                "open_time_ms": open_ms,
                "close_time_ms": close_ms,
                "gross_pnl_usd": round(gross_pnl, 4),
                "funding_pnl_usd": 0.0,
                "book_pnl_usd": round(book_pnl, 4),
                "total_fee_usd": round(total_fee, 4),
                "total_rebate_usd": round(total_rebate, 4),
                "total_discount_saved_usd": 0.0,
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
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": round(entry_rebate, 4),
                    "rebate_pct": round(eff_ratio * 100.0, 2),
                    "net_fee_usd": round(entry_net_fee, 4),
                    "rebate_status": "SETTLED",
                },
                "exit_leg": {
                    "role": "MAKER" if exit_is_maker else "TAKER",
                    "mode_code": "LIMIT_MAKER" if exit_is_maker else "MARKET_TAKER",
                    "fee_bps": round(exit_bps, 2),
                    "time_ms": close_ms,
                    "avg_price": exit_px,
                    "notional_usd": round(exit_notional, 2),
                    "gross_fee_usd": round(exit_gross_fee, 4),
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": round(exit_rebate, 4),
                    "rebate_pct": round(eff_ratio * 100.0, 2),
                    "net_fee_usd": round(exit_net_fee, 4),
                    "rebate_status": "SETTLED",
                },
            }
        )
    records.sort(key=lambda r: (r["close_time_ms"], r["open_time_ms"]), reverse=True)
    return records
