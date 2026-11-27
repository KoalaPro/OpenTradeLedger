"""Evidence shared by venue adapters that reconstruct a position from individual fills."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from opentradeledger.core.open_reductions import open_reduction_key, open_reduction_record
from opentradeledger.core.venue_primitives import fmt_ms


def cycle_journal_keys(keys: Sequence[str], partial: bool) -> dict[str, Any]:
    """A new cycle key can be edited or cleared independently of all legacy keys."""
    return {
        "trade_id": f"{keys[0]}_cycle" if partial else keys[-1],
        "_journal_keys": keys,
    }


def usd_fee(amount: Any, currency: Any) -> float | None:
    """Only stablecoin fees can be represented directly as USD without a conversion."""
    if amount in (None, ""):
        return None
    value = float(amount)
    if value == 0 or str(currency).upper() in {"USD", "USDT", "USDC"}:
        return value
    return None


def event_fill(
    time_ms: int, qty: Any, price: Any, is_entry: bool, fee_usd: Any, order_id: str,
    *, gross: Any = None, qty_unit: str = "base", role: str = "", linear_cost_basis: bool = False,
) -> dict[str, Any]:
    return {
        "time_ms": time_ms, "qty": qty, "price": price, "is_entry": is_entry,
        "fee_usd": fee_usd, "order_id": order_id, "gross_pnl_usd": gross, "qty_unit": qty_unit,
        "role": role, "linear_cost_basis": linear_cost_basis,
    }


def project_live_reduction(
    *, venue: str, symbol: str, side: str, fills: Sequence[Mapping[str, Any]],
    remaining_qty: Any, avg_entry_price: Any, journals: Mapping[str, Mapping[str, Any]],
    history: list[dict[str, Any]], linear: bool = True,
) -> dict[str, Any] | None:
    """Extend a partial native statement only when it covers a prefix of this verified live cycle."""
    raw = open_reduction_record(
        venue=venue, symbol=symbol, side=side, fills=fills, remaining_qty=remaining_qty,
        avg_entry_price=avg_entry_price, journals=journals, history=[], linear=linear,
    )
    if raw is None:
        return None
    events = raw["position_events"]
    overlaps = [
        t for t in history if (t.get("symbol") or t.get("contract")) == symbol and t["side"] == side and
        t["close_time_ms"] >= raw["open_time_ms"]
    ]
    if not overlaps:
        return raw
    if len(overlaps) != 1:
        return None
    native = overlaps[0]
    prefix = native.get("position_events") or []
    if (not prefix or not prefix[-1]["remaining_qty"] or
            open_reduction_key(venue, symbol, side, prefix) != raw["record_key"]):
        return None
    # Match quantities and prices as well as order/time so an unrelated or
    # corrected historical fill cannot cause a duplicate settlement.
    fields = ("time_ms", "action", "order_id", "qty", "price", "qty_unit")
    if len(events) <= len(prefix) or any(
        any(a[k] != b[k] for k in fields) for a, b in zip(prefix, events)
    ):
        return None
    tail = events[len(prefix):]
    if not any(e["action"] == "REDUCE" for e in tail):
        native["position_events"] = prefix + tail
        return None

    covered = [e for e in prefix if e["action"] == "REDUCE"]
    if not covered or any(e.get("net_pnl_usd") is None for e in covered):
        return None
    # The provisional parent includes all paid entry fees. Replace only the
    # already settled fraction with the venue statement, retaining funding
    # and any adjudicated fees, discounts or rebates exactly once.
    native_book = native.get("book_pnl_usd", native.get("gate_book_pnl_usd", 0.0))
    book_delta = native_book - sum(e["net_pnl_usd"] for e in covered)
    raw["gate_book_pnl_usd"] += book_delta
    native_day = fmt_ms(native["close_time_ms"], "%Y-%m-%d")
    dated_funding = sum((native.get("funding_by_day") or {}).values())
    # Native book excludes rebates. Funding with its own receipt date is posted
    # separately; any remaining statement adjustment belongs to this native close.
    raw["settlements_by_day"] = {native_day: book_delta - dated_funding}
    raw["gross_pnl_usd"] += native["gross_pnl_usd"] - sum(e["gross_pnl_usd"] for e in covered)
    raw["funding_pnl_usd"] = native.get("funding_pnl_usd", 0.0)
    for field in ("funding_by_day", "rebates_by_day", "total_discount_saved_usd"):
        if field in native:
            raw[field] = native[field]
    for leg_key, fee_key in (("entry_leg", "allocated_entry_fee_usd"), ("exit_leg", "fee_usd")):
        fee_delta = native[leg_key]["gross_fee_usd"] - sum(e[fee_key] for e in covered)
        raw[leg_key]["gross_fee_usd"] += fee_delta
        raw["total_fee_usd"] += fee_delta
        leg_rebate = native[leg_key].get("rebate_usd", native[leg_key].get("settled_rebate_usd", 0.0))
        raw[leg_key]["settled_rebate_usd"] = leg_rebate
    # Keep the exchange's per-order settlement where the history row identifies it.
    settled_batches = native.get("exit_batches") or []
    covered_orders = {e["order_id"] for e in covered}
    if not settled_batches and len(covered_orders) == 1:
        settled_batches = [{**native["exit_leg"], "order_id": covered[0]["order_id"],
                            "gross_pnl_usd": native["gross_pnl_usd"]}]
    for settled in settled_batches:
        oid = settled.get("order_id")
        if not oid:
            continue
        subset = [e for e in covered if e["order_id"] == oid]
        batch = next((b for b in raw["exit_batches"] if b.get("order_id") == oid), None)
        if not subset or not batch:
            continue
        gross_change = (settled["gross_pnl_usd"] - sum(e["gross_pnl_usd"] for e in subset)
                        if settled.get("gross_pnl_usd") is not None else 0)
        exit_fee_change = settled["gross_fee_usd"] - sum(e["fee_usd"] for e in subset)
        batch["gross_pnl_usd"] += gross_change
        batch["gross_fee_usd"] += exit_fee_change
        batch["net_pnl_usd"] += gross_change - exit_fee_change
        if len(covered_orders) == 1:
            batch["net_pnl_usd"] -= native["entry_leg"]["gross_fee_usd"] - sum(e["allocated_entry_fee_usd"] for e in covered)
        batch["settled_rebate_usd"] = settled.get("rebate_usd", settled.get("settled_rebate_usd", 0.0))
    raw["settled_rebate_usd"] = native.get("settled_rebate_usd", native.get("total_rebate_usd", 0.0))
    raw["pending_rebate_usd"] = native.get("pending_rebate_usd", 0.0)
    raw["realized_net_pnl_usd"] = raw["gate_book_pnl_usd"] + raw["settled_rebate_usd"]
    raw["expected_net_pnl_usd"] = raw["realized_net_pnl_usd"] + raw["pending_rebate_usd"]

    # Retain the native journal key and its saved overrides while the statement catches up.
    raw["_journal_keys"] = [*native.get("_journal_keys", ()), raw["record_key"]]
    raw["record_key"] = native.get("trade_id") or native["record_key"]
    if raw["record_key"] in journals or native.get("note") or native.get("tags"):
        raw["note"] = native.get("note", "")
        raw["tags"] = native.get("tags", [])
    history.remove(native)
    return raw
