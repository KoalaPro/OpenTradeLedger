"""Independent read-only sync module for KuCoin (`venue_id = 'kucoin'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.kucoin.client import KucoinApiError, kucoin_position_unique_key
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


def extract_kucoin_snapshot_activity(snap: dict[str, Any]) -> tuple[float, bool, tuple[int, ...]]:
    tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}
    fut_ov = snap.get("futures_overview") if isinstance(snap.get("futures_overview"), dict) else {}
    fut_raw = (
        fut_ov.get("accountEquity")
        if fut_ov.get("accountEquity") is not None
        else (fut_ov.get("marginBalance") if fut_ov.get("marginBalance") is not None else fut_ov.get("availableBalance"))
    )
    equity_usd = float(fut_raw or 0.0)

    for acct in snap.get("spot_accounts") or []:
        if not isinstance(acct, dict):
            continue
        acct_type = str(acct.get("type") or "").lower()
        if acct_type in {"contract", "futures"}:
            continue
        ccy = str(acct.get("currency") or "").strip().upper()
        bal = (
            float(acct["balance"])
            if acct.get("balance") is not None
            else (float(acct.get("available") or 0.0) + float(acct.get("holds") or 0.0))
        )
        if not ccy or bal <= 0:
            continue
        equity_usd += bal if ccy in STABLECOINS else (bal * float(tickers.get(ccy) or 0.0))

    open_count = sum(
        1
        for p in (snap.get("positions") or [])
        if isinstance(p, dict) and (p.get("isOpen", True)) and abs(float(p.get("currentQty") or 0.0)) > 0
    )
    fills_cnt = len(snap.get("fills") or [])
    closed_cnt = len(snap.get("history_positions") or [])
    return round(equity_usd, 4), open_count > 0, (open_count, closed_cnt, fills_cnt)


async def sync_kucoin_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    _ = now_s
    existing_snap = store.venue_scope("kucoin").get_kv("snapshot") or {}
    _, _, fp_before = extract_kucoin_snapshot_activity(existing_snap) if existing_snap else (0.0, False, ())
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
    snap = store.upsert_plugin_snapshot("kucoin", raw_snap, merge_specs=KUCOIN_MERGE_SPECS)
    eq_usd, has_open, fp_after = extract_kucoin_snapshot_activity(snap)
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
