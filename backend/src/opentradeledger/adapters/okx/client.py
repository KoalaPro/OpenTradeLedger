"""Strictly GET-only OKX API v5 signed client and per-leg trade normalizer.

Signature specification (OKX v5):
  message = timestamp_iso8601 + "GET" + request_path_with_query
  OK-ACCESS-SIGN = Base64(HMAC-SHA256(message, api_secret))
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx

from opentradeledger.security.readonly_guard import assert_readonly_request

OKX_ALLOWED_GET_PATHS: tuple[str, ...] = (
    "/api/v5/public/time",
    "/api/v5/public/instruments",
    "/api/v5/market/tickers",
    "/api/v5/account/balance",
    "/api/v5/account/positions",
    "/api/v5/account/positions-history",
    "/api/v5/account/trade-fee",
    "/api/v5/account/bills",
    "/api/v5/account/bills-archive",
    "/api/v5/trade/fills",
    "/api/v5/trade/fills-history",
    "/api/v5/trade/orders-pending",
    "/api/v5/trade/orders-history",
    "/api/v5/asset/balances",
    "/api/v5/asset/deposit-history",
    "/api/v5/asset/withdrawal-history",
    "/api/v5/copytrading/current-subpositions",
)


class OkxApiError(RuntimeError):
    """Raised when OKX v5 returns a non-zero business error code or HTTP error."""


def _default_iso_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def sign_okx_get(timestamp: str, request_path: str, api_secret: str) -> str:
    message = f"{timestamp}GET{request_path}"
    digest = hmac.new(api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class OkxReadOnlyClient:
    """Strictly GET-only signed REST client for OKX v5 Unified Account."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        passphrase: str,
        base_url: str = "https://www.okx.com",
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 12.0,
        clock_iso: Optional[Callable[[], str]] = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout_sec
        self._clock_iso = clock_iso or _default_iso_timestamp

    def build_headers(self, request_path: str, timestamp: Optional[str] = None) -> dict[str, str]:
        ts = timestamp or self._clock_iso()
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign_okx_get(ts, request_path, self.api_secret),
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }

    async def request_readonly(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        assert_readonly_request(method, path, OKX_ALLOWED_GET_PATHS)
        clean_params = {k: str(v) for k, v in (params or {}).items() if v is not None and v != ""}
        request_path = f"{path}?{urlencode(sorted(clean_params.items()))}" if clean_params else path
        headers = self.build_headers(request_path)
        url = f"{self.base_url}{request_path}"

        if self._client is not None:
            resp = await self._client.get(url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.get(url, headers=headers)

        if resp.status_code >= 400:
            raise OkxApiError(f"OKX GET {path} HTTP {resp.status_code}: {resp.text[:240]}")
        payload = resp.json()
        code = str(payload.get("code", "0"))
        if code != "0":
            raise OkxApiError(f"OKX GET {path} error code={code}: {payload.get('msg', '')}")
        data = payload.get("data")
        return data if isinstance(data, list) else []

    async def get_account_balance(self) -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/account/balance")

    async def get_funding_balances(self) -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/asset/balances")

    async def get_positions(self, inst_type: str = "SWAP") -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/account/positions", {"instType": inst_type})

    async def get_positions_history(
        self, inst_type: str = "SWAP", limit: int = 100
    ) -> list[dict[str, Any]]:
        return await self.request_readonly(
            "GET", "/api/v5/account/positions-history", {"instType": inst_type, "limit": limit}
        )

    async def get_fills_history(
        self, inst_type: str = "SWAP", limit: int = 100
    ) -> list[dict[str, Any]]:
        return await self.request_readonly(
            "GET", "/api/v5/trade/fills-history", {"instType": inst_type, "limit": limit}
        )

    async def get_trade_fee(self, inst_type: str = "SWAP") -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/account/trade-fee", {"instType": inst_type})

    async def get_bills(self, inst_type: str = "SWAP", limit: int = 100) -> list[dict[str, Any]]:
        return await self.request_readonly(
            "GET", "/api/v5/account/bills", {"instType": inst_type, "limit": limit}
        )

    async def get_deposit_history(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/asset/deposit-history", {"limit": limit})

    async def get_withdrawal_history(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/asset/withdrawal-history", {"limit": limit})

    async def fetch_full_snapshot(self) -> dict[str, Any]:
        """Collects read-only unified account snapshot with graceful degradation on optional endpoints."""
        # Primary balance call verifies credentials & connectivity
        balances = await self.get_account_balance()
        warnings: list[str] = []

        async def _opt(name: str, coro: Any) -> list[dict[str, Any]]:
            try:
                return await coro
            except Exception as exc:
                warnings.append(f"{name}: {exc}")
                return []

        funding = await _opt("funding", self.get_funding_balances())
        positions = await _opt("positions", self.get_positions("SWAP"))
        positions_history = await _opt("positions_history", self.get_positions_history("SWAP"))
        fills = await _opt("fills", self.get_fills_history("SWAP"))
        trade_fee = await _opt("trade_fee", self.get_trade_fee("SWAP"))
        bills = await _opt("bills", self.get_bills("SWAP"))
        deposits = await _opt("deposits", self.get_deposit_history())
        withdrawals = await _opt("withdrawals", self.get_withdrawal_history())

        # Dynamically estimate observed rebate ratio if OKX rebate bills exist
        gross_fee_sum = sum(abs(float(f.get("fee") or 0.0)) for f in fills)
        rebate_sum = sum(
            max(0.0, float(b.get("balChg") or b.get("pnl") or 0.0))
            for b in bills
            if str(b.get("type") or "") in {"15", "22"} or "rebate" in str(b.get("subType") or "").lower()
        )
        observed_rebate_ratio = (
            min(0.95, rebate_sum / gross_fee_sum) if gross_fee_sum > 0 and rebate_sum > 0 else 0.0
        )

        return {
            "venue_id": "okx",
            "balances": balances,
            "funding": funding,
            "positions": positions,
            "positions_history": positions_history,
            "fills": fills,
            "trade_fee": trade_fee,
            "bills": bills,
            "deposits": deposits,
            "withdrawals": withdrawals,
            "observed_rebate_ratio": round(observed_rebate_ratio, 6),
            "warnings": warnings,
        }


def normalize_okx_closed_trades(
    positions_history: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    rebate_ratio: float = 0.0,
) -> list[dict[str, Any]]:
    """Builds normalized closed trades with explicit per-leg entry & exit fees from OKX v5 data."""
    fills_by_inst: dict[str, list[dict[str, Any]]] = {}
    for fill in fills:
        inst = str(fill.get("instId") or "").upper()
        if inst:
            fills_by_inst.setdefault(inst, []).append(fill)

    records: list[dict[str, Any]] = []
    for pos in positions_history:
        inst = str(pos.get("instId") or "").upper()
        if not inst:
            continue
        open_ms = int(pos.get("cTime") or 0)
        close_ms = int(pos.get("uTime") or open_ms)
        open_px = float(pos.get("openAvgPx") or 0.0)
        close_px = float(pos.get("closeAvgPx") or 0.0)
        qty = abs(float(pos.get("closeTotalPos") or pos.get("openMaxPos") or 0.0))
        direction = str(pos.get("direction") or pos.get("posSide") or "long").upper()
        side = "SHORT" if "SHORT" in direction else "LONG"

        # Separate entry vs exit fills within the position window
        inst_fills = [
            f
            for f in fills_by_inst.get(inst, [])
            if open_ms - 5_000 <= int(f.get("ts") or 0) <= close_ms + 5_000
        ]
        entry_side = "buy" if side == "LONG" else "sell"
        entry_fills = [f for f in inst_fills if str(f.get("side") or "").lower() == entry_side]
        exit_fills = [f for f in inst_fills if str(f.get("side") or "").lower() != entry_side]

        total_fee_pos = abs(float(pos.get("fee") or 0.0))
        entry_gross_fee = sum(abs(float(f.get("fee") or 0.0)) for f in entry_fills)
        exit_gross_fee = sum(abs(float(f.get("fee") or 0.0)) for f in exit_fills)
        if entry_gross_fee == 0.0 and exit_gross_fee == 0.0 and total_fee_pos > 0:
            # Split proportionally when fill archive aged out
            entry_gross_fee = round(total_fee_pos * 0.5, 6)
            exit_gross_fee = round(total_fee_pos - entry_gross_fee, 6)

        entry_is_maker = any(str(f.get("execType") or "").upper() == "M" for f in entry_fills)
        exit_is_maker = any(str(f.get("execType") or "").upper() == "M" for f in exit_fills)

        entry_notional = open_px * qty
        exit_notional = close_px * qty
        entry_bps = (entry_gross_fee / entry_notional * 10_000) if entry_notional > 0 else 0.0
        exit_bps = (exit_gross_fee / exit_notional * 10_000) if exit_notional > 0 else 0.0

        eff_ratio = max(0.0, min(1.0, rebate_ratio))
        entry_rebate = round(entry_gross_fee * eff_ratio, 6)
        exit_rebate = round(exit_gross_fee * eff_ratio, 6)
        entry_net_fee = round(max(0.0, entry_gross_fee - entry_rebate), 6)
        exit_net_fee = round(max(0.0, exit_gross_fee - exit_rebate), 6)

        gross_pnl = float(pos.get("pnl") or 0.0)
        funding_pnl = float(pos.get("fundingFee") or 0.0)
        total_fee = round(entry_gross_fee + exit_gross_fee, 6)
        total_rebate = round(entry_rebate + exit_rebate, 6)
        book_pnl = float(pos.get("realizedPnl") or (gross_pnl - total_fee + funding_pnl))
        net_pnl = round(book_pnl + total_rebate, 6)

        sym = inst.replace("-SWAP", "").replace("-", "_")
        records.append(
            {
                "trade_id": f"okx_{pos.get('posId') or sym}_{close_ms}",
                "venue_id": "okx",
                "venue_name": "OKX",
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
                "total_discount_saved_usd": 0.0,
                "net_fee_usd": round(entry_net_fee + exit_net_fee, 4),
                "net_pnl_usd": round(net_pnl, 4),
                "rebate_status": "SETTLED",
                "is_partial_close": False,
                "entry_leg": {
                    "role": "MAKER" if entry_is_maker else "TAKER",
                    "mode_code": "LIMIT_MAKER" if entry_is_maker else "MARKET_TAKER",
                    "fee_bps": round(entry_bps, 2),
                    "time_ms": open_ms,
                    "avg_price": open_px,
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
                    "avg_price": close_px,
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
