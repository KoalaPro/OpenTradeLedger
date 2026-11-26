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
import math
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx

from opentradeledger.security.readonly_guard import assert_readonly_request

TZ_UTC8 = timezone(timedelta(hours=8))

KUCOIN_ALLOWED_GET_PATHS: tuple[str, ...] = (
    "/api/v1/user/api-key",
    "/api/v1/user-info",
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


def _is_valid_kucoin_num(val: Any) -> bool:
    if val is None or isinstance(val, bool):
        return False
    if isinstance(val, (int, float)):
        return math.isfinite(float(val))
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return False
        try:
            return math.isfinite(float(s))
        except ValueError:
            return False
    return False


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
        key_version: str = "2",
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 12.0,
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.key_version = str(key_version or "2")
        self.spot_base_url = spot_base_url.rstrip("/")
        self.futures_base_url = futures_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec, connect=6.0))
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def build_headers(
        self,
        path_with_query: str,
        timestamp_ms: Optional[str] = None,
        *,
        key_version: Optional[str] = None,
    ) -> dict[str, str]:
        ts = timestamp_ms or str(self._clock_ms())
        ver = str(key_version or self.key_version or "2")
        return {
            "KC-API-KEY": self.api_key,
            "KC-API-SIGN": sign_kucoin_get(ts, path_with_query, self.api_secret),
            "KC-API-TIMESTAMP": ts,
            "KC-API-PASSPHRASE": sign_kucoin_passphrase(self.passphrase, self.api_secret),
            "KC-API-KEY-VERSION": ver,
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
        payload: Any = None
        try:
            payload = resp.json()
        except Exception:
            payload = None

        code_str = str(payload.get("code") or "") if isinstance(payload, dict) else ""
        if signed and code_str == "400009":
            alt_ver = "3" if self.key_version == "2" else "2"
            alt_headers = self.build_headers(path_with_query, key_version=alt_ver)
            resp2 = await self._client.get(f"{base}{path_with_query}", headers=alt_headers)
            payload2: Any = None
            try:
                payload2 = resp2.json()
            except Exception:
                payload2 = None
            if resp2.status_code < 400 and isinstance(payload2, dict) and str(payload2.get("code") or "") == "200000":
                self.key_version = alt_ver
                return payload2.get("data")
            if resp2.status_code >= 400:
                raise KucoinApiError(f"KuCoin GET {path} HTTP {resp2.status_code}: {resp2.text[:240]}")
            if isinstance(payload2, dict):
                raise KucoinApiError(f"KuCoin GET {path} code={payload2.get('code')}: {payload2.get('msg', '')}")
            raise KucoinApiError(f"KuCoin GET {path} unexpected response after key-version retry")

        if resp.status_code >= 400:
            raise KucoinApiError(f"KuCoin GET {path} HTTP {resp.status_code}: {resp.text[:240]}")
        if not isinstance(payload, dict):
            raise KucoinApiError(f"KuCoin GET {path} unexpected payload type: {type(payload).__name__}")
        if code_str != "200000":
            raise KucoinApiError(f"KuCoin GET {path} code={payload.get('code')}: {payload.get('msg', '')}")
        return payload.get("data")

    async def get_api_key_info(self) -> dict[str, Any]:
        res = await self.request_readonly("GET", "/api/v1/user/api-key", futures=False)
        if isinstance(res, dict):
            return res
        if isinstance(res, list) and res and isinstance(res[0], dict):
            return res[0]
        return {}

    async def get_user_info(self) -> dict[str, Any]:
        res = await self.request_readonly("GET", "/api/v2/user-info", futures=False)
        return res if isinstance(res, dict) else {}

    async def get_spot_accounts(self) -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/api/v1/accounts", futures=False)
        if not isinstance(res, list):
            raise KucoinApiError("Malformed KuCoin spot accounts payload")
        out: list[dict[str, Any]] = []
        for r in res:
            if (
                not isinstance(r, dict)
                or not isinstance(r.get("currency"), str)
                or not r["currency"].strip()
            ):
                raise KucoinApiError("Malformed KuCoin spot accounts payload")
            has_num = False
            for k in ("balance", "available"):
                if k in r:
                    if not _is_valid_kucoin_num(r.get(k)):
                        raise KucoinApiError("Malformed KuCoin spot accounts payload")
                    has_num = True
            if "holds" in r and not _is_valid_kucoin_num(r.get("holds")):
                raise KucoinApiError("Malformed KuCoin spot accounts payload")
            if not has_num:
                raise KucoinApiError("Malformed KuCoin spot accounts payload")
            out.append(r)
        return out

    async def get_futures_overview(self, currency: str = "USDT") -> dict[str, Any]:
        res = await self.request_readonly("GET", "/api/v1/account-overview", {"currency": currency}, futures=True)
        if not isinstance(res, dict):
            raise KucoinApiError("Malformed KuCoin futures overview payload")
        has_num = False
        for k in ("accountEquity", "marginBalance", "availableBalance"):
            if k in res:
                if not _is_valid_kucoin_num(res.get(k)):
                    raise KucoinApiError("Malformed KuCoin futures overview payload")
                has_num = True
        if not has_num:
            raise KucoinApiError("Malformed KuCoin futures overview payload")
        return res

    async def get_positions(self) -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/api/v1/positions", futures=True)
        return res if isinstance(res, list) else []

    async def get_history_positions(
        self,
        *,
        since_ms: int = 0,
        page_limit: int = 25,
        warnings_out: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        dedup: dict[str, dict[str, Any]] = {}
        max_p = max(1, int(page_limit))
        truncated = False
        now_ms = self._clock_ms()
        window_ms = 7 * 86_400_000
        if since_ms > 0:
            eff_now = max(int(since_ms), int(now_ms))
            lower_bound = int(since_ms)
            max_windows = max(1, min(53, int((eff_now - lower_bound) // window_ms) + 2))
        else:
            eff_now = max(0, int(now_ms))
            lower_bound = max(0, eff_now - 91 * 86_400_000)
            max_windows = 13

        w_to = eff_now
        abort_windows = False
        for win_idx in range(max_windows):
            if abort_windows or w_to < lower_bound:
                break
            w_from = max(lower_bound, w_to - window_ms)
            saw_older_than_win0 = False

            for page in range(1, max_p + 1):
                res = await self.request_readonly(
                    "GET",
                    "/api/v1/history-positions",
                    {"from": w_from, "to": w_to, "limit": 250, "pageId": page},
                    futures=True,
                )
                items = res.get("items") if isinstance(res, dict) else (res if isinstance(res, list) else [])
                if not isinstance(items, list) or not items:
                    break
                valid_rows = [r for r in items if isinstance(r, dict)]
                if not valid_rows:
                    break
                new_cnt = 0
                row_ts_list: list[int] = []
                for idx, r in enumerate(valid_rows):
                    pk = kucoin_position_unique_key(r, fallback_idx=idx)
                    if pk not in dedup:
                        dedup[pk] = r
                        new_cnt += 1
                    rts = _kucoin_ts_ms(r.get("closeTime") or r.get("closeTimestamp") or r.get("updatedAt"))
                    if rts > 0:
                        row_ts_list.append(rts)

                if win_idx == 0 and any(0 < rts < w_from - 60_000 for rts in row_ts_list):
                    saw_older_than_win0 = True
                if (
                    win_idx >= 1
                    and page == 1
                    and new_cnt == 0
                    and (not row_ts_list or all(rts < w_from - 60_000 or rts > w_to + 60_000 for rts in row_ts_list))
                ):
                    abort_windows = True
                    break
                if new_cnt == 0:
                    break
                if isinstance(res, dict):
                    if res.get("hasMore") is False:
                        break
                    if res.get("totalPage") is not None and page >= int(res.get("totalPage") or 1):
                        break
                    if res.get("totalPage") is None and res.get("hasMore") is None and len(valid_rows) < 250:
                        break
                elif len(valid_rows) < 250:
                    break

                if page == max_p:
                    truncated = True

            if saw_older_than_win0 or w_from <= lower_bound:
                break
            w_to = w_from

        if truncated and warnings_out is not None:
            warnings_out.append(
                f"KuCoin history-positions reached {max_p}-page pagination cap; older positions may be truncated."
            )
        return list(dedup.values())

    async def get_futures_fills(
        self,
        *,
        since_ms: int = 0,
        max_pages: int = 20,
        warnings_out: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        dedup: dict[str, dict[str, Any]] = {}
        max_p = max(1, int(max_pages))
        truncated = False
        for page in range(1, max_p + 1):
            params: dict[str, Any] = {"pageSize": 100, "currentPage": page}
            if since_ms > 0:
                params["startAt"] = since_ms
            res = await self.request_readonly("GET", "/api/v1/fills", params, futures=True)
            items = res.get("items") if isinstance(res, dict) else (res if isinstance(res, list) else [])
            valid_rows = [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []
            if not valid_rows:
                break

            new_cnt = 0
            for idx, r in enumerate(valid_rows):
                fk = str(r.get("tradeId") or r.get("id") or f"{r.get('orderId')}_{r.get('createdAt') or r.get('tradeTime')}_{idx}")
                if fk not in dedup:
                    dedup[fk] = r
                    new_cnt += 1

            if new_cnt == 0:
                break
            if isinstance(res, dict) and res.get("totalPage") is not None:
                if page >= int(res.get("totalPage") or 1):
                    break
            elif len(valid_rows) < 100:
                break

            if page == max_p:
                truncated = True

        if truncated and warnings_out is not None:
            warnings_out.append(
                f"KuCoin futures fills reached {max_p}-page pagination cap; older fills may be truncated."
            )
        return list(dedup.values())

    async def get_futures_transactions(
        self,
        *,
        since_ms: int = 0,
        max_pages: int = 20,
        warnings_out: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        dedup: dict[str, dict[str, Any]] = {}
        max_p = max(1, int(max_pages))
        next_offset: Optional[int] = None
        truncated = False

        for page in range(1, max_p + 1):
            params: dict[str, Any] = {"currency": "USDT", "maxCount": 100}
            if since_ms > 0:
                params["startAt"] = since_ms
            if next_offset is not None:
                params["offset"] = next_offset
            res = await self.request_readonly("GET", "/api/v1/transaction-history", params, futures=True)
            items = res.get("dataList") if isinstance(res, dict) else (res if isinstance(res, list) else [])
            valid_rows = [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []
            if not valid_rows:
                break

            new_cnt = 0
            offsets: list[int] = []
            for r in valid_rows:
                raw_off = r.get("offset")
                if raw_off not in (None, ""):
                    try:
                        offsets.append(int(raw_off))
                    except (TypeError, ValueError):
                        pass
                tk = str(raw_off if raw_off not in (None, "") else f"{r.get('time')}_{r.get('type')}_{r.get('amount')}")
                if tk not in dedup:
                    dedup[tk] = r
                    new_cnt += 1

            if new_cnt == 0:
                break
            has_more = res.get("hasMore") if isinstance(res, dict) else None
            if has_more is False:
                break
            if has_more is not True and len(valid_rows) < 100:
                break
            if not offsets:
                break
            if page == max_p:
                truncated = True
                break

            min_off = min(offsets)
            next_offset = min_off if (next_offset is None or min_off < next_offset) else (next_offset - 1)

        if truncated and warnings_out is not None:
            warnings_out.append(
                f"KuCoin transaction-history reached {max_p}-page pagination cap; older transactions may be truncated."
            )
        return list(dedup.values())

    async def _fetch_spot_cashflows(
        self,
        path: str,
        *,
        id_keys: tuple[str, ...],
        max_pages: int = 10,
        warnings_out: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        dedup: dict[str, dict[str, Any]] = {}
        max_p = max(1, int(max_pages))
        truncated = False
        for page in range(1, max_p + 1):
            res = await self.request_readonly(
                "GET",
                path,
                {"status": "SUCCESS", "pageSize": 100, "currentPage": page},
                futures=False,
            )
            items = res.get("items") if isinstance(res, dict) else (res if isinstance(res, list) else [])
            valid_rows = [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []
            if not valid_rows:
                break

            new_cnt = 0
            for r in valid_rows:
                rk = ""
                for k in id_keys:
                    if r.get(k) not in (None, ""):
                        rk = str(r[k])
                        break
                if not rk:
                    rk = f"{r.get('createdAt')}_{r.get('amount')}_{r.get('currency')}"
                if rk not in dedup:
                    dedup[rk] = r
                    new_cnt += 1

            if new_cnt == 0:
                break
            if isinstance(res, dict) and res.get("totalPage") is not None:
                if page >= int(res.get("totalPage") or 1):
                    break
            elif len(valid_rows) < 100:
                break

            if page == max_p:
                truncated = True

        if truncated and warnings_out is not None:
            warnings_out.append(f"KuCoin {path} reached {max_p}-page pagination cap.")
        return list(dedup.values())

    async def get_deposits(self, *, max_pages: int = 10, warnings_out: Optional[list[str]] = None) -> list[dict[str, Any]]:
        return await self._fetch_spot_cashflows(
            "/api/v1/deposits",
            id_keys=("walletTxId", "id"),
            max_pages=max_pages,
            warnings_out=warnings_out,
        )

    async def get_withdrawals(self, *, max_pages: int = 10, warnings_out: Optional[list[str]] = None) -> list[dict[str, Any]]:
        return await self._fetch_spot_cashflows(
            "/api/v1/withdrawals",
            id_keys=("id", "walletTxId"),
            max_pages=max_pages,
            warnings_out=warnings_out,
        )

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
        failed_fields: list[str] = []

        def _is_fatal(exc: Exception) -> bool:
            msg_low = str(exc).lower()
            return (
                isinstance(exc, httpx.TransportError)
                or "http 5" in msg_low
                or "http 429" in msg_low
                or "code=400009" in msg_low
                or "parameter" in msg_low
            )

        spot_accounts: list[dict[str, Any]] = []
        futures_overview: dict[str, Any] = {}
        try:
            spot_accounts = await self.get_spot_accounts()
        except Exception as spot_exc:
            if _is_fatal(spot_exc):
                raise
            warnings.append(f"spot_accounts: {spot_exc}")
            failed_fields.append("spot_accounts")
        try:
            futures_overview = await self.get_futures_overview("USDT")
        except Exception as fut_exc:
            if _is_fatal(fut_exc) or "spot_accounts" in failed_fields:
                raise fut_exc
            warnings.append(f"futures_overview: {fut_exc}")
            failed_fields.append("futures_overview")

        async def _opt(name: str, coro: Any, default: Any) -> Any:
            try:
                return await coro
            except Exception as exc:
                if _is_fatal(exc):
                    raise
                warnings.append(f"{name}: {exc}")
                failed_fields.append(name)
                return default

        positions = await _opt("positions", self.get_positions(), [])
        history_positions = await _opt(
            "history_positions",
            self.get_history_positions(since_ms=since_ms, warnings_out=warnings),
            [],
        )
        fills = await _opt("fills", self.get_futures_fills(since_ms=since_ms, warnings_out=warnings), [])
        tx_history = await _opt(
            "tx_history",
            self.get_futures_transactions(since_ms=since_ms, warnings_out=warnings),
            [],
        )
        deposits = await _opt("deposits", self.get_deposits(warnings_out=warnings), [])
        withdrawals = await _opt("withdrawals", self.get_withdrawals(warnings_out=warnings), [])
        tickers = await _opt("tickers", self.get_spot_tickers(), {})
        base_fee = await _opt("base_fee", self.get_base_fee(), {})

        out_snap: dict[str, Any] = {
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
        if failed_fields:
            out_snap["_failed_fields"] = failed_fields
        return out_snap


def _kucoin_ts_ms(raw: Any) -> int:
    if raw in (None, ""):
        return 0
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0
    if val <= 0:
        return 0
    if val > 1e15:
        return int(val // 1_000_000)
    if val > 1e10:
        return int(val)
    return int(round(val * 1000.0))


def kucoin_position_unique_key(r: dict[str, Any], *, fallback_idx: int = 0) -> str:
    cid = r.get("closeId") or r.get("id")
    if cid not in (None, ""):
        return str(cid)
    ct = r.get("closeTime") or r.get("closeTimestamp") or r.get("updatedAt")
    ot = r.get("openTime") or r.get("openTimestamp") or r.get("createdAt")
    pid = r.get("positionId") or ""
    sym = r.get("symbol") or ""
    if pid or ct or ot:
        return f"{pid}_{ct}_{ot}_{sym}"
    return f"pos_{sym}_{fallback_idx}"


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

    dedup_pos_map: dict[str, dict[str, Any]] = {}
    for idx_p, p in enumerate(history_positions):
        if isinstance(p, dict):
            pk = kucoin_position_unique_key(p, fallback_idx=idx_p)
            dedup_pos_map[pk] = p
    unique_positions = list(dedup_pos_map.values())

    rebate_sum = sum(
        max(0.0, float(tx.get("amount") or 0.0))
        for tx in (tx_history or [])
        if isinstance(tx, dict) and "REBATE" in str(tx.get("type") or "").upper()
    )
    total_pos_fees = sum(abs(float(p.get("tradeFee") or p.get("fee") or 0.0)) for p in unique_positions)
    rebate_ratio = min(0.95, rebate_sum / total_pos_fees) if total_pos_fees > 0 and rebate_sum > 0 else 0.0

    records: list[dict[str, Any]] = []
    sorted_pos = sorted(
        unique_positions,
        key=lambda r: _kucoin_ts_ms(r.get("closeTime") or r.get("closeTimestamp") or r.get("updatedAt")),
    )

    for idx, row in enumerate(sorted_pos):
        raw_sym = str(row.get("symbol") or "").upper()
        if not raw_sym:
            continue
        open_ms = _kucoin_ts_ms(row.get("openTime") or row.get("openTimestamp") or row.get("createdAt"))
        close_ms = _kucoin_ts_ms(row.get("closeTime") or row.get("closeTimestamp") or row.get("updatedAt")) or open_ms
        entry_px = float(row.get("openPrice") or row.get("avgEntryPrice") or 0.0)
        exit_px = float(row.get("closePrice") or row.get("avgClosePrice") or 0.0)

        pos_total_fee = abs(float(row.get("tradeFee") or row.get("fee") or 0.0))
        funding_pnl = float(row.get("fundingFee") or 0.0)

        has_explicit_net = row.get("pnl") is not None or row.get("realisedPnl") is not None
        has_explicit_gross = row.get("realisedGrossPnl") is not None or row.get("grossPnl") is not None
        if has_explicit_net:
            realized_net_book = float(row.get("pnl") if row.get("pnl") is not None else row.get("realisedPnl") or 0.0)
            book_pnl = round(realized_net_book, 6)
            if has_explicit_gross:
                gross_pnl = round(
                    float(row.get("realisedGrossPnl") if row.get("realisedGrossPnl") is not None else row.get("grossPnl") or 0.0),
                    6,
                )
            else:
                gross_pnl = round(book_pnl + pos_total_fee - funding_pnl, 6)
        elif has_explicit_gross:
            gross_pnl = round(
                float(row.get("realisedGrossPnl") if row.get("realisedGrossPnl") is not None else row.get("grossPnl") or 0.0),
                6,
            )
            book_pnl = round(gross_pnl - pos_total_fee + funding_pnl, 6)
        else:
            gross_pnl = 0.0
            book_pnl = round(-pos_total_fee + funding_pnl, 6)

        side_raw = str(row.get("side") or row.get("type") or "").lower()
        if "short" in side_raw or side_raw == "sell":
            side = "SHORT"
        elif "long" in side_raw or side_raw == "buy":
            side = "LONG"
        elif entry_px > 0 and exit_px > 0 and abs(exit_px - entry_px) > 1e-12 and abs(gross_pnl) > 1e-12:
            side = "LONG" if ((exit_px - entry_px) * gross_pnl > 0) else "SHORT"
        else:
            side = "LONG"

        qty = abs(float(row.get("closeSize") or row.get("size") or row.get("qty") or 0.0))
        mult = abs(float(row.get("multiplier") or 1.0))
        eff_coin_qty = qty * mult if mult > 0 and mult != 1.0 else qty
        if eff_coin_qty <= 0 and entry_px > 0 and exit_px > 0 and abs(exit_px - entry_px) > 1e-12 and abs(gross_pnl) > 1e-12:
            eff_coin_qty = round(abs(gross_pnl) / abs(exit_px - entry_px), 8)

        entry_notional = entry_px * eff_coin_qty if entry_px > 0 and eff_coin_qty > 0 else abs(float(row.get("openValue") or 0.0))
        exit_notional = exit_px * eff_coin_qty if exit_px > 0 and eff_coin_qty > 0 else abs(float(row.get("closeValue") or entry_notional))

        win_fills = [
            f
            for f in fills_by_sym.get(raw_sym, [])
            if open_ms > 0 and (open_ms - 5_000 <= _kucoin_ts_ms(f.get("createdAt") or f.get("tradeTime")) <= close_ms + 5_000)
        ]
        open_side_tok = "buy" if side == "LONG" else "sell"
        en_fills = [f for f in win_fills if str(f.get("side") or "").lower() == open_side_tok]
        ex_fills = [f for f in win_fills if str(f.get("side") or "").lower() != open_side_tok]

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

        if not has_explicit_net and has_explicit_gross:
            book_pnl = round(gross_pnl - total_fee + funding_pnl, 6)

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
