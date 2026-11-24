"""Independent actuarial accounting engine for Binance (`venue_id = 'binance'`)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
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
    classify_spot_stock_token,
    reconstruct_spot_cost_basis,
)
from opentradeledger.core.venue_primitives import (
    STOCK_COMMODITY_BASES,
    aggregate_trade_legs_decimal,
    build_spot_holdings_list,
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

BN_PENDING_REBATE_WINDOW_SEC = 86_400
BN_FALLBACK_BNB_DISCOUNT_FACTOR = Decimal("0.90")


def _build_bnb_price_lookup(
    bnb_prices: Mapping[str, Any] | None,
    binance_cashflows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Decimal], list[tuple[str, Decimal]]]:
    px_by_day: dict[str, Decimal] = {}
    if isinstance(bnb_prices, Mapping):
        for k, v in bnb_prices.items():
            dv = dec(v)
            if dv > 0:
                px_by_day[str(k)] = dv

    for cf in binance_cashflows:
        if str(cf.get("cf_type") or "") != "CONVERT":
            continue
        fa = str(cf.get("from_asset") or "").upper()
        ta = str(cf.get("asset") or "").upper()
        f_amt = dec(cf.get("from_amount"))
        t_amt = dec(cf.get("amount"))
        ts_c = norm_sec(cf.get("time_ms"))
        if ts_c <= 0 or f_amt <= 0 or t_amt <= 0:
            continue
        day_c = fmt_bj(ts_c, "%Y-%m-%d")
        if fa == "USDT" and ta == "BNB":
            px_by_day.setdefault(day_c, f_amt / t_amt)
        elif fa == "BNB" and ta == "USDT":
            px_by_day.setdefault(day_c, t_amt / f_amt)

    dated_sorted = sorted(
        ((d, p) for d, p in px_by_day.items() if len(d) == 10 and d[4] == "-"),
        key=lambda kv: kv[0],
    )
    return px_by_day, dated_sorted


def _bnb_usd_at(
    ts_sec: float,
    px_by_day: Mapping[str, Decimal],
    dated_sorted: Sequence[tuple[str, Decimal]],
) -> Decimal:
    if ts_sec > 0:
        bj_d = fmt_bj(ts_sec, "%Y-%m-%d")
        if bj_d in px_by_day:
            return px_by_day[bj_d]
        utc_d = datetime.fromtimestamp(ts_sec, timezone.utc).strftime("%Y-%m-%d")
        if utc_d in px_by_day:
            return px_by_day[utc_d]
        if dated_sorted:
            best_px = dated_sorted[0][1]
            for d_k, p_v in dated_sorted:
                if d_k <= bj_d:
                    best_px = p_v
                else:
                    break
            return best_px
    if "LIVE" in px_by_day:
        return px_by_day["LIVE"]
    return Decimal("580")


def extract_binance_spot_prices(store: "LedgerStore") -> dict[str, float]:
    scope = store.venue_scope("binance")
    lb = scope.get_kv("live_bundle", {}) or {}
    out: dict[str, float] = {}
    if isinstance(lb, dict) and isinstance(lb.get("spot_tickers"), dict):
        for sym, raw_px in (lb.get("spot_tickers") or {}).items():
            try:
                px = float(raw_px)
                if px > 0:
                    out[str(sym).strip().upper()] = px
            except (TypeError, ValueError):
                continue
    bnb_px_map = scope.get_kv("bnb_prices", {})
    if isinstance(bnb_px_map, dict) and bnb_px_map:
        latest_day = sorted(bnb_px_map.keys())[-1]
        try:
            bnb_px = float(bnb_px_map[latest_day])
            if bnb_px > 0:
                out.setdefault("BNB", bnb_px)
        except (TypeError, ValueError):
            pass
    return out


def _classify_bn_leg_mode(
    roles: set[str],
    *,
    is_exit: bool,
    order_types: set[str],
) -> tuple[str, str]:
    upper_types = {t.upper() for t in order_types if t}
    if is_exit and upper_types & {"STOP", "STOP_MARKET", "TRAILING_STOP_MARKET"}:
        return "STOP_MARKET_TAKER", "止损市价"
    if is_exit and upper_types & {"TAKE_PROFIT", "TAKE_PROFIT_MARKET"}:
        return "MARKET_TAKER", "止盈市价"
    if roles == {"maker"}:
        return "LIMIT_MAKER", "限价挂单"
    if "maker" in roles and "taker" in roles:
        return "MIXED", "混合成交"
    return "MARKET_TAKER", "市价吃单"


def _build_binance_closed_records(
    *,
    binance_fills: Sequence[Mapping[str, Any]],
    binance_income: Sequence[Mapping[str, Any]],
    binance_orders: Sequence[Mapping[str, Any]],
    binance_cashflows: Sequence[Mapping[str, Any]],
    binance_bnb_prices: Mapping[str, Any] | None,
    binance_commission_rates: Mapping[str, Mapping[str, Any]] | None,
    journals: Mapping[str, Mapping[str, Any]],
    now_f: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    px_by_day, dated_bnb = _build_bnb_price_lookup(binance_bnb_prices, binance_cashflows)

    order_type_by_key: dict[tuple[str, str], str] = {}
    for o in binance_orders:
        sym_o = str(o.get("symbol") or "").upper()
        oid_o = str(o.get("orderId") or "").strip()
        if sym_o and oid_o:
            order_type_by_key[(sym_o, oid_o)] = str(o.get("origType") or o.get("type") or "")

    sym_base_rates: dict[str, dict[str, Decimal]] = {}
    if isinstance(binance_commission_rates, Mapping):
        for sym_k, cr_v in binance_commission_rates.items():
            if isinstance(cr_v, Mapping):
                su = str(sym_k or "").strip().upper()
                if su:
                    sym_base_rates[su] = {
                        "maker": abs(dec(cr_v.get("makerCommissionRate"))),
                        "taker": abs(dec(cr_v.get("takerCommissionRate"))),
                    }

    obs_usdt_rates_sym: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    obs_usdt_rates_cat: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for f_obs in binance_fills:
        c_asset = str(f_obs.get("commissionAsset") or "USDT").strip().upper()
        if c_asset == "BNB":
            continue
        sym_obs = str(f_obs.get("symbol") or "").strip().upper()
        not_obs = abs(dec(f_obs.get("quoteQty"))) or (abs(dec(f_obs.get("qty"))) * dec(f_obs.get("price")))
        fee_obs = abs(dec(f_obs.get("commission")))
        if sym_obs and not_obs > Decimal("5"):
            role_obs = "maker" if bool(f_obs.get("maker")) else "taker"
            r_val = fee_obs / not_obs
            obs_usdt_rates_sym[(sym_obs, role_obs)].append(r_val)
            base_obs = normalize_contract_symbol(sym_obs).split("_")[0]
            cat_obs = "stock" if base_obs in STOCK_COMMODITY_BASES else "crypto"
            obs_usdt_rates_cat[(cat_obs, role_obs)].append(r_val)

    def _resolve_bn_base_rate(sym_u: str, role: str) -> Decimal | None:
        if sym_u in sym_base_rates and role in sym_base_rates[sym_u]:
            return sym_base_rates[sym_u][role]
        if obs_usdt_rates_sym.get((sym_u, role)):
            vals = sorted(obs_usdt_rates_sym[(sym_u, role)])
            return vals[len(vals) // 2]
        base_tok = normalize_contract_symbol(sym_u).split("_")[0]
        cat_tok = "stock" if base_tok in STOCK_COMMODITY_BASES else "crypto"
        if obs_usdt_rates_cat.get((cat_tok, role)):
            vals = sorted(obs_usdt_rates_cat[(cat_tok, role)])
            return vals[len(vals) // 2]
        return None

    rebates_by_trade: dict[tuple[str, str], dict[str, Any]] = {}
    all_rebate_rows: list[tuple[tuple[str, str], float, Decimal, Decimal, str, str]] = []
    funding_by_sym: dict[str, list[tuple[int, float, Decimal]]] = defaultdict(list)
    pm_transfer_usd = Decimal("0")

    for idx_inc, inc in enumerate(binance_income):
        inc_tp = str(inc.get("incomeType") or "").upper()
        sym_i = str(inc.get("symbol") or "").strip().upper()
        asset_i = str(inc.get("asset") or "USDT").strip().upper()
        amt_i = dec(inc.get("income"))
        ts_i = norm_sec(inc.get("time"))
        val_usd = (amt_i * _bnb_usd_at(ts_i, px_by_day, dated_bnb)) if asset_i == "BNB" else amt_i

        if inc_tp == "COMMISSION_REBATE" and amt_i > 0:
            tid_i = str(inc.get("tradeId") or inc.get("info") or "").strip()
            k_tr = (sym_i, tid_i)
            bnb_q = amt_i if asset_i == "BNB" else Decimal("0")
            if tid_i:
                slot = rebates_by_trade.setdefault(
                    k_tr,
                    {"rebate_usd": Decimal("0"), "rebate_bnb": Decimal("0"), "asset": asset_i, "time_sec": int(ts_i)},
                )
                slot["rebate_usd"] += val_usd
                slot["rebate_bnb"] += bnb_q
                if int(ts_i) > slot["time_sec"]:
                    slot["time_sec"] = int(ts_i)
            all_rebate_rows.append((k_tr, ts_i, val_usd, bnb_q, sym_i, asset_i))
        elif inc_tp == "FUNDING_FEE" and amt_i != 0:
            funding_by_sym[sym_i].append((idx_inc, ts_i, val_usd))
        elif inc_tp == "TRANSFER":
            pm_transfer_usd += val_usd

    matched_reb_total_usd = Decimal("0")
    matched_fee_total_usd = Decimal("0")
    for f_pre in binance_fills:
        sym_p = str(f_pre.get("symbol") or "").strip().upper()
        tid_p = str(f_pre.get("id") or "").strip()
        if not sym_p or not tid_p or (sym_p, tid_p) not in rebates_by_trade:
            continue
        comm_p = abs(dec(f_pre.get("commission")))
        if comm_p <= 0:
            continue
        ca_p = str(f_pre.get("commissionAsset") or "USDT").strip().upper()
        ts_p = norm_sec(f_pre.get("time"))
        fee_p_usd = (comm_p * _bnb_usd_at(ts_p, px_by_day, dated_bnb)) if ca_p == "BNB" else comm_p
        if fee_p_usd > 0:
            matched_fee_total_usd += fee_p_usd
            matched_reb_total_usd += rebates_by_trade[(sym_p, tid_p)]["rebate_usd"]

    bn_live_rebate_rate = (
        min(Decimal("0.95"), matched_reb_total_usd / matched_fee_total_usd)
        if matched_fee_total_usd > 0
        else Decimal("0")
    )

    claimed_rebate_keys: set[tuple[str, str]] = set()
    claimed_funding_ids: set[int] = set()

    fills_by_bucket: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for f in binance_fills:
        sym_f = str(f.get("symbol") or "").strip().upper()
        pside_f = str(f.get("positionSide") or "BOTH").strip().upper()
        if sym_f:
            fills_by_bucket[(sym_f, pside_f)].append(f)

    def _enrich_fill(raw_f: Mapping[str, Any], sym_u: str, share: Decimal = Decimal("1")) -> dict[str, Any]:
        ts_ms = norm_ms(raw_f.get("time"))
        ts_f = norm_sec(raw_f.get("time"))
        qty_f = abs(dec(raw_f.get("qty"))) * share
        px_f = dec(raw_f.get("price"))
        notional_f = (abs(dec(raw_f.get("quoteQty"))) or (abs(dec(raw_f.get("qty"))) * px_f)) * share
        rpnl_f = dec(raw_f.get("realizedPnl")) * share
        comm_raw = abs(dec(raw_f.get("commission"))) * share
        comm_asset = str(raw_f.get("commissionAsset") or "USDT").strip().upper()
        is_maker = bool(raw_f.get("maker"))
        role_str = "maker" if is_maker else "taker"
        oid_s = str(raw_f.get("orderId") or "").strip()
        tid_s = str(raw_f.get("id") or "").strip()

        if comm_asset == "BNB" and comm_raw > 0:
            bnb_px = _bnb_usd_at(ts_f, px_by_day, dated_bnb)
            fee_usd = comm_raw * bnb_px
            live_base_rate = _resolve_bn_base_rate(sym_u, role_str)
            if live_base_rate is not None and live_base_rate > 0 and notional_f > 0:
                base_fee_usd = notional_f * live_base_rate
                bnb_saved = max(Decimal("0"), base_fee_usd - fee_usd)
            else:
                bnb_saved = fee_usd / BN_FALLBACK_BNB_DISCOUNT_FACTOR * (
                    Decimal("1") - BN_FALLBACK_BNB_DISCOUNT_FACTOR
                )
            bnb_qty = comm_raw
        else:
            fee_usd = comm_raw
            bnb_saved = Decimal("0")
            bnb_qty = Decimal("0")

        s_reb_usd = Decimal("0")
        s_reb_bnb = Decimal("0")
        p_reb_usd = Decimal("0")
        reb_sec = 0
        k_reb = (sym_u, tid_s)
        if tid_s and k_reb in rebates_by_trade:
            rb_obj = rebates_by_trade[k_reb]
            claimed_rebate_keys.add(k_reb)
            s_reb_usd = rb_obj["rebate_usd"] * share
            s_reb_bnb = rb_obj["rebate_bnb"] * share
            reb_sec = int(rb_obj["time_sec"])
        elif fee_usd > 0 and bn_live_rebate_rate > 0 and (now_f - ts_f) <= BN_PENDING_REBATE_WINDOW_SEC:
            p_reb_usd = fee_usd * bn_live_rebate_rate

        return {
            "ts_ms": ts_ms,
            "ts_sec": ts_f,
            "qty": qty_f,
            "px": px_f,
            "notional": notional_f,
            "rpnl": rpnl_f,
            "fee_usd": fee_usd,
            "fee_asset": comm_asset,
            "bnb_qty": bnb_qty,
            "bnb_saved_usd": bnb_saved,
            "settled_reb_usd": s_reb_usd,
            "settled_reb_bnb": s_reb_bnb,
            "pending_reb_usd": p_reb_usd,
            "reb_sec": reb_sec,
            "role": role_str,
            "order_id": oid_s,
            "order_type": order_type_by_key.get((sym_u, oid_s), ""),
            "bnb_counted": False,
        }

    def _build_bn_leg(leg_items: Sequence[Mapping[str, Any]], *, is_exit: bool) -> dict[str, Any]:
        roles = {str(it["role"]) for it in leg_items}
        otypes = {str(it["order_type"]) for it in leg_items if it.get("order_type")}
        mode_code, mode_zh = _classify_bn_leg_mode(roles, is_exit=is_exit, order_types=otypes)

        tot_q = sum((it["qty"] for it in leg_items), Decimal("0"))
        tot_not = sum((it["notional"] for it in leg_items), Decimal("0"))
        avg_p = (tot_not / tot_q) if tot_q > 0 else Decimal("0")
        g_fee = sum((it["fee_usd"] for it in leg_items), Decimal("0"))
        m_not = sum((it["notional"] for it in leg_items if it["role"] == "maker"), Decimal("0"))
        t_not = sum((it["notional"] for it in leg_items if it["role"] != "maker"), Decimal("0"))
        m_fee = sum((it["fee_usd"] for it in leg_items if it["role"] == "maker"), Decimal("0"))
        t_fee = sum((it["fee_usd"] for it in leg_items if it["role"] != "maker"), Decimal("0"))
        s_reb = sum((it["settled_reb_usd"] for it in leg_items), Decimal("0"))
        p_reb = sum((it["pending_reb_usd"] for it in leg_items), Decimal("0"))
        m_reb = sum((it["settled_reb_usd"] + it["pending_reb_usd"] for it in leg_items if it["role"] == "maker"), Decimal("0"))
        t_reb = sum((it["settled_reb_usd"] + it["pending_reb_usd"] for it in leg_items if it["role"] != "maker"), Decimal("0"))
        bnb_q = sum((it["bnb_qty"] for it in leg_items), Decimal("0"))
        bnb_saved = sum((it["bnb_saved_usd"] for it in leg_items), Decimal("0"))
        assets_used = {str(it["fee_asset"]) for it in leg_items if it["fee_usd"] > 0}
        f_asset = "BNB" if assets_used == {"BNB"} else ("MIXED" if "BNB" in assets_used else "USDT")

        t_leg = (
            max((float(it["ts_sec"]) for it in leg_items), default=0.0)
            if is_exit
            else min((float(it["ts_sec"]) for it in leg_items), default=0.0)
        )
        last_reb_s = max((int(it["reb_sec"]) for it in leg_items), default=0)
        eff_reb = s_reb + p_reb
        bps = (g_fee / tot_not * Decimal("10000")) if tot_not > 0 else Decimal("0")
        reb_pct = (eff_reb / g_fee * Decimal("100")) if g_fee > 0 else Decimal("0")

        if p_reb > 0 and s_reb > 0:
            r_stat = "PARTIAL_PENDING"
        elif p_reb > 0:
            r_stat = "PENDING"
        elif s_reb > 0:
            r_stat = "SETTLED"
        else:
            r_stat = "NONE"

        return {
            "mode": mode_code,
            "mode_zh": mode_zh,
            "fill_count": len(leg_items),
            "qty_base": f2(tot_q, 6),
            "avg_price": f2(avg_p, 6),
            "notional_usd": f2(tot_not, 2),
            "maker_notional_usd": f2(m_not, 2),
            "taker_notional_usd": f2(t_not, 2),
            "time_sec": int(t_leg),
            "time_bj": fmt_bj(t_leg, "%m-%d %H:%M:%S") if t_leg > 0 else "—",
            "gross_fee_usd": f2(g_fee, 4),
            "maker_fee_usd": f2(m_fee, 4),
            "taker_fee_usd": f2(t_fee, 4),
            "maker_rebate_usd": f2(m_reb, 4),
            "taker_rebate_usd": f2(t_reb, 4),
            "fee_bps": f2(bps, 2),
            "fee_asset": f_asset,
            "bnb_fee_qty": f2(bnb_q, 6),
            "bnb_discount_saved_usd": f2(bnb_saved, 4),
            "settled_rebate_usd": f2(s_reb, 4),
            "pending_rebate_usd": f2(p_reb, 4),
            "net_fee_usd": f2(g_fee - eff_reb, 4),
            "rebate_pct": f2(reb_pct, 2),
            "rebate_status": r_stat,
            "rebate_settled_bj": fmt_bj(last_reb_s, "%m-%d %H:%M") if last_reb_s > 0 else "",
        }

    records: list[dict[str, Any]] = []
    used_record_keys: set[str] = set()
    total_bnb_burned_qty = Decimal("0")
    total_bnb_rebate_qty = Decimal("0")
    total_bnb_saved_usd = Decimal("0")

    def _slice_entries(pool: list[dict[str, Any]], share: Decimal) -> list[dict[str, Any]]:
        if share >= Decimal("1") - Decimal("1e-11"):
            taken = list(pool)
            pool.clear()
            return taken
        taken = []
        for en in pool:
            a_qty = en["qty"] * share
            a_not = en["notional"] * share
            a_rpnl = en["rpnl"] * share
            a_fee = en["fee_usd"] * share
            a_bnb = en["bnb_qty"] * share
            a_sav = en["bnb_saved_usd"] * share
            a_sreb = en["settled_reb_usd"] * share
            a_srebb = en["settled_reb_bnb"] * share
            a_preb = en["pending_reb_usd"] * share

            en["qty"] -= a_qty
            en["notional"] -= a_not
            en["rpnl"] -= a_rpnl
            en["fee_usd"] -= a_fee
            en["bnb_qty"] -= a_bnb
            en["bnb_saved_usd"] -= a_sav
            en["settled_reb_usd"] -= a_sreb
            en["settled_reb_bnb"] -= a_srebb
            en["pending_reb_usd"] -= a_preb

            taken.append(
                {
                    **en,
                    "qty": a_qty,
                    "notional": a_not,
                    "rpnl": a_rpnl,
                    "fee_usd": a_fee,
                    "bnb_qty": a_bnb,
                    "bnb_saved_usd": a_sav,
                    "settled_reb_usd": a_sreb,
                    "settled_reb_bnb": a_srebb,
                    "pending_reb_usd": a_preb,
                }
            )
            en["bnb_counted"] = True
        return taken

    def _emit_close_order(
        sym_u: str,
        side_str: str,
        entry_items: list[dict[str, Any]],
        exit_items: list[dict[str, Any]],
        *,
        is_partial_close: bool = False,
    ) -> None:
        nonlocal total_bnb_burned_qty, total_bnb_rebate_qty, total_bnb_saved_usd
        if not exit_items:
            return
        contract = normalize_contract_symbol(sym_u)
        t_open = min((float(x["ts_sec"]) for x in (entry_items or exit_items)), default=0.0)
        t_close = max((float(x["ts_sec"]) for x in exit_items), default=t_open)
        t_open_ms = min((int(x["ts_ms"]) for x in (entry_items or exit_items)), default=0)
        t_close_ms = max((int(x["ts_ms"]) for x in exit_items), default=t_open_ms)

        fund_usd = Decimal("0")
        for f_id, f_ts, f_val in funding_by_sym.get(sym_u, []):
            if f_id in claimed_funding_ids:
                continue
            if (t_open - 5.0) <= f_ts <= (t_close + 5.0):
                claimed_funding_ids.add(f_id)
                fund_usd += f_val

        en_leg = _build_bn_leg(entry_items, is_exit=False)
        ex_leg = _build_bn_leg(exit_items, is_exit=True)

        all_it = entry_items + exit_items
        for it in all_it:
            if it["bnb_qty"] > 0:
                total_bnb_burned_qty += it["bnb_qty"]
                total_bnb_saved_usd += it["bnb_saved_usd"]
            total_bnb_rebate_qty += it["settled_reb_bnb"]

        gross_pnl = sum((it["rpnl"] for it in all_it), Decimal("0"))
        tot_fee = sum((it["fee_usd"] for it in all_it), Decimal("0"))
        s_reb = sum((it["settled_reb_usd"] for it in all_it), Decimal("0"))
        p_reb = sum((it["pending_reb_usd"] for it in all_it), Decimal("0"))
        bnb_saved_rec = sum((it["bnb_saved_usd"] for it in all_it), Decimal("0"))
        bnb_qty_rec = sum((it["bnb_qty"] for it in all_it), Decimal("0"))
        eff_reb = s_reb + p_reb
        book_pnl = gross_pnl + fund_usd - tot_fee
        real_net = book_pnl + s_reb
        exp_net = book_pnl + eff_reb
        qty_closed = sum((it["qty"] for it in exit_items), Decimal("0"))
        hold_s = max(0, int(t_close - t_open))

        if p_reb > 0 and s_reb > 0:
            r_state = "PARTIAL_PENDING"
        elif p_reb > 0:
            r_state = "PENDING"
        else:
            r_state = "SETTLED"

        assets_rec = {it["fee_asset"] for it in all_it if it["fee_usd"] > 0}
        rec_fee_asset = "BNB" if assets_rec == {"BNB"} else ("MIXED" if "BNB" in assets_rec else "USDT")

        base_rk = f"bn_fut_{contract}_{int(t_open)}_{int(t_close)}_{side_str.lower()}"
        close_oid = str(exit_items[-1].get("order_id") or "").strip()
        record_key = base_rk if base_rk not in used_record_keys else f"{base_rk}_{close_oid or len(records)}"
        used_record_keys.add(record_key)

        j_entry = journals.get(record_key) or journals.get(base_rk) or {}
        records.append(
            {
                "record_key": record_key,
                "exchange": "BINANCE",
                "exchange_zh": "币安",
                "contract": contract,
                "side": side_str.upper(),
                "is_partial_close": is_partial_close,
                "qty_base": f2(qty_closed, 6),
                "open_time_sec": int(t_open),
                "close_time_sec": int(t_close),
                "open_time_ms": t_open_ms,
                "close_time_ms": t_close_ms,
                "open_datetime_bj": fmt_bj(t_open, "%Y-%m-%d %H:%M:%S"),
                "close_datetime_bj": fmt_bj(t_close, "%Y-%m-%d %H:%M:%S"),
                "close_date_bj": fmt_bj(t_close, "%Y-%m-%d"),
                "holding_seconds": hold_s,
                "holding_zh": format_duration_zh(hold_s),
                "entry_leg": en_leg,
                "exit_leg": ex_leg,
                "gross_pnl_usd": f2(gross_pnl, 4),
                "funding_pnl_usd": f2(fund_usd, 4),
                "total_fee_usd": f2(tot_fee, 4),
                "fee_asset": rec_fee_asset,
                "bnb_fee_qty": f2(bnb_qty_rec, 6),
                "bnb_discount_saved_usd": f2(bnb_saved_rec, 4),
                "settled_rebate_usd": f2(s_reb, 4),
                "pending_rebate_usd": f2(p_reb, 4),
                "net_fee_usd": f2(tot_fee - eff_reb, 4),
                "effective_rebate_pct": f2((eff_reb / tot_fee * Decimal("100")), 2) if tot_fee > 0 else 0.0,
                "rebate_state": r_state,
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

    for (sym_u, pside), raw_list in sorted(fills_by_bucket.items()):
        sorted_fills = sorted(
            raw_list,
            key=lambda x: (int(float(x.get("time") or 0)), int(float(x.get("id") or 0))),
        )
        cur_entries: list[dict[str, Any]] = []
        pending_exit_group: list[dict[str, Any]] = []
        pending_exit_oid = ""
        pos_q = Decimal("0")
        cur_side = pside if pside in ("LONG", "SHORT") else "LONG"

        def _flush_exit_group(side_emit: str) -> None:
            nonlocal pos_q, pending_exit_group, pending_exit_oid
            if not pending_exit_group:
                return
            ex_q = sum((x["qty"] for x in pending_exit_group), Decimal("0"))
            if pos_q <= Decimal("1e-9") or ex_q >= pos_q - Decimal("1e-9"):
                alloc_en = _slice_entries(cur_entries, Decimal("1"))
                pos_q = Decimal("0")
                _emit_close_order(sym_u, side_emit, alloc_en, pending_exit_group, is_partial_close=False)
            else:
                share = ex_q / pos_q
                alloc_en = _slice_entries(cur_entries, share)
                pos_q = max(Decimal("0"), pos_q - ex_q)
                _emit_close_order(sym_u, side_emit, alloc_en, pending_exit_group, is_partial_close=True)
            pending_exit_group = []
            pending_exit_oid = ""

        for rf in sorted_fills:
            f_side = str(rf.get("side") or "BUY").strip().upper()
            q_abs = abs(dec(rf.get("qty")))
            if q_abs <= 0:
                continue
            oid_cur = str(rf.get("orderId") or "").strip()

            if pside in ("LONG", "SHORT"):
                is_entry = (pside == "LONG" and f_side == "BUY") or (pside == "SHORT" and f_side == "SELL")
                if is_entry:
                    _flush_exit_group(pside)
                    cur_entries.append(_enrich_fill(rf, sym_u))
                    pos_q += q_abs
                else:
                    if pending_exit_group and oid_cur and oid_cur != pending_exit_oid:
                        _flush_exit_group(pside)
                    pending_exit_group.append(_enrich_fill(rf, sym_u))
                    pending_exit_oid = oid_cur
                    if sum((x["qty"] for x in pending_exit_group), Decimal("0")) >= pos_q - Decimal("1e-9"):
                        _flush_exit_group(pside)
            else:
                if pos_q <= Decimal("1e-9") and not pending_exit_group:
                    cur_side = "LONG" if f_side == "BUY" else "SHORT"
                    cur_entries.append(_enrich_fill(rf, sym_u))
                    pos_q = q_abs
                elif (cur_side == "LONG" and f_side == "BUY") or (cur_side == "SHORT" and f_side == "SELL"):
                    _flush_exit_group(cur_side)
                    cur_entries.append(_enrich_fill(rf, sym_u))
                    pos_q += q_abs
                else:
                    if pending_exit_group and oid_cur and oid_cur != pending_exit_oid:
                        _flush_exit_group(cur_side)
                    rem_after_prev = pos_q - sum((x["qty"] for x in pending_exit_group), Decimal("0"))
                    if q_abs <= rem_after_prev + Decimal("1e-9"):
                        pending_exit_group.append(_enrich_fill(rf, sym_u))
                        pending_exit_oid = oid_cur
                        if sum((x["qty"] for x in pending_exit_group), Decimal("0")) >= pos_q - Decimal("1e-9"):
                            _flush_exit_group(cur_side)
                    else:
                        close_share = max(Decimal("0"), rem_after_prev / q_abs)
                        open_share = Decimal("1") - close_share
                        if close_share > 0:
                            pending_exit_group.append(_enrich_fill(rf, sym_u, share=close_share))
                        _flush_exit_group(cur_side)
                        cur_side = "SHORT" if cur_side == "LONG" else "LONG"
                        cur_entries = [_enrich_fill(rf, sym_u, share=open_share)]
                        pos_q = q_abs * open_share

        if pending_exit_group:
            _flush_exit_group(cur_side)

    unmatched_reb_usd = Decimal("0")
    unmatched_reb_by_sym: dict[str, Decimal] = defaultdict(Decimal)
    unmatched_reb_items: list[dict[str, Any]] = []
    daily_extra_pnl: dict[str, Decimal] = defaultdict(Decimal)
    daily_unmatched_funding: dict[str, Decimal] = defaultdict(Decimal)
    earliest_bn_sec = 0.0

    for k_tr, ts_r, r_usd, r_bnb, sym_r, asset_r in all_rebate_rows:
        if ts_r > 0 and (earliest_bn_sec == 0.0 or ts_r < earliest_bn_sec):
            earliest_bn_sec = ts_r
        if k_tr[1] and k_tr in claimed_rebate_keys:
            continue
        unmatched_reb_usd += r_usd
        total_bnb_rebate_qty += r_bnb
        if sym_r:
            unmatched_reb_by_sym[sym_r] += r_usd
        if ts_r > 0:
            daily_extra_pnl[fmt_bj(ts_r, "%Y-%m-%d")] += r_usd
        unmatched_reb_items.append(
            {
                "symbol": sym_r,
                "contract": normalize_contract_symbol(sym_r) if sym_r else "USDT",
                "trade_id": k_tr[1],
                "time_sec": int(ts_r),
                "date_bj": fmt_bj(ts_r, "%Y-%m-%d") if ts_r > 0 else "",
                "time_bj": fmt_bj(ts_r, "%m-%d %H:%M:%S") if ts_r > 0 else "—",
                "datetime_bj": fmt_bj(ts_r, "%Y-%m-%d %H:%M:%S") if ts_r > 0 else "—",
                "asset": asset_r,
                "rebate_usd": r_usd,
                "rebate_bnb": r_bnb,
            }
        )

    unmatched_fund_usd = Decimal("0")
    for sym_f, flist in funding_by_sym.items():
        for f_id, f_ts, f_val in flist:
            if f_ts > 0 and (earliest_bn_sec == 0.0 or f_ts < earliest_bn_sec):
                earliest_bn_sec = f_ts
            if f_id not in claimed_funding_ids:
                unmatched_fund_usd += f_val
                if f_ts > 0:
                    d_f = fmt_bj(f_ts, "%Y-%m-%d")
                    daily_extra_pnl[d_f] += f_val
                    daily_unmatched_funding[d_f] += f_val

    for rec in records:
        if rec["open_time_sec"] > 0 and (earliest_bn_sec == 0.0 or rec["open_time_sec"] < earliest_bn_sec):
            earliest_bn_sec = float(rec["open_time_sec"])

    bn_meta = {
        "unmatched_rebate_usd": unmatched_reb_usd,
        "unmatched_rebate_by_sym": dict(unmatched_reb_by_sym),
        "unmatched_rebate_items": unmatched_reb_items,
        "unmatched_funding_usd": unmatched_fund_usd,
        "daily_extra_pnl": dict(daily_extra_pnl),
        "daily_unmatched_funding": dict(daily_unmatched_funding),
        "pm_transfer_usd": pm_transfer_usd,
        "bnb_burned_qty": total_bnb_burned_qty,
        "bnb_discount_saved_usd": total_bnb_saved_usd,
        "live_rebate_rate": bn_live_rebate_rate,
        "earliest_day_bj": fmt_bj(earliest_bn_sec, "%Y-%m-%d") if earliest_bn_sec > 0 else "",
        "px_by_day": px_by_day,
        "dated_bnb": dated_bnb,
    }
    return records, bn_meta


def _reconcile_binance_principal(
    *,
    binance_cashflows: Sequence[Mapping[str, Any]],
    spot_tickers: Mapping[str, Any],
    pm_transfer_usd: Decimal,
    px_by_day: Mapping[str, Decimal],
    dated_bnb: Sequence[tuple[str, Decimal]],
) -> Decimal:
    if not binance_cashflows:
        return max(Decimal("0"), pm_transfer_usd)

    inflow_usd = Decimal("0")
    outflow_usd = Decimal("0")

    def _asset_usd(asset: str, amt: Decimal, ts_s: float) -> Decimal:
        a_u = asset.upper()
        if a_u in ("USDT", "USDC", "FDUSD", "USD1", "BUSD", "USD"):
            return amt
        if a_u == "BNB":
            return amt * _bnb_usd_at(ts_s, px_by_day, dated_bnb)
        tk_px = dec(spot_tickers.get(a_u))
        return amt * tk_px if tk_px > 0 else Decimal("0")

    has_ext_flow = False
    for cf in binance_cashflows:
        tp = str(cf.get("cf_type") or "").upper()
        asset = str(cf.get("asset") or "USDT").upper()
        amt = dec(cf.get("amount"))
        ts_s = norm_sec(cf.get("time_ms"))
        if tp in ("DEPOSIT", "C2C_BUY") and amt > 0:
            inflow_usd += _asset_usd(asset, abs(amt), ts_s)
            has_ext_flow = True
        elif tp in ("WITHDRAW", "C2C_SELL") and amt > 0:
            outflow_usd += _asset_usd(asset, abs(amt), ts_s)
            has_ext_flow = True
        elif tp == "PAY":
            val = _asset_usd(asset, amt, ts_s)
            if val < 0:
                outflow_usd += abs(val)
                has_ext_flow = True

    if not has_ext_flow:
        return max(Decimal("0"), pm_transfer_usd)
    return max(Decimal("0"), inflow_usd - outflow_usd)


def _build_binance_copy_trading(
    *,
    bn_wallet_rows: Sequence[Mapping[str, Any]],
    binance_cashflows: Sequence[Mapping[str, Any]],
    binance_income: Sequence[Mapping[str, Any]],
    unmatched_rebate_items: Sequence[Mapping[str, Any]],
    bn_live_rebate_rate: Decimal,
    bn_spot_usdt_usd: Decimal,
    meta: AdapterMeta,
) -> dict[str, Any]:
    empty_res: dict[str, Any] = {
        "has_copy_trading": False,
        "status": "NONE",
        "status_zh": "未开启",
        "period_zh": "",
        "settle_date_bj": "",
        "total_invested_usd": 0.0,
        "redeemed_usd": 0.0,
        "current_equity_usd": 0.0,
        "book_pnl_usd": 0.0,
        "rebate_count": 0,
        "rebate_total_usd": 0.0,
        "combined_pnl_usd": 0.0,
        "transfers": [],
        "rebates": [],
        "_raw_book_pnl": Decimal("0"),
        "_raw_rebate_total": Decimal("0"),
        "_raw_combined_pnl": Decimal("0"),
        "_daily_rebates": {},
    }

    copy_w = next(
        (w for w in bn_wallet_rows if str(w.get("walletName") or "").strip().lower() == "copy trading"),
        None,
    )
    copy_wallet_active = bool(copy_w and (copy_w.get("activate") or dec(copy_w.get("balance")) > 0))
    live_copy_equity = dec(copy_w.get("balance")) if copy_w else Decimal("0")
    if not copy_wallet_active and live_copy_equity <= 0:
        return empty_res

    funding_to_main_rows: list[tuple[float, Decimal, str]] = []
    for cf in binance_cashflows:
        if str(cf.get("cf_type") or "").upper() != "INTERNAL_TRANSFER":
            continue
        if str(cf.get("asset") or "").upper() != "USDT":
            continue
        if str(cf.get("tran_dir") or "").upper() == "FUNDING_MAIN":
            ts_tr = norm_sec(cf.get("time_ms"))
            amt_tr = abs(dec(cf.get("amount")))
            if amt_tr > 0 and ts_tr > 0:
                funding_to_main_rows.append((ts_tr, amt_tr, str(cf.get("tran_id") or cf.get("cf_key") or "")))
    funding_to_main_rows.sort(key=lambda x: x[0])

    sf_events: list[tuple[float, str, Decimal, str]] = []
    for cf in binance_cashflows:
        tp = str(cf.get("cf_type") or "").upper()
        if tp == "INTERNAL_TRANSFER":
            continue
        ts_c = norm_sec(cf.get("time_ms"))
        asset_c = str(cf.get("asset") or "").upper()
        amt_c = dec(cf.get("amount"))
        fee_c = dec(cf.get("fee"))
        if tp in ("C2C_BUY", "DEPOSIT") and asset_c == "USDT" and amt_c > 0:
            sf_events.append((ts_c, tp, amt_c, str(cf.get("cf_key") or "")))
        elif tp in ("WITHDRAW", "C2C_SELL") and asset_c == "USDT" and amt_c > 0:
            sf_events.append((ts_c, tp, -(amt_c + fee_c), str(cf.get("cf_key") or "")))
        elif tp == "CONVERT":
            fa_c = str(cf.get("from_asset") or "").upper()
            famt_c = dec(cf.get("from_amount"))
            if fa_c == "USDT" and famt_c > 0:
                sf_events.append((ts_c, "CONVERT_OUT", -famt_c, str(cf.get("cf_key") or "")))
            if asset_c == "USDT" and amt_c > 0:
                sf_events.append((ts_c, "CONVERT_IN", amt_c, str(cf.get("cf_key") or "")))
        elif tp == "PAY" and asset_c == "USDT" and amt_c != 0:
            sf_events.append((ts_c, "PAY", amt_c, str(cf.get("cf_key") or "")))

    for inc in binance_income:
        if str(inc.get("incomeType") or "").upper() == "TRANSFER" and str(inc.get("asset") or "").upper() == "USDT":
            ts_i = norm_sec(inc.get("time"))
            val_i = dec(inc.get("income"))
            if val_i != 0 and ts_i > 0:
                sf_events.append(
                    (ts_i, "MAIN_UMFUTURE" if val_i > 0 else "UMFUTURE_MAIN", -val_i, str(inc.get("tranId") or ""))
                )
    sf_events.sort(key=lambda x: x[0])

    daily_rebates: dict[str, Decimal] = defaultdict(Decimal)
    rebate_rows: list[dict[str, Any]] = []
    reb_total_dec = Decimal("0")
    for ur in sorted(unmatched_rebate_items, key=lambda x: int(x.get("time_sec") or 0), reverse=True):
        r_usd = dec(ur.get("rebate_usd"))
        est_f = (r_usd / bn_live_rebate_rate) if bn_live_rebate_rate > 0 else Decimal("0")
        t_s = int(ur.get("time_sec") or 0)
        c_norm = str(ur.get("contract") or "USDT")
        d_bj = str(ur.get("date_bj") or "")
        reb_total_dec += r_usd
        if d_bj:
            daily_rebates[d_bj] += r_usd
        rebate_rows.append(
            {
                "venue_id": "binance",
                "venue_name": meta.display_name,
                "venue_name_zh": meta.display_name_zh,
                "time_sec": t_s,
                "time_str": str(ur.get("datetime_bj") or ur.get("time_bj") or "—"),
                "destination": f"Binance UM Futures USDT · {c_norm}",
                "destination_zh": f"币安合约 USDT · {c_norm}",
                "est_gross_fee_usd": f2(est_f, 3),
                "rebate_usd": f2(r_usd, 4),
            }
        )

    transfers: list[dict[str, Any]] = []
    invested_dec = Decimal("0")
    redeemed_dec = Decimal("0")
    used_fm_idx: set[int] = set()
    settle_sec = 0.0
    first_in_sec = 0.0

    if unmatched_rebate_items:
        first_reb_s = float(min(int(x["time_sec"]) for x in unmatched_rebate_items))
        last_reb_s = float(max(int(x["time_sec"]) for x in unmatched_rebate_items))

        redeem_ev: tuple[float, str, Decimal, str] | None = None
        for ev in sf_events:
            if ev[0] >= last_reb_s and ev[2] <= -Decimal("1") and ev[1] in ("MAIN_UMFUTURE", "WITHDRAW", "C2C_SELL"):
                redeem_ev = ev
                break
        win_end_s = redeem_ev[0] if redeem_ev is not None else (last_reb_s + 86400.0)

        win_evs = [ev for ev in sf_events if (first_reb_s - 86400.0) <= ev[0] <= win_end_s]
        for idx_w, (ts_w, tp_w, delta_w, _) in enumerate(win_evs):
            if delta_w < Decimal("1") or ts_w > (last_reb_s + 3600.0):
                continue
            if tp_w not in ("C2C_BUY", "DEPOSIT", "UMFUTURE_MAIN"):
                continue
            matched_pass_through = any(
                ts_o >= ts_w
                and ts_o < first_reb_s
                and delta_o < 0
                and abs(abs(delta_o) - delta_w) <= Decimal("0.5")
                for ts_o, _, delta_o, _ in win_evs[idx_w + 1 :]
            )
            if matched_pass_through:
                continue

            eff_ts = ts_w
            for fm_i, (fm_ts, fm_amt, _) in enumerate(funding_to_main_rows):
                if fm_i in used_fm_idx:
                    continue
                if (ts_w - 120.0) <= fm_ts <= (ts_w + 7200.0) and abs(fm_amt - delta_w) <= Decimal("0.01"):
                    used_fm_idx.add(fm_i)
                    eff_ts = fm_ts
                    break

            is_first = invested_dec == 0
            invested_dec += delta_w
            if first_in_sec == 0.0 or eff_ts < first_in_sec:
                first_in_sec = eff_ts
            transfers.append(
                {
                    "venue_id": "binance",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "time_sec": int(eff_ts),
                    "time_str": fmt_bj(eff_ts, "%Y-%m-%d %H:%M"),
                    "direction": "Transfer to Copy Pool (Spot -> Copy)" if is_first else "Top-up Copy Pool (Spot -> Copy)",
                    "direction_zh": "划入跟单本金 (现货→跟单)" if is_first else "追加跟单本金 (现货→跟单)",
                    "amount_usd": f2(delta_w, 2),
                }
            )

        if redeem_ev is not None and live_copy_equity <= Decimal("0.01"):
            r_ts, r_tp, r_delta, _ = redeem_ev
            redeemed_dec = abs(r_delta)
            settle_sec = r_ts
            transfers.append(
                {
                    "venue_id": "binance",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "time_sec": int(r_ts),
                    "time_str": fmt_bj(r_ts, "%Y-%m-%d %H:%M"),
                    "direction": "Copy Pool Redemption (Copy -> Futures)" if r_tp == "MAIN_UMFUTURE" else "Copy Pool Redemption (Copy -> Spot)",
                    "direction_zh": "结清赎回转出 (跟单→合约)" if r_tp == "MAIN_UMFUTURE" else "结清赎回转出 (跟单→现货)",
                    "amount_usd": f2(redeemed_dec, 2),
                }
            )

    if live_copy_equity > Decimal("0.01"):
        after_s = settle_sec if settle_sec > 0 else 0.0
        post_net = sum((ev[2] for ev in sf_events if ev[0] > after_s), Decimal("0"))
        active_invested = max(Decimal("0"), post_net - bn_spot_usdt_usd)
        if active_invested >= Decimal("1"):
            invested_dec += active_invested

    if invested_dec <= 0 and reb_total_dec <= 0 and live_copy_equity <= 0:
        return empty_res

    transfers.sort(key=lambda x: x["time_sec"], reverse=True)
    book_pnl_dec = (redeemed_dec + live_copy_equity - invested_dec) if invested_dec > 0 else Decimal("0")
    comb_pnl_dec = book_pnl_dec + reb_total_dec
    is_active = live_copy_equity > Decimal("0.01")
    end_s = settle_sec if settle_sec > 0 else (
        float(max(int(x["time_sec"]) for x in unmatched_rebate_items)) if unmatched_rebate_items else first_in_sec
    )
    start_s = first_in_sec if first_in_sec > 0 else end_s
    period_str = (
        f"{fmt_bj(start_s, '%Y-%m-%d')} ~ {'至今' if is_active else fmt_bj(end_s, '%Y-%m-%d')}"
        if start_s > 0
        else ""
    )

    return {
        "has_copy_trading": True,
        "status": "ACTIVE" if is_active else "SETTLED",
        "status_zh": "跟单在途" if is_active else "已结清",
        "period_zh": period_str,
        "settle_date_bj": fmt_bj(end_s, "%Y-%m-%d") if end_s > 0 else "",
        "total_invested_usd": f2(invested_dec, 2),
        "redeemed_usd": f2(redeemed_dec, 2),
        "current_equity_usd": f2(live_copy_equity, 2),
        "book_pnl_usd": f2(book_pnl_dec, 2),
        "rebate_count": len(rebate_rows),
        "rebate_total_usd": f2(reb_total_dec, 2),
        "combined_pnl_usd": f2(comb_pnl_dec, 2),
        "transfers": transfers,
        "rebates": rebate_rows,
        "_raw_book_pnl": book_pnl_dec,
        "_raw_rebate_total": reb_total_dec,
        "_raw_combined_pnl": comb_pnl_dec,
        "_daily_rebates": dict(daily_rebates),
    }


def _build_binance_stock_section(
    bn_lb: Mapping[str, Any],
    meta: AdapterMeta,
) -> tuple[VenueStockBundle, Decimal, Decimal, Decimal]:
    """Return `(VenueStockBundle, bn_spot_stk_val, bn_iso_stk_eq, bn_stock_net_usd)`."""
    bn_sb = bn_lb.get("stock_bundle") or {}
    bn_tickers = bn_lb.get("spot_tickers") or {}
    bn_dyn_cat: dict[str, dict[str, Any]] = {}
    for ta in bn_sb.get("tokenized_assets") or []:
        if isinstance(ta, Mapping):
            a_sym = str(ta.get("asset") or ta.get("tokenSymbol") or ta.get("symbol") or "").strip().upper()
            u_sym = str(ta.get("underlying") or ta.get("underlyingSymbol") or "").strip().upper()
            if a_sym:
                bn_dyn_cat[a_sym] = {
                    "underlying": u_sym or a_sym.rstrip("B"),
                    "name": str(ta.get("name") or US_EQUITY_TICKERS.get(u_sym, u_sym or a_sym)),
                    "series": "币安 bStock",
                }

    holdings: list[dict[str, Any]] = []
    spot_stk_val = Decimal("0")
    iso_stk_eq = Decimal("0")
    stock_net_pnl = Decimal("0")
    total_fees_usd = Decimal("0")
    total_upnl = Decimal("0")
    eq_notional_sum = Decimal("0")
    eq_fees_sum = Decimal("0")

    bn_eq_trades = bn_sb.get("equity_trades") or []
    bn_eq_quotes = bn_sb.get("equity_quotes") or {}
    bn_trades_by_sym: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for bt in bn_eq_trades:
        if isinstance(bt, Mapping) and bt.get("symbol"):
            bn_trades_by_sym[str(bt["symbol"]).strip().upper()].append(bt)

    bn_has_equity_wallet_row = any(
        any(k in str(w.get("walletName") or "").upper() for k in ("EQUITY", "STOCK"))
        and dec(w.get("balance")) > 0
        for w in (bn_lb.get("wallet_balances") or [])
        if isinstance(w, Mapping)
    )

    for sym_bn, sym_trades in sorted(bn_trades_by_sym.items()):
        tmp = reconstruct_spot_cost_basis(
            fills=sym_trades,
            current_qty=Decimal("0"),
            current_price=Decimal("0"),
            token_symbol=sym_bn,
            quote_price_map=bn_tickers,
        )
        net_sh = tmp["net_qty_from_fills"]
        q_obj = bn_eq_quotes.get(sym_bn) or {}
        live_q_px = dec(q_obj.get("lastPrice") or q_obj.get("price") or q_obj.get("bidPrice") or q_obj.get("askPrice"))
        recon_bn = reconstruct_spot_cost_basis(
            fills=sym_trades,
            current_qty=net_sh,
            current_price=live_q_px if live_q_px > 0 else tmp["avg_cost_usd"],
            token_symbol=sym_bn,
            quote_price_map=bn_tickers,
        )
        stock_net_pnl += recon_bn["net_pnl_usd"]
        total_fees_usd += recon_bn["total_fee_usd"]
        for pf in recon_bn["parsed_fills"]:
            eq_notional_sum += pf["qty"] * pf["price"]
            eq_fees_sum += pf["fee_usd"]
        if net_sh > Decimal("1e-8"):
            mkt_bn = recon_bn["market_value_usd"]
            total_upnl += recon_bn["unrealized_pnl_usd"]
            if not bn_has_equity_wallet_row:
                iso_stk_eq += mkt_bn
            holdings.append(
                {
                    "venue_id": "binance",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "symbol": sym_bn,
                    "name": US_EQUITY_TICKERS.get(sym_bn, sym_bn),
                    "channel": "US Equity",
                    "channel_zh": "美股正股",
                    "shares": f2(net_sh, 4),
                    "avg_cost_usd": f2(recon_bn["avg_cost_usd"], 2),
                    "current_price_usd": f2(live_q_px if live_q_px > 0 else recon_bn["avg_cost_usd"], 2),
                    "market_value_usd": f2(mkt_bn, 2),
                    "pnl_usd": f2(recon_bn["unrealized_pnl_usd"], 2),
                    "pnl_pct": f2(recon_bn["pnl_pct"], 2),
                }
            )

    bn_sp_trades_map = bn_sb.get("spot_stock_trades") or {}
    for sb_bn in bn_lb.get("spot_balances") or []:
        if not isinstance(sb_bn, Mapping):
            continue
        a_bn = str(sb_bn.get("asset") or "").strip().upper()
        q_bn = dec(sb_bn.get("free")) + dec(sb_bn.get("locked"))
        if q_bn <= 0:
            continue
        t_info = classify_spot_stock_token(a_bn, exchange="BINANCE", dynamic_catalog=bn_dyn_cat)
        if not t_info:
            continue
        px_bn = dec(bn_tickers.get(a_bn))
        val_bn = q_bn * px_bn
        recon_sp = reconstruct_spot_cost_basis(
            fills=bn_sp_trades_map.get(a_bn) or [],
            current_qty=q_bn,
            current_price=px_bn,
            token_symbol=a_bn,
            quote_price_map=bn_tickers,
        )
        spot_stk_val += val_bn
        stock_net_pnl += recon_sp["net_pnl_usd"]
        total_fees_usd += recon_sp["total_fee_usd"]
        total_upnl += recon_sp["unrealized_pnl_usd"]
        holdings.append(
            {
                "venue_id": "binance",
                "venue_name": meta.display_name,
                "venue_name_zh": meta.display_name_zh,
                "symbol": t_info["token_symbol"],
                "name": t_info["name"],
                "channel": "1:1 Stock Token",
                "channel_zh": "1:1美股代币",
                "shares": f2(q_bn, 6),
                "avg_cost_usd": f2(recon_sp["avg_cost_usd"], 2),
                "current_price_usd": f2(px_bn, 2),
                "market_value_usd": f2(val_bn, 2),
                "pnl_usd": f2(recon_sp["unrealized_pnl_usd"], 2),
                "pnl_pct": f2(recon_sp["pnl_pct"], 2),
            }
        )

    bn_cr_raw = bn_sb.get("spot_commission_rates") if isinstance(bn_sb.get("spot_commission_rates"), Mapping) else {}
    bn_obs_real_pct = (eq_fees_sum / eq_notional_sum * Decimal("100")) if eq_notional_sum > 0 else None
    has_bn_cr = bool(bn_cr_raw and (bn_cr_raw.get("maker") is not None or bn_cr_raw.get("taker") is not None))
    venue_fee_rate = None
    bn_m_pct = Decimal("0")
    bn_t_pct = Decimal("0")
    if has_bn_cr or bn_obs_real_pct is not None:
        bn_m_pct = dec(bn_cr_raw.get("maker")) * Decimal("100") if has_bn_cr else (bn_obs_real_pct or Decimal("0"))
        bn_t_pct = dec(bn_cr_raw.get("taker")) * Decimal("100") if has_bn_cr else (bn_obs_real_pct or Decimal("0"))
        bn_bnb_on = bool((bn_lb.get("bnb_burn") or {}).get("spotBNBBurn"))
        zh_lbl = f"币安 {fmt_pct_clean(bn_m_pct)}" if bn_m_pct == bn_t_pct else f"币安 挂{fmt_pct_clean(bn_m_pct)}/吃{fmt_pct_clean(bn_t_pct)}"
        en_lbl = f"Binance {fmt_pct_clean(bn_m_pct)}" if bn_m_pct == bn_t_pct else f"Binance Maker {fmt_pct_clean(bn_m_pct)} / Taker {fmt_pct_clean(bn_t_pct)}"
        if bn_bnb_on:
            zh_lbl += " (BNB抵扣)"
            en_lbl += " (BNB Burn)"
        venue_fee_rate = {
            "venue_id": "binance",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "summary": en_lbl,
            "summary_zh": zh_lbl,
            "maker_fee_pct": f2(bn_m_pct, 3),
            "taker_fee_pct": f2(bn_t_pct, 3),
        }

    tot_eq_stk = sum((dec(h["market_value_usd"]) for h in holdings), Decimal("0"))
    return (
        VenueStockBundle(
            equity_usd=f2(tot_eq_stk, 2),
            unrealized_pnl_usd=f2(total_upnl, 2),
            non_perp_fees_usd=f2(total_fees_usd, 4),
            maker_fee_pct=f2(bn_m_pct, 3),
            taker_fee_pct=f2(bn_t_pct, 3),
            venue_fee_rate=venue_fee_rate,
            holdings=holdings,
        ),
        spot_stk_val,
        iso_stk_eq,
        stock_net_pnl,
    )


def build_binance_venue_ledger(
    store: "LedgerStore",
    meta: AdapterMeta,
    *,
    journals: Mapping[str, Mapping[str, Any]],
    now_s: float,
    global_spot_prices: Mapping[str, float] | None = None,
) -> VenueLedgerBundle | None:
    scope = store.venue_scope("binance")
    bn_lb = scope.get_kv("live_bundle", None)
    binance_income = scope.load_records("income", desc=False)
    binance_fills = scope.load_records("fills", desc=False)
    binance_orders = scope.load_records("orders", desc=True)
    binance_cashflows = scope.load_records("cashflows", desc=False)
    if not bn_lb and not binance_income and not binance_fills:
        return None
    bn_lb = bn_lb if isinstance(bn_lb, dict) else {}

    bn_uid = str(bn_lb.get("uid") or "") if bn_lb.get("uid") else ""
    bn_spot_tickers = bn_lb.get("spot_tickers") or {}
    bn_bnb_prices = scope.get_kv("bnb_prices", {})
    bn_commission_rates = scope.get_kv("commission_rates", {})

    bn_closed_records, bn_meta = _build_binance_closed_records(
        binance_fills=binance_fills,
        binance_income=binance_income,
        binance_orders=binance_orders,
        binance_cashflows=binance_cashflows,
        binance_bnb_prices=bn_bnb_prices,
        binance_commission_rates=bn_commission_rates,
        journals=journals,
        now_f=now_s,
    )

    stock_bundle, bn_spot_stk_val, bn_iso_stk_eq, bn_stock_net_usd = _build_binance_stock_section(bn_lb, meta)

    bn_pm_acc = bn_lb.get("pm_account") or {}
    bn_futures_equity_usd = dec(bn_pm_acc.get("accountEquity"))
    bn_futures_available_usd = dec(bn_pm_acc.get("totalAvailableBalance"))
    bn_unrealized_usd = sum((dec(p.get("unRealizedProfit")) for p in (bn_lb.get("futures_positions") or [])), Decimal("0"))

    bn_spot_usdt_usd = Decimal("0")
    bn_raw_spot_tokens_usd = Decimal("0")
    raw_spot_rows: list[tuple[str, float, float, str]] = []
    for spb in bn_lb.get("spot_balances") or []:
        qty_b = dec(spb.get("free")) + dec(spb.get("locked"))
        if qty_b <= 0:
            continue
        asset_b = str(spb.get("asset") or "").upper()
        raw_spot_rows.append((asset_b, float(qty_b), 0.0, "SPOT"))
        if asset_b in ("USDT", "USDC", "FDUSD", "USD1", "BUSD", "USD"):
            bn_spot_usdt_usd += qty_b
        else:
            px_b = (
                _bnb_usd_at(now_s, bn_meta["px_by_day"], bn_meta["dated_bnb"])
                if asset_b == "BNB"
                else dec(bn_spot_tickers.get(asset_b))
            )
            bn_raw_spot_tokens_usd += qty_b * px_b

    bn_non_usdt_tokens_usd = max(Decimal("0"), bn_raw_spot_tokens_usd - bn_spot_stk_val)
    bn_wallet_rows = bn_lb.get("wallet_balances") or []
    if bn_wallet_rows:
        bn_total_equity_usd = sum((dec(w.get("balance")) for w in bn_wallet_rows), Decimal("0")) + bn_iso_stk_eq
    else:
        bn_total_equity_usd = bn_futures_equity_usd + bn_spot_usdt_usd + bn_raw_spot_tokens_usd + bn_iso_stk_eq

    has_binance = bool(bn_uid or bn_total_equity_usd > 0 or bn_closed_records or binance_income)
    if not has_binance:
        return None

    bn_principal_usd = _reconcile_binance_principal(
        binance_cashflows=binance_cashflows,
        spot_tickers=bn_spot_tickers,
        pm_transfer_usd=bn_meta["pm_transfer_usd"],
        px_by_day=bn_meta["px_by_day"],
        dated_bnb=bn_meta["dated_bnb"],
    )
    bn_copy = _build_binance_copy_trading(
        bn_wallet_rows=bn_wallet_rows,
        binance_cashflows=binance_cashflows,
        binance_income=binance_income,
        unmatched_rebate_items=bn_meta["unmatched_rebate_items"],
        bn_live_rebate_rate=bn_meta["live_rebate_rate"],
        bn_spot_usdt_usd=bn_spot_usdt_usd,
        meta=meta,
    )
    has_bn_copy = bool(bn_copy["has_copy_trading"])
    bn_non_copy_unmatched_reb_usd = Decimal("0") if has_bn_copy else bn_meta["unmatched_rebate_usd"]
    bn_non_copy_unmatched_by_sym = {} if has_bn_copy else bn_meta["unmatched_rebate_by_sym"]

    # BNB live discount %
    bn_bnb_paid_fees = Decimal("0")
    for r_bn in bn_closed_records:
        for lk_bn in ("entry_leg", "exit_leg"):
            lg_bn = r_bn.get(lk_bn) or {}
            if dec(lg_bn.get("bnb_discount_saved_usd")) > 0:
                bn_bnb_paid_fees += dec(lg_bn.get("gross_fee_usd"))
    bn_bnb_saved_total_usd = bn_meta["bnb_discount_saved_usd"]
    bn_bnb_base_fees = bn_bnb_paid_fees + bn_bnb_saved_total_usd
    bn_live_bnb_discount_pct = (
        (bn_bnb_saved_total_usd / bn_bnb_base_fees * Decimal("100"))
        if bn_bnb_base_fees > 0
        else Decimal("0")
    )

    # Conservation Law (pop _raw_* before public trade conversion)
    bn_raw_closed_sum = sum((dec(r.pop("_raw_realized_net", r["realized_net_pnl_usd"])) for r in bn_closed_records), Decimal("0"))
    bn_book_sum_usd = sum((dec(r.pop("_raw_book_pnl", r["gate_book_pnl_usd"])) for r in bn_closed_records), Decimal("0")) + bn_meta["unmatched_funding_usd"]
    bn_manual_reb_usd = sum((dec(r.pop("_raw_settled_rebate", r["settled_rebate_usd"])) for r in bn_closed_records), Decimal("0")) + bn_non_copy_unmatched_reb_usd
    bn_futures_net_usd = bn_raw_closed_sum + bn_non_copy_unmatched_reb_usd + bn_meta["unmatched_funding_usd"] + bn_unrealized_usd
    bn_copy_net_usd = bn_copy["_raw_combined_pnl"] if has_bn_copy else Decimal("0")

    if bn_total_equity_usd > 0 or bn_principal_usd > 0:
        bn_net_pnl_usd = bn_total_equity_usd - bn_principal_usd
        bn_raw_adj_usd = bn_net_pnl_usd - (bn_futures_net_usd + bn_copy_net_usd + bn_stock_net_usd + bn_non_usdt_tokens_usd)
    else:
        bn_net_pnl_usd = bn_futures_net_usd + bn_copy_net_usd + bn_stock_net_usd
        bn_raw_adj_usd = Decimal("0")

    has_bn_early_adj = (not has_bn_copy) and abs(bn_raw_adj_usd) >= Decimal("1.0")
    bn_early_adj_usd = bn_raw_adj_usd if has_bn_early_adj else Decimal("0")
    bn_small_adj_usd = Decimal("0") if has_bn_early_adj else bn_raw_adj_usd

    public_trades = [
        normalize_internal_record_to_public_trade(
            r,
            venue_id="binance",
            venue_name=meta.display_name,
            venue_name_zh=meta.display_name_zh,
            color_token=meta.color_token,
        )
        for r in bn_closed_records
    ]
    stock_bundle.perpetuals = build_venue_stock_perpetuals(
        public_trades,
        venue_id="binance",
        venue_name=meta.display_name,
        venue_name_zh=meta.display_name_zh,
        extra_rebate_by_sym=bn_non_copy_unmatched_by_sym,
    )

    # Open positions
    bn_sl_protected: set[str] = set()
    for boo in bn_lb.get("futures_open_orders") or []:
        bn_c = normalize_contract_symbol(str(boo.get("symbol") or ""))
        if str(boo.get("origType") or boo.get("type") or "").upper() in ("STOP", "STOP_MARKET", "TRAILING_STOP_MARKET") and bn_c:
            bn_sl_protected.add(bn_c)

    open_positions: list[dict[str, Any]] = []
    for bp in bn_lb.get("futures_positions") or []:
        raw_amt = dec(bp.get("positionAmt"))
        if raw_amt == 0:
            continue
        bn_c = normalize_contract_symbol(str(bp.get("symbol") or ""))
        ps_raw = str(bp.get("positionSide") or "BOTH").upper()
        is_short_bn = (ps_raw == "SHORT") or (ps_raw == "BOTH" and raw_amt < 0)
        qty_bn = abs(raw_amt)
        entry_px_bn = dec(bp.get("entryPrice"))
        mark_px_bn = dec(bp.get("markPrice")) or entry_px_bn
        liq_px_bn = dec(bp.get("liquidationPrice"))
        unreal_bn = dec(bp.get("unRealizedProfit"))
        notional_bn = f2(qty_bn * mark_px_bn, 2)
        upnl_2dp = f2(unreal_bn, 2)
        try:
            lev = max(1.0, float(bp.get("leverage") or 1.0))
        except (TypeError, ValueError):
            lev = 1.0
        uroi = round((upnl_2dp / (notional_bn / lev)) * 100.0, 2) if notional_bn > 0 else 0.0
        open_positions.append(
            {
                "venue_id": "binance",
                "venue_name": meta.display_name,
                "color_token": meta.color_token,
                "symbol": bn_c,
                "side": "SHORT" if is_short_bn else "LONG",
                "leverage": lev,
                "qty": f2(qty_bn, 6),
                "notional_usd": notional_bn,
                "entry_price": f2(entry_px_bn, 4),
                "mark_price": f2(mark_px_bn, 4),
                "liq_price": f2(liq_px_bn, 4),
                "unrealized_pnl_usd": upnl_2dp,
                "unrealized_roi_pct": uroi,
                "has_stop_order": bn_c in bn_sl_protected,
            }
        )

    eq_2dp = f2(bn_total_equity_usd, 2)
    prin_2dp = f2(bn_principal_usd, 2)
    pnl_2dp = round(eq_2dp - prin_2dp, 2) if (bn_total_equity_usd > 0 or bn_principal_usd > 0) else f2(bn_net_pnl_usd, 2)

    reconcile_streams: list[dict[str, Any]] = [
        {
            "key": "binance_futures",
            "title_zh": "币安 (Binance) 手操合约实收净盈亏" if has_bn_copy else "币安 (Binance) 统一账户合约实收净盈亏",
            "label_zh": "币安 (Binance) 手操合约实收净盈亏" if has_bn_copy else "币安 (Binance) 统一账户合约实收净盈亏",
            "detail_zh": (
                f"币安 {len(bn_closed_records)} 笔平仓单账面 {(bn_book_sum_usd + bn_unrealized_usd):+.2f} U"
                f" + 手操实时返佣 +{bn_manual_reb_usd:.2f} U（另 BNB抵扣已省 +{bn_bnb_saved_total_usd:.2f} U）"
            ),
            "amount_usd": f2(bn_futures_net_usd, 2),
        }
    ]
    if has_bn_copy:
        reconcile_streams.append(
            {
                "key": "binance_copy_trading",
                "title_zh": f"币安 (Binance) 历史合约跟单综合净损益（{bn_copy['status_zh']}）",
                "label_zh": f"币安 (Binance) 历史合约跟单综合净损益（{bn_copy['status_zh']}）",
                "detail_zh": (
                    f"{bn_copy['period_zh']} 累计投入 {bn_copy['total_invested_usd']:.2f} U"
                    + (
                        f" → 结清赎回 {bn_copy['redeemed_usd']:.2f} U"
                        if bn_copy["status"] == "SETTLED"
                        else f" → 当前净值 {bn_copy['current_equity_usd']:.2f} U"
                    )
                    + f"（池账面 {bn_copy['book_pnl_usd']:+.2f} U + {bn_copy['rebate_count']} 笔跟单返佣 +{bn_copy['rebate_total_usd']:.2f} U）"
                ),
                "amount_usd": f2(bn_copy_net_usd, 2),
            }
        )
    if abs(bn_stock_net_usd) >= Decimal("0.01"):
        reconcile_streams.append(
            {
                "key": "binance_stock",
                "title_zh": "币安 (Binance) 美股与代币净损益",
                "label_zh": "币安 (Binance) 美股与代币净损益",
                "detail_zh": f"币安正股与 bStock 净损益 {bn_stock_net_usd:+.2f} U",
                "amount_usd": f2(bn_stock_net_usd, 2),
            }
        )
    if has_bn_early_adj:
        reconcile_streams.append(
            {
                "key": "binance_early_adj",
                "title_zh": "币安升级统一账户前经典合约及汇兑损益",
                "label_zh": "币安升级统一账户前经典合约及汇兑损益",
                "detail_zh": "早期经典合约损益及汇兑/提币费校准",
                "amount_usd": f2(bn_early_adj_usd + bn_non_usdt_tokens_usd, 2),
            }
        )
    elif abs(bn_non_usdt_tokens_usd + bn_small_adj_usd) >= Decimal("0.01"):
        reconcile_streams.append(
            {
                "key": "binance_fx_spot_adj",
                "title_zh": "币安 (Binance) BNB汇兑、尾仓与提币费校准",
                "label_zh": "币安 (Binance) BNB汇兑、尾仓与提币费校准",
                "detail_zh": f"币安现货尾仓 {bn_non_usdt_tokens_usd:.2f} U + 币安 BNB 闪兑升值与提币费净差 {bn_small_adj_usd:+.2f} U",
                "amount_usd": f2(bn_non_usdt_tokens_usd + bn_small_adj_usd, 2),
            }
        )
    reconcile_streams = clamp_reconcile_streams_to_target(
        reconcile_streams,
        pnl_2dp,
        fallback_key="binance_fx_spot_adj",
        fallback_label_zh="币安 (Binance) 账户净值变动",
        fallback_detail_zh="币安现货与合约账户净值变动",
    )

    today_bj_str = fmt_bj(now_s, "%Y-%m-%d")
    bn_first_day = bn_meta["earliest_day_bj"] or today_bj_str
    daily_pnl: dict[str, Decimal] = defaultdict(Decimal)
    daily_comp: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))

    for bn_r in bn_closed_records:
        d_bn = bn_r["close_date_bj"]
        v_bn = dec(bn_r["realized_net_pnl_usd"])
        daily_pnl[d_bn] += v_bn
        daily_comp[d_bn]["binance_futures"] += v_bn
    if has_bn_copy:
        for d_uf, v_uf in bn_meta["daily_unmatched_funding"].items():
            daily_pnl[d_uf] += v_uf
            daily_comp[d_uf]["binance_futures"] += v_uf
        for d_cr, v_cr in bn_copy["_daily_rebates"].items():
            daily_pnl[d_cr] += v_cr
            daily_comp[d_cr]["binance_copy_trading"] += v_cr
        bn_copy_settle_day = bn_copy["settle_date_bj"] or bn_first_day
        if bn_copy["_raw_book_pnl"] != 0:
            daily_pnl[bn_copy_settle_day] += bn_copy["_raw_book_pnl"]
            daily_comp[bn_copy_settle_day]["binance_copy_trading"] += bn_copy["_raw_book_pnl"]
    else:
        for d_ex, v_ex in bn_meta["daily_extra_pnl"].items():
            daily_pnl[d_ex] += v_ex
            daily_comp[d_ex]["binance_futures"] += v_ex
    if bn_unrealized_usd != 0:
        daily_pnl[today_bj_str] += bn_unrealized_usd
        daily_comp[today_bj_str]["binance_futures"] += bn_unrealized_usd
    if bn_stock_net_usd != 0:
        daily_pnl[today_bj_str] += bn_stock_net_usd
        daily_comp[today_bj_str]["binance_stock"] += bn_stock_net_usd
    if bn_non_usdt_tokens_usd != 0:
        daily_pnl[bn_first_day] += bn_non_usdt_tokens_usd
        daily_comp[bn_first_day]["binance_early_adj" if has_bn_early_adj else "binance_fx_spot_adj"] += bn_non_usdt_tokens_usd
    if has_bn_early_adj:
        daily_pnl[bn_first_day] += bn_early_adj_usd
        daily_comp[bn_first_day]["binance_early_adj"] += bn_early_adj_usd
    elif bn_small_adj_usd != 0:
        daily_pnl[bn_first_day] += bn_small_adj_usd
        daily_comp[bn_first_day]["binance_fx_spot_adj"] += bn_small_adj_usd

    px_float_map = {**(global_spot_prices or {}), **extract_binance_spot_prices(store)}
    spot_holdings = build_spot_holdings_list(
        raw_spot_rows,
        venue_id="binance",
        venue_name=meta.display_name,
        color_token=meta.color_token,
        spot_prices=px_float_map,
        excluded_symbols={h["symbol"] for h in stock_bundle.holdings},
    )

    bn_copy_platform = (
        {
            "venue_id": "binance",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "status": bn_copy["status"],
            "invested_usd": bn_copy["total_invested_usd"],
            "equity_usd": bn_copy["current_equity_usd"],
            "book_pnl_usd": bn_copy["book_pnl_usd"],
            "rebate_usd": bn_copy["rebate_total_usd"],
            "combined_pnl_usd": bn_copy["combined_pnl_usd"],
            "rebate_count": bn_copy["rebate_count"],
        }
        if has_bn_copy
        else None
    )

    return VenueLedgerBundle(
        venue_id="binance",
        exchange_card={
            "venue_id": "binance",
            "display_name": meta.display_name,
            "display_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "connected": True,
            "equity_usd": eq_2dp,
            "net_principal_usd": prin_2dp,
            "net_pnl_usd": pnl_2dp,
            "available_margin_usd": f2(bn_futures_available_usd, 2),
            "trade_count": len(public_trades),
            "rebate_rate_pct": f2(bn_meta["live_rebate_rate"] * Decimal("100"), 1),
            "discount_rate_pct": f2(bn_live_bnb_discount_pct, 1),
            "capabilities": meta.capabilities.model_dump(),
        },
        trades=public_trades,
        open_positions=open_positions,
        spot_holdings=spot_holdings,
        reconcile_streams=reconcile_streams,
        daily_pnl=dict(daily_pnl),
        daily_components={d: dict(c) for d, c in daily_comp.items()},
        copy_trading=VenueCopyBundle(
            has_copy_trading=has_bn_copy,
            invested_usd=bn_copy["total_invested_usd"] if has_bn_copy else 0.0,
            active_equity_usd=bn_copy["current_equity_usd"] if has_bn_copy else 0.0,
            book_pnl_usd=bn_copy["book_pnl_usd"] if has_bn_copy else 0.0,
            rebate_usd=bn_copy["rebate_total_usd"] if has_bn_copy else 0.0,
            combined_pnl_usd=bn_copy["combined_pnl_usd"] if has_bn_copy else 0.0,
            platform=bn_copy_platform,
            transfers=bn_copy["transfers"] if has_bn_copy else [],
            rebates=bn_copy["rebates"] if has_bn_copy else [],
        ),
        stocks=stock_bundle,
        fee_context={"live_rebate_rate": bn_meta["live_rebate_rate"]},
    )


def build_binance_fee_matrix_row(
    bundle: VenueLedgerBundle,
    range_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    ex = bundle.exchange_card
    bn_live_rebate_rate: Decimal = bundle.fee_context.get("live_rebate_rate", Decimal("0"))
    bn_all = aggregate_trade_legs_decimal(range_trades)
    bn_crypto_recs = [r for r in range_trades if str(r.get("symbol") or "").split("_")[0] not in STOCK_COMMODITY_BASES]
    bn_stock_recs = [r for r in range_trades if str(r.get("symbol") or "").split("_")[0] in STOCK_COMMODITY_BASES]
    bn_c = aggregate_trade_legs_decimal(bn_crypto_recs)
    bn_s = aggregate_trade_legs_decimal(bn_stock_recs)

    bn_m_gross_bps = (bn_all["m_fee"] / bn_all["m_not"] * Decimal("10000")) if bn_all["m_not"] > 0 else Decimal("0")
    bn_m_net_bps = (bn_all["m_net"] / bn_all["m_not"] * Decimal("10000")) if bn_all["m_not"] > 0 else Decimal("0")
    bn_t_gross_bps = (bn_all["t_fee"] / bn_all["t_not"] * Decimal("10000")) if bn_all["t_not"] > 0 else Decimal("0")
    bn_t_net_bps = (bn_all["t_net"] / bn_all["t_not"] * Decimal("10000")) if bn_all["t_not"] > 0 else Decimal("0")

    bn_c_m_net_rate = (bn_c["m_net"] / bn_c["m_not"]) if bn_c["m_not"] > 0 else Decimal("0")
    bn_s_m_net_rate = (bn_s["m_net"] / bn_s["m_not"]) if bn_s["m_not"] > 0 else Decimal("0")
    bn_savings = (
        (max(Decimal("0"), bn_c["t_net"] - bn_c["t_not"] * bn_c_m_net_rate) if bn_c["m_not"] > 0 else Decimal("0"))
        + max(Decimal("0"), bn_s["t_net"] - bn_s["t_not"] * bn_s_m_net_rate)
    )
    bn_overall_reb_pct = (
        (bn_all["tot_reb"] / bn_all["g_fee"] * Decimal("100"))
        if bn_all["g_fee"] > 0
        else (bn_live_rebate_rate * Decimal("100"))
    )
    bn_base_before_bnb = bn_all["g_fee"] + bn_all["disc_saved"]
    bn_combined_relief_pct = (
        ((bn_all["disc_saved"] + bn_all["tot_reb"]) / bn_base_before_bnb * Decimal("100"))
        if bn_base_before_bnb > 0
        else Decimal("0")
    )

    return {
        "venue_id": "binance",
        "display_name": ex["display_name"],
        "display_name_zh": ex["display_name_zh"],
        "color_token": ex["color_token"],
        "trade_count": len(range_trades),
        "maker_gross_bps": f2(bn_m_gross_bps, 2),
        "maker_net_bps": f2(bn_m_net_bps, 2),
        "taker_gross_bps": f2(bn_t_gross_bps, 2),
        "taker_net_bps": f2(bn_t_net_bps, 2),
        "gross_fee_usd": f2(bn_all["g_fee"], 2),
        "discount_saved_usd": f2(bn_all["disc_saved"], 2),
        "discount_label": "BNB",
        "rebate_usd": f2(bn_all["tot_reb"], 2),
        "rebate_pct": f2(bn_overall_reb_pct, 1),
        "net_fee_usd": f2(bn_all["tot_net"], 2),
        "combined_relief_pct": f2(bn_combined_relief_pct, 1),
        "potential_maker_savings_usd": f2(bn_savings, 2),
    }
