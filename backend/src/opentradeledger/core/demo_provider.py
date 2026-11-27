"""Realistic, fully sanitized multi-exchange Demo dataset (Binance / OKX / Bybit / Gate / Bitget / MEXC)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from opentradeledger.config import UserPreferences

_DEMO_EXCHANGES: list[dict[str, Any]] = [
    {
        "venue_id": "binance",
        "display_name": "Binance",
        "display_name_zh": "币安",
        "color_token": "binance",
        "connected": True,
        "equity_usd": 8420.50,
        "net_principal_usd": 7500.00,
        "net_pnl_usd": 920.50,
        "available_margin_usd": 4210.00,
        "rebate_rate_pct": 20.0,
        "discount_rate_pct": 10.0,
        "capabilities": {"futures": True, "rebates": True, "token_discount": True, "copy_trading": True, "stocks": True, "tokenized_stocks": True},
    },
    {
        "venue_id": "okx",
        "display_name": "OKX",
        "display_name_zh": "OKX",
        "color_token": "okx",
        "connected": True,
        "equity_usd": 5340.20,
        "net_principal_usd": 4800.00,
        "net_pnl_usd": 540.20,
        "available_margin_usd": 2980.00,
        "rebate_rate_pct": 35.0,
        "discount_rate_pct": 0.0,
        "capabilities": {"futures": True, "rebates": True, "token_discount": False, "copy_trading": True, "stocks": False, "tokenized_stocks": False},
    },
    {
        "venue_id": "bybit",
        "display_name": "Bybit",
        "display_name_zh": "Bybit",
        "color_token": "bybit",
        "connected": True,
        "equity_usd": 4210.80,
        "net_principal_usd": 3900.00,
        "net_pnl_usd": 310.80,
        "available_margin_usd": 3500.00,
        "rebate_rate_pct": 25.0,
        "discount_rate_pct": 0.0,
        "capabilities": {"futures": True, "rebates": True, "token_discount": False, "copy_trading": True, "stocks": False, "tokenized_stocks": False},
    },
    {
        "venue_id": "gate",
        "display_name": "Gate",
        "display_name_zh": "Gate",
        "color_token": "gate",
        "connected": True,
        "equity_usd": 3890.00,
        "net_principal_usd": 3500.00,
        "net_pnl_usd": 390.00,
        "available_margin_usd": 1480.00,
        "rebate_rate_pct": 74.3,
        "discount_rate_pct": 0.0,
        "capabilities": {"futures": True, "rebates": True, "token_discount": False, "copy_trading": True, "stocks": True, "tokenized_stocks": True},
    },
    {
        "venue_id": "bitget",
        "display_name": "Bitget",
        "display_name_zh": "Bitget",
        "color_token": "bitget",
        "connected": True,
        "equity_usd": 1765.40,
        "net_principal_usd": 1400.00,
        "net_pnl_usd": 365.40,
        "available_margin_usd": 1765.40,
        "rebate_rate_pct": 50.0,
        "discount_rate_pct": 0.0,
        "capabilities": {"futures": True, "rebates": True, "token_discount": True, "copy_trading": True, "stocks": True, "tokenized_stocks": True},
    },
    {
        "venue_id": "mexc",
        "display_name": "MEXC",
        "display_name_zh": "MEXC",
        "color_token": "mexc",
        "connected": True,
        "equity_usd": 1053.60,
        "net_principal_usd": 900.00,
        "net_pnl_usd": 153.60,
        "available_margin_usd": 510.00,
        "rebate_rate_pct": 0.0,
        "discount_rate_pct": 20.0,
        "capabilities": {"futures": True, "rebates": True, "token_discount": True, "copy_trading": True, "stocks": True, "tokenized_stocks": True},
    },
]


def _make_demo_trade(
    idx: int,
    venue_id: str,
    venue_name: str,
    symbol: str,
    side: str,
    qty: float,
    days_ago: int,
    hold_min: int,
    entry_px: float,
    exit_px: float,
    en_role: str,
    ex_role: str,
    en_bps: float,
    ex_bps: float,
    en_gross_fee: float,
    ex_gross_fee: float,
    rebate_pct: float,
    en_disc: float = 0.0,
    ex_disc: float = 0.0,
    disc_asset: str = "",
    funding: float = 0.0,
    partial: bool = False,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    base_dt = datetime(2026, 11, 22, 18, 0, tzinfo=timezone(timedelta(hours=8)))
    close_dt = base_dt - timedelta(days=days_ago, hours=idx % 9)
    open_dt = close_dt - timedelta(minutes=hold_min)
    open_ms = int(open_dt.timestamp() * 1000)
    close_ms = int(close_dt.timestamp() * 1000)

    sign = 1.0 if side == "LONG" else -1.0
    gross_pnl = round((exit_px - entry_px) * qty * sign, 4)

    en_reb = round(en_gross_fee * (rebate_pct / 100.0), 4)
    ex_reb = round(ex_gross_fee * (rebate_pct / 100.0), 4)
    en_net_fee = round(max(0.0, en_gross_fee - en_reb), 4)
    ex_net_fee = round(max(0.0, ex_gross_fee - ex_reb), 4)

    total_fee = round(en_gross_fee + ex_gross_fee, 4)
    total_reb = round(en_reb + ex_reb, 4)
    total_disc = round(en_disc + ex_disc, 4)
    net_fee = round(en_net_fee + ex_net_fee, 4)

    book_pnl = round(gross_pnl - total_fee + funding, 4)
    net_pnl = round(book_pnl + total_reb, 4)
    base_before_relief = total_fee + total_disc
    relief_pct = round(((total_reb + total_disc) / base_before_relief) * 100.0, 1) if base_before_relief > 0 else 0.0

    return {
        "trade_id": f"demo_{venue_id}_{symbol}_{idx}",
        "venue_id": venue_id,
        "venue_name": venue_name,
        "color_token": venue_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "open_time_ms": open_ms,
        "close_time_ms": close_ms,
        "open_time_str": open_dt.strftime("%Y-%m-%d %H:%M"),
        "close_time_str": close_dt.strftime("%Y-%m-%d %H:%M"),
        "close_date": close_dt.strftime("%Y-%m-%d"),
        "holding_sec": hold_min * 60,
        "is_partial_close": partial,
        "gross_pnl_usd": gross_pnl,
        "funding_pnl_usd": funding,
        "book_pnl_usd": book_pnl,
        "total_fee_usd": total_fee,
        "total_rebate_usd": total_reb,
        "total_discount_saved_usd": total_disc,
        "net_fee_usd": net_fee,
        "net_pnl_usd": net_pnl,
        "effective_relief_pct": relief_pct,
        "rebate_status": "SETTLED",
        "entry_leg": {
            "role": en_role,
            "mode_code": "LIMIT_MAKER" if en_role == "MAKER" else "MARKET_TAKER",
            "fee_bps": en_bps,
            "time_ms": open_ms,
            "time_str": open_dt.strftime("%m-%d %H:%M"),
            "avg_price": entry_px,
            "notional_usd": round(entry_px * qty, 2),
            "gross_fee_usd": en_gross_fee,
            "discount_saved_usd": en_disc,
            "discount_asset": disc_asset,
            "rebate_usd": en_reb,
            "rebate_pct": rebate_pct,
            "net_fee_usd": en_net_fee,
            "rebate_status": "NONE" if en_gross_fee <= 0 else "SETTLED",
            "settled_at": close_dt.strftime("%m-%d %H:%M") if en_reb > 0 else "",
        },
        "exit_leg": {
            "role": ex_role,
            "mode_code": "LIMIT_MAKER" if ex_role == "MAKER" else ("STOP_MARKET" if gross_pnl < 0 else "MARKET_TAKER"),
            "fee_bps": ex_bps,
            "time_ms": close_ms,
            "time_str": close_dt.strftime("%m-%d %H:%M"),
            "avg_price": exit_px,
            "notional_usd": round(exit_px * qty, 2),
            "gross_fee_usd": ex_gross_fee,
            "discount_saved_usd": ex_disc,
            "discount_asset": disc_asset,
            "rebate_usd": ex_reb,
            "rebate_pct": rebate_pct,
            "net_fee_usd": ex_net_fee,
            "rebate_status": "NONE" if ex_gross_fee <= 0 else "SETTLED",
            "settled_at": close_dt.strftime("%m-%d %H:%M") if ex_reb > 0 else "",
        },
        "exit_batches": (
            [
                {
                    "batch_index": 1,
                    "qty": round(qty / 2.0, 6),
                    "role": ex_role,
                    "mode_code": "LIMIT_MAKER" if ex_role == "MAKER" else "MARKET_TAKER",
                    "fee_known": True,
                    "fee_bps": ex_bps,
                    "time_ms": open_ms + (close_ms - open_ms) // 2,
                    "time_str": (open_dt + (close_dt - open_dt) / 2).strftime("%m-%d %H:%M"),
                    "avg_price": exit_px,
                    "notional_usd": round(exit_px * (qty / 2.0), 2),
                    "gross_fee_usd": round(ex_gross_fee / 2.0, 4),
                    "discount_saved_usd": round(ex_disc / 2.0, 4),
                    "discount_asset": disc_asset,
                    "rebate_usd": round(ex_reb / 2.0, 4),
                    "net_fee_usd": round(ex_net_fee / 2.0, 4),
                    "gross_pnl_usd": round(gross_pnl / 2.0, 4),
                    "net_pnl_usd": round(gross_pnl / 2.0 - ex_net_fee / 2.0, 4),
                },
                {
                    "batch_index": 2,
                    "qty": round(qty - round(qty / 2.0, 6), 6),
                    "role": ex_role,
                    "mode_code": "LIMIT_MAKER" if ex_role == "MAKER" else ("STOP_MARKET" if gross_pnl < 0 else "MARKET_TAKER"),
                    "fee_known": True,
                    "fee_bps": ex_bps,
                    "time_ms": close_ms,
                    "time_str": close_dt.strftime("%m-%d %H:%M"),
                    "avg_price": exit_px,
                    "notional_usd": round(exit_px * qty - round(exit_px * (qty / 2.0), 2), 2),
                    "gross_fee_usd": round(ex_gross_fee - round(ex_gross_fee / 2.0, 4), 4),
                    "discount_saved_usd": round(ex_disc - round(ex_disc / 2.0, 4), 4),
                    "discount_asset": disc_asset,
                    "rebate_usd": round(ex_reb - round(ex_reb / 2.0, 4), 4),
                    "net_fee_usd": round(ex_net_fee - round(ex_net_fee / 2.0, 4), 4),
                    "gross_pnl_usd": round(gross_pnl - round(gross_pnl / 2.0, 4), 4),
                    "net_pnl_usd": round((gross_pnl - round(gross_pnl / 2.0, 4)) - (ex_net_fee - round(ex_net_fee / 2.0, 4)), 4),
                },
            ]
            if partial
            else []
        ),
        "tags": tags or [],
        "note": "",
    }


def _demo_trades() -> list[dict[str, Any]]:
    return [
        _make_demo_trade(1, "gate", "Gate", "ETH_USDT", "SHORT", 6.0, 0, 32, 2740.0, 2714.0, "MAKER", "TAKER", 2.0, 5.0, 3.288, 8.142, 74.3, tags=["planned", "clean_tp"]),
        _make_demo_trade(2, "okx", "OKX", "BTC_USDT", "LONG", 0.25, 1, 145, 87200.0, 89120.0, "MAKER", "MAKER", 2.0, 2.0, 4.360, 4.456, 35.0, tags=["planned"]),
        _make_demo_trade(3, "bybit", "Bybit", "SOL_USDT", "LONG", 40.0, 2, 75, 184.5, 193.2, "MAKER", "TAKER", 2.0, 5.5, 1.476, 4.250, 25.0, partial=True),
        _make_demo_trade(4, "binance", "Binance", "BTC_USDT", "LONG", 0.30, 3, 210, 85900.0, 88400.0, "MAKER", "TAKER", 1.8, 4.5, 4.638, 11.934, 20.0, en_disc=0.515, ex_disc=1.326, disc_asset="BNB", tags=["planned"]),
        _make_demo_trade(5, "bitget", "Bitget", "ETH_USDT", "LONG", 4.5, 4, 190, 2690.0, 2652.0, "MAKER", "TAKER", 2.0, 6.0, 2.421, 7.160, 50.0, tags=["clean_sl"]),
        _make_demo_trade(6, "mexc", "MEXC", "NVDAX_USDT", "LONG", 20.0, 5, 65, 141.0, 147.8, "MAKER", "TAKER", 0.0, 0.0, 0.0, 0.0, 0.0, tags=["planned"]),
        _make_demo_trade(7, "okx", "OKX", "SOL_USDT", "SHORT", 30.0, 6, 48, 196.0, 191.5, "MAKER", "TAKER", 2.0, 5.0, 1.176, 2.872, 35.0),
        _make_demo_trade(8, "bybit", "Bybit", "ETH_USDT", "SHORT", 3.5, 8, 280, 2760.0, 2792.0, "TAKER", "TAKER", 5.5, 5.5, 5.313, 5.374, 25.0, tags=["taker_drag"]),
        _make_demo_trade(9, "binance", "Binance", "SNDK_USDT", "LONG", 15.0, 10, 95, 78.2, 85.4, "MAKER", "TAKER", 0.0, 3.6, 0.0, 0.461, 20.0, ex_disc=0.051, disc_asset="BNB"),
        _make_demo_trade(10, "gate", "Gate", "BTC_USDT", "LONG", 0.18, 12, 320, 84500.0, 86700.0, "MAKER", "MAKER", 2.0, 2.0, 3.042, 3.121, 80.0, funding=-1.20),
        _make_demo_trade(11, "bitget", "Bitget", "BTC_USDT", "SHORT", 0.22, 18, 110, 86800.0, 85100.0, "MAKER", "TAKER", 2.0, 6.0, 3.819, 11.233, 50.0),
        _make_demo_trade(12, "mexc", "MEXC", "ETH_USDT", "LONG", 3.0, 26, 85, 2610.0, 2668.0, "MAKER", "TAKER", 0.0, 1.6, 0.0, 1.280, 0.0, ex_disc=0.320, disc_asset="MX"),
    ]


def build_demo_dashboard(
    *,
    range_key: str = "all",
    venue_filter: str = "ALL",
    symbol_filter: str = "ALL",
    prefs: UserPreferences | None = None,
    journals: dict[str, dict[str, Any]] | None = None,
    latest_ai_review: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> dict[str, Any]:
    prefs = prefs or UserPreferences()
    journals = journals or {}
    all_trades = _demo_trades()
    for tr in all_trades:
        if tr["trade_id"] in journals:
            tr["tags"] = journals[tr["trade_id"]].get("tags", tr["tags"])
            tr["note"] = journals[tr["trade_id"]].get("note", "")

    now_dt = now_dt or datetime(2026, 11, 22, 23, 59, tzinfo=timezone(timedelta(hours=8)))

    def _in_range(tr: dict[str, Any]) -> bool:
        if range_key == "7d":
            return tr["close_time_ms"] >= int((now_dt - timedelta(days=7)).timestamp() * 1000)
        if range_key == "30d":
            return tr["close_time_ms"] >= int((now_dt - timedelta(days=30)).timestamp() * 1000)
        if len(range_key) == 7 and range_key[4] == "-":
            return tr["close_date"].startswith(range_key)
        return True

    filtered = [
        tr
        for tr in all_trades
        if _in_range(tr)
        and (venue_filter.upper() == "ALL" or tr["venue_id"].upper() == venue_filter.upper())
        and (symbol_filter.upper() == "ALL" or tr["symbol"].upper() == symbol_filter.upper())
    ]

    exchanges = [dict(ex, warnings=[]) for ex in _DEMO_EXCHANGES]
    period_trades = [tr for tr in all_trades if _in_range(tr)]
    counts_by_venue: dict[str, int] = defaultdict(int)
    for tr in period_trades:
        counts_by_venue[tr["venue_id"]] += 1
    for ex in exchanges:
        ex["trade_count"] = counts_by_venue.get(ex["venue_id"], 0)

    total_equity = round(sum(e["equity_usd"] for e in exchanges), 2)
    total_principal = round(sum(e["net_principal_usd"] for e in exchanges), 2)
    whole_net_pnl = round(total_equity - total_principal, 2)

    futures_net = round(sum(t["net_pnl_usd"] for t in filtered), 2)
    futures_book = round(sum(t["book_pnl_usd"] for t in filtered), 2)
    total_fee = round(sum(t["total_fee_usd"] for t in filtered), 2)
    total_reb = round(sum(t["total_rebate_usd"] for t in filtered), 2)
    total_disc = round(sum(t["total_discount_saved_usd"] for t in filtered), 2)
    net_fee = round(sum(t["net_fee_usd"] for t in filtered), 2)
    wins = sum(1 for t in filtered if t["net_pnl_usd"] > 0)
    win_rate = round((wins / len(filtered)) * 100.0, 1) if filtered else 0.0
    base_fee = total_fee + total_disc
    relief_pct = round(((total_reb + total_disc) / base_fee) * 100.0, 1) if base_fee > 0 else 0.0

    # Equity curve & calendar days
    day_deltas: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tr in all_trades:
        day_deltas[tr["close_date"]].append(tr)
    calendar_days = [
        {"date": day, "net_pnl_usd": round(sum(t["net_pnl_usd"] for t in items), 2), "trade_count": len(items)}
        for day, items in sorted(day_deltas.items())
    ]
    period_days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tr in filtered:
        period_days[tr["close_date"]].append(tr)
    equity_curve = []
    cum_net = cum_book = peak = max_drawdown = 0.0
    for seq, (d, items) in enumerate(sorted(period_days.items()), start=1):
        d_net = sum(x["net_pnl_usd"] for x in items)
        cum_net += d_net
        cum_book += sum(x["book_pnl_usd"] for x in items)
        peak = max(peak, cum_net)
        max_drawdown = max(max_drawdown, peak - cum_net)
        equity_curve.append({
            "seq": seq, "date": d, "label": d[5:], "delta_usd": round(d_net, 2),
            "cum_net_usd": round(cum_net, 2), "cum_book_usd": round(cum_book, 2),
        })

    # Fee matrix by venue
    fee_matrix: list[dict[str, Any]] = []
    for ex in exchanges:
        v_trades = [t for t in filtered if t["venue_id"] == ex["venue_id"]]
        if not v_trades:
            continue
        maker_bps = [
            leg["fee_bps"]
            for t in v_trades
            for leg in (t["entry_leg"], t["exit_leg"])
            if leg["role"] == "MAKER"
        ]
        taker_bps = [
            leg["fee_bps"]
            for t in v_trades
            for leg in (t["entry_leg"], t["exit_leg"])
            if leg["role"] == "TAKER"
        ]
        avg_m = round(sum(maker_bps) / len(maker_bps), 2) if maker_bps else 0.0
        avg_t = round(sum(taker_bps) / len(taker_bps), 2) if taker_bps else 0.0
        v_gross = round(sum(t["total_fee_usd"] for t in v_trades), 2)
        v_reb = round(sum(t["total_rebate_usd"] for t in v_trades), 2)
        v_disc = round(sum(t["total_discount_saved_usd"] for t in v_trades), 2)
        v_net = round(sum(t["net_fee_usd"] for t in v_trades), 2)
        v_reb_pct = round((v_reb / v_gross) * 100.0, 1) if v_gross > 0 else 0.0
        v_rel = round(((v_reb + v_disc) / (v_gross + v_disc)) * 100.0, 1) if (v_gross + v_disc) > 0 else 0.0
        m_net = round(avg_m * (1.0 - v_reb_pct / 100.0), 2)
        t_net = round(avg_t * (1.0 - v_reb_pct / 100.0), 2)
        fee_matrix.append(
            {
                "venue_id": ex["venue_id"],
                "display_name": ex["display_name"],
                "color_token": ex["color_token"],
                "trade_count": len(v_trades),
                "maker_bps_known": bool(maker_bps),
                "taker_bps_known": bool(taker_bps),
                "maker_gross_bps": avg_m,
                "maker_net_bps": m_net,
                "taker_gross_bps": avg_t,
                "taker_net_bps": t_net,
                "gross_fee_usd": v_gross,
                "discount_saved_usd": v_disc,
                "discount_label": "BNB" if ex["venue_id"] == "binance" else ("MX" if ex["venue_id"] == "mexc" else ""),
                "rebate_usd": v_reb,
                "rebate_pct": v_reb_pct,
                "net_fee_usd": v_net,
                "combined_relief_pct": v_rel,
                "potential_maker_savings_usd": round(max(0.0, (avg_t - avg_m) / max(avg_t, 1.0) * v_net * 0.6), 2),
            }
        )

    # Symbol attribution
    sym_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in filtered:
        sym_groups[(t["venue_id"], t["symbol"])].append(t)
    symbol_attribution = []
    for (vid, sym), rows in sym_groups.items():
        w = sum(1 for r in rows if r["net_pnl_usd"] > 0)
        symbol_attribution.append(
            {
                "venue_id": vid,
                "venue_name": rows[0]["venue_name"],
                "color_token": vid,
                "symbol": sym,
                "trade_count": len(rows),
                "win_rate_pct": round((w / len(rows)) * 100.0, 1),
                "gross_fee_usd": round(sum(r["total_fee_usd"] for r in rows), 2),
                "rebate_usd": round(sum(r["total_rebate_usd"] for r in rows), 2),
                "discount_saved_usd": round(sum(r["total_discount_saved_usd"] for r in rows), 2),
                "book_pnl_usd": round(sum(r["book_pnl_usd"] for r in rows), 2),
                "net_pnl_usd": round(sum(r["net_pnl_usd"] for r in rows), 2),
            }
        )
    symbol_attribution.sort(key=lambda r: r["net_pnl_usd"], reverse=True)

    # Reconciliation items summing to whole_net_pnl with 0.00 residual
    futures_all_net = round(sum(t["net_pnl_usd"] for t in all_trades), 2)
    copy_net = 286.70
    stock_net = 86.40
    earn_and_adj = round(whole_net_pnl - futures_all_net - copy_net - stock_net, 2)
    reconcile_items = [
        {"key": "futures", "label": "Futures Realized Net PnL", "label_zh": "合约平仓实收净盈亏", "detail": f"{len(all_trades)} closed trades including fee rebates & discounts", "detail_zh": f"{len(all_trades)} 笔平仓含返佣与平台币抵扣", "amount_usd": futures_all_net},
        {"key": "copy", "label": "Copy Trading Combined PnL", "label_zh": "跟单专账综合净收益", "detail": "Active + settled copy pools + direct rebates", "detail_zh": "在途与已结清跟单池净利 + 直退返佣", "amount_usd": copy_net},
        {"key": "stocks", "label": "Equities & Tokenized Stocks", "label_zh": "正股与 1:1 股票代币收益", "detail": "Spot equity & tokenized holdings unrealized + realized", "detail_zh": "跨平台正股与代币持仓浮盈及交割", "amount_usd": stock_net},
        {"key": "earn_adj", "label": "Earn Yield & Open Unrealized", "label_zh": "理财生息与在途持仓浮盈", "detail": "Flexible earn rewards + live open position MtM", "detail_zh": "持币生息奖励与当前在途仓位浮盈", "amount_usd": earn_and_adj},
    ]

    range_net = round(sum(t["net_pnl_usd"] for t in period_trades), 2)
    range_items = [{
        **reconcile_items[0],
        "detail": f"{len(period_trades)} closed trades in this period",
        "detail_zh": f"本期 {len(period_trades)} 笔平仓",
        "amount_usd": range_net,
    }] if period_trades else []
    if range_key == "all":
        range_net = whole_net_pnl
        range_items = reconcile_items

    # Optional custom goal tracker
    goal_info = None
    if prefs.goal_enabled and prefs.goal_target_usd and prefs.goal_target_usd > 0:
        target = float(prefs.goal_target_usd)
        remaining = max(0.0, round(target - total_equity, 2))
        surplus = max(0.0, round(total_equity - target, 2))
        today_str = now_dt.strftime("%Y-%m-%d")
        goal_info = {
            "enabled": True,
            "label": prefs.goal_label or "",
            "target_usd": target,
            "current_equity_usd": total_equity,
            "remaining_usd": remaining,
            "surplus_usd": surplus,
            "is_reached": total_equity >= target,
            "progress_pct": round(min(100.0, (total_equity / target) * 100.0), 2),
            "needed_gain_pct": round((remaining / total_equity) * 100.0, 2) if total_equity > 0 and remaining > 0 else 0.0,
            "today_delta_usd": next((pt["delta_usd"] for pt in reversed(equity_curve) if pt.get("date") == today_str), 0.0),
        }

    rk_norm = (range_key or "all").strip().lower() or "all"
    rk_lbl_en = "Last 7 Days" if rk_norm == "7d" else ("Last 30 Days" if rk_norm == "30d" else (rk_norm if rk_norm != "all" else "All-Time"))
    rk_lbl_zh = "近 7 天" if rk_norm == "7d" else ("近 30 天" if rk_norm == "30d" else (f"{rk_norm} 月度" if rk_norm != "all" else "全部历史"))

    default_ai = latest_ai_review or {
        "generated_at": "2026-11-22 18:00:00",
        "generated_ms": int(now_dt.timestamp() * 1000),
        "model": "demo-local-actuarial",
        "range_key": rk_norm,
        "range_label": rk_lbl_en,
        "range_label_zh": rk_lbl_zh,
        "trade_count_snapshot": len(filtered),
        "equity_snapshot_usd": total_equity,
        "stale": False,
        "highlights": [
            {
                "title": "Maker vs Taker Drag",
                "title_zh": "挂单与吃单磨损结构",
                "metric": f"-{net_fee:.2f} U",
                "body": "Entry legs are predominantly Maker while exit legs use Market Taker more often. Shifting exits to limit orders reduces net fee drag.",
                "body_zh": "进场挂单比例较高，但出场仍偏多市价吃单；分批限价止盈可进一步压缩净手续费磨损。",
            },
            {
                "title": "Win Rate & Payoff Discipline",
                "title_zh": "胜率与盈亏纪律",
                "metric": f"{win_rate:.1f}%",
                "body": "Winning trades close cleanly along trend structure while outlier losses come from oversized taker exits.",
                "body_zh": "盈利单执行干脆，少数回撤主要来自震荡行情中的市价追单与止损偏晚。",
            },
            {
                "title": "Rebate & Discount Coverage",
                "title_zh": "返佣与抵扣覆盖率",
                "metric": f"{relief_pct:.1f}%",
                "body": "Combined fee rebates and token discounts offset a substantial portion of gross trading fees across venues.",
                "body_zh": "动态返佣与平台币抵扣显著降低了真实交易成本，建议持续优先在净费率更低的平台执行高频交易。",
            },
        ],
        "markdown_commentary": (
            "1. **Prioritize limit exits on liquid majors**: Entry maker execution is strong, while market-taker exits account for most net fee cost.\n"
            "2. **Keep per-trade risk symmetric**: Cap single-position notional on volatile altcoins to prevent one stop-out from erasing three winners.\n"
            "3. **Leverage fee tiers dynamically**: Use *0-bps maker venues* for range-bound scalps and reserve higher-liquidity books for breakout fills."
        ),
        "markdown_commentary_zh": (
            "1. **主流币优先分批限价止盈**：当前进场挂单（Maker）执行表现优秀，但出场市价吃单贡献了大部分净手续费成本，改用限价单可显著留存利润。\n"
            "2. **保持单笔风险敞口对称**：对高波动山寨标的设置单笔名义仓位上限，避免一次止损回吐多笔趋势盈利。\n"
            "3. **按净费率梯队分配策略**：震荡网格与高频波段优先部署在 *0 bps 挂单免佣或高返佣平台*，突破追单则保留在高流动性盘口。"
        ),
    }

    spot_holdings = [
        {
            "venue_id": "binance",
            "venue_name": "Binance",
            "color_token": "binance",
            "asset": "BTC",
            "qty": 0.032,
            "price_usd": 89000.0,
            "market_value_usd": 2848.0,
            "wallet_type": "SPOT",
        },
        {
            "venue_id": "okx",
            "venue_name": "OKX",
            "color_token": "okx",
            "asset": "SOL",
            "qty": 8.5,
            "price_usd": 192.0,
            "market_value_usd": 1632.0,
            "wallet_type": "UNIFIED",
        },
    ]

    vf_raw = (venue_filter or "ALL").strip()
    vf_out = "ALL" if (not vf_raw or vf_raw.lower() == "all") else vf_raw.lower()
    sf_raw = (symbol_filter or "ALL").strip()
    sf_out = "ALL" if (not sf_raw or sf_raw.upper() == "ALL") else sf_raw.upper()

    return {
        "demo_mode": True,
        "read_only": True,
        "last_sync_ms": int(now_dt.timestamp() * 1000),
        "syncing": False,
        "range_key": range_key,
        "venue_filter": vf_out,
        "symbol_filter": sf_out,
        "warnings": [],
        "available_months": ["2026-11", "2026-10"],
        "available_symbols": sorted({t["symbol"] for t in all_trades}),
        "summary": {
            "total_equity_usd": total_equity,
            "net_principal_usd": total_principal,
            "whole_net_pnl_usd": whole_net_pnl,
            "range_whole_net_pnl_usd": range_net,
            "range_whole_roi_pct": round(range_net / total_principal * 100, 2) if total_principal > 0 else 0.0,
            "whole_roi_pct": round((whole_net_pnl / total_principal) * 100.0, 2) if total_principal > 0 else 0.0,
            "futures_net_pnl_usd": futures_net,
            "futures_book_pnl_usd": futures_book,
            "trade_count": len(filtered),
            "all_time_trade_count": len(all_trades),
            "win_rate_pct": win_rate,
            "total_fee_usd": total_fee,
            "total_rebate_usd": total_reb,
            "total_discount_saved_usd": total_disc,
            "net_fee_usd": net_fee,
            "combined_relief_pct": relief_pct,
            "potential_maker_savings_usd": round(sum(r["potential_maker_savings_usd"] for r in fee_matrix), 2),
            "max_drawdown_usd": round(max_drawdown, 2),
        },
        "equity_breakdown": {
            "futures_margin_usd": round(total_equity - 1642.80 - 1240.00 - 4480.00, 2),
            "copy_equity_usd": 1642.80,
            "stocks_equity_usd": 1240.00,
            "spot_and_other_usd": 4480.00,
        },
        "exchanges": exchanges,
        "open_positions": [
            {
                "venue_id": "binance",
                "venue_name": "Binance",
                "color_token": "binance",
                "symbol": "ETH_USDT",
                "side": "LONG",
                "leverage": 5.0,
                "qty": 2.5,
                "notional_usd": 6840.0,
                "entry_price": 2690.0,
                "mark_price": 2736.0,
                "liq_price": 2180.0,
                "unrealized_pnl_usd": 115.0,
                "unrealized_roi_pct": 1.71,
                "has_stop_order": True,
            },
            {
                "venue_id": "okx",
                "venue_name": "OKX",
                "color_token": "okx",
                "symbol": "BTC_USDT",
                "side": "SHORT",
                "leverage": 3.0,
                "qty": 0.08,
                "notional_usd": 7120.0,
                "entry_price": 89450.0,
                "mark_price": 89000.0,
                "liq_price": 118200.0,
                "unrealized_pnl_usd": 36.0,
                "unrealized_roi_pct": 0.50,
                "has_stop_order": True,
            },
        ],
        "spot_holdings": spot_holdings,
        "spot_wallet_balances": [{k: v for k, v in row.items() if k != "price_usd"} for row in spot_holdings],
        "trades": filtered,
        "reconciliation": {
            "net_principal_usd": total_principal,
            "total_equity_usd": total_equity,
            "net_pnl_usd": whole_net_pnl,
            "residual_usd": 0.0,
            "items": reconcile_items,
            "range_items": range_items,
        },
        "equity_curve": equity_curve,
        "calendar_days": calendar_days,
        "symbol_attribution": symbol_attribution,
        "fee_matrix": fee_matrix,
        "goal": goal_info,
        "ai_review": default_ai,
        "extensions": {
            "copy_trading": {
                "enabled": prefs.modules.copy_trading,
                "total_invested_usd": 3000.00,
                "active_equity_usd": 1642.80,
                "book_pnl_usd": 248.30,
                "rebate_total_usd": 38.40,
                "combined_pnl_usd": 286.70,
                "combined_roi_pct": 9.56,
                "platforms": [
                    {"venue_id": "gate", "venue_name": "Gate", "color_token": "gate", "status": "ACTIVE", "invested_usd": 1500.0, "equity_usd": 1642.8, "rebate_usd": 24.1, "combined_pnl_usd": 166.9, "rebate_count": 42},
                    {"venue_id": "binance", "venue_name": "Binance", "color_token": "binance", "status": "SETTLED", "invested_usd": 1500.0, "equity_usd": 0.0, "rebate_usd": 14.3, "combined_pnl_usd": 119.8, "rebate_count": 31},
                ],
                "transfers": [
                    {"venue_id": "gate", "venue_name": "Gate", "time_str": "2026-11-17 18:38", "direction": "Deposit to Copy Pool", "direction_zh": "划入跟单池", "amount_usd": 1500.00},
                    {"venue_id": "binance", "venue_name": "Binance", "time_str": "2026-11-05 09:20", "direction": "Redeem to Main Account", "direction_zh": "结清赎回主账户", "amount_usd": 1605.50},
                ],
                "rebates": [
                    {"venue_id": "gate", "venue_name": "Gate", "time_str": "2026-11-22 13:12:21", "destination": "Unified USDT", "est_gross_fee_usd": 0.420, "rebate_usd": 0.3024},
                    {"venue_id": "binance", "venue_name": "Binance", "time_str": "2026-11-21 20:05:10", "destination": "Futures · ETH_USDT", "est_gross_fee_usd": 1.150, "rebate_usd": 0.2300},
                    {"venue_id": "gate", "venue_name": "Gate", "time_str": "2026-11-21 11:40:02", "destination": "Unified USDT", "est_gross_fee_usd": 0.510, "rebate_usd": 0.3672},
                ],
            },
            "stocks": {
                "enabled": prefs.modules.stocks,
                "equity_usd": 1240.00,
                "unrealized_pnl_usd": 86.40,
                "non_perp_fees_usd": 1.24,
                "fee_rate_range": "0% ~ 0.1%",
                "venue_fee_rates": [
                    {"venue_id": "mexc", "venue_name": "MEXC", "summary": "0% / 0.08%"},
                    {"venue_id": "bitget", "venue_name": "Bitget", "summary": "0.05%"},
                    {"venue_id": "gate", "venue_name": "Gate", "summary": "0.098%"},
                    {"venue_id": "binance", "venue_name": "Binance", "summary": "0.1% (BNB)"},
                ],
                "holdings": [
                    {"venue_id": "gate", "venue_name": "Gate", "symbol": "NVDA", "name": "NVIDIA", "channel": "US Equity", "channel_zh": "美股正股", "shares": 5.0, "avg_cost_usd": 132.0, "current_price_usd": 144.8, "market_value_usd": 724.0, "pnl_usd": 64.0, "pnl_pct": 9.70},
                    {"venue_id": "mexc", "venue_name": "MEXC", "symbol": "AAPLX", "name": "Apple Token", "channel": "1:1 Tokenized", "channel_zh": "1:1 股票代币", "shares": 2.2, "avg_cost_usd": 224.36, "current_price_usd": 234.55, "market_value_usd": 516.0, "pnl_usd": 22.4, "pnl_pct": 4.54},
                ],
                "cash_ledger": [
                    {"venue_id": "gate", "venue_name": "Gate", "time_str": "2026-11-20 09:30", "type": "Buy", "symbol": "NVDA", "change": "+5.00 Shares"},
                    {"venue_id": "mexc", "venue_name": "MEXC", "time_str": "2026-11-15 14:15", "type": "Buy", "symbol": "AAPLX", "change": "+2.20 AAPLX"},
                ],
                "perpetuals": [
                    {"venue_id": "mexc", "venue_name": "MEXC", "symbol": "NVDAX_USDT", "category": "US Equity Perp", "trade_count": 14, "qty": 120.0, "maker_bps": 0.0, "taker_bps": 0.0, "gross_fee_usd": 0.0, "rebate_usd": 0.0, "net_fee_usd": 0.0, "net_pnl_usd": 164.20},
                    {"venue_id": "binance", "venue_name": "Binance", "symbol": "SNDK_USDT", "category": "US Equity Perp", "trade_count": 9, "qty": 45.0, "maker_bps": 0.0, "taker_bps": 3.6, "gross_fee_usd": 8.42, "rebate_usd": 1.684, "net_fee_usd": 6.736, "net_pnl_usd": 92.50},
                ],
            },
        },
    }
