"""Independent actuarial accounting engine for Bitget (`venue_id = 'bitget'`)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from opentradeledger.adapters.base import (
    AdapterMeta,
    VenueCopyBundle,
    VenueLedgerBundle,
    VenueStockBundle,
)
from opentradeledger.adapters.stock_channels import (
    US_EQUITY_TICKERS,
    _extract_fill_fields,
    classify_spot_stock_token,
    reconstruct_spot_cost_basis,
)
from opentradeledger.core.venue_primitives import (
    TZ_BEIJING,
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
    norm_ms,
    norm_sec,
    normalize_contract_symbol,
    normalize_internal_record_to_public_trade,
)

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

BG_PENDING_REBATE_WINDOW_SEC = 86_400


def extract_bitget_spot_prices(store: "LedgerStore") -> dict[str, float]:
    lb = store.venue_scope("bitget").get_kv("live_bundle", {}) or {}
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


def _build_bitget_closed_records(
    *,
    bitget_positions_history: Sequence[Mapping[str, Any]],
    bitget_fills: Sequence[Mapping[str, Any]],
    bitget_orders: Sequence[Mapping[str, Any]],
    bitget_bills: Sequence[Mapping[str, Any]],
    bitget_fee_rates: Mapping[str, Mapping[str, Any]] | None,
    spot_tickers: Mapping[str, Any],
    journals: Mapping[str, Mapping[str, Any]],
    now_f: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    bgb_px = dec(spot_tickers.get("BGB"), "0")
    sym_rates: dict[str, dict[str, Decimal]] = {}
    if isinstance(bitget_fee_rates, Mapping):
        for s_k, r_v in bitget_fee_rates.items():
            if isinstance(r_v, Mapping):
                sym_rates[str(s_k).strip().upper()] = {
                    "maker": abs(dec(r_v.get("makerFeeRate"))),
                    "taker": abs(dec(r_v.get("takerFeeRate"))),
                }

    order_by_id: dict[str, Mapping[str, Any]] = {
        str(bo.get("orderId")).strip(): bo for bo in bitget_orders if bo.get("orderId")
    }

    def _bg_coin_usd(coin: str, amt: Decimal) -> Decimal:
        cu = (coin or "USDT").strip().upper()
        if cu in ("USDT", "USDC", "FDUSD", "USD1", "USD"):
            return amt
        if cu == "BGB" and bgb_px > 0:
            return amt * bgb_px
        px = dec(spot_tickers.get(cu))
        return amt * px if px > 0 else Decimal("0")

    has_v3_dep_wd = any(str(bl.get("_source") or "") in ("v3_deposit", "v3_withdraw") for bl in bitget_bills)
    rebate_dedup: dict[tuple[int, str], tuple[float, Decimal]] = {}
    flow_dedup: set[tuple[str, int, str]] = set()
    inflow_usd = Decimal("0")
    outflow_usd = Decimal("0")
    has_ext_flow = False
    latest_rebate_sec = 0

    for bl in bitget_bills:
        src_b = str(bl.get("_source") or "")
        b_tp = str(
            bl.get("businessType")
            or bl.get("spotTaxType")
            or bl.get("futureTaxType")
            or bl.get("type")
            or bl.get("billType")
            or ""
        ).strip().lower()
        grp_tp = str(bl.get("groupType") or "").strip().lower()
        coin_b = str(bl.get("coin") or bl.get("currency") or "USDT").upper()
        amt_b = dec(bl.get("amount") or bl.get("size") or bl.get("change"))
        ts_b = norm_sec(
            bl.get("ts") or bl.get("createdTime") or bl.get("cTime") or bl.get("ctime") or bl.get("updatedTime")
        )
        val_b = _bg_coin_usd(coin_b, amt_b)

        if any(k in b_tp for k in ("rebate", "commission_return", "invite", "cashback")) and val_b > 0:
            r_key = (int(ts_b // 60), f"{val_b:.4f}")
            if r_key not in rebate_dedup:
                rebate_dedup[r_key] = (ts_b, val_b)
            if int(ts_b) > latest_rebate_sec:
                latest_rebate_sec = int(ts_b)
            continue

        if has_v3_dep_wd:
            if src_b == "v3_deposit":
                if str(bl.get("status") or "success").lower() in ("success", "completed", "done") and abs(val_b) > 0:
                    inflow_usd += abs(val_b)
                    has_ext_flow = True
            elif src_b == "v3_withdraw":
                if str(bl.get("status") or "success").lower() in ("success", "completed", "done") and abs(val_b) > 0:
                    outflow_usd += abs(val_b)
                    has_ext_flow = True
        else:
            if any(k in b_tp for k in ("deposit", "p2p_in", "fiat_buy", "c2c_buy")) and val_b > 0:
                fk = ("in", int(ts_b // 120), f"{abs(val_b):.2f}")
                if fk not in flow_dedup:
                    flow_dedup.add(fk)
                    inflow_usd += abs(val_b)
                    has_ext_flow = True
            elif (
                any(k in b_tp for k in ("withdraw", "p2p_out", "fiat_sell", "c2c_sell"))
                and "freeze" not in b_tp
                and (val_b < 0 or (grp_tp != "withdraw" and val_b != 0))
            ):
                fk = ("out", int(ts_b // 120), f"{abs(val_b):.2f}")
                if fk not in flow_dedup:
                    flow_dedup.add(fk)
                    outflow_usd += abs(val_b)
                    has_ext_flow = True

    rebate_events = sorted(rebate_dedup.values(), key=lambda x: x[0])
    total_rebate_pool_usd = sum((v for _, v in rebate_events), Decimal("0"))

    fills_by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fl in bitget_fills:
        sym_f = str(fl.get("symbol") or "").strip().upper()
        if not sym_f:
            continue
        ts_ms = norm_ms(fl.get("createdTime") or fl.get("updatedTime") or fl.get("cTime") or fl.get("ctime") or fl.get("execTime"))
        ts_s = norm_sec(ts_ms)
        q_f = abs(dec(fl.get("execQty") or fl.get("baseVolume") or fl.get("size")))
        px_f = dec(fl.get("execPrice") or fl.get("price"))
        not_f = abs(dec(fl.get("execValue") or fl.get("quoteVolume"))) or (q_f * px_f)
        role_f = str(fl.get("tradeScope") or fl.get("execScope") or fl.get("role") or "taker").lower()
        is_m = "maker" in role_f
        oid_f = str(fl.get("orderId") or "").strip()
        pos_side_f = str(fl.get("posSide") or "").strip().lower()
        exec_pnl_f = dec(fl.get("execPnl"))

        fee_detail = fl.get("feeDetail")
        fee_coin = "USDT"
        fee_raw = Decimal("0")
        bgb_saved = Decimal("0")
        bgb_qty = Decimal("0")
        if isinstance(fee_detail, list) and fee_detail:
            for fd in fee_detail:
                fc = str(fd.get("feeCoin") or "USDT").upper()
                fv = abs(dec(fd.get("totalFee") or fd.get("fee")))
                if fc:
                    fee_coin = fc
                fee_raw += _bg_coin_usd(fc, fv)
                if fc == "BGB":
                    bgb_qty += fv
                ded_saved = abs(dec(fd.get("deductionFee") or "0"))
                if ded_saved > 0:
                    bgb_saved += ded_saved
        elif isinstance(fee_detail, Mapping):
            fee_coin = str(fee_detail.get("feeCoin") or "USDT").upper()
            fv = abs(dec(fee_detail.get("totalFee") or fee_detail.get("fee")))
            fee_raw = _bg_coin_usd(fee_coin, fv)
            if fee_coin == "BGB":
                bgb_qty = fv
        else:
            fee_coin = str(fl.get("feeCoin") or "USDT").upper()
            fv = abs(dec(fl.get("fee") or fl.get("execFee")))
            fee_raw = _bg_coin_usd(fee_coin, fv)
            if fee_coin == "BGB":
                bgb_qty = fv

        if fee_coin == "BGB" and fee_raw > 0 and bgb_saved == 0:
            r_cfg = sym_rates.get(sym_f, {})
            live_r = r_cfg.get("maker" if is_m else "taker", Decimal("0"))
            if live_r > 0 and not_f > 0:
                bgb_saved = max(Decimal("0"), not_f * live_r - fee_raw)

        fills_by_sym[sym_f].append(
            {
                "id": str(fl.get("execId") or fl.get("tradeId") or ""),
                "order_id": oid_f,
                "pos_side": pos_side_f,
                "ts_ms": ts_ms,
                "ts_sec": ts_s,
                "qty": q_f,
                "px": px_f,
                "notional": not_f,
                "exec_pnl": exec_pnl_f,
                "fee_usd": fee_raw,
                "fee_asset": fee_coin,
                "bgb_qty": bgb_qty,
                "bgb_saved": bgb_saved,
                "role": "maker" if is_m else "taker",
                "trade_side": str(fl.get("tradeSide") or fl.get("side") or "").lower(),
            }
        )

    for s_list in fills_by_sym.values():
        s_list.sort(key=lambda x: (x["ts_ms"], x["id"]))

    def _classify_bg_leg_mode(flist: list[dict[str, Any]], is_exit: bool) -> tuple[str, str]:
        if is_exit and flist:
            for fx in flist:
                ord_obj = order_by_id.get(fx["order_id"]) or {}
                del_tp = str(ord_obj.get("delegateType") or "").lower()
                if "pos_loss" in del_tp or "stop_loss" in del_tp:
                    return ("STOP_LIMIT", "止损限价") if "limit" in del_tp else ("STOP_MARKET_TAKER", "止损市价")
                if "pos_profit" in del_tp or "take_profit" in del_tp:
                    return ("TP_LIMIT", "止盈限价") if "limit" in del_tp else ("TP_MARKET_TAKER", "止盈市价")
                if "reverse" in del_tp:
                    return "REVERSE_MARKET", "反手市价"
        roles = {x["role"] for x in flist} if flist else {"taker"}
        if roles == {"maker"}:
            return "LIMIT_MAKER", "限价挂单"
        if len(roles) > 1:
            return "MIXED", "混合成交"
        return "MARKET_TAKER", "市价吃单"

    claimed_fill_ids: set[str] = set()
    drafts: list[dict[str, Any]] = []
    total_bgb_saved = Decimal("0")
    total_bgb_qty = Decimal("0")

    sorted_pos = sorted(
        bitget_positions_history,
        key=lambda p: norm_ms(
            p.get("updatedTime") or p.get("uTime") or p.get("utime") or p.get("createdTime") or p.get("cTime") or p.get("ctime")
        ),
    )
    for idx_p, pos in enumerate(sorted_pos):
        raw_sym = str(pos.get("symbol") or "").strip().upper()
        contract = normalize_contract_symbol(raw_sym)
        hold_side = str(pos.get("posSide") or pos.get("holdSide") or "long").lower()
        side_str = "SHORT" if "short" in hold_side else "LONG"
        t_open_ms = norm_ms(pos.get("createdTime") or pos.get("cTime") or pos.get("ctime"))
        t_close_ms = norm_ms(pos.get("updatedTime") or pos.get("uTime") or pos.get("utime") or t_open_ms)
        t_open = norm_sec(t_open_ms)
        t_close = norm_sec(t_close_ms)

        qty_closed = abs(dec(pos.get("closeTotalPos") or pos.get("openTotalPos") or pos.get("size")))
        open_px = dec(pos.get("openPriceAvg") or pos.get("openAvgPrice"))
        close_px = dec(pos.get("closePriceAvg") or pos.get("closeAvgPrice"))
        pnl_gross = dec(pos.get("cumRealisedPnl") or pos.get("pnl"))
        net_profit = dec(pos.get("netProfit"))
        open_fee = abs(dec(pos.get("openFeeTotal") or pos.get("openFee")))
        close_fee = abs(dec(pos.get("closeFeeTotal") or pos.get("closeFee")))
        fund_pnl = dec(pos.get("totalFunding"))
        pid_str = str(pos.get("positionId") or f"{int(t_close)}_{idx_p}")

        en_fills: list[dict[str, Any]] = []
        ex_fills: list[dict[str, Any]] = []
        for f_cand in fills_by_sym.get(raw_sym, []):
            if f_cand["id"] and f_cand["id"] in claimed_fill_ids:
                continue
            if f_cand["pos_side"] and f_cand["pos_side"] not in ("net", "both") and f_cand["pos_side"] != hold_side:
                continue
            if f_cand["ts_sec"] < (t_open - 3.0) or f_cand["ts_sec"] > (t_close + 3.0):
                continue
            if f_cand["id"]:
                claimed_fill_ids.add(f_cand["id"])
            if "close" in f_cand["trade_side"]:
                ex_fills.append(f_cand)
            else:
                en_fills.append(f_cand)

        en_fee_tot = sum((x["fee_usd"] for x in en_fills), Decimal("0")) if en_fills else open_fee
        en_bgb_sav_tot = sum((x["bgb_saved"] for x in en_fills), Decimal("0"))
        en_bgb_q_tot = sum((x["bgb_qty"] for x in en_fills), Decimal("0"))
        ex_bgb_sav_tot = sum((x["bgb_saved"] for x in ex_fills), Decimal("0"))
        ex_bgb_q_tot = sum((x["bgb_qty"] for x in ex_fills), Decimal("0"))
        total_bgb_saved += en_bgb_sav_tot + ex_bgb_sav_tot
        total_bgb_qty += en_bgb_q_tot + ex_bgb_q_tot

        close_groups: list[tuple[str, list[dict[str, Any]]]] = []
        if ex_fills:
            grp_map: dict[str, list[dict[str, Any]]] = {}
            grp_order: list[str] = []
            for xf in ex_fills:
                gk = xf["order_id"] or f"ts_{xf['ts_ms']}"
                if gk not in grp_map:
                    grp_map[gk] = []
                    grp_order.append(gk)
                grp_map[gk].append(xf)
            close_groups = [(gk, grp_map[gk]) for gk in grp_order]

        pos_book_target = (
            net_profit
            if net_profit != 0
            else (
                pnl_gross
                + fund_pnl
                - (en_fee_tot + (sum((x["fee_usd"] for x in ex_fills), Decimal("0")) if ex_fills else close_fee))
            )
        )

        if len(close_groups) <= 1:
            ex_fee_1 = sum((x["fee_usd"] for x in ex_fills), Decimal("0")) if ex_fills else close_fee
            drafts.append(
                {
                    "record_key": f"bg_fut_{contract}_{pid_str}",
                    "contract": contract,
                    "side_str": side_str,
                    "qty_closed": qty_closed,
                    "t_open_ms": t_open_ms,
                    "t_close_ms": t_close_ms,
                    "t_open": t_open,
                    "t_close": t_close,
                    "is_partial_close": False,
                    "en_fills": en_fills,
                    "ex_fills": ex_fills,
                    "en_px": open_px,
                    "ex_px": close_px,
                    "en_not": qty_closed * open_px,
                    "ex_not": qty_closed * close_px,
                    "en_fee": en_fee_tot,
                    "ex_fee": ex_fee_1,
                    "en_bgb_q": en_bgb_q_tot,
                    "ex_bgb_q": ex_bgb_q_tot,
                    "en_bgb_sav": en_bgb_sav_tot,
                    "ex_bgb_sav": ex_bgb_sav_tot,
                    "en_share": Decimal("1"),
                    "pnl_gross": pnl_gross,
                    "fund_pnl": fund_pnl,
                    "book_pnl": pos_book_target,
                }
            )
        else:
            tot_ex_q = sum((x["qty"] for x in ex_fills), Decimal("0")) or qty_closed
            rem_gross = pnl_gross
            rem_fund = fund_pnl
            rem_en_fee = en_fee_tot
            rem_book = pos_book_target
            for g_idx, (gk, g_flist) in enumerate(close_groups):
                is_last = g_idx == len(close_groups) - 1
                g_qty = sum((x["qty"] for x in g_flist), Decimal("0"))
                share = Decimal("1") if (is_last or tot_ex_q <= 0) else min(Decimal("1"), g_qty / tot_ex_q)
                g_ex_not = sum((x["notional"] for x in g_flist), Decimal("0"))
                g_ex_px = (g_ex_not / g_qty) if g_qty > 0 else close_px
                g_ex_fee = sum((x["fee_usd"] for x in g_flist), Decimal("0"))
                g_ex_bgb_q = sum((x["bgb_qty"] for x in g_flist), Decimal("0"))
                g_ex_bgb_sav = sum((x["bgb_saved"] for x in g_flist), Decimal("0"))
                g_close_ms = max(x["ts_ms"] for x in g_flist)
                g_close_s = norm_sec(g_close_ms)

                if is_last:
                    g_en_fee = rem_en_fee
                    g_gross = rem_gross
                    g_fund = rem_fund
                    g_book = rem_book
                else:
                    g_en_fee = en_fee_tot * share
                    raw_exec_pnl = sum((x["exec_pnl"] for x in g_flist), Decimal("0"))
                    g_gross = raw_exec_pnl if raw_exec_pnl != 0 else (pnl_gross * share)
                    g_fund = fund_pnl * share
                    g_book = g_gross + g_fund - g_en_fee - g_ex_fee
                    rem_en_fee -= g_en_fee
                    rem_gross -= g_gross
                    rem_fund -= g_fund
                    rem_book -= g_book

                drafts.append(
                    {
                        "record_key": f"bg_fut_{contract}_{pid_str}_{gk}",
                        "contract": contract,
                        "side_str": side_str,
                        "qty_closed": g_qty,
                        "t_open_ms": t_open_ms,
                        "t_close_ms": g_close_ms,
                        "t_open": t_open,
                        "t_close": g_close_s,
                        "is_partial_close": not is_last,
                        "en_fills": en_fills,
                        "ex_fills": g_flist,
                        "en_px": open_px,
                        "ex_px": g_ex_px,
                        "en_not": g_qty * open_px,
                        "ex_not": g_ex_not,
                        "en_fee": g_en_fee,
                        "ex_fee": g_ex_fee,
                        "en_bgb_q": en_bgb_q_tot * share,
                        "ex_bgb_q": g_ex_bgb_q,
                        "en_bgb_sav": en_bgb_sav_tot * share,
                        "ex_bgb_sav": g_ex_bgb_sav,
                        "en_share": share,
                        "pnl_gross": g_gross,
                        "fund_pnl": g_fund,
                        "book_pnl": g_book,
                    }
                )

    # Detect Strategy Bot (`strategy`) & Copy (`trace`) sessions from `v3_funding_fin`
    ff_seen: set[str] = set()
    ff_sorted: list[Mapping[str, Any]] = []
    for bl in sorted(
        (b for b in bitget_bills if str(b.get("_source") or "") == "v3_funding_fin"),
        key=lambda x: norm_ms(x.get("ts") or x.get("cTime")),
    ):
        fid = str(bl.get("id") or bl.get("recordId") or "").strip()
        if fid and fid in ff_seen:
            continue
        if fid:
            ff_seen.add(fid)
        ff_sorted.append(bl)

    bg_sessions: list[dict[str, Any]] = []
    active_sess: dict[str, Any] | None = None
    for f_row in ff_sorted:
        f_ts = norm_ms(f_row.get("ts") or f_row.get("cTime"))
        f_grp = str(f_row.get("groupType") or "").strip().lower()
        f_tp = str(f_row.get("type") or "").strip().lower()
        f_amt = dec(f_row.get("amount"))
        if f_grp in ("strategy", "trace") and f_amt < 0:
            if active_sess is None:
                active_sess = {
                    "kind": f_grp,
                    "start_ms": f_ts,
                    "end_ms": f_ts,
                    "invested": abs(f_amt),
                    "returned": Decimal("0"),
                    "closed": False,
                    "in_events": [(f_ts, f_grp, abs(f_amt), str(f_row.get("id") or ""))],
                }
            else:
                active_sess["invested"] += abs(f_amt)
                active_sess["end_ms"] = f_ts
                active_sess["in_events"].append((f_ts, f_grp, abs(f_amt), str(f_row.get("id") or "")))
        elif active_sess is not None and f_tp == "transfer_in" and f_amt > Decimal("5"):
            active_sess["end_ms"] = f_ts
            active_sess["returned"] = f_amt
            active_sess["closed"] = True
            active_sess["out_event"] = (f_ts, active_sess["kind"], f_amt, str(f_row.get("id") or ""))
            bg_sessions.append(active_sess)
            active_sess = None
    if active_sess is not None:
        active_sess["end_ms"] = norm_ms(now_f)
        bg_sessions.append(active_sess)

    drafts.sort(key=lambda d: (d["t_close_ms"], d["t_open_ms"], d["record_key"]))

    leg_slots: list[dict[str, Any]] = []
    for d_idx, dr in enumerate(drafts):
        leg_slots.append({"d_idx": d_idx, "side": "en", "ts": dr["t_open"], "fee": dr["en_fee"], "reb": Decimal("0"), "reb_ts": 0})
        leg_slots.append({"d_idx": d_idx, "side": "ex", "ts": dr["t_close"], "fee": dr["ex_fee"], "reb": Decimal("0"), "reb_ts": 0})

    def _bj_day_start_sec(ts_val: float) -> float:
        dt_bj = datetime.fromtimestamp(ts_val, TZ_BEIJING)
        return dt_bj.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    claimed_rebate_total = Decimal("0")
    for r_idx, (reb_ts, reb_amt) in enumerate(rebate_events):
        reb_day_start = _bj_day_start_sec(reb_ts)
        prev_day_start = _bj_day_start_sec(rebate_events[r_idx - 1][0]) if r_idx > 0 else (reb_day_start - 96 * 3600)
        cand_slots = [
            s
            for s in leg_slots
            if s["fee"] > 0 and s["reb"] == 0 and (prev_day_start - 5.0) <= s["ts"] < reb_day_start and (reb_day_start - s["ts"]) <= 120 * 3600
        ]
        if not cand_slots and not has_v3_dep_wd:
            prev_raw = rebate_events[r_idx - 1][0] if r_idx > 0 else (reb_ts - 96 * 3600)
            cand_slots = [
                s
                for s in leg_slots
                if s["fee"] > 0 and s["reb"] == 0 and (prev_raw - 60.0) < s["ts"] <= (reb_ts + 5.0) and (reb_ts - s["ts"]) <= 96 * 3600
            ]
        if not cand_slots:
            continue
        cand_fee_sum = sum((s["fee"] for s in cand_slots), Decimal("0"))
        if cand_fee_sum <= 0:
            continue
        alloc_total = min(reb_amt, cand_fee_sum * Decimal("0.95"))
        rem_alloc = alloc_total
        for s_i, sl in enumerate(cand_slots):
            part = max(Decimal("0"), rem_alloc) if s_i == len(cand_slots) - 1 else (alloc_total * (sl["fee"] / cand_fee_sum))
            rem_alloc -= part
            sl["reb"] += part
            sl["reb_ts"] = int(reb_ts)
        claimed_rebate_total += alloc_total

    matched_fee_denom = sum((s["fee"] for s in leg_slots if s["reb"] > 0), Decimal("0"))
    all_draft_fees = sum((dr["en_fee"] + dr["ex_fee"] for dr in drafts), Decimal("0"))
    if matched_fee_denom > 0 and claimed_rebate_total > 0:
        uta_covered_fees = sum(
            (s["fee"] for s in leg_slots if s["fee"] > 0 and (now_f - s["ts"]) > BG_PENDING_REBATE_WINDOW_SEC),
            Decimal("0"),
        )
        denom_use = uta_covered_fees if uta_covered_fees >= matched_fee_denom else matched_fee_denom
        bg_live_rebate_rate = min(Decimal("0.95"), claimed_rebate_total / denom_use)
    elif all_draft_fees > 0 and total_rebate_pool_usd > 0 and not has_v3_dep_wd:
        alloc_fb = min(total_rebate_pool_usd, all_draft_fees * Decimal("0.95"))
        bg_live_rebate_rate = alloc_fb / all_draft_fees
        for sl in leg_slots:
            if sl["fee"] > 0:
                sl["reb"] = sl["fee"] * bg_live_rebate_rate
                sl["reb_ts"] = latest_rebate_sec
        claimed_rebate_total = alloc_fb
    else:
        bg_live_rebate_rate = Decimal("0")

    unmatched_rebate_usd = max(Decimal("0"), total_rebate_pool_usd - claimed_rebate_total)
    slot_lookup: dict[tuple[int, str], dict[str, Any]] = {(s["d_idx"], s["side"]): s for s in leg_slots}
    records: list[dict[str, Any]] = []

    for d_idx, dr in enumerate(drafts):
        en_sl = slot_lookup[(d_idx, "en")]
        ex_sl = slot_lookup[(d_idx, "ex")]
        is_fresh = bg_live_rebate_rate > 0 and ((now_f - dr["t_close"]) <= BG_PENDING_REBATE_WINDOW_SEC)

        def _finalize_bg_leg(
            flist: list[dict[str, Any]],
            *,
            is_exit: bool,
            qty_leg: Decimal,
            px_leg: Decimal,
            not_leg: Decimal,
            fee_leg: Decimal,
            ts_leg: float,
            bgb_q_leg: Decimal,
            bgb_sav_leg: Decimal,
            share_leg: Decimal,
            sl_info: Mapping[str, Any],
        ) -> tuple[dict[str, Any], Decimal, Decimal]:
            mode_c, mode_z = _classify_bg_leg_mode(flist, is_exit)
            m_not = sum((x["notional"] for x in flist if x["role"] == "maker"), Decimal("0")) * (Decimal("1") if is_exit else share_leg)
            t_not = sum((x["notional"] for x in flist if x["role"] != "maker"), Decimal("0")) * (Decimal("1") if is_exit else share_leg)
            if not flist and not_leg > 0:
                t_not = not_leg
            m_fee = sum((x["fee_usd"] for x in flist if x["role"] == "maker"), Decimal("0")) * (Decimal("1") if is_exit else share_leg)
            t_fee = max(Decimal("0"), fee_leg - m_fee)

            s_r = sl_info["reb"]
            p_r = (fee_leg * bg_live_rebate_rate) if (s_r == 0 and fee_leg > 0 and is_fresh) else Decimal("0")
            eff_r = s_r + p_r
            m_sh = (m_fee / fee_leg) if fee_leg > 0 else Decimal("0")
            bps = (fee_leg / not_leg * Decimal("10000")) if not_leg > 0 else Decimal("0")
            r_st = "PENDING" if (p_r > 0 and s_r == 0) else ("PARTIAL_PENDING" if p_r > 0 else ("SETTLED" if s_r > 0 else "NONE"))
            r_ts = int(sl_info["reb_ts"] or latest_rebate_sec)
            return (
                {
                    "mode": mode_c,
                    "mode_zh": mode_z,
                    "fill_count": max(1, len(flist)),
                    "qty_base": f2(qty_leg, 6),
                    "avg_price": f2(px_leg, 6),
                    "notional_usd": f2(not_leg, 2),
                    "maker_notional_usd": f2(m_not, 2),
                    "taker_notional_usd": f2(t_not, 2),
                    "time_sec": int(ts_leg),
                    "time_bj": fmt_bj(ts_leg, "%m-%d %H:%M:%S") if ts_leg > 0 else "—",
                    "gross_fee_usd": f2(fee_leg, 4),
                    "maker_fee_usd": f2(m_fee, 4),
                    "taker_fee_usd": f2(t_fee, 4),
                    "maker_rebate_usd": f2(eff_r * m_sh, 4),
                    "taker_rebate_usd": f2(eff_r * (Decimal("1") - m_sh), 4),
                    "fee_bps": f2(bps, 2),
                    "fee_asset": "BGB" if bgb_q_leg > 0 else "USDT",
                    "bnb_fee_qty": f2(bgb_q_leg, 6),
                    "bnb_discount_saved_usd": f2(bgb_sav_leg, 4),
                    "settled_rebate_usd": f2(s_r, 4),
                    "pending_rebate_usd": f2(p_r, 4),
                    "net_fee_usd": f2(fee_leg - eff_r, 4),
                    "rebate_pct": f2((eff_r / fee_leg * Decimal("100")), 2) if fee_leg > 0 else 0.0,
                    "rebate_status": r_st,
                    "rebate_settled_bj": fmt_bj(r_ts, "%m-%d %H:%M") if (s_r > 0 and r_ts > 0) else "",
                },
                s_r,
                p_r,
            )

        en_leg, en_sr, en_pr = _finalize_bg_leg(
            dr["en_fills"],
            is_exit=False,
            qty_leg=dr["qty_closed"],
            px_leg=dr["en_px"],
            not_leg=dr["en_not"],
            fee_leg=dr["en_fee"],
            ts_leg=dr["t_open"],
            bgb_q_leg=dr["en_bgb_q"],
            bgb_sav_leg=dr["en_bgb_sav"],
            share_leg=dr["en_share"],
            sl_info=en_sl,
        )
        ex_leg, ex_sr, ex_pr = _finalize_bg_leg(
            dr["ex_fills"],
            is_exit=True,
            qty_leg=dr["qty_closed"],
            px_leg=dr["ex_px"],
            not_leg=dr["ex_not"],
            fee_leg=dr["ex_fee"],
            ts_leg=dr["t_close"],
            bgb_q_leg=dr["ex_bgb_q"],
            bgb_sav_leg=dr["ex_bgb_sav"],
            share_leg=Decimal("1"),
            sl_info=ex_sl,
        )
        tot_fee = dr["en_fee"] + dr["ex_fee"]
        s_reb = en_sr + ex_sr
        p_reb = en_pr + ex_pr
        eff_reb = s_reb + p_reb
        book_pnl = dr["book_pnl"]
        real_net = book_pnl + s_reb
        exp_net = book_pnl + eff_reb
        hold_s = max(0, int(dr["t_close"] - dr["t_open"]))
        record_key = dr["record_key"]
        j_entry = journals.get(record_key) or {}
        tot_bgb_q_rec = dr["en_bgb_q"] + dr["ex_bgb_q"]
        tot_bgb_sav_rec = dr["en_bgb_sav"] + dr["ex_bgb_sav"]

        records.append(
            {
                "record_key": record_key,
                "exchange": "BITGET",
                "exchange_zh": "Bitget",
                "contract": dr["contract"],
                "side": dr["side_str"],
                "qty_base": f2(dr["qty_closed"], 6),
                "open_time_sec": int(dr["t_open"]),
                "close_time_sec": int(dr["t_close"]),
                "open_time_ms": dr["t_open_ms"],
                "close_time_ms": dr["t_close_ms"],
                "open_datetime_bj": fmt_bj(dr["t_open"], "%Y-%m-%d %H:%M:%S"),
                "close_datetime_bj": fmt_bj(dr["t_close"], "%Y-%m-%d %H:%M:%S"),
                "close_date_bj": fmt_bj(dr["t_close"], "%Y-%m-%d"),
                "is_partial_close": bool(dr["is_partial_close"]),
                "holding_seconds": hold_s,
                "holding_zh": format_duration_zh(hold_s),
                "entry_leg": en_leg,
                "exit_leg": ex_leg,
                "gross_pnl_usd": f2(dr["pnl_gross"], 4),
                "funding_pnl_usd": f2(dr["fund_pnl"], 4),
                "total_fee_usd": f2(tot_fee, 4),
                "fee_asset": "BGB" if tot_bgb_q_rec > 0 else "USDT",
                "bnb_fee_qty": f2(tot_bgb_q_rec, 6),
                "bnb_discount_saved_usd": f2(tot_bgb_sav_rec, 4),
                "settled_rebate_usd": f2(s_reb, 4),
                "pending_rebate_usd": f2(p_reb, 4),
                "net_fee_usd": f2(tot_fee - eff_reb, 4),
                "effective_rebate_pct": f2((eff_reb / tot_fee * Decimal("100")), 2) if tot_fee > 0 else 0.0,
                "rebate_state": "PENDING" if (p_reb > 0 and s_reb == 0) else ("PARTIAL_PENDING" if p_reb > 0 else "SETTLED"),
                "gate_book_pnl_usd": f2(book_pnl, 4),
                "realized_net_pnl_usd": f2(real_net, 4),
                "expected_net_pnl_usd": f2(exp_net, 4),
                "_raw_realized_net": real_net,
                "_raw_book_pnl": book_pnl,
                "_raw_settled_rebate": s_reb,
                "tags": list(j_entry.get("tags") or []),
                "note": str(j_entry.get("note") or ""),
            }
        )

    bg_meta = {
        "live_rebate_rate": bg_live_rebate_rate,
        "total_rebate_pool_usd": total_rebate_pool_usd,
        "unmatched_rebate_usd": unmatched_rebate_usd,
        "bgb_discount_saved_usd": total_bgb_saved,
        "has_ext_flow": has_ext_flow,
        "ext_net_principal_usd": inflow_usd - outflow_usd,
        "copy_sessions": bg_sessions,
    }
    return records, bg_meta


def _build_bitget_stock_section(
    bg_lb: Mapping[str, Any],
    meta: AdapterMeta,
) -> tuple[VenueStockBundle, Decimal, Decimal, Decimal]:
    bg_sb = bg_lb.get("stock_bundle") or {}
    bg_tickers = bg_lb.get("spot_tickers") or {}
    bg_sp_acc = bg_sb.get("stockplus_account") or {}
    bg_sp_positions = bg_sb.get("stockplus_positions") or []
    bg_sp_execs = bg_sb.get("stockplus_executions") or []
    bg_sp_cfs = bg_sb.get("stockplus_cashflows") or []
    bg_sp_trs = bg_sb.get("stockplus_transfers") or []

    holdings: list[dict[str, Any]] = []
    cash_ledger: list[dict[str, Any]] = []
    spot_stk_val = Decimal("0")
    iso_stk_eq = Decimal("0")
    stock_net_pnl = Decimal("0")
    total_fees_usd = Decimal("0")
    total_upnl = Decimal("0")
    bg_pos_mkt_sum = Decimal("0")
    bg_pos_unr_sum = Decimal("0")

    for bp in bg_sp_positions:
        if not isinstance(bp, Mapping):
            continue
        vol_b = dec(bp.get("qty") or bp.get("position") or bp.get("holdVol") or bp.get("availableQty") or bp.get("volume"))
        if vol_b <= 0:
            continue
        cost_b = dec(bp.get("avgCostPrice") or bp.get("openAvgPrice") or bp.get("costPrice") or bp.get("avg_cost_price"))
        cur_b = dec(bp.get("marketPrice") or bp.get("lastPrice") or bp.get("currentPrice"))
        mkt_b = dec(bp.get("marketValue") or bp.get("positionValue")) or (vol_b * (cur_b or cost_b))
        if cur_b <= 0 and vol_b > 0:
            cur_b = mkt_b / vol_b
        unr_b = dec(bp.get("unrealizedPnl") or bp.get("unrealisedPnl") or bp.get("positionPnl")) or (mkt_b - vol_b * cost_b)
        pct_b = dec(bp.get("unrealizedPnlRate") or bp.get("pnlRate"))
        if pct_b == 0 and cost_b > 0:
            pct_b = (unr_b / (vol_b * cost_b)) * Decimal("100")
        bg_pos_mkt_sum += mkt_b
        bg_pos_unr_sum += unr_b
        sym_b = str(bp.get("symbol") or "").strip().upper()
        holdings.append(
            {
                "venue_id": "bitget",
                "venue_name": meta.display_name,
                "venue_name_zh": meta.display_name_zh,
                "symbol": sym_b,
                "name": str(bp.get("name") or US_EQUITY_TICKERS.get(sym_b) or sym_b),
                "channel": "US Equity",
                "channel_zh": "美股正股",
                "shares": f2(vol_b, 4),
                "avg_cost_usd": f2(cost_b, 2),
                "current_price_usd": f2(cur_b, 2),
                "market_value_usd": f2(mkt_b, 2),
                "pnl_usd": f2(unr_b, 2),
                "pnl_pct": f2(pct_b, 2),
            }
        )

    bg_sp_eq = Decimal("0")
    if isinstance(bg_sp_acc, Mapping) and bg_sp_acc:
        bg_sp_eq = dec(
            bg_sp_acc.get("totalEquity")
            or bg_sp_acc.get("netLiquidationValue")
            or bg_sp_acc.get("usdtEquity")
            or bg_sp_acc.get("equity")
        )
        if bg_sp_eq <= 0:
            bg_sp_eq = dec(bg_sp_acc.get("cashBalance") or bg_sp_acc.get("available")) + bg_pos_mkt_sum
    elif bg_pos_mkt_sum > 0:
        bg_sp_eq = bg_pos_mkt_sum

    bg_exec_fees = Decimal("0")
    bg_exec_not = Decimal("0")
    for bex in bg_sp_execs:
        if isinstance(bex, Mapping):
            pf = _extract_fill_fields(bex, quote_price_map=bg_tickers)
            bg_exec_fees += pf["fee_usd"]
            bg_exec_not += pf["qty"] * pf["price"]
            total_fees_usd += pf["fee_usd"]

    bg_div_sum = Decimal("0")
    for bcf in bg_sp_cfs:
        if isinstance(bcf, Mapping):
            ts_ms = norm_ms(bcf.get("cTime") or bcf.get("ts") or bcf.get("time"))
            amt_cf = dec(bcf.get("amount") or bcf.get("change"))
            tp_cf = str(bcf.get("type") or bcf.get("bizType") or "CASH_FLOW").upper()
            if "DIV" in tp_cf:
                bg_div_sum += amt_cf
            cash_ledger.append(
                {
                    "venue_id": "bitget",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "time_str": fmt_bj(norm_sec(ts_ms), "%Y-%m-%d %H:%M"),
                    "type": "Equity Cash Dividend" if "DIV" in tp_cf else tp_cf,
                    "type_zh": "美股现金分红" if "DIV" in tp_cf else tp_cf,
                    "symbol": str(bcf.get("symbol") or bcf.get("currency") or "USD"),
                    "change": f"{amt_cf:+.2f} {bcf.get('currency') or 'USD'}",
                }
            )

    bg_tr_net_in = Decimal("0")
    has_bg_sp_tr = False
    for btr in bg_sp_trs:
        if isinstance(btr, Mapping):
            amt_tr = abs(dec(btr.get("amount") or btr.get("size")))
            if amt_tr <= 0:
                continue
            has_bg_sp_tr = True
            dir_tr = str(btr.get("direction") or btr.get("type") or btr.get("toType") or "IN").upper()
            is_out = dir_tr in ("OUT", "WITHDRAW", "TO_SPOT", "TO_UTA") or "OUT" in dir_tr
            bg_tr_net_in += (-amt_tr if is_out else amt_tr)

    if bg_sp_eq > 0 or has_bg_sp_tr or bg_pos_unr_sum != 0:
        iso_stk_eq += bg_sp_eq
        total_upnl += bg_pos_unr_sum
        stock_net_pnl += (bg_sp_eq - bg_tr_net_in) if has_bg_sp_tr else (bg_pos_unr_sum + bg_div_sum - bg_exec_fees)

    bg_spot_qty_val: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    bg_uta = bg_lb.get("uta_assets")
    uta_arr = (bg_uta.get("assets") or bg_uta.get("list") or []) if isinstance(bg_uta, Mapping) else (bg_uta if isinstance(bg_uta, list) else [])
    for ca in uta_arr:
        if isinstance(ca, Mapping):
            c_nm = str(ca.get("coin") or "").strip()
            if classify_spot_stock_token(c_nm, exchange="BITGET"):
                q_c = dec(ca.get("equity") or ca.get("available") or ca.get("balance"))
                cu = c_nm.upper()
                v_c = dec(ca.get("usdtEquity")) or (q_c * dec(bg_tickers.get(cu)))
                if q_c > 0:
                    bg_spot_qty_val[cu][0] += q_c
                    bg_spot_qty_val[cu][1] += v_c
    for fa_r in bg_lb.get("funding_assets") or []:
        if isinstance(fa_r, Mapping):
            c_nm = str(fa_r.get("coin") or "").strip()
            if classify_spot_stock_token(c_nm, exchange="BITGET"):
                q_f = dec(fa_r.get("balance") or fa_r.get("available"))
                cu = c_nm.upper()
                if q_f > 0:
                    bg_spot_qty_val[cu][0] += q_f
                    bg_spot_qty_val[cu][1] += q_f * dec(bg_tickers.get(cu))

    bg_sp_fills_map = bg_sb.get("spot_stock_fills") or {}
    for cu, (q_tot, v_tot) in sorted(bg_spot_qty_val.items()):
        t_info = classify_spot_stock_token(cu, exchange="BITGET")
        if t_info and q_tot > 0:
            px_u = dec(bg_tickers.get(cu)) or ((v_tot / q_tot) if v_tot > 0 else Decimal("0"))
            val_u = v_tot if v_tot > 0 else (q_tot * px_u)
            recon = reconstruct_spot_cost_basis(
                fills=bg_sp_fills_map.get(cu) or [],
                current_qty=q_tot,
                current_price=px_u,
                token_symbol=cu,
                quote_price_map=bg_tickers,
            )
            spot_stk_val += val_u
            stock_net_pnl += recon["net_pnl_usd"]
            total_fees_usd += recon["total_fee_usd"]
            total_upnl += recon["unrealized_pnl_usd"]
            holdings.append(
                {
                    "venue_id": "bitget",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "symbol": t_info["token_symbol"],
                    "name": t_info["name"],
                    "channel": "1:1 Stock Token",
                    "channel_zh": "1:1美股代币",
                    "shares": f2(q_tot, 6),
                    "avg_cost_usd": f2(recon["avg_cost_usd"], 2),
                    "current_price_usd": f2(px_u, 2),
                    "market_value_usd": f2(val_u, 2),
                    "pnl_usd": f2(recon["unrealized_pnl_usd"], 2),
                    "pnl_pct": f2(recon["pnl_pct"], 2),
                }
            )

    bg_fr_raw = bg_sb.get("spot_stock_fee_rate") if isinstance(bg_sb.get("spot_stock_fee_rate"), Mapping) else {}
    bg_obs_real_pct = (bg_exec_fees / bg_exec_not * Decimal("100")) if bg_exec_not > 0 else None
    has_bg_fr = bool(bg_fr_raw and (bg_fr_raw.get("makerFeeRate") is not None or bg_fr_raw.get("takerFeeRate") is not None))
    venue_fee_rate = None
    bg_m_pct = Decimal("0")
    bg_t_pct = Decimal("0")
    if has_bg_fr or bg_obs_real_pct is not None:
        bg_m_pct = dec(bg_fr_raw.get("makerFeeRate")) * Decimal("100") if has_bg_fr else (bg_obs_real_pct or Decimal("0"))
        bg_t_pct = dec(bg_fr_raw.get("takerFeeRate")) * Decimal("100") if has_bg_fr else (bg_obs_real_pct or Decimal("0"))
        venue_fee_rate = {
            "venue_id": "bitget",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "summary": f"Bitget {fmt_pct_clean(bg_m_pct)}",
            "summary_zh": f"Bitget rStock {fmt_pct_clean(bg_m_pct)}",
            "maker_fee_pct": f2(bg_m_pct, 3),
            "taker_fee_pct": f2(bg_t_pct, 3),
        }

    return (
        VenueStockBundle(
            equity_usd=f2(bg_sp_eq + spot_stk_val, 2),
            unrealized_pnl_usd=f2(total_upnl, 2),
            non_perp_fees_usd=f2(total_fees_usd, 4),
            maker_fee_pct=f2(bg_m_pct, 3),
            taker_fee_pct=f2(bg_t_pct, 3),
            venue_fee_rate=venue_fee_rate,
            holdings=holdings,
            cash_ledger=cash_ledger,
        ),
        spot_stk_val,
        iso_stk_eq,
        stock_net_pnl,
    )


def build_bitget_venue_ledger(
    store: "LedgerStore",
    meta: AdapterMeta,
    *,
    journals: Mapping[str, Mapping[str, Any]],
    now_s: float,
    global_spot_prices: Mapping[str, float] | None = None,
) -> VenueLedgerBundle | None:
    scope = store.venue_scope("bitget")
    bg_lb = scope.get_kv("live_bundle", None)
    pos_hist = scope.load_records("positions_history", desc=True)
    fills = scope.load_records("fills", desc=False)
    orders = scope.load_records("orders", desc=False)
    bills = scope.load_records("bills", desc=False)
    if not bg_lb and not pos_hist and not bills:
        return None
    bg_lb = bg_lb if isinstance(bg_lb, dict) else {}

    bg_uid = str(bg_lb.get("uid") or "")
    bg_spot_tickers = bg_lb.get("spot_tickers") or {}
    bg_fee_rates = scope.get_kv("fee_rates", {})

    bg_all_records, bg_meta = _build_bitget_closed_records(
        bitget_positions_history=pos_hist,
        bitget_fills=fills,
        bitget_orders=orders,
        bitget_bills=bills,
        bitget_fee_rates=bg_fee_rates,
        spot_tickers=bg_spot_tickers,
        journals=journals,
        now_f=now_s,
    )
    bg_sess_records = [r for r in bg_all_records if str(r.get("record_key") or "").startswith("bg_sess_")]
    bg_closed_records = [r for r in bg_all_records if not str(r.get("record_key") or "").startswith("bg_sess_")]

    stock_bundle, bg_spot_stk_val, bg_iso_stk_eq, bg_stock_net_usd = _build_bitget_stock_section(bg_lb, meta)

    bg_uta_equity_usd = Decimal("0")
    bg_futures_available_usd = Decimal("0")
    bg_unrealized_usd = sum(
        (dec(p.get("unrealizedPL") or p.get("unrealisedPnl") or 0) for p in (bg_lb.get("futures_positions") or [])),
        Decimal("0"),
    )
    bg_non_usdt_tokens_usd = Decimal("0")
    raw_spot_rows: list[tuple[str, float, float, str]] = []
    uta_assets = bg_lb.get("uta_assets")
    if isinstance(uta_assets, Mapping) and uta_assets:
        bg_uta_equity_usd = dec(uta_assets.get("accountEquity") or uta_assets.get("usdtEquity") or uta_assets.get("totalEquity"))
        bg_futures_available_usd = dec(uta_assets.get("available") or uta_assets.get("usdtAvailable") or bg_uta_equity_usd)
        assets_arr = uta_assets.get("assets") or uta_assets.get("list") or []
        if isinstance(assets_arr, list):
            for ca in assets_arr:
                c_nm = str(ca.get("coin") or "").upper()
                q_c = float(ca.get("equity") or ca.get("balance") or ca.get("available") or 0.0)
                raw_spot_rows.append((c_nm, q_c, float(ca.get("usdtEquity") or 0.0), "UTA"))
                if bg_uta_equity_usd == 0:
                    eq_v = dec(ca.get("usdtEquity") or ca.get("equity"))
                    if c_nm in ("USDT", "USDC", "USD1", "USD"):
                        bg_uta_equity_usd += eq_v
                        bg_futures_available_usd += dec(ca.get("available") or eq_v)
                    else:
                        px_c = dec(bg_spot_tickers.get(c_nm), "1" if eq_v > 0 and ca.get("usdtEquity") else "0")
                        val_c = dec(ca.get("usdtEquity")) or (dec(ca.get("equity")) * px_c)
                        bg_uta_equity_usd += val_c
                        bg_non_usdt_tokens_usd += val_c
    else:
        for cb in bg_lb.get("classic_balances") or []:
            bg_uta_equity_usd += dec(cb.get("usdtBalance"))
        for fa_bg in bg_lb.get("futures_accounts") or []:
            bg_futures_available_usd += dec(fa_bg.get("available") or fa_bg.get("crossedMaxAvailable"))
            if bg_uta_equity_usd == 0:
                bg_uta_equity_usd += dec(fa_bg.get("usdtEquity") or fa_bg.get("accountEquity"))

    bg_funding_usd = Decimal("0")
    for fa_row in bg_lb.get("funding_assets") or []:
        c_f = str(fa_row.get("coin") or "").upper()
        q_f = dec(fa_row.get("balance") or fa_row.get("available"))
        if q_f <= 0:
            continue
        raw_spot_rows.append((c_f, float(q_f), 0.0, "FUNDING"))
        if c_f in ("USDT", "USDC", "FDUSD", "USD1", "USD"):
            bg_funding_usd += q_f
        else:
            val_f = q_f * dec(bg_spot_tickers.get(c_f))
            bg_funding_usd += val_f
            bg_non_usdt_tokens_usd += val_f

    bg_non_usdt_tokens_usd = max(Decimal("0"), bg_non_usdt_tokens_usd - bg_spot_stk_val)
    bg_total_equity_usd = bg_uta_equity_usd + bg_funding_usd + bg_iso_stk_eq
    has_bitget = bool(bg_uid or bg_total_equity_usd > 0 or bg_all_records or bills)
    if not has_bitget:
        return None

    bg_book_raw_sum = sum((dec(r.get("_raw_book_pnl", r["gate_book_pnl_usd"])) for r in bg_all_records), Decimal("0"))
    if bg_meta["has_ext_flow"]:
        bg_principal_usd = bg_meta["ext_net_principal_usd"]
    else:
        bg_principal_usd = max(
            Decimal("0"),
            bg_total_equity_usd - bg_unrealized_usd - bg_book_raw_sum - bg_meta["total_rebate_pool_usd"] - bg_stock_net_usd,
        )

    bg_raw_closed_sum = sum((dec(r.pop("_raw_realized_net", r["realized_net_pnl_usd"])) for r in bg_closed_records), Decimal("0"))
    bg_book_sum_usd = sum((dec(r.pop("_raw_book_pnl", r["gate_book_pnl_usd"])) for r in bg_closed_records), Decimal("0"))
    bg_manual_reb_usd = sum((dec(r.pop("_raw_settled_rebate", r["settled_rebate_usd"])) for r in bg_closed_records), Decimal("0"))
    bg_copy_net_usd = sum((dec(r.pop("_raw_realized_net", r["realized_net_pnl_usd"])) for r in bg_sess_records), Decimal("0"))
    bg_copy_book_usd = sum((dec(r.pop("_raw_book_pnl", r["gate_book_pnl_usd"])) for r in bg_sess_records), Decimal("0"))
    bg_copy_reb_usd = sum((dec(r.pop("_raw_settled_rebate", r["settled_rebate_usd"])) for r in bg_sess_records), Decimal("0"))

    has_bg_copy = bool((bg_meta.get("copy_sessions") or []) or bg_sess_records)
    bg_futures_net_usd = bg_raw_closed_sum + bg_unrealized_usd
    if bg_total_equity_usd > 0 or bg_principal_usd != 0 or bg_meta["has_ext_flow"]:
        bg_net_pnl_usd = bg_total_equity_usd - bg_principal_usd
        bg_raw_adj_usd = bg_net_pnl_usd - (bg_futures_net_usd + bg_copy_net_usd + bg_stock_net_usd + bg_non_usdt_tokens_usd)
    else:
        bg_net_pnl_usd = bg_futures_net_usd + bg_copy_net_usd + bg_stock_net_usd + bg_meta["unmatched_rebate_usd"]
        bg_raw_adj_usd = bg_meta["unmatched_rebate_usd"]

    public_trades = [
        normalize_internal_record_to_public_trade(
            r,
            venue_id="bitget",
            venue_name=meta.display_name,
            venue_name_zh=meta.display_name_zh,
            color_token=meta.color_token,
        )
        for r in bg_closed_records
    ]
    stock_bundle.perpetuals = build_venue_stock_perpetuals(
        public_trades,
        venue_id="bitget",
        venue_name=meta.display_name,
        venue_name_zh=meta.display_name_zh,
    )

    open_positions: list[dict[str, Any]] = []
    for bgp in bg_lb.get("futures_positions") or []:
        bg_q = abs(dec(bgp.get("total") or bgp.get("size") or bgp.get("pos")))
        if bg_q == 0:
            continue
        bg_c = normalize_contract_symbol(str(bgp.get("symbol") or ""))
        h_side = str(bgp.get("holdSide") or bgp.get("posSide") or "long").lower()
        is_sh_bg = "short" in h_side
        en_px_bg = dec(bgp.get("openPriceAvg") or bgp.get("avgPrice"))
        mk_px_bg = dec(bgp.get("markPrice")) or en_px_bg
        lq_px_bg = dec(bgp.get("liquidationPrice"))
        unr_bg = dec(bgp.get("unrealizedPL") or bgp.get("unrealisedPnl"))
        not_bg = f2(bg_q * mk_px_bg, 2)
        upnl_2dp = f2(unr_bg, 2)
        try:
            lev = max(1.0, float(bgp.get("leverage") or 1.0))
        except (TypeError, ValueError):
            lev = 1.0
        uroi = round((upnl_2dp / (not_bg / lev)) * 100.0, 2) if not_bg > 0 else 0.0
        open_positions.append(
            {
                "venue_id": "bitget",
                "venue_name": meta.display_name,
                "color_token": meta.color_token,
                "symbol": bg_c,
                "side": "SHORT" if is_sh_bg else "LONG",
                "leverage": lev,
                "qty": f2(bg_q, 6),
                "notional_usd": not_bg,
                "entry_price": f2(en_px_bg, 4),
                "mark_price": f2(mk_px_bg, 4),
                "liq_price": f2(lq_px_bg, 4),
                "unrealized_pnl_usd": upnl_2dp,
                "unrealized_roi_pct": uroi,
                "has_stop_order": dec(bgp.get("stopLoss")) > 0,
            }
        )

    eq_2dp = f2(bg_total_equity_usd, 2)
    prin_2dp = f2(bg_principal_usd, 2)
    pnl_2dp = round(eq_2dp - prin_2dp, 2) if (bg_total_equity_usd > 0 or bg_principal_usd != 0 or bg_meta["has_ext_flow"]) else f2(bg_net_pnl_usd, 2)

    reconcile_streams: list[dict[str, Any]] = [
        {
            "key": "bitget_futures",
            "title_zh": "Bitget 手操合约实收净盈亏" if has_bg_copy else "Bitget 统一账户合约实收净盈亏",
            "label_zh": "Bitget 手操合约实收净盈亏" if has_bg_copy else "Bitget 统一账户合约实收净盈亏",
            "detail_zh": f"Bitget {len(bg_closed_records)} 笔平仓账面 {(bg_book_sum_usd + bg_unrealized_usd):+.2f} U + 手操实时返佣 +{bg_manual_reb_usd:.2f} U",
            "amount_usd": f2(bg_futures_net_usd, 2),
        }
    ]
    if has_bg_copy:
        reconcile_streams.append(
            {
                "key": "bitget_copy_trading",
                "title_zh": "Bitget 历史跟单与策略综合净赚（已结清）",
                "label_zh": "Bitget 历史跟单与策略综合净赚（已结清）",
                "detail_zh": f"{len(bg_meta.get('copy_sessions') or [])} 轮划转（池账面 {bg_copy_book_usd:+.2f} U + T+1 返佣 +{bg_copy_reb_usd:.2f} U）",
                "amount_usd": f2(bg_copy_net_usd, 2),
            }
        )
    if abs(bg_stock_net_usd) >= Decimal("0.01"):
        reconcile_streams.append(
            {
                "key": "bitget_stock",
                "title_zh": "Bitget 美股与 rStock 净损益",
                "label_zh": "Bitget 美股与 rStock 净损益",
                "detail_zh": f"Stock+ 与 1:1 美股代币净损益 {bg_stock_net_usd:+.2f} U",
                "amount_usd": f2(bg_stock_net_usd, 2),
            }
        )
    bg_rem_adj = bg_raw_adj_usd + bg_non_usdt_tokens_usd
    if abs(bg_rem_adj) >= Decimal("0.01"):
        reconcile_streams.append(
            {
                "key": "bitget_other_net",
                "title_zh": "Bitget 经典仓位/理财空投与返佣净额",
                "label_zh": "Bitget 经典仓位/理财空投与返佣净额",
                "detail_zh": "网格策略/早期仓位/现货代币及额外返佣闭环净额",
                "amount_usd": f2(bg_rem_adj, 2),
            }
        )
    reconcile_streams = clamp_reconcile_streams_to_target(
        reconcile_streams,
        pnl_2dp,
        fallback_key="bitget_other_net",
        fallback_label_zh="Bitget 账户净值变动",
        fallback_detail_zh="统一账户余额与估值净变动",
    )

    today_bj_str = fmt_bj(now_s, "%Y-%m-%d")
    bg_first_day = min((r["close_date_bj"] for r in bg_all_records if r.get("close_date_bj")), default=today_bj_str)
    daily_pnl: dict[str, Decimal] = defaultdict(Decimal)
    daily_comp: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for bg_r in bg_closed_records:
        d_bg = bg_r["close_date_bj"]
        v_bg = dec(bg_r["realized_net_pnl_usd"])
        daily_pnl[d_bg] += v_bg
        daily_comp[d_bg]["bitget_futures"] += v_bg
    for bg_sr in bg_sess_records:
        d_bs = bg_sr["close_date_bj"]
        v_bs = dec(bg_sr["realized_net_pnl_usd"])
        daily_pnl[d_bs] += v_bs
        daily_comp[d_bs]["bitget_copy_trading"] += v_bs
    if bg_unrealized_usd != 0:
        daily_pnl[today_bj_str] += bg_unrealized_usd
        daily_comp[today_bj_str]["bitget_futures"] += bg_unrealized_usd
    if bg_stock_net_usd != 0:
        daily_pnl[bg_first_day] += bg_stock_net_usd
        daily_comp[bg_first_day]["bitget_stock"] += bg_stock_net_usd
    if bg_rem_adj != 0:
        daily_pnl[bg_first_day] += bg_rem_adj
        daily_comp[bg_first_day]["bitget_other_net"] += bg_rem_adj

    px_float_map = {**(global_spot_prices or {}), **extract_bitget_spot_prices(store)}
    wallet_balances = build_spot_wallet_balances(
        raw_spot_rows,
        venue_id="bitget",
        venue_name=meta.display_name,
        color_token=meta.color_token,
        spot_prices=px_float_map,
        excluded_symbols={h["symbol"] for h in stock_bundle.holdings},
    )

    bg_sessions = bg_meta.get("copy_sessions") or []
    bg_inv_sum = sum((dec(s.get("invested")) for s in bg_sessions), Decimal("0"))
    bg_act_prin = sum((dec(s.get("invested")) for s in bg_sessions if not bool(s.get("closed"))), Decimal("0"))
    bg_copy_platform = (
        {
            "venue_id": "bitget",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "status": "ACTIVE" if bg_act_prin > Decimal("0.01") else "SETTLED",
            "invested_usd": f2(bg_inv_sum, 2),
            "equity_usd": f2(bg_act_prin if bg_act_prin > Decimal("0.01") else Decimal("0"), 2),
            "book_pnl_usd": f2(bg_copy_book_usd, 2),
            "rebate_usd": f2(bg_copy_reb_usd, 2),
            "combined_pnl_usd": f2(bg_copy_net_usd, 2),
            "rebate_count": len(bg_sess_records),
        }
        if has_bg_copy
        else None
    )

    if isinstance(uta_assets, Mapping) and uta_assets:
        bg_futures_equity_usd = max(Decimal("0"), bg_uta_equity_usd - bg_non_usdt_tokens_usd - bg_spot_stk_val)
    else:
        bg_futures_equity_usd = sum(
            (dec(fa_bg.get("usdtEquity") or fa_bg.get("accountEquity")) for fa_bg in (bg_lb.get("futures_accounts") or [])),
            Decimal("0"),
        )

    return VenueLedgerBundle(
        venue_id="bitget",
        exchange_card={
            "venue_id": "bitget",
            "display_name": meta.display_name,
            "display_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "connected": True,
            "equity_usd": eq_2dp,
            "net_principal_usd": prin_2dp,
            "net_pnl_usd": pnl_2dp,
            "futures_equity_usd": f2(bg_futures_equity_usd, 2),
            "available_margin_usd": f2(bg_futures_available_usd, 2),
            "trade_count": len(public_trades),
            "rebate_rate_pct": f2(bg_meta["live_rebate_rate"] * Decimal("100"), 1),
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
            has_copy_trading=has_bg_copy,
            invested_usd=f2(bg_inv_sum, 2) if has_bg_copy else 0.0,
            active_equity_usd=f2(bg_act_prin if bg_act_prin > Decimal("0.01") else Decimal("0"), 2) if has_bg_copy else 0.0,
            book_pnl_usd=f2(bg_copy_book_usd, 2) if has_bg_copy else 0.0,
            rebate_usd=f2(bg_copy_reb_usd, 2) if has_bg_copy else 0.0,
            combined_pnl_usd=f2(bg_copy_net_usd, 2) if has_bg_copy else 0.0,
            platform=bg_copy_platform,
        ),
        stocks=stock_bundle,
        fee_context={"live_rebate_rate": bg_meta["live_rebate_rate"]},
    )


def build_bitget_fee_matrix_row(
    bundle: VenueLedgerBundle,
    range_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    ex = bundle.exchange_card
    bg_live_rebate_rate: Decimal = bundle.fee_context.get("live_rebate_rate", Decimal("0"))
    bg_all = aggregate_trade_legs_decimal(range_trades)
    bg_m_gross_bps = (bg_all["m_fee"] / bg_all["m_not"] * Decimal("10000")) if bg_all["m_not"] > 0 else Decimal("0")
    bg_t_gross_bps = (bg_all["t_fee"] / bg_all["t_not"] * Decimal("10000")) if bg_all["t_not"] > 0 else Decimal("0")
    bg_t_net_bps = (bg_all["t_net"] / bg_all["t_not"] * Decimal("10000")) if bg_all["t_not"] > 0 else Decimal("0")
    bg_m_net_rate = (bg_all["m_net"] / bg_all["m_not"]) if bg_all["m_not"] > 0 else Decimal("0")
    bg_savings = max(Decimal("0"), bg_all["t_net"] - bg_all["t_not"] * bg_m_net_rate) if bg_all["m_not"] > 0 else Decimal("0")
    bg_overall_reb_pct = (
        (bg_all["tot_reb"] / bg_all["g_fee"] * Decimal("100"))
        if bg_all["g_fee"] > 0
        else (bg_live_rebate_rate * Decimal("100"))
    )
    disc_saved = f2(bg_all["disc_saved"], 2)
    reb_pct_f = f2(bg_overall_reb_pct, 1)
    m_gross_f = f2(bg_m_gross_bps, 2)

    blended_gross_bps = (bg_all["g_fee"] / bg_all["tot_not"] * Decimal("10000")) if bg_all["tot_not"] > 0 else Decimal("0")
    blended_net_bps = (max(Decimal("0"), bg_all["tot_net"]) / bg_all["tot_not"] * Decimal("10000")) if bg_all["tot_not"] > 0 else Decimal("0")

    return {
        "venue_id": "bitget",
        "display_name": ex["display_name"],
        "display_name_zh": ex["display_name_zh"],
        "color_token": ex["color_token"],
        "trade_count": len(range_trades),
        "maker_gross_bps": m_gross_f,
        "maker_net_bps": round(m_gross_f * (1.0 - reb_pct_f / 100.0), 2),
        "taker_gross_bps": f2(bg_t_gross_bps, 2),
        "taker_net_bps": f2(bg_t_net_bps, 2),
        "maker_bps_known": bool(bg_all["m_not"] > 0 or len(range_trades) == 0),
        "taker_bps_known": bool(bg_all["t_not"] > 0 or len(range_trades) == 0),
        "blended_gross_bps": f2(blended_gross_bps, 2),
        "blended_net_bps": f2(blended_net_bps, 2),
        "gross_fee_usd": f2(bg_all["g_fee"], 2),
        "discount_saved_usd": disc_saved,
        "discount_label": "BGB" if disc_saved > 0 else "",
        "rebate_usd": f2(bg_all["tot_reb"], 2),
        "rebate_pct": reb_pct_f,
        "net_fee_usd": f2(bg_all["tot_net"], 2),
        "combined_relief_pct": reb_pct_f,
        "potential_maker_savings_usd": f2(bg_savings, 2),
    }
