"""Independent actuarial accounting engine for EasiCoin (`venue_id = 'easicoin'`)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from opentradeledger.adapters.cycle_evidence import event_fill, project_live_reduction, usd_fee
from opentradeledger.core.open_reductions import open_reduction_key
from opentradeledger.core.position_lifecycle import build_position_events, trade_daily_amounts
from opentradeledger.adapters.base import (
    AdapterMeta,
    VenueCopyBundle,
    VenueLedgerBundle,
    VenueStockBundle,
)
from opentradeledger.adapters.stock_channels import (
    classify_spot_stock_token,
    reconstruct_spot_cost_basis,
)
from opentradeledger.core.venue_primitives import (
    STOCK_COMMODITY_BASES,
    USD_CASH_ASSETS,
    aggregate_trade_legs_decimal,
    build_spot_wallet_balances,
    spot_holdings_from_wallets,
    build_venue_stock_perpetuals,
    clamp_reconcile_streams_to_target,
    dec,
    f2,
    fmt_bj,
    format_duration_zh,
    merge_legacy_journal_entries,
    norm_ms,
    norm_sec,
    normalize_contract_symbol,
    normalize_internal_record_to_public_trade,
)

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

EC_PENDING_REBATE_WINDOW_SEC = 129_600


def _build_ec_price_map(tickers: Any) -> dict[str, Decimal]:
    px_map: dict[str, Decimal] = {
        "USDT": Decimal("1"),
        "USDC": Decimal("1"),
        "USD1": Decimal("1"),
        "USD": Decimal("1"),
    }
    if isinstance(tickers, Mapping):
        items = [{"symbol": k, "last_price": v} for k, v in tickers.items()]
    elif isinstance(tickers, Sequence):
        items = [x for x in tickers if isinstance(x, Mapping)]
    else:
        items = []

    for tk in items:
        sym = str(tk.get("symbol") or "").upper()
        lp = dec(tk.get("last_price"))
        if lp <= 0:
            continue
        if sym.endswith("USDT") and len(sym) > 4:
            base = sym[:-4]
        else:
            base = sym
        for pfx in ("1000000", "100000", "10000", "1000", "100"):
            if base.startswith(pfx) and len(base) > len(pfx):
                px_map.setdefault(base[len(pfx):], lp / Decimal(pfx))
                break
        px_map[base] = lp
    return px_map


def extract_easicoin_spot_prices(store: "LedgerStore") -> dict[str, float]:
    lb = store.venue_scope("easicoin").get_kv("live_bundle", {}) or {}
    if not isinstance(lb, dict):
        return {}
    px_map = _build_ec_price_map(lb.get("tickers"))
    return {k: float(v) for k, v in px_map.items() if v > 0 and k not in ("USDT", "USDC", "USD1", "USD")}


def _classify_ec_leg_mode(
    roles: set[str],
    *,
    is_exit: bool,
    stop_order_type: str = "",
) -> tuple[str, str]:
    st = (stop_order_type or "").strip()
    if is_exit and st in ("StopLoss", "Stop"):
        return "STOP_MARKET_TAKER", "止损市价"
    if is_exit and st == "TakeProfit":
        return "MARKET_TAKER", "止盈市价"
    if roles == {"maker"}:
        return "LIMIT_MAKER", "限价挂单"
    if "maker" in roles and "taker" in roles:
        return "MIXED", "混合成交"
    if roles == {"taker"}:
        return "MARKET_TAKER", "市价吃单"
    return ("MARKET_TAKER", "市价吃单") if not roles else ("MIXED", "混合成交")


def _easicoin_cycles(
    fills: Sequence[Mapping[str, Any]], orders: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]]]:
    """Index close orders by the nonzero inventory interval they actually reduced."""
    active: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_close: dict[tuple[str, str], dict[str, Any]] = {}
    ordered = sorted(fills, key=lambda f: (norm_ms(f.get("exec_time")), str(f.get("exec_id") or "")))
    for index, f in enumerate(ordered):
        if f.get("exec_type") != "Trade":
            continue
        qty = abs(dec(f.get("exec_qty")))
        closed = abs(dec(f.get("closed_size")))
        token = str(f.get("side") or "").lower()
        if not qty or token not in {"buy", "sell"}:
            continue
        symbol = str(f.get("symbol") or "").upper()
        oid = str(f.get("order_id") or "")
        link = str(f.get("pz_link_id") or (orders.get(oid) or {}).get("pz_link_id") or "")
        entry_side = "long" if token == "buy" else "short"
        close_side = "short" if entry_side == "long" else "long"
        ts = norm_ms(f.get("exec_time"))
        px = f.get("exec_price")
        fee = usd_fee(f.get("exec_fee"), f.get("fee_currency") or f.get("exec_fee_currency") or "USDT")
        role = str(f.get("liquidity_type") or "").lower()
        if closed:
            bucket = (symbol, close_side, link)
            cycle = active.get(bucket)
            if cycle and closed <= qty and closed <= cycle["remaining"]:
                cycle["fills"].append(event_fill(ts, closed, px, False,
                                                fee * float(closed / qty) if fee is not None else None, oid,
                                                role=role, gross=f.get("exec_pnl")))
                take = closed / cycle["remaining"]
                allocations = cycle["allocation"].setdefault(oid, {})
                for idx, slot in enumerate(cycle["entries"]):
                    entry, remaining_share = slot
                    consumed = take * remaining_share
                    if consumed:
                        previous = allocations.get(idx, (entry, Decimal("0")))[1]
                        allocations[idx] = (entry, previous + consumed)
                        slot[1] -= consumed
                cycle["remaining"] -= closed
                cycle["closed"] += closed
                if oid:
                    by_close[(symbol, oid)] = cycle
                if not cycle["remaining"]:
                    active.pop(bucket)
            else:
                active.pop(bucket, None)
        if qty > closed:
            opened = qty - closed
            cycle = active.setdefault((symbol, entry_side, link), {
                "anchor": f"fills_{index}", "entries": [], "fills": [], "allocation": {},
                "remaining": Decimal("0"), "closed": Decimal("0"),
            })
            cycle["remaining"] += opened
            cycle["entries"].append([f, opened / qty])
            cycle["fills"].append(event_fill(ts, opened, px, True,
                                            fee * float(opened / qty) if fee is not None else None, oid, role=role))
    return by_close, active


def _build_easicoin_closed_records(
    *,
    closed_pnl_rows: Sequence[Mapping[str, Any]],
    fills: Sequence[Mapping[str, Any]],
    orders: Sequence[Mapping[str, Any]],
    journals: Mapping[str, Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:

    order_map: dict[str, Mapping[str, Any]] = {
        str(o.get("order_id")): o for o in orders if o.get("order_id")
    }
    cycles_by_order, active = _easicoin_cycles(fills, order_map)
    exit_fills_by_order: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    entry_fills_by_pz: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    unlinked_entries_by_sym: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)

    for idx, f in enumerate(fills):
        et = str(f.get("exec_type") or "")
        oid = str(f.get("order_id") or "").strip()
        pz = str(f.get("pz_link_id") or "").strip()
        sym = str(f.get("symbol") or "").strip().upper()
        csz = abs(dec(f.get("closed_size")))
        if et != "Trade":
            continue
        if oid and csz > 0:
            exit_fills_by_order[oid].append(f)
        elif csz == 0:
            if pz:
                entry_fills_by_pz[pz].append(f)
            elif sym:
                unlinked_entries_by_sym[sym].append((idx, f))

    for sym_list in unlinked_entries_by_sym.values():
        sym_list.sort(key=lambda pair: norm_sec(pair[1].get("exec_time")))

    def _effective_close_sec(row: Mapping[str, Any]) -> float:
        oid = str(row.get("order_id") or "").strip()
        efs = exit_fills_by_order.get(oid) or []
        if efs:
            return max(norm_sec(ef.get("exec_time")) for ef in efs)
        return norm_sec(row.get("created_at"))

    unlinked_pool_by_sym: dict[str, list[list[Any]]] = {
        sym_k: [[u_idx, uf, Decimal("1")] for u_idx, uf in sym_list]
        for sym_k, sym_list in unlinked_entries_by_sym.items()
    }
    out: list[dict[str, Any]] = []

    for c_row in sorted(closed_pnl_rows, key=_effective_close_sec):
        raw_sym = str(c_row.get("symbol") or "").strip().upper()
        contract = normalize_contract_symbol(raw_sym)
        oid = str(c_row.get("order_id") or "").strip()
        close_side_raw = str(c_row.get("side") or "Sell").strip().capitalize()
        pos_side = "short" if close_side_raw == "Buy" else "long"
        qty_base = abs(dec(c_row.get("closed_size") or c_row.get("qty")))
        avg_entry_px = dec(c_row.get("avg_entry_price"))
        avg_exit_px = dec(c_row.get("avg_exit_price"))
        cum_entry_val = dec(c_row.get("cum_entry_value")) or (qty_base * avg_entry_px)
        cum_exit_val = dec(c_row.get("cum_exit_value")) or (qty_base * avg_exit_px)
        closed_pnl = dec(c_row.get("closed_pnl"))

        efs = exit_fills_by_order.get(oid) or []
        t_close = _effective_close_sec(c_row)
        ord_obj = order_map.get(oid) or {}
        pz_id = ""
        for ef in efs:
            if ef.get("pz_link_id"):
                pz_id = str(ef.get("pz_link_id")).strip()
                break
        if not pz_id and ord_obj.get("pz_link_id"):
            pz_id = str(ord_obj.get("pz_link_id")).strip()

        entry_gross_fee = Decimal("0")
        entry_maker_fee = Decimal("0")
        entry_maker_notional = Decimal("0")
        entry_taker_notional = Decimal("0")
        entry_roles: set[str] = set()
        entry_fill_count = 0
        t_open = t_close
        unlinked_anchor = ""

        cycle = cycles_by_order.get((raw_sym, oid))
        matched_slices: list[tuple[Mapping[str, Any], Decimal]] = []
        if cycle:
            matched_slices = list(cycle["allocation"].get(oid, {}).values())
        elif pz_id and entry_fills_by_pz.get(pz_id):
            pz_entries = entry_fills_by_pz[pz_id]
            tot_pz_qty = sum((abs(dec(ef.get("exec_qty"))) for ef in pz_entries), Decimal("0"))
            share = min(Decimal("1"), qty_base / tot_pz_qty) if tot_pz_qty > 0 else Decimal("1")
            matched_slices = [(ef, share) for ef in pz_entries]
        else:
            want_entry_side = "Sell" if pos_side == "short" else "Buy"
            rem_need = qty_base
            anchor_ids: list[str] = []
            for slot in unlinked_pool_by_sym.get(raw_sym, []):
                u_idx, uf, rem_sh = slot[0], slot[1], slot[2]
                if rem_sh <= Decimal("1e-9"):
                    continue
                uf_sec = norm_sec(uf.get("exec_time"))
                if uf_sec > t_close + 5.0:
                    continue
                if str(uf.get("side") or "").capitalize() != want_entry_side:
                    continue
                raw_q = abs(dec(uf.get("exec_qty")))
                avail_q = raw_q * rem_sh
                if avail_q <= Decimal("1e-12"):
                    slot[2] = Decimal("0")
                    continue
                if rem_need > 0 and avail_q > rem_need + Decimal("1e-8"):
                    take_sh = rem_sh * (rem_need / avail_q)
                    slot[2] = max(Decimal("0"), rem_sh - take_sh)
                    rem_need = Decimal("0")
                else:
                    take_sh = rem_sh
                    slot[2] = Decimal("0")
                    rem_need = max(Decimal("0"), rem_need - avail_q)
                matched_slices.append((uf, take_sh))
                anchor_ids.append(str(uf.get("order_id") or u_idx))
                if rem_need <= Decimal("1e-8"):
                    break
            if matched_slices:
                unlinked_anchor = f"ul_{raw_sym}_{anchor_ids[0]}"
        if matched_slices:
            t_open = min(norm_sec(uf.get("exec_time")) for uf, _ in matched_slices)
            entry_fill_count = len(matched_slices)
            for uf, take_sh in matched_slices:
                f_fee = abs(dec(uf.get("exec_fee"))) * take_sh
                f_val = (abs(dec(uf.get("exec_value"))) or (abs(dec(uf.get("exec_qty"))) * dec(uf.get("exec_price")))) * take_sh
                liq = str(uf.get("liquidity_type") or "taker").lower()
                entry_roles.add(liq)
                entry_gross_fee += f_fee
                if liq == "maker":
                    entry_maker_fee += f_fee
                    entry_maker_notional += f_val
                else:
                    entry_taker_notional += f_val

        exit_gross_fee = Decimal("0")
        exit_maker_fee = Decimal("0")
        exit_maker_notional = Decimal("0")
        exit_taker_notional = Decimal("0")
        exit_roles: set[str] = set()
        for ef in efs:
            f_fee = abs(dec(ef.get("exec_fee")))
            f_val = abs(dec(ef.get("exec_value"))) or (abs(dec(ef.get("exec_qty"))) * dec(ef.get("exec_price")))
            liq = str(ef.get("liquidity_type") or "taker").lower()
            exit_roles.add(liq)
            exit_gross_fee += f_fee
            if liq == "maker":
                exit_maker_fee += f_fee
                exit_maker_notional += f_val
            else:
                exit_taker_notional += f_val

        gross_pnl = (cum_entry_val - cum_exit_val) if pos_side == "short" else (cum_exit_val - cum_entry_val)
        has_any_fills = bool(efs) or entry_fill_count > 0
        if has_any_fills:
            total_fee = entry_gross_fee + exit_gross_fee
            funding_pnl = closed_pnl - (gross_pnl - total_fee)
        else:
            inferred_fee = max(Decimal("0"), gross_pnl - closed_pnl)
            entry_gross_fee = inferred_fee / Decimal("2")
            exit_gross_fee = inferred_fee - entry_gross_fee
            total_fee = inferred_fee
            funding_pnl = closed_pnl - (gross_pnl - total_fee)
            entry_fill_count = 1
            entry_taker_notional = cum_entry_val
            exit_taker_notional = cum_exit_val

        if (entry_maker_notional + entry_taker_notional) <= 0 and cum_entry_val > 0:
            if entry_roles == {"maker"}:
                entry_maker_notional = cum_entry_val
            else:
                entry_taker_notional = cum_entry_val
        if (exit_maker_notional + exit_taker_notional) <= 0 and cum_exit_val > 0:
            if exit_roles == {"maker"}:
                exit_maker_notional = cum_exit_val
            else:
                exit_taker_notional = cum_exit_val

        entry_taker_fee = max(Decimal("0"), entry_gross_fee - entry_maker_fee)
        exit_taker_fee = max(Decimal("0"), exit_gross_fee - exit_maker_fee)

        stop_type = str(ord_obj.get("stop_order_type") or "")
        entry_mode, entry_mode_zh = _classify_ec_leg_mode(entry_roles, is_exit=False)
        exit_mode, exit_mode_zh = _classify_ec_leg_mode(exit_roles, is_exit=True, stop_order_type=stop_type)

        entry_bps = (entry_gross_fee / cum_entry_val * Decimal("10000")) if cum_entry_val > 0 else Decimal("0")
        exit_bps = (exit_gross_fee / cum_exit_val * Decimal("10000")) if cum_exit_val > 0 else Decimal("0")

        entry_leg = {
            "mode": entry_mode,
            "mode_zh": entry_mode_zh,
            "fill_count": max(1, entry_fill_count),
            "qty_base": f2(qty_base, 6),
            "avg_price": f2(avg_entry_px, 6),
            "notional_usd": f2(cum_entry_val, 2),
            "maker_notional_usd": f2(entry_maker_notional, 2),
            "taker_notional_usd": f2(entry_taker_notional, 2),
            "time_sec": int(t_open),
            "time_bj": fmt_bj(t_open, "%m-%d %H:%M:%S") if t_open > 0 else "—",
            "gross_fee_usd": f2(entry_gross_fee, 4),
            "maker_fee_usd": f2(entry_maker_fee, 4),
            "taker_fee_usd": f2(entry_taker_fee, 4),
            "maker_rebate_usd": 0.0,
            "taker_rebate_usd": 0.0,
            "fee_bps": f2(entry_bps, 2),
            "settled_rebate_usd": 0.0,
            "pending_rebate_usd": 0.0,
            "net_fee_usd": f2(entry_gross_fee, 4),
            "rebate_pct": 0.0,
            "rebate_status": "NONE",
            "rebate_settled_bj": "",
        }
        exit_leg = {
            "mode": exit_mode,
            "mode_zh": exit_mode_zh,
            "fill_count": max(1, len(efs)),
            "qty_base": f2(qty_base, 6),
            "avg_price": f2(avg_exit_px, 6),
            "notional_usd": f2(cum_exit_val, 2),
            "maker_notional_usd": f2(exit_maker_notional, 2),
            "taker_notional_usd": f2(exit_taker_notional, 2),
            "time_sec": int(t_close),
            "time_bj": fmt_bj(t_close, "%m-%d %H:%M:%S") if t_close > 0 else "—",
            "gross_fee_usd": f2(exit_gross_fee, 4),
            "maker_fee_usd": f2(exit_maker_fee, 4),
            "taker_fee_usd": f2(exit_taker_fee, 4),
            "maker_rebate_usd": 0.0,
            "taker_rebate_usd": 0.0,
            "fee_bps": f2(exit_bps, 2),
            "settled_rebate_usd": 0.0,
            "pending_rebate_usd": 0.0,
            "net_fee_usd": f2(exit_gross_fee, 4),
            "rebate_pct": 0.0,
            "rebate_status": "NONE",
            "rebate_settled_bj": "",
        }

        record_key = f"ec_fut_{oid}" if oid else f"ec_fut_{contract}_{int(t_close)}"
        j_entry = journals.get(record_key) or {}
        hold_sec = max(0, int(t_close - t_open))

        out.append(
            {
                "record_key": record_key,
                "exchange": "EASICOIN",
                "exchange_zh": "易币",
                "contract": contract,
                "side": pos_side.upper(),
                "is_partial_close": False,
                "_cycle": cycle,
                "_close_order_id": oid,
                "_group_anchor": cycle["anchor"] if cycle else pz_id or unlinked_anchor,
                "_raw_qty": qty_base,
                "_raw_entry_val": cum_entry_val,
                "_raw_exit_val": cum_exit_val,
                "_raw_gross_pnl": gross_pnl,
                "_raw_funding_pnl": funding_pnl,
                "qty_base": f2(qty_base, 6),
                "open_time_sec": int(t_open),
                "close_time_sec": int(t_close),
                "open_time_ms": norm_ms(t_open),
                "close_time_ms": norm_ms(t_close),
                "open_datetime_bj": fmt_bj(t_open, "%Y-%m-%d %H:%M:%S"),
                "close_datetime_bj": fmt_bj(t_close, "%Y-%m-%d %H:%M:%S"),
                "close_date_bj": fmt_bj(t_close, "%Y-%m-%d"),
                "holding_seconds": hold_sec,
                "holding_zh": format_duration_zh(hold_sec),
                "entry_leg": entry_leg,
                "exit_leg": exit_leg,
                "gross_pnl_usd": f2(gross_pnl, 4),
                "funding_pnl_usd": f2(funding_pnl, 4),
                "total_fee_usd": f2(total_fee, 4),
                "settled_rebate_usd": 0.0,
                "pending_rebate_usd": 0.0,
                "net_fee_usd": f2(total_fee, 4),
                "effective_rebate_pct": 0.0,
                "rebate_state": "SETTLED",
                "gate_book_pnl_usd": f2(closed_pnl, 4),
                "realized_net_pnl_usd": f2(closed_pnl, 4),
                "expected_net_pnl_usd": f2(closed_pnl, 4),
                "_raw_closed_pnl": closed_pnl,
                "_raw_total_fee": total_fee,
                "_raw_entry_fee": entry_gross_fee,
                "_raw_exit_fee": exit_gross_fee,
                "_raw_entry_maker_fee": entry_maker_fee,
                "_raw_exit_maker_fee": exit_maker_fee,
                "tags": list(j_entry.get("tags") or []),
                "note": str(j_entry.get("note") or ""),
            }
        )

    groups_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    ordered_groups: list[list[dict[str, Any]]] = []
    for idx_r, r_ec in enumerate(out):
        anchor = str(r_ec.pop("_group_anchor", "") or "")
        if anchor:
            gk = (str(r_ec["contract"]), str(r_ec["side"]), anchor)
            if gk not in groups_by_key:
                grp: list[dict[str, Any]] = []
                groups_by_key[gk] = grp
                ordered_groups.append(grp)
            groups_by_key[gk].append(r_ec)
        else:
            merged_into_prev = False
            for existing_grp in ordered_groups:
                head = existing_grp[0]
                if (
                    head["contract"] == r_ec["contract"]
                    and head["side"] == r_ec["side"]
                    and abs(dec(head["entry_leg"]["avg_price"]) - dec(r_ec["entry_leg"]["avg_price"])) <= Decimal("1e-6")
                    and r_ec["open_time_sec"] <= head["close_time_sec"] < r_ec["close_time_sec"]
                ):
                    existing_grp.append(r_ec)
                    merged_into_prev = True
                    break
            if not merged_into_prev:
                ordered_groups.append([r_ec])

    consolidated: list[dict[str, Any]] = []
    for grp in ordered_groups:
        cycles = [r.pop("_cycle", None) for r in grp]
        cycle = cycles[0]
        total_closed = sum((r["_raw_qty"] for r in grp), Decimal("0"))
        same_cycle = all(c is cycle for c in cycles)
        evidence = cycle["fills"] if cycle and same_cycle else []
        if cycle and cycle["closed"] > total_closed:
            last_close = max(r["close_time_ms"] for r in grp)
            evidence = [f for f in evidence if f["time_ms"] <= last_close]
        matched_qty = sum((f["qty"] for f in evidence if not f["is_entry"]), Decimal("0"))
        events = build_position_events(evidence, side=grp[0]["side"]) if matched_qty == total_closed else []
        if len(grp) == 1:
            single = grp[0]
            single.pop("_close_order_id", None)
            if events:
                saved = merge_legacy_journal_entries(
                    journals, single["record_key"], [open_reduction_key("easicoin", single["contract"], single["side"], events)]
                )
                single["tags"] = list(saved.get("tags") or [])
                single["note"] = str(saved.get("note") or "")
            single["position_events"] = events
            single["is_partial_close"] = bool(events and events[-1]["remaining_qty"] > 0)
            for tmp_k in ("_raw_qty", "_raw_entry_val", "_raw_exit_val", "_raw_gross_pnl", "_raw_funding_pnl"):
                single.pop(tmp_k, None)
            consolidated.append(single)
            continue

        grp.sort(key=lambda x: (x["close_time_sec"], x["open_time_sec"]))
        last_rec = grp[-1]
        primary_rk = str(last_rec["record_key"])
        legacy_rks = [str(x["record_key"]) for x in grp]
        if events:
            legacy_rks.append(open_reduction_key("easicoin", last_rec["contract"], last_rec["side"], events))
        j_merged = merge_legacy_journal_entries(journals, primary_rk, legacy_rks)

        tot_q = sum((dec(x.pop("_raw_qty", x["qty_base"])) for x in grp), Decimal("0"))
        tot_en_val = sum((dec(x.pop("_raw_entry_val", x["entry_leg"]["notional_usd"])) for x in grp), Decimal("0"))
        tot_ex_val = sum((dec(x.pop("_raw_exit_val", x["exit_leg"]["notional_usd"])) for x in grp), Decimal("0"))
        tot_gpnl = sum((dec(x.pop("_raw_gross_pnl", x["gross_pnl_usd"])) for x in grp), Decimal("0"))
        tot_fpnl = sum((dec(x.pop("_raw_funding_pnl", x["funding_pnl_usd"])) for x in grp), Decimal("0"))
        tot_cpnl = sum((dec(x["_raw_closed_pnl"]) for x in grp), Decimal("0"))
        tot_fee_g = sum((dec(x["_raw_total_fee"]) for x in grp), Decimal("0"))
        tot_en_fee = sum((dec(x["_raw_entry_fee"]) for x in grp), Decimal("0"))
        tot_ex_fee = sum((dec(x["_raw_exit_fee"]) for x in grp), Decimal("0"))
        tot_en_mf = sum((dec(x["_raw_entry_maker_fee"]) for x in grp), Decimal("0"))
        tot_ex_mf = sum((dec(x["_raw_exit_maker_fee"]) for x in grp), Decimal("0"))

        open_s = min(int(x["open_time_sec"]) for x in grp)
        close_s = max(int(x["close_time_sec"]) for x in grp)
        open_ms_g = min(int(x["open_time_ms"]) for x in grp)
        close_ms_g = max(int(x["close_time_ms"]) for x in grp)
        hold_s = max(0, close_s - open_s)
        en_px_g = (tot_en_val / tot_q) if tot_q > 0 else dec(last_rec["entry_leg"]["avg_price"])
        ex_px_g = (tot_ex_val / tot_q) if tot_q > 0 else dec(last_rec["exit_leg"]["avg_price"])

        en_m_not = sum((dec(x["entry_leg"]["maker_notional_usd"]) for x in grp), Decimal("0"))
        en_t_not = sum((dec(x["entry_leg"]["taker_notional_usd"]) for x in grp), Decimal("0"))
        ex_m_not = sum((dec(x["exit_leg"]["maker_notional_usd"]) for x in grp), Decimal("0"))
        ex_t_not = sum((dec(x["exit_leg"]["taker_notional_usd"]) for x in grp), Decimal("0"))

        en_modes = {str(x["entry_leg"]["mode"]) for x in grp}
        ex_modes = {str(x["exit_leg"]["mode"]) for x in grp}
        en_mode_c = next(iter(en_modes)) if len(en_modes) == 1 else "MIXED"
        en_mode_z = grp[0]["entry_leg"]["mode_zh"] if len(en_modes) == 1 else "混合成交"
        ex_mode_c = next(iter(ex_modes)) if len(ex_modes) == 1 else "MIXED"
        ex_mode_z = last_rec["exit_leg"]["mode_zh"] if len(ex_modes) == 1 else "混合成交"

        en_bps_g = (tot_en_fee / tot_en_val * Decimal("10000")) if tot_en_val > 0 else Decimal("0")
        ex_bps_g = (tot_ex_fee / tot_ex_val * Decimal("10000")) if tot_ex_val > 0 else Decimal("0")

        merged_en_leg = {
            **grp[0]["entry_leg"],
            "mode": en_mode_c,
            "mode_zh": en_mode_z,
            "qty_base": f2(tot_q, 6),
            "avg_price": f2(en_px_g, 6),
            "notional_usd": f2(tot_en_val, 2),
            "maker_notional_usd": f2(en_m_not, 2),
            "taker_notional_usd": f2(en_t_not, 2),
            "time_sec": open_s,
            "time_bj": fmt_bj(open_s, "%m-%d %H:%M:%S") if open_s > 0 else "—",
            "gross_fee_usd": f2(tot_en_fee, 4),
            "maker_fee_usd": f2(tot_en_mf, 4),
            "taker_fee_usd": f2(max(Decimal("0"), tot_en_fee - tot_en_mf), 4),
            "fee_bps": f2(en_bps_g, 2),
            "net_fee_usd": f2(tot_en_fee, 4),
        }
        merged_ex_leg = {
            **last_rec["exit_leg"],
            "mode": ex_mode_c,
            "mode_zh": ex_mode_z,
            "fill_count": sum(int(x["exit_leg"]["fill_count"]) for x in grp),
            "qty_base": f2(tot_q, 6),
            "avg_price": f2(ex_px_g, 6),
            "notional_usd": f2(tot_ex_val, 2),
            "maker_notional_usd": f2(ex_m_not, 2),
            "taker_notional_usd": f2(ex_t_not, 2),
            "time_sec": close_s,
            "time_bj": fmt_bj(close_s, "%m-%d %H:%M:%S") if close_s > 0 else "—",
            "gross_fee_usd": f2(tot_ex_fee, 4),
            "maker_fee_usd": f2(tot_ex_mf, 4),
            "taker_fee_usd": f2(max(Decimal("0"), tot_ex_fee - tot_ex_mf), 4),
            "fee_bps": f2(ex_bps_g, 2),
            "net_fee_usd": f2(tot_ex_fee, 4),
        }
        ec_raw_batches: list[dict[str, Any]] = []
        for b_i, sub_r in enumerate(grp):
            ec_raw_batches.append(
                {
                    **sub_r["exit_leg"],
                    "batch_index": b_i + 1,
                    "order_id": sub_r.pop("_close_order_id", ""),
                    "qty": sub_r["qty_base"],
                    "gross_pnl_usd": sub_r["gross_pnl_usd"],
                }
            )
        consolidated.append(
            {
                **last_rec,
                "record_key": primary_rk,
                "is_partial_close": True,
                "qty_base": f2(tot_q, 6),
                "open_time_sec": open_s,
                "close_time_sec": close_s,
                "open_time_ms": open_ms_g,
                "close_time_ms": close_ms_g,
                "open_datetime_bj": fmt_bj(open_s, "%Y-%m-%d %H:%M:%S"),
                "close_datetime_bj": fmt_bj(close_s, "%Y-%m-%d %H:%M:%S"),
                "close_date_bj": fmt_bj(close_s, "%Y-%m-%d"),
                "holding_seconds": hold_s,
                "holding_zh": format_duration_zh(hold_s),
                "entry_leg": merged_en_leg,
                "exit_leg": merged_ex_leg,
                "exit_batches": ec_raw_batches,
                "position_events": events,
                "gross_pnl_usd": f2(tot_gpnl, 4),
                "funding_pnl_usd": f2(tot_fpnl, 4),
                "total_fee_usd": f2(tot_fee_g, 4),
                "net_fee_usd": f2(tot_fee_g, 4),
                "gate_book_pnl_usd": f2(tot_cpnl, 4),
                "realized_net_pnl_usd": f2(tot_cpnl, 4),
                "expected_net_pnl_usd": f2(tot_cpnl, 4),
                "_raw_closed_pnl": tot_cpnl,
                "_raw_total_fee": tot_fee_g,
                "_raw_entry_fee": tot_en_fee,
                "_raw_exit_fee": tot_ex_fee,
                "_raw_entry_maker_fee": tot_en_mf,
                "_raw_exit_maker_fee": tot_ex_mf,
                "tags": list(j_merged.get("tags") or []),
                "note": str(j_merged.get("note") or ""),
            }
        )
    for pos in positions:
        symbol = str(pos.get("symbol") or "").upper()
        position_idx = str(pos.get("position_idx") or "")
        token = str(pos.get("side") or "").lower()
        side = "short" if token in {"sell", "short"} or (not token and position_idx == "2") else "long"
        link = str(pos.get("pz_link_id") or "")
        candidates = [cycle for (sym, direction, pz), cycle in active.items()
                      if sym == symbol and direction == side and (not link or link == pz)]
        if len(candidates) != 1:
            continue
        raw = project_live_reduction(
            venue="easicoin", symbol=normalize_contract_symbol(symbol), side=side.upper(), fills=candidates[0]["fills"],
            remaining_qty=pos.get("size") or pos.get("qty"), avg_entry_price=pos.get("entry_price") or pos.get("avg_price"),
            journals=journals, history=consolidated,
        )
        if raw:
            raw["_raw_closed_pnl"] = raw["gate_book_pnl_usd"]
            raw["_raw_entry_fee"] = raw["entry_leg"]["gross_fee_usd"]
            raw["_raw_exit_fee"] = raw["exit_leg"]["gross_fee_usd"]
            consolidated.append(raw)
    return consolidated


def _reconcile_easicoin_rebates_and_principal(
    *,
    ec_closed_records: list[dict[str, Any]],
    easicoin_transfers: Sequence[Mapping[str, Any]],
    ec_funding_usdt_usd: Decimal,
    ec_spot_usdt_usd: Decimal,
    ec_futures_equity_usd: Decimal,
    ec_unrealized_usd: Decimal,
    has_easicoin: bool,
    now_f: float,
    ec_px_map: Mapping[str, Decimal] | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    leg_fee_events: list[tuple[float, Decimal]] = []
    total_ec_fees = Decimal("0")
    ec_closed_book_sum = Decimal("0")
    for rec in ec_closed_records:
        ef = dec(rec.get("_raw_entry_fee"))
        xf = dec(rec.get("_raw_exit_fee"))
        ec_closed_book_sum += dec(rec.get("_raw_closed_pnl"))
        total_ec_fees += ef + xf
        if ef > 0:
            leg_fee_events.append((float(rec["open_time_sec"]), ef))
        if xf > 0:
            leg_fee_events.append((float(rec["close_time_sec"]), xf))
    leg_fee_events.sort(key=lambda pair: pair[0])

    claimed_rebate_transfers_usd = Decimal("0")
    latest_rebate_tx_sec = 0.0
    fees_at_latest_rebate_tx = Decimal("0")
    running_observed_rate = Decimal("0")
    px_lookup = ec_px_map or {}

    if easicoin_transfers:
        ec_capital_tx_net_usd = Decimal("0")
        valid_txs: list[tuple[float, str, str, str, Decimal, str]] = []
        for etx in easicoin_transfers:
            raw_st = etx.get("transfer_status") if etx.get("transfer_status") not in (None, "") else etx.get("status")
            if raw_st not in (None, ""):
                if str(raw_st).strip().lower() not in ("1", "success", "completed", "ok"):
                    continue
            coin_tx = str(etx.get("coin") or "").strip().upper()
            if not coin_tx:
                continue
            px_tx = Decimal("1") if coin_tx in USD_CASH_ASSETS else dec(px_lookup.get(coin_tx), "0")
            if px_tx <= 0:
                continue
            amt_tx = abs(dec(etx.get("amount"))) * px_tx
            if amt_tx <= 0:
                continue
            fw = str(etx.get("from_wallet") or "").strip().upper()
            tw = str(etx.get("to_wallet") or "").strip().upper()
            tx_type = str(etx.get("type") or etx.get("transfer_type") or "").strip().lower()
            ts_tx = norm_sec(etx.get("time"))
            valid_txs.append((ts_tx, fw, tw, coin_tx, amt_tx, tx_type))

        valid_txs.sort(key=lambda row: row[0])
        for ts_tx, fw, tw, coin_tx, amt_tx, tx_type in valid_txs:
            if fw == "FUNDING" and tw in ("TRADING", "SPOT", "CONTRACT", "FUTURES"):
                if coin_tx == "USDT":
                    cum_fees_at_tx = sum(
                        (fee_v for f_ts, fee_v in leg_fee_events if (ts_tx <= 0 or f_ts <= ts_tx + 60.0)),
                        Decimal("0"),
                    )
                    max_plausible_rebate = cum_fees_at_tx * Decimal("0.98")
                    unclaimed_cap = max(Decimal("0"), max_plausible_rebate - claimed_rebate_transfers_usd)
                    if cum_fees_at_tx > 0 and amt_tx <= (unclaimed_cap + Decimal("0.05")):
                        claimed_rebate_transfers_usd += amt_tx
                        if ts_tx >= latest_rebate_tx_sec:
                            latest_rebate_tx_sec = ts_tx
                            fees_at_latest_rebate_tx = cum_fees_at_tx
                        running_observed_rate = min(Decimal("0.95"), claimed_rebate_transfers_usd / cum_fees_at_tx)
                    else:
                        est_earned = cum_fees_at_tx * running_observed_rate if running_observed_rate > 0 else Decimal("0")
                        reb_part = max(Decimal("0"), min(amt_tx, est_earned - claimed_rebate_transfers_usd))
                        prin_part = amt_tx - reb_part
                        if reb_part > 0:
                            claimed_rebate_transfers_usd += reb_part
                            if ts_tx >= latest_rebate_tx_sec:
                                latest_rebate_tx_sec = ts_tx
                                fees_at_latest_rebate_tx = cum_fees_at_tx
                        ec_capital_tx_net_usd += prin_part
                else:
                    ec_capital_tx_net_usd += amt_tx
            elif tw == "FUNDING" and fw in ("TRADING", "SPOT", "CONTRACT", "FUTURES"):
                ec_capital_tx_net_usd -= amt_tx
            elif not fw and tx_type in ("deposit", "in"):
                ec_capital_tx_net_usd += amt_tx
            elif not tw and tx_type in ("withdraw", "withdrawal", "out"):
                ec_capital_tx_net_usd -= amt_tx

        if running_observed_rate > 0:
            est_total_reb = total_ec_fees * running_observed_rate
            untransferred_rebate = min(
                ec_funding_usdt_usd,
                max(Decimal("0"), est_total_reb - claimed_rebate_transfers_usd + Decimal("0.05")),
            )
        elif total_ec_fees > 0 and Decimal("0") < ec_funding_usdt_usd <= (total_ec_fees * Decimal("0.98")):
            untransferred_rebate = ec_funding_usdt_usd
        else:
            untransferred_rebate = Decimal("0")

        ec_observed_rebate_pool_usd = claimed_rebate_transfers_usd + untransferred_rebate
        ec_principal_usd = max(Decimal("0"), ec_capital_tx_net_usd + (ec_funding_usdt_usd - untransferred_rebate))
    elif has_easicoin:
        if total_ec_fees > 0 and Decimal("0") < ec_funding_usdt_usd <= (total_ec_fees * Decimal("0.98")):
            untransferred_rebate = ec_funding_usdt_usd
        else:
            untransferred_rebate = Decimal("0")
        ec_observed_rebate_pool_usd = untransferred_rebate
        ec_principal_usd = max(
            Decimal("0"),
            (ec_futures_equity_usd - ec_unrealized_usd - ec_closed_book_sum)
            + (ec_funding_usdt_usd - untransferred_rebate)
            + ec_spot_usdt_usd,
        )
    else:
        ec_observed_rebate_pool_usd = Decimal("0")
        ec_principal_usd = Decimal("0")

    settled_fee_denom = (
        fees_at_latest_rebate_tx
        if (fees_at_latest_rebate_tx > 0 and ec_funding_usdt_usd <= Decimal("0.05"))
        else total_ec_fees
    )
    if settled_fee_denom > 0 and ec_observed_rebate_pool_usd > 0:
        ec_live_rebate_rate = min(Decimal("0.95"), ec_observed_rebate_pool_usd / settled_fee_denom)
    else:
        ec_live_rebate_rate = Decimal("0")

    has_rebate_channel = ec_live_rebate_rate > 0
    remaining_pool = ec_observed_rebate_pool_usd
    settled_bj_label = fmt_bj(latest_rebate_tx_sec, "%m-%d %H:%M") if latest_rebate_tx_sec > 0 else "资金账户入账"

    for rec in ec_closed_records:
        raw_closed_pnl = dec(rec.pop("_raw_closed_pnl", rec["gate_book_pnl_usd"]))
        raw_total_fee = dec(rec.pop("_raw_total_fee", rec["total_fee_usd"]))
        raw_entry_fee = dec(rec.pop("_raw_entry_fee", rec["entry_leg"]["gross_fee_usd"]))
        raw_exit_fee = dec(rec.pop("_raw_exit_fee", rec["exit_leg"]["gross_fee_usd"]))
        raw_entry_mf = dec(rec.pop("_raw_entry_maker_fee", rec["entry_leg"]["maker_fee_usd"]))
        raw_exit_mf = dec(rec.pop("_raw_exit_maker_fee", rec["exit_leg"]["maker_fee_usd"]))
        t_close = float(rec["close_time_sec"])
        is_fresh = has_rebate_channel and ((now_f - t_close) <= EC_PENDING_REBATE_WINDOW_SEC)

        rec_settled = Decimal("0")
        rec_pending = Decimal("0")
        for leg_obj, leg_fee, leg_mf in (
            (rec["entry_leg"], raw_entry_fee, raw_entry_mf),
            (rec["exit_leg"], raw_exit_fee, raw_exit_mf),
        ):
            if leg_fee <= 0:
                leg_obj["rebate_status"] = "NONE"
                continue
            target_reb = leg_fee * ec_live_rebate_rate
            s_reb = min(target_reb, max(Decimal("0"), remaining_pool))
            remaining_pool = max(Decimal("0"), remaining_pool - s_reb)
            gap_reb = max(Decimal("0"), target_reb - s_reb)
            p_reb = gap_reb if (gap_reb > Decimal("0.0005") and is_fresh) else Decimal("0")
            eff_leg_reb = s_reb + p_reb

            if p_reb > 0 and s_reb > 0:
                l_status = "PARTIAL_PENDING"
            elif p_reb > 0:
                l_status = "PENDING"
            elif s_reb > 0:
                l_status = "SETTLED"
            else:
                l_status = "NONE"

            mf_share = (leg_mf / leg_fee) if leg_fee > 0 else Decimal("0")
            l_m_reb = eff_leg_reb * mf_share
            l_t_reb = eff_leg_reb - l_m_reb
            leg_obj["settled_rebate_usd"] = f2(s_reb, 4)
            leg_obj["pending_rebate_usd"] = f2(p_reb, 4)
            leg_obj["maker_rebate_usd"] = f2(l_m_reb, 4)
            leg_obj["taker_rebate_usd"] = f2(l_t_reb, 4)
            leg_obj["net_fee_usd"] = f2(leg_fee - eff_leg_reb, 4)
            leg_obj["rebate_pct"] = f2((eff_leg_reb / leg_fee * Decimal("100")), 2) if leg_fee > 0 else 0.0
            leg_obj["rebate_status"] = l_status
            leg_obj["rebate_settled_bj"] = settled_bj_label if s_reb > 0 else ""

            rec_settled += s_reb
            rec_pending += p_reb

        ex_leg_obj = rec["exit_leg"]
        ex_g_dec = dec(ex_leg_obj.get("gross_fee_usd"))
        ex_s_dec = dec(ex_leg_obj.get("settled_rebate_usd"))
        ex_p_dec = dec(ex_leg_obj.get("pending_rebate_usd"))
        if ex_g_dec > 0 and rec.get("exit_batches"):
            s_ratio_ec = ex_s_dec / ex_g_dec
            p_ratio_ec = ex_p_dec / ex_g_dec
            for b_obj in rec["exit_batches"]:
                bf_dec = dec(b_obj.get("gross_fee_usd"))
                bs_dec = bf_dec * s_ratio_ec
                bp_dec = bf_dec * p_ratio_ec
                b_obj["settled_rebate_usd"] = f2(bs_dec, 4)
                b_obj["pending_rebate_usd"] = f2(bp_dec, 4)
                b_obj["net_fee_usd"] = f2(max(Decimal("0"), bf_dec - bs_dec - bp_dec), 4)

        rec_eff = rec_settled + rec_pending
        if rec_pending > 0 and rec_settled > 0:
            r_state = "PARTIAL_PENDING"
        elif rec_pending > 0:
            r_state = "PENDING"
        else:
            r_state = "SETTLED"

        rec["settled_rebate_usd"] = f2(rec_settled, 4)
        rec["pending_rebate_usd"] = f2(rec_pending, 4)
        rec["net_fee_usd"] = f2(raw_total_fee - rec_eff, 4)
        rec["effective_rebate_pct"] = f2((rec_eff / raw_total_fee * Decimal("100")), 2) if raw_total_fee > 0 else 0.0
        rec["rebate_state"] = r_state
        rec["realized_net_pnl_usd"] = f2(raw_closed_pnl + rec_settled, 4)
        rec["expected_net_pnl_usd"] = f2(raw_closed_pnl + rec_eff, 4)

    return ec_principal_usd, ec_observed_rebate_pool_usd, ec_live_rebate_rate


def build_easicoin_venue_ledger(
    store: "LedgerStore",
    meta: AdapterMeta,
    *,
    journals: Mapping[str, Mapping[str, Any]],
    now_s: float,
    global_spot_prices: Mapping[str, float] | None = None,
) -> VenueLedgerBundle | None:
    scope = store.venue_scope("easicoin")
    ec_lb = scope.get_kv("live_bundle", None)
    closed_pnl_rows = scope.load_records("closed_pnl", desc=True)
    fills = scope.load_records("fills", desc=True)
    orders = scope.load_records("orders", desc=True)
    transfers = scope.load_records("transfers", desc=True)

    if not ec_lb and not closed_pnl_rows and not transfers:
        return None
    ec_lb = ec_lb if isinstance(ec_lb, dict) else {}

    ec_uid = str(ec_lb.get("userid") or ec_lb.get("uid") or "")
    ec_px_map = _build_ec_price_map(ec_lb.get("tickers"))
    for gs_sym, gs_px in (global_spot_prices or {}).items():
        gs_u = str(gs_sym or "").strip().upper()
        gs_d = dec(gs_px, "0")
        if gs_u and gs_d > 0:
            ec_px_map.setdefault(gs_u, gs_d)
    for usd_c in USD_CASH_ASSETS:
        ec_px_map.setdefault(usd_c, Decimal("1"))
    ec_closed_records = _build_easicoin_closed_records(
        closed_pnl_rows=closed_pnl_rows,
        fills=fills,
        orders=orders,
        journals=journals,
        positions=ec_lb.get("futures_positions") or [],
    )

    # Spot / Funding 1:1 Tokenized Stocks on EasiCoin
    ec_sb = ec_lb.get("stock_bundle") or {}
    ec_qty_by_tok: dict[str, Decimal] = defaultdict(Decimal)
    for fnd in ec_lb.get("funding_balances") or []:
        if isinstance(fnd, Mapping):
            c_f = str(fnd.get("coin") or "").strip().upper()
            q_f = dec(fnd.get("total_equity") or fnd.get("available_balance") or fnd.get("balance"))
            if q_f > 0 and classify_spot_stock_token(c_f, exchange="EASICOIN"):
                ec_qty_by_tok[c_f] += q_f
    for spb in ec_lb.get("spot_balances") or []:
        if isinstance(spb, Mapping):
            c_s = str(spb.get("token_name") or spb.get("coin") or "").strip().upper()
            q_s = dec(spb.get("balance") or spb.get("available"))
            if q_s > 0 and classify_spot_stock_token(c_s, exchange="EASICOIN"):
                ec_qty_by_tok[c_s] += q_s

    ec_sp_fills = ec_sb.get("spot_stock_fills") or {}
    stk_holdings: list[dict[str, Any]] = []
    ec_spot_stk_val = Decimal("0")
    ec_stock_net_usd = Decimal("0")
    ec_stk_fees = Decimal("0")
    ec_stk_upnl = Decimal("0")
    for c_ec, q_ec in sorted(ec_qty_by_tok.items()):
        t_info = classify_spot_stock_token(c_ec, exchange="EASICOIN")
        if t_info and q_ec > 0:
            px_ec = ec_px_map.get(c_ec, Decimal("0"))
            val_ec = q_ec * px_ec
            recon = reconstruct_spot_cost_basis(
                fills=ec_sp_fills.get(c_ec) or [],
                current_qty=q_ec,
                current_price=px_ec,
                token_symbol=c_ec,
                quote_price_map=ec_px_map,
            )
            ec_spot_stk_val += val_ec
            ec_stock_net_usd += recon["net_pnl_usd"]
            ec_stk_fees += recon["total_fee_usd"]
            ec_stk_upnl += recon["unrealized_pnl_usd"]
            stk_holdings.append(
                {
                    "venue_id": "easicoin",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "symbol": t_info["token_symbol"],
                    "name": t_info["name"],
                    "channel": "1:1 Stock Token",
                    "channel_zh": "1:1美股代币",
                    "shares": f2(q_ec, 6),
                    "avg_cost_usd": f2(recon["avg_cost_usd"], 2),
                    "current_price_usd": f2(px_ec, 2),
                    "market_value_usd": f2(val_ec, 2),
                    "pnl_usd": f2(recon["unrealized_pnl_usd"], 2),
                    "pnl_pct": f2(recon["pnl_pct"], 2),
                }
            )

    ec_futures_equity_usd = Decimal("0")
    ec_futures_available_usd = Decimal("0")
    ec_unrealized_usd = Decimal("0")
    for fb in ec_lb.get("futures_balances") or []:
        if str(fb.get("coin") or "").upper() in ("USDT", "USDC", "USD"):
            ec_futures_equity_usd += dec(fb.get("equity") or fb.get("wallet_balance"))
            ec_futures_available_usd += dec(fb.get("available_balance"))
            ec_unrealized_usd += dec(fb.get("unrealised_pnl"))

    ec_funding_usdt_usd = Decimal("0")
    ec_funding_tokens_usd = Decimal("0")
    raw_spot_rows: list[tuple[str, float, float, str]] = []
    for fnd in ec_lb.get("funding_balances") or []:
        qty_f = dec(fnd.get("total_equity") or fnd.get("available_balance") or fnd.get("balance"))
        if qty_f <= 0:
            continue
        coin_f = str(fnd.get("coin") or fnd.get("token_name") or "").upper()
        raw_spot_rows.append((coin_f, float(qty_f), 0.0, "FUNDING"))
        if coin_f in ("USDT", "USDC", "USD1", "USD"):
            ec_funding_usdt_usd += qty_f
        else:
            ec_funding_tokens_usd += qty_f * ec_px_map.get(coin_f, Decimal("0"))

    ec_spot_usdt_usd = Decimal("0")
    ec_spot_tokens_usd = Decimal("0")
    for spb in ec_lb.get("spot_balances") or []:
        qty_s = dec(spb.get("balance") or spb.get("available"))
        if qty_s <= 0:
            continue
        coin_s = str(spb.get("token_name") or spb.get("coin") or "").upper()
        raw_spot_rows.append((coin_s, float(qty_s), 0.0, "SPOT"))
        if coin_s in ("USDT", "USDC", "USD1", "USD"):
            ec_spot_usdt_usd += qty_s
        else:
            ec_spot_tokens_usd += qty_s * ec_px_map.get(coin_s, Decimal("0"))

    ec_raw_tokens_usd = ec_funding_tokens_usd + ec_spot_tokens_usd
    ec_non_usdt_tokens_usd = max(Decimal("0"), ec_raw_tokens_usd - ec_spot_stk_val)
    ec_total_equity_usd = ec_futures_equity_usd + ec_funding_usdt_usd + ec_funding_tokens_usd + ec_spot_usdt_usd + ec_spot_tokens_usd
    has_easicoin = bool(ec_uid or ec_total_equity_usd > 0 or ec_closed_records or transfers)
    if not has_easicoin:
        return None

    ec_principal_usd, ec_observed_rebate_pool_usd, ec_live_rebate_rate = _reconcile_easicoin_rebates_and_principal(
        ec_closed_records=ec_closed_records,
        easicoin_transfers=transfers,
        ec_funding_usdt_usd=ec_funding_usdt_usd,
        ec_spot_usdt_usd=ec_spot_usdt_usd,
        ec_futures_equity_usd=ec_futures_equity_usd,
        ec_unrealized_usd=ec_unrealized_usd,
        has_easicoin=has_easicoin,
        now_f=now_s,
        ec_px_map=ec_px_map,
    )

    public_trades = [
        normalize_internal_record_to_public_trade(
            r,
            venue_id="easicoin",
            venue_name=meta.display_name,
            venue_name_zh=meta.display_name_zh,
            color_token=meta.color_token,
        )
        for r in ec_closed_records
    ]

    # Open positions
    ec_sl_protected: set[str] = set()
    for eoo in ec_lb.get("futures_open_orders") or []:
        ec_c = normalize_contract_symbol(str(eoo.get("symbol") or ""))
        if str(eoo.get("stop_order_type") or "") in ("StopLoss", "Stop") and ec_c:
            ec_sl_protected.add(ec_c)

    open_positions: list[dict[str, Any]] = []
    for ep in ec_lb.get("futures_positions") or []:
        ec_sz = abs(dec(ep.get("size") or ep.get("qty")))
        if ec_sz == 0:
            continue
        ec_c = normalize_contract_symbol(str(ep.get("symbol") or ""))
        p_side_raw = str(ep.get("side") or "").lower()
        p_idx = int(float(ep.get("position_idx") or 0))
        is_short_pos = p_side_raw in ("sell", "short") or (p_side_raw == "" and p_idx == 2)
        entry_px_ec = dec(ep.get("entry_price") or ep.get("avg_price"))
        mark_px_ec = dec(ep.get("mark_price")) or ec_px_map.get(ec_c.replace("_USDT", ""), entry_px_ec)
        liq_px_ec = dec(ep.get("liq_price") or ep.get("bust_price"))
        unreal_ec = dec(ep.get("unrealised_pnl"))
        notional_ec = f2(ec_sz * mark_px_ec, 2)
        upnl_2dp = f2(unreal_ec, 2)
        try:
            lev = max(1.0, float(ep.get("leverage") or 1.0))
        except (TypeError, ValueError):
            lev = 1.0
        uroi = round((upnl_2dp / (notional_ec / lev)) * 100.0, 2) if notional_ec > 0 else 0.0
        open_positions.append(
            {
                "venue_id": "easicoin",
                "venue_name": meta.display_name,
                "color_token": meta.color_token,
                "symbol": ec_c,
                "side": "SHORT" if is_short_pos else "LONG",
                "leverage": lev,
                "qty": f2(ec_sz, 6),
                "notional_usd": notional_ec,
                "entry_price": f2(entry_px_ec, 4),
                "mark_price": f2(mark_px_ec, 4),
                "liq_price": f2(liq_px_ec, 4),
                "unrealized_pnl_usd": upnl_2dp,
                "unrealized_roi_pct": uroi,
                "has_stop_order": (ec_c in ec_sl_protected) or (dec(ep.get("stop_loss")) > 0),
            }
        )

    # Copy trading detection
    copy_eq = dec(ec_lb.get("copy_equity") or ec_lb.get("copy_trading_equity"))
    copy_transfers: list[dict[str, Any]] = []
    copy_inv = Decimal("0")
    copy_red = Decimal("0")
    for tr in transfers:
        fw = str(tr.get("from_wallet") or tr.get("from_account_type") or "").strip().upper()
        tw = str(tr.get("to_wallet") or tr.get("to_account_type") or "").strip().upper()
        tp_s = str(tr.get("type") or tr.get("transfer_type") or "").strip().upper()
        if str(tr.get("coin") or "USDT").strip().upper() not in ("USDT", "USDC", "USD"):
            continue
        amt = abs(dec(tr.get("amount")))
        if amt <= 0:
            continue
        ts_s = norm_sec(tr.get("time") or tr.get("created_at"))
        is_to_copy = any(k in tw or k in tp_s for k in ("COPY", "FOLLOW", "TRACE", "BOT", "STRATEGY"))
        is_from_copy = any(k in fw for k in ("COPY", "FOLLOW", "TRACE", "BOT", "STRATEGY"))
        if is_to_copy and not is_from_copy:
            copy_inv += amt
            copy_transfers.append(
                {
                    "venue_id": "easicoin",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "time_sec": int(ts_s),
                    "time_str": fmt_bj(ts_s, "%Y-%m-%d %H:%M"),
                    "direction": "Transfer to EasiCoin Copy Pool",
                    "direction_zh": "划入易币跟单本金",
                    "amount_usd": f2(amt, 2),
                }
            )
        elif is_from_copy:
            copy_red += amt
            copy_transfers.append(
                {
                    "venue_id": "easicoin",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "time_sec": int(ts_s),
                    "time_str": fmt_bj(ts_s, "%Y-%m-%d %H:%M"),
                    "direction": "Withdraw from EasiCoin Copy Pool",
                    "direction_zh": "易币跟单本金转回",
                    "amount_usd": f2(amt, 2),
                }
            )
    copy_transfers.sort(key=lambda x: x["time_sec"], reverse=True)
    has_ec_ct = bool(copy_transfers or copy_eq > Decimal("0.01"))
    is_ct_active = copy_eq > Decimal("0.01") or (copy_inv > copy_red and copy_red == 0)
    copy_book_pnl = (copy_red + copy_eq - copy_inv) if copy_inv > 0 else Decimal("0")
    ec_copy_platform = (
        {
            "venue_id": "easicoin",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "status": "ACTIVE" if is_ct_active else "SETTLED",
            "invested_usd": f2(copy_inv, 2),
            "equity_usd": f2(copy_eq, 2),
            "book_pnl_usd": f2(copy_book_pnl, 2),
            "rebate_usd": 0.0,
            "combined_pnl_usd": f2(copy_book_pnl, 2),
            "rebate_count": 0,
        }
        if has_ec_ct
        else None
    )

    # Conservation Law
    ec_book_sum_usd = sum((dec(r["gate_book_pnl_usd"]) for r in ec_closed_records), Decimal("0"))
    ec_settled_reb_usd = sum((dec(r["settled_rebate_usd"]) for r in ec_closed_records), Decimal("0"))
    ec_closed_sum_usd = sum((dec(r["realized_net_pnl_usd"]) for r in ec_closed_records), Decimal("0"))
    ec_futures_net_usd = ec_closed_sum_usd + ec_unrealized_usd

    if ec_total_equity_usd > 0 or ec_principal_usd > 0:
        ec_net_pnl_usd = ec_total_equity_usd - ec_principal_usd
        ec_adj_usd = ec_net_pnl_usd - (ec_futures_net_usd + ec_stock_net_usd + ec_non_usdt_tokens_usd)
    else:
        ec_net_pnl_usd = ec_futures_net_usd + ec_stock_net_usd
        ec_adj_usd = Decimal("0")

    eq_2dp = f2(ec_total_equity_usd, 2)
    prin_2dp = f2(ec_principal_usd, 2)
    pnl_2dp = round(eq_2dp - prin_2dp, 2) if (ec_total_equity_usd > 0 or ec_principal_usd > 0) else f2(ec_net_pnl_usd, 2)

    reconcile_streams: list[dict[str, Any]] = [
        {
            "key": "easicoin_futures",
            "title_zh": "易币 (EasiCoin) 合约实收净盈亏",
            "label_zh": "易币 (EasiCoin) 合约实收净盈亏",
            "detail_zh": f"易币 {len(ec_closed_records)} 笔账面 {(ec_book_sum_usd + ec_unrealized_usd):+.2f} U + 实时返佣 +{ec_settled_reb_usd:.2f} U",
            "amount_usd": f2(ec_futures_net_usd, 2),
        }
    ]
    if abs(ec_stock_net_usd) >= Decimal("0.01"):
        reconcile_streams.append(
            {
                "key": "easicoin_stock",
                "title_zh": "易币 (EasiCoin) 美股代币净损益",
                "label_zh": "易币 (EasiCoin) 美股代币净损益",
                "detail_zh": f"1:1 美股代币市值 {ec_spot_stk_val:.2f} U · 净损益 {ec_stock_net_usd:+.2f} U",
                "amount_usd": f2(ec_stock_net_usd, 2),
            }
        )
    ec_tok_and_adj = ec_non_usdt_tokens_usd + ec_adj_usd
    if abs(ec_tok_and_adj) >= Decimal("0.01"):
        reconcile_streams.append(
            {
                "key": "easicoin_tokens_adj",
                "title_zh": "易币 (EasiCoin) 红包代币与结算校准",
                "label_zh": "易币 (EasiCoin) 红包代币与结算校准",
                "detail_zh": f"易币红包代币 {ec_non_usdt_tokens_usd:.2f} U 及结算尾差校准",
                "amount_usd": f2(ec_tok_and_adj, 2),
            }
        )
    reconcile_streams = clamp_reconcile_streams_to_target(
        reconcile_streams,
        pnl_2dp,
        fallback_key="easicoin_tokens_adj",
        fallback_label_zh="易币 (EasiCoin) 账户净值变动",
        fallback_detail_zh="易币资金与交易账户净值变动",
    )

    today_bj_str = fmt_bj(now_s, "%Y-%m-%d")
    ec_first_day = min(
        (r["close_date_bj"] for r in ec_closed_records if r.get("close_date_bj")),
        default=today_bj_str,
    )
    daily_pnl: dict[str, Decimal] = defaultdict(Decimal)
    daily_comp: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for ec_r in ec_closed_records:
        for day, amount in trade_daily_amounts(ec_r, dec(ec_r["realized_net_pnl_usd"])).items():
            daily_pnl[day] += amount
            daily_comp[day]["easicoin_futures"] += amount
    if ec_unrealized_usd != 0:
        daily_pnl[today_bj_str] += ec_unrealized_usd
        daily_comp[today_bj_str]["easicoin_futures"] += ec_unrealized_usd
    if ec_stock_net_usd != 0:
        daily_pnl[ec_first_day] += ec_stock_net_usd
        daily_comp[ec_first_day]["easicoin_stock"] += ec_stock_net_usd
    if ec_tok_and_adj != 0:
        daily_pnl[ec_first_day] += ec_tok_and_adj
        daily_comp[ec_first_day]["easicoin_tokens_adj"] += ec_tok_and_adj

    px_float_map = {**(global_spot_prices or {}), **extract_easicoin_spot_prices(store)}
    wallet_balances = build_spot_wallet_balances(
        raw_spot_rows,
        venue_id="easicoin",
        venue_name=meta.display_name,
        color_token=meta.color_token,
        spot_prices=px_float_map,
        excluded_symbols={h["symbol"] for h in stk_holdings},
    )
    stock_perps = build_venue_stock_perpetuals(
        public_trades,
        venue_id="easicoin",
        venue_name=meta.display_name,
        venue_name_zh=meta.display_name_zh,
    )

    return VenueLedgerBundle(
        venue_id="easicoin",
        exchange_card={
            "venue_id": "easicoin",
            "display_name": meta.display_name,
            "display_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "connected": True,
            "equity_usd": eq_2dp,
            "net_principal_usd": prin_2dp,
            "net_pnl_usd": pnl_2dp,
            "futures_equity_usd": f2(ec_futures_equity_usd, 2),
            "available_margin_usd": f2(ec_futures_available_usd, 2),
            "trade_count": len(public_trades),
            "rebate_rate_pct": f2(ec_live_rebate_rate * Decimal("100"), 1),
            "discount_rate_pct": 0.0,
            "capabilities": meta.capabilities.model_dump(),
        },
        trades=public_trades,
        open_positions=open_positions,
        spot_holdings=spot_holdings_from_wallets(wallet_balances),
        spot_wallet_balances=wallet_balances,
        reconcile_streams=reconcile_streams,
        daily_pnl=dict(daily_pnl),
        daily_components={d: dict(c) for d, c in daily_comp.items()},
        copy_trading=VenueCopyBundle(
            has_copy_trading=has_ec_ct,
            invested_usd=f2(copy_inv, 2) if has_ec_ct else 0.0,
            active_equity_usd=f2(copy_eq, 2) if has_ec_ct else 0.0,
            book_pnl_usd=f2(copy_book_pnl, 2) if has_ec_ct else 0.0,
            rebate_usd=0.0,
            combined_pnl_usd=f2(copy_book_pnl, 2) if has_ec_ct else 0.0,
            platform=ec_copy_platform,
            transfers=copy_transfers if has_ec_ct else [],
        ),
        stocks=VenueStockBundle(
            equity_usd=f2(ec_spot_stk_val, 2),
            unrealized_pnl_usd=f2(ec_stk_upnl, 2),
            non_perp_fees_usd=f2(ec_stk_fees, 4),
            holdings=stk_holdings,
            perpetuals=stock_perps,
        ),
        fee_context={"live_rebate_rate": ec_live_rebate_rate},
    )


def build_easicoin_fee_matrix_row(
    bundle: VenueLedgerBundle,
    range_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    ex = bundle.exchange_card
    ec_live_rebate_rate: Decimal = bundle.fee_context.get("live_rebate_rate", Decimal("0"))
    ec_all = aggregate_trade_legs_decimal(range_trades)
    ec_crypto_recs = [r for r in range_trades if str(r.get("symbol") or "").split("_")[0] not in STOCK_COMMODITY_BASES]
    ec_stock_recs = [r for r in range_trades if str(r.get("symbol") or "").split("_")[0] in STOCK_COMMODITY_BASES]
    ec_c = aggregate_trade_legs_decimal(ec_crypto_recs)
    ec_s = aggregate_trade_legs_decimal(ec_stock_recs)

    ec_m_gross_bps = (ec_all["m_fee"] / ec_all["m_not"] * Decimal("10000")) if ec_all["m_not"] > 0 else Decimal("0")
    ec_m_net_bps = (ec_all["m_net"] / ec_all["m_not"] * Decimal("10000")) if ec_all["m_not"] > 0 else Decimal("0")
    ec_t_gross_bps = (ec_all["t_fee"] / ec_all["t_not"] * Decimal("10000")) if ec_all["t_not"] > 0 else Decimal("0")
    ec_t_net_bps = (ec_all["t_net"] / ec_all["t_not"] * Decimal("10000")) if ec_all["t_not"] > 0 else Decimal("0")

    ec_c_m_net_rate = (
        (ec_c["m_net"] / ec_c["m_not"])
        if ec_c["m_not"] > 0
        else (ec_c["t_net"] / ec_c["t_not"] if ec_c["t_not"] > 0 else Decimal("0"))
    )
    ec_s_m_net_rate = (ec_s["m_net"] / ec_s["m_not"]) if ec_s["m_not"] > 0 else Decimal("0")
    ec_savings = max(Decimal("0"), ec_c["t_net"] - ec_c["t_not"] * ec_c_m_net_rate) + max(
        Decimal("0"), ec_s["t_net"] - ec_s["t_not"] * ec_s_m_net_rate
    )
    ec_overall_reb_pct = (
        (ec_all["tot_reb"] / ec_all["g_fee"] * Decimal("100"))
        if ec_all["g_fee"] > 0
        else (ec_live_rebate_rate * Decimal("100"))
    )

    blended_gross_bps = (ec_all["g_fee"] / ec_all["tot_not"] * Decimal("10000")) if ec_all["tot_not"] > 0 else Decimal("0")
    blended_net_bps = (max(Decimal("0"), ec_all["tot_net"]) / ec_all["tot_not"] * Decimal("10000")) if ec_all["tot_not"] > 0 else Decimal("0")

    return {
        "venue_id": "easicoin",
        "display_name": ex["display_name"],
        "display_name_zh": ex["display_name_zh"],
        "color_token": ex["color_token"],
        "trade_count": len(range_trades),
        "maker_gross_bps": f2(ec_m_gross_bps, 2),
        "maker_net_bps": f2(ec_m_net_bps, 2),
        "taker_gross_bps": f2(ec_t_gross_bps, 2),
        "taker_net_bps": f2(ec_t_net_bps, 2),
        "maker_bps_known": bool(ec_all["m_not"] > 0),
        "taker_bps_known": bool(ec_all["t_not"] > 0),
        "blended_gross_bps": f2(blended_gross_bps, 2),
        "blended_net_bps": f2(blended_net_bps, 2),
        "gross_fee_usd": f2(ec_all["g_fee"], 2),
        "discount_saved_usd": 0.0,
        "discount_label": "",
        "rebate_usd": f2(ec_all["tot_reb"], 2),
        "rebate_pct": f2(ec_overall_reb_pct, 1),
        "net_fee_usd": f2(ec_all["tot_net"], 2),
        "combined_relief_pct": f2(ec_overall_reb_pct, 1),
        "potential_maker_savings_usd": f2(ec_savings, 2),
    }
