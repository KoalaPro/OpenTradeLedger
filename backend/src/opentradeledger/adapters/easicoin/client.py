"""Strictly read-only EasiCoin (易币) REST client (`GET` only, explicit path whitelist)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlencode

import httpx

from opentradeledger.adapters.compat_creds import EasiCoinCredentials
from opentradeledger.adapters.easicoin.identity import EasiCoinIdentityError, account_uid
from opentradeledger.adapters.stock_channels import classify_spot_stock_token
from opentradeledger.security.readonly_guard import ReadOnlyGuardError

logger = logging.getLogger("gate_manual.easicoin")

WINDOW_DAYS_MS = 29 * 86400 * 1000

_ALLOWED_GET_PATHS = frozenset(
    {
        "/futures/public/v1/market/time",
        "/futures/public/v1/market/tickers",
        "/futures/public/v1/instruments",
        "/asset-api/account/private/v1/userid",
        "/asset-api/account/private/v1/get-funding-account-balance",
        "/asset-api/account/private/v1/user-transfer-rercord/page",
        "/spot-api/account/private/v1/balance",
        "/spot-api/account/private/v1/bills",
        "/spot-api/market/public/v1/tickers",
        "/futures/private/v1/account/balance",
        "/futures/private/v1/account/fee-rate",
        "/futures/private/v1/position/list",
        "/futures/private/v1/position/closed-pnl",
        "/futures/private/v1/trade/activity-orders",
        "/futures/private/v1/trade/orders",
        "/futures/private/v1/trade/fills",
    }
)


class EasiCoinApiError(RuntimeError):
    """Raised on HTTP >= 400 or non-zero EasiCoin business status code."""

    def __init__(self, status_code: int, path: str, detail: Any) -> None:
        self.status_code = status_code
        self.path = path
        self.detail = detail
        super().__init__(f"EasiCoin GET {path} failed ({status_code}): {detail}")


class EasiCoinReadOnlyClient:
    """HMAC-SHA256 hex signed strictly read-only client for EasiCoin (`https://api.easicoin.io`)."""

    def __init__(
        self,
        credentials: EasiCoinCredentials,
        *,
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 14.0,
        recv_window_ms: int = 10000,
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self.credentials = credentials
        self.recv_window_ms = int(recv_window_ms)
        self._custom_clock_ms = clock_ms
        self._time_offset_ms: int = 0
        self._last_time_sync_mono: float = 0.0
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=credentials.base_url,
            timeout=httpx.Timeout(timeout_sec, connect=7.0),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _now_ms(self) -> int:
        if self._custom_clock_ms is not None:
            return int(self._custom_clock_ms())
        return int(time.time() * 1000) + self._time_offset_ms

    async def _ensure_time_sync(self) -> None:
        if self._custom_clock_ms is not None:
            return
        if (time.monotonic() - self._last_time_sync_mono) < 60.0:
            return
        try:
            payload = await self._request("GET", "/futures/public/v1/market/time", signed=False)
            srv_ms: Optional[int] = None
            if isinstance(payload, dict):
                top_t = payload.get("time")
                if top_t is not None:
                    v = int(top_t)
                    srv_ms = v * 1000 if v < 10_000_000_000 else v
                elif isinstance(payload.get("data"), dict) and payload["data"].get("time") is not None:
                    v = int(payload["data"]["time"])
                    srv_ms = v * 1000 if v < 10_000_000_000 else v
            if srv_ms is not None:
                self._time_offset_ms = srv_ms - int(time.time() * 1000)
                self._last_time_sync_mono = time.monotonic()
        except Exception as exc:
            logger.debug("EasiCoin server time sync skipped: %s", exc)

    async def _request(
        self,
        method: str,
        path: str,
        query_params: Sequence[tuple[str, Optional[str | int]]] = (),
        *,
        signed: bool = True,
    ) -> Any:
        if method.upper() != "GET":
            raise ReadOnlyGuardError(
                f"EasiCoinReadOnlyClient strictly forbids non-GET method {method!r} on {path!r}"
            )
        clean_path = "/" + path.lstrip("/")
        if clean_path not in _ALLOWED_GET_PATHS:
            raise ReadOnlyGuardError(
                f"EasiCoinReadOnlyClient blocked non-whitelisted path {clean_path!r}"
            )

        if signed:
            await self._ensure_time_sync()

        filtered: list[tuple[str, str]] = [
            (k, str(v)) for k, v in query_params if v is not None and str(v) != ""
        ]
        query_str = urlencode(filtered)
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if signed:
            ts_str = str(self._now_ms())
            recv_str = str(self.recv_window_ms)
            sign_payload = f"{ts_str}{self.credentials.api_key}{recv_str}{query_str}"
            signature = hmac.new(
                self.credentials.api_secret.encode("utf-8"),
                sign_payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            headers["Access-Key"] = self.credentials.api_key
            headers["Access-Timestamp"] = ts_str
            headers["Recv-Window"] = recv_str
            headers["Access-Sign"] = signature

        rel_url = f"{clean_path}?{query_str}" if query_str else clean_path
        url = rel_url if self._owns_client else f"{self.credentials.base_url}{rel_url}"
        resp = await self._client.request("GET", url, headers=headers)
        is_json = "json" in resp.headers.get("content-type", "").lower()
        payload = resp.json() if (is_json and resp.content) else resp.text
        if resp.status_code >= 400:
            raise EasiCoinApiError(resp.status_code, clean_path, payload)
        if isinstance(payload, dict):
            code = payload.get("code")
            if code not in (0, "0", None):
                raise EasiCoinApiError(resp.status_code, clean_path, payload)
        return payload

    async def raw_get(
        self,
        path: str,
        query_params: Sequence[tuple[str, Optional[str | int]]] = (),
        *,
        signed: bool = True,
    ) -> Any:
        return await self._request("GET", path, query_params, signed=signed)

    async def _safe_get(
        self,
        path: str,
        query_params: Sequence[tuple[str, Optional[str | int]]] = (),
        *,
        signed: bool = True,
        default: Any = None,
    ) -> Any:
        try:
            return await self.raw_get(path, query_params, signed=signed)
        except ReadOnlyGuardError:
            raise
        except Exception as exc:
            logger.warning("EasiCoin optional GET %s failed: %s", path, exc)
            return default

    async def fetch_live_overview_bundle(self) -> dict[str, Any]:
        uid_response = await self.raw_get("/asset-api/account/private/v1/userid")
        uid_data = uid_response.get("data") if isinstance(uid_response, dict) else None
        uid = account_uid(uid_data.get("userid")) if isinstance(uid_data, dict) else ""
        if not uid:
            raise EasiCoinIdentityError("ACCOUNT_UID_UNAVAILABLE")
        fut_bal_res = await self.raw_get("/futures/private/v1/account/balance")
        pos_res = await self._safe_get("/futures/private/v1/position/list", [("coin", "USDT")], default={})
        open_ord_res = await self._safe_get(
            "/futures/private/v1/trade/activity-orders", [("coin", "USDT")], default={}
        )
        fund_res = await self._safe_get(
            "/asset-api/account/private/v1/get-funding-account-balance", default={}
        )
        spot_res = await self._safe_get("/spot-api/account/private/v1/balance", default={})
        fee_res = await self._safe_get("/futures/private/v1/account/fee-rate", default={})
        tickers_res = await self._safe_get(
            "/futures/public/v1/market/tickers", signed=False, default={}
        )
        funding_list = list(((fund_res or {}).get("data") or {}).get("list") or [])
        spot_list = list((spot_res or {}).get("data") or [])
        tickers_list = list(((tickers_res or {}).get("data") or {}).get("list") or [])

        # Fallback: if daily red-packet / spot altcoins have no futures contract, query EasiCoin public spot ticker
        covered_bases: set[str] = {"USDT", "USDC", "USD1", "USD"}
        for tk in tickers_list:
            sym_u = str(tk.get("symbol") or "").upper()
            if not sym_u.endswith("USDT"):
                continue
            b_sym = sym_u[:-4]
            covered_bases.add(b_sym)
            for pfx in ("1000000", "100000", "10000", "1000", "100"):
                if b_sym.startswith(pfx) and len(b_sym) > len(pfx):
                    covered_bases.add(b_sym[len(pfx):])

        held_altcoins: set[str] = set()
        for fnd in funding_list:
            if float(fnd.get("total_equity") or fnd.get("available_balance") or 0) > 0:
                c_name = str(fnd.get("coin") or "").upper()
                if c_name and c_name not in covered_bases:
                    held_altcoins.add(c_name)
        for spb in spot_list:
            if float(spb.get("balance") or spb.get("available") or 0) > 0:
                c_name = str(spb.get("token_name") or spb.get("coin") or "").upper()
                if c_name and c_name not in covered_bases:
                    held_altcoins.add(c_name)

        for alt_c in sorted(held_altcoins)[:15]:
            sp_tk = await self._safe_get(
                "/spot-api/market/public/v1/tickers",
                [("symbol_name", f"{alt_c}USDT")],
                signed=False,
                default={},
            )
            sp_data = (sp_tk or {}).get("data") if isinstance(sp_tk, dict) else None
            if isinstance(sp_data, dict) and sp_data.get("last_price"):
                tickers_list.append({"symbol": f"{alt_c}USDT", "last_price": str(sp_data["last_price"])})

        held_ec_stocks: set[str] = set()
        for fnd in funding_list:
            if float(fnd.get("total_equity") or fnd.get("available_balance") or 0) > 0:
                c_nm = str(fnd.get("coin") or "").strip().upper()
                if classify_spot_stock_token(c_nm, exchange="EASICOIN"):
                    held_ec_stocks.add(c_nm)
        for spb in spot_list:
            if float(spb.get("balance") or spb.get("available") or 0) > 0:
                c_nm = str(spb.get("token_name") or spb.get("coin") or "").strip().upper()
                if classify_spot_stock_token(c_nm, exchange="EASICOIN"):
                    held_ec_stocks.add(c_nm)

        spot_stock_fills: dict[str, list[dict[str, Any]]] = {}
        if held_ec_stocks:
            bills_res = await self._safe_get("/spot-api/account/private/v1/bills", [("limit", 100)], default={})
            b_data = (bills_res or {}).get("data")
            b_rows = b_data.get("list") if isinstance(b_data, dict) else (b_data if isinstance(b_data, list) else [])
            for br in b_rows or []:
                if isinstance(br, dict):
                    c_b = str(br.get("token_name") or br.get("coin") or "").strip().upper()
                    if c_b in held_ec_stocks and str(br.get("type") or "").lower() in ("buy", "sell", "trade", ""):
                        spot_stock_fills.setdefault(c_b, []).append(dict(br))

        return {
            "userid": uid,
            "futures_balances": list(((fut_bal_res or {}).get("data") or {}).get("list") or []),
            "funding_balances": funding_list,
            "spot_balances": spot_list,
            "futures_positions": list(((pos_res or {}).get("data") or {}).get("list") or []),
            "futures_open_orders": list(((open_ord_res or {}).get("data") or {}).get("list") or []),
            "fee_rates": list(((fee_res or {}).get("data") or {}).get("list") or []),
            "tickers": tickers_list,
            "stock_bundle": {"spot_stock_fills": spot_stock_fills},
        }

    async def fetch_closed_pnl(self, *, since_sec: int, max_pages: int = 30) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _ in range(max_pages):
            params: list[tuple[str, Optional[str | int]]] = [("coin", "USDT"), ("limit", 100)]
            if cursor:
                params.append(("cursor", cursor))
            res = await self.raw_get("/futures/private/v1/position/closed-pnl", params)
            data = (res or {}).get("data") or {}
            rows = list(data.get("list") or [])
            if not rows:
                break
            for r in rows:
                out.append(dict(r))
            oldest = min(int(float(r.get("created_at") or 0)) for r in rows)
            next_cur = str(data.get("next_page_cursor") or "").strip()
            if not next_cur or next_cur in seen_cursors or (oldest > 0 and oldest < since_sec):
                break
            seen_cursors.add(next_cur)
            cursor = next_cur
        return out

    async def fetch_trade_fills(self, *, since_sec: int, max_pages: int = 50) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _ in range(max_pages):
            params: list[tuple[str, Optional[str | int]]] = [("coin", "USDT"), ("limit", 100)]
            if cursor:
                params.append(("cursor", cursor))
            res = await self.raw_get("/futures/private/v1/trade/fills", params)
            data = (res or {}).get("data") or {}
            rows = list(data.get("list") or [])
            if not rows:
                break
            for r in rows:
                out.append(dict(r))
            oldest = min(int(float(r.get("exec_time") or 0)) for r in rows)
            next_cur = str(data.get("next_page_cursor") or "").strip()
            if not next_cur or next_cur in seen_cursors or (oldest > 0 and oldest < since_sec):
                break
            seen_cursors.add(next_cur)
            cursor = next_cur
        return out

    async def fetch_orders_history(self, *, since_sec: int, max_pages: int = 20) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _ in range(max_pages):
            params: list[tuple[str, Optional[str | int]]] = [("coin", "USDT"), ("limit", 100)]
            if cursor:
                params.append(("cursor", cursor))
            res = await self._safe_get("/futures/private/v1/trade/orders", params, default={})
            data = (res or {}).get("data") or {}
            rows = list(data.get("list") or [])
            if not rows:
                break
            for r in rows:
                out.append(dict(r))
            oldest = min(int(float(r.get("created_time") or 0)) for r in rows)
            next_cur = str(data.get("next_page_cursor") or "").strip()
            if not next_cur or next_cur in seen_cursors or (oldest > 0 and oldest < since_sec):
                break
            seen_cursors.add(next_cur)
            cursor = next_cur
        return out

    async def fetch_transfers(self, *, since_sec: int, max_windows: int = 13) -> list[dict[str, Any]]:
        now_ms = self._now_ms()
        since_ms = int(since_sec) * 1000
        out: list[dict[str, Any]] = []
        for w in range(max_windows):
            if w > 0 and self._owns_client:
                await asyncio.sleep(0.25)
            end_ms = now_ms - w * WINDOW_DAYS_MS
            if end_ms < since_ms:
                break
            start_ms = max(since_ms - 86400 * 1000, end_ms - WINDOW_DAYS_MS)
            for page_num in range(1, 6):
                res = await self._safe_get(
                    "/asset-api/account/private/v1/user-transfer-rercord/page",
                    [
                        ("start_time", start_ms),
                        ("end_time", end_ms),
                        ("page_num", page_num),
                        ("page_size", 100),
                    ],
                    default={},
                )
                data = (res or {}).get("data")
                rows = list(data.get("list") or []) if isinstance(data, dict) else []
                if not rows:
                    break
                for r in rows:
                    out.append(dict(r))
                if len(rows) < 100:
                    break
        return out
