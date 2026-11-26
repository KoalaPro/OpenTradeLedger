"""Built-in keyless public Copy-Trading slot & position radar (`CopyWatcherService`).

Security & Read-Only Guard:
- 100% keyless public queries; strictly forbids carrying any API key or authorization header.
- Enforces HTTP GET everywhere except the two explicitly whitelisted unauthenticated Binance
  public pagination endpoints (`order-history` and `position-history`).
- Zero personal defaults: fresh installations start with an empty target list (`[]`).
"""

from __future__ import annotations

import asyncio
import base64
from collections import Counter
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import logging
import random
import re
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

import httpx

from opentradeledger.config import NotificationSettings
from opentradeledger.core.notifier import bark_configured, dispatch_notification, parse_bark_input
from opentradeledger.security.readonly_guard import ReadOnlyGuardError

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

logger = logging.getLogger("opentradeledger.copy_watcher")

DEFAULT_BINANCE_WEB_BASE_URL = "https://www.binance.com"
DEFAULT_BINANCE_FAPI_BASE_URL = "https://fapi.binance.com"
BINANCE_COPY_DETAIL_PATH = "/bapi/futures/v1/friendly/future/copy-trade/lead-portfolio/detail"
BINANCE_ORDER_HISTORY_PATH = "/bapi/futures/v1/friendly/future/copy-trade/lead-portfolio/order-history"
BINANCE_POSITION_HISTORY_PATH = "/bapi/futures/v1/friendly/future/copy-trade/lead-portfolio/position-history"

_ALLOWED_PUBLIC_QUERY_POST_PATHS = frozenset(
    {
        BINANCE_ORDER_HISTORY_PATH,
        BINANCE_POSITION_HISTORY_PATH,
    }
)
_FORBIDDEN_AUTH_HEADERS = frozenset({"x-mbx-apikey", "authorization", "apikey"})
_PORTFOLIO_ID_RE = re.compile(r"(?<!\d)(\d{15,22})(?!\d)")
_BJ_TZ = timezone(timedelta(hours=8))

WATCHER_META_KEY = "copy_watcher_state_v1"

DEFAULT_HTTP_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


class CopyWatcherError(RuntimeError):
    """Raised when public copy-trading probing fails."""


@dataclass(frozen=True)
class PortfolioSlotSnapshot:
    portfolio_id: str
    nickname: str
    status: str
    portfolio_type: str
    current_copy_count: int
    max_copy_count: int
    min_copy_usd: float
    profit_sharing_rate: str

    @property
    def available_slots(self) -> int:
        return max(0, self.max_copy_count - self.current_copy_count)

    @property
    def has_open_slot(self) -> bool:
        return self.status == "ACTIVE" and self.max_copy_count > 0 and self.available_slots > 0

    @property
    def lead_url(self) -> str:
        return f"https://www.binance.com/zh-CN/copy-trading/lead-details/{self.portfolio_id}"

    @property
    def app_deeplink(self) -> str:
        encoded = base64.urlsafe_b64encode(self.lead_url.encode("utf-8")).decode("ascii").rstrip("=")
        return f"bnc://app.binance.com/webview/webview?type=default&url={encoded}"


@dataclass(frozen=True)
class LeadOrderRecord:
    order_time_ms: int
    time_bj: str
    symbol: str
    side: str
    position_side: str
    action_zh: str
    action_en: str
    order_type: str
    executed_qty: float
    avg_price: float
    realized_pnl: float
    order_id: str = ""


@dataclass(frozen=True)
class InferredOpenPosition:
    symbol: str
    position_side: str
    side_zh: str
    qty: float
    avg_entry_price: float
    notional_usd: float
    mark_price: float
    unrealized_pnl_usd: float
    unrealized_roi_pct: float
    opened_at_bj: str
    last_action_bj: str


@dataclass(frozen=True)
class LeadTradeActivity:
    inferred_positions: tuple[InferredOpenPosition, ...]
    recent_orders: tuple[LeadOrderRecord, ...]
    orders: tuple[LeadOrderRecord, ...]
    uncertain_symbols: tuple[str, ...]


def _bark_destination(settings: NotificationSettings) -> str:
    server, key = parse_bark_input(settings.bark_server_url, settings.bark_push_key)
    return hashlib.sha256(f"{server}/{key}".encode()).hexdigest() if key else ""


def _order_keys(orders: Sequence[LeadOrderRecord]) -> dict[str, LeadOrderRecord]:
    """Prefer upstream IDs. Without IDs only multiplicity distinguishes identical fills."""
    occurrences: Counter[str] = Counter()
    result = {}
    for order in orders:
        if order.order_id:
            key = f"{order.symbol}:{order.order_id}"
        else:
            fields = (
                order.order_time_ms,
                order.symbol,
                order.side,
                order.position_side,
                order.order_type,
                order.executed_qty,
                order.avg_price,
                order.realized_pnl,
            )
            digest = hashlib.sha256(json.dumps(fields).encode()).hexdigest()
            occurrences[digest] += 1
            key = f"{digest}:{occurrences[digest]}"
        result[key] = order
    return result


def _advance_cursor(
    cursor: Any, orders: Sequence[LeadOrderRecord], now_ms: int
) -> tuple[dict, list[LeadOrderRecord]]:
    batch = _order_keys(orders)
    if not isinstance(cursor, dict):
        # Older ledgers only know the timestamp. Treat its already visible ties as acknowledged.
        floor = int(cursor or max((o.order_time_ms for o in orders), default=now_ms))
        seen = {key: o.order_time_ms for key, o in batch.items() if o.order_time_ms == floor}
    else:
        floor, seen = cursor["floor_ms"], dict(cursor["seen"])
    fresh = []
    for key, order in batch.items():
        if order.order_time_ms >= floor and key not in seen:
            fresh.append(order)
            seen[key] = order.order_time_ms
    # Retain the overlap of the public 100-record feed, including all ties at its boundary.
    if len(seen) > 100:
        floor = max(floor, sorted(seen.values(), reverse=True)[99])
        seen = {k: ts for k, ts in seen.items() if ts >= floor}
    return {"floor_ms": floor, "seen": seen}, sorted(fresh, key=lambda o: o.order_time_ms)


def extract_portfolio_ids(raw: str) -> tuple[str, ...]:
    """Extract unique 15-22 digit Binance leadPortfolioIds preserving order from share text, URLs, or lists."""
    matches = _PORTFOLIO_ID_RE.findall(raw or "")
    seen: set[str] = set()
    ordered: list[str] = []
    for pid in matches:
        if pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    return tuple(ordered)


def _fmt_bj_ms(ts_ms: int) -> str:
    if ts_ms <= 0:
        return "-"
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=_BJ_TZ).strftime("%m-%d %H:%M:%S")


