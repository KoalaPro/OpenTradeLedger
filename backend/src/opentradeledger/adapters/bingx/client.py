"""Strictly GET-only BingX OpenAPI v2/v3 signed REST client and per-leg trade normalizer.

Signature specification (BingX OpenAPI):
  sorted_query = urlencode(sorted({**params, "timestamp": timestamp_ms}.items()))
  signature = hex(HMAC-SHA256(sorted_query, api_secret))
  Header: X-BX-APIKEY = api_key
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import math
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx

from opentradeledger.adapters.cycle_evidence import cycle_journal_keys, event_fill, usd_fee
from opentradeledger.core.position_lifecycle import build_position_events, dated_cash

from opentradeledger.security.readonly_guard import assert_readonly_request

TZ_UTC8 = timezone(timedelta(hours=8))

BINGX_ALLOWED_GET_PATHS: tuple[str, ...] = (
    "/openApi/account/v1/uid",
    "/openApi/subAccount/v1/list",
    "/openApi/swap/v3/user/balance",
    "/openApi/swap/v2/user/balance",
    "/openApi/swap/v2/user/positions",
    "/openApi/swap/v2/user/income",
    "/openApi/swap/v2/trade/fillHistory",
    "/openApi/swap/v2/trade/allFillOrders",
    "/openApi/swap/v2/trade/commissionRate",
    "/openApi/spot/v1/account/balance",
    "/openApi/spot/v1/ticker/price",
    "/openApi/wallets/v1/capital/deposit/hisrec",
    "/openApi/wallets/v1/capital/withdraw/history",
    "/openApi/api/v3/capital/deposit/hisrec",
    "/openApi/api/v3/capital/withdraw/history",
)


class BingxApiError(RuntimeError):
    """Raised when BingX OpenAPI returns code != 0 or an HTTP error."""


def _is_valid_bingx_num(val: Any) -> bool:
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


def _validate_bingx_swap_item(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise BingxApiError("Malformed BingX swap balance payload")
    if "asset" in row and (not isinstance(row.get("asset"), str) or not row["asset"].strip()):
        raise BingxApiError("Malformed BingX swap balance payload")
    has_num = False
    for key in ("equity", "balance"):
        if key in row:
            if not _is_valid_bingx_num(row.get(key)):
                raise BingxApiError("Malformed BingX swap balance payload")
            has_num = True
    if not has_num:
        raise BingxApiError("Malformed BingX swap balance payload")
    return row


def _validate_bingx_spot_item(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise BingxApiError("Malformed BingX spot balance payload")
    asset = row.get("asset")
    if not isinstance(asset, str) or not asset.strip():
        raise BingxApiError("Malformed BingX spot balance payload")
    bingx_spot_quantity(row)
    return row


def bingx_spot_quantity(row: dict[str, Any]) -> float:
    for key in ("free", "locked", "balance"):
        if key in row and not _is_valid_bingx_num(row[key]):
            raise BingxApiError("Malformed BingX spot balance payload")
    if "balance" in row:
        return float(row["balance"])
    if "free" in row and "locked" in row:
        return float(row["free"]) + float(row["locked"])
    raise BingxApiError("Malformed BingX spot balance payload: missing total or its components")


def parse_bingx_ts_ms(raw: Any) -> int:
    if raw in (None, ""):
        return 0
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val <= 0:
            return 0
        return int(val) if val > 1e10 else int(round(val * 1000.0))
    s = str(raw).strip()
    if not s:
        return 0
    try:
        val = float(s)
        if val <= 0:
            return 0
        return int(val) if val > 1e10 else int(round(val * 1000.0))
    except ValueError:
        pass
    iso_s = f"{s[:-1]}+00:00" if s.endswith(("Z", "z")) else s
    try:
        dt = datetime.fromisoformat(iso_s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int(round(dt.timestamp() * 1000.0)))
    except ValueError:
        return 0


def _fmt_bj_ms(ms: int) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ_UTC8).strftime("%m-%d %H:%M")


def sign_bingx_query(query_string: str, api_secret: str) -> str:
    return hmac.new(api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_bingx_symbol(raw: str) -> str:
    s = (raw or "").strip().upper().replace("-", "_")
    if "_" in s:
        return s
    for q in ("USDT", "USDC", "USD"):
        if s.endswith(q) and len(s) > len(q):
            return f"{s[:-len(q)]}_{q}"
    return s


class BingxReadOnlyClient:
    """Strictly GET-only signed client for BingX Perpetual Swap & Spot OpenAPI."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = "https://open-api.bingx.com",
        recv_window: int = 5000,
        client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 12.0,
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = int(recv_window)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec, connect=6.0))
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request_readonly(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        *,
        signed: bool = True,
    ) -> Any:
        assert_readonly_request(method, path, BINGX_ALLOWED_GET_PATHS)
        clean_params = {k: str(v) for k, v in (params or {}).items() if v is not None and v != ""}
        if signed:
            clean_params.setdefault("timestamp", str(self._clock_ms()))
            clean_params.setdefault("recvWindow", str(self.recv_window))
            qs = urlencode(sorted(clean_params.items()))
            sig = sign_bingx_query(qs, self.api_secret)
            url = f"{self.base_url}{path}?{qs}&signature={sig}"
            headers = {"X-BX-APIKEY": self.api_key}
        else:
            qs = urlencode(sorted(clean_params.items()))
            url = f"{self.base_url}{path}" + (f"?{qs}" if qs else "")
            headers = {}

        resp = await self._client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise BingxApiError(f"BingX GET {path} HTTP {resp.status_code}: {resp.text[:240]}")
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            raise BingxApiError(f"BingX GET {path} unexpected payload type: {type(payload).__name__}")
        if int(payload.get("code", -1)) != 0:
            raise BingxApiError(f"BingX GET {path} code={payload.get('code')}: {payload.get('msg', '')}")
        return payload.get("data")

    async def get_uid(self) -> str:
        res = await self.request_readonly("GET", "/openApi/account/v1/uid")
        if isinstance(res, dict) and res.get("uid") not in (None, "", 0):
            return str(res["uid"]).strip()
        return ""

    async def get_swap_balance(self) -> list[dict[str, Any]]:
        try:
            res = await self.request_readonly("GET", "/openApi/swap/v3/user/balance")
        except Exception as v3_exc:
            msg_low = str(v3_exc).lower()
            if isinstance(v3_exc, httpx.TransportError) or "http 5" in msg_low or "http 429" in msg_low:
                raise
            res = await self.request_readonly("GET", "/openApi/swap/v2/user/balance")
        if isinstance(res, list):
            return [_validate_bingx_swap_item(r) for r in res]
        if isinstance(res, dict) and "balance" in res:
            bal_node = res["balance"]
            if isinstance(bal_node, dict):
                return [_validate_bingx_swap_item(bal_node)]
            if isinstance(bal_node, list):
                return [_validate_bingx_swap_item(r) for r in bal_node]
        raise BingxApiError("Malformed BingX swap balance payload")

    async def get_positions(self) -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/openApi/swap/v2/user/positions")
        return [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []

    async def get_income(
        self,
        *,
        since_ms: int = 0,
        limit: int = 500,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        page_limit = max(1, min(1000, int(limit)))
        merged: dict[str, dict[str, Any]] = {}
        cursor_start: Optional[int] = since_ms if since_ms > 0 else None
        cursor_end: Optional[int] = None

        for _ in range(max(1, int(max_pages))):
            params: dict[str, Any] = {"limit": page_limit}
            if cursor_start is not None and cursor_start > 0:
                params["startTime"] = cursor_start
            if cursor_end is not None and cursor_end > 0:
                params["endTime"] = cursor_end
            res = await self.request_readonly("GET", "/openApi/swap/v2/user/income", params)
            valid_rows = [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []
            if not valid_rows:
                break

            new_cnt = 0
            ts_list: list[int] = []
            for r in valid_rows:
                ik = str(r.get("tranId") or f"{r.get('time')}_{r.get('incomeType')}_{r.get('income')}_{r.get('symbol')}")
                if ik not in merged:
                    merged[ik] = r
                    new_cnt += 1
                rts = parse_bingx_ts_ms(r.get("time"))
                if rts > 0:
                    ts_list.append(rts)

            if len(valid_rows) < page_limit or new_cnt == 0 or not ts_list:
                break

            first_ts = parse_bingx_ts_ms(valid_rows[0].get("time"))
            last_ts = parse_bingx_ts_ms(valid_rows[-1].get("time"))
            min_ts = min(ts_list)
            max_ts = max(ts_list)
            if first_ts >= last_ts:
                next_end = min_ts if (cursor_end is None or min_ts < cursor_end) and min_ts < max_ts else (min_ts - 1)
                if next_end <= 0 or (since_ms > 0 and next_end < since_ms):
                    break
                cursor_end = next_end
            else:
                next_start = max_ts if (cursor_start is None or max_ts > cursor_start) and min_ts < max_ts else (max_ts + 1)
                cursor_start = next_start

        return list(merged.values())

    async def get_fills(
        self,
        *,
        since_ms: int = 0,
        max_windows: int = 53,
        max_empty_windows: int = 8,
        earliest_hint_ms: int = 0,
    ) -> list[dict[str, Any]]:
        now_ms = self._clock_ms()
        seven_days_ms = 7 * 86_400_000 - 60_000
        merged: dict[str, dict[str, Any]] = {}
        w_end = now_ms
        if since_ms > 0:
            span = max(0, now_ms - since_ms)
            needed = max(1, int(span // seven_days_ms) + 1)
            win_cap = min(max(1, int(max_windows)), needed)
        else:
            win_cap = max(1, int(max_windows))

        empty_streak = 0
        abort_all = False
        for _ in range(win_cap):
            if since_ms > 0 and w_end < since_ms:
                break
            w_start = max(since_ms, w_end - seven_days_ms) if since_ms > 0 else max(0, w_end - seven_days_ms)
            sub_end = w_end
            win_had_rows = False

            for _sub_idx in range(10):
                params: dict[str, Any] = {
                    "tradingUnit": "COIN",
                    "startTs": w_start,
                    "endTs": sub_end,
                    "limit": 500,
                }
                res = await self.request_readonly("GET", "/openApi/swap/v2/trade/allFillOrders", params)
                rows = (
                    res.get("fill_orders")
                    if isinstance(res, dict) and isinstance(res.get("fill_orders"), list)
                    else (res if isinstance(res, list) else [])
                )
                valid_rows = [r for r in rows if isinstance(r, dict)]
                if not valid_rows:
                    break

                win_had_rows = True
                empty_streak = 0
                sub_new = 0
                in_bounds = False
                sub_ts_list: list[int] = []
                for r in valid_rows:
                    fk = str(r.get("tradeId") or f"{r.get('orderId')}_{r.get('filledTm') or r.get('time')}")
                    if fk not in merged:
                        merged[fk] = r
                        sub_new += 1
                    rts = parse_bingx_ts_ms(r.get("filledTm") or r.get("time") or r.get("updateTime"))
                    if rts > 0:
                        sub_ts_list.append(rts)
                    if w_start - 86_400_000 <= rts <= sub_end + 86_400_000:
                        in_bounds = True

                if sub_new == 0 and not in_bounds:
                    abort_all = True
                    break
                if len(valid_rows) < 500 or sub_new == 0 or not sub_ts_list:
                    break

                min_rts = min(sub_ts_list)
                max_rts = max(sub_ts_list)
                next_sub_end = min_rts if (min_rts < sub_end and min_rts < max_rts) else (min_rts - 1)
                if next_sub_end <= w_start:
                    break
                sub_end = next_sub_end

            if abort_all or w_start <= 0 or (since_ms > 0 and w_start <= since_ms):
                break

            if not win_had_rows and since_ms <= 0:
                if earliest_hint_ms > 0 and w_start > earliest_hint_ms:
                    pass
                else:
                    empty_streak += 1
                    if max_empty_windows > 0 and empty_streak >= max_empty_windows:
                        break

            w_end = w_start - 1

        return list(merged.values())

    async def get_spot_balances(self) -> list[dict[str, Any]]:
        res = await self.request_readonly("GET", "/openApi/spot/v1/account/balance")
        if isinstance(res, dict) and "balances" in res and isinstance(res["balances"], list):
            return [_validate_bingx_spot_item(r) for r in res["balances"]]
        if isinstance(res, list):
            return [_validate_bingx_spot_item(r) for r in res]
        raise BingxApiError("Malformed BingX spot balance payload")

    async def get_spot_tickers(self) -> dict[str, float]:
        res = await self.request_readonly("GET", "/openApi/spot/v1/ticker/price", signed=False)
        prices: dict[str, float] = {}
        rows = res if isinstance(res, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").upper().replace("_", "-")
            trades = row.get("trades")
            raw_px = (
                trades[0].get("price")
                if isinstance(trades, list) and trades and isinstance(trades[0], dict)
                else row.get("price")
            )
            for q in ("-USDT", "-USDC"):
                if sym.endswith(q) and len(sym) > len(q):
                    base = sym[: -len(q)]
                    try:
                        px = float(raw_px or 0.0)
                        if px > 0:
                            prices[base] = px
                    except (TypeError, ValueError):
                        pass
                    break
        return prices

    async def _fetch_capital_history(
        self,
        v1_path: str,
        v3_path: str,
        *,
        id_keys: tuple[str, ...],
        ts_keys: tuple[str, ...],
        since_ms: int = 0,
        max_windows: int = 4,
        page_limit: int = 200,
        max_pages_per_window: int = 20,
    ) -> list[dict[str, Any]]:
        now_ms = self._clock_ms()
        ninety_days_ms = 90 * 86_400_000 - 60_000
        merged: dict[str, dict[str, Any]] = {}
        active_path = v1_path
        w_end = now_ms

        if since_ms > 0:
            span = max(0, now_ms - since_ms)
            win_cap = min(max(1, int(max_windows)), max(1, int(span // ninety_days_ms) + 1))
        else:
            win_cap = max(1, int(max_windows))

        abort_windows = False
        for _ in range(win_cap):
            if since_ms > 0 and w_end < since_ms:
                break
            w_start = max(since_ms, w_end - ninety_days_ms) if since_ms > 0 else max(0, w_end - ninety_days_ms)
            offset = 0
            for _page_idx in range(max(1, int(max_pages_per_window))):
                params: dict[str, Any] = {
                    "limit": page_limit,
                    "offset": offset,
                    "startTime": w_start,
                    "endTime": w_end,
                }
                try:
                    res = await self.request_readonly("GET", active_path, params)
                except BingxApiError as exc:
                    err_low = str(exc).lower()
                    if active_path == v1_path and ("http 404" in err_low or "not found" in err_low):
                        active_path = v3_path
                        res = await self.request_readonly("GET", active_path, params)
                    else:
                        raise

                rows = (
                    res.get("list")
                    if isinstance(res, dict) and isinstance(res.get("list"), list)
                    else (res if isinstance(res, list) else [])
                )
                valid_rows = [r for r in rows if isinstance(r, dict)]
                if not valid_rows:
                    break

                page_new = 0
                in_bounds = False
                for r in valid_rows:
                    rid = ""
                    for k in id_keys:
                        if r.get(k) not in (None, ""):
                            rid = str(r[k])
                            break
                    rts = 0
                    for tk in ts_keys:
                        rts = parse_bingx_ts_ms(r.get(tk))
                        if rts > 0:
                            break
                    if not rid:
                        rid = f"{rts}_{r.get('coin')}_{r.get('amount')}_{r.get('address') or ''}"
                    if rid not in merged:
                        merged[rid] = r
                        page_new += 1
                    if rts == 0 or (w_start - 86_400_000 <= rts <= w_end + 86_400_000):
                        in_bounds = True

                if page_new == 0:
                    if not in_bounds:
                        abort_windows = True
                    break
                if len(valid_rows) < page_limit:
                    break
                offset += page_limit

            if abort_windows or w_start <= 0 or (since_ms > 0 and w_start <= since_ms):
                break
            w_end = w_start - 1

        return list(merged.values())

    async def get_deposits(self, *, since_ms: int = 0) -> list[dict[str, Any]]:
        return await self._fetch_capital_history(
            "/openApi/wallets/v1/capital/deposit/hisrec",
            "/openApi/api/v3/capital/deposit/hisrec",
            id_keys=("txId", "id"),
            ts_keys=("insertTime", "createTime", "time"),
            since_ms=since_ms,
        )

    async def get_withdrawals(self, *, since_ms: int = 0) -> list[dict[str, Any]]:
        return await self._fetch_capital_history(
            "/openApi/wallets/v1/capital/withdraw/history",
            "/openApi/api/v3/capital/withdraw/history",
            id_keys=("id", "txId"),
            ts_keys=("applyTime", "completeTime", "time"),
            since_ms=since_ms,
        )

    async def get_commission_rate(self) -> dict[str, Any]:
        res = await self.request_readonly("GET", "/openApi/swap/v2/trade/commissionRate")
        if isinstance(res, dict) and isinstance(res.get("commission"), dict):
            return res["commission"]
        return res if isinstance(res, dict) else {}

    async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
        warnings: list[str] = []
        failed_fields: list[str] = []
        swap_balances = await self.get_swap_balance()

        def _is_fatal(exc: Exception) -> bool:
            msg_low = str(exc).lower()
            return (
                isinstance(exc, httpx.TransportError)
                or "http 5" in msg_low
                or "http 429" in msg_low
                or "code=429" in msg_low
                or "code=100400" in msg_low
                or "code=100410" in msg_low
                or "code=100500" in msg_low
                or "100410" in msg_low
                or "100500" in msg_low
                or "rate limit" in msg_low
                or "too many" in msg_low
                or "busy" in msg_low
                or "timeout" in msg_low
                or "parameter" in msg_low
            )

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
        income = await _opt("income", self.get_income(since_ms=since_ms), [])
        spot_balances = await _opt("spot_balances", self.get_spot_balances(), [])
        tickers = await _opt("tickers", self.get_spot_tickers(), {})
        deposits = await _opt("deposits", self.get_deposits(since_ms=since_ms), [])
        withdrawals = await _opt("withdrawals", self.get_withdrawals(since_ms=since_ms), [])

        hint_candidates: list[int] = []
        for inc_row in income:
            if isinstance(inc_row, dict):
                ts_i = parse_bingx_ts_ms(inc_row.get("time"))
                if ts_i > 0:
                    hint_candidates.append(ts_i)
        for dep_row in deposits:
            if isinstance(dep_row, dict):
                ts_d = parse_bingx_ts_ms(dep_row.get("insertTime") or dep_row.get("createTime") or dep_row.get("time"))
                if ts_d > 0:
                    hint_candidates.append(ts_d)
        for wd_row in withdrawals:
            if isinstance(wd_row, dict):
                ts_w = parse_bingx_ts_ms(wd_row.get("applyTime") or wd_row.get("completeTime") or wd_row.get("time"))
                if ts_w > 0:
                    hint_candidates.append(ts_w)
        earliest_hint_ms = min(hint_candidates) if hint_candidates else 0

        fills = await _opt(
            "fills",
            self.get_fills(since_ms=since_ms, earliest_hint_ms=earliest_hint_ms),
            [],
        )
        commission = await _opt("commission", self.get_commission_rate(), {})

        out_snap: dict[str, Any] = {
            "venue_id": "bingx",
            "swap_balances": swap_balances,
            "positions": positions,
            "income": income,
            "fills": fills,
            "spot_balances": spot_balances,
            "tickers": tickers,
            "deposits": deposits,
            "withdrawals": withdrawals,
            "commission": commission,
            "warnings": warnings,
        }
        if failed_fields:
            out_snap["_failed_fields"] = failed_fields
        return out_snap


def _bingx_fee_cost(fill: dict[str, Any]) -> float | None:
    # Swap fill fees are a wallet movement: a negative amount is a debit.
    raw = fill.get("commission")
    if raw in (None, ""):
        raw = fill.get("fee")
    movement = usd_fee(raw, fill.get("commissionAsset") or fill.get("feeAsset") or fill.get("currency") or "USDT")
    return -movement if movement is not None else None


def normalize_bingx_closed_trades(
    fills: list[dict[str, Any]],
    income_rows: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Reconstructs closed perpetual trades on BingX from `allFillOrders` & `income` ledger."""
    rebates_total = sum(
        max(0.0, float(inc.get("income") or 0.0))
        for inc in (income_rows or [])
        if isinstance(inc, dict)
        and any(tok in str(inc.get("incomeType") or "").upper() for tok in ("REBATE", "COMMISSION_REBATE", "CASHBACK"))
    )
    fees_total = sum(max(0.0, _bingx_fee_cost(f) or 0.0) for f in fills if isinstance(f, dict))
    rebate_ratio = min(0.95, rebates_total / fees_total) if fees_total > 0 and rebates_total > 0 else 0.0

    funding_by_sym: dict[str, list[dict[str, Any]]] = {}
    consumed_fund: set[str] = set()
    for idx, inc in enumerate(income_rows or []):
        if not isinstance(inc, dict):
            continue
        if str(inc.get("incomeType") or "").upper() == "FUNDING_FEE":
            sym = normalize_bingx_symbol(str(inc.get("symbol") or ""))
            ts = parse_bingx_ts_ms(inc.get("time"))
            fid = str(inc.get("tranId") or f"bf_{sym}_{ts}_{idx}")
            funding_by_sym.setdefault(sym, []).append({"_id": fid, "time": ts, "val": float(inc.get("income") or 0.0)})

    sorted_fills = sorted(
        (f for f in fills if isinstance(f, dict)),
        key=lambda r: parse_bingx_ts_ms(r.get("filledTm") or r.get("time") or r.get("updateTime")),
    )

    open_lots: dict[tuple[str, str], dict[str, Any]] = {}
    records: list[dict[str, Any]] = []

    def _emit_bingx_record(
        *,
        sym: str,
        trade_side: str,
        qty: float,
        open_ms: int,
        close_ms: int,
        en_px: float,
        ex_px: float,
        en_notional: float,
        ex_notional: float,
        en_fee: float,
        ex_fee: float,
        en_known: bool,
        en_makers: bool,
        en_takers: bool,
        ex_makers: bool,
        ex_takers: bool,
        realized_pnl: float,
        oid_id: str,
        is_partial: bool,
        en_credit: float = 0.0,
        ex_credit: float = 0.0,
        ex_known: bool = True,
        exit_batches: list[dict[str, Any]] | None = None,
        journal_keys: list[str] | None = None,
        position_events: list[dict[str, Any]] | None = None,
    ) -> None:
        total_fee = round(en_fee + ex_fee, 6)
        en_reb = round(en_credit + en_fee * rebate_ratio, 6)
        ex_reb = round(ex_credit + ex_fee * rebate_ratio, 6)
        total_rebate = round(en_reb + ex_reb, 6)

        funding_pnl = 0.0
        funding_receipts = []
        for fl in funding_by_sym.get(sym, []):
            if fl["_id"] in consumed_fund:
                continue
            if open_ms - 5_000 <= fl["time"] <= close_ms + 5_000:
                consumed_fund.add(fl["_id"])
                funding_pnl += float(fl["val"])
                funding_receipts.append((fl["time"], fl["val"]))

        book_pnl = round(realized_pnl - total_fee + funding_pnl, 6)
        net_pnl = round(book_pnl + total_rebate, 6)
        settled_str = _fmt_bj_ms(close_ms) if total_rebate > 0 else ""
        en_maker = en_makers and not en_takers
        ex_maker = ex_makers and not ex_takers
        en_mode = "MIXED" if (en_makers and en_takers) else ("LIMIT_MAKER" if en_makers else "MARKET_TAKER")
        ex_mode = "MIXED" if (ex_makers and ex_takers) else ("LIMIT_MAKER" if ex_makers else "MARKET_TAKER")

        records.append(
            {
                **cycle_journal_keys(journal_keys or [f"bingx_{oid_id}_{close_ms}"], is_partial),
                "position_events": position_events or [],
                "venue_id": "bingx",
                "venue_name": "BingX",
                "venue_name_zh": "BingX",
                "symbol": sym,
                "side": trade_side,
                "qty": round(qty, 8),
                "open_time_ms": open_ms,
                "close_time_ms": close_ms,
                "gross_pnl_usd": round(realized_pnl, 4),
                "funding_pnl_usd": round(funding_pnl, 4),
                "funding_by_day": dated_cash(funding_receipts),
                "book_pnl_usd": round(book_pnl, 4),
                "total_fee_usd": round(total_fee, 4),
                "total_rebate_usd": round(total_rebate, 4),
                "total_discount_saved_usd": 0.0,
                "net_fee_usd": round(max(0.0, total_fee - total_rebate), 4),
                "net_pnl_usd": round(net_pnl, 4),
                "rebate_status": "SETTLED",
                "is_partial_close": is_partial,
                "entry_leg": {
                    "role": ("MAKER" if en_maker else "TAKER") if en_known else "UNKNOWN",
                    "mode_code": en_mode if en_known else "UNKNOWN",
                    "fee_known": en_known,
                    "fee_bps": round((en_fee / en_notional) * 10_000, 2) if (en_known and en_notional > 0) else None,
                    "time_ms": open_ms,
                    "avg_price": round(en_px, 6),
                    "notional_usd": round(en_notional, 2),
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
                    "role": "MAKER" if ex_maker else "TAKER",
                    "mode_code": ex_mode,
                    "fee_known": ex_known,
                    "fee_bps": round((ex_fee / ex_notional) * 10_000, 2) if ex_known and ex_notional > 0 else None,
                    "time_ms": close_ms,
                    "avg_price": round(ex_px, 6),
                    "notional_usd": round(ex_notional, 2),
                    "gross_fee_usd": round(ex_fee, 4),
                    "discount_saved_usd": 0.0,
                    "discount_asset": "",
                    "rebate_usd": round(ex_reb, 4),
                    "rebate_pct": round(rebate_ratio * 100.0, 2),
                    "net_fee_usd": round(max(0.0, ex_fee - ex_reb), 4),
                    "rebate_status": "SETTLED",
                    "settled_at": settled_str,
                },
                "exit_batches": list(exit_batches or []),
            }
        )

    def _flush_bingx_lot(sym: str, trade_side: str, lot: dict[str, Any], *, full_close: bool) -> None:
        exits = lot.get("exits") or []
        if not exits:
            return
        closed_q = sum(float(x["qty"]) for x in exits)
        open_q = float(lot["qty"])
        ratio = 1.0 if (full_close or open_q <= 1e-12) else min(1.0, closed_q / open_q)
        open_ms = int(lot["open_ms"])
        close_ms = max(int(x["ts"]) for x in exits)
        en_notional = float(lot["notional"]) * ratio
        en_px = (float(lot["notional"]) / open_q) if open_q > 0 else float(exits[0]["px"])
        en_fee = float(lot["fee"]) * ratio
        ex_notional = sum(float(x["notional"]) for x in exits)
        ex_px = (ex_notional / closed_q) if closed_q > 0 else float(exits[-1]["px"])
        ex_fee = sum(float(x["fee"]) for x in exits)
        events = build_position_events(lot["event_fills"], side=trade_side, linear=True)
        for fill, event in zip(exits, (e for e in events if e["action"] in {"REDUCE", "CLOSE"})):
            if event["gross_pnl_usd"] is not None:
                fill["realized_pnl"] = event["gross_pnl_usd"]
        rpnl_sum = sum(float(x["realized_pnl"]) for x in exits)
        distinct_oids = {str(x["oid_raw"]) for x in exits if x.get("oid_raw")}
        distinct_ts = {int(x["ts"]) for x in exits}
        is_partial = (not full_close) or len(distinct_oids) > 1 or (len(exits) > 1 and len(distinct_ts) > 1)
        bx_batches: list[dict[str, Any]] = []
        bx_by_oid: dict[str, list[dict[str, Any]]] = {}
        for idx_e, x_e in enumerate(exits):
            ok_e = str(x_e.get("oid_raw") or "").strip() or f"ts_{x_e['ts']}_{idx_e}"
            bx_by_oid.setdefault(ok_e, []).append(x_e)
        if len(bx_by_oid) > 1 and (len(distinct_oids) > 1 or len(distinct_ts) > 1):
            ordered_bx = sorted(bx_by_oid.values(), key=lambda g_e: max(int(y["ts"]) for y in g_e))
            for b_i, g_e in enumerate(ordered_bx):
                bq = sum(float(y["qty"]) for y in g_e)
                bnot = sum(float(y["notional"]) for y in g_e)
                bpx = (bnot / bq) if bq > 0 else float(g_e[-1]["px"])
                bfee = sum(float(y["fee"]) for y in g_e)
                breb = round(sum(y["credit"] for y in g_e) + bfee * rebate_ratio, 4)
                bnetf = round(max(0.0, bfee - breb), 4)
                bgpnl = sum(float(y["realized_pnl"]) for y in g_e)
                bmk = any(bool(y["is_maker"]) for y in g_e)
                btk = any(not bool(y["is_maker"]) for y in g_e)
                bts = max(int(y["ts"]) for y in g_e)
                bx_batches.append(
                    {
                        "batch_index": b_i + 1,
                        "order_id": str(g_e[0].get("oid_raw") or ""),
                        "qty": round(bq, 8),
                        "role": "MAKER" if (bmk and not btk) else "TAKER",
                        "mode_code": "MIXED" if (bmk and btk) else ("LIMIT_MAKER" if bmk else "MARKET_TAKER"),
                        "fee_known": all(y["fee_known"] for y in g_e),
                        "fee_bps": round(bfee / bnot * 10_000, 2) if bnot > 0 else 0.0,
                        "time_ms": bts,
                        "time_str": _fmt_bj_ms(bts),
                        "avg_price": round(bpx, 6),
                        "notional_usd": round(bnot, 2),
                        "gross_fee_usd": round(bfee, 4),
                        "discount_saved_usd": 0.0,
                        "discount_asset": "",
                        "rebate_usd": breb,
                        "net_fee_usd": bnetf,
                        "gross_pnl_usd": round(bgpnl, 4),
                        "net_pnl_usd": round(bgpnl - bnetf, 4),
                    }
                )
        _emit_bingx_record(
            sym=sym,
            trade_side=trade_side,
            qty=closed_q,
            open_ms=open_ms,
            close_ms=close_ms,
            en_px=en_px,
            ex_px=ex_px,
            en_notional=en_notional,
            ex_notional=ex_notional,
            en_fee=en_fee,
            ex_fee=ex_fee,
            en_credit=lot["credit"] * ratio,
            ex_credit=sum(x["credit"] for x in exits),
            en_known=lot["fee_known"],
            ex_known=all(x["fee_known"] for x in exits),
            en_makers=bool(lot["maker_notional"] > 0),
            en_takers=bool(lot["taker_notional"] > 0),
            ex_makers=any(bool(x["is_maker"]) for x in exits),
            ex_takers=any(not bool(x["is_maker"]) for x in exits),
            realized_pnl=rpnl_sum,
            oid_id=str(exits[-1]["oid_id"]),
            is_partial=is_partial,
            exit_batches=bx_batches,
            journal_keys=[f"bingx_{x['oid_id']}_{x['ts']}" for x in exits],
            position_events=events,
        )

    for idx, f in enumerate(sorted_fills):
        sym = normalize_bingx_symbol(str(f.get("symbol") or ""))
        if not sym:
            continue
        ts = parse_bingx_ts_ms(f.get("filledTm") or f.get("time") or f.get("updateTime"))
        px = float(f.get("price") or f.get("avgPrice") or 0.0)
        qty = abs(float(f.get("qty") or f.get("executedQty") or f.get("volume") or 0.0))
        if px <= 0 or qty <= 0:
            continue
        charged = _bingx_fee_cost(f)
        fee = max(0.0, charged or 0.0)
        credit = max(0.0, -(charged or 0.0))
        native_gross = f.get("realizedPnl") if f.get("realizedPnl") not in (None, "") else f.get("profit")
        realized_pnl = float(native_gross or 0)
        side_raw = str(f.get("side") or "").upper()
        pos_side = str(f.get("positionSide") or "").upper()
        role_raw = str(f.get("role") or ("MAKER" if f.get("maker") else "TAKER")).upper()
        is_maker = "MAKER" in role_raw
        oid_raw = str(f.get("orderId") or "").strip()
        oid_id = str(f.get("orderId") or f.get("tradeId") or idx)

        if pos_side in {"LONG", "SHORT"}:
            trade_side = pos_side
            is_open = (pos_side == "LONG" and side_raw == "BUY") or (pos_side == "SHORT" and side_raw == "SELL")
        else:
            opposing_side = "SHORT" if side_raw == "BUY" else "LONG"
            is_open = (sym, opposing_side) not in open_lots and abs(realized_pnl) <= 1e-9
            trade_side = "LONG" if (side_raw == "BUY" if is_open else side_raw == "SELL") else "SHORT"

        lot_key = (sym, trade_side)
        if is_open and abs(realized_pnl) <= 1e-9:
            lot = open_lots.setdefault(
                lot_key,
                {
                    "qty": 0.0,
                    "closed_qty": 0.0,
                    "notional": 0.0,
                    "fee": 0.0,
                    "credit": 0.0,
                    "fee_known": True,
                    "open_ms": float(ts),
                    "maker_notional": 0.0,
                    "taker_notional": 0.0,
                    "event_fills": [],
                    "exits": [],
                },
            )
            if (lot["qty"] - lot["closed_qty"]) <= 1e-12 and not lot["exits"]:
                lot["open_ms"] = float(ts)
            lot["event_fills"].append(event_fill(ts, qty, px, True, charged, oid_raw))
            lot["qty"] += qty
            lot["notional"] += px * qty
            lot["fee"] += fee
            lot["credit"] += credit
            lot["fee_known"] = lot["fee_known"] and charged is not None
            if is_maker:
                lot["maker_notional"] += px * qty
            else:
                lot["taker_notional"] += px * qty
            continue

        lot = open_lots.get(lot_key)
        rem_open_q = (float(lot["qty"]) - float(lot["closed_qty"])) if lot else 0.0
        if lot and rem_open_q > 1e-12:
            event_gross = realized_pnl if native_gross not in (None, "") else None
            if qty <= rem_open_q + 1e-9:
                lot["event_fills"].append(event_fill(ts, qty, px, False, charged, oid_raw, gross=event_gross))
                lot["exits"].append(
                    {
                        "qty": qty,
                        "px": px,
                        "notional": px * qty,
                        "fee_known": charged is not None,
                        "credit": credit,
                        "fee": fee,
                        "realized_pnl": realized_pnl,
                        "is_maker": is_maker,
                        "ts": ts,
                        "oid_raw": oid_raw,
                        "oid_id": oid_id,
                    }
                )
                lot["closed_qty"] += qty
                if float(lot["qty"]) - float(lot["closed_qty"]) <= 1e-9:
                    _flush_bingx_lot(sym, trade_side, lot, full_close=True)
                    open_lots.pop(lot_key, None)
            else:
                close_sh = rem_open_q / qty
                open_sh = 1.0 - close_sh
                lot["event_fills"].append(event_fill(ts, rem_open_q, px, False,
                                                    charged * close_sh if charged is not None else None,
                                                    oid_raw, gross=event_gross))
                lot["exits"].append(
                    {
                        "qty": rem_open_q,
                        "px": px,
                        "notional": px * rem_open_q,
                        "fee_known": charged is not None,
                        "credit": credit * close_sh,
                        "fee": fee * close_sh,
                        "realized_pnl": realized_pnl,
                        "is_maker": is_maker,
                        "ts": ts,
                        "oid_raw": oid_raw,
                        "oid_id": oid_id,
                    }
                )
                lot["closed_qty"] = float(lot["qty"])
                _flush_bingx_lot(sym, trade_side, lot, full_close=True)
                open_lots.pop(lot_key, None)
                if pos_side not in {"LONG", "SHORT"}:
                    opp_side = "SHORT" if trade_side == "LONG" else "LONG"
                    rem_q = qty - rem_open_q
                    open_lots[(sym, opp_side)] = {
                        "qty": rem_q,
                        "closed_qty": 0.0,
                        "notional": px * rem_q,
                        "fee_known": charged is not None,
                        "credit": credit * open_sh,
                        "fee": fee * open_sh,
                        "open_ms": float(ts),
                        "maker_notional": (px * rem_q) if is_maker else 0.0,
                        "taker_notional": 0.0 if is_maker else (px * rem_q),
                        "event_fills": [event_fill(ts, rem_q, px, True,
                                                   charged * open_sh if charged is not None else None, oid_raw)],
                        "exits": [],
                    }
        else:
            en_px = (px - realized_pnl / qty) if trade_side == "LONG" else (px + realized_pnl / qty)
            if en_px <= 0:
                en_px = px
            _emit_bingx_record(
                sym=sym,
                trade_side=trade_side,
                qty=qty,
                open_ms=ts,
                close_ms=ts,
                en_px=en_px,
                ex_px=px,
                en_notional=en_px * qty,
                ex_notional=px * qty,
                en_fee=0.0,
                ex_credit=credit,
                ex_known=charged is not None,
                ex_fee=fee,
                en_known=False,
                en_makers=False,
                en_takers=False,
                ex_makers=is_maker,
                ex_takers=not is_maker,
                realized_pnl=realized_pnl,
                oid_id=oid_id,
                is_partial=False,
            )

    for (sym_rem, side_rem), lot_rem in list(open_lots.items()):
        if lot_rem.get("exits"):
            _flush_bingx_lot(sym_rem, side_rem, lot_rem, full_close=False)

    records.sort(key=lambda r: (r["close_time_ms"], r["open_time_ms"]), reverse=True)
    return records
