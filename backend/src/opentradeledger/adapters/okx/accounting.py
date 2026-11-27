"""Independent actuarial accounting engine for OKX v5 (`venue_id = 'okx'`)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from opentradeledger.core.position_lifecycle import trade_daily_amounts
from opentradeledger.adapters.base import (
    AdapterMeta,
    VenueCopyBundle,
    VenueLedgerBundle,
    VenueStockBundle,
)
from opentradeledger.adapters.okx.contracts import contract_quantity, instrument_at
from opentradeledger.adapters.okx.client import is_okx_trading_rebate, normalize_okx_closed_trades, okx_fill_credit_ids
from opentradeledger.adapters.okx.client import validate_snapshot_funding
from opentradeledger.core.venue_primitives import (
    STABLECOINS,
    build_spot_wallet_balances,
    spot_holdings_from_wallets,
    build_venue_stock_perpetuals,
    clamp_reconcile_streams_to_target,
    dec,
    fmt_ms,
    merge_legacy_journal_entries,
    norm_ms,
    is_tokenized_stock_symbol,
)

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore


def extract_okx_spot_prices(store: "LedgerStore") -> dict[str, float]:
    snap = store.venue_scope("okx").get_kv("snapshot")
    out: dict[str, float] = {}
    if isinstance(snap, dict) and isinstance(snap.get("tickers"), dict):
        for sym, raw_px in (snap.get("tickers") or {}).items():
            try:
                px = float(raw_px)
                if px > 0:
                    out[str(sym).strip().upper()] = px
            except (TypeError, ValueError):
                continue
    return out


def build_okx_venue_ledger(
    store: "LedgerStore",
    meta: AdapterMeta,
    *,
    journals: Mapping[str, Mapping[str, Any]],
    now_s: float,
    global_spot_prices: Mapping[str, float] | None = None,
) -> VenueLedgerBundle | None:
    snap = store.venue_scope("okx").get_kv("snapshot")
    if not isinstance(snap, dict):
        return None

    validate_snapshot_funding(snap)
    px_map = {**(global_spot_prices or {}), **extract_okx_spot_prices(store)}

    def _coin_usd(ccy_raw: str, amt: float) -> float:
        c = (ccy_raw or "").strip().upper()
        if not c or amt == 0:
            return 0.0
        if c in STABLECOINS:
            return amt
        return amt * float(px_map.get(c, 0.0))

    equity_usd = 0.0
    available_usd = 0.0
    tokenized_stocks: list[dict[str, Any]] = []
    raw_spot_rows: list[tuple[str, float, float, str]] = []

    for bal in snap.get("balances") or []:
        equity_usd += float(bal.get("totalEq") or 0.0)
        for d in bal.get("details") or []:
            ccy = str(d.get("ccy") or "").strip().upper()
            q = float(d.get("eq") or d.get("cashBal") or 0.0)
            eq_u = float(d.get("eqUsd") or 0.0)
            if ccy in {"USDT", "USDC"}:
                available_usd += float(d.get("availBal") or 0.0)
            raw_spot_rows.append((ccy, q, eq_u, "UNIFIED"))
            if is_tokenized_stock_symbol(ccy):
                mv_stk = eq_u or _coin_usd(ccy, q)
                if q > 0 and mv_stk >= 0.5:
                    cur_px = mv_stk / q
                    avg_px = float(d.get("openAvgPx") or d.get("accAvgPx") or cur_px)
                    pnl_stk = round((cur_px - avg_px) * q, 2)
                    pnl_pct = round(((cur_px - avg_px) / avg_px) * 100.0, 2) if avg_px > 0 else 0.0
                    tokenized_stocks.append(
                        {
                            "venue_id": "okx",
                            "venue_name": meta.display_name,
                            "venue_name_zh": meta.display_name_zh,
                            "symbol": ccy,
                            "name": ccy,
                            "channel": "1:1 Stock Token",
                            "channel_zh": "1:1美股代币",
                            "shares": round(q, 6),
                            "avg_cost_usd": round(avg_px, 2),
                            "current_price_usd": round(cur_px, 2),
                            "market_value_usd": round(mv_stk, 2),
                            "pnl_usd": pnl_stk,
                            "pnl_pct": pnl_pct,
                        }
                    )

    for fb in snap.get("funding") or []:
        ccy_f = str(fb.get("ccy") or "").strip().upper()
        q_f = float(fb.get("bal") or 0.0)
        equity_usd += _coin_usd(ccy_f, q_f)
        raw_spot_rows.append((ccy_f, q_f, 0.0, "FUNDING"))

    dep = sum(
        _coin_usd(str(x.get("ccy") or ""), float(x.get("amt") or 0.0))
        for x in (snap.get("deposits") or [])
        if str(x.get("state") or "2") == "2"
    )
    wd = sum(
        _coin_usd(str(x.get("ccy") or ""), float(x.get("amt") or 0.0))
        for x in (snap.get("withdrawals") or [])
        if str(x.get("state") or "2") == "2"
    )
    ext_asset_flow = 0.0
    for ab in snap.get("asset_bills") or []:
        ab_type = str(ab.get("type") or "").strip().lower()
        if ab_type in {"", "1", "2", "6", "transfer", "internal_transfer"}:
            continue
        if any(tok in ab_type for tok in ("c2c", "p2p", "fiat", "otc", "uid", "sub")):
            ext_asset_flow += _coin_usd(str(ab.get("ccy") or ""), float(ab.get("balChg") or 0.0))
    principal_usd = round(dep - wd + ext_asset_flow, 2)

    # Trading-account and funding-account bill types use different code tables.
    daily_comp: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    bill_totals: dict[str, Decimal] = defaultdict(Decimal)
    seen_cash_bills = okx_fill_credit_ids(snap.get("fills") or [])
    for stream in ("bills", "asset_bills"):
        for bill in snap.get(stream) or []:
            bill_id = str(bill.get("billId") or "")
            if bill_id and bill_id in seen_cash_bills:
                continue
            bill_type = str(bill.get("type") or "")
            subtype = str(bill.get("subType") or "").lower()
            if stream == "bills":
                is_rebate = is_okx_trading_rebate(bill)
                is_interest = subtype in {"interest", "earn_interest", "saving_interest"} or bool(dec(bill.get("earnAmt")))
            else:
                is_rebate = bill_type in {"68", "173", "300"}
                is_interest = bill_type in {"83", "89", "139", "307", "308", "309", "343", "344", "350", "400", "408", "516", "523"}
            if not (is_rebate or is_interest):
                continue
            if bill_id:
                seen_cash_bills.add(bill_id)
            raw_amount = bill.get("earnAmt") if is_interest and dec(bill.get("earnAmt")) else bill.get("balChg")
            amount = _coin_usd(str(bill.get("ccy") or "USDT"), float(dec(raw_amount)))
            key = "okx_earn_spot"
            if is_rebate:
                key = "okx_fee_rebates" if stream == "asset_bills" else "okx_futures"
            bill_totals[key] += dec(amount)
            day = fmt_ms(norm_ms(bill.get("ts")), "%Y-%m-%d")
            if day and amount:
                daily_comp[day][key] += dec(amount)

    copy_pos_rows = snap.get("copy_positions") or []
    copy_hist_rows = snap.get("copy_history") or []
    copy_platform: dict[str, Any] | None = None
    copy_invested_usd = 0.0
    copy_equity_usd = 0.0
    copy_pnl_usd = 0.0
    if copy_pos_rows or copy_hist_rows:
        c_margin = sum(float(cp.get("margin") or 0.0) for cp in copy_pos_rows)
        c_upl = sum(float(cp.get("upl") or 0.0) for cp in copy_pos_rows)
        c_real = sum(float(ch.get("pnl") or ch.get("realizedPnl") or 0.0) for ch in copy_hist_rows)
        copy_invested_usd = round(c_margin, 2)
        copy_equity_usd = round(max(0.0, c_margin + c_upl), 2)
        copy_pnl_usd = round(c_upl + c_real, 2)
        copy_platform = {
            "venue_id": "okx",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "status": "ACTIVE" if copy_pos_rows else "HISTORICAL",
            "invested_usd": copy_invested_usd,
            "equity_usd": copy_equity_usd,
            "book_pnl_usd": copy_pnl_usd,
            "rebate_usd": 0.0,
            "combined_pnl_usd": copy_pnl_usd,
            "rebate_count": 0,
        }

    quote = (snap.get("trade_fee") or [{}])[0]
    maker_quote = quote.get("makerU") if quote.get("makerU") not in (None, "") else quote.get("maker")
    taker_quote = quote.get("takerU") if quote.get("takerU") not in (None, "") else quote.get("taker")
    maker_bps = -float(maker_quote or 0.0) * 10_000
    taker_bps = -float(taker_quote or 0.0) * 10_000

    instruments = snap.get("instruments") or []
    open_positions: list[dict[str, Any]] = []
    for pos in snap.get("positions") or []:
        count = abs(float(pos.get("pos") or 0.0))
        if count <= 0:
            continue
        inst = str(pos.get("instId") or "").replace("-SWAP", "").replace("-", "_")
        side_raw = str(pos.get("posSide") or "long").upper()
        side = "SHORT" if side_raw == "SHORT" or float(pos.get("pos") or 0.0) < 0 else "LONG"
        open_positions.append(
            {
                "venue_id": "okx",
                "venue_name": meta.display_name,
                "color_token": meta.color_token,
                "symbol": inst,
                "side": side,
                "leverage": float(pos.get("lever") or 1.0),
                "contract_count": count,
                "qty": contract_quantity(instrument_at(instruments, str(pos.get("instId")), int(pos.get("cTime") or 0)),
                                         count, float(pos.get("markPx") or 0)),
                "notional_usd": round(abs(float(pos.get("notionalUsd") or 0.0)), 2),
                "entry_price": float(pos.get("avgPx") or 0.0),
                "mark_price": float(pos.get("markPx") or 0.0),
                "liq_price": float(pos.get("liqPx") or 0.0),
                "unrealized_pnl_usd": round(float(pos.get("upl") or 0.0), 2),
                "unrealized_roi_pct": round(float(pos.get("uplRatio") or 0.0) * 100.0, 2),
                "has_stop_order": False,
            }
        )

    raw_trades = normalize_okx_closed_trades(
        snap.get("positions_history") or [],
        snap.get("fills") or [],
        bills=snap.get("bills") or [],
        instruments=instruments,
        live_positions=snap.get("positions") or [],
        rebate_ratio=float(snap.get("observed_rebate_ratio") or 0.0),
        okb_usd_price=float(px_map.get("OKB") or 0.0),
    )
    trades: list[dict[str, Any]] = []

    for rt in raw_trades:
        rt["color_token"] = meta.color_token
        rt["open_time_str"] = fmt_ms(rt["open_time_ms"])
        rt["close_time_str"] = fmt_ms(rt["close_time_ms"])
        rt["close_date"] = fmt_ms(rt["close_time_ms"], "%Y-%m-%d")
        rt["holding_sec"] = max(0, (rt["close_time_ms"] - rt["open_time_ms"]) // 1000)
        base_f = rt["total_fee_usd"] + rt["total_discount_saved_usd"]
        rt["effective_relief_pct"] = (
            round(((rt["total_rebate_usd"] + rt["total_discount_saved_usd"]) / base_f) * 100.0, 1)
            if base_f > 0
            else 0.0
        )
        rt["entry_leg"]["time_str"] = fmt_ms(rt["entry_leg"]["time_ms"], "%m-%d %H:%M")
        rt["entry_leg"].setdefault("settled_at", "")
        rt["exit_leg"]["time_str"] = fmt_ms(rt["exit_leg"]["time_ms"], "%m-%d %H:%M")
        rt["exit_leg"].setdefault("settled_at", "")
        legacy_ids = rt.pop("_legacy_trade_ids", ()) or ()
        j = merge_legacy_journal_entries(journals, rt["trade_id"], legacy_ids)
        rt["tags"] = list(j.get("tags") or [])
        rt["note"] = str(j.get("note") or "")
        trades.append(rt)
        if rt["close_date"]:
            # Fill credits are already included in exchange realized PnL; separate cash bills post on their settlement day.
            for day, amount in trade_daily_amounts(rt, dec(rt["realized_pnl_usd"])).items():
                daily_comp[day]["okx_futures"] += amount

    equity_2dp = round(equity_usd, 2)

    open_upl_sum = sum(float(p["unrealized_pnl_usd"]) for p in open_positions)
    closed_book_sum = sum(float(t["realized_pnl_usd"]) for t in trades)
    closed_reb_sum = float(bill_totals["okx_futures"])
    wallet_rebates_usd = float(bill_totals["okx_fee_rebates"])
    earn_interest_usd = float(bill_totals["okx_earn_spot"])
    closed_disc_sum = sum(float(t["total_discount_saved_usd"]) for t in trades)
    futures_stream_usd = round(closed_book_sum + closed_reb_sum + open_upl_sum, 2)
    net_pnl = round(equity_2dp - principal_usd, 2) if principal_usd != 0 else round(
        closed_book_sum + closed_reb_sum + wallet_rebates_usd + earn_interest_usd, 2
    )

    reconcile_streams: list[dict[str, Any]] = []
    if trades or open_positions or abs(futures_stream_usd) > 0.001:
        disc_note_en = f" (OKB saved +{closed_disc_sum:.2f} U)" if closed_disc_sum > 0.005 else ""
        disc_note_zh = f" (另 OKB 抵扣已省 +{closed_disc_sum:.2f} U)" if closed_disc_sum > 0.005 else ""
        reconcile_streams.append(
            {
                "key": "okx_futures",
                "label": "OKX Manual Futures Net PnL",
                "label_zh": "OKX 手操合约实收净盈亏",
                "detail": f"All-time {len(trades)} closed trades book {closed_book_sum:+.2f} U + live rebates +{closed_reb_sum:.2f} U{disc_note_en}",
                "detail_zh": f"全历史 {len(trades)} 笔平仓账面 {closed_book_sum:+.2f} U + 实时返佣 +{closed_reb_sum:.2f} U{disc_note_zh}",
                "amount_usd": futures_stream_usd,
            }
        )

    copy_stream_usd = round(copy_pnl_usd, 2) if copy_platform is not None else 0.0
    if copy_platform is not None or abs(copy_stream_usd) > 0.001:
        reconcile_streams.append(
            {
                "key": "okx_copy_trading",
                "label": "OKX Copy Trading Net PnL",
                "label_zh": "OKX 实盘跟单净损益",
                "detail": f"Active copy equity {copy_equity_usd:.2f} U (invested {copy_invested_usd:.2f} U)",
                "detail_zh": f"跟单在途净值 {copy_equity_usd:.2f} U (投入本金 {copy_invested_usd:.2f} U)",
                "amount_usd": copy_stream_usd,
            }
        )

    if wallet_rebates_usd or any("okx_fee_rebates" in parts for parts in daily_comp.values()):
        reconcile_streams.append({
            "key": "okx_fee_rebates",
            "label": "OKX Funding Account Fee Rebates",
            "label_zh": "OKX 资金账户手续费返佣",
            "detail": "Fee rebates credited to the funding wallet",
            "detail_zh": "资金账户已入账的手续费返佣",
            "amount_usd": round(wallet_rebates_usd, 2),
        })
    rem_stream_usd = round(net_pnl - futures_stream_usd - copy_stream_usd - wallet_rebates_usd, 2)
    if abs(rem_stream_usd) >= 0.01 or earn_interest_usd or (not reconcile_streams and abs(net_pnl) > 0.001):
        earn_part_en = f"Earn interest +{earn_interest_usd:.2f} U · " if earn_interest_usd > 0.005 else ""
        earn_part_zh = f"理财生息 +{earn_interest_usd:.2f} U · " if earn_interest_usd > 0.005 else ""
        reconcile_streams.append(
            {
                "key": "okx_earn_spot",
                "label": "OKX Earn, Tokenized Equities & Spot Valuation",
                "label_zh": "OKX 理财生息、代币化美股与现货估值变动",
                "detail": f"{earn_part_en}Unified non-USDT & tokenized stock mark-to-market delta",
                "detail_zh": f"{earn_part_zh}统一账户非U现货与美股代币市值及体验金净变动",
                "amount_usd": rem_stream_usd,
            }
        )

    reconcile_streams = clamp_reconcile_streams_to_target(
        reconcile_streams,
        net_pnl,
        fallback_key="okx_earn_spot",
        fallback_label_zh="OKX 账户净值变动",
        fallback_detail_zh="统一账户余额与估值净变动",
    )

    stock_syms = {h["symbol"] for h in tokenized_stocks}
    wallet_balances = build_spot_wallet_balances(
        raw_spot_rows,
        venue_id="okx",
        venue_name=meta.display_name,
        color_token=meta.color_token,
        spot_prices=px_map,
        excluded_symbols=stock_syms,
    )
    stock_perps = build_venue_stock_perpetuals(
        trades,
        venue_id="okx",
        venue_name=meta.display_name,
        venue_name_zh=meta.display_name_zh,
    )

    tot_gross = sum(float(t["total_fee_usd"]) for t in trades)
    tot_disc = sum(float(t["total_discount_saved_usd"]) for t in trades)
    disc_pct = round((tot_disc / (tot_gross + tot_disc)) * 100.0, 1) if (tot_gross + tot_disc) > 0 and tot_disc > 0 else 0.0

    stk_fee_rate = (
        {
            "venue_id": "okx",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "summary": "0.1%",
            "summary_zh": "0.1%",
        }
        if tokenized_stocks
        else None
    )

    uta_total_eq = sum(float(b.get("totalEq") or 0.0) for b in (snap.get("balances") or []))
    uta_non_stable_usd = sum(
        (eq_u if eq_u > 0 else _coin_usd(ccy, q))
        for ccy, q, eq_u, acct_tp in raw_spot_rows
        if acct_tp == "UNIFIED" and ccy not in STABLECOINS and q > 0
    )
    okx_futures_equity_usd = max(0.0, uta_total_eq - uta_non_stable_usd)

    return VenueLedgerBundle(
        venue_id="okx",
        exchange_card={
            "venue_id": "okx",
            "display_name": meta.display_name,
            "display_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "connected": True,
            "equity_usd": equity_2dp,
            "net_principal_usd": principal_usd,
            "net_pnl_usd": net_pnl,
            "futures_equity_usd": round(okx_futures_equity_usd, 2),
            "available_margin_usd": round(available_usd, 2),
            "trade_count": len(trades),
            "rebate_rate_pct": round(float(snap.get("observed_rebate_ratio") or 0.0) * 100.0, 1),
            "discount_rate_pct": disc_pct,
            "capabilities": meta.capabilities.model_dump(),
        },
        trades=trades,
        open_positions=open_positions,
        spot_holdings=spot_holdings_from_wallets(wallet_balances),
        spot_wallet_balances=wallet_balances,
        reconcile_streams=reconcile_streams,
        daily_pnl={day: sum(parts.values(), Decimal("0")) for day, parts in daily_comp.items()},
        daily_components={d: dict(c) for d, c in daily_comp.items()},
        copy_trading=VenueCopyBundle(
            has_copy_trading=copy_platform is not None,
            invested_usd=copy_invested_usd,
            active_equity_usd=copy_equity_usd,
            book_pnl_usd=copy_pnl_usd,
            rebate_usd=0.0,
            combined_pnl_usd=copy_pnl_usd,
            platform=copy_platform,
        ),
        stocks=VenueStockBundle(
            equity_usd=round(sum(float(h["market_value_usd"]) for h in tokenized_stocks), 2),
            unrealized_pnl_usd=round(sum(float(h["pnl_usd"]) for h in tokenized_stocks), 2),
            maker_fee_pct=0.1 if tokenized_stocks else 0.0,
            taker_fee_pct=0.1 if tokenized_stocks else 0.0,
            venue_fee_rate=stk_fee_rate,
            holdings=tokenized_stocks,
            perpetuals=stock_perps,
        ),
        fee_context={
            "maker_bps": round(maker_bps, 2),
            "taker_bps": round(taker_bps, 2),
            "maker_bps_configured": maker_quote not in (None, ""),
            "taker_bps_configured": taker_quote not in (None, ""),
        },
    )


def build_okx_fee_matrix_row(
    bundle: VenueLedgerBundle,
    range_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    m_bps_cfg = float(bundle.fee_context.get("maker_bps") or 0.0)
    t_bps_cfg = float(bundle.fee_context.get("taker_bps") or 0.0)
    if not range_trades and not (bundle.fee_context.get("maker_bps_configured") or bundle.fee_context.get("taker_bps_configured")):
        return None
    ex = bundle.exchange_card
    v_gross = round(sum(float(t["total_fee_usd"]) for t in range_trades), 2)
    v_reb = round(sum(float(t["total_rebate_usd"]) for t in range_trades), 2)
    v_disc = round(sum(float(t["total_discount_saved_usd"]) for t in range_trades), 2)
    v_net = round(sum(float(t["net_fee_usd"]) for t in range_trades), 2)
    v_reb_pct = round((v_reb / v_gross) * 100.0, 1) if v_gross > 0 else float(ex["rebate_rate_pct"])
    v_base_fee = v_gross + v_disc
    v_relief_pct = round(((v_reb + v_disc) / v_base_fee) * 100.0, 1) if v_base_fee > 0 else 0.0

    legs = [trade[key] for trade in range_trades for key in ("entry_leg", "exit_leg")]
    # Unknown fills contribute to totals but never lend their fee to another fill's denominator.
    def observed(role: str, configured: float) -> tuple[float, float, float | None, float]:
        samples = [leg["role_samples"][role] for leg in legs if role in leg.get("role_samples", {})]
        notional = sum(s["notional"] for s in samples)
        if not notional:
            return 0.0, configured, None, 0.0
        net = sum(s["net"] for s in samples)
        return notional, round(sum(s["paid"] for s in samples) / notional * 10000, 2), round(net / notional * 10000, 2), net

    m_not, m_gross_bps, m_net_bps, _ = observed("MAKER", m_bps_cfg)
    t_not, t_gross_bps, t_net_bps, t_net_sum = observed("TAKER", t_bps_cfg)
    tot_not = sum(leg["notional_usd"] for leg in legs) if legs and all(leg.get("notional_usd") and leg["fee_known"] for leg in legs) else 0.0
    # Position-level totals can include fills missing from the retained history.
    comparable = all(abs(float(t["net_fee_usd"]) - t["entry_leg"]["net_fee_usd"] - t["exit_leg"]["net_fee_usd"]) < 0.0001
                     for t in range_trades)
    maker_sav = round(max(0.0, t_net_sum - t_not * (m_net_bps / 10_000.0)), 2) if t_not > 0 and m_net_bps is not None else 0.0

    return {
        "venue_id": "okx",
        "display_name": ex["display_name"],
        "display_name_zh": ex["display_name_zh"],
        "color_token": ex["color_token"],
        "trade_count": len(range_trades),
        "maker_gross_bps": m_gross_bps,
        "maker_net_bps": m_net_bps,
        "taker_gross_bps": t_gross_bps,
        "taker_net_bps": t_net_bps,
        "maker_bps_known": m_not > 0,
        "maker_rate_source": "observed" if m_not > 0 else ("configured" if bundle.fee_context.get("maker_bps_configured") else "unknown"),
        "taker_bps_known": t_not > 0,
        "taker_rate_source": "observed" if t_not > 0 else ("configured" if bundle.fee_context.get("taker_bps_configured") else "unknown"),
        "blended_gross_bps": round((v_gross / tot_not) * 10_000, 2) if tot_not > 0 and comparable else None,
        "blended_net_bps": round((v_net / tot_not) * 10_000, 2) if tot_not > 0 and comparable else None,
        "gross_fee_usd": v_gross,
        "discount_saved_usd": v_disc,
        "discount_label": "OKB" if v_disc > 0 else "",
        "rebate_usd": v_reb,
        "rebate_pct": v_reb_pct,
        "net_fee_usd": v_net,
        "combined_relief_pct": v_relief_pct,
        "potential_maker_savings_usd": maker_sav,
    }
