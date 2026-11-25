"""Strictly GET-only KuCoin Spot & Futures v2 signed REST client and per-leg trade normalizer.

Signature specification (KuCoin API Key v2):
  str_to_sign = timestamp_ms + "GET" + endpoint_with_query
  KC-API-SIGN = Base64(HMAC-SHA256(str_to_sign, api_secret))
  KC-API-PASSPHRASE = Base64(HMAC-SHA256(passphrase, api_secret))
  KC-API-KEY-VERSION = "2"
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx

from opentradeledger.security.readonly_guard import assert_readonly_request

TZ_UTC8 = timezone(timedelta(hours=8))

KUCOIN_ALLOWED_GET_PATHS: tuple[str, ...] = (
    "/api/v2/user-info",
    "/api/v1/sub/user",
    "/api/v1/accounts",
    "/api/v1/market/allTickers",
    "/api/v1/deposits",
    "/api/v1/withdrawals",
    "/api/v1/account-overview",
    "/api/v1/positions",
    "/api/v1/history-positions",
    "/api/v1/fills",
    "/api/v1/transaction-history",
    "/api/v1/base-fee",
    "/api/v1/contracts/active",
)


class KucoinApiError(RuntimeError):
    """Raised when KuCoin returns code != '200000' or an HTTP error."""


def _fmt_bj_ms(ms: int) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ_UTC8).strftime("%m-%d %H:%M")


def sign_kucoin_get(timestamp_ms: str, path_with_query: str, api_secret: str) -> str:
    msg = f"{timestamp_ms}GET{path_with_query}".encode("utf-8")
    digest = hmac.new(api_secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def sign_kucoin_passphrase(passphrase: str, api_secret: str) -> str:
    digest = hmac.new(api_secret.encode("utf-8"), passphrase.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def normalize_kucoin_symbol(raw: str) -> str:
    s = (raw or "").strip().upper().replace("-", "_")
    if s.startswith("XBT"):
        s = "BTC" + s[3:]
    if s.endswith("USDTM") and len(s) > 5:
        return f"{s[:-5]}_USDT"
    if s.endswith("USDCM") and len(s) > 5:
        return f"{s[:-5]}_USDC"
    if s.endswith("USDM") and len(s) > 4:
        return f"{s[:-4]}_USD"
    if "_" in s:
        return s
    for q in ("USDT", "USDC", "USD"):
        if s.endswith(q) and len(s) > len(q):
            return f"{s[:-len(q)]}_{q}"
    return s


class KucoinReadOnlyClient:
    """Strictly GET-only signed client for KuCoin Spot (`api.kucoin.com`) & Futures (`api-futures.kucoin.com`)."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        passphrase: str,
        spot_base_url: str = "https://api.kucoin.com",
        futures_base_url: str = "https://api-futures.kucoin.com",
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 12.0,
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.spot_base_url = spot_base_url.rstrip("/")
        self.futures_base_url = futures_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec, connect=6.0))
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def build_headers(self, path_with_query: str, timestamp_ms: Optional[str] = None) -> dict[str, str]:
        ts = timestamp_ms or str(self._clock_ms())
        return {
            "KC-API-KEY": self.api_key,
            "KC-API-SIGN": sign_kucoin_get(ts, path_with_query, self.api_secret),
            "KC-API-TIMESTAMP": ts,
            "KC-API-PASSPHRASE": sign_kucoin_passphrase(self.passphrase, self.api_secret),
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
        }

    async def request_readonly(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        *,
        futures: bool = False,
        signed: bool = True,
    ) -> Any:
        assert_readonly_request(method, path, KUCOIN_ALLOWED_GET_PATHS)
        clean_params = {k: str(v) for k, v in (params or {}).items() if v is not None and v != ""}
        qs = urlencode(sorted(clean_params.items())) if clean_params else ""
        path_with_query = f"{path}?{qs}" if qs else path
        base = self.futures_base_url if futures else self.spot_base_url
        headers = self.build_headers(path_with_query) if signed else {"Accept": "application/json"}
        resp = await self._client.get(f"{base}{path_with_query}", headers=headers)
        if resp.status_code >= 400:
            raise KucoinApiError(f"KuCoin GET {path} HTTP {resp.status_code}: {resp.text[:240]}")
        payload = resp.json()
        if str(payload.get("code") or "") != "200000":
            raise KucoinApiError(f"KuCoin GET {path} code={payload.get('code')}: {payload.get('msg', '')}")
        return payload.get("data")

    async def get_user_info(self) -> dict[str, Any]:
        res = await self.request_readonly("GET", "/api/v2/user-info", futures=False)
        return res if isinstance(res, dict) else {}

    async def get_spot_accounts(self) -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/api/v1/accounts", futures=False)
        return res if isinstance(res, list) else []

    async def get_futures_overview(self, currency: str = "USDT") -> dict[str, Any]:
        res = await self.request_readonly("GET", "/api/v1/account-overview", {"currency": currency}, futures=True)
        return res if isinstance(res, dict) else {}

    async def get_positions(self) -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/api/v1/positions", futures=True)
        return res if isinstance(res, list) else []

    async def get_history_positions(self, *, page_limit: int = 5) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, page_limit + 1):
            res = await self.request_readonly(
                "GET",
                "/api/v1/history-positions",
                {"currentPage": page, "pageSize": 50},
                futures=True,
            )
            items = res.get("items") if isinstance(res, dict) else (res if isinstance(res, list) else [])
            if not isinstance(items, list) or not items:
                break
            rows.extend(r for r in items if isinstance(r, dict))
            total_page = int(res.get("totalPage") or 1) if isinstance(res, dict) else 1
            if page >= total_page:
                break
        return rows

    async def get_futures_fills(self, *, since_ms: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"pageSize": 100, "currentPage": 1}
        if since_ms > 0:
            params["startAt"] = since_ms
        res = await self.request_readonly("GET", "/api/v1/fills", params, futures=True)
        items = res.get("items") if isinstance(res, dict) else (res if isinstance(res, list) else [])
        return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []

    async def get_futures_transactions(self, *, since_ms: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"currency": "USDT", "maxCount": 100}
        if since_ms > 0:
            params["startAt"] = since_ms
        res = await self.request_readonly("GET", "/api/v1/transaction-history", params, futures=True)
        items = res.get("dataList") if isinstance(res, dict) else (res if isinstance(res, list) else [])
        return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []

    async def get_deposits(self) -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/api/v1/deposits", {"status": "SUCCESS", "pageSize": 100}, futures=False)
        items = res.get("items") if isinstance(res, dict) else []
        return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []

    async def get_withdrawals(self) -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/api/v1/withdrawals", {"status": "SUCCESS", "pageSize": 100}, futures=False)
        items = res.get("items") if isinstance(res, dict) else []
        return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []

    async def get_spot_tickers(self) -> dict[str, float]:
        res = await self.request_readonly("GET", "/api/v1/market/allTickers", futures=False, signed=False)
        prices: dict[str, float] = {}
        ticker_list = res.get("ticker") if isinstance(res, dict) else []
        for row in ticker_list or []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").upper()
            for quote in ("-USDT", "-USDC"):
                if sym.endswith(quote) and len(sym) > len(quote):
                    base = sym[: -len(quote)]
                    try:
                        px = float(row.get("last") or 0.0)
                        if px > 0:
                            prices[base] = px
                    except (TypeError, ValueError):
                        pass
                    break
        return prices

    async def get_base_fee(self) -> dict[str, Any]:
        res = await self.request_readonly("GET", "/api/v1/base-fee", futures=False)
        return res if isinstance(res, dict) else {}

    async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
        warnings: list[str] = []
        spot_accounts: list[dict[str, Any]] = []
        futures_overview: dict[str, Any] = {}
        try:
            spot_accounts = await self.get_spot_accounts()
        except Exception as spot_exc:
            warnings.append(f"spot_accounts: {spot_exc}")
        try:
            futures_overview = await self.get_futures_overview("USDT")
        except Exception as fut_exc:
            if not spot_accounts:
                raise fut_exc
            warnings.append(f"futures_overview: {fut_exc}")

        async def _opt(name: str, coro: Any, default: Any) -> Any:
            try:
                return await coro
            except Exception as exc:
                warnings.append(f"{name}: {exc}")
                return default

        positions = await _opt("positions", self.get_positions(), [])
        history_positions = await _opt("history_positions", self.get_history_positions(), [])
        fills = await _opt("fills", self.get_futures_fills(since_ms=since_ms), [])
        tx_history = await _opt("tx_history", self.get_futures_transactions(since_ms=since_ms), [])
        deposits = await _opt("deposits", self.get_deposits(), [])
        withdrawals = await _opt("withdrawals", self.get_withdrawals(), [])
        tickers = await _opt("tickers", self.get_spot_tickers(), {})
        base_fee = await _opt("base_fee", self.get_base_fee(), {})

        return {
            "venue_id": "kucoin",
            "spot_accounts": spot_accounts,
            "futures_overview": futures_overview,
            "positions": positions,
            "history_positions": history_positions,
            "fills": fills,
            "tx_history": tx_history,
            "deposits": deposits,
            "withdrawals": withdrawals,
            "tickers": tickers,
            "base_fee": base_fee,
            "warnings": warnings,
        }


