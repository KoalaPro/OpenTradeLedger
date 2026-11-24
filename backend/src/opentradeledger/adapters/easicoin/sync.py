"""Independent read-only sync module for EasiCoin (`venue_id = 'easicoin'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.base import VenueSyncOutcome

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

FULL_BACKFILL_LOOKBACK_SEC = 365 * 86400
INCREMENTAL_LOOKBACK_SEC = 7 * 86400


async def sync_easicoin_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    scope = store.venue_scope("easicoin")
    backfill_done = bool(scope.get_kv("backfill_completed", False))
    do_full = force_full or (not backfill_done)
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        scope.count_records("closed_pnl"),
        scope.count_records("fills"),
        scope.count_records("transfers"),
    )

    live = await client.fetch_live_overview_bundle()
    closed = await client.fetch_closed_pnl(since_sec=since_sec)
    fills = await client.fetch_trade_fills(since_sec=since_sec)
    orders = await client.fetch_orders_history(since_sec=since_sec)
    transfers = await client.fetch_transfers(since_sec=since_sec, max_windows=13 if do_full else 2)

    scope.upsert_records(
        "closed_pnl",
        closed,
        key_fn=lambda r, i: str(r.get("order_id") or f"{r.get('symbol')}_{int(float(r.get('created_at') or 0))}_{r.get('closed_pnl')}_{i}"),
        ts_fn=lambda r: float(r.get("created_at") or 0),
    )
    scope.upsert_records(
        "fills",
        fills,
        key_fn=lambda r, i: str(r.get("exec_id") or f"{r.get('order_id')}_{r.get('exec_time')}_{i}"),
        ts_fn=lambda r: float(r.get("exec_time") or 0),
    )
    scope.upsert_records(
        "orders",
        [o for o in orders if o.get("order_id")],
        key_fn=lambda r: str(r.get("order_id") or ""),
        ts_fn=lambda r: float(r.get("created_time") or 0),
    )
    scope.upsert_records(
        "transfers",
        transfers,
        key_fn=lambda r: f"{int(float(r.get('time') or 0))}_{r.get('coin')}_{r.get('amount')}_{r.get('from_wallet')}_{r.get('to_wallet')}",
        ts_fn=lambda r: float(r.get("time") or 0),
    )
    scope.set_kv("live_bundle", live)
    scope.set_kv("backfill_completed", True)

    eq_val = 0.0
    for fb in live.get("futures_balances") or []:
        if str(fb.get("coin") or "").upper() in ("USDT", "USDC", "USD"):
            eq_val += float(fb.get("equity") or fb.get("wallet_balance") or 0.0)
    has_open = bool(live.get("positions") or live.get("futures_positions") or live.get("open_orders") or live.get("futures_open_orders"))
    fp_after = (
        scope.count_records("closed_pnl"),
        scope.count_records("fills"),
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
    )
