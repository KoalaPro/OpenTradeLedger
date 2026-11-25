"""Strictly GET-only OKX API v5 signed client and actuarial per-leg trade normalizer.

Signature specification (OKX v5):
  message = timestamp_iso8601 + "GET" + request_path_with_query
  OK-ACCESS-SIGN = Base64(HMAC-SHA256(message, api_secret))
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx

from opentradeledger.adapters.okx.contracts import (
    amount_usd,
    build_fill_leg,
    contract_quantity,
    finite_number,
    instrument_at,
    positive_number,
)
from opentradeledger.security.readonly_guard import assert_readonly_request

TZ_UTC8 = timezone(timedelta(hours=8))

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
    "/api/v5/asset/bills",
    "/api/v5/asset/deposit-history",
    "/api/v5/asset/withdrawal-history",
    "/api/v5/copytrading/current-subpositions",
    "/api/v5/copytrading/subpositions-history",
)


class OkxApiError(RuntimeError):
    """Raised when OKX v5 returns a non-zero business error code or HTTP error."""


def _default_iso_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _fmt_bj_ms(ms: int) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ_UTC8).strftime("%m-%d %H:%M")


def sign_okx_get(timestamp: str, request_path: str, api_secret: str) -> str:
    message = f"{timestamp}GET{request_path}"
    digest = hmac.new(api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class OkxReadOnlyClient:
    """Strictly GET-only signed REST client for OKX v5 Unified Account with multi-page cursor pagination."""

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
        page_limit: int = 100,
        max_pages: int = 10,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout_sec
        self._clock_iso = clock_iso or _default_iso_timestamp
        self.page_limit = max(1, min(100, int(page_limit)))
        self.max_pages = max(1, int(max_pages))

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
        headers = {} if path.startswith("/api/v5/public/") else self.build_headers(request_path)
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

    async def _paginate_after(
        self,
        path: str,
        base_params: dict[str, Any],
        *,
        cursor_fields: tuple[str, ...],
        id_fn: Callable[[dict[str, Any]], str],
        ts_field: str = "",
        since_ms: int = 0,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        eff_limit = limit or self.page_limit
        collected: dict[str, dict[str, Any]] = {}
        after_cursor: Optional[str] = None
        seen_cursors: set[str] = set()

        for _ in range(self.max_pages):
            params = {**base_params, "limit": eff_limit}
            if after_cursor:
                params["after"] = after_cursor
            batch = await self.request_readonly("GET", path, params)
            if not batch:
                break

            new_in_batch = 0
            oldest_ts = 0
            for row in batch:
                if not isinstance(row, dict):
                    continue
                rid = id_fn(row)
                if rid not in collected:
                    collected[rid] = row
                    new_in_batch += 1
                if ts_field:
                    try:
                        rts = int(row.get(ts_field) or 0)
                        if rts > 0 and (oldest_ts == 0 or rts < oldest_ts):
                            oldest_ts = rts
                    except (TypeError, ValueError):
                        pass

            if len(batch) < eff_limit or new_in_batch == 0:
                break
            if since_ms > 0 and oldest_ts > 0 and oldest_ts <= since_ms:
                break

            last_row = batch[-1]
            next_cursor = ""
            for cf in cursor_fields:
                val = str(last_row.get(cf) or "").strip()
                if val:
                    next_cursor = val
                    break
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            after_cursor = next_cursor

        return list(collected.values())

    async def get_account_balance(self) -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/account/balance")

    async def get_funding_balances(self) -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/asset/balances")

    async def get_positions(self, inst_type: str = "SWAP") -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/account/positions", {"instType": inst_type})

    async def get_positions_history(
        self, inst_type: str = "SWAP", limit: Optional[int] = None, since_ms: int = 0
    ) -> list[dict[str, Any]]:
        return await self._paginate_after(
            "/api/v5/account/positions-history",
            {"instType": inst_type},
            cursor_fields=("uTime", "posId"),
            id_fn=lambda r: f"{r.get('posId')}_{r.get('uTime')}_{r.get('instId')}",
            ts_field="uTime",
            since_ms=since_ms,
            limit=limit,
        )

    async def get_fills_history(
        self, inst_type: str = "SWAP", limit: Optional[int] = None, since_ms: int = 0
    ) -> list[dict[str, Any]]:
        return await self._paginate_after(
            "/api/v5/trade/fills-history",
            {"instType": inst_type},
            cursor_fields=("billId",),
            id_fn=lambda r: str(r.get("billId") or r.get("tradeId") or f"{r.get('ordId')}_{r.get('ts')}"),
            ts_field="ts",
            since_ms=since_ms,
            limit=limit,
        )

    async def get_trade_fee(self, inst_type: str = "SWAP") -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/account/trade-fee", {"instType": inst_type})

    async def get_bills(
        self, inst_type: str = "SWAP", limit: Optional[int] = None, since_ms: int = 0
    ) -> list[dict[str, Any]]:
        recent = await self._paginate_after(
            "/api/v5/account/bills",
            {"instType": inst_type},
            cursor_fields=("billId",),
            id_fn=lambda r: str(r.get("billId") or f"{r.get('ts')}_{r.get('subType')}_{r.get('balChg')}"),
            ts_field="ts",
            since_ms=since_ms,
            limit=limit,
        )
        try:
            archive = await self._paginate_after(
                "/api/v5/account/bills-archive",
                {"instType": inst_type},
                cursor_fields=("billId",),
                id_fn=lambda r: str(r.get("billId") or f"{r.get('ts')}_{r.get('subType')}_{r.get('balChg')}"),
                ts_field="ts",
                since_ms=since_ms,
                limit=limit,
            )
        except Exception:
            archive = []
        merged: dict[str, dict[str, Any]] = {}
        for b in archive + recent:
            bid = str(b.get("billId") or f"{b.get('ts')}_{b.get('subType')}_{b.get('balChg')}")
            merged[bid] = b
        return list(merged.values())

    async def get_asset_bills(self, limit: Optional[int] = None, since_ms: int = 0) -> list[dict[str, Any]]:
        return await self._paginate_after(
            "/api/v5/asset/bills",
            {},
            cursor_fields=("ts",),
            id_fn=lambda r: str(r.get("billId") or f"{r.get('ts')}_{r.get('type')}_{r.get('balChg')}"),
            ts_field="ts",
            since_ms=since_ms,
            limit=limit,
        )

    async def get_deposit_history(self, limit: Optional[int] = None, since_ms: int = 0) -> list[dict[str, Any]]:
        return await self._paginate_after(
            "/api/v5/asset/deposit-history",
            {},
            cursor_fields=("depId",),
            id_fn=lambda r: str(r.get("depId") or r.get("txId") or f"{r.get('ts')}_{r.get('amt')}"),
            ts_field="ts",
            since_ms=since_ms,
            limit=limit,
        )

    async def get_withdrawal_history(self, limit: Optional[int] = None, since_ms: int = 0) -> list[dict[str, Any]]:
        return await self._paginate_after(
            "/api/v5/asset/withdrawal-history",
            {},
            cursor_fields=("wdId",),
            id_fn=lambda r: str(r.get("wdId") or r.get("txId") or f"{r.get('ts')}_{r.get('amt')}"),
            ts_field="ts",
            since_ms=since_ms,
            limit=limit,
        )

    async def get_copy_subpositions(self) -> list[dict[str, Any]]:
        return await self.request_readonly("GET", "/api/v5/copytrading/current-subpositions", {"instType": "SWAP"})

    async def get_copy_subpositions_history(self, since_ms: int = 0) -> list[dict[str, Any]]:
        return await self._paginate_after(
            "/api/v5/copytrading/subpositions-history",
            {"instType": "SWAP"},
            cursor_fields=("subPosId",),
            id_fn=lambda r: str(r.get("subPosId") or f"{r.get('instId')}_{r.get('uTime')}"),
            ts_field="uTime",
            since_ms=since_ms,
        )

    async def get_spot_tickers(self) -> dict[str, float]:
        rows = await self.request_readonly("GET", "/api/v5/market/tickers", {"instType": "SPOT"})
        prices: dict[str, float] = {}
        for row in rows:
            inst = str(row.get("instId") or "").upper()
            if inst.endswith("-USDT") or inst.endswith("-USDC"):
                base = inst.split("-")[0]
                try:
                    px = float(row.get("last") or 0.0)
                    if px > 0:
                        prices[base] = px
                except (TypeError, ValueError):
                    continue
        return prices

    async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
        """Collects read-only unified account snapshot with multi-page history and graceful degradation."""
        balances = await self.get_account_balance()
        warnings: list[str] = []

        async def _opt(name: str, coro: Any, default: Any = None) -> Any:
            try:
                return await coro
            except Exception as exc:
                warnings.append(f"{name}: {exc}")
                return [] if default is None else default

        funding = await _opt("funding", self.get_funding_balances())
        positions = await _opt("positions", self.get_positions("SWAP"))
        positions_history = await _opt("positions_history", self.get_positions_history("SWAP", since_ms=since_ms))
        fills = await _opt("fills", self.get_fills_history("SWAP", since_ms=since_ms))
        trade_fee = await _opt("trade_fee", self.get_trade_fee("SWAP"))
        bills = await _opt("bills", self.get_bills("SWAP", since_ms=since_ms))
        asset_bills = await _opt("asset_bills", self.get_asset_bills(since_ms=since_ms))
        deposits = await _opt("deposits", self.get_deposit_history(since_ms=since_ms))
        withdrawals = await _opt("withdrawals", self.get_withdrawal_history(since_ms=since_ms))
        copy_positions = await _opt("copy_positions", self.get_copy_subpositions())
        copy_history = await _opt("copy_history", self.get_copy_subpositions_history(since_ms=since_ms))
        tickers = await _opt("tickers", self.get_spot_tickers(), {})

        instruments = await _opt("instruments", self.request_readonly("GET", "/api/v5/public/instruments", {"instType": "SWAP"}))
        instrument_fields = ("instId", "ctVal", "ctValCcy", "ctType", "settleCcy", "listTime")
        instruments = [
            {k: row[k] for k in instrument_fields if k in row}
            for row in instruments if row.get("instId") and positive_number(row.get("ctVal")) and row.get("ctValCcy")
        ]
        gross_fee_sum = sum(max(0.0, -(finite_number(f.get("fee")) or 0.0)) for f in fills)
        if gross_fee_sum <= 0:
            gross_fee_sum = sum(max(0.0, -(finite_number(p.get("fee")) or 0.0)) for p in positions_history)
        embedded_bills = okx_fill_credit_ids(fills)
        rebate_sum = sum(
            max(0.0, float(b.get("balChg") or b.get("pnl") or 0.0))
            for b in bills
            if is_okx_trading_rebate(b) and str(b.get("billId") or "") not in embedded_bills
        )
        observed_rebate_ratio = (
            min(0.95, rebate_sum / gross_fee_sum) if gross_fee_sum > 0 and rebate_sum > 0 else 0.0
        )

        return {
            "venue_id": "okx",
            "balances": balances,
            "funding": funding,
            "positions": positions,
            "instruments": instruments,
            "positions_history": positions_history,
            "fills": fills,
            "trade_fee": trade_fee,
            "bills": bills,
            "asset_bills": asset_bills,
            "deposits": deposits,
            "withdrawals": withdrawals,
            "copy_positions": copy_positions,
            "copy_history": copy_history,
            "tickers": tickers,
            "observed_rebate_ratio": round(observed_rebate_ratio, 6),
            "warnings": warnings,
        }


def okx_fill_credit_ids(fills: list[dict[str, Any]]) -> set[str]:
    return {str(fill["billId"]) for fill in fills if fill.get("billId") and positive_number(fill.get("fee"))}


def is_okx_trading_rebate(b: dict[str, Any]) -> bool:
    b_type = str(b.get("type") or "").strip()
    sub_type = str(b.get("subType") or "").strip().lower()
    return (
        # Older plugin snapshots explicitly represented a commission with this
        # pair. In the trading account, subtypes 173/174 alone are funding.
        (b_type == "15" and sub_type == "173")
        or "rebate" in sub_type
        or "commission" in sub_type
    )


def _match_position_fills(positions: list[dict], fills: list[dict]) -> list[list[dict]]:
    windows: dict[str, list[tuple[int, dict, int, int, str]]] = {}
    matched: list[list[dict]] = [[] for _ in positions]
    for index, pos in enumerate(positions):
        start = int(pos.get("cTime") or 0)
        end = int(pos.get("uTime") or start)
        side = str(pos.get("direction") or pos.get("posSide") or "long").lower()
        windows.setdefault(str(pos.get("instId") or "").upper(), []).append((index, pos, start, end, side))

    for fill in fills:
        if str(fill.get("side") or "").lower() not in {"buy", "sell"}:
            continue
        timestamp = int(fill.get("fillTime") or fill.get("ts") or 0)
        matches = []
        for index, pos, start, end, side in windows.get(str(fill.get("instId") or "").upper(), []):
            if fill.get("posId") and str(fill["posId"]) != str(pos.get("posId")):
                continue
            if (fill.get("posSide") or "net").lower() not in {"net", side}:
                continue
            distance = max(start - timestamp, timestamp - end, 0)
            if distance <= 5000:
                matches.append((distance, index))
        # Prefer the actual position window. A clock-skewed fill belongs to its
        # unique nearest window; an ambiguous fill cannot support a leg split.
        matches.sort()
        if matches and (len(matches) == 1 or matches[0][0] < matches[1][0]):
            matched[matches[0][1]].append(fill)
    return matched


def normalize_okx_closed_trades(
    positions_history: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    *,
    bills: Optional[list[dict[str, Any]]] = None,
    rebate_ratio: float = 0.0,
    okb_usd_price: float = 0.0,
    instruments: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Builds normalized closed trades with per-leg fees, matched rebates, OKB discounts, and partial-close flags."""
    rebate_bills_by_inst: dict[str, list[dict[str, Any]]] = {}
    consumed_bill_ids = okx_fill_credit_ids(fills)
    for idx, b in enumerate(bills or []):
        if not is_okx_trading_rebate(b):
            continue
        amt = max(0.0, float(b.get("balChg") or b.get("pnl") or 0.0))
        if amt <= 0:
            continue
        inst = str(b.get("instId") or "").upper()
        bid = str(b.get("billId") or f"idx_{idx}")
        rebate_bills_by_inst.setdefault(inst, []).append({**b, "_bid": bid, "_amt": amt})

    records: list[dict[str, Any]] = []
    # Process chronologically so per-instrument rebate bills bind cleanly to their corresponding trade
    sorted_positions = sorted(
        positions_history,
        key=lambda p: (int(p.get("uTime") or 0), int(p.get("cTime") or 0)),
    )

    for pos, inst_fills in zip(sorted_positions, _match_position_fills(sorted_positions, fills)):
        inst = str(pos.get("instId") or "").upper()
        if not inst:
            continue
        open_ms = int(pos.get("cTime") or 0)
        close_ms = int(pos.get("uTime") or open_ms)
        open_px = float(pos.get("openAvgPx") or 0.0)
        close_px = float(pos.get("closeAvgPx") or 0.0)
        contracts = abs(float(pos.get("closeTotalPos") or pos.get("openMaxPos") or 0.0))
        spec = instrument_at(instruments or [], inst, open_ms)
        qty = contract_quantity(spec, contracts, close_px)
        direction = str(pos.get("direction") or pos.get("posSide") or "long").upper()
        side = "SHORT" if "SHORT" in direction else "LONG"
        is_partial = str(pos.get("type") or "") == "2"

        entry_side = "buy" if side == "LONG" else "sell"
        entry_fills = [f for f in inst_fills if str(f.get("side") or "").lower() == entry_side]
        exit_fills = [f for f in inst_fills if str(f.get("side") or "").lower() != entry_side]

        entry = build_fill_leg(entry_fills, spec, inst, open_ms, open_px, okb_usd_price)
        exit_leg = build_fill_leg(exit_fills, spec, inst, close_ms, close_px, okb_usd_price)
        legs = (entry, exit_leg)
        total_fee = sum(leg["gross_fee_usd"] for leg in legs)
        embedded_rebate = sum(leg["fill_rebate_usd"] for leg in legs)
        settlement = str(pos.get("ccy") or spec.get("settleCcy") or inst.split("-")[1]).upper()

        def pos_usd(key: str) -> float:
            return amount_usd(finite_number(pos.get(key)) or 0.0, settlement, inst, close_px, okb_usd_price) or 0.0

        # A position fee is signed and already includes maker credits. Reconcile the
        # unobserved remainder when the history contains only part of the fills.
        if finite_number(pos.get("fee")) is not None:
            position_fee = pos_usd("fee")
            if settlement == inst.split("-")[0]:
                # Compare the same currency at the same price before valuing any
                # missing fees. Known base-currency fills use their execution prices.
                observed = sum((finite_number(f.get("fee")) or 0) * close_px for f in inst_fills
                               if str(f.get("feeCcy") or settlement).upper() == settlement)
            else:
                observed = embedded_rebate - total_fee
            remainder = position_fee - observed
            total_fee += max(0.0, -remainder)
            embedded_rebate += max(0.0, remainder)

        matched_reb_amt = 0.0
        settled_ts = 0
        for rb in rebate_bills_by_inst.get(inst, []):
            if rb["_bid"] in consumed_bill_ids:
                continue
            bts = int(rb.get("ts") or 0)
            if open_ms - 5_000 <= bts <= close_ms + 86_400_000:
                consumed_bill_ids.add(rb["_bid"])
                matched_reb_amt += float(rb["_amt"])
                settled_ts = max(settled_ts, bts)

        external_rebate = matched_reb_amt or total_fee * max(0.0, min(1.0, rebate_ratio))
        ratio = external_rebate / total_fee if total_fee else 0.0
        for leg in legs:
            paid = leg["gross_fee_usd"]
            rebate = leg["fill_rebate_usd"] + paid * ratio
            leg["rebate_usd"] = round(rebate, 6)
            leg["net_fee_usd"] = round(paid - rebate, 6)
            leg["rebate_pct"] = round(rebate / paid * 100, 2) if paid else 0.0
            leg["rebate_status"] = ("PENDING" if paid * ratio and not matched_reb_amt
                                    else "SETTLED" if rebate else "NONE")
            credited_at = settled_ts if matched_reb_amt and paid else leg["time_ms"]
            leg["settled_at"] = _fmt_bj_ms(credited_at) if leg["rebate_status"] == "SETTLED" else ""
            for sample in leg["role_samples"].values():
                sample["net"] = sample["paid"] * (1 - ratio) - sample["credit"]

        gross_pnl = pos_usd("pnl")
        funding_pnl = pos_usd("fundingFee")
        realized = (pos_usd("realizedPnl") if finite_number(pos.get("realizedPnl")) is not None else
                    gross_pnl - total_fee + embedded_rebate + funding_pnl + pos_usd("liqPenalty") + pos_usd("settledPnl"))
        total_rebate = embedded_rebate + external_rebate
        # The UI's pre-rebate book amount plus all rebates must bridge to net.
        book_pnl = realized - embedded_rebate
        net_pnl = realized + external_rebate
        rebate_status = ("PENDING" if external_rebate and not matched_reb_amt
                         else "SETTLED" if total_rebate else "NONE")

        sym = inst.replace("-SWAP", "").replace("-", "_")
        records.append(
            {
                "trade_id": f"okx_{pos.get('posId') or sym}_{close_ms}",
                "venue_id": "okx",
                "venue_name": "OKX",
                "venue_name_zh": "OKX",
                "symbol": sym,
                "side": side,
                "qty": qty,
                "contract_count": contracts,
                "realized_pnl_usd": round(realized, 4),
                "open_time_ms": open_ms,
                "close_time_ms": close_ms,
                "gross_pnl_usd": round(gross_pnl, 4),
                "funding_pnl_usd": round(funding_pnl, 4),
                "book_pnl_usd": round(book_pnl, 4),
                "total_fee_usd": round(total_fee, 4),
                "total_rebate_usd": round(total_rebate, 4),
                "total_discount_saved_usd": round(sum(leg["discount_saved_usd"] for leg in legs), 4),
                "net_fee_usd": round(total_fee - total_rebate, 4),
                "net_pnl_usd": round(net_pnl, 4),
                "rebate_status": rebate_status,
                "is_partial_close": is_partial,
                "entry_leg": entry,
                "exit_leg": exit_leg,
            }
        )
    records.sort(key=lambda r: (r["close_time_ms"], r["open_time_ms"]), reverse=True)
    return records
