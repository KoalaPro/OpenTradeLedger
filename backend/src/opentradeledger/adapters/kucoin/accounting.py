"""Independent actuarial accounting engine for KuCoin (`venue_id = 'kucoin'`)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from opentradeledger.adapters.base import (
    AdapterMeta,
    VenueCopyBundle,
    VenueLedgerBundle,
    VenueStockBundle,
)
from opentradeledger.adapters.kucoin.client import normalize_kucoin_closed_trades, normalize_kucoin_symbol
from opentradeledger.core.venue_primitives import (
    STABLECOINS,
    build_spot_wallet_balances,
    build_venue_stock_perpetuals,
    clamp_reconcile_streams_to_target,
    dec,
    fmt_ms,
    spot_holdings_from_wallets,
)

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore


def extract_kucoin_spot_prices(store: "LedgerStore") -> dict[str, float]:
    snap = store.venue_scope("kucoin").get_kv("snapshot")
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


def build_kucoin_venue_ledger(
    store: "LedgerStore",
    meta: AdapterMeta,
    *,
    journals: Mapping[str, Mapping[str, Any]],
    now_s: float,
    global_spot_prices: Mapping[str, float] | None = None,
) -> VenueLedgerBundle | None:
    _ = now_s
    snap = store.venue_scope("kucoin").get_kv("snapshot")
    if not isinstance(snap, dict):
        return None

    px_map = {**(global_spot_prices or {}), **extract_kucoin_spot_prices(store)}

    def _coin_usd(ccy_raw: str, amt: float) -> float:
        c = (ccy_raw or "").strip().upper()
        if not c or amt == 0:
            return 0.0
        if c in STABLECOINS:
            return amt
        return amt * float(px_map.get(c, 0.0))

    fut_ov = snap.get("futures_overview") if isinstance(snap.get("futures_overview"), dict) else {}
    fut_equity = float(fut_ov.get("accountEquity") or 0.0)
    avail_margin = float(fut_ov.get("availableBalance") or fut_equity)

    raw_spot_rows: list[tuple[str, float, float, str]] = []
    spot_equity = 0.0
    for acct in snap.get("spot_accounts") or []:
        if not isinstance(acct, dict):
            continue
        acct_tp = str(acct.get("type") or "trade").upper()
        if acct_tp in {"CONTRACT", "FUTURES"}:
            continue
        ccy = str(acct.get("currency") or "").strip().upper()
        bal = float(acct.get("balance") or 0.0)
        if not ccy or bal <= 0:
            continue
        u_val = _coin_usd(ccy, bal)
        spot_equity += u_val
        raw_spot_rows.append((ccy, bal, u_val, acct_tp))

    if fut_equity > 0:
        raw_spot_rows.append(("USDT", fut_equity, fut_equity, "FUTURES"))

    equity_2dp = round(fut_equity + spot_equity, 2)
    dep = sum(_coin_usd(str(x.get("currency") or ""), float(x.get("amount") or 0.0)) for x in (snap.get("deposits") or []))
    wd = sum(
        _coin_usd(str(x.get("currency") or ""), float(x.get("amount") or 0.0) + float(x.get("fee") or 0.0))
        for x in (snap.get("withdrawals") or [])
    )
    principal_usd = round(dep - wd, 2)

    open_positions: list[dict[str, Any]] = []
    for pos in snap.get("positions") or []:
        if not isinstance(pos, dict) or not pos.get("isOpen", True):
            continue
        raw_qty = float(pos.get("currentQty") or 0.0)
        if abs(raw_qty) <= 0:
            continue
        mult = abs(float(pos.get("multiplier") or 1.0))
        qty = abs(raw_qty) * mult
        sym = normalize_kucoin_symbol(str(pos.get("symbol") or ""))
        upnl = round(float(pos.get("unrealisedPnl") or 0.0), 2)
        notional = abs(float(pos.get("markValue") or (qty * float(pos.get("markPrice") or 0.0))))
        lev = float(pos.get("realLeverage") or pos.get("leverage") or 1.0)
        open_positions.append(
            {
                "venue_id": "kucoin",
                "venue_name": meta.display_name,
                "color_token": meta.color_token,
                "symbol": sym,
                "side": "LONG" if raw_qty > 0 else "SHORT",
                "leverage": max(1.0, round(lev, 2)),
                "qty": qty,
                "contract_count": abs(raw_qty) if mult != 1.0 else None,
                "notional_usd": round(notional, 2),
                "entry_price": float(pos.get("avgEntryPrice") or 0.0),
                "mark_price": float(pos.get("markPrice") or 0.0),
                "liq_price": float(pos.get("liquidationPrice") or 0.0),
                "unrealized_pnl_usd": upnl,
                "unrealized_roi_pct": round(float(pos.get("unrealisedRoePcnt") or 0.0) * 100.0, 2),
                "has_stop_order": False,
            }
        )

    raw_trades = normalize_kucoin_closed_trades(
        snap.get("history_positions") or [],
        snap.get("fills") or [],
        snap.get("tx_history") or [],
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
        rt["exit_leg"]["time_str"] = fmt_ms(rt["exit_leg"]["time_ms"], "%m-%d %H:%M")
        j = journals.get(rt["trade_id"]) or {}
        rt["tags"] = list(j.get("tags") or [])
        rt["note"] = str(j.get("note") or "")
        trades.append(rt)
        if rt["close_date"]:
            v_dec = dec(rt["net_pnl_usd"])
            daily_pnl[rt["close_date"]] += v_dec
            daily_comp[rt["close_date"]]["kucoin_futures"] += v_dec

    open_upl_sum = sum(float(p["unrealized_pnl_usd"]) for p in open_positions)
    closed_net_sum = sum(float(t["net_pnl_usd"]) for t in trades)
    closed_book_sum = sum(float(t["book_pnl_usd"]) for t in trades)
    closed_reb_sum = sum(float(t["total_rebate_usd"]) for t in trades)
    futures_stream_usd = round(closed_net_sum + open_upl_sum, 2)
    net_pnl = round(equity_2dp - principal_usd, 2) if principal_usd != 0 else futures_stream_usd

    reconcile_streams: list[dict[str, Any]] = []
    if trades or open_positions or abs(futures_stream_usd) > 0.001:
        reconcile_streams.append(
            {
                "key": "kucoin_futures",
                "label": "KuCoin Futures Net PnL",
                "label_zh": "KuCoin 合约实收净盈亏",
                "detail": f"All-time {len(trades)} closed trades book {closed_book_sum:+.2f} U + live rebates +{closed_reb_sum:.2f} U",
                "detail_zh": f"全历史 {len(trades)} 笔平仓账面 {closed_book_sum:+.2f} U + 实时返佣 +{closed_reb_sum:.2f} U",
                "amount_usd": futures_stream_usd,
            }
        )

    rem_stream_usd = round(net_pnl - futures_stream_usd, 2)
    if abs(rem_stream_usd) >= 0.01 or (not reconcile_streams and abs(net_pnl) > 0.001):
        reconcile_streams.append(
            {
                "key": "kucoin_earn_spot",
                "label": "KuCoin Spot & Margin Valuation",
                "label_zh": "KuCoin 现货、理财与估值变动",
                "detail": "Spot & main account non-USDT mark-to-market delta",
                "detail_zh": "储蓄与币币账户现货估值及理财净变动",
                "amount_usd": rem_stream_usd,
            }
        )

    reconcile_streams = clamp_reconcile_streams_to_target(
        reconcile_streams,
        net_pnl,
        fallback_key="kucoin_earn_spot",
        fallback_label_zh="KuCoin 账户净值变动",
        fallback_detail_zh="现货与合约账户余额估值净变动",
    )

    wallet_balances = build_spot_wallet_balances(
        raw_spot_rows,
        venue_id="kucoin",
        venue_name=meta.display_name,
        color_token=meta.color_token,
        spot_prices=px_map,
        excluded_symbols=set(),
    )
    stock_perps = build_venue_stock_perpetuals(
        trades,
        venue_id="kucoin",
        venue_name=meta.display_name,
        venue_name_zh=meta.display_name_zh,
    )
    base_fee = snap.get("base_fee") if isinstance(snap.get("base_fee"), dict) else {}
    m_cfg = abs(float(base_fee.get("makerFeeRate") or 0.0002)) * 10_000
    t_cfg = abs(float(base_fee.get("takerFeeRate") or 0.0006)) * 10_000
    tot_gross = sum(float(t["total_fee_usd"]) for t in trades)
    obs_reb_pct = round((closed_reb_sum / tot_gross) * 100.0, 1) if tot_gross > 0 else 0.0

    return VenueLedgerBundle(
        venue_id="kucoin",
        exchange_card={
            "venue_id": "kucoin",
            "display_name": meta.display_name,
            "display_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "connected": True,
            "equity_usd": equity_2dp,
            "net_principal_usd": principal_usd,
            "net_pnl_usd": net_pnl,
            "futures_equity_usd": round(fut_equity, 2),
            "available_margin_usd": round(avail_margin, 2),
            "trade_count": len(trades),
            "rebate_rate_pct": obs_reb_pct,
            "discount_rate_pct": 0.0,
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
        stocks=VenueStockBundle(perpetuals=stock_perps),
        fee_context={
            "maker_bps": round(m_cfg, 2),
            "taker_bps": round(t_cfg, 2),
            "maker_bps_configured": bool(base_fee),
            "taker_bps_configured": bool(base_fee),
        },
    )


def build_kucoin_fee_matrix_row(
    bundle: VenueLedgerBundle,
    range_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not range_trades and not bundle.fee_context.get("maker_bps_configured"):
        return None
    ex = bundle.exchange_card
    v_gross = round(sum(float(t["total_fee_usd"]) for t in range_trades), 2)
    v_reb = round(sum(float(t["total_rebate_usd"]) for t in range_trades), 2)
    v_net = round(sum(float(t["net_fee_usd"]) for t in range_trades), 2)
    v_reb_pct = round((v_reb / v_gross) * 100.0, 1) if v_gross > 0 else float(ex["rebate_rate_pct"])

    m_legs = [t[lk] for t in range_trades for lk in ("entry_leg", "exit_leg") if t[lk].get("fee_known", True) and t[lk]["role"] == "MAKER"]
    t_legs = [t[lk] for t in range_trades for lk in ("entry_leg", "exit_leg") if t[lk].get("fee_known", True) and t[lk]["role"] == "TAKER"]
    tot_not = sum(float(t[lk]["notional_usd"] or 0.0) for t in range_trades for lk in ("entry_leg", "exit_leg"))
    m_not = sum(float(l["notional_usd"] or 0.0) for l in m_legs)
    t_not = sum(float(l["notional_usd"] or 0.0) for l in t_legs)
    m_gross_bps = round((sum(float(l["gross_fee_usd"]) for l in m_legs) / m_not) * 10_000, 2) if m_not > 0 else float(bundle.fee_context.get("maker_bps") or 2.0)
    m_net_bps = round((sum(float(l["net_fee_usd"]) for l in m_legs) / m_not) * 10_000, 2) if m_not > 0 else None
    t_gross_bps = round((sum(float(l["gross_fee_usd"]) for l in t_legs) / t_not) * 10_000, 2) if t_not > 0 else float(bundle.fee_context.get("taker_bps") or 6.0)
    t_net_bps = round((sum(float(l["net_fee_usd"]) for l in t_legs) / t_not) * 10_000, 2) if t_not > 0 else None
    t_net_sum = sum(float(l["net_fee_usd"]) for l in t_legs)
    maker_sav = round(max(0.0, t_net_sum - t_not * ((m_net_bps or m_gross_bps) / 10_000.0)), 2) if t_not > 0 else 0.0

    return {
        "venue_id": "kucoin",
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
        "discount_saved_usd": 0.0,
        "discount_label": "",
        "rebate_usd": v_reb,
        "rebate_pct": v_reb_pct,
        "net_fee_usd": v_net,
        "combined_relief_pct": v_reb_pct,
        "potential_maker_savings_usd": maker_sav,
    }
