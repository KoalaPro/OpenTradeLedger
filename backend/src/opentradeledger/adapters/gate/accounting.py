"""Independent actuarial accounting engine for Gate (`venue_id = 'gate'`)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
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
    aggregate_trade_legs_decimal,
    build_spot_wallet_balances,
    spot_holdings_from_wallets,
    build_venue_stock_perpetuals,
    clamp_reconcile_streams_to_target,
    contract_mult,
    dec,
    f2,
    fmt_bj,
    fmt_pct_clean,
    format_duration_zh,
    norm_ms,
    norm_sec,
    normalize_internal_record_to_public_trade,
)

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

PENDING_REBATE_WINDOW_SEC = 43_200


def pair_futures_trades_with_rebates(
    trades: Sequence[Mapping[str, Any]],
    rebates: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], Decimal, Decimal]:
    """Pair individual Gate `my_trades` fills 1-to-1 with `FUTURES` USDT partner rebates."""
    fee_fills: list[tuple[str, float, Decimal, str]] = []
    for idx, t in enumerate(trades):
        fee = abs(dec(t.get("fee")))
        if fee <= 0:
            continue
        t_key = str(t.get("id") or f"{t.get('order_id')}_{t.get('create_time')}_{idx}")
        ts = norm_sec(t.get("create_time"))
        role = str(t.get("role") or "taker").lower()
        fee_fills.append((t_key, ts, fee, role))

    fee_fills.sort(key=lambda x: (x[1], x[0]))

    fut_rebates: list[dict[str, Any]] = []
    for idx, r in enumerate(rebates):
        if str(r.get("source") or "").upper() != "FUTURES":
            continue
        if str(r.get("commission_asset") or "").upper() != "USDT":
            continue
        amt = dec(r.get("commission_amount"))
        ct = int(norm_sec(r.get("commission_time")))
        if amt > 0 and ct > 0:
            fut_rebates.append(
                {
                    "_idx": idx,
                    "uid": str(r.get("_rebate_uid") or f"{ct}_{amt}_{idx}"),
                    "commission_time": ct,
                    "commission_amount": amt,
                    "raw": dict(r),
                }
            )
    fut_rebates.sort(key=lambda x: (x["commission_time"], x["uid"]))

    valid_edges: list[tuple[int, int, int, Decimal, float, Decimal, Decimal, int]] = []
    bp_counts: dict[int, int] = defaultdict(int)

    for fi, (_, f_ts, f_fee, _) in enumerate(fee_fills):
        tol = max(Decimal("0.015"), (Decimal("5e-8") / f_fee) * Decimal("10000"))
        for ci, c in enumerate(fut_rebates):
            lag = float(c["commission_time"]) - f_ts
            if lag < 20 or lag > 64_800:
                continue
            rb = c["commission_amount"]
            ratio = rb / f_fee
            if not (Decimal("0.05") <= ratio <= Decimal("0.95")):
                continue
            scaled = ratio * Decimal("10000")
            bp = int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
            rem = abs(scaled - Decimal(bp))
            if rem > tol:
                continue
            bp_counts[bp] += 1
            valid_edges.append((fi, ci, bp, rem, lag, f_fee, rb, c["commission_time"]))

    valid_edges.sort(key=lambda e: (-bp_counts[e[2]], e[0], e[3], e[4], e[1]))

    used_f: set[int] = set()
    used_c: set[int] = set()
    paired: dict[str, dict[str, Any]] = {}
    maker_reb_sum = Decimal("0")
    maker_fee_sum = Decimal("0")
    taker_reb_sum = Decimal("0")
    taker_fee_sum = Decimal("0")

    for fi, ci, _, _, _, f_fee, rb, ct in valid_edges:
        if fi in used_f or ci in used_c:
            continue
        used_f.add(fi)
        used_c.add(ci)
        t_key, _, _, role = fee_fills[fi]
        paired[t_key] = {"rebate_usd": rb, "ratio": rb / f_fee, "commission_time": ct}
        if role == "maker":
            maker_reb_sum += rb
            maker_fee_sum += f_fee
        else:
            taker_reb_sum += rb
            taker_fee_sum += f_fee

    prelim_m_rate = (maker_reb_sum / maker_fee_sum) if maker_fee_sum > 0 else Decimal("0.50")
    prelim_t_rate = (taker_reb_sum / taker_fee_sum) if taker_fee_sum > 0 else Decimal("0.50")
    fallback_edges: list[tuple[Decimal, float, int, int, Decimal, Decimal, int]] = []
    for fi, (_, f_ts, f_fee, role) in enumerate(fee_fills):
        if fi in used_f:
            continue
        role_hint = prelim_m_rate if role == "maker" else prelim_t_rate
        for ci, c in enumerate(fut_rebates):
            if ci in used_c:
                continue
            lag = float(c["commission_time"]) - f_ts
            if lag < 20 or lag > 18_000:
                continue
            rb = c["commission_amount"]
            ratio = rb / f_fee
            if not (Decimal("0.05") <= ratio <= Decimal("0.98")):
                continue
            fallback_edges.append((abs(ratio - role_hint), lag, fi, ci, f_fee, rb, c["commission_time"]))

    fallback_edges.sort(key=lambda e: (e[0], e[1], e[2], e[3]))
    for _, _, fi, ci, f_fee, rb, ct in fallback_edges:
        if fi in used_f or ci in used_c:
            continue
        used_f.add(fi)
        used_c.add(ci)
        t_key, _, _, role = fee_fills[fi]
        paired[t_key] = {"rebate_usd": rb, "ratio": rb / f_fee, "commission_time": ct}
        if role == "maker":
            maker_reb_sum += rb
            maker_fee_sum += f_fee
        else:
            taker_reb_sum += rb
            taker_fee_sum += f_fee

    unmatched = [c["raw"] for ci, c in enumerate(fut_rebates) if ci not in used_c]
    maker_rate = (maker_reb_sum / maker_fee_sum) if maker_fee_sum > 0 else (
        (taker_reb_sum / taker_fee_sum) if taker_fee_sum > 0 else Decimal("0")
    )
    taker_rate = (taker_reb_sum / taker_fee_sum) if taker_fee_sum > 0 else maker_rate
    return paired, unmatched, maker_rate, taker_rate


def _classify_leg_mode(
    fills: Sequence[Mapping[str, Any]],
    order_map: Mapping[str, Mapping[str, Any]],
    *,
    is_exit: bool,
) -> tuple[str, str]:
    if not fills:
        return "UNKNOWN", "账本汇总"
    roles = {str(f.get("role") or "").lower() for f in fills if dec(f.get("size")) != 0 or dec(f.get("fee")) > 0}
    has_auto_stop = False
    for f in fills:
        txt = str(f.get("text") or "").strip().lower()
        oid = str(f.get("order_id") or "").strip()
        ord_obj = order_map.get(oid) or {}
        ord_txt = str(ord_obj.get("text") or "").strip().lower()
        if txt.startswith("ao-") or ord_txt.startswith("ao-"):
            has_auto_stop = True
            break
    if roles == {"maker"}:
        return "LIMIT_MAKER", "限价挂单"
    if roles == {"taker"}:
        if is_exit and has_auto_stop:
            return "STOP_MARKET_TAKER", "止损市价"
        return "MARKET_TAKER", "市价吃单"
    if "maker" in roles and "taker" in roles:
        return "MIXED", "混合成交"
    return "MARKET_TAKER", "市价吃单"


def _summarize_leg(
    fills: Sequence[tuple[str, Mapping[str, Any], Decimal]],
    *,
    is_exit: bool,
    fallback_avg_price: Decimal,
    fallback_qty_base: Decimal,
    paired_rebates: Mapping[str, Mapping[str, Any]],
    order_map: Mapping[str, Mapping[str, Any]],
    maker_rate: Decimal,
    taker_rate: Decimal,
    mult: Decimal,
    now_sec: float,
    override_qty_base: Decimal | None = None,
) -> dict[str, Any]:
    raw_fills = [f for _, f, _ in fills]
    mode_code, mode_zh = _classify_leg_mode(raw_fills, order_map, is_exit=is_exit)

    total_contracts = Decimal("0")
    weighted_px = Decimal("0")
    gross_fee = Decimal("0")
    maker_fee = Decimal("0")
    taker_fee = Decimal("0")
    maker_notional = Decimal("0")
    taker_notional = Decimal("0")
    maker_rebate = Decimal("0")
    taker_rebate = Decimal("0")
    settled_rebate = Decimal("0")
    pending_rebate = Decimal("0")
    latest_fill_sec = 0.0
    earliest_fill_sec = 0.0
    latest_rebate_sec = 0
    has_pending = False
    has_settled = False

    for t_key, f, share in fills:
        if share <= 0:
            continue
        sz = abs(dec(f.get("size"))) * share
        px = dec(f.get("price"))
        fee = abs(dec(f.get("fee"))) * share
        f_sec = norm_sec(f.get("create_time"))
        role = str(f.get("role") or "taker").lower()
        if f_sec > latest_fill_sec:
            latest_fill_sec = f_sec
        if f_sec > 0 and (earliest_fill_sec == 0.0 or f_sec < earliest_fill_sec):
            earliest_fill_sec = f_sec
        fill_notional = (sz * mult * px) if (sz > 0 and px > 0) else Decimal("0")
        if sz > 0 and px > 0:
            total_contracts += sz
            weighted_px += sz * px
        gross_fee += fee
        if role == "maker":
            maker_fee += fee
            maker_notional += fill_notional
        else:
            taker_fee += fee
            taker_notional += fill_notional

        if fee <= 0:
            continue
        match = paired_rebates.get(t_key)
        fill_eff_reb = Decimal("0")
        if match is not None:
            rb_amt = dec(match["rebate_usd"]) * share
            settled_rebate += rb_amt
            fill_eff_reb = rb_amt
            has_settled = True
            ct = int(match["commission_time"])
            if ct > latest_rebate_sec:
                latest_rebate_sec = ct
        elif (now_sec - f_sec) <= PENDING_REBATE_WINDOW_SEC:
            est_rate = maker_rate if role == "maker" else taker_rate
            p_amt = fee * est_rate
            pending_rebate += p_amt
            fill_eff_reb = p_amt
            has_pending = True

        if role == "maker":
            maker_rebate += fill_eff_reb
        else:
            taker_rebate += fill_eff_reb

    qty_base = (
        override_qty_base
        if (override_qty_base is not None and override_qty_base > 0)
        else ((total_contracts * mult) if total_contracts > 0 else fallback_qty_base)
    )
    avg_px = (weighted_px / total_contracts) if total_contracts > 0 else fallback_avg_price
    notional_usd = qty_base * avg_px
    if not fills and notional_usd > 0 and gross_fee > 0:
        taker_notional = notional_usd
    fee_bps = (gross_fee / notional_usd * Decimal("10000")) if notional_usd > 0 else Decimal("0")

    if has_pending and has_settled:
        reb_status = "PARTIAL_PENDING"
    elif has_pending:
        reb_status = "PENDING"
    elif has_settled:
        reb_status = "SETTLED"
    else:
        reb_status = "NONE"

    eff_rebate = settled_rebate + pending_rebate
    reb_pct = (eff_rebate / gross_fee * Decimal("100")) if gross_fee > 0 else Decimal("0")
    leg_sec = latest_fill_sec if is_exit else (earliest_fill_sec or latest_fill_sec)

    return {
        "mode": mode_code,
        "mode_zh": mode_zh,
        "fill_count": len(fills),
        "qty_base": f2(qty_base, 6),
        "avg_price": f2(avg_px, 6),
        "notional_usd": f2(notional_usd, 2),
        "maker_notional_usd": f2(maker_notional, 2),
        "taker_notional_usd": f2(taker_notional, 2),
        "time_sec": int(leg_sec) if leg_sec > 0 else 0,
        "time_bj": fmt_bj(leg_sec, "%m-%d %H:%M:%S") if leg_sec > 0 else "—",
        "gross_fee_usd": f2(gross_fee, 4),
        "maker_fee_usd": f2(maker_fee, 4),
        "taker_fee_usd": f2(taker_fee, 4),
        "maker_rebate_usd": f2(maker_rebate, 4),
        "taker_rebate_usd": f2(taker_rebate, 4),
        "fee_bps": f2(fee_bps, 2),
        "settled_rebate_usd": f2(settled_rebate, 4),
        "pending_rebate_usd": f2(pending_rebate, 4),
        "net_fee_usd": f2(gross_fee - eff_rebate, 4),
        "rebate_pct": f2(reb_pct, 2),
        "rebate_status": reb_status,
        "rebate_settled_bj": fmt_bj(latest_rebate_sec, "%m-%d %H:%M") if latest_rebate_sec > 0 else "",
    }


def _build_gate_stock_section(
    live_bundle: Mapping[str, Any],
    stock_deals: Sequence[Mapping[str, Any]],
    stock_transactions: Sequence[Mapping[str, Any]],
    partner_rebates: Sequence[Mapping[str, Any]],
    meta: AdapterMeta,
) -> tuple[VenueStockBundle, Decimal, Decimal]:
    """Return `(VenueStockBundle, gate_spot_stk_val_dec, gate_spot_stk_net_dec)`."""
    holdings: list[dict[str, Any]] = []
    cash_ledger: list[dict[str, Any]] = []
    total_fees_usd = Decimal("0")
    total_pos_pnl_usd = Decimal("0")
    spot_stk_val = Decimal("0")
    spot_stk_net = Decimal("0")

    stk_assets = live_bundle.get("stock_assets") or {}
    wt_details = ((live_bundle.get("wallet_total") or {}).get("details") or {})
    gate_real_eq = dec(stk_assets.get("equity") or (wt_details.get("tradfi_spot") or {}).get("amount"))
    total_pos_pnl_usd += dec(stk_assets.get("position_pnl"))

    for sp in live_bundle.get("stock_positions") or []:
        vol = dec(sp.get("volume") or sp.get("amount"))
        if vol <= 0:
            continue
        cost_px = dec(sp.get("avg_cost_price") or sp.get("cost_price"))
        pos_pnl = dec(sp.get("position_pnl") or sp.get("profit"))
        cost_val = vol * cost_px
        mkt_val = dec(sp.get("market_value")) or (cost_val + pos_pnl)
        cur_px = dec(sp.get("last_price")) or ((mkt_val / vol) if vol > 0 else cost_px)
        mkt_code = str(sp.get("exchange") or "us").upper()
        chan_en = "HK Equity" if mkt_code == "HK" else "US Equity"
        chan_zh = "港股正股" if mkt_code == "HK" else "美股正股"
        sym_g = str(sp.get("symbol") or "").strip().upper()
        holdings.append(
            {
                "venue_id": "gate",
                "venue_name": meta.display_name,
                "venue_name_zh": meta.display_name_zh,
                "symbol": sym_g,
                "name": str(sp.get("symbol_desc") or sp.get("name") or US_EQUITY_TICKERS.get(sym_g) or sym_g),
                "channel": chan_en,
                "channel_zh": chan_zh,
                "shares": f2(vol, 4),
                "avg_cost_usd": f2(cost_px, 2),
                "current_price_usd": f2(cur_px, 2),
                "market_value_usd": f2(mkt_val, 2),
                "pnl_usd": f2(pos_pnl, 2),
                "pnl_pct": f2(dec(sp.get("pnl_rate") or sp.get("profit_rate")), 2),
            }
        )

    for sd in stock_deals:
        total_fees_usd += abs(dec(sd.get("fee")))

    tx_with_ms: list[tuple[int, dict[str, Any]]] = []
    for tx in stock_transactions:
        ts_ms = norm_ms(tx.get("time"))
        ts_t = norm_sec(ts_ms)
        tp_zh = str(tx.get("type_desc") or tx.get("type") or "")
        tx_with_ms.append(
            (
                ts_ms,
                {
                    "venue_id": "gate",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "time_str": fmt_bj(ts_t, "%Y-%m-%d %H:%M"),
                    "type": str(tx.get("type") or "Settlement"),
                    "type_zh": tp_zh,
                    "symbol": str(tx.get("symbol") or tx.get("asset") or "USDT"),
                    "change": f"{tx.get('change', '0')} {tx.get('unit_text') or tx.get('asset') or ''}".strip(),
                },
            )
        )
    tx_with_ms.sort(key=lambda pair: pair[0], reverse=True)
    cash_ledger = [row for _, row in tx_with_ms]

    uni_bals = ((live_bundle.get("unified") or {}).get("balances") or {})
    sp_bundle = live_bundle.get("spot_stock_bundle") or {}
    sp_tickers = sp_bundle.get("tickers") or {}
    sp_trades = sp_bundle.get("trades") or {}
    if isinstance(uni_bals, Mapping):
        for ccy_k, b_obj in uni_bals.items():
            if not isinstance(b_obj, Mapping):
                continue
            t_info = classify_spot_stock_token(str(ccy_k), exchange="GATE")
            if not t_info:
                continue
            qty_g = dec(b_obj.get("available")) + dec(b_obj.get("freeze") or b_obj.get("locked"))
            if qty_g <= 0:
                continue
            eq_g = dec(b_obj.get("equity"))
            px_g = dec(sp_tickers.get(t_info["token_symbol"])) or ((eq_g / qty_g) if eq_g > 0 else Decimal("0"))
            val_g = eq_g if eq_g > 0 else (qty_g * px_g)
            recon = reconstruct_spot_cost_basis(
                fills=sp_trades.get(t_info["token_symbol"]) or [],
                current_qty=qty_g,
                current_price=px_g,
                token_symbol=t_info["token_symbol"],
            )
            spot_stk_val += val_g
            spot_stk_net += recon["net_pnl_usd"]
            total_fees_usd += recon["total_fee_usd"]
            total_pos_pnl_usd += recon["unrealized_pnl_usd"]
            holdings.append(
                {
                    "venue_id": "gate",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "symbol": t_info["token_symbol"],
                    "name": t_info["name"],
                    "channel": "1:1 Stock Token",
                    "channel_zh": "1:1美股代币",
                    "shares": f2(qty_g, 6),
                    "avg_cost_usd": f2(recon["avg_cost_usd"], 2),
                    "current_price_usd": f2(px_g, 2),
                    "market_value_usd": f2(val_g, 2),
                    "pnl_usd": f2(recon["unrealized_pnl_usd"], 2),
                    "pnl_pct": f2(recon["pnl_pct"], 2),
                }
            )

    stk_rebates_usd = sum(
        (
            dec(r.get("commission_amount"))
            for r in partner_rebates
            if str(r.get("source") or "").upper() in ("STOCK", "TRADFI", "XSTOCKS")
        ),
        Decimal("0"),
    )

    gate_tier = int((live_bundle.get("account_detail") or {}).get("tier") or 0)
    gate_sfr_list = live_bundle.get("stock_fee_rates") or []
    gate_matched_fr = next(
        (r for r in gate_sfr_list if isinstance(r, Mapping) and int(r.get("vip_level") or -1) == gate_tier),
        None,
    )
    gate_wf = live_bundle.get("wallet_fee") or {}
    g_m_pct: Decimal | None = None
    g_t_pct: Decimal | None = None
    if isinstance(gate_matched_fr, Mapping) and (
        gate_matched_fr.get("maker_fee") is not None or gate_matched_fr.get("taker_fee") is not None
    ):
        g_m_pct = dec(gate_matched_fr.get("maker_fee")) * Decimal("100")
        g_t_pct = dec(gate_matched_fr.get("taker_fee")) * Decimal("100")
    elif isinstance(gate_wf, Mapping) and (gate_wf.get("maker_fee") is not None or gate_wf.get("taker_fee") is not None):
        g_m_pct = dec(gate_wf.get("maker_fee")) * Decimal("100")
        g_t_pct = dec(gate_wf.get("taker_fee")) * Decimal("100")

    venue_fee_rate = None
    if g_m_pct is not None and g_t_pct is not None:
        zh_s = f"Gate {fmt_pct_clean(g_m_pct)}" if g_m_pct == g_t_pct else f"Gate 挂{fmt_pct_clean(g_m_pct)}/吃{fmt_pct_clean(g_t_pct)}"
        en_s = f"Gate {fmt_pct_clean(g_m_pct)}" if g_m_pct == g_t_pct else f"Gate Maker {fmt_pct_clean(g_m_pct)} / Taker {fmt_pct_clean(g_t_pct)}"
        venue_fee_rate = {
            "venue_id": "gate",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "summary": en_s,
            "summary_zh": zh_s,
            "maker_fee_pct": f2(g_m_pct, 3),
            "taker_fee_pct": f2(g_t_pct, 3),
        }

    tot_stk_eq = gate_real_eq + spot_stk_val
    return (
        VenueStockBundle(
            equity_usd=f2(tot_stk_eq, 2),
            unrealized_pnl_usd=f2(total_pos_pnl_usd, 2),
            non_perp_fees_usd=f2(total_fees_usd - stk_rebates_usd, 4),
            stock_rebates_usd=f2(stk_rebates_usd, 4),
            maker_fee_pct=f2(g_m_pct if g_m_pct is not None else Decimal("0.1"), 3),
            taker_fee_pct=f2(g_t_pct if g_t_pct is not None else Decimal("0.1"), 3),
            venue_fee_rate=venue_fee_rate,
            holdings=holdings,
            cash_ledger=cash_ledger,
        ),
        spot_stk_val,
        spot_stk_net,
    )


def extract_gate_spot_prices(store: "LedgerStore") -> dict[str, float]:
    lb = store.venue_scope("gate").get_kv("live_bundle", {}) or {}
    out: dict[str, float] = {}
    if isinstance(lb, dict):
        sp_tickers = ((lb.get("spot_stock_bundle") or {}).get("tickers")) or {}
        if isinstance(sp_tickers, dict):
            for sym, raw_px in sp_tickers.items():
                try:
                    px = float(raw_px)
                    if px > 0:
                        out[str(sym).strip().upper()] = px
                except (TypeError, ValueError):
                    continue
        uni_bals = ((lb.get("unified") or {}).get("balances")) or {}
        if isinstance(uni_bals, dict):
            for coin, row in uni_bals.items():
                if not isinstance(row, dict):
                    continue
                q = float(row.get("equity") or 0.0) or (float(row.get("available") or 0.0) + float(row.get("freeze") or 0.0))
                eq_u = float(row.get("equity_usd") or 0.0)
                if q > 0 and eq_u > 0:
                    out.setdefault(str(coin).strip().upper(), eq_u / q)
    return out


def build_gate_venue_ledger(
    store: "LedgerStore",
    meta: AdapterMeta,
    *,
    journals: Mapping[str, Mapping[str, Any]],
    now_s: float,
    global_spot_prices: Mapping[str, float] | None = None,
) -> VenueLedgerBundle | None:
    scope = store.venue_scope("gate")
    live_bundle = scope.get_kv("live_bundle", None)
    futures_closes = scope.load_records("futures_closes", desc=True)
    spot_account_book = scope.load_records("spot_account_book", desc=True)
    if not live_bundle and not futures_closes and not spot_account_book:
        return None
    live_bundle = live_bundle if isinstance(live_bundle, dict) else {}

    futures_trades = scope.load_records("futures_trades", desc=True)
    futures_orders = scope.load_records("futures_orders", desc=True)
    partner_rebates = scope.load_records("partner_rebates", desc=True)
    stock_deals = scope.load_records("stock_deals", desc=False)
    stock_transactions = scope.load_records("stock_transactions", desc=True)
    contract_specs = scope.get_kv("contract_specs", {}) or {}
    eod_snapshots = scope.load_records("eod_snapshots", desc=False)

    paired_rebates, unmatched_fut_rebates, maker_reb_rate, taker_reb_rate = pair_futures_trades_with_rebates(
        futures_trades, partner_rebates
    )
    order_map: dict[str, Mapping[str, Any]] = {
        str(o.get("id")): o for o in futures_orders if o.get("id")
    }

    trades_by_contract: dict[str, list[tuple[str, Mapping[str, Any], float]]] = defaultdict(list)
    for idx, t in enumerate(futures_trades):
        c = str(t.get("contract") or "")
        t_key = str(t.get("id") or f"{t.get('order_id')}_{t.get('create_time')}_{idx}")
        trades_by_contract[c].append((t_key, t, norm_sec(t.get("create_time"))))
    for c_list in trades_by_contract.values():
        c_list.sort(key=lambda x: (x[2], int(dec(x[0], "0")) if x[0].isdigit() else 0, x[0]))

    used_entry_trade_keys: set[str] = set()
    used_exit_trade_keys: set[str] = set()
    internal_records: list[dict[str, Any]] = []

    def _emit_gate_record(
        *,
        rec_key: str,
        base_rec_key: str,
        contract_name: str,
        pos_side: str,
        open_s: float,
        close_s: float,
        qty_b: Decimal,
        en_fills_w: Sequence[tuple[str, Mapping[str, Any], Decimal]],
        ex_fills_w: Sequence[tuple[str, Mapping[str, Any], Decimal]],
        en_px_fb: Decimal,
        ex_px_fb: Decimal,
        g_pnl: Decimal,
        b_fee: Decimal,
        f_pnl: Decimal,
        b_pnl: Decimal,
        mult_v: Decimal,
        is_partial: bool,
        override_leg_qty: Decimal | None = None,
    ) -> None:
        entry_leg = _summarize_leg(
            en_fills_w,
            is_exit=False,
            fallback_avg_price=en_px_fb,
            fallback_qty_base=qty_b,
            paired_rebates=paired_rebates,
            order_map=order_map,
            maker_rate=maker_reb_rate,
            taker_rate=taker_reb_rate,
            mult=mult_v,
            now_sec=now_s,
            override_qty_base=override_leg_qty,
        )
        exit_leg = _summarize_leg(
            ex_fills_w,
            is_exit=True,
            fallback_avg_price=ex_px_fb,
            fallback_qty_base=qty_b,
            paired_rebates=paired_rebates,
            order_map=order_map,
            maker_rate=maker_reb_rate,
            taker_rate=taker_reb_rate,
            mult=mult_v,
            now_sec=now_s,
            override_qty_base=override_leg_qty,
        )
        fills_fee_sum = dec(entry_leg["gross_fee_usd"]) + dec(exit_leg["gross_fee_usd"])
        total_fee = b_fee if b_fee > 0 else fills_fee_sum
        if total_fee > fills_fee_sum + Decimal("0.0001"):
            if int(entry_leg.get("fill_count") or 0) == 0:
                entry_leg["fee_known"] = False
                entry_leg["mode_code"] = "UNKNOWN"
            if int(exit_leg.get("fill_count") or 0) == 0:
                exit_leg["fee_known"] = False
                exit_leg["mode_code"] = "UNKNOWN"
        settled_reb = dec(entry_leg["settled_rebate_usd"]) + dec(exit_leg["settled_rebate_usd"])
        pending_reb = dec(entry_leg["pending_rebate_usd"]) + dec(exit_leg["pending_rebate_usd"])

        if pending_reb > 0 and settled_reb > 0:
            rebate_state = "PARTIAL_PENDING"
        elif pending_reb > 0:
            rebate_state = "PENDING"
        else:
            rebate_state = "SETTLED"

        eff_reb = settled_reb + pending_reb
        eff_reb_pct = (eff_reb / total_fee * Decimal("100")) if total_fee > 0 else Decimal("0")
        realized_net = b_pnl + settled_reb
        expected_net = b_pnl + eff_reb
        hold_sec = max(0, int(close_s - open_s))

        j_entry = journals.get(rec_key) or journals.get(base_rec_key) or {}
        internal_records.append(
            {
                "record_key": rec_key,
                "exchange": "GATE",
                "exchange_zh": "Gate",
                "contract": contract_name,
                "side": pos_side.upper(),
                "is_partial_close": is_partial,
                "qty_base": f2(qty_b, 6),
                "open_time_sec": int(open_s),
                "close_time_sec": int(close_s),
                "open_time_ms": norm_ms(open_s),
                "close_time_ms": norm_ms(close_s),
                "open_datetime_bj": fmt_bj(open_s, "%Y-%m-%d %H:%M:%S"),
                "close_datetime_bj": fmt_bj(close_s, "%Y-%m-%d %H:%M:%S"),
                "close_date_bj": fmt_bj(close_s, "%Y-%m-%d"),
                "holding_seconds": hold_sec,
                "holding_zh": format_duration_zh(hold_sec),
                "entry_leg": entry_leg,
                "exit_leg": exit_leg,
                "gross_pnl_usd": f2(g_pnl, 4),
                "funding_pnl_usd": f2(f_pnl, 4),
                "total_fee_usd": f2(total_fee, 4),
                "settled_rebate_usd": f2(settled_reb, 4),
                "pending_rebate_usd": f2(pending_reb, 4),
                "net_fee_usd": f2(total_fee - eff_reb, 4),
                "effective_rebate_pct": f2(eff_reb_pct, 2),
                "rebate_state": rebate_state,
                "gate_book_pnl_usd": f2(b_pnl, 4),
                "realized_net_pnl_usd": f2(realized_net, 4),
                "expected_net_pnl_usd": f2(expected_net, 4),
                "tags": list(j_entry.get("tags") or []),
                "note": str(j_entry.get("note") or ""),
            }
        )

    closes_chrono = sorted(
        futures_closes,
        key=lambda x: (norm_sec(x.get("time")), norm_sec(x.get("first_open_time"))),
    )
    for c_row in closes_chrono:
        contract = str(c_row.get("contract") or "")
        side = str(c_row.get("side") or "long").lower()
        t_open = norm_sec(c_row.get("first_open_time"))
        t_close = norm_sec(c_row.get("time"))
        base_record_key = f"fut_{contract}_{int(t_open)}_{int(t_close)}_{side}"
        mult = contract_mult(contract, contract_specs)

        total_contracts_closed = abs(dec(c_row.get("accum_size") or c_row.get("max_size")))
        qty_base_total = total_contracts_closed * mult
        long_px = dec(c_row.get("long_price"))
        short_px = dec(c_row.get("short_price"))
        entry_px_fb = short_px if side == "short" else long_px
        exit_px_fb = long_px if side == "short" else short_px

        gross_pnl_tot = dec(c_row.get("pnl_pnl"))
        book_fee_tot = abs(dec(c_row.get("pnl_fee")))
        funding_pnl_tot = dec(c_row.get("pnl_fund"))
        gate_book_pnl_tot = dec(c_row.get("pnl"))

        seq_events: list[tuple[str, float, str, str, Decimal, Decimal, Mapping[str, Any], Decimal]] = []
        for t_key, tr, tr_sec in trades_by_contract.get(contract, []):
            if tr_sec < (t_open - 2.0) or tr_sec > (t_close + 2.0):
                continue
            close_sz = dec(tr.get("close_size"))
            sz = dec(tr.get("size"))
            oid_g = str(tr.get("order_id") or t_key).strip()

            if sz == 0:
                if abs(tr_sec - t_close) <= abs(tr_sec - t_open) and t_key not in used_exit_trade_keys:
                    used_exit_trade_keys.add(t_key)
                    seq_events.append(("EXIT", tr_sec, t_key, oid_g, Decimal("0"), dec(tr.get("price")), tr, Decimal("1")))
                elif t_key not in used_entry_trade_keys:
                    used_entry_trade_keys.add(t_key)
                    seq_events.append(("ENTRY", tr_sec, t_key, oid_g, Decimal("0"), dec(tr.get("price")), tr, Decimal("1")))
                continue

            is_exit_dir = (sz < 0) if side == "long" else (sz > 0)
            is_entry_dir = (sz > 0) if side == "long" else (sz < 0)
            abs_sz = abs(sz)
            abs_csz = abs(close_sz)

            if is_exit_dir and abs_csz > 0 and t_key not in used_exit_trade_keys:
                eff_contracts = min(abs_sz, abs_csz)
                share_f = eff_contracts / abs_sz
                used_exit_trade_keys.add(t_key)
                seq_events.append(("EXIT", tr_sec, t_key, oid_g, eff_contracts, dec(tr.get("price")), tr, share_f))
            elif is_entry_dir and abs_sz > abs_csz and t_key not in used_entry_trade_keys:
                eff_contracts = abs_sz - abs_csz
                share_f = eff_contracts / abs_sz
                used_entry_trade_keys.add(t_key)
                seq_events.append(("ENTRY", tr_sec, t_key, oid_g, eff_contracts, dec(tr.get("price")), tr, share_f))

        distinct_exit_oids: list[str] = []
        for kind_e, _, _, oid_e, _, _, _, _ in seq_events:
            if kind_e == "EXIT" and oid_e not in distinct_exit_oids:
                distinct_exit_oids.append(oid_e)

        if len(distinct_exit_oids) <= 1:
            en_w = [(e[2], e[6], e[7]) for e in seq_events if e[0] == "ENTRY"]
            ex_w = [(e[2], e[6], e[7]) for e in seq_events if e[0] == "EXIT"]
            _emit_gate_record(
                rec_key=base_record_key,
                base_rec_key=base_record_key,
                contract_name=contract,
                pos_side=side,
                open_s=t_open,
                close_s=t_close,
                qty_b=qty_base_total,
                en_fills_w=en_w,
                ex_fills_w=ex_w,
                en_px_fb=entry_px_fb,
                ex_px_fb=exit_px_fb,
                g_pnl=gross_pnl_tot,
                b_fee=book_fee_tot,
                f_pnl=funding_pnl_tot,
                b_pnl=gate_book_pnl_tot,
                mult_v=mult,
                is_partial=False,
                override_leg_qty=qty_base_total if qty_base_total > 0 else None,
            )
        else:
            grouped_seq: list[tuple[str, list[tuple[str, float, str, str, Decimal, Decimal, Mapping[str, Any], Decimal]]]] = []
            for ev in seq_events:
                if ev[0] == "ENTRY":
                    grouped_seq.append(("ENTRY", [ev]))
                else:
                    if grouped_seq and grouped_seq[-1][0] == "EXIT" and grouped_seq[-1][1][0][3] == ev[3]:
                        grouped_seq[-1][1].append(ev)
                    else:
                        grouped_seq.append(("EXIT", [ev]))

            total_exit_groups = sum(1 for k_g, _ in grouped_seq if k_g == "EXIT")
            seen_exit_groups = 0
            open_pool: list[list[Any]] = []
            pool_t0 = t_open
            rem_gross = gross_pnl_tot
            rem_fund = funding_pnl_tot
            rem_book_fee = book_fee_tot
            rem_book_pnl = gate_book_pnl_tot

            for k_g, ev_list in grouped_seq:
                if k_g == "ENTRY":
                    for _, ts_e, tk_e, _, c_e, px_e, tr_e, sh_e in ev_list:
                        if sum((p[3] for p in open_pool), Decimal("0")) <= Decimal("1e-9"):
                            pool_t0 = ts_e
                        open_pool.append([tk_e, tr_e, sh_e, c_e, px_e])
                else:
                    seen_exit_groups += 1
                    is_last_grp = seen_exit_groups == total_exit_groups
                    ex_contracts = sum((x[4] for x in ev_list), Decimal("0"))
                    ex_px_avg = (
                        sum((x[4] * x[5] for x in ev_list), Decimal("0")) / ex_contracts
                        if ex_contracts > 0
                        else exit_px_fb
                    )
                    ex_t1 = max(x[1] for x in ev_list)
                    ex_oid = ev_list[-1][3]
                    ex_w_grp = [(x[2], x[6], x[7]) for x in ev_list]

                    pool_tot_c = sum((p[3] for p in open_pool), Decimal("0"))
                    pool_avg_px = (
                        sum((p[3] * p[4] for p in open_pool), Decimal("0")) / pool_tot_c
                        if pool_tot_c > 0
                        else entry_px_fb
                    )
                    slice_ratio = (
                        Decimal("1")
                        if (is_last_grp or pool_tot_c <= 0)
                        else min(Decimal("1"), ex_contracts / pool_tot_c)
                    )
                    sliced_en_w: list[tuple[str, Mapping[str, Any], Decimal]] = []
                    if slice_ratio >= Decimal("1") - Decimal("1e-11"):
                        for p_item in open_pool:
                            if p_item[2] > 0:
                                sliced_en_w.append((p_item[0], p_item[1], p_item[2]))
                        open_pool.clear()
                    else:
                        for p_item in open_pool:
                            take_sh = p_item[2] * slice_ratio
                            take_c = p_item[3] * slice_ratio
                            p_item[2] -= take_sh
                            p_item[3] -= take_c
                            if take_sh > 0:
                                sliced_en_w.append((p_item[0], p_item[1], take_sh))

                    q_grp = ex_contracts * mult
                    if is_last_grp:
                        g_pnl_grp = rem_gross
                        f_pnl_grp = rem_fund
                        b_fee_grp = rem_book_fee
                        b_pnl_grp = rem_book_pnl
                        rk_grp = base_record_key
                    else:
                        g_pnl_grp = (ex_px_avg - pool_avg_px) * q_grp if side == "long" else (pool_avg_px - ex_px_avg) * q_grp
                        f_pnl_grp = funding_pnl_tot * (ex_contracts / total_contracts_closed) if total_contracts_closed > 0 else Decimal("0")
                        en_f_est = sum((abs(dec(tr_i.get("fee"))) * sh_i for _, tr_i, sh_i in sliced_en_w), Decimal("0"))
                        ex_f_est = sum((abs(dec(tr_i.get("fee"))) * sh_i for _, tr_i, sh_i in ex_w_grp), Decimal("0"))
                        b_fee_grp = en_f_est + ex_f_est
                        b_pnl_grp = g_pnl_grp + f_pnl_grp - b_fee_grp
                        rem_gross -= g_pnl_grp
                        rem_fund -= f_pnl_grp
                        rem_book_fee = max(Decimal("0"), rem_book_fee - b_fee_grp)
                        rem_book_pnl -= b_pnl_grp
                        rk_grp = f"fut_{contract}_{int(pool_t0)}_{int(ex_t1)}_{side}_{ex_oid}"

                    _emit_gate_record(
                        rec_key=rk_grp,
                        base_rec_key=base_record_key,
                        contract_name=contract,
                        pos_side=side,
                        open_s=pool_t0,
                        close_s=ex_t1,
                        qty_b=q_grp,
                        en_fills_w=sliced_en_w,
                        ex_fills_w=ex_w_grp,
                        en_px_fb=pool_avg_px,
                        ex_px_fb=ex_px_avg,
                        g_pnl=g_pnl_grp,
                        b_fee=b_fee_grp,
                        f_pnl=f_pnl_grp,
                        b_pnl=b_pnl_grp,
                        mult_v=mult,
                        is_partial=not is_last_grp,
                    )

    public_trades = [
        normalize_internal_record_to_public_trade(
            r,
            venue_id="gate",
            venue_name=meta.display_name,
            venue_name_zh=meta.display_name_zh,
            color_token=meta.color_token,
        )
        for r in internal_records
    ]

    # Live balances & open positions
    wt = live_bundle.get("wallet_total") or {}
    wt_total = wt.get("total") or {}
    wt_details = wt.get("details") or {}
    fa = live_bundle.get("futures_account") or {}
    uni = live_bundle.get("unified") or {}
    stk_assets = live_bundle.get("stock_assets") or {}

    gate_total_equity_usd = dec(wt_total.get("amount"))
    unified_equity_usd = dec(uni.get("unified_account_total_equity") or (wt_details.get("spot") or {}).get("amount"))
    gate_futures_available_usd = dec(fa.get("available"))
    quant_equity_usd = dec((wt_details.get("quant") or {}).get("amount"))
    stock_equity_usd = dec(stk_assets.get("equity") or (wt_details.get("tradfi_spot") or {}).get("amount"))
    finance_equity_usd = dec((wt_details.get("finance") or {}).get("amount"))

    sl_protected_contracts: set[str] = set()
    for po in live_bundle.get("futures_price_orders") or []:
        c = str((po.get("initial") or {}).get("contract") or po.get("contract") or "")
        is_close = bool(
            (po.get("initial") or {}).get("is_close")
            or (po.get("initial") or {}).get("is_reduce_only")
            or po.get("is_close")
        )
        if c and is_close:
            sl_protected_contracts.add(c)

    open_positions: list[dict[str, Any]] = []
    gate_open_upl = Decimal("0")
    for p in live_bundle.get("futures_positions") or []:
        c = str(p.get("contract") or "")
        sz = dec(p.get("size"))
        if sz == 0:
            continue
        mult = contract_mult(c, contract_specs)
        qty_base = abs(sz) * mult
        entry_px = dec(p.get("entry_price"))
        mark_px = dec(p.get("mark_price"))
        liq_px = dec(p.get("liq_price"))
        unreal = dec(p.get("unrealised_pnl"))
        notional = qty_base * mark_px
        raw_lev = p.get("cross_leverage_limit") or p.get("leverage") or 1
        try:
            lev = max(1.0, float(raw_lev))
        except (TypeError, ValueError):
            lev = 1.0
        notional_2dp = f2(notional, 2)
        upnl_2dp = f2(unreal, 2)
        uroi = round((upnl_2dp / (notional_2dp / lev)) * 100.0, 2) if notional_2dp > 0 else 0.0
        gate_open_upl += dec(f2(unreal, 4))
        open_positions.append(
            {
                "venue_id": "gate",
                "venue_name": meta.display_name,
                "color_token": meta.color_token,
                "symbol": c,
                "side": "LONG" if sz > 0 else "SHORT",
                "leverage": lev,
                "qty": f2(qty_base, 6),
                "notional_usd": notional_2dp,
                "entry_price": f2(entry_px, 4),
                "mark_price": f2(mark_px, 4),
                "liq_price": f2(liq_px, 4),
                "unrealized_pnl_usd": upnl_2dp,
                "unrealized_roi_pct": uroi,
                "has_stop_order": c in sl_protected_contracts,
            }
        )

    # Gate Copy Trading
    copy_labels = {
        "fct_out": ("Transfer to Copy Pool", "划入跟单本金"),
        "fct_in": ("Copy Pool Redemption", "跟单本金转回"),
        "fct_refund": ("Copy Pool Refund", "跟单资金退回"),
        "fct_fee": ("Leader Profit Share", "支付带单员分润"),
        "fct_fee_refund": ("Profit Share Refund", "带单分润退回"),
    }
    copy_transfers: list[dict[str, Any]] = []
    net_copy_principal = Decimal("0")
    earliest_copy_sec: float | None = None
    for sb_row in spot_account_book:
        tp = str(sb_row.get("type") or "")
        if tp in copy_labels and str(sb_row.get("currency") or "").upper() == "USDT":
            chg = dec(sb_row.get("change"))
            if chg == 0:
                continue
            ts_s = norm_sec(sb_row.get("time"))
            if tp in ("fct_out", "fct_in", "fct_refund"):
                net_copy_principal += -chg
            if earliest_copy_sec is None or ts_s < earliest_copy_sec:
                earliest_copy_sec = ts_s
            dir_en, dir_zh = copy_labels[tp]
            copy_transfers.append(
                {
                    "venue_id": "gate",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "time_sec": int(ts_s),
                    "time_str": fmt_bj(ts_s, "%Y-%m-%d %H:%M"),
                    "direction": dir_en,
                    "direction_zh": dir_zh,
                    "amount_usd": f2(abs(chg), 2),
                }
            )
    copy_transfers.sort(key=lambda x: x["time_sec"], reverse=True)

    pu_rebate_entries = [
        {
            "id": str(sb_row.get("id") or ""),
            "sec": norm_sec(sb_row.get("time")),
            "change": dec(sb_row.get("change")),
        }
        for sb_row in spot_account_book
        if str(sb_row.get("type") or "") == "pu_rebate" and str(sb_row.get("currency") or "").upper() == "USDT"
    ]
    used_pu_ids: set[str] = set()
    copy_rebates: list[dict[str, Any]] = []
    other_unmatched_rebate_usd = Decimal("0")
    for ur in sorted(unmatched_fut_rebates, key=lambda x: int(norm_sec(x.get("commission_time"))), reverse=True):
        ct = int(norm_sec(ur.get("commission_time")))
        amt = dec(ur.get("commission_amount"))
        if earliest_copy_sec is not None and ct >= (earliest_copy_sec - 300):
            for pu in pu_rebate_entries:
                if pu["id"] and pu["id"] in used_pu_ids:
                    continue
                if abs(pu["change"] - amt) <= Decimal("1e-8") and abs(pu["sec"] - float(ct)) <= 10.0:
                    if pu["id"]:
                        used_pu_ids.add(pu["id"])
                    break
            copy_rebates.append(
                {
                    "venue_id": "gate",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "time_sec": ct,
                    "time_str": fmt_bj(ct, "%m-%d %H:%M:%S"),
                    "destination": "Unified Account USDT (pu_rebate)",
                    "destination_zh": "统一账户 USDT (pu_rebate)",
                    "est_gross_fee_usd": f2(amt / taker_reb_rate, 3) if taker_reb_rate > 0 else 0.0,
                    "rebate_usd": f2(amt, 4),
                }
            )
        else:
            other_unmatched_rebate_usd += amt

    copy_reb_total = sum((dec(x["rebate_usd"]) for x in copy_rebates), Decimal("0"))
    copy_book_pnl = (quant_equity_usd - net_copy_principal) if net_copy_principal > 0 else Decimal("0")
    copy_combined_pnl = copy_book_pnl + copy_reb_total
    has_gate_copy = bool(net_copy_principal > 0 or copy_rebates or quant_equity_usd > 0)
    gate_copy_platform = (
        {
            "venue_id": "gate",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "status": "ACTIVE" if quant_equity_usd > Decimal("0.01") else "SETTLED",
            "invested_usd": f2(net_copy_principal, 2),
            "equity_usd": f2(quant_equity_usd, 2),
            "book_pnl_usd": f2(copy_book_pnl, 2),
            "rebate_usd": f2(copy_reb_total, 2),
            "combined_pnl_usd": f2(copy_combined_pnl, 2),
            "rebate_count": len(copy_rebates),
        }
        if has_gate_copy
        else None
    )

    # Gate Stocks & Perpetuals
    stock_bundle, gate_spot_stk_val, gate_spot_stk_net = _build_gate_stock_section(
        live_bundle, stock_deals, stock_transactions, partner_rebates, meta
    )
    stock_bundle.perpetuals = build_venue_stock_perpetuals(
        public_trades,
        venue_id="gate",
        venue_name=meta.display_name,
        venue_name_zh=meta.display_name_zh,
    )

    # Gate Whole-Account Conservation Law
    deposit_in_usd = Decimal("0")
    push_in_usd = Decimal("0")
    c2c_in_usd = Decimal("0")
    outflow_usd = Decimal("0")
    launchpad_net_usdt = Decimal("0")
    has_launchpad_flow = False
    earn_ledger_usdt = Decimal("0")
    cv_net_usdt = Decimal("0")

    for sb_row in spot_account_book:
        if str(sb_row.get("currency") or "").upper() != "USDT":
            continue
        tp = str(sb_row.get("type") or "")
        cd = str(sb_row.get("code") or "")
        chg = dec(sb_row.get("change"))
        if tp == "deposit":
            deposit_in_usd += chg
        elif tp == "push_rcvd":
            push_in_usd += chg
        elif tp in ("c2c_om", "c2c_in") and chg > 0:
            c2c_in_usd += chg
        elif tp in ("withdraw", "push_dctd", "c2c_out") or (tp == "c2c_om" and chg < 0):
            outflow_usd += abs(chg)
        elif cd in ("150222", "150223", "150256", "150258"):
            launchpad_net_usdt += chg
            has_launchpad_flow = True
        elif tp in ("lend", "redeem") or cd in ("686", "687", "688", "150349"):
            earn_ledger_usdt += chg
        elif tp in ("cv_buy", "cv_sell"):
            cv_net_usdt += chg

    if has_launchpad_flow:
        sub_str = f"{launchpad_net_usdt:+.2f}"
        if not any(sub_str in r["change"] for r in stock_bundle.cash_ledger):
            stock_bundle.cash_ledger.append(
                {
                    "venue_id": "gate",
                    "venue_name": meta.display_name,
                    "venue_name_zh": meta.display_name_zh,
                    "time_str": "IPO / Launchpad",
                    "type": "IPO Subscription Net Debit",
                    "type_zh": "美股打新认购净扣款",
                    "symbol": "USDT",
                    "change": f"{sub_str} USDT",
                }
            )

    gate_net_principal_usd = deposit_in_usd + push_in_usd + c2c_in_usd - outflow_usd
    gate_manual_book_usd = sum((dec(r["gate_book_pnl_usd"]) for r in internal_records), Decimal("0")) + gate_open_upl
    gate_manual_settled_reb_usd = sum((dec(r["settled_rebate_usd"]) for r in internal_records), Decimal("0"))
    gate_manual_net_usd = gate_manual_book_usd + gate_manual_settled_reb_usd

    copy_all_net_usd = copy_combined_pnl + other_unmatched_rebate_usd
    gate_real_stock_net_usd = (launchpad_net_usdt + stock_equity_usd) if has_launchpad_flow else dec(stk_assets.get("position_pnl"))
    gate_stock_net_usd = gate_real_stock_net_usd + gate_spot_stk_net
    earn_all_usd = earn_ledger_usdt + finance_equity_usd

    uni_balances = uni.get("balances") if isinstance(uni, Mapping) else None
    usdt_bal_obj = (uni_balances or {}).get("USDT") if isinstance(uni_balances, Mapping) else None
    if isinstance(usdt_bal_obj, Mapping) and (
        usdt_bal_obj.get("equity") is not None
        or usdt_bal_obj.get("available") is not None
        or usdt_bal_obj.get("freeze") is not None
    ):
        usdt_eq_val = dec(usdt_bal_obj.get("equity"))
        usdt_spot_cash = (
            usdt_eq_val
            if usdt_eq_val > 0
            else (dec(usdt_bal_obj.get("available")) + dec(usdt_bal_obj.get("freeze") or usdt_bal_obj.get("locked")))
        )
        non_usdt_tokens_usd = max(Decimal("0"), unified_equity_usd - usdt_spot_cash - gate_spot_stk_val)
    else:
        non_usdt_tokens_usd = Decimal("0")

    if gate_net_principal_usd > 0:
        gate_only_net_pnl_usd = gate_total_equity_usd - gate_net_principal_usd
        fx_and_voucher_adj_usd = gate_only_net_pnl_usd - (
            gate_manual_net_usd + copy_all_net_usd + gate_stock_net_usd + earn_all_usd + non_usdt_tokens_usd
        )
    else:
        pv_dnw = dec(
            ((fa.get("history") or {}) if isinstance(fa, Mapping) else {}).get("pv_dnw")
            or ((fa.get("history") or {}) if isinstance(fa, Mapping) else {}).get("dnw")
        )
        fx_and_voucher_adj_usd = cv_net_usdt + pv_dnw
        gate_only_net_pnl_usd = (
            gate_manual_net_usd + copy_all_net_usd + gate_stock_net_usd + earn_all_usd + non_usdt_tokens_usd + fx_and_voucher_adj_usd
        )

    eq_2dp = f2(gate_total_equity_usd, 2)
    prin_2dp = f2(gate_net_principal_usd, 2)
    pnl_2dp = round(eq_2dp - prin_2dp, 2) if gate_net_principal_usd > 0 else f2(gate_only_net_pnl_usd, 2)

    reconcile_streams: list[dict[str, Any]] = [
        {
            "key": "manual_futures",
            "title_zh": "Gate 手操合约实收净盈亏",
            "label_zh": "Gate 手操合约实收净盈亏",
            "detail_zh": f"Gate {len(internal_records)} 笔平仓及浮盈 {gate_manual_book_usd:+.2f} + 合伙人返佣 +{gate_manual_settled_reb_usd:.2f}",
            "amount_usd": f2(gate_manual_net_usd, 2),
        },
        {
            "key": "copy_trading",
            "title_zh": "Gate 实盘跟单综合净赚",
            "label_zh": "Gate 实盘跟单综合净赚",
            "detail_zh": f"跟单池浮盈 {copy_book_pnl:+.2f} + 返佣直退统一账户 +{(copy_reb_total + other_unmatched_rebate_usd):.2f}",
            "amount_usd": f2(copy_all_net_usd, 2),
        },
        {
            "key": "stock",
            "title_zh": "Gate 美股打新与持仓净赚",
            "label_zh": "Gate 美股打新与持仓净赚",
            "detail_zh": (
                f"认购净扣 {launchpad_net_usdt:+.2f} U + 当前股票市值 {stock_equity_usd:.2f} U"
                if has_launchpad_flow
                else f"当前股票持仓浮盈 {dec(stk_assets.get('position_pnl')):+.2f} U"
            ),
            "amount_usd": f2(gate_stock_net_usd, 2),
        },
        {
            "key": "earn",
            "title_zh": "余币宝与理财奖励利息",
            "label_zh": "余币宝与理财奖励利息",
            "detail_zh": "定期/新手理财利息 + 余币宝赎回收益 + 活动加息奖励",
            "amount_usd": f2(earn_all_usd, 2),
        },
        {
            "key": "airdrop_tokens",
            "title_zh": "Gate 空投及非U代币现值",
            "label_zh": "Gate 空投及非U代币现值",
            "detail_zh": f"统一账户内 WLFI / GT 等空投代币折合估值 {non_usdt_tokens_usd:.2f} U",
            "amount_usd": f2(non_usdt_tokens_usd, 2),
        },
        {
            "key": "fx_voucher_adj",
            "title_zh": "Gate 闪兑点差与体验券微调",
            "label_zh": "Gate 闪兑点差与体验券微调",
            "detail_zh": f"USD1 闪兑 {cv_net_usdt:+.2f} U 及体验券/结算尾差校准",
            "amount_usd": f2(fx_and_voucher_adj_usd, 2),
        },
    ]
    reconcile_streams = clamp_reconcile_streams_to_target(
        reconcile_streams,
        pnl_2dp,
        fallback_key="fx_voucher_adj",
        fallback_label_zh="Gate 闪兑点差与体验券微调",
        fallback_detail_zh="体验券与结算尾差校准",
    )

    # Gate EOD Telescoping Daily PnL
    today_bj_str = fmt_bj(now_s, "%Y-%m-%d")
    daily_ext_flow: dict[str, Decimal] = defaultdict(Decimal)
    daily_realized_ledger: dict[str, Decimal] = defaultdict(Decimal)
    daily_comp_realized: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    daily_copy_prin_flow: dict[str, Decimal] = defaultdict(Decimal)
    daily_lp_flow: dict[str, Decimal] = defaultdict(Decimal)
    has_spot_settle = any(
        str(r.get("type") or "") in ("spot_settle_in", "spot_settle_out") for r in spot_account_book
    )
    lent_principal_run = Decimal("0")
    fx_pending_run = Decimal("0")

    for sb_row in sorted(spot_account_book, key=lambda x: norm_sec(x.get("time"))):
        if str(sb_row.get("currency") or "").upper() != "USDT":
            continue
        tp = str(sb_row.get("type") or "")
        cd = str(sb_row.get("code") or "")
        rid = str(sb_row.get("id") or "")
        chg = dec(sb_row.get("change"))
        ts_sb = norm_sec(sb_row.get("time"))
        if ts_sb <= 0:
            continue
        day_sb = fmt_bj(ts_sb, "%Y-%m-%d")

        if tp in ("deposit", "push_rcvd") or (tp in ("c2c_om", "c2c_in") and chg > 0):
            daily_ext_flow[day_sb] += chg
        elif tp in ("withdraw", "push_dctd", "c2c_out") or (tp == "c2c_om" and chg < 0):
            daily_ext_flow[day_sb] -= abs(chg)
        elif tp in ("fct_out", "fct_in", "fct_refund"):
            daily_copy_prin_flow[day_sb] += -chg
        elif cd in ("150222", "150223", "150256", "150258"):
            daily_lp_flow[day_sb] += chg
        elif tp in ("spot_settle_in", "spot_settle_out"):
            daily_realized_ledger[day_sb] += chg
            daily_comp_realized[day_sb]["manual_futures"] += chg
        elif tp == "pu_rebate":
            daily_realized_ledger[day_sb] += chg
            if rid and rid in used_pu_ids:
                daily_comp_realized[day_sb]["copy_trading"] += chg
            else:
                daily_comp_realized[day_sb]["manual_futures"] += chg
        elif cd in ("688", "150349"):
            daily_realized_ledger[day_sb] += chg
            daily_comp_realized[day_sb]["earn"] += chg
        elif tp == "lend" and chg < 0:
            lent_principal_run += abs(chg)
        elif tp == "redeem" and chg > 0:
            used_p = min(lent_principal_run, chg)
            interest_part = chg - used_p
            lent_principal_run -= used_p
            if interest_part != 0:
                daily_realized_ledger[day_sb] += interest_part
                daily_comp_realized[day_sb]["earn"] += interest_part
        elif tp == "cv_sell" and chg < 0:
            fx_pending_run += chg
        elif tp == "cv_buy" and chg > 0:
            fx_diff = fx_pending_run + chg
            daily_realized_ledger[day_sb] += fx_diff
            daily_comp_realized[day_sb]["fx_voucher_adj"] += fx_diff
            fx_pending_run = Decimal("0")

    if not has_spot_settle:
        for rec in internal_records:
            d_close = rec["close_date_bj"]
            m_val = dec(rec["gate_book_pnl_usd"]) + dec(rec["settled_rebate_usd"])
            daily_realized_ledger[d_close] += m_val
            daily_comp_realized[d_close]["manual_futures"] += m_val

    eod_snap_by_day: dict[str, dict[str, Decimal]] = {}
    for snap in sorted(eod_snapshots, key=lambda s: float(dec(s.get("ts_sec")))):
        ts_sn = float(dec(snap.get("ts_sec")))
        eq_sn = dec(snap.get("total_equity"))
        if ts_sn > 0 and eq_sn > 0:
            eod_snap_by_day[fmt_bj(ts_sn, "%Y-%m-%d")] = {
                "total": eq_sn,
                "quant": dec(snap.get("quant_equity")),
                "stock": dec(snap.get("stock_equity")),
            }
    if gate_total_equity_usd > 0:
        eod_snap_by_day[today_bj_str] = {
            "total": gate_total_equity_usd,
            "quant": quant_equity_usd,
            "stock": stock_equity_usd,
        }

    snap_days = sorted(eod_snap_by_day.keys())
    anchor_day = snap_days[0] if snap_days else today_bj_str

    all_gate_hist_days = [
        d
        for d in (
            set(daily_ext_flow.keys())
            | set(daily_realized_ledger.keys())
            | set(daily_copy_prin_flow.keys())
            | set(daily_lp_flow.keys())
            | {r["close_date_bj"] for r in internal_records if r.get("close_date_bj")}
        )
        if d and d <= anchor_day
    ]
    gate_first_day = min(all_gate_hist_days) if all_gate_hist_days else anchor_day
    copy_origin_day = min(
        (d for d in daily_copy_prin_flow.keys() if d and d <= anchor_day),
        default=gate_first_day,
    )
    lp_days_pre = [d for d in daily_lp_flow.keys() if d and d <= anchor_day]
    stk_deal_days_pre = [
        fmt_bj(norm_sec(sd.get("time") or sd.get("create_time")), "%Y-%m-%d")
        for sd in stock_deals
        if norm_sec(sd.get("time") or sd.get("create_time")) > 0
    ]
    stk_deal_days_pre = [d for d in stk_deal_days_pre if d and d <= anchor_day]
    stock_origin_day = (
        max(lp_days_pre)
        if lp_days_pre
        else (min(stk_deal_days_pre) if stk_deal_days_pre else gate_first_day)
    )

    whole_daily_pnl: dict[str, Decimal] = defaultdict(Decimal)
    daily_comp: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for d_key, val in daily_realized_ledger.items():
        if d_key <= anchor_day and val != 0:
            whole_daily_pnl[d_key] += val
            for c_k, c_v in daily_comp_realized[d_key].items():
                daily_comp[d_key][c_k] += c_v

    cum_prin_at_anchor = sum((v for d_k, v in daily_ext_flow.items() if d_k <= anchor_day), Decimal("0"))
    if anchor_day in eod_snap_by_day and cum_prin_at_anchor > 0:
        target_cum_at_anchor = eod_snap_by_day[anchor_day]["total"] - cum_prin_at_anchor
    else:
        target_cum_at_anchor = gate_only_net_pnl_usd

    if anchor_day == today_bj_str or len(snap_days) <= 1:
        anchor_targets = {
            "manual_futures": gate_manual_net_usd,
            "copy_trading": copy_all_net_usd,
            "stock": gate_stock_net_usd,
            "earn": earn_all_usd,
            "airdrop_tokens": non_usdt_tokens_usd,
        }
    else:
        a_snap = eod_snap_by_day[anchor_day]
        copy_prin_anchor = sum((v for d_k, v in daily_copy_prin_flow.items() if d_k <= anchor_day), Decimal("0"))
        copy_reb_anchor = sum(
            (daily_comp_realized[d_k]["copy_trading"] for d_k in daily_comp_realized if d_k <= anchor_day), Decimal("0")
        ) + other_unmatched_rebate_usd
        lp_anchor = sum((v for d_k, v in daily_lp_flow.items() if d_k <= anchor_day), Decimal("0"))
        anchor_targets = {
            "manual_futures": sum(
                (daily_comp_realized[d_k]["manual_futures"] for d_k in daily_comp_realized if d_k <= anchor_day), Decimal("0")
            ),
            "copy_trading": ((a_snap["quant"] - copy_prin_anchor) if copy_prin_anchor > 0 else Decimal("0")) + copy_reb_anchor,
            "stock": (lp_anchor + a_snap["stock"]) if has_launchpad_flow else Decimal("0"),
            "earn": sum(
                (daily_comp_realized[d_k]["earn"] for d_k in daily_comp_realized if d_k <= anchor_day), Decimal("0")
            ),
            "airdrop_tokens": non_usdt_tokens_usd,
        }

    for c_k, t_val in anchor_targets.items():
        realized_c = sum((daily_comp[d_k][c_k] for d_k in list(daily_comp.keys()) if d_k <= anchor_day), Decimal("0"))
        gap_c = t_val - realized_c
        if gap_c == 0:
            continue
        if c_k == "manual_futures":
            if anchor_day == today_bj_str and gate_open_upl != 0:
                whole_daily_pnl[today_bj_str] += gate_open_upl
                daily_comp[today_bj_str]["manual_futures"] += gate_open_upl
                rem_m = gap_c - gate_open_upl
                if rem_m != 0:
                    whole_daily_pnl[gate_first_day] += rem_m
                    daily_comp[gate_first_day]["manual_futures"] += rem_m
            else:
                whole_daily_pnl[gate_first_day] += gap_c
                daily_comp[gate_first_day]["manual_futures"] += gap_c
        elif c_k == "copy_trading":
            if other_unmatched_rebate_usd != 0:
                whole_daily_pnl[gate_first_day] += other_unmatched_rebate_usd
                daily_comp[gate_first_day]["copy_trading"] += other_unmatched_rebate_usd
            rem_cp = gap_c - other_unmatched_rebate_usd
            if rem_cp != 0:
                whole_daily_pnl[copy_origin_day] += rem_cp
                daily_comp[copy_origin_day]["copy_trading"] += rem_cp
        elif c_k == "stock":
            whole_daily_pnl[stock_origin_day] += gap_c
            daily_comp[stock_origin_day]["stock"] += gap_c
        else:
            whole_daily_pnl[gate_first_day] += gap_c
            daily_comp[gate_first_day][c_k] += gap_c

    fx_gap = target_cum_at_anchor - sum(
        (whole_daily_pnl[d_k] for d_k in list(whole_daily_pnl.keys()) if d_k <= anchor_day),
        Decimal("0"),
    )
    if fx_gap != 0:
        whole_daily_pnl[gate_first_day] += fx_gap
        daily_comp[gate_first_day]["fx_voucher_adj"] += fx_gap

    for idx_s in range(1, len(snap_days)):
        prev_d = snap_days[idx_s - 1]
        cur_d = snap_days[idx_s]
        flow_between = sum((v for d_k, v in daily_ext_flow.items() if prev_d < d_k <= cur_d), Decimal("0"))
        whole_daily_pnl[cur_d] = (eod_snap_by_day[cur_d]["total"] - eod_snap_by_day[prev_d]["total"]) - flow_between

        m_between = sum(
            (daily_comp_realized[d_k]["manual_futures"] for d_k in daily_comp_realized if prev_d < d_k <= cur_d), Decimal("0")
        )
        e_between = sum(
            (daily_comp_realized[d_k]["earn"] for d_k in daily_comp_realized if prev_d < d_k <= cur_d), Decimal("0")
        )
        c_prin_between = sum((v for d_k, v in daily_copy_prin_flow.items() if prev_d < d_k <= cur_d), Decimal("0"))
        c_reb_between = sum(
            (daily_comp_realized[d_k]["copy_trading"] for d_k in daily_comp_realized if prev_d < d_k <= cur_d), Decimal("0")
        )
        lp_between = sum((v for d_k, v in daily_lp_flow.items() if prev_d < d_k <= cur_d), Decimal("0"))

        daily_comp[cur_d]["manual_futures"] = m_between
        daily_comp[cur_d]["copy_trading"] = (
            eod_snap_by_day[cur_d]["quant"] - eod_snap_by_day[prev_d]["quant"] - c_prin_between + c_reb_between
        )
        daily_comp[cur_d]["stock"] = (
            eod_snap_by_day[cur_d]["stock"]
            - eod_snap_by_day[prev_d]["stock"]
            + lp_between
            + (gate_spot_stk_net if cur_d == today_bj_str else Decimal("0"))
        )
        daily_comp[cur_d]["earn"] = e_between
        daily_comp[cur_d]["airdrop_tokens"] = Decimal("0")
        daily_comp[cur_d]["fx_voucher_adj"] = whole_daily_pnl[cur_d] - sum(
            (daily_comp[cur_d][k] for k in ("manual_futures", "copy_trading", "stock", "earn", "airdrop_tokens")),
            Decimal("0"),
        )

    # Gate Spot Holdings
    px_map = {**(global_spot_prices or {}), **extract_gate_spot_prices(store)}
    raw_spot_rows: list[tuple[str, float, float, str]] = []
    if isinstance(uni_balances, Mapping):
        for coin, row in uni_balances.items():
            if not isinstance(row, Mapping):
                continue
            q = float(row.get("equity") or 0.0) if float(row.get("equity") or 0.0) > 0 else (
                float(row.get("available") or 0.0) + float(row.get("freeze") or 0.0)
            )
            raw_spot_rows.append((str(coin), q, 0.0, "UNIFIED"))

    wallet_balances = build_spot_wallet_balances(
        raw_spot_rows,
        venue_id="gate",
        venue_name=meta.display_name,
        color_token=meta.color_token,
        spot_prices=px_map,
        excluded_symbols={h["symbol"] for h in stock_bundle.holdings},
    )

    fa_pos_m = dec(fa.get("position_margin")) if isinstance(fa, Mapping) else Decimal("0")
    fa_ord_m = dec(fa.get("order_margin")) if isinstance(fa, Mapping) else Decimal("0")
    fa_upnl = dec(fa.get("unrealised_pnl")) if isinstance(fa, Mapping) else Decimal("0")
    fa_tot_plus_upnl = (dec(fa.get("total")) + fa_upnl) if (isinstance(fa, Mapping) and fa.get("total") is not None) else Decimal("0")
    fa_comp_sum = gate_futures_available_usd + fa_pos_m + fa_ord_m + fa_upnl
    wt_fut_amt = dec((wt_details.get("futures") or {}).get("amount"))
    uni_credit_mode = bool(
        (isinstance(fa, Mapping) and fa.get("enable_credit"))
        or str(uni.get("mode") or "").strip().lower() in {"multi_currency", "portfolio", "single_currency"}
    )
    uni_usdt_eq = (
        dec(usdt_bal_obj.get("equity") or usdt_bal_obj.get("cross_balance"))
        if (uni_credit_mode and isinstance(usdt_bal_obj, Mapping))
        else Decimal("0")
    )
    raw_gate_fut_eq = max(Decimal("0"), wt_fut_amt, fa_tot_plus_upnl, fa_comp_sum, uni_usdt_eq)
    gate_max_fut = max(
        Decimal("0"),
        gate_total_equity_usd - quant_equity_usd - stock_equity_usd - gate_spot_stk_val - finance_equity_usd - non_usdt_tokens_usd,
    )
    gate_futures_equity_usd = min(gate_max_fut, raw_gate_fut_eq)

    return VenueLedgerBundle(
        venue_id="gate",
        exchange_card={
            "venue_id": "gate",
            "display_name": meta.display_name,
            "display_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "connected": True,
            "equity_usd": eq_2dp,
            "net_principal_usd": prin_2dp,
            "net_pnl_usd": pnl_2dp,
            "futures_equity_usd": f2(gate_futures_equity_usd, 2),
            "available_margin_usd": f2(gate_futures_available_usd, 2),
            "trade_count": len(public_trades),
            "rebate_rate_pct": f2(taker_reb_rate * Decimal("100"), 1),
            "discount_rate_pct": 0.0,
            "capabilities": meta.capabilities.model_dump(),
        },
        trades=public_trades,
        open_positions=open_positions,
        spot_holdings=spot_holdings_from_wallets(wallet_balances),
        spot_wallet_balances=wallet_balances,
        reconcile_streams=reconcile_streams,
        daily_pnl=dict(whole_daily_pnl),
        daily_components={d: dict(c) for d, c in daily_comp.items()},
        copy_trading=VenueCopyBundle(
            has_copy_trading=has_gate_copy,
            invested_usd=f2(net_copy_principal, 2) if has_gate_copy else 0.0,
            active_equity_usd=f2(quant_equity_usd, 2) if has_gate_copy else 0.0,
            book_pnl_usd=f2(copy_book_pnl, 2) if has_gate_copy else 0.0,
            rebate_usd=f2(copy_reb_total, 2) if has_gate_copy else 0.0,
            combined_pnl_usd=f2(copy_combined_pnl, 2) if has_gate_copy else 0.0,
            platform=gate_copy_platform,
            transfers=copy_transfers if has_gate_copy else [],
            rebates=copy_rebates if has_gate_copy else [],
        ),
        stocks=stock_bundle,
        fee_context={
            "maker_reb_rate": maker_reb_rate,
            "taker_reb_rate": taker_reb_rate,
        },
    )


def build_gate_fee_matrix_row(
    bundle: VenueLedgerBundle,
    range_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    ex = bundle.exchange_card
    maker_reb_rate: Decimal = bundle.fee_context.get("maker_reb_rate", Decimal("0"))
    taker_reb_rate: Decimal = bundle.fee_context.get("taker_reb_rate", Decimal("0"))
    g = aggregate_trade_legs_decimal(range_trades)

    g_m_gross_bps = (g["m_fee"] / g["m_not"] * Decimal("10000")) if g["m_not"] > 0 else Decimal("0")
    g_m_net_bps = (g["m_net"] / g["m_not"] * Decimal("10000")) if g["m_not"] > 0 else Decimal("0")
    g_t_gross_bps = (g["t_fee"] / g["t_not"] * Decimal("10000")) if g["t_not"] > 0 else Decimal("0")
    g_t_net_bps = (g["t_net"] / g["t_not"] * Decimal("10000")) if g["t_not"] > 0 else Decimal("0")

    g_m_net_rate = (g["m_net"] / g["m_not"]) if g["m_not"] > 0 else (Decimal("0.0002") * (Decimal("1") - maker_reb_rate))
    g_eff_t_not = g["t_not"] if g["t_not"] > 0 else (g["t_fee"] / Decimal("0.0005") if g["t_fee"] > 0 else Decimal("0"))
    g_savings = max(Decimal("0"), g["t_net"] - (g_eff_t_not * g_m_net_rate))

    gross = f2(g["g_fee"], 2)
    reb = f2(g["tot_reb"], 2)
    net_fee = round(max(0.0, f2(g["tot_net"], 2)), 2)
    reb_pct = round((reb / gross) * 100.0, 1) if gross > 0 else f2(taker_reb_rate * Decimal("100"), 1)
    blended_gross_bps = (g["g_fee"] / g["tot_not"] * Decimal("10000")) if g["tot_not"] > 0 else Decimal("0")
    blended_net_bps = (max(Decimal("0"), g["tot_net"]) / g["tot_not"] * Decimal("10000")) if g["tot_not"] > 0 else Decimal("0")

    return {
        "venue_id": "gate",
        "display_name": ex["display_name"],
        "display_name_zh": ex["display_name_zh"],
        "color_token": ex["color_token"],
        "trade_count": len(range_trades),
        "maker_gross_bps": f2(g_m_gross_bps, 2),
        "maker_net_bps": f2(g_m_net_bps, 2),
        "taker_gross_bps": f2(g_t_gross_bps, 2),
        "taker_net_bps": f2(g_t_net_bps, 2),
        "maker_bps_known": bool(g["m_not"] > 0),
        "taker_bps_known": bool(g["t_not"] > 0),
        "blended_gross_bps": f2(blended_gross_bps, 2),
        "blended_net_bps": f2(blended_net_bps, 2),
        "gross_fee_usd": gross,
        "discount_saved_usd": 0.0,
        "discount_label": "",
        "rebate_usd": reb,
        "rebate_pct": reb_pct,
        "net_fee_usd": net_fee,
        "combined_relief_pct": reb_pct,
        "potential_maker_savings_usd": f2(g_savings, 2),
    }
