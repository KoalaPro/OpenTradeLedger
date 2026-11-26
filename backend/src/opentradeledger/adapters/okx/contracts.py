"""OKX SWAP amounts in their exchange-reported denominations."""

from __future__ import annotations

from math import isfinite
from typing import Any

from opentradeledger.core.venue_primitives import USD_CASH_ASSETS


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def positive_number(value: Any) -> float:
    return max(0.0, finite_number(value) or 0.0)


def contract_quantity(spec: dict, count: float, price: float) -> float | None:
    """Base currency represented by this count at this price (inverse contracts vary)."""
    face = positive_number(spec.get("ctVal"))
    inst = str(spec.get("instId") or "").upper()
    ccy = str(spec.get("ctValCcy") or "").upper()
    kind = spec.get("ctType")
    if not face or not inst or not ccy:
        return None
    if ccy == inst.split("-")[0] and kind in (None, "", "linear"):
        return count * face
    if ccy == "USD" and price > 0 and kind in (None, "", "inverse"):
        return count * face / price
    return None


def instrument_at(instruments: list[dict], inst_id: str, timestamp: int) -> dict:
    # A relisting may reuse the symbol but change its contract specification.
    selected = {}
    latest_listing = -1
    for spec in instruments:
        if spec.get("instId") != inst_id:
            continue
        try:
            listed = int(spec.get("listTime") or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        if listed >= 0 and (not timestamp or listed <= timestamp) and listed > latest_listing:
            selected, latest_listing = spec, listed
    return selected


def fill_notional(fill: dict, spec: dict) -> float | None:
    explicit = positive_number(fill.get("fillNotionalUsd"))
    if explicit and (not fill.get("accFillSz") or positive_number(fill["accFillSz"]) == positive_number(fill.get("fillSz"))):
        return explicit
    count = positive_number(fill.get("fillSz"))
    price = positive_number(fill.get("fillPx"))
    qty = contract_quantity(spec, count, price)
    if qty is None or not count:
        return None
    quote = spec["instId"].split("-")[1].split("_")[0]
    if price and quote in USD_CASH_ASSETS:
        return qty * price
    return None


def amount_usd(amount: float, ccy: str, inst_id: str, price: float, okb_price: float) -> float | None:
    if not amount or ccy in USD_CASH_ASSETS:
        return amount
    if ccy == inst_id.split("-")[0] and price > 0:
        return amount * price
    if ccy == "OKB" and okb_price > 0:
        return amount * okb_price
    return None


def build_fill_leg(
    fills: list[dict],
    spec: dict,
    inst: str,
    at_ms: int,
    avg_price: float,
    okb_price: float,
    *,
    scale: float = 1.0,
) -> dict:
    eff_scale = scale if (0.0 < scale < 1.0) else 1.0
    fees, credits, discount = 0.0, 0.0, 0.0
    notionals: list[float | None] = []
    role_samples: dict[str, dict[str, float]] = {}
    roles = set()
    fee_known = bool(fills)
    for fill in fills:
        role = {"M": "MAKER", "T": "TAKER"}.get(fill.get("execType"), "UNKNOWN")
        roles.add(role)
        ccy = str(fill.get("feeCcy") or spec.get("settleCcy") or inst.split("-")[1]).upper()
        signed_fee = finite_number(fill.get("fee"))
        fee = (amount_usd(signed_fee, ccy, inst, positive_number(fill.get("fillPx")), okb_price)
               if signed_fee is not None else None)
        raw_notional = fill_notional(fill, spec)
        notional = (raw_notional * eff_scale) if raw_notional is not None else None
        notionals.append(notional)
        if fee is None:
            fee_known = False
            continue
        cost, credit = max(0.0, -fee) * eff_scale, max(0.0, fee) * eff_scale
        fees += cost
        credits += credit
        if ccy == "OKB":
            discount += cost * 0.25
        if notional and role in {"MAKER", "TAKER"}:
            sample = role_samples.setdefault(role, {"notional": 0.0, "paid": 0.0, "credit": 0.0})
            sample["notional"] += notional
            sample["paid"] += cost
            sample["credit"] += credit

    role = next(iter(roles)) if len(roles) == 1 else "UNKNOWN"
    notional = sum(n for n in notionals if n is not None) if notionals and all(notionals) else None
    mode_code = (
        "MIXED"
        if ("MAKER" in roles and "TAKER" in roles)
        else {"MAKER": "LIMIT_MAKER", "TAKER": "MARKET_TAKER"}.get(role, "UNKNOWN")
    )
    return {
        "role": role, "fee_known": fee_known,
        "mode_code": mode_code,
        "fee_bps": round((fees - credits) / notional * 10000, 2) if fee_known and notional else None,
        "time_ms": at_ms, "avg_price": avg_price, "notional_usd": notional, "role_samples": role_samples,
        "gross_fee_usd": fees, "fill_rebate_usd": credits, "discount_saved_usd": discount,
        "discount_asset": "OKB" if discount else "",
    }
