"""Deprecated legacy compatibility shim.

All 7 exchanges (binance, okx, bybit, gate, bitget, mexc, easicoin) now own
independent accounting modules under `opentradeledger.adapters.<venue>.accounting`.
"""

from __future__ import annotations

from opentradeledger.adapters.gate.accounting import pair_futures_trades_with_rebates

__all__ = ["pair_futures_trades_with_rebates"]
