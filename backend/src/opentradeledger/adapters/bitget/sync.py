"""Independent read-only sync module for Bitget (`venue_id = 'bitget'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.base import VenueSyncOutcome

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

FULL_BACKFILL_LOOKBACK_SEC = 365 * 86400
INCREMENTAL_LOOKBACK_SEC = 7 * 86400


async def sync_bitget_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    scope = store.venue_scope("bitget")
    backfill_done = bool(scope.get_kv("backfill_completed", False))
    do_full = force_full or (not backfill_done)
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        scope.count_records("positions_history"),
        scope.count_records("fills"),
        scope.count_records("bills"),
    )

    live = await client.fetch_live_overview_bundle()
    bg_mode = str(live.get("account_mode") or "UTA")

    pos_hist = await client.fetch_history_positions(since_sec=since_sec, account_mode=bg_mode)
    fills = await client.fetch_fills(since_sec=since_sec, account_mode=bg_mode)
    orders = await client.fetch_orders_history(since_sec=since_sec, account_mode=bg_mode)
    bills = await client.fetch_financial_bills(
        since_sec=since_sec,
        account_mode=bg_mode,
        include_tax_ledger=do_full,
    )

    scope.upsert_records(
        "positions_history",
        pos_hist,
        key_fn=lambda r, i: str(r.get("positionId") or f"{r.get('symbol')}_{r.get('uTime') or r.get('updatedTime') or 0}_{i}"),
        ts_fn=lambda r: float(r.get("updatedTime") or r.get("uTime") or r.get("utime") or r.get("createdTime") or r.get("cTime") or 0),
    )
    scope.upsert_records(
        "fills",
        fills,
        key_fn=lambda r, i: str(r.get("execId") or r.get("tradeId") or f"{r.get('orderId')}_{r.get('cTime') or 0}_{i}"),
        ts_fn=lambda r: float(r.get("createdTime") or r.get("updatedTime") or r.get("cTime") or r.get("execTime") or 0),
    )
    scope.upsert_records(
        "orders",
        orders,
        key_fn=lambda r, i: str(r.get("orderId") or f"{r.get('symbol')}_{r.get('cTime') or 0}_{i}"),
        ts_fn=lambda r: float(r.get("updatedTime") or r.get("createdTime") or r.get("cTime") or 0),
    )
    def _bill_key(r: dict[str, Any], i: int) -> str:
        src = r.get("_source") or r.get("_category") or "bg"
        bid = r.get("orderId") or r.get("billId") or r.get("id") or f"{r.get('ts') or 0}_{i}"
        return f"{src}_{bid}"

    scope.upsert_records(
        "bills",
        bills,
        key_fn=_bill_key,
        ts_fn=lambda r: float(r.get("ts") or r.get("createdTime") or r.get("cTime") or r.get("updatedTime") or 0),
    )
    prev_live = scope.get_kv("live_bundle")
    live = dict(live) if isinstance(live, dict) else {}
    failed_fields = [str(x) for x in (live.pop("_failed_fields", None) or ()) if x]
    warnings = [str(w) for w in (live.get("_warnings") or ()) if w]
    if isinstance(prev_live, dict) and failed_fields:
        for fk in failed_fields:
            if fk in prev_live and prev_live[fk]:
                live[fk] = prev_live[fk]
    scope.set_kv("live_bundle", live)

    syms = {str(p.get("symbol") or "").strip().upper() for p in pos_hist if p.get("symbol")}
    if syms and (do_full or not scope.get_kv("fee_rates", None)):
        rates = await client.fetch_symbol_fee_rates(sorted(syms), account_mode=bg_mode)
        if rates:
            scope.set_kv("fee_rates", rates)

    scope.set_kv("backfill_completed", True)

    eq_val = 0.0
    uta_obj = live.get("uta_assets")
    if isinstance(uta_obj, dict):
        eq_val = float(uta_obj.get("accountEquity") or uta_obj.get("usdtEquity") or 0.0)
    elif isinstance(uta_obj, list):
        for ua_item in uta_obj:
            if isinstance(ua_item, dict):
                eq_val += float(
                    ua_item.get("accountEquity")
                    or ua_item.get("usdtEquity")
                    or ua_item.get("equity")
                    or 0.0
                )
    for fa_item in live.get("funding_assets") or []:
        c_fa = str(fa_item.get("coin") or "").upper()
        q_fa = float(fa_item.get("balance") or fa_item.get("available") or 0.0) + float(
            fa_item.get("frozen") or 0.0 if fa_item.get("balance") is None else 0.0
        )
        if q_fa > 0:
            px_fa = (
                1.0
                if c_fa in ("USDT", "USDC", "FDUSD", "USD1", "USD")
                else float((live.get("spot_tickers") or {}).get(c_fa) or 0.0)
            )
            eq_val += q_fa * px_fa
    sb_live = live.get("stock_bundle") or {}
    sp_acc = sb_live.get("stockplus_account") or {}
    if isinstance(sp_acc, dict):
        eq_val += float(
            sp_acc.get("totalEquity")
            or sp_acc.get("netLiquidationValue")
            or sp_acc.get("equity")
            or sp_acc.get("cashBalance")
            or 0.0
        )
    has_open = bool(
        live.get("current_positions")
        or live.get("futures_positions")
        or live.get("open_orders")
        or live.get("futures_open_orders")
        or sb_live.get("stockplus_positions")
    )
    fp_after = (
        scope.count_records("positions_history"),
        scope.count_records("fills"),
        scope.count_records("bills"),
    )
    new_closed = max(0, int(fp_after[0]) - int(fp_before[0])) if sum(fp_before) > 0 else 0
    return VenueSyncOutcome(
        equity_usd=eq_val,
        has_open_activity=has_open,
        fp_before=fp_before,
        fp_after=fp_after,
        new_closed_delta=new_closed,
        mode="venue_sync",
        warnings=warnings,
    )
