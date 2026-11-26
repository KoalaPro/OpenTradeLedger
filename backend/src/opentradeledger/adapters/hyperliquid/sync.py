"""Independent read-only sync module for Hyperliquid (`venue_id = 'hyperliquid'`)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.hyperliquid.client import resolve_hyperliquid_spot_price
from opentradeledger.core.venue_primitives import STABLECOINS, dec

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

HYPERLIQUID_MERGE_SPECS = {
    "fills": lambda r: str(r.get("tid") or r.get("hash") or f"{r.get('oid')}_{r.get('time')}_{r.get('coin')}"),
    "funding_history": lambda r: str(r.get("hash") or f"{r.get('time')}_{(r.get('delta') or {}).get('coin')}_{(r.get('delta') or {}).get('usdc')}"),
    "ledger_updates": lambda r: str(r.get("hash") or f"{r.get('time')}_{(r.get('delta') or {}).get('type')}"),
}


def extract_hyperliquid_snapshot_activity(snap: dict[str, Any]) -> tuple[float, bool, tuple[int, ...]]:
    ch = snap.get("clearinghouse") if isinstance(snap.get("clearinghouse"), dict) else {}
    ms = ch.get("marginSummary") if isinstance(ch.get("marginSummary"), dict) else {}
    perp_eq = float(ms.get("accountValue") or 0.0)

    spot_eq = 0.0
    spot = snap.get("spot") if isinstance(snap.get("spot"), dict) else {}
    for bal in spot.get("balances") or []:
        if not isinstance(bal, dict):
            continue
        coin = str(bal.get("coin") or "").strip().upper()
        total_amt = float(bal.get("total") or 0.0)
        if not coin or total_amt <= 0:
            continue
        if coin in STABLECOINS:
            spot_eq += total_amt
        else:
            spot_eq += total_amt * resolve_hyperliquid_spot_price(bal, snap)

    vault_eq = max(
        0.0,
        float(
            sum(
                (dec(v.get("equity")) for v in (snap.get("vault_equities") or []) if isinstance(v, dict)),
                Decimal("0"),
            )
        ),
    )

    open_count = 0
    for ap in ch.get("assetPositions") or []:
        pos = ap.get("position") if isinstance(ap, dict) and isinstance(ap.get("position"), dict) else ap
        if isinstance(pos, dict) and abs(float(pos.get("szi") or 0.0)) > 0:
            open_count += 1

    fills = snap.get("fills") or []
    closed_count = sum(
        1
        for f in fills
        if isinstance(f, dict)
        and ("Close" in str(f.get("dir") or "") or abs(float(f.get("closedPnl") or 0.0)) > 1e-9)
    )
    return round(perp_eq + spot_eq + vault_eq, 4), open_count > 0, (open_count, closed_count, len(fills))


async def sync_hyperliquid_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    _ = now_s
    existing_snap = store.venue_scope("hyperliquid").get_kv("snapshot") or {}
    _, _, fp_before = extract_hyperliquid_snapshot_activity(existing_snap) if existing_snap else (0.0, False, ())
    last_sync_ms = int(existing_snap.get("_last_sync_ms") or 0)
    since_ms = max(0, last_sync_ms - 2 * 86_400_000) if (last_sync_ms > 0 and not force_full) else 0
    raw_snap = await client.fetch_full_snapshot(since_ms=since_ms)
    snap = store.upsert_plugin_snapshot("hyperliquid", raw_snap, merge_specs=HYPERLIQUID_MERGE_SPECS)
    eq_usd, has_open, fp_after = extract_hyperliquid_snapshot_activity(snap)
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
