"""Full read-only data syncers for Gate, Binance, Bitget, MEXC, and EasiCoin into SQLite WAL."""

from __future__ import annotations

from typing import Any

from opentradeledger.core.legacy_store import GateManualStore

FULL_BACKFILL_LOOKBACK_SEC = 365 * 86400
INCREMENTAL_LOOKBACK_SEC = 7 * 86400


async def sync_gate(
    client: Any,
    ls: GateManualStore,
    *,
    now_s: int,
    force_full: bool = False,
) -> tuple[float, bool, tuple[int, ...], tuple[int, ...]]:
    backfill_done = bool(ls.get_meta("backfill_completed", False))
    do_full = force_full or (not backfill_done)
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        len(ls.load_futures_closes()),
        len(ls.load_futures_trades()),
        len(ls.load_partner_rebates()),
        len(ls.load_spot_account_book()),
    )

    live_bundle = await client.fetch_live_overview_bundle()
    ls.set_meta("live_bundle", live_bundle)

    specs = ls.get_meta("contract_specs", {})
    if not specs or do_full:
        fetched_specs = await client.fetch_contract_specs()
        if fetched_specs:
            ls.set_meta("contract_specs", fetched_specs)

    closes = await client.fetch_futures_closes(since_sec=since_sec)
    trades = await client.fetch_futures_trades(since_sec=since_sec)
    orders = await client.fetch_futures_finished_orders(since_sec=since_sec)
    rebates = await client.fetch_partner_rebates(since_sec=since_sec)
    spot_book = await client.fetch_spot_account_book(since_sec=since_sec, max_pages=20 if do_full else 4)
    stk_orders, stk_deals = await client.fetch_stock_orders_and_deals(max_pages=10 if do_full else 2)
    stk_txs = await client.fetch_stock_transactions(max_pages=10 if do_full else 2)

    ls.upsert_futures_closes(closes)
    ls.upsert_futures_trades(trades)
    ls.upsert_futures_orders(orders)
    ls.upsert_partner_rebates(rebates)
    ls.upsert_spot_account_book(spot_book)
    ls.upsert_stock_records(orders=stk_orders, deals=stk_deals, transactions=stk_txs)
    ls.set_meta("backfill_completed", True)
    ls.set_meta("spot_window_backfill_v2", True)
    ls.set_meta("gate_decimal_trades_v2", True)

    wt_g = live_bundle.get("wallet_total") or {}
    det_g = wt_g.get("details") or {}
    eq_now = float((wt_g.get("total") or {}).get("amount") or 0.0)
    q_now = float((det_g.get("quant") or {}).get("amount") or 0.0)
    has_open = bool(
        live_bundle.get("positions")
        or live_bundle.get("futures_positions")
        or live_bundle.get("open_orders")
        or live_bundle.get("futures_open_orders")
        or live_bundle.get("price_orders")
        or live_bundle.get("stock_positions")
        or live_bundle.get("stock_open_orders")
        or q_now >= 1.0
    )
    fp_after = (
        len(ls.load_futures_closes()),
        len(ls.load_futures_trades()),
        len(ls.load_partner_rebates()),
        len(ls.load_spot_account_book()),
    )
    return eq_now, has_open, fp_before, fp_after


