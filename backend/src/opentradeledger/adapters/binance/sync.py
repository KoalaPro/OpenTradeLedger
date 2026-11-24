"""Independent read-only sync module for Binance (`venue_id = 'binance'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.base import VenueSyncOutcome

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

FULL_BACKFILL_LOOKBACK_SEC = 365 * 86400
INCREMENTAL_LOOKBACK_SEC = 7 * 86400


async def sync_binance_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    scope = store.venue_scope("binance")
    backfill_done = bool(scope.get_kv("backfill_completed", False))
    do_full = force_full or (not backfill_done)
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        scope.count_records("fills"),
        scope.count_records("orders"),
        scope.count_records("income"),
        scope.count_records("cashflows"),
    )

    live = await client.fetch_live_overview_bundle()
    acc_mode = str(live.get("account_mode") or "PAPI")
    inc = await client.fetch_um_income(since_sec=since_sec, account_mode=acc_mode)

    active_syms = {
        str(r.get("symbol") or "").strip().upper() for r in inc if r.get("symbol")
    } | {
        str(p.get("symbol") or "").strip().upper()
        for p in (live.get("futures_positions") or [])
        if p.get("symbol")
    }
    fills: list[dict[str, Any]] = []
    ords: list[dict[str, Any]] = []
    fresh_cr: dict[str, Any] = {}
    cached_cr = scope.get_kv("commission_rates", {})
    if not isinstance(cached_cr, dict):
        cached_cr = {}
    if active_syms:
        fills = await client.fetch_um_trades_for_symbols(sorted(active_syms), account_mode=acc_mode)
        ords = await client.fetch_um_orders_for_symbols(sorted(active_syms), account_mode=acc_mode)
        if do_full or not cached_cr:
            fresh_cr = await client.fetch_um_commission_rates(sorted(active_syms), account_mode=acc_mode) or {}

    last_cf_sync = int(scope.get_kv("cashflow_sync_sec", 0) or 0)
    cf_done = bool(scope.get_kv("copy_cf_v1", False))
    cfs: list[dict[str, Any]] | None = None
    cf_full = do_full or (not cf_done)
    if do_full or (not cf_done) or (now_s - last_cf_sync) >= 300:
        cf_since = (now_s - FULL_BACKFILL_LOOKBACK_SEC) if cf_full else since_sec
        cfs = await client.fetch_cashflows(since_sec=cf_since, do_full=cf_full)

    cached_bnb_px = scope.get_kv("bnb_prices", {})
    if not isinstance(cached_bnb_px, dict):
        cached_bnb_px = {}
    fresh_bnb_px = await client.fetch_bnb_daily_prices(full_history=do_full or (len(cached_bnb_px) < 30))

    scope.upsert_records(
        "income",
        inc,
        key_fn=lambda r, i: f"{r.get('tranId') or i}_{r.get('incomeType')}_{int(float(r.get('time') or 0))}_{r.get('asset')}_{r.get('income')}_{r.get('tradeId') or ''}",
        ts_fn=lambda r: float(r.get("time") or 0),
    )
    if active_syms:
        def _fill_key(r: dict[str, Any], i: int) -> str:
            sym = str(r.get("symbol") or "").upper()
            fid = r.get("id") or f"{r.get('orderId')}_{int(float(r.get('time') or 0))}_{i}"
            return f"{sym}_{fid}"

        scope.upsert_records(
            "fills",
            fills,
            key_fn=_fill_key,
            ts_fn=lambda r: float(r.get("time") or 0),
        )
        scope.upsert_records(
            "orders",
            [o for o in ords if o.get("orderId") and o.get("symbol")],
            key_fn=lambda r: f"{str(r.get('symbol') or '').upper()}_{str(r.get('orderId') or '').strip()}",
            ts_fn=lambda r: float(r.get("updateTime") or r.get("time") or 0),
        )
        if fresh_cr:
            cached_cr.update(fresh_cr)
            scope.set_kv("commission_rates", cached_cr)
    if cfs is not None:
        scope.upsert_records(
            "cashflows",
            cfs,
            key_fn=lambda r, i: str(r.get("cf_key") or f"{r.get('cf_type')}_{r.get('time_ms')}_{i}"),
            ts_fn=lambda r: float(r.get("time_ms") or 0),
        )
        scope.set_kv("cashflow_sync_sec", now_s)
        if cf_full:
            scope.set_kv("copy_cf_v1", True)
    if fresh_bnb_px:
        cached_bnb_px.update(fresh_bnb_px)
        scope.set_kv("bnb_prices", cached_bnb_px)
    scope.set_kv("live_bundle", live)
    scope.set_kv("backfill_completed", True)

    eq_val = sum(float(wb.get("balance") or 0.0) for wb in (live.get("wallet_balances") or []))
    sb_live = live.get("stock_bundle") or {}
    has_open = bool(
        [p for p in (live.get("futures_positions") or []) if abs(float(p.get("positionAmt") or 0.0)) > 1e-9]
        or live.get("open_orders")
        or live.get("futures_open_orders")
        or sb_live.get("equity_open_orders")
        or sb_live.get("equity_quotes")
    )
    fp_after = (
        scope.count_records("fills"),
        scope.count_records("orders"),
        scope.count_records("income"),
        scope.count_records("cashflows"),
    )
    new_closed = max(0, int(fp_after[0]) - int(fp_before[0])) if sum(fp_before) > 0 else 0
    return VenueSyncOutcome(
        equity_usd=eq_val,
        has_open_activity=has_open,
        fp_before=fp_before,
        fp_after=fp_after,
        new_closed_delta=new_closed,
        mode="venue_sync",
    )