def normalize_kucoin_closed_trades(
    history_positions: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    tx_history: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Normalizes KuCoin Futures closed positions with per-leg Maker/Taker fees, funding, and rebates."""
    fills_by_sym: dict[str, list[dict[str, Any]]] = {}
    for f in fills:
        if isinstance(f, dict):
            s = str(f.get("symbol") or "").upper()
            if s:
                fills_by_sym.setdefault(s, []).append(f)

    rebate_sum = sum(
        max(0.0, float(tx.get("amount") or 0.0))
        for tx in (tx_history or [])
        if isinstance(tx, dict) and "REBATE" in str(tx.get("type") or "").upper()
    )
    total_pos_fees = sum(abs(float(p.get("tradeFee") or p.get("fee") or 0.0)) for p in history_positions if isinstance(p, dict))
    rebate_ratio = min(0.95, rebate_sum / total_pos_fees) if total_pos_fees > 0 and rebate_sum > 0 else 0.0

    records: list[dict[str, Any]] = []
    sorted_pos = sorted(
        (p for p in history_positions if isinstance(p, dict)),
        key=lambda r: int(r.get("closeTimestamp") or r.get("updatedAt") or 0),
    )

    for idx, row in enumerate(sorted_pos):
        raw_sym = str(row.get("symbol") or "").upper()
        if not raw_sym:
            continue
        open_ms = int(row.get("openTimestamp") or row.get("createdAt") or 0)
        close_ms = int(row.get("closeTimestamp") or row.get("updatedAt") or open_ms)
        side_raw = str(row.get("side") or row.get("type") or "long").lower()
        side = "SHORT" if "short" in side_raw or side_raw == "sell" else "LONG"
        qty = abs(float(row.get("closeSize") or row.get("size") or row.get("qty") or 0.0))
        entry_px = float(row.get("openPrice") or row.get("avgEntryPrice") or 0.0)
        exit_px = float(row.get("closePrice") or row.get("avgClosePrice") or 0.0)
        mult = abs(float(row.get("multiplier") or 1.0))
        eff_coin_qty = qty * mult if mult > 0 and mult != 1.0 else qty
        entry_notional = entry_px * eff_coin_qty if entry_px > 0 and eff_coin_qty > 0 else abs(float(row.get("openValue") or 0.0))
        exit_notional = exit_px * eff_coin_qty if exit_px > 0 and eff_coin_qty > 0 else abs(float(row.get("closeValue") or entry_notional))

        win_fills = [
            f
            for f in fills_by_sym.get(raw_sym, [])
            if open_ms - 5_000 <= int(f.get("createdAt") or f.get("tradeTime") or 0) <= close_ms + 5_000
        ]
        open_side_tok = "buy" if side == "LONG" else "sell"
        en_fills = [f for f in win_fills if str(f.get("side") or "").lower() == open_side_tok]
        ex_fills = [f for f in win_fills if str(f.get("side") or "").lower() != open_side_tok]

        pos_total_fee = abs(float(row.get("tradeFee") or row.get("fee") or 0.0))
        if en_fills or ex_fills:
            en_fee = sum(abs(float(f.get("fee") or 0.0)) for f in en_fills)
            ex_fee = sum(abs(float(f.get("fee") or 0.0)) for f in ex_fills)
            if pos_total_fee > 0 and (en_fee + ex_fee) > 0:
                scale = pos_total_fee / (en_fee + ex_fee)
                en_fee *= scale
                ex_fee *= scale
            en_maker = any(str(f.get("liquidity") or "").lower() == "maker" for f in en_fills)
            ex_maker = any(str(f.get("liquidity") or "").lower() == "maker" for f in ex_fills)
            fee_known = True
        else:
            en_fee = pos_total_fee * 0.5
            ex_fee = pos_total_fee - en_fee
            en_maker = False
            ex_maker = False
            fee_known = pos_total_fee == 0

        total_fee = round(en_fee + ex_fee, 6)
        en_reb = round(en_fee * rebate_ratio, 6)
        ex_reb = round(ex_fee * rebate_ratio, 6)
        total_rebate = round(en_reb + ex_reb, 6)

        funding_pnl = float(row.get("fundingFee") or 0.0)
        # KuCoin `pnl` / `realisedPnl` on history-positions is net of tradeFee & fundingFee, or gross profit in `profit`
        if row.get("grossPnl") is not None:
            gross_pnl = float(row["grossPnl"])
            book_pnl = round(gross_pnl - total_fee + funding_pnl, 6)
        else:
            realized_net_book = float(row.get("pnl") if row.get("pnl") is not None else (row.get("realisedPnl") or 0.0))
            book_pnl = round(realized_net_book, 6)
            gross_pnl = round(book_pnl + total_fee - funding_pnl, 6)

        net_pnl = round(book_pnl + total_rebate, 6)
        en_bps = (en_fee / entry_notional * 10_000) if (fee_known and entry_notional > 0) else None
        ex_bps = (ex_fee / exit_notional * 10_000) if (fee_known and exit_notional > 0) else None
        sym = normalize_kucoin_symbol(raw_sym)
        pos_id = str(row.get("closeId") or row.get("positionId") or idx)
        settled_str = _fmt_bj_ms(close_ms) if total_rebate > 0 else ""

        records.append(
            {
                "trade_id": f"kucoin_{pos_id}_{close_ms}",
                "venue_id": "kucoin",
                "venue_name": "KuCoin",
                "venue_name_zh": "KuCoin",
                "symbol": sym,
                "side": side,
                "qty": eff_coin_qty,
                "contract_count": qty if mult != 1.0 else None,
                "open_time_ms": open_ms,
                "close_time_ms": close_ms,
                "gross_pnl_usd": round(gross_pnl, 4),
                "funding_pnl_usd": round(funding_pnl, 4),
                "book_pnl_usd": round(book_pnl, 4),
                "total_fee_usd": round(total_fee, 4),
                "total_rebate_usd": round(total_rebate, 4),
                "total_discount_saved_usd": 0.0,
                "net_fee_usd": round(max(0.0, total_fee - total_rebate), 4),
                "net_pnl_usd": round(net_pnl, 4),
                "rebate_status": "SETTLED",
                "is_partial_close": False,
                "entry_leg": {
                    "role": ("MAKER" if en_maker else "TAKER") if fee_known else "UNKNOWN",
                    "mode_code": ("LIMIT_MAKER" if en_maker else "MARKET_TAKER") if fee_known else "UNKNOWN",
                    "fee_known": fee_known,
                    "fee_bps": round(en_bps, 2) if en_bps is not None else None,
                    "time_ms": open_ms,
                    "avg_price": entry_px,
                    "notional_usd": round(entry_notional, 2) if entry_notional > 0 else None,
                    "gross_fee_usd": round(en_fee, 4),
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": round(en_reb, 4),
                    "rebate_pct": round(rebate_ratio * 100.0, 2),
                    "net_fee_usd": round(max(0.0, en_fee - en_reb), 4),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
                "exit_leg": {
                    "role": ("MAKER" if ex_maker else "TAKER") if fee_known else "UNKNOWN",
                    "mode_code": ("LIMIT_MAKER" if ex_maker else "MARKET_TAKER") if fee_known else "UNKNOWN",
                    "fee_known": fee_known,
                    "fee_bps": round(ex_bps, 2) if ex_bps is not None else None,
                    "time_ms": close_ms,
                    "avg_price": exit_px,
                    "notional_usd": round(exit_notional, 2) if exit_notional > 0 else None,
                    "gross_fee_usd": round(ex_fee, 4),
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": round(ex_reb, 4),
                    "rebate_pct": round(rebate_ratio * 100.0, 2),
                    "net_fee_usd": round(max(0.0, ex_fee - ex_reb), 4),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
            }
        )
    records.sort(key=lambda r: (r["close_time_ms"], r["open_time_ms"]), reverse=True)
    return records