async def sync_easicoin(
    client: Any,
    ls: GateManualStore,
    *,
    now_s: int,
    force_full: bool = False,
) -> tuple[float, bool, tuple[int, ...], tuple[int, ...]]:
    backfill_done = bool(ls.get_meta("easicoin_backfill_v1", False))
    do_full = force_full or (not backfill_done)
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        len(ls.load_easicoin_closed_pnl()),
        len(ls.load_easicoin_fills()),
        len(ls.load_easicoin_transfers()),
    )

    live = await client.fetch_live_overview_bundle()
    ls.set_meta("easicoin_live_bundle", live)

    closed = await client.fetch_closed_pnl(since_sec=since_sec)
    fills = await client.fetch_trade_fills(since_sec=since_sec)
    orders = await client.fetch_orders_history(since_sec=since_sec)
    transfers = await client.fetch_transfers(since_sec=since_sec, max_windows=13 if do_full else 2)
    ls.upsert_easicoin_closed_pnl(closed)
    ls.upsert_easicoin_fills(fills)
    ls.upsert_easicoin_orders(orders)
    ls.upsert_easicoin_transfers(transfers)
    ls.set_meta("easicoin_backfill_v1", True)

    eq_val = 0.0
    for fb in live.get("futures_balances") or []:
        if str(fb.get("coin") or "").upper() in ("USDT", "USDC", "USD"):
            eq_val += float(fb.get("equity") or fb.get("wallet_balance") or 0.0)
    has_open = bool(live.get("positions") or live.get("open_orders"))
    fp_after = (
        len(ls.load_easicoin_closed_pnl()),
        len(ls.load_easicoin_fills()),
        len(ls.load_easicoin_transfers()),
    )
    return eq_val, has_open, fp_before, fp_after


async def sync_binance(
    client: Any,
    ls: GateManualStore,
    *,
    now_s: int,
    force_full: bool = False,
) -> tuple[float, bool, tuple[int, ...], tuple[int, ...]]:
    backfill_done = bool(ls.get_meta("binance_backfill_v2", False))
    do_full = force_full or (not backfill_done)
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        len(ls.load_binance_fills()),
        len(ls.load_binance_orders()),
        len(ls.load_binance_income()),
        len(ls.load_binance_cashflows()),
    )

    live = await client.fetch_live_overview_bundle()
    ls.set_meta("binance_live_bundle", live)

    acc_mode = str(live.get("account_mode") or "PAPI")
    inc = await client.fetch_um_income(since_sec=since_sec, account_mode=acc_mode)
    ls.upsert_binance_income(inc)

    active_syms = {
        str(r.get("symbol") or "").strip().upper() for r in inc if r.get("symbol")
    } | {
        str(p.get("symbol") or "").strip().upper()
        for p in (live.get("futures_positions") or [])
        if p.get("symbol")
    }
    if active_syms:
        fills = await client.fetch_um_trades_for_symbols(sorted(active_syms), account_mode=acc_mode)
        ords = await client.fetch_um_orders_for_symbols(sorted(active_syms), account_mode=acc_mode)
        ls.upsert_binance_fills(fills)
        ls.upsert_binance_orders(ords)

        cached_cr = ls.get_meta("binance_commission_rates", {})
        if not isinstance(cached_cr, dict):
            cached_cr = {}
        if do_full or not cached_cr:
            fresh_cr = await client.fetch_um_commission_rates(sorted(active_syms), account_mode=acc_mode)
            if fresh_cr:
                cached_cr.update(fresh_cr)
                ls.set_meta("binance_commission_rates", cached_cr)

    last_cf_sync = int(ls.get_meta("binance_cashflow_sync_sec", 0) or 0)
    cf_done = bool(ls.get_meta("binance_copy_cf_v1", False))
    if do_full or (not cf_done) or (now_s - last_cf_sync) >= 300:
        cf_full = do_full or (not cf_done)
        cf_since = (now_s - FULL_BACKFILL_LOOKBACK_SEC) if cf_full else since_sec
        cfs = await client.fetch_cashflows(since_sec=cf_since, do_full=cf_full)
        ls.upsert_binance_cashflows(cfs)
        ls.set_meta("binance_cashflow_sync_sec", now_s)
        if cf_full:
            ls.set_meta("binance_copy_cf_v1", True)

    cached_bnb_px = ls.get_meta("binance_bnb_prices", {})
    if not isinstance(cached_bnb_px, dict):
        cached_bnb_px = {}
    fresh_bnb_px = await client.fetch_bnb_daily_prices(full_history=do_full or (len(cached_bnb_px) < 30))
    if fresh_bnb_px:
        cached_bnb_px.update(fresh_bnb_px)
        ls.set_meta("binance_bnb_prices", cached_bnb_px)

    ls.set_meta("binance_backfill_v2", True)

    eq_val = sum(float(wb.get("balance") or 0.0) for wb in (live.get("wallet_balances") or []))
    sb_live = live.get("stock_bundle") or {}
    has_open = bool(
        [p for p in (live.get("futures_positions") or []) if abs(float(p.get("positionAmt") or 0.0)) > 1e-9]
        or live.get("open_orders")
        or live.get("futures_open_orders")
        or sb_live.get("equity_open_orders")
        or sb_live.get("equity_quotes")
    )
    fp_after = (
        len(ls.load_binance_fills()),
        len(ls.load_binance_orders()),
        len(ls.load_binance_income()),
        len(ls.load_binance_cashflows()),
    )
    return eq_val, has_open, fp_before, fp_after


