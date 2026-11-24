"""Compatibility re-exports forwarding to per-venue independent sync modules (`adapters.<venue>.sync`)."""

from __future__ import annotations

from typing import Any

from opentradeledger.adapters.binance.sync import sync_binance_venue
from opentradeledger.adapters.bitget.sync import sync_bitget_venue
from opentradeledger.adapters.easicoin.sync import sync_easicoin_venue
from opentradeledger.adapters.gate.sync import sync_gate_venue
from opentradeledger.adapters.mexc.sync import sync_mexc_venue
from opentradeledger.core.store import LedgerStore


def _wrap_legacy_sync(fn: Any) -> Any:
    async def _runner(
        client: Any,
        ls: Any,
        *,
        now_s: int,
        force_full: bool = False,
    ) -> tuple[float, bool, tuple[int, ...], tuple[int, ...]]:
        store = ls if isinstance(ls, LedgerStore) else LedgerStore(ls.db_path.parent)
        out = await fn(client, store, now_s=now_s, force_full=force_full)
        return out.equity_usd, out.has_open_activity, out.fp_before, out.fp_after

    return _runner


sync_gate = _wrap_legacy_sync(sync_gate_venue)
sync_easicoin = _wrap_legacy_sync(sync_easicoin_venue)
sync_binance = _wrap_legacy_sync(sync_binance_venue)
sync_bitget = _wrap_legacy_sync(sync_bitget_venue)
sync_mexc = _wrap_legacy_sync(sync_mexc_venue)

LEGACY_VENUE_SYNCERS = {
    "gate": sync_gate,
    "easicoin": sync_easicoin,
    "binance": sync_binance,
    "bitget": sync_bitget,
    "mexc": sync_mexc,
}
