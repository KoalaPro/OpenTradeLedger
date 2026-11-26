"""Independent actuarial accounting engine for BingX (`venue_id = 'bingx'`)."""

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
from opentradeledger.adapters.bingx.client import (
    bingx_spot_quantity,
    normalize_bingx_closed_trades,
    normalize_bingx_symbol,
)
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


def _is_completed_bingx_deposit(row: Mapping[str, Any]) -> bool:
    st = row.get("status")
    if st is None or str(st).strip() == "":
        return True
    norm = str(st).strip().lower()
    return norm in {"6", "completed", "success", "credited"}


def _is_completed_bingx_withdrawal(row: Mapping[str, Any]) -> bool:
    st = row.get("status")
    if st is None or str(st).strip() == "":
        return True
    norm = str(st).strip().lower()
    return norm in {"6", "completed", "success"}


def extract_bingx_spot_prices(store: "LedgerStore") -> dict[str, float]:
    snap = store.venue_scope("bingx").get_kv("snapshot")
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


def build_bingx_venue_ledger(
    store: "LedgerStore",
    meta: AdapterMeta,
    *,
    journals: Mapping[str, Mapping[str, Any]],
    now_s: float,
    global_spot_prices: Mapping[str, float] | None = None,
) -> VenueLedgerBundle | None:
    _ = now_s
    snap = store.venue_scope("bingx").get_kv("snapshot")
    if not isinstance(snap, dict):
        return None

    px_map = {**(global_spot_prices or {}), **extract_bingx_spot_prices(store)}

    def _coin_usd(ccy_raw: str, amt: float) -> float:
        c = (ccy_raw or "").strip().upper()
        if not c or amt == 0:
            return 0.0
        if c in STABLECOINS:
            return amt
        return amt * float(px_map.get(c, 0.0))

    fut_equity = 0.0
    avail_margin = 0.0
    raw_spot_rows: list[tuple[str, float, float, str]] = []
    for bal in snap.get("swap_balances") or []:
        if not isinstance(bal, dict):
            continue
        asset = str(bal.get("asset") or "USDT").strip().upper()
        eq = float(bal["equity"] if bal.get("equity") is not None else bal.get("balance") or 0.0)
        av = float(bal["availableMargin"] if bal.get("availableMargin") is not None else eq)
        u_eq = _coin_usd(asset, eq)
        fut_equity += u_eq
        avail_margin += _coin_usd(asset, av)
        if eq > 0:
            raw_spot_rows.append((asset, eq, u_eq, "SWAP"))

    spot_equity = 0.0
    for sb in snap.get("spot_balances") or []:
        if not isinstance(sb, dict):
            continue
        ccy = str(sb.get("asset") or "").strip().upper()
        qty = bingx_spot_quantity(sb)
        if not ccy or qty <= 0:
            continue
        u_val = _coin_usd(ccy, qty)
        spot_equity += u_val
        raw_spot_rows.append((ccy, qty, u_val, "SPOT"))

    equity_2dp = round(fut_equity + spot_equity, 2)
    dep = sum(
        _coin_usd(str(x.get("coin") or ""), float(x.get("amount") or 0.0))
        for x in (snap.get("deposits") or [])
        if isinstance(x, dict) and _is_completed_bingx_deposit(x)
    )
    wd = sum(
        _coin_usd(str(x.get("coin") or ""), float(x.get("amount") or 0.0) + float(x.get("transactionFee") or x.get("fee") or 0.0))
        for x in (snap.get("withdrawals") or [])
        if isinstance(x, dict) and _is_completed_bingx_withdrawal(x)
    )
    principal_usd = round(dep - wd, 2)

    open_positions: list[dict[str, Any]] = []
    for pos in snap.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        raw_amt = float(pos.get("positionAmt") or pos.get("availableAmt") or 0.0)
        qty = abs(raw_amt)
        if qty <= 0:
            continue
        p_side = str(pos.get("positionSide") or "").upper()
        side = p_side if p_side in {"LONG", "SHORT"} else ("LONG" if raw_amt > 0 else "SHORT")
        sym = normalize_bingx_symbol(str(pos.get("symbol") or ""))
        mark_px = float(pos.get("markPrice") or pos.get("avgPrice") or 0.0)
        notional = abs(float(pos.get("positionValue") or (qty * mark_px)))
        open_positions.append(
            {
                "venue_id": "bingx",
                "venue_name": meta.display_name,
                "color_token": meta.color_token,
                "symbol": sym,
                "side": side,
                "leverage": float(pos.get("leverage") or 1.0),
                "qty": qty,
                "notional_usd": round(notional, 2),
                "entry_price": float(pos.get("avgPrice") or 0.0),
                "mark_price": mark_px,
                "liq_price": float(pos.get("liquidationPrice") or 0.0),
                "unrealized_pnl_usd": round(float(pos.get("unrealizedProfit") or 0.0), 2),
                "unrealized_roi_pct": round(float(pos.get("pnlRatio") or 0.0) * 100.0, 2),
                "has_stop_order": False,
            }
        )

    raw_trades = normalize_bingx_closed_trades(
        snap.get("fills") or [],
        snap.get("income") or [],
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
            daily_comp[rt["close_date"]]["bingx_futures"] += v_dec

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
                "key": "bingx_futures",
                "label": "BingX Perpetual Net PnL",
                "label_zh": "BingX 永续合约实收净盈亏",
                "detail": f"All-time {len(trades)} closed trades book {closed_book_sum:+.2f} U + live rebates +{closed_reb_sum:.2f} U",
                "detail_zh": f"全历史 {len(trades)} 笔平仓账面 {closed_book_sum:+.2f} U + 实时返佣 +{closed_reb_sum:.2f} U",
                "amount_usd": futures_stream_usd,
            }
        )

    rem_stream_usd = round(net_pnl - futures_stream_usd, 2)
    if abs(rem_stream_usd) >= 0.01 or (not reconcile_streams and abs(net_pnl) > 0.001):
        reconcile_streams.append(
            {
                "key": "bingx_earn_spot",
                "label": "BingX Spot & Wealth Valuation",
                "label_zh": "BingX 现货、跟单与理财估值变动",
                "detail": "Spot, wealth & copy sub-pool valuation delta",
                "detail_zh": "现货、理财及跟单子仓估值净变动",
                "amount_usd": rem_stream_usd,
            }
        )

    reconcile_streams = clamp_reconcile_streams_to_target(
        reconcile_streams,
        net_pnl,
        fallback_key="bingx_earn_spot",
        fallback_label_zh="BingX 账户净值变动",
        fallback_detail_zh="永续与现货账户余额估值净变动",
    )

    wallet_balances = build_spot_wallet_balances(
        raw_spot_rows,
        venue_id="bingx",
        venue_name=meta.display_name,
        color_token=meta.color_token,
        spot_prices=px_map,
        excluded_symbols=set(),
    )
    stock_perps = build_venue_stock_perpetuals(
        trades,
        venue_id="bingx",
        venue_name=meta.display_name,
        venue_name_zh=meta.display_name_zh,
    )
    comm = snap.get("commission") if isinstance(snap.get("commission"), dict) else {}
    m_cfg = abs(float(comm.get("makerCommissionRate") or 0.0002)) * 10_000
    t_cfg = abs(float(comm.get("takerCommissionRate") or 0.0005)) * 10_000
    tot_gross = sum(float(t["total_fee_usd"]) for t in trades)
    obs_reb_pct = round((closed_reb_sum / tot_gross) * 100.0, 1) if tot_gross > 0 else 0.0

    return VenueLedgerBundle(
        venue_id="bingx",
        exchange_card={
            "venue_id": "bingx",
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
            "maker_bps_configured": bool(comm),
            "taker_bps_configured": bool(comm),
        },
    )


def build_bingx_fee_matrix_row(
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
    t_gross_bps = round((sum(float(l["gross_fee_usd"]) for l in t_legs) / t_not) * 10_000, 2) if t_not > 0 else float(bundle.fee_context.get("taker_bps") or 5.0)
    t_net_bps = round((sum(float(l["net_fee_usd"]) for l in t_legs) / t_not) * 10_000, 2) if t_not > 0 else None
    t_net_sum = sum(float(l["net_fee_usd"]) for l in t_legs)
    maker_sav = round(max(0.0, t_net_sum - t_not * ((m_net_bps or m_gross_bps) / 10_000.0)), 2) if t_not > 0 else 0.0

    return {
        "venue_id": "bingx",
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
