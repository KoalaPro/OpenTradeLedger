"""Independent read-only sync module for EasiCoin (`venue_id = 'easicoin'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.balance_validation import require_wallet_cache
from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.easicoin.client import valid_funding_balances, valid_spot_balances
from opentradeledger.adapters.easicoin.identity import EasiCoinIdentityError, account_uid, transfer_record_id, verify_archive_rows
from opentradeledger.core.venue_primitives import USD_CASH_ASSETS

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
    if live.get("_invalid_wallet_fields"):
        require_wallet_cache(
            live, scope.get_kv("live_bundle"),
            {"spot_balances": valid_spot_balances, "funding_balances": valid_funding_balances},
        )
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
    full_transfers = do_full or not scope.get_kv("transfer_history_backfilled_v2")
    transfer_since = now_s - FULL_BACKFILL_LOOKBACK_SEC if full_transfers else since_sec
    transfers = await client.fetch_transfers(since_sec=transfer_since, max_windows=13 if full_transfers else 2)

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
    existing_transfers = scope.load_records("transfers")
    legacy_noid_keys = {
        transfer_record_id(ar)
        for ar in existing_transfers
        if not (ar.get("transfer_id") or ar.get("id"))
    }
    comp_counts: dict[str, int] = {}
    for tr in transfers:
        ck = transfer_record_id(tr)
        comp_counts[ck] = comp_counts.get(ck, 0) + 1

    def _transfer_upsert_key(r: Any) -> str:
        ck = transfer_record_id(r)
        tid = str(r.get("transfer_id") or r.get("id") or "").strip()
        if tid and (ck not in legacy_noid_keys or comp_counts.get(ck, 0) > 1):
            return f"tr_{tid}"
        return ck

    scope.upsert_records(
        "transfers",
        transfers,
        key_fn=_transfer_upsert_key,
        ts_fn=lambda r: float(r.get("time") or 0),
    )
    prev_live = scope.get_kv("live_bundle")
    live = dict(live) if isinstance(live, dict) else {}
    failed_fields = [str(x) for x in (live.pop("_failed_fields", None) or ()) if x]
    warnings = [str(w) for w in (live.get("_warnings") or ()) if w]
    for tw in getattr(client, "_transfer_warnings", None) or ():
        if tw and str(tw) not in warnings:
            warnings.append(str(tw))
    if warnings:
        live["_warnings"] = warnings
    if not has_unattributed_snapshot and isinstance(prev_live, dict) and failed_fields:
        prev_uid_chk = account_uid(prev_live.get("userid") or prev_live.get("uid"))
        if not prev_uid_chk or prev_uid_chk == uid:
            for fk in failed_fields:
                if fk in prev_live and prev_live[fk]:
                    live[fk] = prev_live[fk]

    scope.set_kv("live_bundle", live)
    transfers_complete = not getattr(client, "_transfer_warnings", None)
    scope.set_kv("backfill_completed", transfers_complete)
    scope.set_kv("transfer_history_backfilled_v2", transfers_complete)

    px_map: dict[str, float] = {c: 1.0 for c in USD_CASH_ASSETS}
    for tk in live.get("tickers") or []:
        if isinstance(tk, dict):
            sym_u = str(tk.get("symbol") or "").upper()
            try:
                lp = float(tk.get("last_price") or 0.0)
            except (TypeError, ValueError):
                lp = 0.0
            if lp <= 0:
                continue
            base = sym_u[:-4] if (sym_u.endswith("USDT") and len(sym_u) > 4) else sym_u
            for pfx in ("1000000", "100000", "10000", "1000", "100"):
                if base.startswith(pfx) and len(base) > len(pfx):
                    px_map.setdefault(base[len(pfx):], lp / float(pfx))
                    break
            px_map[base] = lp

    eq_val = 0.0
    for fb in live.get("futures_balances") or []:
        coin_fb = str(fb.get("coin") or "").upper()
        q_fb = float(fb.get("equity") or fb.get("wallet_balance") or 0.0)
        if q_fb > 0:
            eq_val += q_fb * px_map.get(coin_fb, 0.0)
    for fnd in live.get("funding_balances") or []:
        q_fnd = float(fnd.get("total_equity") or fnd.get("available_balance") or fnd.get("balance") or 0.0)
        if q_fnd > 0:
            coin_fnd = str(fnd.get("coin") or fnd.get("token_name") or "").upper()
            eq_val += q_fnd * px_map.get(coin_fnd, 0.0)
    for spb in live.get("spot_balances") or []:
        q_sp = float(spb.get("balance") or spb.get("available") or 0.0)
        if q_sp > 0:
            coin_sp = str(spb.get("token_name") or spb.get("coin") or "").upper()
            eq_val += q_sp * px_map.get(coin_sp, 0.0)

    has_open = bool(live.get("positions") or live.get("futures_positions") or live.get("open_orders") or live.get("futures_open_orders"))
    fp_after = (
        scope.count_records("closed_pnl"),
        scope.count_records("fills"),
        scope.count_records("transfers"),
    )
    new_closed = max(0, int(fp_after[0]) - int(fp_before[0])) if sum(fp_before) > 0 else 0
    return VenueSyncOutcome(
        equity_usd=round(eq_val, 4),
        has_open_activity=has_open,
        fp_before=fp_before,
        fp_after=fp_after,
        new_closed_delta=new_closed,
        mode="venue_sync",
        warnings=warnings,
    )
