"""Independent read-only sync module for KuCoin (`venue_id = 'kucoin'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.kucoin.client import (
    KucoinApiError,
    kucoin_futures_equity,
    kucoin_position_unique_key,
    kucoin_spot_balance,
)
from opentradeledger.core.venue_primitives import STABLECOINS

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

KUCOIN_MERGE_SPECS = {
    "history_positions": lambda r: kucoin_position_unique_key(r),
    "fills": lambda r: str(r.get("tradeId") or f"{r.get('orderId')}_{r.get('createdAt')}"),
    "tx_history": lambda r: str(r.get("offset") or f"{r.get('time')}_{r.get('type')}_{r.get('amount')}"),
    "deposits": lambda r: str(r.get("walletTxId") or f"{r.get('createdAt')}_{r.get('amount')}"),
    "withdrawals": lambda r: str(r.get("id") or r.get("walletTxId") or f"{r.get('createdAt')}_{r.get('amount')}"),
}


def _activity_counts(snap: dict[str, Any]) -> tuple[int, int, int]:
    open_count = sum(
        1
        for p in (snap.get("positions") or [])
        if isinstance(p, dict) and (p.get("isOpen", True)) and abs(float(p.get("currentQty") or 0.0)) > 0
    )
    fills_cnt = len(snap.get("fills") or [])
    closed_cnt = len(snap.get("history_positions") or [])
    return (open_count, closed_cnt, fills_cnt)


def extract_kucoin_snapshot_activity(snap: dict[str, Any]) -> tuple[float, bool, tuple[int, ...]]:
    tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}
    fut_ov = snap.get("futures_overview") if isinstance(snap.get("futures_overview"), dict) else {}
    equity_usd = kucoin_futures_equity(fut_ov) if fut_ov else 0.0

    for acct in snap.get("spot_accounts") or []:
        if not isinstance(acct, dict):
            continue
        acct_type = str(acct.get("type") or "").lower()
        if acct_type in {"contract", "futures"}:
            continue
        ccy = str(acct.get("currency") or "").strip().upper()
        bal = kucoin_spot_balance(acct)
        if not ccy or bal <= 0:
            continue
        equity_usd += bal if ccy in STABLECOINS else (bal * float(tickers.get(ccy) or 0.0))

    counts = _activity_counts(snap)
    return round(equity_usd, 4), counts[0] > 0, counts


async def sync_kucoin_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    _ = now_s
    existing_snap = store.venue_scope("kucoin").get_kv("snapshot") or {}
    fp_before = _activity_counts(existing_snap) if existing_snap else ()
    last_sync_ms = int(existing_snap.get("_last_sync_ms") or 0)
    since_ms = max(0, last_sync_ms - 2 * 86_400_000) if (last_sync_ms > 0 and not force_full) else 0
    raw_snap = await client.fetch_full_snapshot(since_ms=since_ms)
    failed_fields = [str(k) for k in (raw_snap.get("_failed_fields") or ()) if k]
    raw_warnings = [str(w) for w in (raw_snap.get("warnings") or ())]
    if not existing_snap:
        for w in raw_warnings:
            if "malformed" in w.lower():
                raise KucoinApiError(w)
    else:
        for fk in failed_fields:
            if fk in existing_snap and existing_snap[fk] is not None:
                raw_snap[fk] = existing_snap[fk]
            else:
                for w in raw_warnings:
                    if w.startswith(f"{fk}:") and "malformed" in w.lower():
                        raise KucoinApiError(w)
    if existing_snap and {"spot_accounts", "futures_overview"}.intersection(failed_fields):
        # Only carry forward cached balances that are safe to value.
        extract_kucoin_snapshot_activity(raw_snap)
    snap = store.upsert_plugin_snapshot("kucoin", raw_snap, merge_specs=KUCOIN_MERGE_SPECS)
    eq_usd, has_open, fp_after = extract_kucoin_snapshot_activity(snap)
    return VenueSyncOutcome(
        equity_usd=eq_usd,
        has_open_activity=has_open,
        fp_before=fp_before or fp_after,
        fp_after=fp_after,
        mode="venue_sync",
        warnings=list(snap.get("warnings") or []),
    )
