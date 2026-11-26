"""Independent read-only sync module for BingX (`venue_id = 'bingx'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.bingx.client import BingxApiError, bingx_spot_quantity
from opentradeledger.core.venue_primitives import STABLECOINS

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

BINGX_MERGE_SPECS = {
    "fills": lambda r: str(r.get("tradeId") or f"{r.get('orderId')}_{r.get('filledTm') or r.get('time')}"),
    "income": lambda r: str(r.get("tranId") or f"{r.get('time')}_{r.get('incomeType')}_{r.get('income')}"),
    "deposits": lambda r: str(r.get("txId") or r.get("id") or f"{r.get('insertTime')}_{r.get('amount')}"),
    "withdrawals": lambda r: str(r.get("id") or r.get("txId") or f"{r.get('applyTime')}_{r.get('amount')}"),
}


def _activity_counts(snap: dict[str, Any]) -> tuple[int, int, int]:
    open_count = sum(
        1
        for p in (snap.get("positions") or [])
        if isinstance(p, dict) and abs(float(p.get("positionAmt") or p.get("availableAmt") or 0.0)) > 0
    )
    fills = snap.get("fills") or []
    closed_cnt = sum(1 for f in fills if isinstance(f, dict) and abs(float(f.get("realizedPnl") or f.get("profit") or 0.0)) > 1e-9)
    return (open_count, closed_cnt, len(fills))


def extract_bingx_snapshot_activity(snap: dict[str, Any]) -> tuple[float, bool, tuple[int, ...]]:
    tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}
    equity_usd = 0.0
    for bal in snap.get("swap_balances") or []:
        if not isinstance(bal, dict):
            continue
        asset = str(bal.get("asset") or "USDT").strip().upper()
        eq = float(bal["equity"] if bal.get("equity") is not None else bal.get("balance") or 0.0)
        equity_usd += eq if asset in STABLECOINS else (eq * float(tickers.get(asset) or 0.0))

    for sb in snap.get("spot_balances") or []:
        if not isinstance(sb, dict):
            continue
        ccy = str(sb.get("asset") or "").strip().upper()
        qty = bingx_spot_quantity(sb)
        if not ccy or qty <= 0:
            continue
        equity_usd += qty if ccy in STABLECOINS else (qty * float(tickers.get(ccy) or 0.0))

    counts = _activity_counts(snap)
    return round(equity_usd, 4), counts[0] > 0, counts


async def sync_bingx_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    _ = now_s
    existing_snap = store.venue_scope("bingx").get_kv("snapshot") or {}
    fp_before = _activity_counts(existing_snap) if existing_snap else ()
    last_sync_ms = int(existing_snap.get("_last_sync_ms") or 0)
    since_ms = max(0, last_sync_ms - 2 * 86_400_000) if (last_sync_ms > 0 and not force_full) else 0
    raw_snap = await client.fetch_full_snapshot(since_ms=since_ms)
    failed_fields = [str(k) for k in (raw_snap.get("_failed_fields") or ()) if k]
    raw_warnings = [str(w) for w in (raw_snap.get("warnings") or ())]
    if not existing_snap:
        for w in raw_warnings:
            if "malformed" in w.lower():
                raise BingxApiError(w)
    else:
        for fk in failed_fields:
            if fk in existing_snap and existing_snap[fk] is not None:
                raw_snap[fk] = existing_snap[fk]
            else:
                for w in raw_warnings:
                    if w.startswith(f"{fk}:") and "malformed" in w.lower():
                        raise BingxApiError(w)
    if existing_snap and "spot_balances" in failed_fields:
        # Only carry forward cached balances that are safe to value.
        extract_bingx_snapshot_activity(raw_snap)
    snap = store.upsert_plugin_snapshot("bingx", raw_snap, merge_specs=BINGX_MERGE_SPECS)
    eq_usd, has_open, fp_after = extract_bingx_snapshot_activity(snap)
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
