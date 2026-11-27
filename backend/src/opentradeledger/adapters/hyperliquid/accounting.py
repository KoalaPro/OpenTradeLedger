"""Independent actuarial accounting engine for Hyperliquid (`venue_id = 'hyperliquid'`)."""

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
from opentradeledger.adapters.hyperliquid.client import (
    _norm_hl_symbol,
    normalize_hyperliquid_closed_trades,
    resolve_hyperliquid_spot_market,
    resolve_hyperliquid_spot_price,
)
from opentradeledger.adapters.stock_channels import classify_spot_stock_token, spot_token_holding
from opentradeledger.core.venue_primitives import (
    STABLECOINS,
    build_spot_wallet_balances,
    build_venue_stock_perpetuals,
    clamp_reconcile_streams_to_target,
    dec,
    fmt_ms,
    merge_legacy_journal_entries,
    spot_holdings_from_wallets,
)

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore


def extract_hyperliquid_spot_prices(store: "LedgerStore") -> dict[str, float]:
    snap = store.venue_scope("hyperliquid").get_kv("snapshot")
    out: dict[str, float] = {}
    if not isinstance(snap, dict):
        return out
    src_key = "spot_tickers" if isinstance(snap.get("spot_tickers"), dict) and (snap.get("has_spot_meta") or snap.get("spot_tickers")) else (
        "tickers" if not snap.get("has_spot_meta") and not snap.get("has_at_mids") else ""
    )
    if src_key and isinstance(snap.get(src_key), dict):
        for sym, raw_px in (snap.get(src_key) or {}).items():
            try:
                px = float(raw_px)
                if px > 0:
                    out[str(sym).strip().upper()] = px
            except (TypeError, ValueError):
                continue
    return out


def _compute_hl_principal(ledger_updates: list[dict[str, Any]], user_addr: str) -> float:
    addr = (user_addr or "").strip().lower()
    net = 0.0
    for row in ledger_updates:
        if not isinstance(row, dict):
            continue
        delta = row.get("delta") if isinstance(row.get("delta"), dict) else row
        tp = str(delta.get("type") or "").strip()
        if tp == "deposit":
            net += float(delta.get("usdc") or 0.0)
        elif tp == "withdraw":
            net -= abs(float(delta.get("usdc") or 0.0)) + abs(float(delta.get("fee") or 0.0))
        elif tp in {"internalTransfer", "subAccountTransfer"}:
            amt = abs(float(delta.get("usdc") or 0.0))
            dest = str(delta.get("destination") or "").strip().lower()
            src = str(delta.get("user") or "").strip().lower()
            if addr and dest == addr and src != addr:
                net += amt
            elif addr and src == addr and dest != addr:
                net -= amt + abs(float(delta.get("fee") or 0.0))
        elif tp == "spotTransfer":
            token = str(delta.get("token") or "").strip().upper()
            if token in STABLECOINS:
                amt = abs(float(delta.get("amount") or delta.get("usdcValue") or 0.0))
                dest = str(delta.get("destination") or "").strip().lower()
                src = str(delta.get("user") or "").strip().lower()
                if addr and dest == addr and src != addr:
                    net += amt
                elif addr and src == addr and dest != addr:
                    net -= amt
        elif tp in {
            "accountClassTransfer",
            "vaultDeposit",
            "vaultWithdraw",
            "vaultDistribution",
            "vaultLeaderCommission",
        }:
            # Internal moves between Perp, Spot, and HLP/Vaults on the same wallet leave external principal unchanged.
            continue
    return round(net, 2)


