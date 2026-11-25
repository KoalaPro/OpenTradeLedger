"""Independent read-only sync module for EasiCoin (`venue_id = 'easicoin'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.easicoin.identity import EasiCoinIdentityError, account_uid, transfer_record_id, verify_archive_rows

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
    owner_uid = scope.get_kv("account_uid")
    do_full = force_full or not backfill_done or not owner_uid
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        scope.count_records("closed_pnl"),
        scope.count_records("fills"),
        scope.count_records("transfers"),
    )

    live = await client.fetch_live_overview_bundle()
    uid = account_uid(live.get("userid"))
    if not uid:
        raise EasiCoinIdentityError("ACCOUNT_UID_UNAVAILABLE")
    previous_live = scope.get_kv("live_bundle") if not owner_uid else None
    previous_uid = account_uid(previous_live.get("userid")) if isinstance(previous_live, dict) else ""
    expected_uid = owner_uid or previous_uid
    if expected_uid and expected_uid != uid:
        raise EasiCoinIdentityError("ACCOUNT_UID_MISMATCH")

    closed = await client.fetch_closed_pnl(since_sec=since_sec)
    fills = await client.fetch_trade_fills(since_sec=since_sec)
    orders = await client.fetch_orders_history(since_sec=since_sec)
    transfers = await client.fetch_transfers(since_sec=since_sec, max_windows=13 if do_full else 2)

    verified_archive_rows = 0
    for stream, rows in (("closed_pnl", closed), ("fills", fills), ("orders", orders), ("transfers", transfers)):
        foreign = sum(1 for row in rows if row.get("userid") and account_uid(row["userid"]) != uid)
        if foreign:
            raise EasiCoinIdentityError("ARCHIVE_OWNER_UNVERIFIED", stream, foreign)
        if not owner_uid:
            archived = scope.load_records(stream)
            if archived:
                verify_archive_rows(stream, archived, rows, uid)
                verified_archive_rows += len(archived)

    has_unattributed_snapshot = bool(previous_live and not previous_uid)
    if has_unattributed_snapshot and not verified_archive_rows:
        raise EasiCoinIdentityError("ARCHIVE_OWNER_UNVERIFIED")
    if not owner_uid and not scope.bind_account_uid(uid):
        raise EasiCoinIdentityError("ACCOUNT_UID_MISMATCH")

    if has_unattributed_snapshot:
        scope.set_kv("unattributed_live_bundle", previous_live)

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
        key_fn=transfer_record_id,
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
