"""Attribute close-order PnL using native fills and reconciled position quantities."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from opentradeledger.core.position_lifecycle import build_position_events


def reported_amount(raw: Any) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (ValueError, InvalidOperation):
        return None
    return value if value.is_finite() else None


def position_fill_accounting(
    fills: list[dict], *, side: str, closed_qty: Any, gross_pnl: Any, linear: bool = True,
    still_open: bool = False,
) -> tuple[list[dict], dict[str, Decimal | None]]:
    """Use native PnL first. A single missing fill can be reconciled to its parent's gross."""
    exits = [fill for fill in fills if not fill['is_entry']]
    opened = sum((Decimal(str(fill['qty'])) for fill in fills if fill['is_entry']), Decimal(0))
    closed = sum((Decimal(str(fill['qty'])) for fill in exits), Decimal(0))
    expected = reported_amount(closed_qty)
    # Quantity conversion on some venues is a floating point multiplication.
    coverage = bool(expected and abs(closed - expected) <= expected * Decimal('1e-10'))
    balanced = abs(opened - closed) <= closed * Decimal('1e-10')
    complete = coverage and (balanced or (still_open and opened > closed))
    parent = reported_amount(gross_pnl)
    missing = [f for f in exits if f.get('gross_pnl_usd') is None]
    if coverage and parent is not None and len(missing) == 1:
        known = sum((Decimal(str(f['gross_pnl_usd'])) for f in exits if f.get('gross_pnl_usd') is not None), Decimal(0))
        missing[0]['gross_pnl_usd'] = parent - known

    events = build_position_events(fills, side=side.upper(), linear=linear) if complete else []
    order_values: dict[str, list[Decimal | None]] = defaultdict(list)
    for idx, fill in enumerate(fills):
        if fill['is_entry']:
            continue
        value = reported_amount(fill.get('gross_pnl_usd'))
        if value is None and len(events) == len(fills):
            value = reported_amount(events[idx].get('gross_pnl_usd'))
        order_values[fill['order_id']].append(value)
    totals = {order: sum(values, Decimal(0)) if None not in values else None for order, values in order_values.items()}
    missing_orders = [order for order, value in totals.items() if value is None]
    if coverage and parent is not None and len(missing_orders) == 1:
        known = sum((value for value in totals.values() if value is not None), Decimal(0))
        totals[missing_orders[0]] = parent - known
    return events, totals
