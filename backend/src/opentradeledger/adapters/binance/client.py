"""Strictly read-only Binance (币安) REST client (`GET` only, explicit path whitelist).

Supports both Binance Unified / Portfolio Margin (`https://papi.binance.com`) and
Classic USD-M Futures (`https://fapi.binance.com`) accounts, plus Spot/SAPI read-only ledgers.
"""

from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlencode


def _parse_utc_str_ms(raw: str) -> int:
    val = (raw or "").strip()
    if not val:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return int(datetime.strptime(val, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    return 0

import httpx

from opentradeledger.adapters.compat_creds import BinanceCredentials
from opentradeledger.adapters.stock_channels import _KNOWN_CRYPTO_EXCLUSIONS, classify_spot_stock_token
from opentradeledger.security.readonly_guard import ReadOnlyGuardError

logger = logging.getLogger("gate_manual.binance")

INCOME_WINDOW_MS = 14 * 86400 * 1000
CAPITAL_WINDOW_MS = 89 * 86400 * 1000
SAPI_30D_WINDOW_MS = 29 * 86400 * 1000
SAPI_150D_WINDOW_MS = 150 * 86400 * 1000

_SPOT_FALLBACK_BASES = (
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api3.binance.com",
)

_ALLOWED_GET_PATHS = frozenset(
    {
        # Spot & SAPI
        "/api/v3/time",
        "/api/v3/account",
        "/api/v3/klines",
        "/api/v3/ticker/price",
        "/api/v3/myTrades",
        "/sapi/v1/asset/wallet/balance",
        "/sapi/v1/asset/transfer",
        "/sapi/v1/copyTrading/futures/userStatus",
        "/sapi/v1/bnbBurn",
        "/sapi/v1/capital/deposit/hisrec",
        "/sapi/v1/capital/withdraw/history",
        "/sapi/v1/c2c/orderMatch/listUserOrderHistory",
        "/sapi/v1/convert/tradeFlow",
        "/sapi/v1/pay/transactions",
        # Binance Stocks & Tokenized Equities (strictly read-only GET)
        "/sapi/v1/equity/market/exchangeInfo",
        "/sapi/v1/equity/market/tokenized-assets",
        "/sapi/v1/equity/market/quote",
        "/sapi/v1/equity/order/open-orders",
        "/sapi/v1/equity/order/history",
        "/sapi/v1/equity/order/detail",
        "/sapi/v1/equity/trade/history",
        "/sapi/v1/equity/tokenized/history",
        # Portfolio Margin (Unified Account)
        "/papi/v1/account",
        "/papi/v1/balance",
        "/papi/v1/um/account",
        "/papi/v1/um/commissionRate",
        "/papi/v1/um/income",
        "/papi/v1/um/userTrades",
        "/papi/v1/um/openOrders",
        "/papi/v1/um/allOrders",
        # Classic USD-M Futures fallback
        "/fapi/v1/time",
        "/fapi/v2/account",
        "/fapi/v2/balance",
        "/fapi/v2/positionRisk",
        "/fapi/v1/commissionRate",
        "/fapi/v1/openOrders",
        "/fapi/v1/income",
        "/fapi/v1/userTrades",
        "/fapi/v1/allOrders",
        "/fapi/v1/ticker/price",
    }
)


class BinanceApiError(RuntimeError):
    """Raised on HTTP >= 400 or negative Binance error code."""

    def __init__(self, status_code: int, path: str, detail: Any) -> None:
        self.status_code = status_code
        self.path = path
        self.detail = detail
        super().__init__(f"Binance GET {path} failed ({status_code}): {detail}")


class BinanceReadOnlyClient:
    """HMAC-SHA256 hex signed strictly read-only client for Binance Spot/SAPI + PAPI/FAPI."""

    def __init__(
        self,
        credentials: BinanceCredentials,
        *,
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 12.0,
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
            timeout=httpx.Timeout(timeout_sec, connect=5.0),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _now_ms(self) -> int:
        if self._custom_clock_ms is not None:
            return int(self._custom_clock_ms())
        return int(time.time() * 1000) + self._time_offset_ms

    def _base_for_path(self, clean_path: str) -> str:
        if clean_path.startswith("/papi/"):
            return self.credentials.papi_base_url
        if clean_path.startswith("/fapi/"):
            return self.credentials.fapi_base_url
        return self.credentials.base_url

    async def _ensure_time_sync(self) -> None:
        if self._custom_clock_ms is not None:
            return
        if (time.monotonic() - self._last_time_sync_mono) < 60.0:
            return
        try:
            payload = await self._request("GET", "/api/v3/time", signed=False)
            if isinstance(payload, dict) and payload.get("serverTime") is not None:
                self._time_offset_ms = int(payload["serverTime"]) - int(time.time() * 1000)
                self._last_time_sync_mono = time.monotonic()
        except Exception as exc:
            logger.debug("Binance server time sync skipped: %s", exc)

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
                f"BinanceReadOnlyClient strictly forbids non-GET method {method!r} on {path!r}"
            )
        clean_path = "/" + path.lstrip("/")
        if clean_path not in _ALLOWED_GET_PATHS:
            raise ReadOnlyGuardError(
                f"BinanceReadOnlyClient blocked non-whitelisted path {clean_path!r}"
            )

        if signed:
            await self._ensure_time_sync()

        filtered: list[tuple[str, str]] = [
            (k, str(v)) for k, v in query_params if v is not None and str(v) != ""
        ]
        headers: dict[str, str] = {"Accept": "application/json"}
        if signed:
            filtered.append(("timestamp", str(self._now_ms())))
            filtered.append(("recvWindow", str(self.recv_window_ms)))
            qs = urlencode(filtered)
            sig = hmac.new(
                self.credentials.api_secret.encode("utf-8"),
                qs.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            final_qs = f"{qs}&signature={sig}"
            headers["X-MBX-APIKEY"] = self.credentials.api_key
        else:
            final_qs = urlencode(filtered)

        rel_url = f"{clean_path}?{final_qs}" if final_qs else clean_path
        primary_base = self._base_for_path(clean_path)

        bases_to_try: list[str] = [primary_base]
        if (
            self._owns_client
            and primary_base == "https://api.binance.com"
            and not clean_path.startswith(("/papi/", "/fapi/"))
        ):
            bases_to_try.extend(b for b in _SPOT_FALLBACK_BASES if b != primary_base)

        last_exc: Optional[Exception] = None
        resp: Optional[httpx.Response] = None
        for b_url in bases_to_try:
            full_url = f"{b_url}{rel_url}"
            try:
                resp = await self._client.request("GET", full_url, headers=headers)
                break
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                last_exc = exc
                continue

        if resp is None:
            err_msg = (
                f"{type(last_exc).__name__}: {last_exc}".rstrip(": ")
                if last_exc is not None
                else "ConnectTimeout"
            )
            raise BinanceApiError(0, clean_path, err_msg)

        is_json = "json" in resp.headers.get("content-type", "").lower()
        payload: Any
        if is_json and resp.content:
            try:
                payload = resp.json()
            except ValueError:
                payload = resp.text
        else:
            payload = resp.text

        if resp.status_code >= 400:
            raise BinanceApiError(resp.status_code, clean_path, payload)
        if isinstance(payload, dict) and isinstance(payload.get("code"), int) and payload["code"] < 0:
            raise BinanceApiError(resp.status_code, clean_path, payload)
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
            logger.debug("Binance optional GET %s skipped: %s", path, exc)
            return default

    async def fetch_bnb_daily_prices(self, *, full_history: bool = False) -> dict[str, str]:
        limit = 450 if full_history else 14
        rows = await self._safe_get(
            "/api/v3/klines",
            [("symbol", "BNBUSDT"), ("interval", "1d"), ("limit", limit)],
            signed=False,
            default=[],
        )
        out: dict[str, str] = {}
        if isinstance(rows, list):
            for kl in rows:
                if isinstance(kl, (list, tuple)) and len(kl) >= 5:
                    open_ms = int(kl[0])
                    close_px = str(kl[4])
                    # Store both UTC date and Beijing date keys for seamless lookup
                    utc_day = time.strftime("%Y-%m-%d", time.gmtime(open_ms / 1000.0))
                    bj_day = time.strftime("%Y-%m-%d", time.gmtime(open_ms / 1000.0 + 8 * 3600))
                    out[utc_day] = close_px
                    out[bj_day] = close_px
        tk = await self._safe_get(
            "/api/v3/ticker/price",
            [("symbol", "BNBUSDT")],
            signed=False,
            default={},
        )
        if isinstance(tk, dict) and tk.get("price"):
            out["LIVE"] = str(tk["price"])
        return out

    async def fetch_live_overview_bundle(self) -> dict[str, Any]:
        spot_acc = await self.raw_get("/api/v3/account", [("omitZeroBalances", "true")])
        wallet_bal = await self._safe_get(
            "/sapi/v1/asset/wallet/balance", [("quoteAsset", "USDT")], default=[]
        )
        bnb_burn = await self._safe_get("/sapi/v1/bnbBurn", default={})

        account_mode = "PAPI"
        futures_balances: list[dict[str, Any]] = []
        futures_positions: list[dict[str, Any]] = []
        futures_open_orders: list[dict[str, Any]] = []
        pm_account: dict[str, Any] = {}

        papi_acc = await self._safe_get("/papi/v1/account", default=None)
        if isinstance(papi_acc, dict) and "accountEquity" in papi_acc:
            pm_account = papi_acc
            bal_res = await self._safe_get("/papi/v1/balance", default=[])
            if isinstance(bal_res, list):
                futures_balances = [dict(b) for b in bal_res if isinstance(b, dict)]
            um_acc = await self._safe_get("/papi/v1/um/account", default={})
            if isinstance(um_acc, dict):
                for p in um_acc.get("positions") or []:
                    if abs(float(p.get("positionAmt") or 0)) > 0:
                        futures_positions.append(dict(p))
            oo_res = await self._safe_get("/papi/v1/um/openOrders", default=[])
            if isinstance(oo_res, list):
                futures_open_orders = [dict(o) for o in oo_res if isinstance(o, dict)]
        else:
            fapi_acc = await self._safe_get("/fapi/v2/account", default=None)
            if isinstance(fapi_acc, dict):
                account_mode = "FAPI"
                pm_account = {
                    "accountEquity": str(fapi_acc.get("totalMarginBalance") or "0"),
                    "totalAvailableBalance": str(fapi_acc.get("availableBalance") or "0"),
                }
                for a in fapi_acc.get("assets") or []:
                    if abs(float(a.get("walletBalance") or 0)) > 0:
                        futures_balances.append(dict(a))
                pos_risk = await self._safe_get("/fapi/v2/positionRisk", default=[])
                if isinstance(pos_risk, list):
                    for p in pos_risk:
                        if abs(float(p.get("positionAmt") or 0)) > 0:
                            futures_positions.append(dict(p))
                oo_res = await self._safe_get("/fapi/v1/openOrders", default=[])
                if isinstance(oo_res, list):
                    futures_open_orders = [dict(o) for o in oo_res if isinstance(o, dict)]

        # Enrich any open positions missing markPrice via public futures ticker
        for pos in futures_positions:
            if not float(pos.get("markPrice") or 0) and pos.get("symbol"):
                tk_p = await self._safe_get(
                    "/fapi/v1/ticker/price",
                    [("symbol", str(pos["symbol"]))],
                    signed=False,
                    default={},
                )
                if isinstance(tk_p, dict) and tk_p.get("price"):
                    pos["markPrice"] = str(tk_p["price"])

        spot_balances = [
            dict(b)
            for b in ((spot_acc or {}).get("balances") or [])
            if (float(b.get("free") or 0) + float(b.get("locked") or 0)) > 0
        ]
        spot_tickers: dict[str, str] = {"USDT": "1", "USDC": "1", "FDUSD": "1", "USD1": "1"}
        for sb in spot_balances[:15]:
            asset = str(sb.get("asset") or "").upper()
            if asset and asset not in spot_tickers:
                sp_tk = await self._safe_get(
                    "/api/v3/ticker/price",
                    [("symbol", f"{asset}USDT")],
                    signed=False,
                    default={},
                )
                if isinstance(sp_tk, dict) and sp_tk.get("price"):
                    spot_tickers[asset] = str(sp_tk["price"])

        spot_comm_rates: dict[str, str] = {}
        if isinstance(spot_acc, dict):
            cr_obj = spot_acc.get("commissionRates")
            if isinstance(cr_obj, dict) and (cr_obj.get("maker") is not None or cr_obj.get("taker") is not None):
                spot_comm_rates = {
                    "maker": str(cr_obj.get("maker") or "0"),
                    "taker": str(cr_obj.get("taker") or "0"),
                }
            elif spot_acc.get("makerCommission") is not None or spot_acc.get("takerCommission") is not None:
                spot_comm_rates = {
                    "maker": str(float(spot_acc.get("makerCommission") or 0) / 10000.0),
                    "taker": str(float(spot_acc.get("takerCommission") or 0) / 10000.0),
                }

        stock_bundle = await self._fetch_stock_bundle(spot_balances, spot_comm_rates=spot_comm_rates)

        return {
            "uid": int((spot_acc or {}).get("uid") or 0),
            "account_mode": account_mode,
            "bnb_burn": dict(bnb_burn) if isinstance(bnb_burn, dict) else {},
            "wallet_balances": [dict(w) for w in (wallet_bal or []) if isinstance(w, dict)],
            "spot_balances": spot_balances,
            "spot_tickers": spot_tickers,
            "pm_account": pm_account,
            "futures_balances": futures_balances,
            "futures_positions": futures_positions,
            "futures_open_orders": futures_open_orders,
            "stock_bundle": stock_bundle,
        }

    async def _fetch_stock_bundle(
        self,
        spot_balances: Sequence[dict[str, Any]],
        *,
        spot_comm_rates: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Fetch Binance `/sapi/v1/equity/*` real US stock fills/quotes & Spot bStocks cost basis."""
        now_ms = self._now_ms()
        equity_trades_res = await self._safe_get(
            "/sapi/v1/equity/trade/history",
            [("startTime", max(0, now_ms - SAPI_30D_WINDOW_MS)), ("endTime", now_ms), ("size", 100)],
            default=[],
        )
        equity_trades: list[dict[str, Any]] = []
        if isinstance(equity_trades_res, list):
            equity_trades = [dict(r) for r in equity_trades_res if isinstance(r, dict)]
        elif isinstance(equity_trades_res, dict):
            rows_eq = equity_trades_res.get("rows") or equity_trades_res.get("list") or equity_trades_res.get("data") or []
            if isinstance(rows_eq, list):
                equity_trades = [dict(r) for r in rows_eq if isinstance(r, dict)]

        equity_open_orders_res = await self._safe_get("/sapi/v1/equity/order/open-orders", default=[])
        equity_open_orders = (
            [dict(o) for o in equity_open_orders_res if isinstance(o, dict)]
            if isinstance(equity_open_orders_res, list)
            else []
        )

        # Fetch live quotes for any equity symbol with trade history
        eq_syms = sorted({str(t.get("symbol") or "").strip().upper() for t in equity_trades if t.get("symbol")})
        equity_quotes: dict[str, dict[str, Any]] = {}
        for esym in eq_syms[:20]:
            q_res = await self._safe_get("/sapi/v1/equity/market/quote", [("symbol", esym)], default={})
            if isinstance(q_res, dict) and q_res:
                equity_quotes[esym] = dict(q_res)

        candidate_spot_assets = [
            str(sb.get("asset") or "").strip().upper()
            for sb in spot_balances
            if str(sb.get("asset") or "").strip().upper() not in _KNOWN_CRYPTO_EXCLUSIONS
        ]
        tokenized_assets: list[dict[str, Any]] = []
        dyn_cat: dict[str, dict[str, Any]] = {}
        if candidate_spot_assets:
            ta_res = await self._safe_get("/sapi/v1/equity/market/tokenized-assets", signed=False, default=[])
            rows_ta = (
                ta_res
                if isinstance(ta_res, list)
                else ((ta_res.get("data") or ta_res.get("list") or []) if isinstance(ta_res, dict) else [])
            )
            for item in rows_ta or []:
                if isinstance(item, dict):
                    tokenized_assets.append(dict(item))
                    a_u = str(item.get("asset") or item.get("tokenSymbol") or item.get("symbol") or "").strip().upper()
                    u_u = str(item.get("underlying") or item.get("underlyingSymbol") or "").strip().upper()
                    if a_u:
                        dyn_cat[a_u] = {"underlying": u_u or a_u.rstrip("B"), "name": str(item.get("name") or u_u or a_u)}

        spot_stock_trades: dict[str, list[dict[str, Any]]] = {}
        for cand in candidate_spot_assets[:15]:
            if classify_spot_stock_token(cand, exchange="BINANCE", dynamic_catalog=dyn_cat):
                tr_rows = await self._safe_get("/api/v3/myTrades", [("symbol", f"{cand}USDT"), ("limit", 500)], default=[])
                if not tr_rows:
                    tr_rows = await self._safe_get("/api/v3/myTrades", [("symbol", f"{cand}USDC"), ("limit", 500)], default=[])
                if isinstance(tr_rows, list):
                    spot_stock_trades[cand] = [dict(r) for r in tr_rows if isinstance(r, dict)]

        return {
            "tokenized_assets": tokenized_assets,
            "equity_trades": equity_trades,
            "equity_open_orders": equity_open_orders,
            "equity_quotes": equity_quotes,
            "spot_stock_trades": spot_stock_trades,
            "spot_commission_rates": dict(spot_comm_rates or {}),
        }

    async def fetch_um_income(
        self,
        *,
        since_sec: int,
        account_mode: str = "PAPI",
    ) -> list[dict[str, Any]]:
        path = "/papi/v1/um/income" if account_mode == "PAPI" else "/fapi/v1/income"
        now_ms = self._now_ms()
        t_cur = max(0, int(since_sec) * 1000)
        out: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()

        while t_cur < now_ms:
            t_end = min(now_ms, t_cur + INCOME_WINDOW_MS)
            page_start = t_cur
            for _ in range(8):
                rows = await self._safe_get(
                    path,
                    [("startTime", page_start), ("endTime", t_end), ("limit", 1000)],
                    default=[],
                )
                if not isinstance(rows, list) or not rows:
                    break
                max_t = page_start
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    rt = int(r.get("time") or 0)
                    if rt > max_t:
                        max_t = rt
                    dedup_k = (
                        r.get("tranId"),
                        r.get("incomeType"),
                        rt,
                        r.get("income"),
                        r.get("asset"),
                        r.get("tradeId"),
                    )
                    if dedup_k not in seen:
                        seen.add(dedup_k)
                        out.append(dict(r))
                if len(rows) < 1000 or max_t <= page_start:
                    break
                page_start = max_t + 1
            t_cur = t_end + 1
            if self._owns_client and t_cur < now_ms:
                await asyncio.sleep(0.05)
        return out

    async def fetch_um_trades_for_symbols(
        self,
        symbols: Sequence[str],
        *,
        account_mode: str = "PAPI",
    ) -> list[dict[str, Any]]:
        path = "/papi/v1/um/userTrades" if account_mode == "PAPI" else "/fapi/v1/userTrades"
        out: list[dict[str, Any]] = []
        for sym in sorted({s.strip().upper() for s in symbols if s and s.strip()}):
            from_id = 0
            for _ in range(10):
                rows = await self._safe_get(
                    path,
                    [("symbol", sym), ("fromId", from_id), ("limit", 1000)],
                    default=[],
                )
                if not isinstance(rows, list) or not rows:
                    break
                max_id = from_id
                for r in rows:
                    if isinstance(r, dict):
                        out.append(dict(r))
                        rid = int(r.get("id") or 0)
                        if rid > max_id:
                            max_id = rid
                if len(rows) < 1000 or max_id <= from_id:
                    break
                from_id = max_id + 1
            if self._owns_client:
                await asyncio.sleep(0.04)
        return out

    async def fetch_um_orders_for_symbols(
        self,
        symbols: Sequence[str],
        *,
        account_mode: str = "PAPI",
    ) -> list[dict[str, Any]]:
        path = "/papi/v1/um/allOrders" if account_mode == "PAPI" else "/fapi/v1/allOrders"
        out: list[dict[str, Any]] = []
        for sym in sorted({s.strip().upper() for s in symbols if s and s.strip()}):
            from_oid = 0
            for _ in range(6):
                rows = await self._safe_get(
                    path,
                    [("symbol", sym), ("orderId", from_oid), ("limit", 500)],
                    default=[],
                )
                if not isinstance(rows, list) or not rows:
                    break
                max_oid = from_oid
                for r in rows:
                    if isinstance(r, dict):
                        out.append(dict(r))
                        oid_val = int(r.get("orderId") or 0)
                        if oid_val > max_oid:
                            max_oid = oid_val
                if len(rows) < 500 or max_oid <= from_oid:
                    break
                from_oid = max_oid + 1
            if self._owns_client:
                await asyncio.sleep(0.03)
        return out

    async def fetch_um_commission_rates(
        self,
        symbols: Sequence[str],
        *,
        account_mode: str = "PAPI",
    ) -> dict[str, dict[str, str]]:
        """Fetch live base Maker/Taker commission rates per symbol directly from Binance API."""
        path = "/papi/v1/um/commissionRate" if account_mode == "PAPI" else "/fapi/v1/commissionRate"
        out: dict[str, dict[str, str]] = {}
        for sym in sorted({s.strip().upper() for s in symbols if s and s.strip()}):
            res = await self._safe_get(path, [("symbol", sym)], default={})
            if isinstance(res, dict) and (
                res.get("makerCommissionRate") is not None or res.get("takerCommissionRate") is not None
            ):
                out[sym] = {
                    "makerCommissionRate": str(res.get("makerCommissionRate") or "0"),
                    "takerCommissionRate": str(res.get("takerCommissionRate") or "0"),
                }
            if self._owns_client:
                await asyncio.sleep(0.02)
        return out

    async def fetch_cashflows(self, *, since_sec: int, do_full: bool = False) -> list[dict[str, Any]]:
        """Fetch external deposits, withdrawals, C2C trades, Pay crypto-boxes, and BNB converts."""
        now_ms = self._now_ms()
        start_ms = max(0, int(since_sec) * 1000)
        events: list[dict[str, Any]] = []

        # 1. On-chain Deposits & Withdrawals (89d max window)
        t0 = start_ms
        while t0 < now_ms:
            t1 = min(now_ms, t0 + CAPITAL_WINDOW_MS)
            deps = await self._safe_get(
                "/sapi/v1/capital/deposit/hisrec",
                [("startTime", t0), ("endTime", t1)],
                default=[],
            )
            if isinstance(deps, list):
                for d in deps:
                    if int(d.get("status") or 0) == 1:
                        ts = int(d.get("insertTime") or 0)
                        events.append(
                            {
                                "cf_key": f"dep_{d.get('id') or d.get('txId') or ts}",
                                "cf_type": "DEPOSIT",
                                "time_ms": ts,
                                "asset": str(d.get("coin") or "").upper(),
                                "amount": str(d.get("amount") or "0"),
                                "fee": "0",
                                "raw": dict(d),
                            }
                        )
            wds = await self._safe_get(
                "/sapi/v1/capital/withdraw/history",
                [("startTime", t0), ("endTime", t1)],
                default=[],
            )
            if isinstance(wds, list):
                for w in wds:
                    if int(w.get("status") or 0) == 6:
                        # applyTime is "YYYY-MM-DD HH:MM:SS" UTC or completeTime
                        wid = str(w.get("id") or w.get("txId") or "")
                        apply_str = str(w.get("applyTime") or w.get("completeTime") or "")
                        w_ts = int(w.get("timestamp") or 0) or _parse_utc_str_ms(apply_str)
                        events.append(
                            {
                                "cf_key": f"wd_{wid}",
                                "cf_type": "WITHDRAW",
                                "time_ms": w_ts,
                                "apply_time_utc": apply_str,
                                "asset": str(w.get("coin") or "").upper(),
                                "amount": str(w.get("amount") or "0"),
                                "fee": str(w.get("transactionFee") or "0"),
                                "raw": dict(w),
                            }
                        )
            t0 = t1 + 1
            if not do_full:
                break
            if self._owns_client:
                await asyncio.sleep(0.05)

        # 2. C2C Fiat Buy / Sell & Convert (29d windows)
        t0 = start_ms
        while t0 < now_ms:
            t1 = min(now_ms, t0 + SAPI_30D_WINDOW_MS)
            for tr_type in ("BUY", "SELL"):
                c2c_res = await self._safe_get(
                    "/sapi/v1/c2c/orderMatch/listUserOrderHistory",
                    [
                        ("tradeType", tr_type),
                        ("startTimestamp", t0),
                        ("endTimestamp", t1),
                        ("rows", 100),
                    ],
                    default={},
                )
                for row in (c2c_res.get("data") if isinstance(c2c_res, dict) else None) or []:
                    if str(row.get("orderStatus") or "") in ("COMPLETED", "4"):
                        ono = str(row.get("orderNumber") or "")
                        ct = int(row.get("createTime") or 0)
                        events.append(
                            {
                                "cf_key": f"c2c_{ono or ct}",
                                "cf_type": f"C2C_{tr_type}",
                                "time_ms": ct,
                                "asset": str(row.get("asset") or "").upper(),
                                "amount": str(row.get("amount") or "0"),
                                "fiat_price": str(row.get("totalPrice") or "0"),
                                "fee": str(row.get("commission") or "0"),
                                "raw": dict(row),
                            }
                        )
            cv_res = await self._safe_get(
                "/sapi/v1/convert/tradeFlow",
                [("startTime", t0), ("endTime", t1), ("limit", 100)],
                default={},
            )
            for cv in (cv_res.get("list") if isinstance(cv_res, dict) else None) or []:
                if str(cv.get("orderStatus") or "") in ("SUCCESS", "PROCESS", ""):
                    oid = str(cv.get("orderId") or cv.get("quoteId") or cv.get("createTime") or "")
                    ct = int(cv.get("createTime") or 0)
                    events.append(
                        {
                            "cf_key": f"cv_{oid}",
                            "cf_type": "CONVERT",
                            "time_ms": ct,
                            "asset": str(cv.get("toAsset") or "").upper(),
                            "amount": str(cv.get("toAmount") or "0"),
                            "from_asset": str(cv.get("fromAsset") or "").upper(),
                            "from_amount": str(cv.get("fromAmount") or "0"),
                            "fee": "0",
                            "raw": dict(cv),
                        }
                    )
            t0 = t1 + 1
            if self._owns_client and t0 < now_ms:
                await asyncio.sleep(0.04)

        # 3. Binance Pay (Crypto Box red packets / internal transfers)
        pay_res = await self._safe_get("/sapi/v1/pay/transactions", [("limit", 100)], default={})
        for p in (pay_res.get("data") if isinstance(pay_res, dict) else None) or []:
            pt = int(p.get("transactionTime") or 0)
            if pt >= start_ms:
                tid = str(p.get("transactionId") or p.get("orderId") or pt)
                events.append(
                    {
                        "cf_key": f"pay_{tid}",
                        "cf_type": "PAY",
                        "time_ms": pt,
                        "asset": str(p.get("currency") or "").upper(),
                        "amount": str(p.get("amount") or "0"),
                        "fee": "0",
                        "raw": dict(p),
                    }
                )

        # 4. Internal Universal Transfers across Spot / Funding / UM Futures (for Copy Trading conservation)
        for tr_dir in (
            "FUNDING_MAIN",
            "MAIN_FUNDING",
            "MAIN_UMFUTURE",
            "UMFUTURE_MAIN",
            "FUNDING_UMFUTURE",
            "UMFUTURE_FUNDING",
        ):
            t0 = start_ms
            while t0 < now_ms:
                t1 = min(now_ms, t0 + SAPI_150D_WINDOW_MS)
                tr_res = await self._safe_get(
                    "/sapi/v1/asset/transfer",
                    [("type", tr_dir), ("startTime", t0), ("endTime", t1), ("size", 100)],
                    default={},
                )
                for tr in (tr_res.get("rows") if isinstance(tr_res, dict) else None) or []:
                    if str(tr.get("status") or "").upper() in ("CONFIRMED", "SUCCESS", ""):
                        ts_tr = int(tr.get("timestamp") or 0)
                        tid_tr = str(tr.get("tranId") or ts_tr)
                        events.append(
                            {
                                "cf_key": f"tr_{tr_dir}_{tid_tr}",
                                "cf_type": "INTERNAL_TRANSFER",
                                "tran_dir": tr_dir,
                                "tran_id": tid_tr,
                                "time_ms": ts_tr,
                                "asset": str(tr.get("asset") or "").upper(),
                                "amount": str(tr.get("amount") or "0"),
                                "fee": "0",
                                "raw": dict(tr),
                            }
                        )
                t0 = t1 + 1
                if not do_full:
                    break
                if self._owns_client and t0 < now_ms:
                    await asyncio.sleep(0.03)

        return events
