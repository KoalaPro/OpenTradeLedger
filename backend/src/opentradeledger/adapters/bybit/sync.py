"""Independent read-only sync module for Bybit v5 (`venue_id = 'bybit'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.balance_validation import require_wallet_cache
from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.bybit.client import valid_funding_balance, validate_snapshot_funding
from opentradeledger.core.venue_primitives import STABLECOINS

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

BYBIT_MERGE_SPECS = {
    "closed_pnl": lambda r: f"{r.get('orderId')}_{r.get('updatedTime')}_{r.get('symbol')}",
    "executions": lambda r: str(r.get("execId") or f"{r.get('orderId')}_{r.get('execTime')}"),
    "tx_logs": lambda r: str(r.get("id") or f"{r.get('transactionTime')}_{r.get('type')}_{r.get('symbol')}_{r.get('cashFlow')}"),
    "deposits": lambda r: str(r.get("id") or r.get("txID") or f"{r.get('successAt')}_{r.get('amount')}"),
    "internal_deposits": lambda r: str(r.get("id") or f"{r.get('createdTime')}_{r.get('amount')}"),
    "withdrawals": lambda r: str(r.get("withdrawId") or r.get("txID") or f"{r.get('createTime')}_{r.get('amount')}"),
}


def _activity_counts(snap: dict[str, Any]) -> tuple[int, int, int]:
    return (
        sum(1 for row in snap.get("positions") or [] if float(row.get("size") or 0.0) != 0),
        len(snap.get("closed_pnl") or []),
        len(snap.get("executions") or []),
    )


def extract_bybit_snapshot_activity(snap: dict[str, Any]) -> tuple[float, bool, tuple[int, ...]]:
    tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}
    equity_usd = 0.0
    wallet = snap.get("wallet") or {}
    for acct in wallet.get("list") or []:
        equity_usd += float(acct.get("totalEquity") or 0.0)
    validate_snapshot_funding(snap)
    funding = snap.get("funding", {})
    for coin in funding.get("balance") or []:
        explicit_u = float(coin.get("usdValue") or 0.0)
        if explicit_u > 0:
            equity_usd += explicit_u
            continue
        ccy = str(coin.get("coin") or "").strip().upper()
        amt = float(coin.get("walletBalance") or 0.0)
        if not ccy or amt == 0:
            continue
        if ccy in STABLECOINS:
            equity_usd += amt
        else:
            try:
                equity_usd += amt * float(tickers.get(ccy) or 0.0)
            except (TypeError, ValueError):
                pass
    counts = _activity_counts(snap)
    return round(equity_usd, 4), counts[0] > 0, counts


async def sync_bybit_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    existing_snap = store.venue_scope("bybit").get_kv("snapshot") or {}
    # An older release could store an invalid wallet amount. Only history counts
    # are needed here; a healthy response must always be able to replace that wallet.
    fp_before = _activity_counts(existing_snap) if existing_snap else ()
    last_sync_ms = int(existing_snap.get("_last_sync_ms") or 0)
    history_backfilled = bool(existing_snap.get("_closed_pnl_backfilled_v2"))
    since_ms = max(0, last_sync_ms - 2 * 86_400_000) if history_backfilled and not force_full else 0
    try:
        raw_snap = await client.fetch_full_snapshot(since_ms=since_ms)
    except TypeError:
        raw_snap = await client.fetch_full_snapshot()
    if raw_snap.get("_invalid_wallet_fields"):
        require_wallet_cache(
            raw_snap, existing_snap,
            {"funding": valid_funding_balance},
        )
    # Older releases stopped after empty weeks and still recorded a successful sync.
    failed_fields = raw_snap.get("_failed_fields") or []
    raw_snap["_closed_pnl_backfilled_v2"] = "closed_pnl" not in failed_fields
    if "closed_pnl" in failed_fields and raw_snap.get("closed_pnl"):
        # Keep the retry marker, but let the history merger persist the pages already read.
        raw_snap["_failed_fields"] = [field for field in failed_fields if field != "closed_pnl"]
    snap = store.upsert_plugin_snapshot(
        "bybit", raw_snap, merge_specs=BYBIT_MERGE_SPECS, validate=extract_bybit_snapshot_activity,
    )
    eq_usd, has_open, fp_after = extract_bybit_snapshot_activity(snap)
    new_closed = 0
    if len(fp_before) >= 2 and sum(fp_before) > 0 and len(fp_after) >= 2:
        new_closed = max(0, int(fp_after[1]) - int(fp_before[1]))
    return VenueSyncOutcome(
        equity_usd=eq_usd,
        has_open_activity=has_open,
        fp_before=fp_before or fp_after,
        fp_after=fp_after,
        new_closed_delta=new_closed,
        mode="venue_sync",
        warnings=list(snap.get("warnings") or []),
    )