def build_hyperliquid_venue_ledger(
    store: "LedgerStore",
    meta: AdapterMeta,
    *,
    journals: Mapping[str, Mapping[str, Any]],
    now_s: float,
    global_spot_prices: Mapping[str, float] | None = None,
) -> VenueLedgerBundle | None:
    _ = now_s
    snap = store.venue_scope("hyperliquid").get_kv("snapshot")
    if not isinstance(snap, dict):
        return None

    perp_tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}
    perp_px_map: dict[str, float] = {**(global_spot_prices or {})}
    for sym, raw_px in perp_tickers.items():
        try:
            px_v = float(raw_px)
            if px_v > 0:
                perp_px_map[str(sym).strip().upper()] = px_v
        except (TypeError, ValueError):
            continue

    ch = snap.get("clearinghouse") if isinstance(snap.get("clearinghouse"), dict) else {}
    ms = ch.get("marginSummary") if isinstance(ch.get("marginSummary"), dict) else {}
    perp_equity_usd = float(ms.get("accountValue") or 0.0)
    withdrawable = ch.get("withdrawable")
    if withdrawable is None:
        withdrawable = ms.get("withdrawable")
    withdrawable_usd = float(withdrawable) if withdrawable is not None else perp_equity_usd

    wallet_balances: list[dict[str, Any]] = []
    spot_holdings: list[dict[str, Any]] = []
    stock_holdings: list[dict[str, Any]] = []
    spot_equity_usd = 0.0
    spot = snap.get("spot") if isinstance(snap.get("spot"), dict) else {}
    for bal in spot.get("balances") or []:
        if not isinstance(bal, dict):
            continue
        ccy = str(bal.get("coin") or "").strip().upper()
        qty = float(bal.get("total") or 0.0)
        if not ccy or qty <= 0:
            continue
        unit_px = 1.0 if ccy in STABLECOINS else resolve_hyperliquid_spot_price(bal, snap)
        usd_val = qty * unit_px
        spot_equity_usd += usd_val
        market = None if ccy in STABLECOINS else resolve_hyperliquid_spot_market(
            bal, snap, unit_price=unit_px, quantity=qty,
        )
        idx = str(bal.get("token"))
        identity = {"spot_token_index": int(idx)} if idx.isascii() and idx.isdigit() else {}
        stock = classify_spot_stock_token(ccy, exchange="HYPERLIQUID", token_index=idx)
        if stock and usd_val >= 0.5:
            holding = spot_token_holding(
                stock, qty, usd_val, venue_id="hyperliquid", venue_name=meta.display_name,
                venue_name_zh=meta.display_name_zh, spot_market=market,
            )
            stock_holdings.append({**holding, **identity})
            continue

        # HyperCore permits duplicate tickers. Keep each token's own quantity,
        # quote and liquidity through both wallet and overview projections.
        rows = build_spot_wallet_balances(
            [(ccy, qty, usd_val, "SPOT")], venue_id="hyperliquid", venue_name=meta.display_name,
            color_token=meta.color_token, spot_prices={}, excluded_symbols=set(),
        )
        for row in rows:
            row.update(identity)
            if market:
                row["spot_market"] = market
        wallet_balances.extend(rows)
        spot_holdings.extend(spot_holdings_from_wallets(rows))

    raw_spot_rows: list[tuple[str, float, float, str]] = []
    if perp_equity_usd > 0:
        raw_spot_rows.append(("USDC", perp_equity_usd, perp_equity_usd, "PERP_MARGIN"))

    vault_equities = snap.get("vault_equities") if isinstance(snap.get("vault_equities"), list) else []
    vault_equity_dec = sum(
        (dec(v.get("equity")) for v in vault_equities if isinstance(v, dict)),
        Decimal("0"),
    )
    vault_equity_usd = max(0.0, float(vault_equity_dec))
    if vault_equity_usd > 0:
        raw_spot_rows.append(("USDC", vault_equity_usd, vault_equity_usd, "VAULT"))

    equity_2dp = round(perp_equity_usd + spot_equity_usd + vault_equity_usd, 2)
    principal_usd = _compute_hl_principal(
        snap.get("ledger_updates") or [],
        str(snap.get("wallet_address") or ""),
    )

    open_positions: list[dict[str, Any]] = []
    for ap in ch.get("assetPositions") or []:
        pos = ap.get("position") if isinstance(ap, dict) and isinstance(ap.get("position"), dict) else ap
        if not isinstance(pos, dict):
            continue
        szi = float(pos.get("szi") or 0.0)
        if abs(szi) <= 0:
            continue
        raw_coin = str(pos.get("coin") or "")
        sym = _norm_hl_symbol(raw_coin)
        lev_obj = pos.get("leverage")
        lev = float(lev_obj.get("value") or 1.0) if isinstance(lev_obj, dict) else float(lev_obj or 1.0)
        entry_px = float(pos.get("entryPx") or 0.0)
        notional = abs(float(pos.get("positionValue") or (abs(szi) * entry_px)))
        mark_px = (notional / abs(szi)) if abs(szi) > 0 and notional > 0 else float(perp_px_map.get(raw_coin.upper(), entry_px))
        upnl = round(float(pos.get("unrealizedPnl") or 0.0), 2)
        margin_used = float(pos.get("marginUsed") or (notional / max(1.0, lev)))
        roi_pct = round((upnl / margin_used) * 100.0, 2) if margin_used > 0 else 0.0
        open_positions.append(
            {
                "venue_id": "hyperliquid",
                "venue_name": meta.display_name,
                "color_token": meta.color_token,
                "symbol": sym,
                "side": "LONG" if szi > 0 else "SHORT",
                "leverage": lev,
                "qty": abs(szi),
                "notional_usd": round(notional, 2),
                "entry_price": entry_px,
                "mark_price": round(mark_px, 6),
                "liq_price": float(pos.get("liquidationPx") or 0.0),
                "unrealized_pnl_usd": upnl,
                "unrealized_roi_pct": roi_pct,
                "has_stop_order": False,
            }
        )

    raw_trades = normalize_hyperliquid_closed_trades(
        snap.get("fills") or [],
        snap.get("funding_history") or [],
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
            round((rt["total_rebate_usd"] / base_f) * 100.0, 1)
            if base_f > 0
            else (100.0 if rt["total_rebate_usd"] > 0 else 0.0)
        )
        rt["entry_leg"]["time_str"] = fmt_ms(rt["entry_leg"]["time_ms"], "%m-%d %H:%M")
        rt["exit_leg"]["time_str"] = fmt_ms(rt["exit_leg"]["time_ms"], "%m-%d %H:%M")
        j = merge_legacy_journal_entries(journals, rt["trade_id"], rt.pop("_journal_keys", ()))
        rt["tags"] = list(j.get("tags") or [])
        rt["note"] = str(j.get("note") or "")
        trades.append(rt)
        for day, amount in trade_daily_amounts(rt, dec(rt["net_pnl_usd"])).items():
            daily_pnl[day] += amount
            daily_comp[day]["hyperliquid_futures"] += amount

    open_upl_sum = sum(float(p["unrealized_pnl_usd"]) for p in open_positions)
    closed_net_sum = sum(float(t["net_pnl_usd"]) for t in trades)
    closed_book_sum = sum(float(t["book_pnl_usd"]) for t in trades)
    closed_reb_sum = sum(float(t["total_rebate_usd"]) for t in trades)
    futures_stream_usd = round(closed_net_sum + open_upl_sum, 2)
    net_pnl = (
        round(equity_2dp - principal_usd, 2)
        if principal_usd != 0 or (snap.get("ledger_updates"))
        else futures_stream_usd
    )

    reconcile_streams: list[dict[str, Any]] = []
    if trades or open_positions or abs(futures_stream_usd) > 0.001:
        reconcile_streams.append(
            {
                "key": "hyperliquid_futures",
                "label": "Hyperliquid Perp Net PnL (incl. Maker Rebates & Funding)",
                "label_zh": "Hyperliquid 链上永续合约实收净盈亏",
                "detail": f"All-time {len(trades)} closed trades book {closed_book_sum:+.2f} U + Maker rebates +{closed_reb_sum:.2f} U",
                "detail_zh": f"全历史 {len(trades)} 笔平仓账面 {closed_book_sum:+.2f} U + 挂单返费 +{closed_reb_sum:.2f} U",
                "amount_usd": futures_stream_usd,
            }
        )

    rem_stream_usd = round(net_pnl - futures_stream_usd, 2)
    if abs(rem_stream_usd) >= 0.01 or (not reconcile_streams and abs(net_pnl) > 0.001):
        reconcile_streams.append(
            {
                "key": "hyperliquid_earn_spot",
                "label": "Hyperliquid Spot & HLP Valuation",
                "label_zh": "Hyperliquid 链上现货与 HLP 金库估值变动",
                "detail": "On-chain spot token valuation & vault delta",
                "detail_zh": "链上现货代币（如 HYPE）市值及金库收益净变动",
                "amount_usd": rem_stream_usd,
            }
        )

    reconcile_streams = clamp_reconcile_streams_to_target(
        reconcile_streams,
        net_pnl,
        fallback_key="hyperliquid_earn_spot",
        fallback_label_zh="Hyperliquid 链上账户净值变动",
        fallback_detail_zh="链上永续与现货账户估值净变动",
    )

    wallet_balances.extend(build_spot_wallet_balances(
        raw_spot_rows,
        venue_id="hyperliquid",
        venue_name=meta.display_name,
        color_token=meta.color_token,
        spot_prices={},
        excluded_symbols=set(),
    ))
    stock_perps = build_venue_stock_perpetuals(
        trades,
        venue_id="hyperliquid",
        venue_name=meta.display_name,
        venue_name_zh=meta.display_name_zh,
    )
    tot_gross = sum(float(t["total_fee_usd"]) for t in trades)
    obs_reb_pct = round((closed_reb_sum / tot_gross) * 100.0, 1) if tot_gross > 0 else 0.0

    return VenueLedgerBundle(
        venue_id="hyperliquid",
        exchange_card={
            "venue_id": "hyperliquid",
            "display_name": meta.display_name,
            "display_name_zh": meta.display_name_zh,
            "color_token": meta.color_token,
            "connected": True,
            "equity_usd": equity_2dp,
            "net_principal_usd": principal_usd,
            "net_pnl_usd": net_pnl,
            "futures_equity_usd": round(perp_equity_usd, 2),
            "available_margin_usd": round(withdrawable_usd, 2),
            "trade_count": len(trades),
            "rebate_rate_pct": obs_reb_pct,
            "discount_rate_pct": 0.0,
            "capabilities": meta.capabilities.model_dump(),
        },
        trades=trades,
        open_positions=open_positions,
        spot_holdings=spot_holdings,
        spot_wallet_balances=wallet_balances,
        reconcile_streams=reconcile_streams,
        daily_pnl=dict(daily_pnl),
        daily_components={d: dict(c) for d, c in daily_comp.items()},
        copy_trading=VenueCopyBundle(),
        stocks=VenueStockBundle(
            holdings=stock_holdings, perpetuals=stock_perps,
            equity_usd=round(sum(row["market_value_usd"] for row in stock_holdings), 2),
        ),
        fee_context={
            "maker_bps": 1.5,
            "taker_bps": 4.5,
            "maker_bps_configured": True,
            "taker_bps_configured": True,
        },
    )


