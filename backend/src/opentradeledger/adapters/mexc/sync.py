"""Independent read-only sync module for MEXC (`venue_id = 'mexc'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.balance_validation import require_wallet_cache
from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.mexc.client import valid_futures_assets, valid_spot_balances

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

FULL_BACKFILL_LOOKBACK_SEC = 365 * 86400
INCREMENTAL_LOOKBACK_SEC = 7 * 86400


async def sync_mexc_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    scope = store.venue_scope("mexc")
    backfill_done = bool(scope.get_kv("backfill_completed", False))
    do_full = force_full or (not backfill_done)
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        scope.count_records("positions_history"),
        scope.count_records("deals"),
        scope.count_records("transfers"),
    )

    live = await client.fetch_live_overview_bundle()
    if live.get("_invalid_wallet_fields"):
        require_wallet_cache(
            live, scope.get_kv("live_bundle"),
            {"spot_balances": valid_spot_balances, "futures_assets": valid_futures_assets},
        )
    pos_hist = await client.fetch_history_positions(since_sec=since_sec, max_pages=20 if do_full else 3)
    deals = await client.fetch_order_deals(since_sec=since_sec, max_pages=30 if do_full else 4)
    orders = await client.fetch_history_orders(since_sec=since_sec, max_pages=30 if do_full else 4)
    funding = await client.fetch_funding_records(since_sec=since_sec, max_pages=15 if do_full else 3)
    last_cf = int(scope.get_kv("cashflow_sync_sec", 0) or 0)
    transfers: list[dict[str, Any]] | None = None
    if do_full or (now_s - last_cf) >= 120:
        transfers = await client.fetch_cashflows_and_rebates(since_sec=since_sec, do_full=do_full)

    scope.upsert_records(
        "positions_history",
        pos_hist,
        key_fn=lambda r, i: str(r.get("positionId") or f"{r.get('symbol')}_{r.get('updateTime') or r.get('createTime') or 0}_{i}"),
        ts_fn=lambda r: float(r.get("updateTime") or r.get("createTime") or 0),
    )
    scope.upsert_records(
        "deals",
        deals,
        key_fn=lambda r, i: str(r.get("id") or f"{r.get('orderId')}_{r.get('timestamp') or 0}_{i}"),
        ts_fn=lambda r: float(r.get("timestamp") or 0),
    )
    scope.upsert_records(
        "orders",
        orders,
        key_fn=lambda r, i: str(r.get("orderId") or f"{r.get('symbol')}_{r.get('updateTime') or 0}_{i}"),
        ts_fn=lambda r: float(r.get("updateTime") or r.get("createTime") or 0),
    )
    scope.upsert_records(
        "funding",
        funding,
        key_fn=lambda r, i: str(r.get("id") or f"{r.get('symbol')}_{r.get('settleTime') or 0}_{i}"),
        ts_fn=lambda r: float(r.get("settleTime") or 0),
    )
    if transfers is not None:
        scope.upsert_records(
            "transfers",
            transfers,
            key_fn=lambda r, i: str(r.get("_key") or r.get("id") or r.get("txid") or f"{r.get('_time_ms') or r.get('createTime') or 0}_{i}"),
            ts_fn=lambda r: float(r.get("_time_ms") or r.get("createTime") or 0),
        )
        scope.set_kv("cashflow_sync_sec", now_s)
    prev_live = scope.get_kv("live_bundle")
    live = dict(live) if isinstance(live, dict) else {}
    failed_fields = [str(x) for x in (live.pop("_failed_fields", None) or ()) if x]
    warnings = [str(w) for w in (live.get("_warnings") or ()) if w]
    if isinstance(prev_live, dict) and failed_fields:
        for fk in failed_fields:
            if fk in prev_live and prev_live[fk]:
                live[fk] = prev_live[fk]
    scope.set_kv("live_bundle", live)

    syms = (
        {str(p.get("symbol") or "").strip().upper() for p in pos_hist if p.get("symbol")}
        | {str(d.get("symbol") or "").strip().upper() for d in deals if d.get("symbol")}
        | {str(p.get("symbol") or "").strip().upper() for p in (live.get("futures_positions") or []) if p.get("symbol")}
    )
    cached_specs = scope.get_kv("contract_specs", {})
    if not isinstance(cached_specs, dict):
        cached_specs = {}
    missing = sorted(syms - set(cached_specs.keys()))
    if missing or do_full:
        fresh_specs = await client.fetch_contract_specs(sorted(syms) if do_full else missing)
        if fresh_specs:
            cached_specs.update(fresh_specs)
            scope.set_kv("contract_specs", cached_specs)

    scope.set_kv("backfill_completed", True)

    eq_val = 0.0
    tickers = live.get("spot_tickers") or {}
    for fa_m in live.get("futures_assets") or []:
        c_m = str(fa_m.get("currency") or "").upper()
        eq_m = float(fa_m.get("equity") or fa_m.get("cashBalance") or 0.0)
        if eq_m > 0:
            px_m = 1.0 if c_m in ("USDT", "USDC", "USDE", "FDUSD", "USD") else float(tickers.get(c_m) or 0.0)
            eq_val += eq_m * px_m
    for sb_m in live.get("spot_balances") or []:
        c_sb = str(sb_m.get("asset") or "").upper()
        q_sb = float(sb_m.get("free") or 0.0) + float(sb_m.get("locked") or 0.0)
        if q_sb > 0:
            px_sb = 1.0 if c_sb in ("USDT", "USDC", "USDE", "FDUSD", "USD") else float(tickers.get(c_sb) or 0.0)
            eq_val += q_sb * px_sb
    has_open = bool(
        live.get("futures_positions") or live.get("open_orders") or live.get("futures_open_orders")
    )
    fp_after = (
        scope.count_records("positions_history"),
        scope.count_records("deals"),
        scope.count_records("transfers"),
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
