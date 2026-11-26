"""Independent read-only sync module for Gate (`venue_id = 'gate'`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence

from opentradeledger.adapters.base import VenueSyncOutcome
from opentradeledger.adapters.gate.client import GateApiHttpError
from opentradeledger.core.venue_primitives import fmt_bj

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

FULL_BACKFILL_LOOKBACK_SEC = 365 * 86400
INCREMENTAL_LOOKBACK_SEC = 7 * 86400


def _dedup_partner_rebates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    per_ts_occ: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: (int(x.get("commission_time") or 0), str(x.get("commission_amount") or ""))):
        uid = str(r.get("_rebate_uid") or "").strip()
        if not uid:
            base_k = f"{r.get('commission_time')}_{r.get('commission_amount')}_{r.get('commission_asset')}_{r.get('source')}"
            occ = per_ts_occ.get(base_k, 0) + 1
            per_ts_occ[base_k] = occ
            uid = f"{base_k}#{occ}"
        out.append({**dict(r), "_rebate_uid": uid})
    return out


async def sync_gate_venue(
    client: Any,
    store: "LedgerStore",
    *,
    now_s: int,
    force_full: bool = False,
) -> VenueSyncOutcome:
    scope = store.venue_scope("gate")
    backfill_done = bool(scope.get_kv("backfill_completed", False))
    do_full = force_full or (not backfill_done)
    since_sec = now_s - (FULL_BACKFILL_LOOKBACK_SEC if do_full else INCREMENTAL_LOOKBACK_SEC)

    fp_before = (
        scope.count_records("futures_closes"),
        scope.count_records("futures_trades"),
        scope.count_records("partner_rebates"),
        scope.count_records("spot_account_book"),
    )

    live_bundle = await client.fetch_live_overview_bundle()
    specs = scope.get_kv("contract_specs", {})
    fetched_specs = None
    if not specs or do_full:
        fetched_specs = await client.fetch_contract_specs()

    closes = await client.fetch_futures_closes(since_sec=since_sec)
    trades = await client.fetch_futures_trades(since_sec=since_sec)
    orders = await client.fetch_futures_finished_orders(since_sec=since_sec)
    rebates = _dedup_partner_rebates(await client.fetch_partner_rebates(since_sec=since_sec))
    full_spot_book = do_full or not scope.get_kv("spot_window_backfill_v2", True)
    spot_since_sec = now_s - FULL_BACKFILL_LOOKBACK_SEC if full_spot_book else since_sec
    spot_book = await client.fetch_spot_account_book(since_sec=spot_since_sec, max_pages=20 if full_spot_book else 4)
    spot_book_warnings = getattr(client, "spot_book_warnings", [])
    stk_orders, stk_deals = await client.fetch_stock_orders_and_deals(max_pages=10 if do_full else 2)
    stk_txs = await client.fetch_stock_transactions(max_pages=10 if do_full else 2)

    scope.upsert_records(
        "futures_closes",
        closes,
        key_fn=lambda r: f"{r.get('contract')}_{int(float(r.get('first_open_time') or 0))}_{int(float(r.get('time') or 0))}_{r.get('side')}",
        ts_fn=lambda r: float(r.get("time") or 0),
    )
    scope.upsert_records(
        "futures_trades",
        trades,
        key_fn=lambda r, i: str(r.get("id") or f"{r.get('order_id')}_{r.get('create_time')}_{i}"),
        ts_fn=lambda r: float(r.get("create_time") or 0),
    )
    scope.upsert_records(
        "futures_orders",
        [o for o in orders if o.get("id")],
        key_fn=lambda r: str(r.get("id") or ""),
        ts_fn=lambda r: float(r.get("create_time") or 0),
    )
    scope.upsert_records(
        "partner_rebates",
        rebates,
        key_fn=lambda r: str(r.get("_rebate_uid") or ""),
        ts_fn=lambda r: float(r.get("commission_time") or 0),
    )
    scope.upsert_records(
        "spot_account_book",
        spot_book,
        key_fn=lambda r, i: str(r.get("id") or f"{r.get('time')}_{r.get('type')}_{i}"),
        ts_fn=lambda r: float(r.get("time") or 0),
    )
    scope.upsert_records(
        "stock_orders",
        [o for o in stk_orders if o.get("id")],
        key_fn=lambda r: str(r.get("id") or ""),
        ts_fn=lambda r: float(r.get("create_time") or 0),
    )
    scope.upsert_records(
        "stock_deals",
        stk_deals,
        key_fn=lambda r, i: str(r.get("id") or f"{r.get('order_id')}_{i}"),
        ts_fn=lambda r: float(r.get("time") or r.get("create_time") or 0),
    )
    scope.upsert_records(
        "stock_transactions",
        stk_txs,
        key_fn=lambda r, i: str(r.get("ref_id") or f"{r.get('time')}_{r.get('type')}_{i}"),
        ts_fn=lambda r: float(r.get("time") or 0),
    )

    if fetched_specs:
        scope.set_kv("contract_specs", fetched_specs)
    prev_live = scope.get_kv("live_bundle")
    live_bundle = dict(live_bundle) if isinstance(live_bundle, dict) else {}
    failed_fields = [str(x) for x in (live_bundle.pop("_failed_fields", None) or ()) if x]
    warnings = [str(w) for w in (live_bundle.get("_warnings") or ()) if w]
    if spot_book_warnings:
        warnings.extend(spot_book_warnings)
        live_bundle["_warnings"] = warnings
    if not isinstance(prev_live, dict) and {"wallet_total", "unified", "futures_account"}.issubset(set(failed_fields)):
        raise GateApiHttpError(
            200,
            "/wallet/total_balance",
            "; ".join(warnings) or "Malformed Gate balance payload",
        )
    if isinstance(prev_live, dict) and failed_fields:
        for fk in failed_fields:
            if fk in prev_live and prev_live[fk]:
                live_bundle[fk] = prev_live[fk]
    scope.set_kv("live_bundle", live_bundle)
    scope.set_kv("backfill_completed", True)
    scope.set_kv("spot_window_backfill_v2", not spot_book_warnings)
    scope.set_kv("gate_decimal_trades_v2", True)

    wt_g = live_bundle.get("wallet_total") or {}
    wt_tot_obj = wt_g.get("total") if isinstance(wt_g, dict) else None
    has_wt_amount = isinstance(wt_tot_obj, dict) and wt_tot_obj.get("amount") is not None
    det_g = (wt_g.get("details") or {}) if isinstance(wt_g, dict) else {}
    stk_assets_g = live_bundle.get("stock_assets") or {}
    q_now = float((det_g.get("quant") or {}).get("amount") or 0.0)
    s_now = float(
        stk_assets_g.get("equity")
        or stk_assets_g.get("total_equity")
        or (det_g.get("tradfi_spot") or {}).get("amount")
        or 0.0
    )
    if has_wt_amount:
        eq_now = float(wt_tot_obj.get("amount") or 0.0)
    else:
        uni_g = live_bundle.get("unified") or {}
        fa_g = live_bundle.get("futures_account") or {}
        uni_eq_g = float(
            uni_g.get("unified_account_total_equity")
            or uni_g.get("total_equity_usd")
            or 0.0
        )
        if uni_eq_g <= 0 and isinstance(uni_g.get("balances"), dict):
            for ccy_u, b_u in uni_g["balances"].items():
                if not isinstance(b_u, dict):
                    continue
                eq_u = float(b_u.get("equity_usd") or 0.0)
                if eq_u > 0:
                    uni_eq_g += eq_u
                elif str(ccy_u).strip().upper() in ("USDT", "USDC", "USD1", "FDUSD", "USD"):
                    uni_eq_g += float(b_u.get("equity") or 0.0) or (
                        float(b_u.get("available") or 0.0) + float(b_u.get("freeze") or b_u.get("locked") or 0.0)
                    )
        fa_eq_g = (
            float(fa_g.get("total") or 0.0) + float(fa_g.get("unrealised_pnl") or 0.0)
            if isinstance(fa_g, dict) and fa_g.get("total") is not None
            else float(fa_g.get("available") or 0.0) if isinstance(fa_g, dict) else 0.0
        )
        uni_credit_g = bool(
            (isinstance(fa_g, dict) and fa_g.get("enable_credit"))
            or str(uni_g.get("mode") or "").strip().lower() in {"multi_currency", "portfolio", "single_currency"}
        )
        core_eq_g = max(uni_eq_g, fa_eq_g) if (uni_credit_g or uni_eq_g > 0) else (uni_eq_g + fa_eq_g)
        eq_now = core_eq_g + q_now + s_now
    if eq_now > 0:
        day_bj = fmt_bj(now_s, "%Y-%m-%d")
        scope.upsert_records(
            "eod_snapshots",
            [
                {
                    "day_bj": day_bj,
                    "ts_sec": int(now_s),
                    "total_equity": eq_now,
                    "quant_equity": q_now,
                    "stock_equity": s_now,
                }
            ],
            key_fn=lambda r: str(r.get("day_bj") or fmt_bj(float(r.get("ts_sec") or 0), "%Y-%m-%d")),
            ts_fn=lambda r: float(r.get("ts_sec") or 0),
        )
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
        scope.count_records("futures_closes"),
        scope.count_records("futures_trades"),
        scope.count_records("partner_rebates"),
        scope.count_records("spot_account_book"),
    )
    new_closed = max(0, int(fp_after[0]) - int(fp_before[0])) if sum(fp_before) > 0 else 0
    return VenueSyncOutcome(
        equity_usd=eq_now,
        has_open_activity=has_open,
        fp_before=fp_before,
        fp_after=fp_after,
        new_closed_delta=new_closed,
        mode="venue_sync",
        warnings=warnings,
    )