def build_hyperliquid_fee_matrix_row(
    bundle: VenueLedgerBundle,
    range_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not range_trades:
        return None
    ex = bundle.exchange_card
    v_gross = round(sum(float(t["total_fee_usd"]) for t in range_trades), 2)
    v_reb = round(sum(float(t["total_rebate_usd"]) for t in range_trades), 2)
    v_net = round(sum(float(t["net_fee_usd"]) for t in range_trades), 2)
    v_reb_pct = round((v_reb / v_gross) * 100.0, 1) if v_gross > 0 else float(ex["rebate_rate_pct"])
    v_relief_pct = round((v_reb / v_gross) * 100.0, 1) if v_gross > 0 else 0.0

    m_legs = [t[lk] for t in range_trades for lk in ("entry_leg", "exit_leg") if t[lk].get("fee_known", True) and t[lk]["role"] == "MAKER"]
    t_legs = [t[lk] for t in range_trades for lk in ("entry_leg", "exit_leg") if t[lk].get("fee_known", True) and t[lk]["role"] == "TAKER"]
    tot_not = sum(float(t[lk]["notional_usd"] or 0.0) for t in range_trades for lk in ("entry_leg", "exit_leg"))
    m_not = sum(float(l["notional_usd"] or 0.0) for l in m_legs)
    t_not = sum(float(l["notional_usd"] or 0.0) for l in t_legs)
    m_gross_bps = round((sum(float(l["gross_fee_usd"]) for l in m_legs) / m_not) * 10_000, 2) if m_not > 0 else float(bundle.fee_context.get("maker_bps") or 1.5)
    m_net_bps = round((sum(float(l["net_fee_usd"]) for l in m_legs) / m_not) * 10_000, 2) if m_not > 0 else None
    t_gross_bps = round((sum(float(l["gross_fee_usd"]) for l in t_legs) / t_not) * 10_000, 2) if t_not > 0 else float(bundle.fee_context.get("taker_bps") or 4.5)
    t_net_bps = round((sum(float(l["net_fee_usd"]) for l in t_legs) / t_not) * 10_000, 2) if t_not > 0 else None
    t_net_sum = sum(float(l["net_fee_usd"]) for l in t_legs)
    maker_sav = round(max(0.0, t_net_sum - t_not * ((m_net_bps or m_gross_bps) / 10_000.0)), 2) if t_not > 0 else 0.0

    return {
        "venue_id": "hyperliquid",
        "display_name": ex["display_name"],
        "display_name_zh": ex["display_name_zh"],
        "color_token": ex["color_token"],
        "trade_count": len(range_trades),
        "maker_gross_bps": m_gross_bps,
        "maker_net_bps": m_net_bps,
        "taker_gross_bps": t_gross_bps,
        "taker_net_bps": t_net_bps,
        "maker_bps_known": m_not > 0,
        "maker_rate_source": "observed" if m_not > 0 else "configured",
        "taker_bps_known": t_not > 0,
        "taker_rate_source": "observed" if t_not > 0 else "configured",
        "blended_gross_bps": round((v_gross / tot_not) * 10_000, 2) if tot_not > 0 else 0.0,
        "blended_net_bps": round((v_net / tot_not) * 10_000, 2) if tot_not > 0 else 0.0,
        "gross_fee_usd": v_gross,
        "discount_saved_usd": 0.0,
        "discount_label": "",
        "rebate_usd": v_reb,
        "rebate_pct": v_reb_pct,
        "net_fee_usd": v_net,
        "combined_relief_pct": v_relief_pct,
        "potential_maker_savings_usd": maker_sav,
    }