async def sync_bitget(
    client: Any,
    ls: GateManualStore,
    *,
    now_s: int,
    force_full: bool = False,
) -> tuple[float, bool, tuple[int, ...], tuple[int, ...]]:
    backfill_done = bool(ls.get_meta("bitget_backfill_v3", False))
    do_full = force_full or (not backfill_done)
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        len(ls.load_bitget_positions_history()),
        len(ls.load_bitget_fills()),
        len(ls.load_bitget_bills()),
    )

    live = await client.fetch_live_overview_bundle()
    ls.set_meta("bitget_live_bundle", live)
    bg_mode = str(live.get("account_mode") or "UTA")

    pos_hist = await client.fetch_history_positions(since_sec=since_sec, account_mode=bg_mode)
    fills = await client.fetch_fills(since_sec=since_sec, account_mode=bg_mode)
    orders = await client.fetch_orders_history(since_sec=since_sec, account_mode=bg_mode)
    bills = await client.fetch_financial_bills(
        since_sec=since_sec,
        account_mode=bg_mode,
        include_tax_ledger=do_full,
    )
    ls.upsert_bitget_positions_history(pos_hist)
    ls.upsert_bitget_fills(fills)
    ls.upsert_bitget_orders(orders)
    ls.upsert_bitget_bills(bills)

    syms = {str(p.get("symbol") or "").strip().upper() for p in pos_hist if p.get("symbol")}
    if syms and (do_full or not ls.get_meta("bitget_fee_rates", None)):
        rates = await client.fetch_symbol_fee_rates(sorted(syms), account_mode=bg_mode)
        if rates:
            ls.set_meta("bitget_fee_rates", rates)

    ls.set_meta("bitget_backfill_v3", True)

    eq_val = 0.0
    uta_obj = live.get("uta_assets") or {}
    if isinstance(uta_obj, dict):
        eq_val = float(uta_obj.get("accountEquity") or uta_obj.get("usdtEquity") or 0.0)
    for fa_item in live.get("funding_assets") or []:
        c_fa = str(fa_item.get("coin") or "").upper()
        q_fa = float(fa_item.get("balance") or fa_item.get("available") or 0.0)
        if q_fa > 0:
            px_fa = (
                1.0
                if c_fa in ("USDT", "USDC", "FDUSD", "USD1", "USD")
                else float((live.get("spot_tickers") or {}).get(c_fa) or 0.0)
            )
            eq_val += q_fa * px_fa
    sb_live = live.get("stock_bundle") or {}
    sp_acc = sb_live.get("stockplus_account") or {}
    if isinstance(sp_acc, dict):
        eq_val += float(
            sp_acc.get("totalEquity")
            or sp_acc.get("netLiquidationValue")
            or sp_acc.get("equity")
            or sp_acc.get("cashBalance")
            or 0.0
        )
    has_open = bool(
        live.get("current_positions")
        or live.get("futures_positions")
        or live.get("open_orders")
        or live.get("futures_open_orders")
        or sb_live.get("stockplus_positions")
    )
    fp_after = (
        len(ls.load_bitget_positions_history()),
        len(ls.load_bitget_fills()),
        len(ls.load_bitget_bills()),
    )
    return eq_val, has_open, fp_before, fp_after