def _classify_order_action(side: str, position_side: str) -> tuple[str, str]:
    s = (side or "").upper()
    ps = (position_side or "").upper()
    if ps == "LONG":
        return ("开多", "Open Long") if s == "BUY" else ("平多", "Close Long")
    if ps == "SHORT":
        return ("开空", "Open Short") if s == "SELL" else ("平空", "Close Short")
    return ("买入", "Buy") if s == "BUY" else ("卖出", "Sell")


def _fmt_smart_price(val: float) -> str:
    if val >= 1000:
        return f"{val:,.2f}"
    if val >= 1:
        return f"{val:.3f}".rstrip("0").rstrip(".")
    return f"{val:.5f}".rstrip("0").rstrip(".")


class PublicCopyWatcherClient:
    """Strictly keyless read-only client for Binance public copy-trading endpoints."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        proxy_url: str = "",
        web_base_url: str = DEFAULT_BINANCE_WEB_BASE_URL,
        fapi_base_url: str = DEFAULT_BINANCE_FAPI_BASE_URL,
    ) -> None:
        self.web_base_url = web_base_url.rstrip("/")
        self.fapi_base_url = fapi_base_url.rstrip("/")
        self._owns_client = client is None
        clean_proxy = (proxy_url or "").strip() or None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.5),
            follow_redirects=True,
            headers=DEFAULT_HTTP_HEADERS,
            proxy=clean_proxy,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _assert_no_auth_headers(self) -> None:
        for hdr_name in self._client.headers.keys():
            if hdr_name.lower() in _FORBIDDEN_AUTH_HEADERS:
                raise ReadOnlyGuardError(
                    f"Authenticated header {hdr_name!r} is strictly forbidden on public copy-trade watcher"
                )

    async def _public_get(self, url: str, params: Mapping[str, Any] | None = None) -> httpx.Response:
        if not url.startswith("https://"):
            raise ReadOnlyGuardError(f"Public copy watcher requires https:// URL, got {url!r}")
        self._assert_no_auth_headers()
        try:
            return await self._client.request("GET", url, params=params)
        except httpx.HTTPError as exc:
            err_detail = f"{type(exc).__name__}: {exc}".rstrip(": ")
            raise CopyWatcherError(f"Network error probing Binance public page ({err_detail})") from exc

    async def _public_post(self, url: str, json_body: Mapping[str, Any]) -> httpx.Response:
        if not url.startswith("https://"):
            raise ReadOnlyGuardError(f"Public copy watcher requires https:// URL, got {url!r}")
        parsed = urlparse(url)
        if parsed.path not in _ALLOWED_PUBLIC_QUERY_POST_PATHS:
            raise ReadOnlyGuardError(f"Non-whitelisted POST path blocked by read-only guard: {parsed.path}")
        self._assert_no_auth_headers()
        try:
            return await self._client.request(
                "POST",
                url,
                json=dict(json_body),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            err_detail = f"{type(exc).__name__}: {exc}".rstrip(": ")
            raise CopyWatcherError(f"Network error querying Binance public history ({err_detail})") from exc

    async def fetch_slot_snapshot(self, portfolio_id: str) -> PortfolioSlotSnapshot:
        pid = (portfolio_id or "").strip()
        if not pid:
            raise CopyWatcherError("Portfolio ID must not be empty")
        url = f"{self.web_base_url}{BINANCE_COPY_DETAIL_PATH}"
        resp = await self._public_get(url, params={"portfolioId": pid})
        if resp.status_code != 200:
            raise CopyWatcherError(f"Binance lead detail returned HTTP {resp.status_code} for {pid}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise CopyWatcherError(f"Binance lead detail returned invalid JSON for {pid}") from exc
        if not isinstance(payload, Mapping) or str(payload.get("code") or "") != "000000":
            msg = payload.get("message") if isinstance(payload, Mapping) else ""
            raise CopyWatcherError(f"Invalid or private Binance portfolio ID {pid} ({msg or 'not found'})")
        info = payload.get("data")
        if not isinstance(info, Mapping) or not info.get("leadPortfolioId"):
            raise CopyWatcherError(f"Portfolio {pid} not found or closed on Binance Copy Trading")

        min_usd_raw = info.get("fixedAmountMinCopyUsd")
        if min_usd_raw is None:
            min_usd_raw = info.get("fixedRadioMinCopyUsd")

        return PortfolioSlotSnapshot(
            portfolio_id=str(info.get("leadPortfolioId") or pid).strip(),
            nickname=str(info.get("nickname") or pid).strip(),
            status=str(info.get("status") or "").strip().upper(),
            portfolio_type=str(info.get("portfolioType") or "PUBLIC").strip().upper(),
            current_copy_count=int(info.get("currentCopyCount") or 0),
            max_copy_count=int(info.get("maxCopyCount") or 0),
            min_copy_usd=float(min_usd_raw or 0.0),
            profit_sharing_rate=str(info.get("profitSharingRate") or ""),
        )

    async def _fetch_mark_price(self, symbol: str) -> float:
        try:
            resp = await self._public_get(
                f"{self.fapi_base_url}/fapi/v1/ticker/price",
                params={"symbol": symbol},
            )
            if resp.status_code == 200:
                body = resp.json()
                if isinstance(body, Mapping) and body.get("price") is not None:
                    return float(body["price"])
        except Exception:
            pass
        return 0.0

    async def _history_rows(self, path: str, query: dict[str, Any]) -> list:
        response = await self._public_post(f"{self.web_base_url}{path}", query)
        endpoint = path.rsplit("/", 1)[-1]
        if response.status_code != 200:
            raise CopyWatcherError(f"Binance {endpoint} HTTP {response.status_code} for {query['portfolioId']}")
        payload = response.json()
        data = payload.get("data") if isinstance(payload, Mapping) and payload.get("code") == "000000" else None
        if not isinstance(data, Mapping) or not isinstance(data.get("list"), list):
            raise CopyWatcherError(f"Binance {endpoint} unavailable for {query['portfolioId']}")
        return data["list"]

    async def fetch_trade_activity(
        self,
        portfolio_id: str,
        *,
        now_ms: int | None = None,
        recent_limit: int = 15,
    ) -> LeadTradeActivity:
        pid = (portfolio_id or "").strip()
        cur_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        start_ms = max(0, cur_ms - 14 * 86400 * 1000)

        pos_list = await self._history_rows(
            BINANCE_POSITION_HISTORY_PATH, {"portfolioId": pid, "pageNumber": 1, "pageSize": 30}
        )

        latest_all_closed_ms: dict[tuple[str, str], int] = {}
        latest_symbol_closed_ms: dict[str, int] = {}
        for row in pos_list:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("status") or "").strip().upper() != "ALL CLOSED":
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            side_up = str(row.get("side") or "").strip().upper()
            closed_ts = int(row.get("closed") or row.get("updateTime") or 0)
            if sym and closed_ts > 0:
                if side_up in ("LONG", "SHORT") and closed_ts > latest_all_closed_ms.get((sym, side_up), 0):
                    latest_all_closed_ms[(sym, side_up)] = closed_ts
                if side_up in ("LONG", "SHORT", "BOTH", "") and closed_ts > latest_symbol_closed_ms.get(sym, 0):
                    latest_symbol_closed_ms[sym] = closed_ts

        raw_orders = await self._history_rows(
            BINANCE_ORDER_HISTORY_PATH, {"portfolioId": pid, "startTime": start_ms, "endTime": cur_ms, "pageSize": 100}
        )

        parsed_orders: list[LeadOrderRecord] = []
        for o in raw_orders:
            if not isinstance(o, Mapping):
                continue
            qty = float(o.get("executedQty") or 0.0)
            price = float(o.get("avgPrice") or 0.0)
            if qty <= 0.0 or price <= 0.0:
                continue
            ts_ms = int(o.get("orderTime") or o.get("orderUpdateTime") or 0)
            sym = str(o.get("symbol") or "").strip().upper()
            side = str(o.get("side") or "").strip().upper()
            pside = str(o.get("positionSide") or "BOTH").strip().upper()
            otype = str(o.get("type") or "MARKET").strip().upper()
            pnl = float(o.get("totalPnl") or 0.0)
            act_zh, act_en = _classify_order_action(side, pside)
            parsed_orders.append(
                LeadOrderRecord(
                    order_time_ms=ts_ms,
                    time_bj=_fmt_bj_ms(ts_ms),
                    symbol=sym,
                    side=side,
                    position_side=pside,
                    action_zh=act_zh,
                    action_en=act_en,
                    order_type=otype,
                    executed_qty=qty,
                    avg_price=price,
                    realized_pnl=pnl,
                    order_id=str(o.get("orderId") or o.get("id") or ""),
                )
            )

        parsed_orders.sort(key=lambda r: r.order_time_ms, reverse=True)

        asc_orders = list(reversed(parsed_orders))
        ledgers: dict[tuple[str, str], list[float]] = {}
        both_ledgers: dict[str, list[float]] = {}
        for rec in asc_orders:
            if rec.position_side in ("LONG", "SHORT"):
                key = (rec.symbol, rec.position_side)
                if rec.order_time_ms <= latest_all_closed_ms.get(key, 0):
                    continue
                st = ledgers.setdefault(key, [0.0, 0.0, 0.0, 0.0])
                is_open_leg = (rec.position_side == "LONG" and rec.side == "BUY") or (
                    rec.position_side == "SHORT" and rec.side == "SELL"
                )
                if is_open_leg:
                    if st[0] <= 1e-9:
                        st[2] = float(rec.order_time_ms)
                    total_cost = st[0] * st[1] + rec.executed_qty * rec.avg_price
                    st[0] += rec.executed_qty
                    st[1] = total_cost / st[0]
                    st[3] = float(rec.order_time_ms)
                else:
                    st[0] = max(0.0, st[0] - rec.executed_qty)
                    st[3] = float(rec.order_time_ms)
                    if st[0] <= 1e-8:
                        st[0] = 0.0
                        st[1] = 0.0
                        st[2] = 0.0
            elif rec.position_side == "BOTH" and rec.side in ("BUY", "SELL"):
                base_closed_ms = latest_symbol_closed_ms.get(rec.symbol, 0)
                if base_closed_ms <= 0 or rec.order_time_ms <= base_closed_ms:
                    continue
                st_b = both_ledgers.setdefault(rec.symbol, [0.0, 0.0, 0.0, 0.0])
                delta = rec.executed_qty if rec.side == "BUY" else -rec.executed_qty
                cur_net = st_b[0]
                cur_avg = st_b[1]
                if abs(cur_net) <= 1e-9:
                    st_b[0] = delta
                    st_b[1] = rec.avg_price
                    st_b[2] = float(rec.order_time_ms)
                    st_b[3] = float(rec.order_time_ms)
                elif (cur_net > 0 and delta > 0) or (cur_net < 0 and delta < 0):
                    new_abs = abs(cur_net) + abs(delta)
                    st_b[1] = (abs(cur_net) * cur_avg + abs(delta) * rec.avg_price) / new_abs
                    st_b[0] = new_abs if cur_net > 0 else -new_abs
                    st_b[3] = float(rec.order_time_ms)
                else:
                    if abs(delta) < abs(cur_net) - 1e-8:
                        rem_abs = abs(cur_net) - abs(delta)
                        st_b[0] = rem_abs if cur_net > 0 else -rem_abs
                        st_b[3] = float(rec.order_time_ms)
                    elif abs(abs(delta) - abs(cur_net)) <= 1e-8:
                        st_b[0] = 0.0
                        st_b[1] = 0.0
                        st_b[2] = 0.0
                        st_b[3] = float(rec.order_time_ms)
                    else:
                        flip_abs = abs(delta) - abs(cur_net)
                        st_b[0] = flip_abs if delta > 0 else -flip_abs
                        st_b[1] = rec.avg_price
                        st_b[2] = float(rec.order_time_ms)
                        st_b[3] = float(rec.order_time_ms)

        for sym_b, (net_q, avg_c, open_ms_b, last_ms_b) in both_ledgers.items():
            if abs(net_q) <= 1e-8:
                continue
            eff_side = "LONG" if net_q > 0 else "SHORT"
            abs_q = abs(net_q)
            if (sym_b, eff_side) in ledgers and ledgers[(sym_b, eff_side)][0] > 1e-8:
                prev_q, prev_avg, prev_open, prev_last = ledgers[(sym_b, eff_side)]
                tot_q = prev_q + abs_q
                ledgers[(sym_b, eff_side)] = [
                    tot_q,
                    (prev_q * prev_avg + abs_q * avg_c) / tot_q,
                    min(prev_open, open_ms_b) if prev_open > 0 else open_ms_b,
                    max(prev_last, last_ms_b),
                ]
            else:
                ledgers[(sym_b, eff_side)] = [abs_q, avg_c, open_ms_b, last_ms_b]

        inferred: list[InferredOpenPosition] = []
        for (sym, pside), (qty, avg_cost, opened_ms, last_ms) in ledgers.items():
            notional = qty * avg_cost
            if qty <= 1e-8 or notional < 5.0:
                continue
            mark = await self._fetch_mark_price(sym)
            if mark > 0.0:
                upnl = (mark - avg_cost) * qty if pside == "LONG" else (avg_cost - mark) * qty
                uroi = (upnl / notional * 100.0) if notional > 0 else 0.0
            else:
                upnl = 0.0
                uroi = 0.0
            inferred.append(
                InferredOpenPosition(
                    symbol=sym,
                    position_side=pside,
                    side_zh="多" if pside == "LONG" else "空",
                    qty=round(qty, 6),
                    avg_entry_price=round(avg_cost, 6),
                    notional_usd=round(notional, 2),
                    mark_price=round(mark, 6),
                    unrealized_pnl_usd=round(upnl, 2),
                    unrealized_roi_pct=round(uroi, 2),
                    opened_at_bj=_fmt_bj_ms(int(opened_ms)),
                    last_action_bj=_fmt_bj_ms(int(last_ms)),
                )
            )

        inferred.sort(key=lambda p: p.notional_usd, reverse=True)
        uncertain_syms = {
            o.symbol
            for o in parsed_orders
            if o.position_side not in ("LONG", "SHORT", "BOTH")
            or (o.position_side == "BOTH" and latest_symbol_closed_ms.get(o.symbol, 0) <= 0)
        }
        return LeadTradeActivity(
            inferred_positions=tuple(inferred),
            recent_orders=tuple(parsed_orders[: max(1, recent_limit)]),
            orders=tuple(parsed_orders),
            uncertain_symbols=tuple(sorted(uncertain_syms)),
        )


def humanize_watcher_error(exc: Exception) -> tuple[str, str]:
    raw = str(exc or "Failed to query Binance public copy-trading endpoint")
    low = raw.lower()
    if any(k in low for k in ("network error", "timeout", "connect", "ssl", "eof")):
        return (
            f"Cannot reach Binance public copy-trading API ({raw}). If running on a NAS in mainland China, configure an HTTP proxy in Settings.",
            f"无法连接币安公开带单接口（{raw}）。若部署在国内 NAS，请在「设置与连接」底部填写局域网 HTTP 代理地址后重试。",
        )
    if "not found" in low or "invalid" in low:
        return (
            f"Lead trader portfolio not found or private: {raw}",
            f"未找到该币安公开带单组合（可能链接有误或交易员已关闭公开带单）：{raw}",
        )
    return raw, f"盯盘查询异常：{raw}"


def build_demo_copy_watcher_status(
    *,
    interval_sec: int = 10,
    trade_poll_sec: int = 30,
    cooldown_sec: int = 180,
    has_bark: bool = False,
) -> dict[str, Any]:
    """Sanitized demonstration radar payload for Demo Mode (zero personal IDs)."""
    now_ms = int(time.time() * 1000)
    demo_id_1 = "3900000000000000101"
    demo_id_2 = "3900000000000000202"
    return {
        "demo_mode": True,
        "watcher_running": True,
        "interval_sec": int(interval_sec),
        "trade_poll_sec": int(trade_poll_sec),
        "cooldown_sec": int(cooldown_sec),
        "updated_at_ms": now_ms,
        "last_error": "",
        "last_error_zh": "",
        "open_slot_count": 2,
        "channels": {"bark": has_bark},
        "configured_targets": [demo_id_1, demo_id_2],
        "items": [
            {
                "portfolio_id": demo_id_1,
                "venue": "binance",
                "nickname": "ApexTrend · 趋势量化 (演示)",
                "status": "ACTIVE",
                "portfolio_type": "PUBLIC",
                "enabled": True,
                "slot_alert": True,
                "trade_alert": True,
                "current_copy_count": 398,
                "max_copy_count": 400,
                "available_slots": 2,
                "has_open_slot": True,
                "min_copy_usd": 100.0,
                "profit_sharing_rate": "10",
                "lead_url": f"https://www.binance.com/zh-CN/copy-trading/lead-details/{demo_id_1}",
                "app_deeplink": "",
                "last_alert_ms": now_ms - 45_000,
                "inferred_positions": [
                    {
                        "symbol": "BTCUSDT",
                        "position_side": "LONG",
                        "side_zh": "多",
                        "qty": 1.25,
                        "avg_entry_price": 91420.0,
                        "notional_usd": 114275.0,
                        "mark_price": 92860.0,
                        "unrealized_pnl_usd": 1800.0,
                        "unrealized_roi_pct": 1.58,
                        "opened_at_bj": "11-24 20:15:08",
                        "last_action_bj": "11-24 22:40:12",
                    },
                    {
                        "symbol": "ETHUSDT",
                        "position_side": "SHORT",
                        "side_zh": "空",
                        "qty": 18.0,
                        "avg_entry_price": 3390.5,
                        "notional_usd": 61029.0,
                        "mark_price": 3348.0,
                        "unrealized_pnl_usd": 765.0,
                        "unrealized_roi_pct": 1.25,
                        "opened_at_bj": "11-24 21:05:44",
                        "last_action_bj": "11-24 21:05:44",
                    },
                ],
                "recent_orders": [
                    {
                        "order_time_ms": now_ms - 1800_000,
                        "time_bj": "11-24 22:40:12",
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "position_side": "LONG",
                        "action_zh": "开多",
                        "action_en": "Open Long",
                        "order_type": "LIMIT",
                        "executed_qty": 0.45,
                        "avg_price": 91680.0,
                        "realized_pnl": 0.0,
                    },
                    {
                        "order_time_ms": now_ms - 5400_000,
                        "time_bj": "11-24 21:10:30",
                        "symbol": "SOLUSDT",
                        "side": "SELL",
                        "position_side": "LONG",
                        "action_zh": "平多",
                        "action_en": "Close Long",
                        "order_type": "MARKET",
                        "executed_qty": 120.0,
                        "avg_price": 244.8,
                        "realized_pnl": 642.5,
                    },
                ],
            },
            {
                "portfolio_id": demo_id_2,
                "venue": "binance",
                "nickname": "SteadyAlpha · 稳健波段 (演示)",
                "status": "ACTIVE",
                "portfolio_type": "PUBLIC",
                "enabled": True,
                "slot_alert": True,
                "trade_alert": False,
                "current_copy_count": 500,
                "max_copy_count": 500,
                "available_slots": 0,
                "has_open_slot": False,
                "min_copy_usd": 200.0,
                "profit_sharing_rate": "15",
                "lead_url": f"https://www.binance.com/zh-CN/copy-trading/lead-details/{demo_id_2}",
                "app_deeplink": "",
                "last_alert_ms": 0,
                "inferred_positions": [],
                "recent_orders": [
                    {
                        "order_time_ms": now_ms - 7200_000,
                        "time_bj": "11-24 19:52:05",
                        "symbol": "ETHUSDT",
                        "side": "SELL",
                        "position_side": "LONG",
                        "action_zh": "平多",
                        "action_en": "Close Long",
                        "order_type": "LIMIT",
                        "executed_qty": 15.0,
                        "avg_price": 3385.0,
                        "realized_pnl": 418.2,
                    }
                ],
            },
        ],
    }


class CopyWatcherService:
    """In-process async background service monitoring public copy-trading slots & open positions."""

    def __init__(
        self,
        *,
        store: "LedgerStore",
        interval_sec: float = 10.0,
        cooldown_sec: float = 180.0,
        trade_poll_every_n_cycles: int = 3,
        http_client: httpx.AsyncClient | None = None,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.interval_sec = max(3.0, float(interval_sec))
        self.cooldown_sec = max(0.0, float(cooldown_sec))
        self.trade_poll_every_n_cycles = max(1, int(trade_poll_every_n_cycles))
        self._injected_client = http_client
        self._clock_fn = clock_fn

        self._lock = asyncio.Lock()
        self._stop_event: asyncio.Event | None = None
        self._wake_event: asyncio.Event | None = None
        self._bg_task: asyncio.Task[None] | None = None
        self._cycles = 0
        self._consecutive_failures = 0
        self._last_trade_poll_mono: dict[str, float] = {}
        self._last_error: str = ""
        self._last_error_zh: str = ""
        self._updated_at_ms: int = 0

        self._bootstrap_legacy_targets()

    def _effective_timings(self) -> tuple[float, float, float]:
        prefs = self.store.get_preferences()
        slot_s = float(max(3, min(300, int(prefs.watcher_interval_sec or self.interval_sec))))
        trade_s = float(max(10, min(600, int(prefs.watcher_trade_interval_sec or (self.interval_sec * self.trade_poll_every_n_cycles)))))
        cool_s = float(max(30, min(3600, int(prefs.watcher_cooldown_sec or self.cooldown_sec))))
        return slot_s, trade_s, cool_s

    def notify_config_changed(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()

    def _load_state(self) -> dict[str, Any]:
        raw = self.store.get_meta(WATCHER_META_KEY, {})
        if not isinstance(raw, dict):
            raw = {}
        targets = raw.get("targets") if isinstance(raw.get("targets"), list) else []
        snapshots = raw.get("snapshots") if isinstance(raw.get("snapshots"), dict) else {}
        watermarks = raw.get("watermarks") if isinstance(raw.get("watermarks"), dict) else {}
        return {"targets": targets, "snapshots": snapshots, "watermarks": watermarks}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.store.set_meta(WATCHER_META_KEY, state)

    def _bootstrap_legacy_targets(self) -> None:
        state = self._load_state()
        if state["targets"]:
            return
        prefs = self.store.get_preferences()
        legacy_ids = extract_portfolio_ids(",".join(prefs.copy_watcher_targets or []))
        if not legacy_ids:
            return
        now_ms = int(time.time() * 1000)
        state["targets"] = [
            {
                "portfolio_id": pid,
                "venue": "binance",
                "nickname": pid,
                "enabled": True,
                "slot_alert": True,
                "trade_alert": True,
                "added_at_ms": now_ms,
            }
            for pid in legacy_ids
        ]
        self._save_state(state)

    def _sync_prefs_target_ids(self, targets: list[dict[str, Any]]) -> None:
        prefs = self.store.get_preferences()
        ids = [str(t.get("portfolio_id") or "") for t in targets if t.get("portfolio_id")]
        if prefs.copy_watcher_targets != ids:
            prefs.copy_watcher_targets = ids
            self.store.save_preferences(prefs)

    def _make_client(self) -> PublicCopyWatcherClient:
        prefs = self.store.get_preferences()
        return PublicCopyWatcherClient(
            client=self._injected_client,
            proxy_url=prefs.outbound_proxy_url,
        )

    async def start(self) -> None:
        if self._bg_task and not self._bg_task.done():
            return
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._bg_task = asyncio.create_task(self._watch_loop(), name="otl-copy-watcher")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._wake_event is not None:
            self._wake_event.set()
        if self._bg_task is not None:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
            self._bg_task = None

    async def _watch_loop(self) -> None:
        assert self._stop_event is not None
        assert self._wake_event is not None
        while not self._stop_event.is_set():
            self._wake_event.clear()
            slot_s, _trade_s, _cool_s = self._effective_timings()
            delay = slot_s + random.uniform(-0.5, 0.5)
            try:
                state = self._load_state()
                has_enabled = any(bool(t.get("enabled", True)) for t in state["targets"])
                saved_demo = self.store.get_meta("demo_mode_override", None)
                if has_enabled and saved_demo is not True:
                    await self.poll_once(force_trades=False)
                    slot_s, _trade_s, _cool_s = self._effective_timings()
                    delay = slot_s + random.uniform(-0.5, 0.5)
                    if self._consecutive_failures > 0:
                        exp = min(5, self._consecutive_failures - 1)
                        delay = min(60.0, slot_s * (2**exp))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Copy watcher background tick error: %s", exc)
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=max(3.0, delay))
            except asyncio.TimeoutError:
                continue

    def get_status(self, *, demo_mode: bool = False) -> dict[str, Any]:
        slot_s, trade_s, cool_s = self._effective_timings()
        destination = _bark_destination(self.store.get_preferences().notifications)
        has_bark = bool(destination)
        if demo_mode:
            return build_demo_copy_watcher_status(
                has_bark=has_bark,
                interval_sec=int(round(slot_s)),
                trade_poll_sec=int(round(trade_s)),
                cooldown_sec=int(round(cool_s)),
            )

        state = self._load_state()
        targets = state["targets"]
        snapshots = state["snapshots"]

        items: list[dict[str, Any]] = []
        open_slot_total = 0
        for t in targets:
            pid = str(t.get("portfolio_id") or "").strip()
            if not pid:
                continue
            cached = snapshots.get(pid) if isinstance(snapshots.get(pid), dict) else {}
            enabled = bool(t.get("enabled", True))
            has_open = bool(cached.get("has_open_slot", False)) and enabled and not cached.get("probe_error")
            avail = int(cached.get("available_slots") or 0) if enabled else 0
            if has_open:
                open_slot_total += avail
            lead_url = cached.get("lead_url") or f"https://www.binance.com/zh-CN/copy-trading/lead-details/{pid}"
            items.append(
                {
                    "portfolio_id": pid,
                    "venue": str(t.get("venue") or "binance"),
                    "nickname": str(cached.get("nickname") or t.get("nickname") or pid),
                    "status": str(cached.get("status") or ("PAUSED" if not enabled else "PENDING")),
                    "portfolio_type": str(cached.get("portfolio_type") or "PUBLIC"),
                    "enabled": enabled,
                    "slot_alert": has_bark and bool(t.get("slot_alert", True)),
                    "trade_alert": has_bark and bool(t.get("trade_alert", True)),
                    "current_copy_count": int(cached.get("current_copy_count") or 0),
                    "max_copy_count": int(cached.get("max_copy_count") or 0),
                    "available_slots": int(cached.get("available_slots") or 0),
                    "has_open_slot": bool(has_open),
                    "min_copy_usd": float(cached.get("min_copy_usd") or 0.0),
                    "profit_sharing_rate": str(cached.get("profit_sharing_rate") or ""),
                    "lead_url": lead_url,
                    "app_deeplink": str(cached.get("app_deeplink") or ""),
                    "last_alert_ms": int(cached.get("last_alert_ms") or 0),
                    "slot_updated_at_ms": int(cached.get("slot_updated_at_ms") or 0),
                    "trade_updated_at_ms": int(cached.get("trade_updated_at_ms") or 0),
                    "trade_stale": bool(cached.get("trade_stale", not cached.get("trade_updated_at_ms"))),
                    "uncertain_symbols": cached.get("uncertain_symbols", []),
                    "pending_alert_count": (int(bool(cached.get("slot_pending"))) + len(cached.get("pending_trades", [])))
                                           if destination and destination == cached.get("pending_destination") else 0,
                    "inferred_positions": list(cached.get("inferred_positions") or []),
                    "recent_orders": list(cached.get("recent_orders") or []),
                    "probe_error": str(cached.get("probe_error") or ""),
                    "probe_error_zh": str(cached.get("probe_error_zh") or ""),
                }
            )

        return {
            "demo_mode": False,
            "watcher_running": bool(self._bg_task and not self._bg_task.done()),
            "interval_sec": int(round(slot_s)),
            "trade_poll_sec": int(round(trade_s)),
            "cooldown_sec": int(round(cool_s)),
            "updated_at_ms": self._updated_at_ms,
            "last_error": self._last_error,
            "last_error_zh": self._last_error_zh,
            "open_slot_count": open_slot_total,
            "channels": {"bark": has_bark},
            "configured_targets": [str(t.get("portfolio_id")) for t in targets if t.get("portfolio_id")],
            "items": items,
        }

    async def add_targets(
        self,
        raw_input: str,
        *,
        slot_alert: bool = True,
        trade_alert: bool = True,
    ) -> list[dict[str, Any]]:
        pids = extract_portfolio_ids(raw_input)
        if not pids:
            raise ValueError(
                "未识别到有效的带单员 ID。请粘贴币安带单员分享链接（含 lead-details/数字）或 15～22 位数字组合 ID。"
            )

        # Probes happen outside the polling lock. A later refresh or removal must win.
        initial = self._load_state()
        revisions = {
            row["portfolio_id"]: (
                row.get("added_at_ms"),
                initial["snapshots"].get(row["portfolio_id"], {}).get("revision", 0),
            )
            for row in initial["targets"]
        }
        watcher_client = self._make_client()
        try:
            probed: list[tuple[PortfolioSlotSnapshot, LeadTradeActivity | None]] = []
            for pid in pids:
                snap = await watcher_client.fetch_slot_snapshot(pid)
                act: LeadTradeActivity | None = None
                try:
                    act = await watcher_client.fetch_trade_activity(pid)
                except Exception as exc:
                    logger.debug("Initial trade activity fetch skipped for %s: %s", pid, exc)
                probed.append((snap, act))
        finally:
            await watcher_client.aclose()

        async with self._lock:
            state = self._load_state()
            targets: list[dict[str, Any]] = list(state["targets"])
            snapshots: dict[str, Any] = dict(state["snapshots"])
            watermarks: dict[str, Any] = dict(state["watermarks"])
            now_ms = int(time.time() * 1000)

            has_bark = bark_configured(self.store.get_preferences().notifications)
            existing_by_id = {str(t.get("portfolio_id")): t for t in targets}
            added_rows: list[dict[str, Any]] = []
            for snap, act in probed:
                pid = snap.portfolio_id
                previous = snapshots.get(pid, {})
                existing = existing_by_id.get(pid)
                observed = (existing.get('added_at_ms'), previous.get('revision', 0)) if existing else None
                if observed != revisions.get(pid):
                    if existing:
                        added_rows.append(previous)
                    continue
                if pid not in watermarks:
                    if act is None:
                        watermarks[pid] = {"floor_ms": now_ms, "seen": {}}
                    else:
                        watermarks[pid], _ = _advance_cursor(None, act.orders, now_ms)

                if pid in existing_by_id:
                    existing_by_id[pid]["nickname"] = snap.nickname
                    existing_by_id[pid].setdefault("enabled", True)
                    existing_by_id[pid].setdefault("slot_alert", has_bark and bool(slot_alert))
                    existing_by_id[pid].setdefault("trade_alert", has_bark and bool(trade_alert))
                else:
                    row = {
                        "portfolio_id": pid,
                        "venue": "binance",
                        "nickname": snap.nickname,
                        "enabled": True,
                        "slot_alert": has_bark and slot_alert,
                        "trade_alert": has_bark and trade_alert,
                        "added_at_ms": now_ms,
                    }
                    targets.append(row)
                    existing_by_id[pid] = row

                snap_dict = self._snapshot(snap, act, snapshots.get(pid, {}), now_ms, history_failed=act is None)
                snapshots[pid] = snap_dict
                added_rows.append(snap_dict)

            state["targets"] = targets
            state["snapshots"] = snapshots
            state["watermarks"] = watermarks
            self._save_state(state)
            self._sync_prefs_target_ids(targets)
            self._updated_at_ms = now_ms
            self._last_error = ""
            self._last_error_zh = ""
            return added_rows

    def set_target_ids_bulk(self, raw_targets: Sequence[str]) -> list[str]:
        pids = extract_portfolio_ids(",".join(str(x) for x in raw_targets))
        state = self._load_state()
        existing = {str(t.get("portfolio_id")): t for t in state["targets"]}
        now_ms = int(time.time() * 1000)
        has_bark = bark_configured(self.store.get_preferences().notifications)
        new_targets: list[dict[str, Any]] = []
        for pid in pids:
            if pid in existing:
                new_targets.append(existing[pid])
            else:
                new_targets.append(
                    {
                        "portfolio_id": pid,
                        "venue": "binance",
                        "nickname": pid,
                        "enabled": True,
                        "slot_alert": has_bark,
                        "trade_alert": has_bark,
                        "added_at_ms": now_ms,
                    }
                )
        state["targets"] = new_targets
        # Prune removed snapshots
        keep_set = set(pids)
        state["snapshots"] = {k: v for k, v in state["snapshots"].items() if k in keep_set}
        state["watermarks"] = {k: v for k, v in state["watermarks"].items() if k in keep_set}
        self._save_state(state)
        self._sync_prefs_target_ids(new_targets)
        return list(pids)

    def update_target(
        self,
        portfolio_id: str,
        *,
        enabled: bool | None = None,
        slot_alert: bool | None = None,
        trade_alert: bool | None = None,
    ) -> dict[str, Any]:
        pid = (portfolio_id or "").strip()
        state = self._load_state()
        found = None
        for t in state["targets"]:
            if str(t.get("portfolio_id")) == pid:
                if (slot_alert or trade_alert) and not bark_configured(self.store.get_preferences().notifications):
                    raise ValueError("请先配置 Bark，再开启手机提醒 / Configure Bark to enable mobile alerts")
                previous = state["snapshots"].get(pid, {})
                if enabled is False or trade_alert is False:
                    previous.pop("pending_trades", None)
                if enabled is False or slot_alert is False:
                    previous.pop("slot_pending", None)
                if (enabled is True and not t.get("enabled", True)) or (trade_alert is True and not t.get("trade_alert", True)):
                    state["watermarks"][pid] = {"floor_ms": int(time.time() * 1000), "seen": {}}
                if enabled is not None:
                    t["enabled"] = bool(enabled)
                if slot_alert is not None:
                    t["slot_alert"] = bool(slot_alert)
                if trade_alert is not None:
                    t["trade_alert"] = bool(trade_alert)
                found = t
                break
        if found is None:
            raise KeyError(f"Watched portfolio {pid} not found")
        self._save_state(state)
        return dict(found)

    def remove_target(self, portfolio_id: str) -> bool:
        pid = (portfolio_id or "").strip()
        state = self._load_state()
        before = len(state["targets"])
        state["targets"] = [t for t in state["targets"] if str(t.get("portfolio_id")) != pid]
        if len(state["targets"]) == before:
            return False
        state["snapshots"].pop(pid, None)
        state["watermarks"].pop(pid, None)
        self._last_trade_poll_mono.pop(pid, None)
        self._save_state(state)
        self._sync_prefs_target_ids(state["targets"])
        return True

    @staticmethod
    def _snapshot(
        snap: PortfolioSlotSnapshot,
        act: LeadTradeActivity | None,
        previous: dict,
        wall_ms: int,
        *,
        history_failed: bool = False,
    ) -> dict:
        row = {
            **previous,
            **asdict(snap),
            "available_slots": snap.available_slots,
            "has_open_slot": snap.has_open_slot,
            "lead_url": snap.lead_url,
            "app_deeplink": snap.app_deeplink,
            "slot_updated_at_ms": wall_ms,
            "probe_error": "",
            "probe_error_zh": "",
            "revision": previous.get("revision", 0) + 1,
        }
        row.setdefault("last_alert_ms", 0)
        row.setdefault("inferred_positions", [])
        row.setdefault("recent_orders", [])
        row.setdefault("trade_updated_at_ms", 0)
        if not snap.has_open_slot:
            row.update(slot_reset=True, slot_pending=False, slot_blocked_until_ms=0)
        if act is not None:
            row.update(
                inferred_positions=[asdict(p) for p in act.inferred_positions],
                recent_orders=[asdict(o) for o in act.recent_orders],
                uncertain_symbols=list(act.uncertain_symbols),
                trade_updated_at_ms=wall_ms,
                trade_stale=False,
            )
        elif history_failed:
            row["trade_stale"] = True
        return row

    @staticmethod
    def _trade_messages(
        snap: PortfolioSlotSnapshot, act: LeadTradeActivity, fresh: Sequence[LeadOrderRecord]
    ) -> list[dict]:
        groups: dict[tuple[str, str, str], list[LeadOrderRecord]] = {}
        for order in fresh:
            groups.setdefault((order.symbol, order.position_side, order.action_zh), []).append(order)
        positions = {(p.symbol, p.position_side): p for p in act.inferred_positions}
        messages = []
        for (symbol, side, action), legs in groups.items():
            qty = sum(o.executed_qty for o in legs)
            avg = sum(o.executed_qty * o.avg_price for o in legs) / qty
            pnl = sum(o.realized_pnl for o in legs)
            parts = [
                f"({len(legs)}笔合并) " if len(legs) > 1 else "",
                f"数量 {qty:g} 均价 {_fmt_smart_price(avg)}",
            ]
            if abs(pnl) >= 0.5:
                parts.append(f"｜已实现 {pnl:+.0f}U")
            position = positions.get((symbol, side))
            if position:
                parts.append(
                    f"｜在途{position.side_zh} {position.qty:g} @ {_fmt_smart_price(position.avg_entry_price)}"
                )
            elif side in ("LONG", "SHORT"):
                parts.append(f"｜未推算到在途{'多' if side == 'LONG' else '空'}仓")
            parts.append(f" ({legs[-1].time_bj})")
            messages.append(
                {
                    "title": f"📊 {snap.nickname} {symbol} {action}",
                    "body": "".join(parts),
                    "url": snap.app_deeplink,
                    "bark_params": {"sound": "bell", "level": "timeSensitive", "group": "binance-copy-trade"},
                }
            )
        return messages

    @staticmethod
    def _target(state: dict, original: dict) -> dict | None:
        return next(
            (
                row
                for row in state["targets"]
                if row["portfolio_id"] == original["portfolio_id"]
                and row.get("added_at_ms") == original.get("added_at_ms")
            ),
            None,
        )

    async def _send_pending(self, original: dict, message: dict, *, slot: bool = False) -> None:
        pid = original["portfolio_id"]
        state = self._load_state()
        target = self._target(state, original)
        setting = "slot_alert" if slot else "trade_alert"
        if not target or not target.get("enabled", True) or not target.get(setting, True):
            return
        row = state["snapshots"][pid]
        if not (row.get("slot_pending") if slot else message in row.get("pending_trades", [])):
            return
        notifications = self.store.get_preferences().notifications
        if row.get("pending_destination") != _bark_destination(notifications):
            return
        result = await dispatch_notification(notifications, **message, client=self._injected_client)
        if not result["ok"]:
            return
        # A UI action may have removed or replaced the target during the HTTP request.
        state = self._load_state()
        if self._target(state, original) is None:
            return
        row = state["snapshots"][pid]
        if slot:
            row.update(slot_pending=False, slot_reset=False, last_alert_ms=int(time.time() * 1000))
        elif message in row.get("pending_trades", []):
            row["pending_trades"].remove(message)
        self._save_state(state)

    @staticmethod
    def _bind_pending(row: dict, destination: str, wall_ms: int, cooldown_sec: float) -> None:
        if row.get("pending_destination") == destination:
            return
        # A pending notification belongs to the device selected when the event was observed.
        if row.get("slot_pending"):
            row["slot_blocked_until_ms"] = wall_ms + int(cooldown_sec * 1000)
        row.update(pending_destination=destination, slot_pending=False, pending_trades=[])

    async def _poll_target(
        self,
        target: dict,
        client: PublicCopyWatcherClient,
        force_trades: bool,
        trade_sec: float,
        cooldown_sec: float,
    ) -> tuple[str, str] | None:
        pid = target["portfolio_id"]
        now = self._clock_fn()
        wall_ms = int(time.time() * 1000)
        try:
            snap = await client.fetch_slot_snapshot(pid)
        except Exception as exc:
            error = humanize_watcher_error(exc)
            state = self._load_state()
            if self._target(state, target) is not None:
                row = state["snapshots"].setdefault(pid, {})
                row.update(
                    probe_error=error[0],
                    probe_error_zh=error[1],
                    trade_stale=True,
                    revision=row.get("revision", 0) + 1,
                )
                self._bind_pending(
                    row, _bark_destination(self.store.get_preferences().notifications), wall_ms, cooldown_sec
                )
                self._save_state(state)
                for msg in list(row.get("pending_trades", [])):
                    await self._send_pending(target, msg)
            return error

        current = self._target(self._load_state(), target)
        if current is None or not current.get("enabled", True):
            return None
        last_trade = self._last_trade_poll_mono.get(pid)
        due = force_trades or last_trade is None or now - last_trade >= trade_sec
        act = None
        if due:
            self._last_trade_poll_mono[pid] = now
            try:
                act = await client.fetch_trade_activity(pid, now_ms=wall_ms)
            except Exception as exc:
                logger.debug("Trade activity fetch failed for %s: %s", pid, exc)

        state = self._load_state()
        current = self._target(state, target)
        if current is None:
            return None
        enabled = current.get("enabled", True)
        destination = _bark_destination(self.store.get_preferences().notifications)
        has_bark = bool(destination)
        row = self._snapshot(
            snap, act, state["snapshots"].get(pid, {}), wall_ms, history_failed=due and act is None
        )
        self._bind_pending(row, destination, wall_ms, cooldown_sec)
        if act is not None:
            cursor, fresh = _advance_cursor(state["watermarks"].get(pid), act.orders, wall_ms)
            state["watermarks"][pid] = cursor
            if enabled and has_bark and current.get("trade_alert", True):
                row.setdefault("pending_trades", []).extend(self._trade_messages(snap, act, fresh))
        if not (enabled and has_bark and current.get("trade_alert", True)):
            row.pop("pending_trades", None)
        slot_due = (
            row.get("slot_reset")
            or not row["last_alert_ms"]
            or wall_ms - row["last_alert_ms"] >= cooldown_sec * 1000
        )
        row["slot_pending"] = bool(
            enabled
            and has_bark
            and current.get("slot_alert", True)
            and snap.has_open_slot
            and slot_due
            and wall_ms >= row.get("slot_blocked_until_ms", 0)
        )
        state["snapshots"][pid] = row
        # Save the observation, cursor and pending notifications together before contacting Bark.
        self._save_state(state)
        if row["slot_pending"]:
            await self._send_pending(
                target,
                {
                    "title": f"🚨 {snap.nickname} 空出 {snap.available_slots} 个跟单名额！",
                    "body": f"当前 {snap.current_copy_count}/{snap.max_copy_count} (起跟 {snap.min_copy_usd:.0f} USDT)，点此通知直跳币安App秒抢",
                    "url": snap.app_deeplink,
                    "bark_params": {
                        "call": "1",
                        "sound": "alarm",
                        "level": "timeSensitive",
                        "group": "binance-copy-slot",
                    },
                },
                slot=True,
            )
        for message in list(row.get("pending_trades", [])):
            await self._send_pending(target, message)
        return None

    async def poll_once(self, *, force_trades: bool = False) -> dict[str, Any]:
        async with self._lock:
            targets = [t for t in self._load_state()["targets"] if t.get("enabled", True)]
            if not targets:
                return self.get_status(demo_mode=False)
            self._cycles += 1
            _, trade_sec, cooldown_sec = self._effective_timings()
            client = self._make_client()
            # Slow public endpoints or a Bark timeout for one trader must leave other probes running.
            limit = asyncio.Semaphore(4)

            async def check(target: dict) -> tuple[str, str] | None:
                async with limit:
                    current = self._target(self._load_state(), target)
                    if current is None or not current.get("enabled", True):
                        return None
                    return await self._poll_target(target, client, force_trades, trade_sec, cooldown_sec)

            try:
                results = await asyncio.gather(*(check(t) for t in targets))
                errors = [error for error in results if error]
            finally:
                await client.aclose()
            self._consecutive_failures = self._consecutive_failures + 1 if len(errors) == len(targets) else 0
            self._last_error, self._last_error_zh = errors[-1] if errors else ("", "")
            self._updated_at_ms = int(time.time() * 1000)
            return self.get_status(demo_mode=False)

    async def send_test_ring(self, portfolio_id: str = "") -> dict[str, Any]:
        prefs = self.store.get_preferences()
        n = prefs.notifications
        state = self._load_state()
        pid = (portfolio_id or "").strip()
        if not pid and state["targets"]:
            pid = str(state["targets"][0].get("portfolio_id") or "")

        cached = state["snapshots"].get(pid) if pid and isinstance(state["snapshots"].get(pid), dict) else None
        if cached:
            nick = str(cached.get("nickname") or pid)
            cur_c = int(cached.get("current_copy_count") or 0)
            max_c = int(cached.get("max_copy_count") or 0)
            avail = int(cached.get("available_slots") or 0)
            min_u = float(cached.get("min_copy_usd") or 0.0)
            deeplink = str(cached.get("app_deeplink") or "")
            lead_url = str(cached.get("lead_url") or "")
            title = f"[测试] 盯盘已就绪：{nick}"
            body = f"当前 {cur_c}/{max_c} (空位 {avail}｜起跟 {min_u:.0f}U)，点此通知可直跳币安App跟单页"
            return await dispatch_notification(
                n,
                title=title,
                body=body,
                url=deeplink or lead_url,
                bark_params={
                    "sound": "bell",
                    "level": "timeSensitive",
                    "group": "binance-copy-slot",
                    **({"url": deeplink} if deeplink else {}),
                },
                client=self._injected_client,
            )

        return await dispatch_notification(
            n,
            title="OpenTradeLedger · 推送通知已就绪",
            body="测试验证成功！空余跟单名额、带单员调仓、新平仓入账及交易所同步异常将实时推送至此设备。",
            bark_params={"sound": "bell", "level": "timeSensitive"},
            client=self._injected_client,
        )
