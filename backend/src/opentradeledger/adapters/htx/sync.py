"""Independent read-only sync module for HTX (`venue_id = 'htx'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.htx.client import HtxApiError, htx_match_realized_pnl, htx_match_unique_key
from opentradeledger.core.venue_primitives import STABLECOINS

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

HTX_MERGE_SPECS = {
    "matches": lambda r: htx_match_unique_key(r),
    "financials": lambda r: str(r.get("id") or f"{r.get('ts')}_{r.get('type')}_{r.get('amount')}"),
    "deposits": lambda r: str(r.get("id") or r.get("tx-hash") or f"{r.get('created-at')}_{r.get('amount')}"),
    "withdrawals": lambda r: str(r.get("id") or r.get("tx-hash") or f"{r.get('created-at')}_{r.get('amount')}"),
}


def extract_htx_snapshot_activity(snap: dict[str, Any]) -> tuple[float, bool, tuple[int, ...]]:
    tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}
    equity_usd = 0.0
    for acc in snap.get("swap_accounts") or []:
        if isinstance(acc, dict):
            mb = (
                acc.get("margin_balance")
                if acc.get("margin_balance") is not None
                else (acc.get("margin_static") if acc.get("margin_static") is not None else acc.get("margin_available"))
            )
            equity_usd += float(mb or 0.0)

    spot_agg: dict[str, float] = {}
    for row in snap.get("spot_balances") or []:
        if not isinstance(row, dict):
            continue
        acct_tp = str(row.get("_account_type") or "SPOT").strip().upper()
        if acct_tp == "POINT":
            continue
        item_tp = str(row.get("type") or "").strip().lower()
        if item_tp and item_tp not in {"trade", "frozen"}:
            continue
        ccy = str(row.get("currency") or "").strip().upper()
        if ccy in {"HBPOINT", "POINT"}:
            continue
        bal = float(row.get("balance") or 0.0)
        if ccy and bal > 0:
            spot_agg[ccy] = spot_agg.get(ccy, 0.0) + bal
    for ccy, qty in spot_agg.items():
        equity_usd += qty if ccy in STABLECOINS else (qty * float(tickers.get(ccy) or 0.0))

    open_count = sum(
        1
        for p in (snap.get("positions") or [])
        if isinstance(p, dict) and abs(float(p.get("volume") or 0.0)) > 0
    )
    matches = snap.get("matches") or []
    closed_cnt = sum(
        1
        for m in matches
        if isinstance(m, dict)
        and (str(m.get("offset") or "").lower() != "open" or abs(htx_match_realized_pnl(m)) > 1e-9)
    )
    return round(equity_usd, 4), open_count > 0, (open_count, closed_cnt, len(matches))


async def sync_htx_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    _ = now_s
    existing_snap = store.venue_scope("htx").get_kv("snapshot") or {}
    _, _, fp_before = extract_htx_snapshot_activity(existing_snap) if existing_snap else (0.0, False, ())
    last_sync_ms = int(existing_snap.get("_last_sync_ms") or 0)
    since_ms = max(0, last_sync_ms - 36 * 3600 * 1000) if (last_sync_ms > 0 and not force_full) else 0
    raw_snap = await client.fetch_full_snapshot(since_ms=since_ms)
    failed_fields = [str(k) for k in (raw_snap.get("_failed_fields") or ()) if k]
    raw_warnings = [str(w) for w in (raw_snap.get("warnings") or ())]
    if not existing_snap:
        for w in raw_warnings:
            if "malformed" in w.lower():
                raise HtxApiError(w)
    else:
        for fk in failed_fields:
            if fk in existing_snap and existing_snap[fk] is not None:
                raw_snap[fk] = existing_snap[fk]
            else:
                for w in raw_warnings:
                    if w.startswith(f"{fk}:") and "malformed" in w.lower():
                        raise HtxApiError(w)
    snap = store.upsert_plugin_snapshot("htx", raw_snap, merge_specs=HTX_MERGE_SPECS)
    eq_usd, has_open, fp_after = extract_htx_snapshot_activity(snap)
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