async def sync_mexc(
    client: Any,
    ls: GateManualStore,
    *,
    now_s: int,
    force_full: bool = False,
) -> tuple[float, bool, tuple[int, ...], tuple[int, ...]]:
    backfill_done = bool(ls.get_meta("mexc_backfill_v1", False))
    do_full = force_full or (not backfill_done)
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        len(ls.load_mexc_positions_history()),
        len(ls.load_mexc_deals()),
        len(ls.load_mexc_transfers()),
    )

    live = await client.fetch_live_overview_bundle()
    ls.set_meta("mexc_live_bundle", live)

    pos_hist = await client.fetch_history_positions(since_sec=since_sec, max_pages=20 if do_full else 3)
    deals = await client.fetch_order_deals(since_sec=since_sec, max_pages=30 if do_full else 4)
    orders = await client.fetch_history_orders(since_sec=since_sec, max_pages=30 if do_full else 4)
    funding = await client.fetch_funding_records(since_sec=since_sec, max_pages=15 if do_full else 3)
    last_cf = int(ls.get_meta("mexc_cashflow_sync_sec", 0) or 0)
    if do_full or (now_s - last_cf) >= 120:
        transfers = await client.fetch_cashflows_and_rebates(since_sec=since_sec, do_full=do_full)
        ls.upsert_mexc_transfers(transfers)
        ls.set_meta("mexc_cashflow_sync_sec", now_s)

    ls.upsert_mexc_positions_history(pos_hist)
    ls.upsert_mexc_deals(deals)
    ls.upsert_mexc_orders(orders)
    ls.upsert_mexc_funding(funding)

    syms = (
        {str(p.get("symbol") or "").strip().upper() for p in pos_hist if p.get("symbol")}
        | {str(d.get("symbol") or "").strip().upper() for d in deals if d.get("symbol")}
        | {str(p.get("symbol") or "").strip().upper() for p in (live.get("futures_positions") or []) if p.get("symbol")}
    )
    cached_specs = ls.get_meta("mexc_contract_specs", {})
    if not isinstance(cached_specs, dict):
        cached_specs = {}
    missing = sorted(syms - set(cached_specs.keys()))
    if missing or do_full:
        fresh_specs = await client.fetch_contract_specs(sorted(syms) if do_full else missing)
        if fresh_specs:
            cached_specs.update(fresh_specs)
            ls.set_meta("mexc_contract_specs", cached_specs)

    ls.set_meta("mexc_backfill_v1", True)

    eq_val = 0.0
    tickers = live.get("spot_tickers") or {}
    for fa_m in live.get("futures_assets") or []:
        c_m = str(fa_m.get("currency") or "").upper()
        eq_m = float(fa_m.get("equity") or fa_m.get("cashBalance") or 0.0)
        if eq_m > 0:
            px_m = 1.0 if c_m in ("USDT", "USDC", "USDE", "FDUSD", "USD") else float(tickers.get(c_m) or 0.0)
            eq_val += eq_m * px_m
    for sb_m in live.get("spot_balances") or []:
        c_sb = str(sb_m.get("asset") or "").upper()
        q_sb = float(sb_m.get("free") or 0.0) + float(sb_m.get("locked") or 0.0)
        if q_sb > 0:
            px_sb = 1.0 if c_sb in ("USDT", "USDC", "USDE", "FDUSD", "USD") else float(tickers.get(c_sb) or 0.0)
            eq_val += q_sb * px_sb
    has_open = bool(
        live.get("futures_positions") or live.get("open_orders") or live.get("futures_open_orders")
    )
    fp_after = (
        len(ls.load_mexc_positions_history()),
        len(ls.load_mexc_deals()),
        len(ls.load_mexc_transfers()),
    )
    return eq_val, has_open, fp_before, fp_after


LEGACY_VENUE_SYNCERS = {
    "gate": sync_gate,
    "easicoin": sync_easicoin,
    "binance": sync_binance,
    "bitget": sync_bitget,
    "mexc": sync_mexc,
}
