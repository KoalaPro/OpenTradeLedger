"""Independent actuarial accounting engine for Bybit v5 (`venue_id = 'bybit'`)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from opentradeledger.core.open_reductions import open_reduction_key
from opentradeledger.core.position_lifecycle import trade_daily_amounts
from opentradeledger.adapters.base import (
    AdapterMeta,
    VenueCopyBundle,
    VenueLedgerBundle,
    VenueStockBundle,
)
from opentradeledger.adapters.bybit.client import normalize_bybit_closed_trades
from opentradeledger.adapters.bybit.client import validate_snapshot_funding
from opentradeledger.adapters.stock_channels import classify_spot_stock_token
from opentradeledger.core.venue_primitives import (
    STABLECOINS,
    build_spot_wallet_balances,
    spot_holdings_from_wallets,
    build_venue_stock_perpetuals,
    clamp_reconcile_streams_to_target,
    dec,
    fmt_ms,
    merge_legacy_journal_entries,
)

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore


def extract_bybit_spot_prices(store: "LedgerStore") -> dict[str, float]:
    snap = store.venue_scope("bybit").get_kv("snapshot")
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


def build_bybit_venue_ledger(
    store: "LedgerStore",
    meta: AdapterMeta,
    *,
    journals: Mapping[str, Mapping[str, Any]],
    now_s: float,
    global_spot_prices: Mapping[str, float] | None = None,
) -> VenueLedgerBundle | None:
    snap = store.venue_scope("bybit").get_kv("snapshot")
    if not isinstance(snap, dict):
        return None

    validate_snapshot_funding(snap)
    px_map = {**(global_spot_prices or {}), **extract_bybit_spot_prices(store)}

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

    wallet = snap.get("wallet") or {}
    for acct in wallet.get("list") or []:
        equity_usd += float(acct.get("totalEquity") or 0.0)
        available_usd += float(acct.get("totalAvailableBalance") or 0.0)
        for c in acct.get("coin") or []:
            ccy = str(c.get("coin") or "").strip().upper()
            q = float(c.get("walletBalance") or 0.0)
            usd = float(c.get("usdValue") or 0.0)
            raw_spot_rows.append((ccy, q, usd, "UNIFIED"))
            t_info = classify_spot_stock_token(ccy, exchange="BYBIT")
            if t_info is not None:
                mv_stk = usd or _coin_usd(ccy, q)
                if q > 0 and mv_stk >= 0.5:
                    cur_px = mv_stk / q
                    avg_px = float(c.get("avgPrice") or cur_px)
                    pnl_stk = round(float(c.get("unrealisedPnl") or ((cur_px - avg_px) * q)), 2)
                    pnl_pct = round(((cur_px - avg_px) / avg_px) * 100.0, 2) if avg_px > 0 else 0.0
                    tokenized_stocks.append(
                        {
                            "venue_id": "bybit",
                            "venue_name": meta.display_name,
                            "venue_name_zh": meta.display_name_zh,
                            "symbol": ccy,
                            "name": t_info["name"],
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

    funding = snap.get("funding") or {}
    for coin in funding.get("balance") or []:
        ccy_f = str(coin.get("coin") or "").strip().upper()
        q_f = float(coin.get("walletBalance") or 0.0)
        f_usd = float(coin.get("usdValue") or 0.0)
        equity_usd += f_usd if f_usd > 0 else _coin_usd(ccy_f, q_f)
        raw_spot_rows.append((ccy_f, q_f, f_usd, "FUNDING"))

    def _status_ok(row: Mapping[str, Any], allowed: set[str]) -> bool:
        raw_st = row.get("status")
        if raw_st is None:
            return True
        st = str(raw_st).strip().lower()
        if not st:
            return True
        return st in allowed

    dep = sum(
        _coin_usd(str(x.get("coin") or ""), float(x.get("amount") or 0.0))
        for x in (snap.get("deposits") or [])
        if _status_ok(x, {"3", "10012", "success", "completed"})
    )
    int_dep = sum(
        _coin_usd(str(x.get("coin") or ""), float(x.get("amount") or 0.0))
        for x in (snap.get("internal_deposits") or [])
        if _status_ok(x, {"2", "success", "completed"})
    )
    wd = sum(
        _coin_usd(
            str(x.get("coin") or ""),
            float(x.get("amount") or 0.0) + max(0.0, float(x.get("withdrawFee") or 0.0)),
        )
        for x in (snap.get("withdrawals") or [])
        if _status_ok(x, {"success", "blockchainconfirmed", "completed"})
    )
    principal_usd = round(dep + int_dep - wd, 2)

    quote = (snap.get("fee_rates") or [{}])[0]
    maker_quote = quote.get("makerFeeRate")
    taker_quote = quote.get("takerFeeRate")
    maker_bps = abs(float(maker_quote or 0.0)) * 10_000
    taker_bps = abs(float(taker_quote or 0.0)) * 10_000

    open_positions: list[dict[str, Any]] = []
    for pos in snap.get("positions") or []:
        qty = abs(float(pos.get("size") or 0.0))
        if qty <= 0:
            continue
        side = "LONG" if str(pos.get("side") or "").lower() == "buy" else "SHORT"
        sym = str(pos.get("symbol") or "").upper()
        if "_" not in sym and sym.endswith("USDT"):
            sym = f"{sym[:-4]}_USDT"
        open_positions.append(
            {
                "venue_id": "bybit",
                "venue_name": meta.display_name,
                "color_token": meta.color_token,
                "symbol": sym,
                "side": side,
                "leverage": float(pos.get("leverage") or 1.0),
                "qty": qty,
                "notional_usd": round(abs(float(pos.get("positionValue") or 0.0)), 2),
                "entry_price": float(pos.get("avgPrice") or 0.0),
                "mark_price": float(pos.get("markPrice") or 0.0),
                "liq_price": float(pos.get("liqPrice") or 0.0),
                "unrealized_pnl_usd": round(float(pos.get("unrealisedPnl") or 0.0), 2),
                "unrealized_roi_pct": 0.0,
                "has_stop_order": bool(pos.get("stopLoss")),
            }
        )

    raw_trades = normalize_bybit_closed_trades(
        snap.get("closed_pnl") or [],
        snap.get("executions") or [],
        tx_logs=snap.get("tx_logs") or [],
        positions=snap.get("positions") or [],
        rebate_ratio=float(snap.get("observed_rebate_ratio") or 0.0),
    )
    trades: list[dict[str, Any]] = []
    daily_pnl: dict[str, Decimal] = defaultdict(Decimal)
    daily_comp: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))

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
        aliases = [*rt.pop("_journal_keys", ()), open_reduction_key("bybit", rt["symbol"], rt["side"], rt.get("position_events") or [])]
        j = merge_legacy_journal_entries(journals, rt["trade_id"], aliases)
        rt["tags"] = list(j.get("tags") or [])
        rt["note"] = str(j.get("note") or "")
        trades.append(rt)
        for day, amount in trade_daily_amounts(rt, dec(rt["net_pnl_usd"])).items():
            daily_pnl[day] += amount
            daily_comp[day]["bybit_futures"] += amount

    equity_2dp = round(equity_usd, 2)
    net_pnl = round(equity_2dp - principal_usd, 2) if principal_usd != 0 else round(sum(t["net_pnl_usd"] for t in trades), 2)

    open_upl_sum = sum(float(p["unrealized_pnl_usd"]) for p in open_positions)
    closed_net_sum = sum(float(t["net_pnl_usd"]) for t in trades)
    closed_book_sum = sum(float(t["book_pnl_usd"]) for t in trades)
    closed_reb_sum = sum(float(t["total_rebate_usd"]) for t in trades)
    closed_disc_sum = sum(float(t["total_discount_saved_usd"]) for t in trades)
    futures_stream_usd = round(closed_net_sum + open_upl_sum, 2)

    reconcile_streams: list[dict[str, Any]] = []
    if trades or open_positions or abs(futures_stream_usd) > 0.001:
        disc_note_en = f" (MNT saved +{closed_disc_sum:.2f} U)" if closed_disc_sum > 0.005 else ""
        disc_note_zh = f" (另 MNT 抵扣已省 +{closed_disc_sum:.2f} U)" if closed_disc_sum > 0.005 else ""
        reconcile_streams.append(
            {
                "key": "bybit_futures",
                "label": "Bybit Manual Futures Net PnL",
                "label_zh": "Bybit 手操合约实收净盈亏",
                "detail": f"All-time {len(trades)} closed trades book {closed_book_sum:+.2f} U + live rebates +{closed_reb_sum:.2f} U{disc_note_en}",
                "detail_zh": f"全历史 {len(trades)} 笔平仓账面 {closed_book_sum:+.2f} U + 实时返佣 +{closed_reb_sum:.2f} U{disc_note_zh}",
                "amount_usd": futures_stream_usd,
            }
        )

    rem_stream_usd = round(net_pnl - futures_stream_usd, 2)
    if abs(rem_stream_usd) >= 0.01 or (not reconcile_streams and abs(net_pnl) > 0.001):
        reconcile_streams.append(
            {
                "key": "bybit_earn_spot",
                "label": "Bybit Earn, Tokenized Equities & Spot Valuation",
                "label_zh": "Bybit 理财生息、代币化美股与现货估值变动",
                "detail": "Unified non-USDT & tokenized stock mark-to-market delta",
                "detail_zh": "统一账户非U现货与美股代币市值及体验金净变动",
                "amount_usd": rem_stream_usd,
            }
        )

    reconcile_streams = clamp_reconcile_streams_to_target(
        reconcile_streams,
        net_pnl,
        fallback_key="bybit_earn_spot",
        fallback_label_zh="Bybit 账户净值变动",
        fallback_detail_zh="统一账户余额与估值净变动",
    )

    stock_syms = {h["symbol"] for h in tokenized_stocks}
    wallet_balances = build_spot_wallet_balances(
        raw_spot_rows,
        venue_id="bybit",
        venue_name=meta.display_name,
        color_token=meta.color_token,
        spot_prices=px_map,
        excluded_symbols=stock_syms,
    )
    stock_perps = build_venue_stock_perpetuals(
        trades,
        venue_id="bybit",
        venue_name=meta.display_name,
        venue_name_zh=meta.display_name_zh,
    )

    tot_gross = sum(float(t["total_fee_usd"]) for t in trades)
    tot_disc = sum(float(t["total_discount_saved_usd"]) for t in trades)
    disc_pct = round((tot_disc / (tot_gross + tot_disc)) * 100.0, 1) if (tot_gross + tot_disc) > 0 and tot_disc > 0 else 0.0

    stk_fee_rate = (
        {
            "venue_id": "bybit",
            "venue_name": meta.display_name,
            "venue_name_zh": meta.display_name_zh,
            "summary": "0.1%",
            "summary_zh": "0.1%",
        }
        if tokenized_stocks
        else None
    )

    uta_total_eq = sum(float(acct.get("totalEquity") or 0.0) for acct in (wallet.get("list") or []))
    uta_non_stable_usd = sum(
        (usd if usd > 0 else _coin_usd(ccy, q))
        for ccy, q, usd, acct_tp in raw_spot_rows
        if acct_tp == "UNIFIED" and ccy not in STABLECOINS and q > 0
    )
    bybit_futures_equity_usd = max(0.0, uta_total_eq - uta_non_stable_usd)

    return VenueLedgerBundle(
        venue_id="bybit",
        exchange_card={
            "venue_id": "bybit",
            "display_name": meta.display_name,
            "display_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "connected": True,
            "equity_usd": equity_2dp,
            "net_principal_usd": principal_usd,
            "net_pnl_usd": net_pnl,
            "futures_equity_usd": round(bybit_futures_equity_usd, 2),
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
        daily_pnl=dict(daily_pnl),
        daily_components={d: dict(c) for d, c in daily_comp.items()},
        copy_trading=VenueCopyBundle(),
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


def build_bybit_fee_matrix_row(
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

    m_legs = [t[lk] for t in range_trades for lk in ("entry_leg", "exit_leg") if t[lk].get("fee_known", True) and t[lk]["role"] == "MAKER"]
    t_legs = [t[lk] for t in range_trades for lk in ("entry_leg", "exit_leg") if t[lk].get("fee_known", True) and t[lk]["role"] == "TAKER"]
    tot_not = sum(float(t[lk]["notional_usd"]) for t in range_trades for lk in ("entry_leg", "exit_leg"))
    m_not = sum(float(l["notional_usd"]) for l in m_legs)
    t_not = sum(float(l["notional_usd"]) for l in t_legs)
    m_gross_bps = round((sum(float(l["gross_fee_usd"]) for l in m_legs) / m_not) * 10_000, 2) if m_not > 0 else m_bps_cfg
    m_net_bps = round((sum(float(l["net_fee_usd"]) for l in m_legs) / m_not) * 10_000, 2) if m_not > 0 else None
    t_gross_bps = round((sum(float(l["gross_fee_usd"]) for l in t_legs) / t_not) * 10_000, 2) if t_not > 0 else t_bps_cfg
    t_net_bps = round((sum(float(l["net_fee_usd"]) for l in t_legs) / t_not) * 10_000, 2) if t_not > 0 else None
    t_net_sum = sum(float(l["net_fee_usd"]) for l in t_legs)
    maker_sav = round(max(0.0, t_net_sum - t_not * (m_net_bps / 10_000.0)), 2) if t_not > 0 and m_net_bps is not None else 0.0

    return {
        "venue_id": "bybit",
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
        "blended_gross_bps": round((v_gross / tot_not) * 10_000, 2) if tot_not > 0 else 0.0,
        "blended_net_bps": round((v_net / tot_not) * 10_000, 2) if tot_not > 0 else 0.0,
        "gross_fee_usd": v_gross,
        "discount_saved_usd": v_disc,
        "discount_label": "MNT" if v_disc > 0 else "",
        "rebate_usd": v_reb,
        "rebate_pct": v_reb_pct,
        "net_fee_usd": v_net,
        "combined_relief_pct": v_relief_pct,
        "potential_maker_savings_usd": maker_sav,
    }
