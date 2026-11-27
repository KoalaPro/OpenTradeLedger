"""Independent actuarial accounting engine for MEXC (`venue_id = 'mexc'`)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from opentradeledger.core.open_reductions import carry_open_reduction_notes, open_reduction_record
from opentradeledger.core.position_lifecycle import trade_daily_amounts
from opentradeledger.adapters.base import (
    AdapterMeta,
    VenueCopyBundle,
    VenueLedgerBundle,
    VenueStockBundle,
)
from opentradeledger.adapters.stock_channels import (
    _STOCK_SUBACCOUNT_NAMES,
    classify_spot_stock_token,
    reconstruct_spot_cost_basis,
)
from opentradeledger.core.position_batch_pnl import position_fill_accounting, reported_amount
from opentradeledger.core.venue_primitives import (
    DEFAULT_CONTRACTS_MULTIPLIER,
    aggregate_trade_legs_decimal,
    build_spot_wallet_balances,
    spot_holdings_from_wallets,
    build_venue_stock_perpetuals,
    clamp_reconcile_streams_to_target,
    dec,
    f2,
    fmt_bj,
    fmt_pct_clean,
    format_duration_zh,
    merge_legacy_journal_entries,
    norm_ms,
    norm_sec,
    normalize_contract_symbol,
    normalize_internal_record_to_public_trade,
)

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore


def extract_mexc_spot_prices(store: "LedgerStore") -> dict[str, float]:
    lb = store.venue_scope("mexc").get_kv("live_bundle", {}) or {}
    out: dict[str, float] = {}
    if isinstance(lb, dict) and isinstance(lb.get("spot_tickers"), dict):
        for sym, raw_px in (lb.get("spot_tickers") or {}).items():
            try:
                px = float(raw_px)
                if px > 0:
                    out[str(sym).strip().upper()] = px
            except (TypeError, ValueError):
                continue
    return out


def _build_mexc_closed_records(
    *,
    mexc_positions_history: Sequence[Mapping[str, Any]],
    open_positions: Sequence[Mapping[str, Any]] = (),
    mexc_deals: Sequence[Mapping[str, Any]],
    mexc_orders: Sequence[Mapping[str, Any]],
    mexc_transfers: Sequence[Mapping[str, Any]],
    mexc_contract_specs: Mapping[str, Mapping[str, Any]] | None,
    spot_tickers: Mapping[str, Any] | None,
    journals: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    specs = mexc_contract_specs or {}
    tickers = spot_tickers or {}
    mx_spot_px = dec(tickers.get("MX"), "2.0")
    if mx_spot_px <= 0:
        mx_spot_px = Decimal("2.0")

    def _mx_mult(sym: str) -> Decimal:
        sp = specs.get(sym) or {}
        cs = dec(sp.get("contractSize"), "0")
        return cs if cs > 0 else DEFAULT_CONTRACTS_MULTIPLIER.get(sym, Decimal("1"))

    order_by_id: dict[str, Mapping[str, Any]] = {
        str(mo.get("orderId")).strip(): mo for mo in mexc_orders if mo.get("orderId")
    }

    mx_ded_tot_qty = Decimal("0")
    mx_conv_tot_usd = Decimal("0")
    for hp in mexc_positions_history:
        for item in hp.get("deductFeeList") or []:
            if str(item.get("currency") or "").upper() == "MX":
                mx_ded_tot_qty += abs(dec(item.get("deductFee")))
                mx_conv_tot_usd += abs(dec(item.get("convertSettleFee")))
    avg_mx_fee_px = (mx_conv_tot_usd / mx_ded_tot_qty) if mx_ded_tot_qty > 0 else mx_spot_px

    contract_hold_earn_usd = Decimal("0")
    daily_contract_hold: dict[str, Decimal] = defaultdict(Decimal)
    cap_in_usd = Decimal("0")
    cap_out_usd = Decimal("0")
    mx_cap_in_qty = Decimal("0")
    mx_cap_out_qty = Decimal("0")
    spot_dep_usd = Decimal("0")
    spot_wd_usd = Decimal("0")
    has_ext_flow = False
    kickback_rebates: list[tuple[float, Decimal]] = []

    for tr in mexc_transfers:
        src_t = str(tr.get("_source") or "fut_transfer")
        if src_t in ("spot_kickback", "spot_rebate"):
            r_amt = abs(dec(tr.get("amount") or tr.get("rebateAmount")))
            r_cur = str(tr.get("asset") or tr.get("coin") or "USDT").upper()
            r_px = Decimal("1") if r_cur in ("USDT", "USDC", "USD") else (dec(tickers.get(r_cur)) or (avg_mx_fee_px if r_cur == "MX" else Decimal("0")))
            r_val = r_amt * r_px
            if r_val > 0:
                kickback_rebates.append((norm_sec(tr.get("_time_ms") or tr.get("createTime")), r_val))
            continue
        if src_t == "spot_deposit":
            raw_d_st = tr.get("status")
            if raw_d_st in (None, "") or str(raw_d_st).strip().upper() in ("5", "SUCCESS", "COMPLETED"):
                d_cur = str(tr.get("coin") or tr.get("asset") or "USDT").upper()
                d_amt = abs(dec(tr.get("amount")))
                d_px = Decimal("1") if d_cur in ("USDT", "USDC", "USDE", "FDUSD", "USD") else dec(tickers.get(d_cur))
                spot_dep_usd += d_amt * d_px
            continue
        if src_t == "spot_withdraw":
            raw_w_st = tr.get("status")
            if raw_w_st in (None, "") or str(raw_w_st).strip().upper() in ("6", "SUCCESS", "COMPLETED"):
                w_cur = str(tr.get("coin") or tr.get("asset") or "USDT").upper()
                w_amt = abs(dec(tr.get("amount")))
                w_px = Decimal("1") if w_cur in ("USDT", "USDC", "USDE", "FDUSD", "USD") else dec(tickers.get(w_cur))
                spot_wd_usd += w_amt * w_px
            continue
        if src_t != "fut_transfer" or str(tr.get("state") or "SUCCESS").upper() != "SUCCESS":
            continue
        cur_t = str(tr.get("currency") or "USDT").upper()
        amt_t = abs(dec(tr.get("amount")))
        tp_t = str(tr.get("type") or "").upper()
        txid_t = str(tr.get("txid") or "")
        ts_t = norm_sec(tr.get("createTime") or tr.get("_time_ms"))
        if amt_t <= 0:
            continue
        if tp_t == "IN" and txid_t.startswith("ContractHold_"):
            val_ch = amt_t if cur_t in ("USDT", "USDC", "USD") else amt_t * (dec(tickers.get(cur_t)) or Decimal("0"))
            contract_hold_earn_usd += val_ch
            if ts_t > 0:
                daily_contract_hold[fmt_bj(ts_t, "%Y-%m-%d")] += val_ch
        elif cur_t in ("USDT", "USDC", "USDE", "FDUSD", "USD"):
            has_ext_flow = True
            if tp_t == "IN":
                cap_in_usd += amt_t
            elif tp_t == "OUT":
                cap_out_usd += amt_t
        elif cur_t == "MX":
            has_ext_flow = True
            if tp_t == "IN":
                mx_cap_in_qty += amt_t
            elif tp_t == "OUT":
                mx_cap_out_qty += amt_t
        else:
            px_ot = dec(tickers.get(cur_t))
            if px_ot > 0:
                has_ext_flow = True
                if tp_t == "IN":
                    cap_in_usd += amt_t * px_ot
                elif tp_t == "OUT":
                    cap_out_usd += amt_t * px_ot

    mx_net_cap_usd = (mx_cap_in_qty - mx_cap_out_qty) * avg_mx_fee_px
    ext_net_principal_usd = (cap_in_usd - cap_out_usd) + mx_net_cap_usd
    if not has_ext_flow and (spot_dep_usd > 0 or spot_wd_usd > 0):
        has_ext_flow = True
        ext_net_principal_usd = spot_dep_usd - spot_wd_usd
    kickback_pool_usd = sum((v for _, v in kickback_rebates), Decimal("0"))

    deals_by_pid: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    unlinked_by_sym: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for dl in mexc_deals:
        oid_d = str(dl.get("orderId") or "").strip()
        ord_d = order_by_id.get(oid_d)
        pid_d = str((ord_d or {}).get("positionId") or dl.get("positionId") or "").strip()
        if pid_d and pid_d != "0":
            deals_by_pid[pid_d].append(dl)
        else:
            sym_d = str(dl.get("symbol") or "").strip().upper()
            if sym_d:
                unlinked_by_sym[sym_d].append(dl)

    used_unlinked_ids: set[str] = set()
    records: list[dict[str, Any]] = []
    used_record_keys: set[str] = set()

    total_mx_burned_qty = Decimal("0")
    total_mx_saved_usd = Decimal("0")
    total_mx_paid_fees_usd = Decimal("0")
    total_mxpoint_relief_usd = Decimal("0")

    sorted_positions = sorted(
        mexc_positions_history,
        key=lambda p: (
            norm_ms(p.get("updateTime") or p.get("createTime")),
            norm_ms(p.get("createTime")),
            str(p.get("positionId") or ""),
        ),
    )

    for hp in sorted_positions:
        pid = str(hp.get("positionId") or "").strip()
        sym = normalize_contract_symbol(str(hp.get("symbol") or "").strip().upper())
        if not sym:
            continue
        mult = _mx_mult(sym)
        is_short = int(float(hp.get("positionType") or 1)) == 2
        side_str = "SHORT" if is_short else "LONG"
        p_open_ms = norm_ms(hp.get("createTime"))
        p_close_ms = norm_ms(hp.get("updateTime") or hp.get("createTime"))

        p_deals = list(deals_by_pid.get(pid) or [])
        if unlinked_by_sym.get(sym):
            for udl in unlinked_by_sym[sym]:
                uid_d = str(udl.get("id") or "")
                uts = norm_ms(udl.get("timestamp"))
                if int(dec(udl.get("side"))) not in ((2, 3) if is_short else (1, 4)):
                    continue
                if uid_d not in used_unlinked_ids and p_open_ms <= uts <= p_close_ms:
                    p_deals.append(udl)
                    if uid_d:
                        used_unlinked_ids.add(uid_d)

        p_deals.sort(key=lambda x: (norm_ms(x.get("timestamp")), int(dec(x.get("id"), "0"))))

        dlist_map: dict[str, tuple[Decimal, Decimal]] = {}
        for ditem in hp.get("deductFeeList") or []:
            dc = str(ditem.get("currency") or "").strip().upper()
            if dc:
                dlist_map[dc] = (abs(dec(ditem.get("deductFee"))), abs(dec(ditem.get("convertSettleFee"))))

        def _enrich_mx_deal(dl: Mapping[str, Any]) -> dict[str, Any]:
            vol = abs(dec(dl.get("vol")))
            px = dec(dl.get("price"))
            notional = vol * mult * px
            is_taker = bool(dl.get("taker", True))
            is_maker = not is_taker
            oid = str(dl.get("orderId") or "").strip()
            ord_obj = order_by_id.get(oid) or {}
            ext_oid = str(ord_obj.get("externalOid") or "").strip().lower()
            ord_tp = int(float(ord_obj.get("orderType") or (5 if is_taker else 1)))
            cat_tp = int(float(ord_obj.get("category") or dl.get("category") or 1))
            rate_ord = abs(dec(ord_obj.get("makerFeeRate" if is_maker else "takerFeeRate")))
            nom_fee = notional * rate_ord

            fcur = str(dl.get("feeCurrency") or ord_obj.get("feeCurrency") or "USDT").strip().upper()
            fval = abs(dec(dl.get("fee")))
            fee_usd = Decimal("0")
            reb_usd = Decimal("0")
            mx_saved = Decimal("0")
            mx_qty = Decimal("0")
            f_asset = "USDT"
            if fval > 0:
                if fcur == "MX":
                    dm, cu = dlist_map.get("MX", (Decimal("0"), Decimal("0")))
                    fee_usd = (fval / dm * cu) if dm > 0 else (fval * avg_mx_fee_px)
                    mx_saved = max(Decimal("0"), nom_fee - fee_usd)
                    mx_qty = fval
                    f_asset = "MX"
                elif fcur == "MXPOINT":
                    dm, cu = dlist_map.get("MXPOINT", (Decimal("0"), Decimal("0")))
                    fee_usd = (fval / dm * cu) if dm > 0 else fval
                    reb_usd = fee_usd
                    f_asset = "MXPOINT"
                else:
                    fee_usd = dec(dl.get("fee"))
                    f_asset = "USDT"

            ts_ms = norm_ms(dl.get("timestamp"))
            return {
                "order_id": oid,
                "side_code": int(float(dl.get("side") or 0)),
                "vol": vol,
                "px": px,
                "notional": notional,
                "is_maker": is_maker,
                "ord_type": ord_tp,
                "category": cat_tp,
                "ext_oid": ext_oid,
                "fee_usd": fee_usd,
                "reb_usd": reb_usd,
                "mx_saved": mx_saved,
                "mx_qty": mx_qty,
                "fee_asset": f_asset,
                "profit": reported_amount(dl.get("profit")),
                "fill_id": str(dl.get("id") or ""),
                "fee_known": reported_amount(dl.get("fee")) is not None and (
                    fcur in {"USD", "USDT", "USDC"} or fval == 0 or
                    (fcur in {"MX", "MXPOINT"} and dlist_map.get(fcur, (0, 0))[0] > 0)
                ),
                "ts_ms": ts_ms,
                "ts_sec": norm_sec(ts_ms),
            }

        def _build_mx_leg(items: Sequence[dict[str, Any]], *, is_exit: bool, is_win_hint: bool) -> dict[str, Any]:
            if not items:
                return {
                    "mode": "UNKNOWN",
                    "mode_zh": "账本汇总",
                    "fill_count": 0,
                    "qty_base": 0.0,
                    "avg_price": 0.0,
                    "notional_usd": 0.0,
                    "maker_notional_usd": 0.0,
                    "taker_notional_usd": 0.0,
                    "time_sec": 0,
                    "time_bj": "—",
                    "gross_fee_usd": 0.0,
                    "maker_fee_usd": 0.0,
                    "taker_fee_usd": 0.0,
                    "maker_rebate_usd": 0.0,
                    "taker_rebate_usd": 0.0,
                    "fee_bps": 0.0,
                    "fee_asset": "USDT",
                    "bnb_fee_qty": 0.0,
                    "bnb_discount_saved_usd": 0.0,
                    "settled_rebate_usd": 0.0,
                    "pending_rebate_usd": 0.0,
                    "net_fee_usd": 0.0,
                    "rebate_pct": 0.0,
                    "rebate_status": "NONE",
                    "rebate_settled_bj": "",
                }
            tot_v = sum((x["vol"] for x in items), Decimal("0"))
            qty_b = tot_v * mult
            avg_px = (sum((x["vol"] * x["px"] for x in items), Decimal("0")) / tot_v) if tot_v > 0 else Decimal("0")
            not_u = sum((x["notional"] for x in items), Decimal("0"))
            m_not = sum((x["notional"] for x in items if x["is_maker"]), Decimal("0"))
            t_not = sum((x["notional"] for x in items if not x["is_maker"]), Decimal("0"))
            g_fee = sum((x["fee_usd"] for x in items), Decimal("0"))
            m_fee = sum((x["fee_usd"] for x in items if x["is_maker"]), Decimal("0"))
            t_fee = sum((x["fee_usd"] for x in items if not x["is_maker"]), Decimal("0"))
            s_reb = sum((x["reb_usd"] for x in items), Decimal("0"))
            m_reb = sum((x["reb_usd"] for x in items if x["is_maker"]), Decimal("0"))
            t_reb = sum((x["reb_usd"] for x in items if not x["is_maker"]), Decimal("0"))
            mx_s = sum((x["mx_saved"] for x in items), Decimal("0"))
            mx_q = sum((x["mx_qty"] for x in items), Decimal("0"))
            bps = (g_fee / not_u * Decimal("10000")) if not_u > 0 else Decimal("0")

            makers = sum(1 for x in items if x["is_maker"])
            takers = len(items) - makers
            has_liq = any(x["category"] in (2, 3) for x in items)
            has_stop = any(x["ext_oid"].startswith("stop") for x in items)
            has_rev = any(x["ext_oid"].startswith("psrv") for x in items)
            if has_liq:
                m_code, m_zh = "LIQUIDATION", "强平接管"
            elif makers > 0 and takers == 0:
                m_code, m_zh = "LIMIT_MAKER", "限价挂单"
            elif takers > 0 and makers == 0:
                if is_exit and has_stop:
                    m_code = "TAKE_PROFIT_MARKET" if is_win_hint else "STOP_MARKET_TAKER"
                    m_zh = "止盈市价" if is_win_hint else "止损市价"
                elif has_rev:
                    m_code, m_zh = "REVERSE_MARKET", "反手市价"
                elif all(x["ord_type"] == 1 for x in items):
                    m_code, m_zh = "LIMIT_TAKER", "限价吃单"
                else:
                    m_code, m_zh = "MARKET_TAKER", "市价吃单"
            else:
                m_code, m_zh = "MIXED", "混合成交"

            t_leg_sec = max((x["ts_sec"] for x in items), default=0.0) if is_exit else min((x["ts_sec"] for x in items if x["ts_sec"] > 0), default=0.0)
            assets_s = {x["fee_asset"] for x in items if x["fee_usd"] > 0}
            leg_asset = "MX" if assets_s == {"MX"} else ("MXPOINT" if assets_s == {"MXPOINT"} else ("MIXED" if len(assets_s) > 1 else "USDT"))
            reb_pct = (s_reb / g_fee * Decimal("100")) if g_fee > 0 else Decimal("0")
            r_status = "NONE" if g_fee <= 0 or s_reb <= 0 else "SETTLED"
            r_bj = "赠金全额免佣" if leg_asset == "MXPOINT" and s_reb > 0 else (fmt_bj(t_leg_sec, "%m-%d %H:%M") if s_reb > 0 else "")

            return {
                "mode": m_code,
                "mode_zh": m_zh,
                "fill_count": len(items),
                "qty_base": f2(qty_b, 6),
                "avg_price": f2(avg_px, 6),
                "notional_usd": f2(not_u, 2),
                "maker_notional_usd": f2(m_not, 2),
                "taker_notional_usd": f2(t_not, 2),
                "time_sec": int(t_leg_sec),
                "time_bj": fmt_bj(t_leg_sec, "%m-%d %H:%M:%S") if t_leg_sec > 0 else "—",
                "gross_fee_usd": f2(g_fee, 4),
                "maker_fee_usd": f2(m_fee, 4),
                "taker_fee_usd": f2(t_fee, 4),
                "maker_rebate_usd": f2(m_reb, 4),
                "taker_rebate_usd": f2(t_reb, 4),
                "fee_bps": f2(bps, 2),
                "fee_asset": leg_asset,
                "bnb_fee_qty": f2(mx_q, 6),
                "bnb_discount_saved_usd": f2(mx_s, 4),
                "settled_rebate_usd": f2(s_reb, 4),
                "pending_rebate_usd": 0.0,
                "net_fee_usd": f2(max(Decimal("0"), g_fee - s_reb), 4),
                "rebate_pct": f2(reb_pct, 1),
                "rebate_status": r_status,
                "rebate_settled_bj": r_bj,
            }

        enriched = [_enrich_mx_deal(dl) for dl in p_deals]
        close_items_all = [x for x in enriched if x["side_code"] in (2, 4)]

        if close_items_all:
            distinct_close_oids = {x["order_id"] for x in close_items_all if x["order_id"]}
            is_partial_pos = len(distinct_close_oids) > 1
            tot_close_vol = sum((x["vol"] for x in close_items_all), Decimal("0"))
            pos_hold_fee = dec(hp.get("holdFee"))

            entry_items_raw = [dict(x) for x in enriched if x["side_code"] in (1, 3)]
            tot_entry_vol = sum((x["vol"] for x in entry_items_raw), Decimal("0"))
            en_share = (
                (tot_close_vol / tot_entry_vol)
                if (tot_close_vol > 0 and tot_entry_vol > tot_close_vol + Decimal("1e-9"))
                else Decimal("1")
            )
            if en_share < Decimal("1") - Decimal("1e-11"):
                sliced_entries = [
                    {
                        **en_it,
                        "vol": en_it["vol"] * en_share,
                        "notional": en_it["notional"] * en_share,
                        "fee_usd": en_it["fee_usd"] * en_share,
                        "reb_usd": en_it["reb_usd"] * en_share,
                        "mx_saved": en_it["mx_saved"] * en_share,
                        "mx_qty": en_it["mx_qty"] * en_share,
                    }
                    for en_it in entry_items_raw
                    if en_it["vol"] > Decimal("1e-12")
                ]
            else:
                sliced_entries = [en_it for en_it in entry_items_raw if en_it["vol"] > Decimal("1e-12")]

            exit_grp = close_items_all
            parent_gross = reported_amount(hp.get("closeProfitLoss"))
            native_mult = dec((specs.get(sym) or {}).get("contractSize"))
            fill_mult = native_mult if native_mult > 0 else Decimal("1")
            fill_inputs = [
                {"time_ms": x["ts_ms"], "qty": x["vol"] * fill_mult,
                 "qty_unit": "base" if native_mult > 0 else "contracts", "price": x["px"],
                 "order_id": x["order_id"], "fill_id": x["fill_id"],
                 "is_entry": x["side_code"] in (1, 3), "gross_pnl_usd": x["profit"],
                 "fee_usd": x["fee_usd"] if x["fee_known"] else None}
                for x in enriched
            ]
            events, order_pnl = position_fill_accounting(
                fill_inputs, side=side_str, closed_qty=abs(dec(hp.get("closeVol"))) * fill_mult,
                gross_pnl=parent_gross, linear=native_mult > 0,
            )
            gpnl = parent_gross if parent_gross is not None else sum(
                (value for value in order_pnl.values() if value is not None), Decimal("0")
            )
            fund_u = pos_hold_fee

            en_leg = _build_mx_leg(sliced_entries, is_exit=False, is_win_hint=(gpnl > 0))
            ex_leg = _build_mx_leg(exit_grp, is_exit=True, is_win_hint=(gpnl > 0))
            mx_exit_batches: list[dict[str, Any]] = []
            if is_partial_pos and all(x["order_id"] for x in exit_grp):
                mx_by_oid: dict[str, list[dict[str, Any]]] = {}
                for x_it in exit_grp:
                    oid_k = x_it["order_id"] or f"ts_{x_it['ts_ms']}"
                    mx_by_oid.setdefault(oid_k, []).append(x_it)
                ordered_mx_grps = sorted(
                    mx_by_oid.values(),
                    key=lambda g_it: max(y["ts_ms"] for y in g_it),
                )
                for b_i, g_items in enumerate(ordered_mx_grps):
                    b_vol = sum((y["vol"] for y in g_items), Decimal("0"))
                    first = g_items[0]
                    b_gpnl = order_pnl.get(first["order_id"] or f"ts_{first['ts_ms']}")
                    b_leg_mx = _build_mx_leg(g_items, is_exit=True, is_win_hint=(b_gpnl is not None and b_gpnl > 0))
                    mx_exit_batches.append(
                        {
                            **b_leg_mx,
                            "order_id": first["order_id"],
                            "batch_index": b_i + 1,
                            "qty": f2(b_vol * mult, 6),
                            "gross_pnl_usd": f2(b_gpnl, 4) if b_gpnl is not None else None,
                            "time_ms": max(x["ts_ms"] for x in g_items),
                        }
                    )

            all_legs = sliced_entries + exit_grp
            tot_fee = sum((x["fee_usd"] for x in all_legs), Decimal("0"))
            s_reb = sum((x["reb_usd"] for x in all_legs), Decimal("0"))
            mx_s_rec = sum((x["mx_saved"] for x in all_legs), Decimal("0"))
            mx_q_rec = sum((x["mx_qty"] for x in all_legs), Decimal("0"))

            if mx_q_rec > 0:
                total_mx_burned_qty += mx_q_rec
                total_mx_saved_usd += mx_s_rec
                total_mx_paid_fees_usd += sum((x["fee_usd"] for x in all_legs if x["fee_asset"] == "MX"), Decimal("0"))
            if s_reb > 0:
                total_mxpoint_relief_usd += s_reb

            book_pnl = gpnl + fund_u - tot_fee
            real_net = book_pnl + s_reb
            t_open_ms = min((x["ts_ms"] for x in sliced_entries if x["ts_ms"] > 0), default=p_open_ms)
            t_close_ms = max((x["ts_ms"] for x in exit_grp if x["ts_ms"] > 0), default=p_close_ms)
            t_open_s = norm_sec(t_open_ms)
            t_close_s = norm_sec(t_close_ms)
            hold_s = max(0, int(t_close_s - t_open_s))
            qty_b = tot_close_vol * mult

            assets_rec = {x["fee_asset"] for x in all_legs if x["fee_usd"] > 0}
            rec_asset = "MX" if assets_rec == {"MX"} else ("MXPOINT" if assets_rec == {"MXPOINT"} else ("MIXED" if ("MX" in assets_rec or "MXPOINT" in assets_rec) else "USDT"))

            c_oid = exit_grp[-1]["order_id"] or str(len(records))
            base_rk = f"mx_fut_{sym}_{pid or c_oid}"
            rk = base_rk if base_rk not in used_record_keys else f"{base_rk}_{c_oid}_{len(records)}"
            used_record_keys.add(rk)
            legacy_mx_keys = [f"mx_fut_{sym}_{pid}", *[f"mx_fut_{sym}_{pid}_{oid}" for oid in sorted(distinct_close_oids)]]
            j_entry = merge_legacy_journal_entries(journals, rk, legacy_mx_keys)

            records.append(
                {
                    "record_key": rk,
                    "exchange": "MEXC",
                    "exchange_zh": "MEXC",
                    "contract": sym,
                    "side": side_str,
                    "is_partial_close": is_partial_pos,
                    "qty_base": f2(qty_b, 6),
                    "open_time_sec": int(t_open_s),
                    "close_time_sec": int(t_close_s),
                    "open_time_ms": int(t_open_ms),
                    "close_time_ms": int(t_close_ms),
                    "open_datetime_bj": fmt_bj(t_open_s, "%Y-%m-%d %H:%M:%S"),
                    "close_datetime_bj": fmt_bj(t_close_s, "%Y-%m-%d %H:%M:%S"),
                    "close_date_bj": fmt_bj(t_close_s, "%Y-%m-%d"),
                    "holding_seconds": hold_s,
                    "holding_zh": format_duration_zh(hold_s),
                    "entry_leg": en_leg,
                    "exit_leg": ex_leg,
                    "exit_batches": mx_exit_batches,
                    "position_events": events,
                    "gross_pnl_usd": f2(gpnl, 4),
                    "funding_pnl_usd": f2(fund_u, 4),
                    "total_fee_usd": f2(tot_fee, 4),
                    "fee_asset": rec_asset,
                    "bnb_fee_qty": f2(mx_q_rec, 6),
                    "bnb_discount_saved_usd": f2(mx_s_rec, 4),
                    "settled_rebate_usd": f2(s_reb, 4),
                    "pending_rebate_usd": 0.0,
                    "net_fee_usd": f2(max(Decimal("0"), tot_fee - s_reb), 4),
                    "effective_rebate_pct": f2((s_reb / tot_fee * Decimal("100")), 2) if tot_fee > 0 else 0.0,
                    "rebate_state": "SETTLED",
                    "gate_book_pnl_usd": f2(book_pnl, 4),
                    "realized_net_pnl_usd": f2(real_net, 4),
                    "expected_net_pnl_usd": f2(real_net, 4),
                    "_raw_realized_net": real_net,
                    "_raw_book_pnl": book_pnl,
                    "_raw_settled_rebate": s_reb,
                    "tags": list(j_entry.get("tags") or []),
                    "note": str(j_entry.get("note") or ""),
                }
            )
        else:
            c_vol = abs(dec(hp.get("closeVol")))
            qty_b = c_vol * mult
            en_px = dec(hp.get("openAvgPrice"))
            ex_px = dec(hp.get("closeAvgPrice"))
            gpnl = dec(hp.get("closeProfitLoss"))
            fund_u = dec(hp.get("holdFee"))
            u_fee = abs(dec(hp.get("fee")))
            mx_q, mx_u = dlist_map.get("MX", (Decimal("0"), Decimal("0")))
            _, mp_u = dlist_map.get("MXPOINT", (Decimal("0"), Decimal("0")))
            tot_fee = u_fee + mx_u + mp_u
            s_reb = mp_u
            mx_s = abs(dec(hp.get("lowSaveTotalFee"))) if mx_q > 0 else Decimal("0")
            if mx_q > 0:
                total_mx_burned_qty += mx_q
                total_mx_saved_usd += mx_s
                total_mx_paid_fees_usd += mx_u
            if s_reb > 0:
                total_mxpoint_relief_usd += s_reb
            book_pnl = gpnl + fund_u - tot_fee
            real_net = book_pnl + s_reb
            t_open_s = norm_sec(p_open_ms)
            t_close_s = norm_sec(p_close_ms)
            hold_s = max(0, int(t_close_s - t_open_s))
            half_f = tot_fee / Decimal("2")
            half_r = s_reb / Decimal("2")
            en_not = qty_b * en_px
            ex_not = qty_b * ex_px
            rec_asset = "MX" if mx_q > 0 else ("MXPOINT" if mp_u > 0 else "USDT")
            rk = f"mx_fut_{sym}_{pid or len(records)}"
            used_record_keys.add(rk)
            j_entry = journals.get(rk) or {}
            records.append(
                {
                    "record_key": rk,
                    "exchange": "MEXC",
                    "exchange_zh": "MEXC",
                    "contract": sym,
                    "side": side_str,
                    "is_partial_close": False,
                    "qty_base": f2(qty_b, 6),
                    "open_time_sec": int(t_open_s),
                    "close_time_sec": int(t_close_s),
                    "open_time_ms": int(p_open_ms),
                    "close_time_ms": int(p_close_ms),
                    "open_datetime_bj": fmt_bj(t_open_s, "%Y-%m-%d %H:%M:%S"),
                    "close_datetime_bj": fmt_bj(t_close_s, "%Y-%m-%d %H:%M:%S"),
                    "close_date_bj": fmt_bj(t_close_s, "%Y-%m-%d"),
                    "holding_seconds": hold_s,
                    "holding_zh": format_duration_zh(hold_s),
                    "entry_leg": {
                        "mode": "MARKET_TAKER",
                        "mode_zh": "账本汇总",
                        "fill_count": 1,
                        "qty_base": f2(qty_b, 6),
                        "avg_price": f2(en_px, 6),
                        "notional_usd": f2(en_not, 2),
                        "maker_notional_usd": 0.0,
                        "taker_notional_usd": f2(en_not, 2),
                        "time_sec": int(t_open_s),
                        "time_bj": fmt_bj(t_open_s, "%m-%d %H:%M:%S"),
                        "gross_fee_usd": f2(half_f, 4),
                        "maker_fee_usd": 0.0,
                        "taker_fee_usd": f2(half_f, 4),
                        "maker_rebate_usd": 0.0,
                        "taker_rebate_usd": f2(half_r, 4),
                        "fee_bps": f2((half_f / en_not * Decimal("10000")) if en_not > 0 else Decimal("0"), 2),
                        "fee_asset": rec_asset,
                        "bnb_fee_qty": f2(mx_q / Decimal("2"), 6),
                        "bnb_discount_saved_usd": f2(mx_s / Decimal("2"), 4),
                        "settled_rebate_usd": f2(half_r, 4),
                        "pending_rebate_usd": 0.0,
                        "net_fee_usd": f2(max(Decimal("0"), half_f - half_r), 4),
                        "rebate_pct": 100.0 if half_r > 0 else 0.0,
                        "rebate_status": "SETTLED" if half_r > 0 else "NONE",
                        "rebate_settled_bj": "赠金全额免佣" if half_r > 0 else "",
                    },
                    "exit_leg": {
                        "mode": "MARKET_TAKER",
                        "mode_zh": "账本汇总",
                        "fill_count": 1,
                        "qty_base": f2(qty_b, 6),
                        "avg_price": f2(ex_px, 6),
                        "notional_usd": f2(ex_not, 2),
                        "maker_notional_usd": 0.0,
                        "taker_notional_usd": f2(ex_not, 2),
                        "time_sec": int(t_close_s),
                        "time_bj": fmt_bj(t_close_s, "%m-%d %H:%M:%S"),
                        "gross_fee_usd": f2(half_f, 4),
                        "maker_fee_usd": 0.0,
                        "taker_fee_usd": f2(half_f, 4),
                        "maker_rebate_usd": 0.0,
                        "taker_rebate_usd": f2(half_r, 4),
                        "fee_bps": f2((half_f / ex_not * Decimal("10000")) if ex_not > 0 else Decimal("0"), 2),
                        "fee_asset": rec_asset,
                        "bnb_fee_qty": f2(mx_q / Decimal("2"), 6),
                        "bnb_discount_saved_usd": f2(mx_s / Decimal("2"), 4),
                        "settled_rebate_usd": f2(half_r, 4),
                        "pending_rebate_usd": 0.0,
                        "net_fee_usd": f2(max(Decimal("0"), half_f - half_r), 4),
                        "rebate_pct": 100.0 if half_r > 0 else 0.0,
                        "rebate_status": "SETTLED" if half_r > 0 else "NONE",
                        "rebate_settled_bj": "赠金全额免佣" if half_r > 0 else "",
                    },
                    "gross_pnl_usd": f2(gpnl, 4),
                    "funding_pnl_usd": f2(fund_u, 4),
                    "total_fee_usd": f2(tot_fee, 4),
                    "fee_asset": rec_asset,
                    "bnb_fee_qty": f2(mx_q, 6),
                    "bnb_discount_saved_usd": f2(mx_s, 4),
                    "settled_rebate_usd": f2(s_reb, 4),
                    "pending_rebate_usd": 0.0,
                    "net_fee_usd": f2(max(Decimal("0"), tot_fee - s_reb), 4),
                    "effective_rebate_pct": f2((s_reb / tot_fee * Decimal("100")), 2) if tot_fee > 0 else 0.0,
                    "rebate_state": "SETTLED",
                    "gate_book_pnl_usd": f2(book_pnl, 4),
                    "realized_net_pnl_usd": f2(real_net, 4),
                    "expected_net_pnl_usd": f2(real_net, 4),
                    "_raw_realized_net": real_net,
                    "_raw_book_pnl": book_pnl,
                    "_raw_settled_rebate": s_reb,
                    "tags": list(j_entry.get("tags") or []),
                    "note": str(j_entry.get("note") or ""),
                }
            )

    mx_base_before_disc = total_mx_paid_fees_usd + total_mx_saved_usd
    mx_live_discount_pct = (total_mx_saved_usd / mx_base_before_disc * Decimal("100")) if mx_base_before_disc > 0 else Decimal("0")

    mx_meta = {
        "mx_discount_saved_usd": total_mx_saved_usd,
        "mx_live_discount_pct": mx_live_discount_pct,
        "mxpoint_relief_usd": total_mxpoint_relief_usd,
        "kickback_rebate_usd": kickback_pool_usd,
        "contract_hold_earn_usd": contract_hold_earn_usd,
        "daily_contract_hold": dict(daily_contract_hold),
        "has_ext_flow": has_ext_flow,
        "ext_net_principal_usd": ext_net_principal_usd,
        "avg_mx_fee_px": avg_mx_fee_px,
    }
    for pos in open_positions:
        symbol = normalize_contract_symbol(str(pos.get("symbol") or ""))
        pid = str(pos.get("positionId") or "")
        mult = dec((specs.get(symbol) or {}).get("contractSize"))
        if mult <= 0 or not symbol.endswith(("_USDT", "_USDC")):
            continue
        short = int(dec(pos.get("positionType"))) == 2
        fills = []
        candidates = list(deals_by_pid.get(pid, ())) + list(unlinked_by_sym.get(symbol, ()))
        candidates.sort(key=lambda d: (norm_ms(d.get("timestamp")), dec(d.get("id"))))
        for deal in candidates:
            code = int(dec(deal.get("side")))
            if code not in ((2, 3) if short else (1, 4)) or str(deal.get("id") or "") in used_unlinked_ids:
                continue
            ts = norm_ms(deal.get("timestamp"))
            fee = reported_amount(deal.get("fee"))
            if str(deal.get("feeCurrency") or "USDT").upper() not in ("USDT", "USDC", "USD") and fee:
                fee = None
            fills.append({"time_ms": ts, "is_entry": code in (1, 3), "qty": abs(dec(deal.get("vol"))) * mult,
                          "price": deal.get("price"), "fee_usd": fee, "gross_pnl_usd": deal.get("profit"),
                          "role": "TAKER" if deal.get("taker", True) else "MAKER",
                          "order_id": str(deal.get("orderId") or "")})
        record = open_reduction_record(venue="mexc", symbol=symbol, side="SHORT" if short else "LONG",
                                       fills=fills, remaining_qty=abs(dec(pos.get("holdVol"))) * mult,
                                       avg_entry_price=pos.get("holdAvgPrice") or pos.get("openAvgPrice"),
                                       journals=journals, history=records)
        if record:
            records.append(record)
    carry_open_reduction_notes(records, "mexc", journals)

    return records, mx_meta


def _build_mexc_stock_section(
    mx_lb: Mapping[str, Any],
    mexc_transfers: Sequence[Mapping[str, Any]],
    meta: AdapterMeta,
) -> tuple[VenueStockBundle, Decimal, Decimal, Decimal]:
    mx_sb = mx_lb.get("stock_bundle") or {}
    mx_tickers = mx_lb.get("spot_tickers") or {}
    mx_dyn_cat = mx_sb.get("tokenized_catalog") if isinstance(mx_sb.get("tokenized_catalog"), Mapping) else {}
    mx_sp_trades_map = mx_sb.get("spot_stock_trades") or {}

    holdings: list[dict[str, Any]] = []
    cash_ledger: list[dict[str, Any]] = []
    spot_stk_val = Decimal("0")
    iso_stk_eq = Decimal("0")
    stock_net_pnl = Decimal("0")
    total_fees_usd = Decimal("0")
    total_upnl = Decimal("0")

    for sb_mx in mx_lb.get("spot_balances") or []:
        if not isinstance(sb_mx, Mapping):
            continue
        a_mx = str(sb_mx.get("asset") or "").strip().upper()
        q_mx = dec(sb_mx.get("free")) + dec(sb_mx.get("locked"))
        if q_mx <= 0:
            continue
        t_info = classify_spot_stock_token(a_mx, exchange="MEXC", dynamic_catalog=mx_dyn_cat)
        if not t_info:
            continue
        px_mx = dec(mx_tickers.get(a_mx))
        val_mx = q_mx * px_mx
        recon = reconstruct_spot_cost_basis(
            fills=mx_sp_trades_map.get(a_mx) or [],
            current_qty=q_mx,
            current_price=px_mx,
            token_symbol=a_mx,
            quote_price_map=mx_tickers,
        )
        spot_stk_val += val_mx
        stock_net_pnl += recon["net_pnl_usd"]
        total_fees_usd += recon["total_fee_usd"]
        total_upnl += recon["unrealized_pnl_usd"]
        holdings.append(
            {
                "venue_id": "mexc",
                "venue_name": meta.display_name,
                "venue_name_zh": meta.display_name_zh,
                "symbol": t_info["token_symbol"],
                "name": t_info["name"],
                "channel": "1:1 Stock Token",
                "channel_zh": "1:1美股代币",
                "shares": f2(q_mx, 6),
                "avg_cost_usd": f2(recon["avg_cost_usd"], 2),
                "current_price_usd": f2(px_mx, 2),
                "market_value_usd": f2(val_mx, 2),
                "pnl_usd": f2(recon["unrealized_pnl_usd"], 2),
                "pnl_pct": f2(recon["pnl_pct"], 2),
            }
        )

    mx_rs_net_in = Decimal("0")
    for mtr in mexc_transfers:
        if not isinstance(mtr, Mapping) or str(mtr.get("_source") or "") != "universal_transfer":
            continue
        if str(mtr.get("status") or "SUCCESS").upper() not in ("SUCCESS", "COMPLETED", "CONFIRMED", ""):
            continue
        from_acc = str(mtr.get("fromAccountType") or "").strip().upper()
        to_acc = str(mtr.get("toAccountType") or "").strip().upper()
        if from_acc not in _STOCK_SUBACCOUNT_NAMES and to_acc not in _STOCK_SUBACCOUNT_NAMES:
            continue
        amt_m = abs(dec(mtr.get("amount")))
        cur_m = str(mtr.get("asset") or mtr.get("coin") or "USDT").strip().upper()
        px_m = Decimal("1") if cur_m in ("USDT", "USDC", "USDE", "FDUSD", "USD") else dec(mx_tickers.get(cur_m))
        val_m = amt_m * px_m
        if val_m <= 0:
            continue
        is_into_stock = to_acc in _STOCK_SUBACCOUNT_NAMES and from_acc not in _STOCK_SUBACCOUNT_NAMES
        mx_rs_net_in += val_m if is_into_stock else -val_m
        ts_ms = norm_ms(mtr.get("_time_ms") or mtr.get("timestamp"))
        cash_ledger.append(
            {
                "venue_id": "mexc",
                "venue_name": meta.display_name,
                "venue_name_zh": meta.display_name_zh,
                "time_str": fmt_bj(norm_sec(ts_ms), "%Y-%m-%d %H:%M"),
                "type": "Transfer into RealStocks" if is_into_stock else "Transfer out of RealStocks",
                "type_zh": "划入 RealStocks 美股账户" if is_into_stock else "RealStocks 美股账户转回",
                "symbol": cur_m,
                "change": f"{(val_m if is_into_stock else -val_m):+.2f} {cur_m}",
            }
        )

    mx_live_rs_eq = dec((mx_sb.get("realstocks_account") or {}).get("totalEquity"))
    if mx_live_rs_eq > 0:
        iso_stk_eq += mx_live_rs_eq
        if mx_rs_net_in > 0:
            stock_net_pnl += mx_live_rs_eq - mx_rs_net_in
    elif mx_rs_net_in > Decimal("0.01"):
        iso_stk_eq += mx_rs_net_in
        holdings.append(
            {
                "venue_id": "mexc",
                "venue_name": meta.display_name,
                "venue_name_zh": meta.display_name_zh,
                "symbol": "REALSTOCKS",
                "name": "MEXC RealStocks Account",
                "channel": "US Equity",
                "channel_zh": "美股正股",
                "shares": f2(mx_rs_net_in, 2),
                "avg_cost_usd": 1.0,
                "current_price_usd": 1.0,
                "market_value_usd": f2(mx_rs_net_in, 2),
                "pnl_usd": 0.0,
                "pnl_pct": 0.0,
            }
        )

    mx_fr_raw = mx_sb.get("spot_stock_fee_rate") if isinstance(mx_sb.get("spot_stock_fee_rate"), Mapping) else {}
    has_mx_fr = bool(mx_fr_raw and (mx_fr_raw.get("makerCommission") is not None or mx_fr_raw.get("takerCommission") is not None))
    venue_fee_rate = None
    mx_m_pct = Decimal("0")
    mx_t_pct = Decimal("0")
    if has_mx_fr:
        mx_m_pct = dec(mx_fr_raw.get("makerCommission")) * Decimal("100")
        mx_t_pct = dec(mx_fr_raw.get("takerCommission")) * Decimal("100")
        venue_fee_rate = {
            "venue_id": "mexc",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "summary": f"MEXC {fmt_pct_clean(mx_m_pct)}",
            "summary_zh": f"MEXC {fmt_pct_clean(mx_m_pct)}",
            "maker_fee_pct": f2(mx_m_pct, 3),
            "taker_fee_pct": f2(mx_t_pct, 3),
        }

    return (
        VenueStockBundle(
            equity_usd=f2(iso_stk_eq + spot_stk_val, 2),
            unrealized_pnl_usd=f2(total_upnl, 2),
            non_perp_fees_usd=f2(total_fees_usd, 4),
            maker_fee_pct=f2(mx_m_pct, 3),
            taker_fee_pct=f2(mx_t_pct, 3),
            venue_fee_rate=venue_fee_rate,
            holdings=holdings,
            cash_ledger=cash_ledger,
        ),
        spot_stk_val,
        iso_stk_eq,
        stock_net_pnl,
    )


def build_mexc_venue_ledger(
    store: "LedgerStore",
    meta: AdapterMeta,
    *,
    journals: Mapping[str, Mapping[str, Any]],
    now_s: float,
    global_spot_prices: Mapping[str, float] | None = None,
) -> VenueLedgerBundle | None:
    scope = store.venue_scope("mexc")
    mx_lb = scope.get_kv("live_bundle", None)
    pos_hist = scope.load_records("positions_history", desc=True)
    deals = scope.load_records("deals", desc=False)
    orders = scope.load_records("orders", desc=False)
    transfers = scope.load_records("transfers", desc=False)
    if not mx_lb and not pos_hist and not transfers:
        return None
    mx_lb = mx_lb if isinstance(mx_lb, dict) else {}

    mx_uid = str(mx_lb.get("refer_code") or "")
    mx_spot_tickers = {**(global_spot_prices or {}), **(mx_lb.get("spot_tickers") or {})}
    mx_contract_specs = scope.get_kv("contract_specs", {})

    mx_closed_records, mx_meta = _build_mexc_closed_records(
        mexc_positions_history=pos_hist,
        open_positions=mx_lb.get("futures_positions") or (),
        mexc_deals=deals,
        mexc_orders=orders,
        mexc_transfers=transfers,
        mexc_contract_specs=mx_contract_specs,
        spot_tickers=mx_spot_tickers,
        journals=journals,
    )
    stock_bundle, mx_spot_stk_val, mx_iso_stk_eq, mx_stock_net_usd = _build_mexc_stock_section(mx_lb, transfers, meta)

    mx_futures_equity_usd = Decimal("0")
    mx_futures_available_usd = Decimal("0")
    mx_unrealized_usd = sum((dec(p.get("unrealized") or 0) for p in (mx_lb.get("futures_positions") or [])), Decimal("0"))
    mx_non_usdt_tokens_usd = Decimal("0")
    raw_spot_rows: list[tuple[str, float, float, str]] = []

    for fa_mx in mx_lb.get("futures_assets") or []:
        c_mx = str(fa_mx.get("currency") or "").upper()
        eq_mx = dec(fa_mx.get("equity") or fa_mx.get("cashBalance"))
        av_mx = dec(fa_mx.get("availableBalance") or eq_mx)
        if eq_mx <= 0 and av_mx <= 0:
            continue
        if c_mx in ("USDT", "USDC", "USDE", "FDUSD", "USD"):
            mx_futures_equity_usd += eq_mx
            mx_futures_available_usd += av_mx
        else:
            px_mx = dec(mx_spot_tickers.get(c_mx)) or (mx_meta["avg_mx_fee_px"] if c_mx == "MX" else Decimal("0"))
            val_mx = eq_mx * px_mx
            mx_futures_equity_usd += val_mx
            mx_non_usdt_tokens_usd += val_mx

    mx_spot_equity_usd = Decimal("0")
    for sb_mx in mx_lb.get("spot_balances") or []:
        c_sm = str(sb_mx.get("asset") or "").upper()
        q_sm = dec(sb_mx.get("free")) + dec(sb_mx.get("locked"))
        if q_sm <= 0:
            continue
        raw_spot_rows.append((c_sm, float(q_sm), 0.0, "SPOT"))
        if c_sm in ("USDT", "USDC", "USDE", "FDUSD", "USD"):
            mx_spot_equity_usd += q_sm
        else:
            px_sm = dec(mx_spot_tickers.get(c_sm)) or (mx_meta["avg_mx_fee_px"] if c_sm == "MX" else Decimal("0"))
            val_sm = q_sm * px_sm
            mx_spot_equity_usd += val_sm
            mx_non_usdt_tokens_usd += val_sm

    mx_non_usdt_tokens_usd = max(Decimal("0"), mx_non_usdt_tokens_usd - mx_spot_stk_val)
    mx_total_equity_usd = mx_futures_equity_usd + mx_spot_equity_usd + mx_iso_stk_eq
    has_mexc = bool(mx_uid or mx_total_equity_usd > 0 or mx_closed_records or transfers)
    if not has_mexc:
        return None

    mx_real_raw_sum = sum((dec(r.get("_raw_realized_net", r["realized_net_pnl_usd"])) for r in mx_closed_records), Decimal("0"))
    if mx_meta["has_ext_flow"]:
        mx_principal_usd = mx_meta["ext_net_principal_usd"]
    else:
        mx_principal_usd = max(
            Decimal("0"),
            mx_total_equity_usd - mx_unrealized_usd - mx_real_raw_sum - mx_meta["contract_hold_earn_usd"] - mx_stock_net_usd,
        )

    mx_raw_closed_sum = sum((dec(r.pop("_raw_realized_net", r["realized_net_pnl_usd"])) for r in mx_closed_records), Decimal("0"))
    mx_book_sum_usd = sum((dec(r.pop("_raw_book_pnl", r["gate_book_pnl_usd"])) for r in mx_closed_records), Decimal("0"))
    mx_manual_reb_usd = sum((dec(r.pop("_raw_settled_rebate", r["settled_rebate_usd"])) for r in mx_closed_records), Decimal("0"))
    mx_settled_reb_usd = mx_manual_reb_usd + mx_meta["kickback_rebate_usd"]
    mx_saved_total_usd = mx_meta["mx_discount_saved_usd"]
    mx_futures_net_usd = mx_raw_closed_sum + mx_unrealized_usd + mx_meta["kickback_rebate_usd"]

    if mx_total_equity_usd > 0 or mx_principal_usd != 0 or mx_meta["has_ext_flow"]:
        mx_net_pnl_usd = mx_total_equity_usd - mx_principal_usd
        mx_raw_adj_usd = mx_net_pnl_usd - (mx_futures_net_usd + mx_stock_net_usd + mx_non_usdt_tokens_usd)
    else:
        mx_net_pnl_usd = mx_futures_net_usd + mx_stock_net_usd + mx_meta["contract_hold_earn_usd"]
        mx_raw_adj_usd = mx_meta["contract_hold_earn_usd"]

    public_trades = [
        normalize_internal_record_to_public_trade(
            r,
            venue_id="mexc",
            venue_name=meta.display_name,
            venue_name_zh=meta.display_name_zh,
            color_token=meta.color_token,
        )
        for r in mx_closed_records
    ]
    stock_bundle.perpetuals = build_venue_stock_perpetuals(
        public_trades,
        venue_id="mexc",
        venue_name=meta.display_name,
        venue_name_zh=meta.display_name_zh,
    )

    open_positions: list[dict[str, Any]] = []
    for mxp in mx_lb.get("futures_positions") or []:
        mx_hv = abs(dec(mxp.get("holdVol")))
        if mx_hv <= 0:
            continue
        mx_c = normalize_contract_symbol(str(mxp.get("symbol") or ""))
        mx_cs = dec(((mx_contract_specs or {}).get(mx_c) or {}).get("contractSize"), "0") or DEFAULT_CONTRACTS_MULTIPLIER.get(mx_c, Decimal("1"))
        mx_qb = mx_hv * mx_cs
        is_sh_mx = int(float(mxp.get("positionType") or 1)) == 2
        en_px_mx = dec(mxp.get("openAvgPrice") or mxp.get("holdAvgPrice"))
        mk_px_mx = dec(mxp.get("fairPrice") or mxp.get("markPrice")) or en_px_mx
        lq_px_mx = dec(mxp.get("liquidatePrice"))
        unr_mx = dec(mxp.get("unrealized"))
        not_mx = f2(mx_qb * mk_px_mx, 2)
        upnl_2dp = f2(unr_mx, 2)
        try:
            lev = max(1.0, float(mxp.get("leverage") or 1.0))
        except (TypeError, ValueError):
            lev = 1.0
        uroi = round((upnl_2dp / (not_mx / lev)) * 100.0, 2) if not_mx > 0 else 0.0
        open_positions.append(
            {
                "venue_id": "mexc",
                "venue_name": meta.display_name,
                "color_token": meta.color_token,
                "symbol": mx_c,
                "side": "SHORT" if is_sh_mx else "LONG",
                "leverage": lev,
                "qty": f2(mx_qb, 6),
                "notional_usd": not_mx,
                "entry_price": f2(en_px_mx, 4),
                "mark_price": f2(mk_px_mx, 4),
                "liq_price": f2(lq_px_mx, 4),
                "unrealized_pnl_usd": upnl_2dp,
                "unrealized_roi_pct": uroi,
                "has_stop_order": False,
            }
        )

    eq_2dp = f2(mx_total_equity_usd, 2)
    prin_2dp = f2(mx_principal_usd, 2)
    pnl_2dp = round(eq_2dp - prin_2dp, 2) if (mx_total_equity_usd > 0 or mx_principal_usd != 0 or mx_meta["has_ext_flow"]) else f2(mx_net_pnl_usd, 2)

    reconcile_streams: list[dict[str, Any]] = [
        {
            "key": "mexc_futures",
            "title_zh": "MEXC 合约实收净盈亏",
            "label_zh": "MEXC 合约实收净盈亏",
            "detail_zh": f"MEXC {len(mx_closed_records)} 笔平仓账面 {(mx_book_sum_usd + mx_unrealized_usd):+.2f} U + 赠金免佣 +{mx_settled_reb_usd:.2f} U（另 MX抵扣已省 +{mx_saved_total_usd:.2f} U）",
            "amount_usd": f2(mx_futures_net_usd, 2),
        }
    ]
    if abs(mx_stock_net_usd) >= Decimal("0.01"):
        reconcile_streams.append(
            {
                "key": "mexc_stock",
                "title_zh": "MEXC 美股与代币净损益",
                "label_zh": "MEXC 美股与代币净损益",
                "detail_zh": f"MEXC 美股与 1:1 代币净损益 {mx_stock_net_usd:+.2f} U",
                "amount_usd": f2(mx_stock_net_usd, 2),
            }
        )
    mx_rem_adj = mx_raw_adj_usd + mx_non_usdt_tokens_usd
    if abs(mx_rem_adj) >= Decimal("0.01") or mx_meta["contract_hold_earn_usd"] > 0:
        reconcile_streams.append(
            {
                "key": "mexc_other_net",
                "title_zh": "MEXC 合约持币生息与赠金抵亏净益",
                "label_zh": "MEXC 合约持币生息与赠金抵亏净益",
                "detail_zh": f"ContractHold 持币生息 +{mx_meta['contract_hold_earn_usd']:.2f} U 及赠金抵亏/尾仓净额",
                "amount_usd": f2(mx_rem_adj, 2),
            }
        )
    reconcile_streams = clamp_reconcile_streams_to_target(
        reconcile_streams,
        pnl_2dp,
        fallback_key="mexc_other_net",
        fallback_label_zh="MEXC 账户净值变动",
        fallback_detail_zh="现货与合约账户净值变动",
    )

    today_bj_str = fmt_bj(now_s, "%Y-%m-%d")
    mx_first_day = min((r["close_date_bj"] for r in mx_closed_records if r.get("close_date_bj")), default=today_bj_str)
    daily_pnl: dict[str, Decimal] = defaultdict(Decimal)
    daily_comp: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for mx_r in mx_closed_records:
        for day, amount in trade_daily_amounts(mx_r, dec(mx_r["realized_net_pnl_usd"])).items():
            daily_pnl[day] += amount
            daily_comp[day]["mexc_futures"] += amount
    if mx_unrealized_usd != 0:
        daily_pnl[today_bj_str] += mx_unrealized_usd
        daily_comp[today_bj_str]["mexc_futures"] += mx_unrealized_usd
    if mx_stock_net_usd != 0:
        daily_pnl[mx_first_day] += mx_stock_net_usd
        daily_comp[mx_first_day]["mexc_stock"] += mx_stock_net_usd
    for d_ch, v_ch in mx_meta["daily_contract_hold"].items():
        daily_pnl[d_ch] += v_ch
        daily_comp[d_ch]["mexc_other_net"] += v_ch
    rem_after_ch = mx_rem_adj - mx_meta["contract_hold_earn_usd"]
    if rem_after_ch != 0:
        daily_pnl[mx_first_day] += rem_after_ch
        daily_comp[mx_first_day]["mexc_other_net"] += rem_after_ch

    px_float_map = {**(global_spot_prices or {}), **extract_mexc_spot_prices(store)}
    wallet_balances = build_spot_wallet_balances(
        raw_spot_rows,
        venue_id="mexc",
        venue_name=meta.display_name,
        color_token=meta.color_token,
        spot_prices=px_float_map,
        excluded_symbols={h["symbol"] for h in stock_bundle.holdings},
    )

    mx_all_agg = aggregate_trade_legs_decimal(public_trades)
    mx_base_all = mx_all_agg["g_fee"] + mx_all_agg["disc_saved"]
    mx_comb_relief = ((mx_all_agg["disc_saved"] + mx_all_agg["tot_reb"]) / mx_base_all * Decimal("100")) if mx_base_all > 0 else Decimal("0")

    return VenueLedgerBundle(
        venue_id="mexc",
        exchange_card={
            "venue_id": "mexc",
            "display_name": meta.display_name,
            "display_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "connected": True,
            "equity_usd": eq_2dp,
            "net_principal_usd": prin_2dp,
            "net_pnl_usd": pnl_2dp,
            "futures_equity_usd": f2(mx_futures_equity_usd, 2),
            "available_margin_usd": f2(mx_futures_available_usd, 2),
            "trade_count": len(public_trades),
            "rebate_rate_pct": f2(mx_comb_relief, 1),
            "discount_rate_pct": f2(mx_meta["mx_live_discount_pct"], 1),
            "capabilities": meta.capabilities.model_dump(),
        },
        trades=public_trades,
        open_positions=open_positions,
        spot_holdings=spot_holdings_from_wallets(wallet_balances),
        spot_wallet_balances=wallet_balances,
        reconcile_streams=reconcile_streams,
        daily_pnl=dict(daily_pnl),
        daily_components={d: dict(c) for d, c in daily_comp.items()},
        copy_trading=VenueCopyBundle(),
        stocks=stock_bundle,
        fee_context={"mx_live_discount_pct": mx_meta["mx_live_discount_pct"]},
    )


def build_mexc_fee_matrix_row(
    bundle: VenueLedgerBundle,
    range_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    ex = bundle.exchange_card
    mx_all = aggregate_trade_legs_decimal(range_trades)
    mx_m_gross_bps = (mx_all["m_fee"] / mx_all["m_not"] * Decimal("10000")) if mx_all["m_not"] > 0 else Decimal("0")
    mx_t_gross_bps = (mx_all["t_fee"] / mx_all["t_not"] * Decimal("10000")) if mx_all["t_not"] > 0 else Decimal("0")
    mx_t_net_bps = (mx_all["t_net"] / mx_all["t_not"] * Decimal("10000")) if mx_all["t_not"] > 0 else Decimal("0")
    mx_m_net_rate = (mx_all["m_net"] / mx_all["m_not"]) if mx_all["m_not"] > 0 else Decimal("0")
    mx_savings = max(Decimal("0"), mx_all["t_net"] - mx_all["t_not"] * mx_m_net_rate) if mx_all["m_not"] > 0 else Decimal("0")
    mx_base_before_disc = mx_all["g_fee"] + mx_all["disc_saved"]
    mx_combined_relief_pct = (
        ((mx_all["disc_saved"] + mx_all["tot_reb"]) / mx_base_before_disc * Decimal("100"))
        if mx_base_before_disc > 0
        else Decimal("0")
    )
    m_gross_f = f2(mx_m_gross_bps, 2)
    comb_f = f2(mx_combined_relief_pct, 1)

    blended_gross_bps = (mx_all["g_fee"] / mx_all["tot_not"] * Decimal("10000")) if mx_all["tot_not"] > 0 else Decimal("0")
    blended_net_bps = (max(Decimal("0"), mx_all["tot_net"]) / mx_all["tot_not"] * Decimal("10000")) if mx_all["tot_not"] > 0 else Decimal("0")

    return {
        "venue_id": "mexc",
        "display_name": ex["display_name"],
        "display_name_zh": ex["display_name_zh"],
        "color_token": ex["color_token"],
        "trade_count": len(range_trades),
        "maker_gross_bps": m_gross_f,
        "maker_net_bps": m_gross_f,
        "taker_gross_bps": f2(mx_t_gross_bps, 2),
        "taker_net_bps": f2(mx_t_net_bps, 2),
        "maker_bps_known": bool(mx_all["m_not"] > 0),
        "taker_bps_known": bool(mx_all["t_not"] > 0),
        "blended_gross_bps": f2(blended_gross_bps, 2),
        "blended_net_bps": f2(blended_net_bps, 2),
        "gross_fee_usd": f2(mx_all["g_fee"], 2),
        "discount_saved_usd": round(f2(mx_all["disc_saved"], 2) + f2(mx_all["tot_reb"], 2), 2),
        "discount_label": "MX/Bonus",
        "rebate_usd": f2(mx_all["tot_reb"], 2),
        "rebate_pct": comb_f,
        "net_fee_usd": f2(mx_all["tot_net"], 2),
        "combined_relief_pct": comb_f,
        "potential_maker_savings_usd": f2(mx_savings, 2),
    }
