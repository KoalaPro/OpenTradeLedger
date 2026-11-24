"""Independent read-only sync module for OKX v5 (`venue_id = 'okx'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.core.venue_primitives import STABLECOINS

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

OKX_MERGE_SPECS = {
    "positions_history": lambda r: f"{r.get('posId')}_{r.get('uTime')}_{r.get('instId')}",
    "fills": lambda r: str(r.get("billId") or r.get("tradeId") or f"{r.get('ordId')}_{r.get('ts')}"),
    "bills": lambda r: str(r.get("billId") or f"{r.get('ts')}_{r.get('subType')}_{r.get('balChg')}"),
    "asset_bills": lambda r: str(r.get("billId") or f"{r.get('ts')}_{r.get('type')}_{r.get('balChg')}"),
    "deposits": lambda r: str(r.get("depId") or r.get("txId") or f"{r.get('ts')}_{r.get('amt')}"),
    "withdrawals": lambda r: str(r.get("wdId") or r.get("txId") or f"{r.get('ts')}_{r.get('amt')}"),
    "copy_history": lambda r: str(r.get("subPosId") or f"{r.get('instId')}_{r.get('uTime')}"),
}


def extract_okx_snapshot_activity(snap: dict[str, Any]) -> tuple[float, bool, tuple[int, ...]]:
    tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}
    equity_usd = 0.0
    for bal in snap.get("balances") or []:
        equity_usd += float(bal.get("totalEq") or 0.0)
    for fb in snap.get("funding") or []:
        ccy = str(fb.get("ccy") or "").strip().upper()
        amt = float(fb.get("bal") or 0.0)
        if not ccy or amt == 0:
            continue
        if ccy in STABLECOINS:
            equity_usd += amt
        else:
            try:
                equity_usd += amt * float(tickers.get(ccy) or 0.0)
            except (TypeError, ValueError):
                pass
    open_count = sum(1 for p in (snap.get("positions") or []) if abs(float(p.get("pos") or 0.0)) > 0)
    fills_count = len(snap.get("fills") or [])
    closed_count = len(snap.get("positions_history") or [])
    return round(equity_usd, 4), open_count > 0, (open_count, closed_count, fills_count)


async def sync_okx_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    existing_snap = store.venue_scope("okx").get_kv("snapshot") or {}
    _, _, fp_before = extract_okx_snapshot_activity(existing_snap) if existing_snap else (0.0, False, ())
    last_sync_ms = int(existing_snap.get("_last_sync_ms") or 0)
    since_ms = max(0, last_sync_ms - 2 * 86_400_000) if (last_sync_ms > 0 and not force_full) else 0
    try:
        raw_snap = await client.fetch_full_snapshot(since_ms=since_ms)
    except TypeError:
        raw_snap = await client.fetch_full_snapshot()
    snap = store.upsert_plugin_snapshot("okx", raw_snap, merge_specs=OKX_MERGE_SPECS)
    eq_usd, has_open, fp_after = extract_okx_snapshot_activity(snap)
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
