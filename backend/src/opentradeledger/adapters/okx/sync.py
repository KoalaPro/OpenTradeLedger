"""Independent read-only sync module for OKX v5 (`venue_id = 'okx'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.balance_validation import require_wallet_cache
from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.okx.client import valid_funding_balances, validate_snapshot_funding
from opentradeledger.core.venue_primitives import STABLECOINS

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

OKX_MERGE_SPECS = {
    "instruments": lambda r: (r.get("instId"), r.get("listTime")),
    "positions_history": lambda r: f"{r.get('posId')}_{r.get('uTime')}_{r.get('instId')}",
    "fills": lambda r: str(r.get("billId") or r.get("tradeId") or f"{r.get('ordId')}_{r.get('ts')}"),
    "bills": lambda r: str(r.get("billId") or f"{r.get('ts')}_{r.get('subType')}_{r.get('balChg')}"),
    "asset_bills": lambda r: str(r.get("billId") or f"{r.get('ts')}_{r.get('type')}_{r.get('balChg')}"),
    "deposits": lambda r: str(r.get("depId") or r.get("txId") or f"{r.get('ts')}_{r.get('amt')}"),
    "withdrawals": lambda r: str(r.get("wdId") or r.get("txId") or f"{r.get('ts')}_{r.get('amt')}"),
    "copy_history": lambda r: str(r.get("subPosId") or f"{r.get('instId')}_{r.get('uTime')}"),
}


def _activity_counts(snap: dict[str, Any]) -> tuple[int, int, int]:
    return (
        sum(1 for row in snap.get("positions") or [] if float(row.get("pos") or 0.0) != 0),
        len(snap.get("positions_history") or []),
        len(snap.get("fills") or []),
    )


def extract_okx_snapshot_activity(snap: dict[str, Any]) -> tuple[float, bool, tuple[int, ...]]:
    tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}
    equity_usd = 0.0
    for bal in snap.get("balances") or []:
        equity_usd += float(bal.get("totalEq") or 0.0)
    validate_snapshot_funding(snap)
    funding = snap.get("funding", [])
    for fb in funding:
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
    counts = _activity_counts(snap)
    return round(equity_usd, 4), counts[0] > 0, counts


async def sync_okx_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    existing_snap = store.venue_scope("okx").get_kv("snapshot") or {}
    # An older release could store an invalid wallet amount. Only history counts
    # are needed here; a healthy response must always be able to replace that wallet.
    fp_before = _activity_counts(existing_snap) if existing_snap else ()
    last_sync_ms = int(existing_snap.get("_last_sync_ms") or 0)
    since_ms = max(0, last_sync_ms - 2 * 86_400_000) if (last_sync_ms > 0 and not force_full) else 0
    try:
        raw_snap = await client.fetch_full_snapshot(since_ms=since_ms)
    except TypeError:
        raw_snap = await client.fetch_full_snapshot()
    if raw_snap.get("_invalid_wallet_fields"):
        require_wallet_cache(
            raw_snap, existing_snap,
            {"funding": valid_funding_balances},
        )
    # One public instrument list covers both current rows and trades retained
    # outside the API history window. Keep only relevant specifications on disk.
    used_instruments = {row.get("instId") for snapshot in (existing_snap, raw_snap)
                        for field in ("positions", "positions_history", "fills")
                        for row in snapshot.get(field) or []}
    raw_snap["instruments"] = [row for row in raw_snap.get("instruments") or []
                               if row.get("instId") in used_instruments]
    snap = store.upsert_plugin_snapshot(
        "okx", raw_snap, merge_specs=OKX_MERGE_SPECS, validate=extract_okx_snapshot_activity,
    )
    eq_usd, has_open, fp_after = extract_okx_snapshot_activity(snap)
    return VenueSyncOutcome(
        equity_usd=eq_usd,
        has_open_activity=has_open,
        fp_before=fp_before or fp_after,
        fp_after=fp_after,
        mode="venue_sync",
        warnings=list(snap.get("warnings") or []),
    )
