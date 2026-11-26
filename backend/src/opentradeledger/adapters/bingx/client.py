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
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx

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
        except Exception:
            res = await self.request_readonly("GET", "/openApi/swap/v2/user/balance")
        if isinstance(res, list):
            return [r for r in res if isinstance(r, dict)]
        if isinstance(res, dict):
            if isinstance(res.get("balance"), dict):
                return [res["balance"]]
            if isinstance(res.get("balance"), list):
                return [r for r in res["balance"] if isinstance(r, dict)]
            return [res]
        return []

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
        if isinstance(res, dict) and isinstance(res.get("balances"), list):
            return [r for r in res["balances"] if isinstance(r, dict)]
        return [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []

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
    fees_total = sum(abs(float(f.get("commission") or f.get("fee") or 0.0)) for f in fills if isinstance(f, dict))
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

    open_lots: dict[tuple[str, str], dict[str, float]] = {}
    records: list[dict[str, Any]] = []

    for idx, f in enumerate(sorted_fills):
        sym = normalize_bingx_symbol(str(f.get("symbol") or ""))
        if not sym:
            continue
        ts = parse_bingx_ts_ms(f.get("filledTm") or f.get("time") or f.get("updateTime"))
        px = float(f.get("price") or f.get("avgPrice") or 0.0)
        qty = abs(float(f.get("qty") or f.get("executedQty") or f.get("volume") or 0.0))
        if px <= 0 or qty <= 0:
            continue
        fee = abs(float(f.get("commission") or f.get("fee") or 0.0))
        realized_pnl = float(f.get("realizedPnl") or f.get("profit") or 0.0)
        side_raw = str(f.get("side") or "").upper()
        pos_side = str(f.get("positionSide") or "").upper()
        role_raw = str(f.get("role") or ("MAKER" if f.get("maker") else "TAKER")).upper()
        is_maker = "MAKER" in role_raw

        if pos_side in {"LONG", "SHORT"}:
            trade_side = pos_side
            is_open = (pos_side == "LONG" and side_raw == "BUY") or (pos_side == "SHORT" and side_raw == "SELL")
        else:
            is_open = abs(realized_pnl) <= 1e-9
            trade_side = "LONG" if (side_raw == "BUY" if is_open else side_raw == "SELL") else "SHORT"

        lot_key = (sym, trade_side)
        if is_open and abs(realized_pnl) <= 1e-9:
            lot = open_lots.setdefault(
                lot_key,
                {"qty": 0.0, "notional": 0.0, "fee": 0.0, "open_ms": float(ts), "maker_notional": 0.0},
            )
            if lot["qty"] <= 1e-12:
                lot["open_ms"] = float(ts)
            lot["qty"] += qty
            lot["notional"] += px * qty
            lot["fee"] += fee
            if is_maker:
                lot["maker_notional"] += px * qty
            continue

        lot = open_lots.get(lot_key)
        if lot and lot["qty"] > 1e-12:
            ratio = min(1.0, qty / lot["qty"])
            open_ms = int(lot["open_ms"])
            en_notional = lot["notional"] * ratio
            en_px = lot["notional"] / lot["qty"]
            en_fee = lot["fee"] * ratio
            en_maker = lot["maker_notional"] >= (lot["notional"] * 0.5)
            lot["qty"] = max(0.0, lot["qty"] - qty)
            lot["notional"] = max(0.0, lot["notional"] - en_notional)
            lot["fee"] = max(0.0, lot["fee"] - en_fee)
            lot["maker_notional"] = max(0.0, lot["maker_notional"] * (1.0 - ratio))
            en_known = True
        else:
            open_ms = ts
            en_px = (px - realized_pnl / qty) if trade_side == "LONG" else (px + realized_pnl / qty)
            if en_px <= 0:
                en_px = px
            en_notional = en_px * qty
            en_fee = 0.0
            en_maker = False
            en_known = False

        ex_notional = px * qty
        ex_fee = fee
        total_fee = round(en_fee + ex_fee, 6)
        en_reb = round(en_fee * rebate_ratio, 6)
        ex_reb = round(ex_fee * rebate_ratio, 6)
        total_rebate = round(en_reb + ex_reb, 6)

        funding_pnl = 0.0
        for fl in funding_by_sym.get(sym, []):
            if fl["_id"] in consumed_fund:
                continue
            if open_ms - 5_000 <= fl["time"] <= ts + 5_000:
                consumed_fund.add(fl["_id"])
                funding_pnl += float(fl["val"])

        book_pnl = round(realized_pnl - total_fee + funding_pnl, 6)
        net_pnl = round(book_pnl + total_rebate, 6)
        oid = str(f.get("orderId") or f.get("tradeId") or idx)
        settled_str = _fmt_bj_ms(ts) if total_rebate > 0 else ""

        records.append(
            {
                "trade_id": f"bingx_{oid}_{ts}",
                "venue_id": "bingx",
                "venue_name": "BingX",
                "venue_name_zh": "BingX",
                "symbol": sym,
                "side": trade_side,
                "qty": qty,
                "open_time_ms": open_ms,
                "close_time_ms": ts,
                "gross_pnl_usd": round(realized_pnl, 4),
                "funding_pnl_usd": round(funding_pnl, 4),
                "book_pnl_usd": round(book_pnl, 4),
                "total_fee_usd": round(total_fee, 4),
                "total_rebate_usd": round(total_rebate, 4),
                "total_discount_saved_usd": 0.0,
                "net_fee_usd": round(max(0.0, total_fee - total_rebate), 4),
                "net_pnl_usd": round(net_pnl, 4),
                "rebate_status": "SETTLED",
                "is_partial_close": bool(lot and lot["qty"] > 1e-9),
                "entry_leg": {
                    "role": ("MAKER" if en_maker else "TAKER") if en_known else "UNKNOWN",
                    "mode_code": ("LIMIT_MAKER" if en_maker else "MARKET_TAKER") if en_known else "UNKNOWN",
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
                    "role": "MAKER" if is_maker else "TAKER",
                    "mode_code": "LIMIT_MAKER" if is_maker else "MARKET_TAKER",
                    "fee_known": True,
                    "fee_bps": round((ex_fee / ex_notional) * 10_000, 2) if ex_notional > 0 else 0.0,
                    "time_ms": ts,
                    "avg_price": round(px, 6),
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
            }
        )
    records.sort(key=lambda r: (r["close_time_ms"], r["open_time_ms"]), reverse=True)
    return records
