"""Inventory and dated cash movements for one account, instrument and position side."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from opentradeledger.core.venue_primitives import fmt_ms

ZERO = Decimal(0)


def _number(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def build_position_events(
    fills: Sequence[Mapping[str, Any]], *, side: str, linear: bool = True
) -> list[dict[str, Any]]:
    """Replay one continuous position. Equal timestamps keep the venue's execution order.

    Quantities are absolute. Fee is a signed cost in USD and may be unknown.
    Gross realized PnL supplied by the venue takes precedence over linear math.
    """
    if side.upper() not in {"LONG", "SHORT"}:
        return []
    direction = 1 if side.upper() == "LONG" else -1
    holding = ZERO
    average = None
    remaining_fees: Decimal | None = ZERO
    result = []
    for fill in sorted(fills, key=lambda f: _number(f.get("time_ms")) or ZERO):
        qty, price, ts = (_number(fill.get(k)) for k in ("qty", "price", "time_ms"))
        if not qty or qty <= 0 or not price or price <= 0 or not ts or ts <= 0:
            return []
        if result and not holding:
            return []  # Adjacent lifecycles must first be partitioned by the venue.
        fee = _number(fill.get("fee_usd"))
        basis = average
        allocated = gross = net = None
        if fill["is_entry"]:
            action = "ADD" if holding else "OPEN"
            if not holding or basis == price:
                average = price
            elif (linear or fill.get("linear_cost_basis")) and basis is not None:
                average = (holding * basis + qty * price) / (holding + qty)
            else:
                average = None  # Inverse/unknown contract conventions need venue evidence.
            remaining_fees = remaining_fees + fee if remaining_fees is not None and fee is not None else None
            holding += qty
        else:
            if qty > holding:
                return []
            gross = _number(fill.get("gross_pnl_usd"))
            if gross is None and linear and basis is not None:
                gross = (price - basis) * qty * direction
            if remaining_fees is not None:
                allocated = remaining_fees * qty / holding
                remaining_fees -= allocated
            if gross is not None and fee is not None and allocated is not None:
                net = gross - fee - allocated
            holding -= qty
            action = "REDUCE" if holding else "CLOSE"
            if not holding:
                average = None
        values = {"qty": qty, "price": price, "remaining_qty": holding, "avg_entry_price": average,
                  "cost_basis_price": basis, "fee_usd": fee, "gross_pnl_usd": gross,
                  "allocated_entry_fee_usd": allocated, "net_pnl_usd": net}
        execution_terms = {}
        role = str(fill.get("role") or "").upper()
        if role in {"MAKER", "TAKER"}:
            execution_terms["role"] = role
        if fill.get("rebate_status") in {"PENDING", "SETTLED"}:
            execution_terms["rebate_status"] = fill["rebate_status"]
        for key in ("fee_bps", "rebate_usd", "net_fee_usd"):
            amount = _number(fill.get(key))
            if amount is not None:
                execution_terms[key] = float(amount)
        result.append({
            **execution_terms,
            "action": action, "time_ms": int(ts), "order_id": str(fill.get("order_id") or ""),
            "qty_unit": fill.get("qty_unit") or "base",
            **{k: float(v) if v is not None else None for k, v in values.items()},
        })
    return result


def _leg_time(leg: Mapping[str, Any]) -> int:
    stamp = _number(leg.get("time_ms"))
    if stamp:
        return int(stamp)
    return int((_number(leg.get("time_sec")) or ZERO) * 1000)


def dated_cash(items: Sequence[tuple[int, Any]]) -> dict[str, float]:
    """Group only cash receipts with an actual transaction timestamp."""
    days: dict[str, Decimal] = defaultdict(Decimal)
    for time_ms, amount in items:
        value = _number(amount)
        if time_ms and value:
            days[fmt_ms(time_ms, "%Y-%m-%d")] += value
    return {day: float(amount) for day, amount in days.items()}


def trade_daily_amounts(trade: Mapping[str, Any], total: Decimal, *, include_rebates: bool = True) -> dict[str, Decimal]:
    """Date known cash amounts; keep unattributed settlement residual at the reported close.

    The caller provides the amount already included in its ledger (e.g. actual net,
    book net, excluding rebates reported separately). Never sum both parent and fills.
    """
    close_day = trade.get("close_date") or trade.get("close_date_bj")
    if not close_day or trade.get("net_pnl_known") is False:
        return {}
    amounts: dict[str, Decimal] = defaultdict(Decimal)
    events = trade.get("position_events") or ()
    if trade.get("statement_net_by_day") is not None:
        # Unpriced fills cannot split a verified settlement across days.
        for day, amount in trade["statement_net_by_day"].items():
            amounts[day] += Decimal(str(amount))
    elif events:
        # Some closed-PnL endpoints charge only the consumed share of the entry fee
        # to their partial statement. The wallet paid the remaining share at entry.
        if events[-1]["remaining_qty"] and (trade.get("entry_leg") or {}).get("fee_known", True):
            entry_fees = [_number(e["fee_usd"]) for e in events if e["action"] in {"OPEN", "ADD"}]
            allocated = [_number(e["allocated_entry_fee_usd"]) for e in events if e["action"] == "REDUCE"]
            reported_fee = _number((trade.get("entry_leg") or {}).get("gross_fee_usd"))
            if (reported_fee is not None and entry_fees and allocated and None not in allocated
                    and None not in entry_fees and all(fee >= 0 for fee in entry_fees)
                    and abs(sum(allocated) - reported_fee) <= Decimal("0.00005")):
                total -= sum(entry_fees) - reported_fee
        for event in events:
            delta = (_number(event.get("gross_pnl_usd")) or ZERO) - (_number(event.get("fee_usd")) or ZERO)
            amounts[fmt_ms(event["time_ms"], "%Y-%m-%d")] += delta
    elif trade.get("exit_batches"):
        # An average entry leg can span several days. Move only dated exit amounts.
        for batch in trade["exit_batches"]:
            stamp = _leg_time(batch)
            if stamp > 0:
                fee = _number(batch.get("gross_fee_usd")) if batch.get("fee_known", True) else None
                delta = (_number(batch.get("gross_pnl_usd")) or ZERO) - (fee or ZERO)
                amounts[fmt_ms(stamp, "%Y-%m-%d")] += delta
    dated_components = ["funding_by_day", "settlements_by_day"]
    if include_rebates:
        dated_components.append("rebates_by_day")
    for key in dated_components:
        for day, value in (trade.get(key) or {}).items():
            amounts[day] += Decimal(str(value))
    amounts[close_day] += total - sum(amounts.values(), ZERO)
    return dict(amounts)


def exit_counts_by_day(trade: Mapping[str, Any]) -> dict[str, int]:
    """Count distinct closing orders on the day they actually filled."""
    events = trade.get("position_events") or ()
    groups: dict[str, set[str | int]] = defaultdict(set)
    if events:
        for index, ev in enumerate(events):
            if ev["action"] in {"REDUCE", "CLOSE"}:
                groups[fmt_ms(ev["time_ms"], "%Y-%m-%d")].add(ev.get("order_id") or index)
    else:
        for index, batch in enumerate(trade.get("exit_batches") or ()):
            stamp = _leg_time(batch)
            if stamp:
                groups[fmt_ms(stamp, "%Y-%m-%d")].add(index)
    if not events and not groups and trade.get("close_date"):
        return {trade["close_date"]: 1}
    return {day: len(orders) for day, orders in groups.items()}


def align_exit_batch_net(trade: dict) -> None:
    """Attach round-trip net only to a complete, positively identified closing order."""
    batches = trade.get("exit_batches") or ()
    if not batches:
        return
    by_order = defaultdict(list)
    for event in trade.get("position_events") or ():
        if event.get("order_id") and event["action"] in ("REDUCE", "CLOSE"):
            by_order[event["order_id"]].append(event)
    for batch in batches:
        if batch.get("net_basis") == "round_trip":
            continue
        batch["net_basis"] = "exit_only"
        events = by_order.get(batch.get("order_id"))
        if not events or any(e["net_pnl_usd"] is None for e in events):
            continue
        qty_key = "contract_count" if events[0].get("qty_unit") == "contracts" else "qty"
        qty = _number(batch.get(qty_key))
        actual_qty = sum(Decimal(str(e["qty"])) for e in events)
        if qty is not None and abs(actual_qty - qty) > max(Decimal("0.0000005"), qty * Decimal("1e-8")):
            continue
        batch_gross = _number(batch.get("gross_pnl_usd"))
        gross = sum(Decimal(str(e["gross_pnl_usd"])) for e in events)
        if batch_gross is None or abs(batch_gross - gross) > Decimal("0.0001"):
            continue
        batch["net_basis"] = "round_trip"
        batch["net_pnl_usd"] = round(float(sum(Decimal(str(e["net_pnl_usd"])) for e in events)), 8)
