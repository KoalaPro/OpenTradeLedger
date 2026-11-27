"""Project realized reductions before a venue publishes its final position summary."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Mapping, Sequence

from opentradeledger.core.position_lifecycle import build_position_events
from opentradeledger.core.venue_primitives import dec, fmt_ms


def open_reduction_key(venue: str, symbol: str, side: str, events: Sequence[Mapping]) -> str:
    if not events or events[0].get("action") != "OPEN":
        return ""
    start = events[0]
    return f"{venue}_live_{symbol}_{side.upper()}_{start['time_ms']}_{start.get('order_id') or ''}"


def _leg(pairs: list[tuple[Mapping, dict]], *, entry: bool = False) -> dict:
    fills, events = zip(*pairs)
    qty = sum(dec(e["qty"]) for e in events)
    weighted_price = sum(dec(e["qty"]) * dec(e["price"]) for e in events)
    fee = sum(dec(e["fee_usd"]) for e in events)
    roles = {str(f.get("role") or "UNKNOWN").upper() for f in fills}
    role = next(iter(roles)) if len(roles) == 1 else "MIXED"
    base_units = events[0]["qty_unit"] == "base"
    result = {
        "qty": float(qty) if base_units else None,
        "contract_count": None if base_units else float(qty),
        "avg_price": float(weighted_price / qty),
        "time_ms": events[0 if entry else -1]["time_ms"],
        "gross_fee_usd": float(fee),
        "fee_known": all(e["fee_usd"] is not None for e in events),
        "role": role,
        "mode_code": {"MAKER": "LIMIT_MAKER", "TAKER": "MARKET_TAKER"}.get(role, role),
        "fill_count": len(fills),
        "notional_usd": float(weighted_price) if base_units else None,
        "fee_bps": float(fee * 10000 / weighted_price) if base_units else None,
    }
    for kind in ("maker", "taker"):
        members = [ev for fl, ev in pairs if str(fl.get("role")).lower() == kind]
        result[kind + "_fee_usd"] = float(sum(dec(e["fee_usd"]) for e in members))
        if base_units:
            result[kind + "_notional_usd"] = float(sum(dec(e["qty"]) * dec(e["price"]) for e in members))
    return result


def open_reduction_record(*, venue: str, symbol: str, side: str, fills: Sequence[Mapping],
                          remaining_qty: Any, avg_entry_price: Any, journals: Mapping,
                          history: Sequence[Mapping], linear: bool = True) -> dict | None:
    """Find the current cycle by unwinding the exchange's live position to zero.

    A nonzero balance alone cannot establish the starting inventory. Reject missing
    boundaries, conflicting costs and fills already covered by a history row.
    """
    side = side.upper()
    live_qty = dec(remaining_qty)
    if live_qty <= 0 or not fills:
        return None
    chronological = sorted(fills, key=lambda f: f["time_ms"])
    start = len(chronological)
    prior_qty = live_qty
    while start > 0 and prior_qty > 0:
        start -= 1
        fill = chronological[start]
        qty = dec(fill["qty"])
        if qty <= 0:
            return None
        prior_qty += -qty if fill["is_entry"] else qty
    if prior_qty:
        return None
    cycle = chronological[start:]
    if all(f["is_entry"] for f in cycle):
        return None
    events = build_position_events(cycle, side=side, linear=linear)
    if not events or dec(events[-1]["remaining_qty"]) != live_qty:
        return None
    basis, actual = dec(events[-1]["avg_entry_price"]), dec(avg_entry_price)
    if actual <= 0 or basis <= 0 or abs(actual - basis) > max(actual, basis) * Decimal("0.00001"):
        return None
    key = open_reduction_key(venue, symbol, side, events)
    for existing in history:
        if str(existing.get("symbol") or existing.get("contract")) != symbol or str(existing.get("side")).upper() != side:
            continue
        old_events = existing.get("position_events") or []
        if old_events and open_reduction_key(venue, symbol, side, old_events) == key:
            return None
        if not old_events and int(existing.get("close_time_ms") or 0) >= events[0]["time_ms"]:
            return None

    entries = [(f, e) for f, e in zip(cycle, events) if f["is_entry"]]
    exits = [(f, e) for f, e in zip(cycle, events) if not f["is_entry"]]
    # A provisional total needs actual costs and evidenced realized PnL.
    if any(e["fee_usd"] is None for e in events) or any(e["gross_pnl_usd"] is None for _, e in exits):
        return None
    by_order = defaultdict(list)
    if all(e["order_id"] for _, e in exits):
        for pair in exits:
            by_order[pair[1]["order_id"]].append(pair)
    batches = []
    for idx, pairs in enumerate(by_order.values(), 1):
        batches.append({**_leg(pairs), "batch_index": idx, "order_id": pairs[0][1]["order_id"],
                        "net_basis": "round_trip",
                        "gross_pnl_usd": float(sum(dec(e["gross_pnl_usd"]) for _, e in pairs)),
                        "net_pnl_usd": float(sum(dec(e["net_pnl_usd"]) for _, e in pairs))})
    gross = sum(dec(e["gross_pnl_usd"]) for _, e in exits)
    fee = sum(dec(e["fee_usd"]) for e in events)
    net = float(gross - fee)
    last_close = exits[-1][1]["time_ms"]
    closed_qty = float(sum(dec(e["qty"]) for _, e in exits))
    base_units = events[0]["qty_unit"] == "base"
    note = journals.get(key) or {}
    return {
        "record_key": key, "contract": symbol, "side": side, "open_time_ms": events[0]["time_ms"],
        "open_time_sec": events[0]["time_ms"] / 1000, "close_time_sec": last_close / 1000,
        "close_time_ms": last_close, "close_date_bj": fmt_ms(last_close, "%Y-%m-%d"),
        "qty_base": closed_qty if base_units else None, "contract_count": None if base_units else closed_qty,
        "is_partial_close": True,
        "entry_leg": _leg(entries, entry=True), "exit_leg": _leg(exits), "exit_batches": batches,
        "position_events": events, "gross_pnl_usd": float(gross), "total_fee_usd": float(fee),
        "gate_book_pnl_usd": net, "realized_net_pnl_usd": net, "expected_net_pnl_usd": net,
        "settled_rebate_usd": 0.0, "pending_rebate_usd": 0.0, "funding_pnl_usd": 0.0,
        "tags": list(note.get("tags") or []), "note": note.get("note") or "",
    }


def carry_open_reduction_notes(records: Sequence[dict], venue: str, journals: Mapping) -> None:
    """Read a provisional key only when the final position starts with that same execution."""
    if not journals:
        return
    for row in records:
        primary = row.get("record_key", row.get("trade_id"))
        if primary in journals:
            continue
        alias = open_reduction_key(venue, row.get("contract", row.get("symbol", "")),
                                   row["side"], row.get("position_events") or ())
        previous = journals.get(alias) if alias else None
        if previous:
            row["tags"] = list(dict.fromkeys([*row.get("tags", []), *previous.get("tags", [])]))
            notes = (previous.get("note") or "", row.get("note") or "")
            row["note"] = "\n".join(dict.fromkeys(note for note in notes if note))
