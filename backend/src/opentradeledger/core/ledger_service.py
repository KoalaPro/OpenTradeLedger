"""Unified ledger normalization service: seamlessly serves Demo mode or multi-exchange live DBs."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import time
from typing import Any

from opentradeledger.adapters.bybit.client import normalize_bybit_closed_trades
from opentradeledger.adapters.okx.client import normalize_okx_closed_trades
from opentradeledger.adapters.registry import AdapterRegistry
from opentradeledger.config import UserPreferences
from opentradeledger.core.accounting_engine import build_full_accounting_report
from opentradeledger.core.demo_provider import build_demo_dashboard
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault

TZ_UTC8 = timezone(timedelta(hours=8))

STABLECOINS = {
    "USDT",
    "USDC",
    "USD",
    "FDUSD",
    "USDE",
    "USD1",
    "DAI",
    "BUSD",
    "TUSD",
    "PYUSD",
    "USDP",
    "EUR",
    "GBP",
    "CNY",
    "HKD",
    "JPY",
    "LDUSDT",
    "LDUSDC",
    "BFUSD",
}


def _build_global_spot_price_map(ls: Any) -> dict[str, float]:
    prices: dict[str, float] = {}
    for meta_key in ("bitget_live_bundle", "binance_live_bundle", "mexc_live_bundle"):
        bundle = ls.get_meta(meta_key, {})
        if not isinstance(bundle, dict):
            continue
        tickers = bundle.get("spot_tickers") or {}
        if isinstance(tickers, dict):
            for sym, raw_px in tickers.items():
                try:
                    px = float(raw_px)
                except (TypeError, ValueError):
                    continue
                if px > 0:
                    prices[str(sym).strip().upper()] = px
    ec_bundle = ls.get_meta("easicoin_live_bundle", {})
    if isinstance(ec_bundle, dict) and isinstance(ec_bundle.get("tickers"), dict):
        for sym, raw_px in (ec_bundle.get("tickers") or {}).items():
            s = str(sym).strip().upper()
            base = s[:-4] if s.endswith("USDT") and len(s) > 4 else s
            try:
                px = float(raw_px)
            except (TypeError, ValueError):
                continue
            if px > 0 and base not in prices:
                prices[base] = px
    bnb_px_map = ls.get_meta("binance_bnb_prices", {})
    if isinstance(bnb_px_map, dict) and bnb_px_map:
        latest_day = sorted(bnb_px_map.keys())[-1]
        try:
            bnb_px = float(bnb_px_map[latest_day])
            if bnb_px > 0:
                prices.setdefault("BNB", bnb_px)
        except (TypeError, ValueError):
            pass
    return prices


def _extract_spot_holdings(
    ls: Any,
    plugin_snapshots: dict[str, dict[str, Any]],
    registry: AdapterRegistry,
    stock_holding_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Aggregate non-stablecoin crypto spot holdings (>= $1) across all venues without double-counting stock tokens."""
    prices = _build_global_spot_price_map(ls)
    agg: dict[tuple[str, str], dict[str, Any]] = {}

    def _add(vid: str, raw_asset: str, qty: float, explicit_usd: float = 0.0, wallet_type: str = "SPOT") -> None:
        asset = str(raw_asset or "").strip().upper()
        if not asset or qty <= 0:
            return
        if asset.startswith("LD") and len(asset) > 4 and asset not in STABLECOINS:
            asset = asset[2:]
        if asset in STABLECOINS:
            return
        if (vid, asset) in stock_holding_keys:
            return
        px = prices.get(asset, 0.0)
        if explicit_usd > 0 and px <= 0:
            px = explicit_usd / qty
        val = explicit_usd if explicit_usd > 0 else (qty * px)
        if val < 1.0:
            return
        key = (vid, asset)
        meta = registry.get(vid).meta if registry.get(vid) else None
        if key not in agg:
            agg[key] = {
                "venue_id": vid,
                "venue_name": meta.display_name if meta else vid.upper(),
                "color_token": meta.color_token if meta else vid,
                "asset": asset,
                "qty": 0.0,
                "price_usd": px,
                "market_value_usd": 0.0,
                "wallet_type": wallet_type,
            }
        agg[key]["qty"] += qty
        agg[key]["market_value_usd"] += val
        if px > 0:
            agg[key]["price_usd"] = px

    # 1. Gate unified balances
    gate_b = ls.get_meta("live_bundle", {})
    if isinstance(gate_b, dict):
        uni_bals = ((gate_b.get("unified") or {}).get("balances")) or {}
        if isinstance(uni_bals, dict):
            for coin, row in uni_bals.items():
                if not isinstance(row, dict):
                    continue
                q = float(row.get("equity") or row.get("available") or 0.0) + float(row.get("freeze") or 0.0)
                if float(row.get("equity") or 0.0) > 0:
                    q = float(row.get("equity") or 0.0)
                _add("gate", coin, q, wallet_type="UNIFIED")

    # 2. Binance spot + PM balances
    bn_b = ls.get_meta("binance_live_bundle", {})
    if isinstance(bn_b, dict):
        for sb in bn_b.get("spot_balances") or []:
            q = float(sb.get("free") or 0.0) + float(sb.get("locked") or 0.0)
            _add("binance", str(sb.get("asset") or ""), q, wallet_type="SPOT")

    # 3. Bitget UTA & funding balances
    bg_b = ls.get_meta("bitget_live_bundle", {})
    if isinstance(bg_b, dict):
        for fa in bg_b.get("funding_assets") or []:
            q = float(fa.get("balance") or fa.get("available") or 0.0)
            _add("bitget", str(fa.get("coin") or ""), q, wallet_type="FUNDING")
        uta_assets = ((bg_b.get("uta_assets") or {}).get("assets")) or []
        for ua in uta_assets:
            q = float(ua.get("equity") or ua.get("balance") or ua.get("available") or 0.0)
            _add("bitget", str(ua.get("coin") or ""), q, wallet_type="UTA")

    # 4. MEXC spot balances
    mx_b = ls.get_meta("mexc_live_bundle", {})
    if isinstance(mx_b, dict):
        for sb in mx_b.get("spot_balances") or []:
            q = float(sb.get("free") or 0.0) + float(sb.get("locked") or 0.0)
            _add("mexc", str(sb.get("asset") or ""), q, wallet_type="SPOT")

    # 5. EasiCoin spot & funding balances
    ec_b = ls.get_meta("easicoin_live_bundle", {})
    if isinstance(ec_b, dict):
        for sb in ec_b.get("spot_balances") or []:
            q = float(sb.get("balance") or 0.0)
            _add("easicoin", str(sb.get("token_name") or sb.get("coin") or ""), q, wallet_type="SPOT")
        for fb in ec_b.get("funding_balances") or []:
            q = float(fb.get("balance") or 0.0)
            _add("easicoin", str(fb.get("token_name") or fb.get("coin") or ""), q, wallet_type="FUNDING")

    # 6. Plugin snapshots (OKX / Bybit)
    for vid, snap in plugin_snapshots.items():
        if vid == "okx":
            for bal in snap.get("balances") or []:
                for d in bal.get("details") or []:
                    q = float(d.get("eq") or d.get("cashBal") or 0.0)
                    usd = float(d.get("eqUsd") or 0.0)
                    _add("okx", str(d.get("ccy") or ""), q, explicit_usd=usd, wallet_type="UNIFIED")
            for fb in snap.get("funding") or []:
                q = float(fb.get("bal") or 0.0)
                _add("okx", str(fb.get("ccy") or ""), q, wallet_type="FUNDING")
        elif vid == "bybit":
            for acct in (snap.get("wallet") or {}).get("list") or []:
                for c in acct.get("coin") or []:
                    q = float(c.get("walletBalance") or 0.0)
                    usd = float(c.get("usdValue") or 0.0)
                    _add("bybit", str(c.get("coin") or ""), q, explicit_usd=usd, wallet_type="UNIFIED")
            for c in (snap.get("funding") or {}).get("balance") or []:
                q = float(c.get("walletBalance") or 0.0)
                _add("bybit", str(c.get("coin") or ""), q, wallet_type="FUNDING")

    items = []
    for row in agg.values():
        row["qty"] = round(row["qty"], 6)
        row["price_usd"] = round(row["price_usd"], 4)
        row["market_value_usd"] = round(row["market_value_usd"], 2)
        items.append(row)
    items.sort(key=lambda x: x["market_value_usd"], reverse=True)
    return items


def _fmt_ms(ms: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ_UTC8).strftime(fmt)


def _normalize_legacy_leg(raw: dict[str, Any], default_time_ms: int) -> dict[str, Any]:
    gross_fee = round(float(raw.get("gross_fee_usd") or 0.0), 4)
    disc_saved = round(float(raw.get("bnb_discount_saved_usd") or 0.0), 4)
    settled_reb = float(raw.get("settled_rebate_usd") or 0.0)
    pending_reb = float(raw.get("pending_rebate_usd") or 0.0)
    rebate = round(settled_reb + pending_reb, 4)
    net_fee = round(max(0.0, gross_fee - rebate), 4)
    mode_zh = str(raw.get("mode_zh") or "")
    is_maker = "挂单" in mode_zh or str(raw.get("role") or "").upper() == "MAKER"
    if "止损" in mode_zh:
        mode_code = "STOP_MARKET"
    elif "止盈" in mode_zh:
        mode_code = "TP_MARKET"
    elif "反手" in mode_zh:
        mode_code = "REVERSE_MARKET"
    elif is_maker:
        mode_code = "LIMIT_MAKER"
    else:
        mode_code = "MARKET_TAKER"
    status = str(raw.get("rebate_status") or "SETTLED").upper()
    if status not in {"SETTLED", "PENDING", "NONE"}:
        status = "SETTLED" if gross_fee > 0 else "NONE"
    return {
        "role": "MAKER" if is_maker else "TAKER",
        "mode_code": mode_code,
        "fee_bps": round(float(raw.get("fee_bps") or 0.0), 2),
        "time_ms": int(raw.get("time_ms") or default_time_ms),
        "time_str": str(raw.get("time_bj") or _fmt_ms(default_time_ms, "%m-%d %H:%M")),
        "avg_price": float(raw.get("avg_price") or 0.0),
        "notional_usd": round(float(raw.get("notional_usd") or 0.0), 2),
        "gross_fee_usd": gross_fee,
        "discount_saved_usd": disc_saved,
        "discount_asset": str(raw.get("fee_asset") or ""),
        "rebate_usd": rebate,
        "rebate_pct": round(float(raw.get("rebate_pct") or 0.0), 1),
        "net_fee_usd": net_fee,
        "rebate_status": status,
        "settled_at": str(raw.get("rebate_settled_bj") or ""),
    }


def _extract_plugin_venue_contribution(
    venue_id: str,
    snap: dict[str, Any],
    registry: AdapterRegistry,
) -> dict[str, Any]:
    meta = registry.get(venue_id).meta if registry.get(venue_id) else None
    disp_name = meta.display_name if meta else venue_id.upper()
    disp_zh = meta.display_name_zh if meta else disp_name
    color_tok = meta.color_token if meta else venue_id

    equity_usd = 0.0
    available_usd = 0.0
    principal_usd = 0.0
    open_positions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    maker_bps = 0.0
    taker_bps = 0.0

    if venue_id == "okx":
        for bal in snap.get("balances") or []:
            equity_usd += float(bal.get("totalEq") or 0.0)
            for d in bal.get("details") or []:
                if str(d.get("ccy") or "").upper() in {"USDT", "USDC"}:
                    available_usd += float(d.get("availBal") or 0.0)
        for fb in snap.get("funding") or []:
            if str(fb.get("ccy") or "").upper() in {"USDT", "USDC"}:
                equity_usd += float(fb.get("bal") or 0.0)
        dep = sum(float(x.get("amt") or 0.0) for x in (snap.get("deposits") or []) if str(x.get("ccy") or "").upper() in {"USDT", "USDC"})
        wd = sum(float(x.get("amt") or 0.0) for x in (snap.get("withdrawals") or []) if str(x.get("ccy") or "").upper() in {"USDT", "USDC"})
        principal_usd = round(dep - wd, 2)
        for frow in snap.get("trade_fee") or []:
            maker_bps = abs(float(frow.get("makerU") or frow.get("maker") or 0.0)) * 10_000
            taker_bps = abs(float(frow.get("takerU") or frow.get("taker") or 0.0)) * 10_000
            break
        for pos in snap.get("positions") or []:
            qty = abs(float(pos.get("pos") or 0.0))
            if qty <= 0:
                continue
            inst = str(pos.get("instId") or "").replace("-SWAP", "").replace("-", "_")
            side_raw = str(pos.get("posSide") or "long").upper()
            side = "SHORT" if side_raw == "SHORT" or float(pos.get("pos") or 0.0) < 0 else "LONG"
            open_positions.append(
                {
                    "venue_id": "okx",
                    "venue_name": disp_name,
                    "color_token": color_tok,
                    "symbol": inst,
                    "side": side,
                    "leverage": float(pos.get("lever") or 1.0),
                    "qty": qty,
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
            rebate_ratio=float(snap.get("observed_rebate_ratio") or 0.0),
        )
        for rt in raw_trades:
            rt["color_token"] = color_tok
            rt["open_time_str"] = _fmt_ms(rt["open_time_ms"])
            rt["close_time_str"] = _fmt_ms(rt["close_time_ms"])
            rt["close_date"] = _fmt_ms(rt["close_time_ms"], "%Y-%m-%d")
            rt["holding_sec"] = max(0, (rt["close_time_ms"] - rt["open_time_ms"]) // 1000)
            rt["effective_relief_pct"] = (
                round((rt["total_rebate_usd"] / rt["total_fee_usd"]) * 100.0, 1)
                if rt["total_fee_usd"] > 0
                else 0.0
            )
            rt["entry_leg"]["time_str"] = _fmt_ms(rt["entry_leg"]["time_ms"], "%m-%d %H:%M")
            rt["entry_leg"]["settled_at"] = ""
            rt["exit_leg"]["time_str"] = _fmt_ms(rt["exit_leg"]["time_ms"], "%m-%d %H:%M")
            rt["exit_leg"]["settled_at"] = ""
            rt["tags"] = []
            rt["note"] = ""
            trades.append(rt)

    elif venue_id == "bybit":
        wallet = snap.get("wallet") or {}
        for acct in wallet.get("list") or []:
            equity_usd += float(acct.get("totalEquity") or 0.0)
            available_usd += float(acct.get("totalAvailableBalance") or 0.0)
        funding = snap.get("funding") or {}
        for coin in funding.get("balance") or []:
            if str(coin.get("coin") or "").upper() in {"USDT", "USDC"}:
                equity_usd += float(coin.get("walletBalance") or 0.0)
        dep = sum(float(x.get("amount") or 0.0) for x in (snap.get("deposits") or []) if str(x.get("coin") or "").upper() in {"USDT", "USDC"})
        wd = sum(float(x.get("amount") or 0.0) for x in (snap.get("withdrawals") or []) if str(x.get("coin") or "").upper() in {"USDT", "USDC"})
        principal_usd = round(dep - wd, 2)
        for frow in snap.get("fee_rates") or []:
            maker_bps = abs(float(frow.get("makerFeeRate") or 0.0)) * 10_000
            taker_bps = abs(float(frow.get("takerFeeRate") or 0.0)) * 10_000
            break
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
                    "venue_name": disp_name,
                    "color_token": color_tok,
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
            rebate_ratio=float(snap.get("observed_rebate_ratio") or 0.0),
        )
        for rt in raw_trades:
            rt["color_token"] = color_tok
            rt["open_time_str"] = _fmt_ms(rt["open_time_ms"])
            rt["close_time_str"] = _fmt_ms(rt["close_time_ms"])
            rt["close_date"] = _fmt_ms(rt["close_time_ms"], "%Y-%m-%d")
            rt["holding_sec"] = max(0, (rt["close_time_ms"] - rt["open_time_ms"]) // 1000)
            rt["effective_relief_pct"] = (
                round((rt["total_rebate_usd"] / rt["total_fee_usd"]) * 100.0, 1)
                if rt["total_fee_usd"] > 0
                else 0.0
            )
            rt["entry_leg"]["time_str"] = _fmt_ms(rt["entry_leg"]["time_ms"], "%m-%d %H:%M")
            rt["entry_leg"]["settled_at"] = ""
            rt["exit_leg"]["time_str"] = _fmt_ms(rt["exit_leg"]["time_ms"], "%m-%d %H:%M")
            rt["exit_leg"]["settled_at"] = ""
            rt["tags"] = []
            rt["note"] = ""
            trades.append(rt)

    net_pnl = round(equity_usd - principal_usd, 2) if principal_usd != 0 else round(sum(t["net_pnl_usd"] for t in trades), 2)
    return {
        "exchange": {
            "venue_id": venue_id,
            "display_name": disp_name,
            "display_name_zh": disp_zh,
            "color_token": color_tok,
            "connected": True,
            "equity_usd": round(equity_usd, 2),
            "net_principal_usd": round(principal_usd, 2),
            "net_pnl_usd": net_pnl,
            "available_margin_usd": round(available_usd, 2),
            "trade_count": len(trades),
            "rebate_rate_pct": round(float(snap.get("observed_rebate_ratio") or 0.0) * 100.0, 1),
            "discount_rate_pct": 0.0,
            "capabilities": meta.capabilities.model_dump() if meta else {},
        },
        "open_positions": open_positions,
        "trades": trades,
        "maker_bps": round(maker_bps, 2),
        "taker_bps": round(taker_bps, 2),
    }


class LedgerService:
    """Produces unified plugin-ready dashboard payloads from Demo or live local SQLite."""

    def __init__(
        self,
        store: LedgerStore,
        vault: CredentialVault,
        registry: AdapterRegistry,
        *,
        default_demo_mode: bool = False,
    ) -> None:
        self.store = store
        self.vault = vault
        self.registry = registry
        self.demo_mode = default_demo_mode
        self.syncing = False
        self.last_sync_ms = 0

    def has_live_data(self) -> bool:
        if self.vault.list_decrypted():
            return True
        if self.store.load_plugin_snapshots():
            return True
        live_bundle = self.store.legacy.get_meta("live_bundle", {})
        if isinstance(live_bundle, dict) and live_bundle:
            return True
        return bool(self.store.legacy.load_futures_closes())

    def is_demo_active(self) -> bool:
        return self.demo_mode or (not self.has_live_data())

    def get_preferences(self) -> UserPreferences:
        return self.store.get_preferences()

    def update_preferences(self, patch: dict[str, Any]) -> UserPreferences:
        current = self.store.get_preferences().model_dump()
        for key, value in patch.items():
            if key in {"modules", "notifications"} and isinstance(value, dict):
                current[key] = {**current.get(key, {}), **value}
            elif key in current:
                current[key] = value
        updated = UserPreferences.model_validate(current)
        return self.store.save_preferences(updated)

    def save_journal(self, trade_id: str, tags: list[str], note: str) -> None:
        self.store.save_journal(trade_id, tags, note)

    def get_dashboard(
        self,
        *,
        range_key: str = "all",
        venue_filter: str = "ALL",
        symbol_filter: str = "ALL",
        force_demo: bool | None = None,
    ) -> dict[str, Any]:
        prefs = self.get_preferences()
        journals = self.store.load_journals()
        latest_ai = self.store.load_latest_ai_review()
        use_demo = force_demo if force_demo is not None else self.is_demo_active()
        if use_demo:
            return build_demo_dashboard(
                range_key=range_key,
                venue_filter=venue_filter,
                symbol_filter=symbol_filter,
                prefs=prefs,
                journals=journals,
                latest_ai_review=latest_ai,
            )
        return self._build_live_dashboard(
            range_key=range_key,
            venue_filter=venue_filter,
            symbol_filter=symbol_filter,
            prefs=prefs,
            journals=journals,
            latest_ai_review=latest_ai,
        )

    def _build_live_dashboard(
        self,
        *,
        range_key: str,
        venue_filter: str,
        symbol_filter: str,
        prefs: UserPreferences,
        journals: dict[str, dict[str, Any]],
        latest_ai_review: dict[str, Any] | None,
    ) -> dict[str, Any]:
        ls = self.store.legacy
        now_s = time.time()
        legacy_ex_filter = (
            venue_filter.upper()
            if venue_filter.upper() in {"ALL", "GATE", "EASICOIN", "BINANCE", "BITGET", "MEXC"}
            else "ALL"
        )
        be_cfg = ls.get_meta("breakeven_config", None)
        if prefs.goal_enabled and prefs.goal_target_usd and prefs.goal_target_usd > 0:
            be_cfg = {**(be_cfg if isinstance(be_cfg, dict) else {}), "target_usd": float(prefs.goal_target_usd)}

        stk_orders, stk_deals, stk_txs = ls.load_stock_records()
        legacy_report = build_full_accounting_report(
            live_bundle=ls.get_meta("live_bundle", {}) or {},
            futures_closes=ls.load_futures_closes(),
            futures_trades=ls.load_futures_trades(),
            futures_finished_orders=ls.load_futures_orders(),
            partner_rebates=ls.load_partner_rebates(),
            spot_account_book=ls.load_spot_account_book(),
            stock_orders_history=stk_orders,
            stock_deals=stk_deals,
            stock_transactions=stk_txs,
            contract_specs=ls.get_meta("contract_specs", {}) or {},
            journals=journals,
            copy_snapshots=ls.load_equity_snapshots(),
            breakeven_config=be_cfg if isinstance(be_cfg, dict) else None,
            easicoin_live_bundle=ls.get_meta("easicoin_live_bundle", None),
            easicoin_closed_pnl=ls.load_easicoin_closed_pnl(),
            easicoin_fills=ls.load_easicoin_fills(),
            easicoin_orders=ls.load_easicoin_orders(),
            easicoin_transfers=ls.load_easicoin_transfers(),
            binance_live_bundle=ls.get_meta("binance_live_bundle", None),
            binance_income=ls.load_binance_income(),
            binance_fills=ls.load_binance_fills(),
            binance_orders=ls.load_binance_orders(),
            binance_cashflows=ls.load_binance_cashflows(),
            binance_bnb_prices=ls.get_meta("binance_bnb_prices", {}),
            binance_commission_rates=ls.get_meta("binance_commission_rates", {}),
            bitget_live_bundle=ls.get_meta("bitget_live_bundle", None),
            bitget_positions_history=ls.load_bitget_positions_history(),
            bitget_fills=ls.load_bitget_fills(),
            bitget_orders=ls.load_bitget_orders(),
            bitget_bills=ls.load_bitget_bills(),
            bitget_fee_rates=ls.get_meta("bitget_fee_rates", {}),
            mexc_live_bundle=ls.get_meta("mexc_live_bundle", None),
            mexc_positions_history=ls.load_mexc_positions_history(),
            mexc_deals=ls.load_mexc_deals(),
            mexc_orders=ls.load_mexc_orders(),
            mexc_funding=ls.load_mexc_funding(),
            mexc_transfers=ls.load_mexc_transfers(),
            mexc_contract_specs=ls.get_meta("mexc_contract_specs", {}),
            now_sec=now_s,
            range_key=range_key,
            contract_filter=symbol_filter,
            exchange_filter=legacy_ex_filter,
        )

        # Build dynamic exchanges[] array from legacy report + new plugin snapshots (OKX / Bybit / ...)
        acc = legacy_report.get("account", {})
        wa = legacy_report.get("whole_account", {})
        fr = legacy_report.get("fee_rates", {})
        ex_counts = legacy_report.get("exchange_counts", {})

        exchanges: list[dict[str, Any]] = []

        venue_health_map = ls.get_meta("venue_health", {})
        if not isinstance(venue_health_map, dict):
            venue_health_map = {}

        def _append_legacy_venue(
            vid: str,
            present: bool,
            eq: float,
            prin: float,
            pnl: float,
            avail: float,
            reb: float,
            disc: float,
        ) -> None:
            if not present:
                return
            meta = self.registry.get(vid).meta if self.registry.get(vid) else None
            vh = venue_health_map.get(vid) if isinstance(venue_health_map.get(vid), dict) else {}
            is_err = vh.get("status") == "error"
            exchanges.append(
                {
                    "venue_id": vid,
                    "display_name": meta.display_name if meta else vid.upper(),
                    "display_name_zh": meta.display_name_zh if meta else vid.upper(),
                    "color_token": meta.color_token if meta else vid,
                    "connected": not is_err,
                    "health_status": "error" if is_err else "connected",
                    "error_reason": str(vh.get("error_reason") or "") if is_err else "",
                    "error_reason_zh": str(vh.get("error_reason_zh") or "") if is_err else "",
                    "equity_usd": round(float(eq or 0.0), 2),
                    "net_principal_usd": round(float(prin or 0.0), 2),
                    "net_pnl_usd": round(float(pnl or 0.0), 2),
                    "available_margin_usd": round(float(avail or 0.0), 2),
                    "trade_count": int(ex_counts.get(vid.upper(), 0)),
                    "rebate_rate_pct": round(float(reb or 0.0), 1),
                    "discount_rate_pct": round(float(disc or 0.0), 1),
                    "capabilities": meta.capabilities.model_dump() if meta else {},
                }
            )

        has_gate = bool(ls.get_meta("live_bundle", {})) or float(acc.get("gate_equity_usd") or 0) > 0
        _append_legacy_venue(
            "gate",
            has_gate,
            acc.get("gate_equity_usd", acc.get("total_equity_usd", 0.0)),
            wa.get("gate_net_principal_usd", wa.get("net_principal_usd", 0.0)),
            wa.get("gate_only_net_pnl_usd", wa.get("gate_net_pnl_usd", 0.0)),
            acc.get("gate_futures_available_usd", acc.get("futures_available_usd", 0.0)),
            fr.get("futures_taker_rebate_pct", 0.0),
            0.0,
        )
        _append_legacy_venue(
            "easicoin",
            bool(legacy_report.get("has_easicoin")),
            acc.get("easicoin_equity_usd", 0.0),
            wa.get("easicoin_net_principal_usd", 0.0),
            wa.get("easicoin_net_pnl_usd", 0.0),
            acc.get("easicoin_available_usd", 0.0),
            fr.get("easicoin_rebate_pct", 0.0),
            0.0,
        )
        _append_legacy_venue(
            "binance",
            bool(legacy_report.get("has_binance")),
            acc.get("binance_equity_usd", 0.0),
            wa.get("binance_net_principal_usd", 0.0),
            wa.get("binance_net_pnl_usd", 0.0),
            acc.get("binance_available_usd", 0.0),
            fr.get("binance_rebate_pct", 0.0),
            fr.get("binance_bnb_discount_pct", 0.0),
        )
        _append_legacy_venue(
            "bitget",
            bool(legacy_report.get("has_bitget")),
            acc.get("bitget_equity_usd", 0.0),
            wa.get("bitget_net_principal_usd", 0.0),
            wa.get("bitget_net_pnl_usd", 0.0),
            acc.get("bitget_available_usd", 0.0),
            fr.get("bitget_rebate_pct", 0.0),
            0.0,
        )
        _append_legacy_venue(
            "mexc",
            bool(legacy_report.get("has_mexc")),
            acc.get("mexc_equity_usd", 0.0),
            wa.get("mexc_net_principal_usd", 0.0),
            wa.get("mexc_net_pnl_usd", 0.0),
            acc.get("mexc_available_usd", 0.0),
            fr.get("mexc_relief_pct", 0.0),
            fr.get("mexc_mx_discount_pct", 0.0),
        )

        # Normalize legacy trades with explicit per-leg entry & exit fees
        trades: list[dict[str, Any]] = []
        if venue_filter.upper() in {"ALL", "GATE", "EASICOIN", "BINANCE", "BITGET", "MEXC"}:
            for r in legacy_report.get("closed_records") or []:
                vid = str(r.get("exchange") or "GATE").lower()
                meta = self.registry.get(vid).meta if self.registry.get(vid) else None
                open_ms = int(r.get("open_time_ms") or int(float(r.get("open_time_sec") or 0) * 1000))
                close_ms = int(r.get("close_time_ms") or int(float(r.get("close_time_sec") or 0) * 1000))
                entry_leg = _normalize_legacy_leg(r.get("entry_leg") or {}, open_ms)
                exit_leg = _normalize_legacy_leg(r.get("exit_leg") or {}, close_ms)
                total_reb = round(float(r.get("settled_rebate_usd") or 0.0) + float(r.get("pending_rebate_usd") or 0.0), 4)
                total_disc = round(float(r.get("bnb_discount_saved_usd") or 0.0), 4)
                total_fee = round(float(r.get("total_fee_usd") or 0.0), 4)
                net_fee = round(float(r.get("net_fee_usd") or (entry_leg["net_fee_usd"] + exit_leg["net_fee_usd"])), 4)
                base_fee = total_fee + total_disc
                relief = round(((total_reb + total_disc) / base_fee) * 100.0, 1) if base_fee > 0 else 0.0
                trades.append(
                    {
                        "trade_id": str(r.get("record_key")),
                        "venue_id": vid,
                        "venue_name": meta.display_name if meta else vid.upper(),
                        "color_token": meta.color_token if meta else vid,
                        "symbol": str(r.get("contract") or ""),
                        "side": "SHORT" if str(r.get("side") or "").upper() == "SHORT" else "LONG",
                        "qty": float(r.get("qty_base") or 0.0),
                        "open_time_ms": open_ms,
                        "close_time_ms": close_ms,
                        "open_time_str": str(r.get("open_datetime_bj") or _fmt_ms(open_ms))[:16],
                        "close_time_str": str(r.get("close_datetime_bj") or _fmt_ms(close_ms))[:16],
                        "close_date": str(r.get("close_date_bj") or _fmt_ms(close_ms, "%Y-%m-%d")),
                        "holding_sec": int(
                            r.get("holding_seconds")
                            or r.get("holding_sec")
                            or max(0, (close_ms - open_ms) // 1000)
                        ),
                        "is_partial_close": bool(r.get("is_partial_close")),
                        "gross_pnl_usd": round(float(r.get("gross_pnl_usd") or 0.0), 4),
                        "funding_pnl_usd": round(float(r.get("funding_pnl_usd") or 0.0), 4),
                        "book_pnl_usd": round(float(r.get("gate_book_pnl_usd") or 0.0), 4),
                        "total_fee_usd": total_fee,
                        "total_rebate_usd": total_reb,
                        "total_discount_saved_usd": total_disc,
                        "net_fee_usd": net_fee,
                        "net_pnl_usd": round(float(r.get("expected_net_pnl_usd") or 0.0), 4),
                        "effective_relief_pct": relief,
                        "rebate_status": "PENDING" if str(r.get("rebate_state")) != "SETTLED" else "SETTLED",
                        "entry_leg": entry_leg,
                        "exit_leg": exit_leg,
                        "tags": list(r.get("tags") or []),
                        "note": str(r.get("note") or ""),
                    }
                )

        # Merge native plugin snapshots (OKX / Bybit / future community plugins)
        open_positions: list[dict[str, Any]] = []
        for pos in (legacy_report.get("live") or {}).get("futures_positions") or []:
            vid = str(pos.get("exchange") or "GATE").lower()
            meta = self.registry.get(vid).meta if self.registry.get(vid) else None
            lev = max(1.0, float(pos.get("leverage") or 1.0))
            notional = round(float(pos.get("notional_usd") or 0.0), 2)
            upnl = round(float(pos.get("unrealized_pnl_usd") or 0.0), 2)
            raw_roi = pos.get("unrealized_pnl_pct") or pos.get("roi_pct")
            if raw_roi is not None and float(raw_roi or 0.0) != 0.0:
                uroi = round(float(raw_roi), 2)
            elif notional > 0:
                uroi = round((upnl / (notional / lev)) * 100.0, 2)
            else:
                uroi = 0.0
            open_positions.append(
                {
                    "venue_id": vid,
                    "venue_name": meta.display_name if meta else vid.upper(),
                    "color_token": meta.color_token if meta else vid,
                    "symbol": str(pos.get("contract") or ""),
                    "side": "SHORT" if str(pos.get("side") or "").upper() == "SHORT" else "LONG",
                    "leverage": lev,
                    "qty": float(pos.get("qty_base") or 0.0),
                    "notional_usd": notional,
                    "entry_price": float(pos.get("entry_price") or 0.0),
                    "mark_price": float(pos.get("mark_price") or 0.0),
                    "liq_price": float(pos.get("liq_price") or 0.0),
                    "unrealized_pnl_usd": upnl,
                    "unrealized_roi_pct": uroi,
                    "has_stop_order": bool(pos.get("has_stop_order")),
                }
            )

        plugin_extra_equity = 0.0
        plugin_extra_principal = 0.0
        plugin_extra_pnl = 0.0
        plugin_fee_rows: list[dict[str, Any]] = []
        plugin_reconcile_items: list[dict[str, Any]] = []

        plugin_snapshots = self.store.load_plugin_snapshots()
        for vid, snap in plugin_snapshots.items():
            contrib = _extract_plugin_venue_contribution(vid, snap, self.registry)
            ex_row = contrib["exchange"]
            vh = venue_health_map.get(vid) if isinstance(venue_health_map.get(vid), dict) else {}
            is_err = vh.get("status") == "error"
            ex_row["connected"] = not is_err
            ex_row["health_status"] = "error" if is_err else "connected"
            ex_row["error_reason"] = str(vh.get("error_reason") or "") if is_err else ""
            ex_row["error_reason_zh"] = str(vh.get("error_reason_zh") or "") if is_err else ""
            exchanges.append(ex_row)
            plugin_extra_equity += ex_row["equity_usd"]
            plugin_extra_principal += ex_row["net_principal_usd"]
            plugin_extra_pnl += ex_row["net_pnl_usd"]
            open_positions.extend(contrib["open_positions"])
            for tr in contrib["trades"]:
                if tr["trade_id"] in journals:
                    tr["tags"] = journals[tr["trade_id"]].get("tags", [])
                    tr["note"] = journals[tr["trade_id"]].get("note", "")
                if venue_filter.upper() in {"ALL", vid.upper()} and symbol_filter.upper() in {"ALL", tr["symbol"].upper()}:
                    trades.append(tr)
            if contrib["trades"] or contrib["maker_bps"] > 0 or contrib["taker_bps"] > 0:
                v_gross = round(sum(t["total_fee_usd"] for t in contrib["trades"]), 2)
                v_reb = round(sum(t["total_rebate_usd"] for t in contrib["trades"]), 2)
                v_net = round(sum(t["net_fee_usd"] for t in contrib["trades"]), 2)
                v_reb_pct = round((v_reb / v_gross) * 100.0, 1) if v_gross > 0 else ex_row["rebate_rate_pct"]
                plugin_fee_rows.append(
                    {
                        "venue_id": vid,
                        "display_name": ex_row["display_name"],
                        "color_token": ex_row["color_token"],
                        "trade_count": len(contrib["trades"]),
                        "maker_gross_bps": contrib["maker_bps"],
                        "maker_net_bps": round(contrib["maker_bps"] * (1.0 - v_reb_pct / 100.0), 2),
                        "taker_gross_bps": contrib["taker_bps"],
                        "taker_net_bps": round(contrib["taker_bps"] * (1.0 - v_reb_pct / 100.0), 2),
                        "gross_fee_usd": v_gross,
                        "discount_saved_usd": 0.0,
                        "discount_label": "",
                        "rebate_usd": v_reb,
                        "rebate_pct": v_reb_pct,
                        "net_fee_usd": v_net,
                        "combined_relief_pct": v_reb_pct,
                        "potential_maker_savings_usd": 0.0,
                    }
                )
            if abs(ex_row["net_pnl_usd"]) > 0.001:
                plugin_reconcile_items.append(
                    {
                        "key": f"plugin_{vid}",
                        "label": f"{ex_row['display_name']} Net PnL",
                        "label_zh": f"{ex_row['display_name_zh']} 账户综合净盈亏",
                        "detail": f"{len(contrib['trades'])} closed trades · equity {ex_row['equity_usd']:.2f} U",
                        "detail_zh": f"{len(contrib['trades'])} 笔平仓 · 权益 {ex_row['equity_usd']:.2f} U",
                        "amount_usd": ex_row["net_pnl_usd"],
                    }
                )

        trades.sort(key=lambda t: (t["close_time_ms"], t["open_time_ms"], t["trade_id"]), reverse=True)

        # Normalize fee_matrix from legacy fee_actuarial + plugins
        fee_matrix: list[dict[str, Any]] = []
        fa = legacy_report.get("fee_actuarial") or {}
        if has_gate and isinstance(fa.get("gate"), dict):
            g = fa["gate"]
            gross = round(float(g.get("maker_gross_fee_usd", 0)) + float(g.get("taker_gross_fee_usd", 0)), 2)
            reb = round(float(g.get("maker_rebate_usd", 0)) + float(g.get("taker_rebate_usd", 0)), 2)
            fee_matrix.append(
                {
                    "venue_id": "gate",
                    "display_name": "Gate",
                    "color_token": "gate",
                    "trade_count": int(g.get("trade_count", 0)),
                    "maker_gross_bps": round(float(g.get("maker_gross_bps", 0)), 2),
                    "maker_net_bps": round(float(g.get("maker_net_bps", 0)), 2),
                    "taker_gross_bps": round(float(g.get("taker_gross_bps", 0)), 2),
                    "taker_net_bps": round(float(g.get("taker_net_bps", 0)), 2),
                    "gross_fee_usd": gross,
                    "discount_saved_usd": 0.0,
                    "discount_label": "",
                    "rebate_usd": reb,
                    "rebate_pct": round((reb / gross) * 100.0, 1) if gross > 0 else 0.0,
                    "net_fee_usd": round(max(0.0, gross - reb), 2),
                    "combined_relief_pct": round((reb / gross) * 100.0, 1) if gross > 0 else 0.0,
                    "potential_maker_savings_usd": round(float(g.get("potential_maker_savings_usd", 0)), 2),
                }
            )
        if legacy_report.get("has_binance") and isinstance(fa.get("binance"), dict):
            b = fa["binance"]
            fee_matrix.append(
                {
                    "venue_id": "binance",
                    "display_name": "Binance",
                    "color_token": "binance",
                    "trade_count": int(b.get("trade_count", 0)),
                    "maker_gross_bps": round(float(b.get("crypto_maker_gross_bps", 0)), 2),
                    "maker_net_bps": round(float(b.get("crypto_maker_gross_bps", 0)) * (1 - float(b.get("overall_rebate_pct", 0)) / 100), 2),
                    "taker_gross_bps": round(float(b.get("crypto_taker_gross_bps", 0)), 2),
                    "taker_net_bps": round(float(b.get("crypto_taker_net_bps", 0)), 2),
                    "gross_fee_usd": round(float(b.get("total_gross_fee_usd", 0)), 2),
                    "discount_saved_usd": round(float(b.get("bnb_discount_saved_usd", 0)), 2),
                    "discount_label": "BNB",
                    "rebate_usd": round(float(b.get("total_rebate_usd", 0)), 2),
                    "rebate_pct": round(float(b.get("overall_rebate_pct", 0)), 1),
                    "net_fee_usd": round(float(b.get("total_net_fee_usd", 0)), 2),
                    "combined_relief_pct": round(float(b.get("combined_relief_pct", 0)), 1),
                    "potential_maker_savings_usd": round(float(b.get("potential_maker_savings_usd", 0)), 2),
                }
            )
        if legacy_report.get("has_bitget") and isinstance(fa.get("bitget"), dict):
            bg = fa["bitget"]
            fee_matrix.append(
                {
                    "venue_id": "bitget",
                    "display_name": "Bitget",
                    "color_token": "bitget",
                    "trade_count": int(bg.get("trade_count", 0)),
                    "maker_gross_bps": round(float(bg.get("maker_gross_bps", 0)), 2),
                    "maker_net_bps": round(float(bg.get("maker_gross_bps", 0)) * (1 - float(bg.get("overall_rebate_pct", 0)) / 100), 2),
                    "taker_gross_bps": round(float(bg.get("taker_gross_bps", 0)), 2),
                    "taker_net_bps": round(float(bg.get("taker_net_bps", 0)), 2),
                    "gross_fee_usd": round(float(bg.get("total_gross_fee_usd", 0)), 2),
                    "discount_saved_usd": round(float(bg.get("bgb_discount_saved_usd", 0)), 2),
                    "discount_label": "BGB" if float(bg.get("bgb_discount_saved_usd", 0)) > 0 else "",
                    "rebate_usd": round(float(bg.get("total_rebate_usd", 0)), 2),
                    "rebate_pct": round(float(bg.get("overall_rebate_pct", 0)), 1),
                    "net_fee_usd": round(float(bg.get("total_net_fee_usd", 0)), 2),
                    "combined_relief_pct": round(float(bg.get("overall_rebate_pct", 0)), 1),
                    "potential_maker_savings_usd": round(float(bg.get("potential_maker_savings_usd", 0)), 2),
                }
            )
        if legacy_report.get("has_mexc") and isinstance(fa.get("mexc"), dict):
            mx = fa["mexc"]
            fee_matrix.append(
                {
                    "venue_id": "mexc",
                    "display_name": "MEXC",
                    "color_token": "mexc",
                    "trade_count": int(mx.get("trade_count", 0)),
                    "maker_gross_bps": round(float(mx.get("maker_gross_bps", 0)), 2),
                    "maker_net_bps": round(float(mx.get("maker_gross_bps", 0)), 2),
                    "taker_gross_bps": round(float(mx.get("taker_gross_bps", 0)), 2),
                    "taker_net_bps": round(float(mx.get("taker_net_bps", 0)), 2),
                    "gross_fee_usd": round(float(mx.get("total_gross_fee_usd", 0)), 2),
                    "discount_saved_usd": round(float(mx.get("mx_discount_saved_usd", 0)) + float(mx.get("mxpoint_relief_usd", 0)), 2),
                    "discount_label": "MX/Bonus",
                    "rebate_usd": round(float(mx.get("mxpoint_relief_usd", 0)), 2),
                    "rebate_pct": round(float(mx.get("combined_relief_pct", 0)), 1),
                    "net_fee_usd": round(float(mx.get("total_net_fee_usd", 0)), 2),
                    "combined_relief_pct": round(float(mx.get("combined_relief_pct", 0)), 1),
                    "potential_maker_savings_usd": round(float(mx.get("potential_maker_savings_usd", 0)), 2),
                }
            )
        if legacy_report.get("has_easicoin") and isinstance(fa.get("easicoin"), dict):
            ec = fa["easicoin"]
            fee_matrix.append(
                {
                    "venue_id": "easicoin",
                    "display_name": "EasiCoin",
                    "color_token": "easicoin",
                    "trade_count": int(ec.get("trade_count", 0)),
                    "maker_gross_bps": round(float(ec.get("crypto_maker_gross_bps", 0)), 2),
                    "maker_net_bps": round(float(ec.get("crypto_net_bps", 0)), 2),
                    "taker_gross_bps": round(float(ec.get("crypto_taker_gross_bps", 0)), 2),
                    "taker_net_bps": round(float(ec.get("crypto_net_bps", 0)), 2),
                    "gross_fee_usd": round(float(ec.get("total_gross_fee_usd", 0)), 2),
                    "discount_saved_usd": 0.0,
                    "discount_label": "",
                    "rebate_usd": round(float(ec.get("total_rebate_usd", 0)), 2),
                    "rebate_pct": round(float(ec.get("overall_rebate_pct", 0)), 1),
                    "net_fee_usd": round(float(ec.get("total_net_fee_usd", 0)), 2),
                    "combined_relief_pct": round(float(ec.get("overall_rebate_pct", 0)), 1),
                    "potential_maker_savings_usd": round(float(ec.get("potential_maker_savings_usd", 0)), 2),
                }
            )
        fee_matrix.extend(plugin_fee_rows)

        total_equity = round(float(acc.get("total_equity_usd") or 0.0) + plugin_extra_equity, 2)
        total_principal = round(float(wa.get("net_principal_usd") or 0.0) + plugin_extra_principal, 2)
        whole_net = round(float(wa.get("gate_net_pnl_usd") or 0.0) + plugin_extra_pnl, 2)
        summary = legacy_report.get("summary") or {}

        _en_reconcile_map = {
            "futures_net": "Futures Realized Net PnL (incl. Rebates & Funding)",
            "copy_trading": "Copy Trading Combined Net PnL",
            "stock": "Equities & Tokenized Stocks Net PnL",
            "spot_cashflow": "Spot / Earn / Bonus & Dust Adjustments",
            "unrealized": "Open Positions Unrealized PnL",
            "easicoin": "EasiCoin Account Net PnL",
            "binance": "Binance Account Net PnL",
            "bitget": "Bitget Account Net PnL",
            "mexc": "MEXC Account Net PnL",
        }
        reconcile_items = [
            {
                "key": str(it.get("key") or idx),
                "label": str(
                    it.get("label")
                    or _en_reconcile_map.get(str(it.get("key") or ""))
                    or it.get("label_zh")
                    or ""
                ),
                "label_zh": str(it.get("label_zh") or ""),
                "detail": str(it.get("detail") or it.get("detail_zh") or ""),
                "detail_zh": str(it.get("detail_zh") or ""),
                "amount_usd": round(float(it.get("amount_usd") or 0.0), 2),
            }
            for idx, it in enumerate(wa.get("items") or [])
        ] + plugin_reconcile_items

        # Equity curve & calendar days
        raw_curve = (wa.get("equity_curve") or legacy_report.get("equity_curve") or [])
        equity_curve = [
            {
                "seq": int(pt.get("seq") or i + 1),
                "date": str(pt.get("contract") or pt.get("date") or ""),
                "label": str(pt.get("contract") or "")[5:] if len(str(pt.get("contract") or "")) >= 10 else str(pt.get("contract") or ""),
                "delta_usd": round(float(pt.get("trade_net_pnl_usd") or 0.0), 2),
                "cum_net_usd": round(float(pt.get("cum_net_pnl_usd") or 0.0), 2),
                "cum_book_usd": round(float(pt.get("cum_book_pnl_usd") or 0.0), 2),
            }
            for i, pt in enumerate(raw_curve)
        ]
        raw_cal = wa.get("calendar_days") or legacy_report.get("calendar_days") or []
        calendar_days = [
            {
                "date": str(d.get("date") or ""),
                "net_pnl_usd": round(float(d.get("net_pnl_usd") or 0.0), 2),
                "trade_count": int(d.get("trade_count") or 0),
            }
            for d in raw_cal
        ]

        symbol_attribution = [
            {
                "venue_id": str(sb.get("exchange") or "GATE").lower(),
                "venue_name": str(sb.get("exchange_zh") or sb.get("exchange") or "Gate"),
                "color_token": str(sb.get("exchange") or "GATE").lower(),
                "symbol": str(sb.get("contract") or ""),
                "trade_count": int(sb.get("count") or 0),
                "win_rate_pct": round(float(sb.get("win_rate_pct") or 0.0), 1),
                "gross_fee_usd": round(float(sb.get("fee_usd") or 0.0), 2),
                "rebate_usd": round(float(sb.get("rebate_usd") or 0.0), 2),
                "discount_saved_usd": round(float(sb.get("bnb_discount_saved_usd") or 0.0), 2),
                "book_pnl_usd": round(float(sb.get("book_pnl_usd") or 0.0), 2),
                "net_pnl_usd": round(float(sb.get("realized_net_usd") or 0.0), 2),
            }
            for sb in (legacy_report.get("symbol_breakdown") or [])
        ]

        # Goal info strictly controlled by user preferences (zero hardcoded defaults)
        goal_info = None
        goal_target = prefs.goal_target_usd if (prefs.goal_enabled and prefs.goal_target_usd) else None
        if goal_target and goal_target > 0:
            rem = max(0.0, round(goal_target - total_equity, 2))
            surp = max(0.0, round(total_equity - goal_target, 2))
            goal_info = {
                "enabled": True,
                "label": prefs.goal_label or "",
                "target_usd": goal_target,
                "current_equity_usd": total_equity,
                "remaining_usd": rem,
                "surplus_usd": surp,
                "is_reached": total_equity >= goal_target,
                "progress_pct": round(min(100.0, (total_equity / goal_target) * 100.0), 2),
                "needed_gain_pct": round((rem / total_equity) * 100.0, 2) if total_equity > 0 and rem > 0 else 0.0,
                "today_delta_usd": equity_curve[-1]["delta_usd"] if equity_curve else 0.0,
            }

        # Extensions: copy trading & stocks normalized
        ct = legacy_report.get("copy_trading") or {}
        comb_ct = ct.get("combined") or ct
        stk = legacy_report.get("stock") or {}
        stk_perps = stk.get("stock_perpetual_contracts") or stk.get("easicoin_stock_contracts") or []
        stock_holding_keys = {
            (str(sp.get("platform") or "gate").lower(), str(sp.get("symbol") or "").strip().upper())
            for sp in (stk.get("positions") or [])
            if sp.get("symbol")
        }
        spot_holdings = _extract_spot_holdings(ls, plugin_snapshots, self.registry, stock_holding_keys)

        # AI staleness check
        ai_out = latest_ai_review
        if ai_out is None:
            legacy_ai = ls.load_latest_ai_diagnostic()
            if isinstance(legacy_ai, dict):
                ai_out = {
                    "generated_at": str(legacy_ai.get("generated_at_bj") or ""),
                    "generated_ms": int(legacy_ai.get("created_ms") or 0),
                    "model": str(legacy_ai.get("model") or "local-actuarial"),
                    "equity_snapshot_usd": total_equity,
                    "stale": True,
                    "highlights": [
                        {
                            "title": str(h.get("title") or ""),
                            "title_zh": str(h.get("title") or ""),
                            "metric": "",
                            "body": str(h.get("body") or ""),
                            "body_zh": str(h.get("body") or ""),
                        }
                        for h in (legacy_ai.get("highlights") or [])
                    ],
                    "markdown_commentary": str(legacy_ai.get("ai_commentary") or ""),
                }
        elif self.last_sync_ms and int(ai_out.get("generated_ms") or 0) + 7_200_000 < self.last_sync_ms:
            ai_out = {**ai_out, "stale": True}

        return {
            "demo_mode": False,
            "read_only": True,
            "last_sync_ms": self.last_sync_ms or int(ls.get_meta("last_sync_ms", 0) or 0),
            "syncing": self.syncing,
            "range_key": range_key,
            "available_months": list(legacy_report.get("available_months") or []),
            "available_symbols": sorted(
                {str(c) for c in (legacy_report.get("available_contracts") or []) if c}
                | {t["symbol"] for t in trades if t["symbol"]}
            ),
            "summary": {
                "total_equity_usd": total_equity,
                "net_principal_usd": total_principal,
                "whole_net_pnl_usd": whole_net,
                "whole_roi_pct": round((whole_net / total_principal) * 100.0, 2) if total_principal > 0 else 0.0,
                "futures_net_pnl_usd": round(sum(t["net_pnl_usd"] for t in trades), 2),
                "futures_book_pnl_usd": round(sum(t["book_pnl_usd"] for t in trades), 2),
                "trade_count": len(trades),
                "all_time_trade_count": int(summary.get("all_time_trade_count") or len(trades)),
                "win_rate_pct": round(float(summary.get("win_rate_pct") or 0.0), 1),
                "total_fee_usd": round(float(summary.get("total_fee_usd") or 0.0), 2),
                "total_rebate_usd": round(float(summary.get("settled_rebate_usd") or 0.0) + float(summary.get("pending_rebate_usd") or 0.0), 2),
                "total_discount_saved_usd": round(float(summary.get("bnb_discount_saved_usd") or 0.0), 2),
                "net_fee_usd": round(float(summary.get("net_fee_after_rebate_usd") or 0.0), 2),
                "combined_relief_pct": round(float(summary.get("combined_fee_relief_pct") or summary.get("effective_rebate_pct") or 0.0), 1),
                "potential_maker_savings_usd": round(float((fa.get("combined") or {}).get("potential_maker_savings_usd") or summary.get("potential_maker_savings_usd") or 0.0), 2),
                "max_drawdown_usd": round(float(wa.get("max_drawdown_usd") or summary.get("max_drawdown_usd") or 0.0), 2),
            },
            "exchanges": exchanges,
            "open_positions": open_positions,
            "spot_holdings": spot_holdings,
            "trades": trades,
            "reconciliation": {
                "net_principal_usd": total_principal,
                "total_equity_usd": total_equity,
                "net_pnl_usd": whole_net,
                "residual_usd": round(float(wa.get("reconcile_residual_usd") or 0.0), 2),
                "items": reconcile_items,
            },
            "equity_curve": equity_curve,
            "calendar_days": calendar_days,
            "symbol_attribution": symbol_attribution,
            "fee_matrix": fee_matrix,
            "goal": goal_info,
            "ai_review": ai_out,
            "extensions": {
                "copy_trading": {
                    "enabled": prefs.modules.copy_trading,
                    "total_invested_usd": round(float(comb_ct.get("cumulative_invested_usd") or comb_ct.get("net_principal_usd") or 0.0), 2),
                    "active_equity_usd": round(float(comb_ct.get("current_equity_usd") or 0.0), 2),
                    "book_pnl_usd": round(float(comb_ct.get("book_pnl_usd") or 0.0), 2),
                    "rebate_total_usd": round(float(comb_ct.get("rebate_total_usd") or 0.0), 2),
                    "combined_pnl_usd": round(float(comb_ct.get("combined_pnl_usd") or 0.0), 2),
                    "combined_roi_pct": round(float(comb_ct.get("combined_pnl_pct") or 0.0), 2),
                    "platforms": [
                        {
                            "venue_id": str(p.get("exchange") or "gate").lower(),
                            "venue_name": str(p.get("exchange_zh") or p.get("exchange") or "Gate"),
                            "color_token": str(p.get("exchange") or "gate").lower(),
                            "status": str(p.get("status") or "ACTIVE"),
                            "invested_usd": round(float(p.get("total_invested_usd") or p.get("net_principal_usd") or 0.0), 2),
                            "equity_usd": round(float(p.get("current_equity_usd") or 0.0), 2),
                            "rebate_usd": round(float(p.get("rebate_total_usd") or 0.0), 2),
                            "combined_pnl_usd": round(float(p.get("combined_pnl_usd") or 0.0), 2),
                            "rebate_count": int(p.get("rebate_count") or 0),
                        }
                        for p in (ct.get("platforms") or [])
                    ],
                    "transfers": [
                        {
                            "venue_id": str(tr.get("exchange") or "gate").lower(),
                            "venue_name": str(tr.get("exchange_zh") or tr.get("exchange") or "Gate"),
                            "time_str": str(tr.get("time_bj") or ""),
                            "direction": str(tr.get("direction_zh") or ""),
                            "direction_zh": str(tr.get("direction_zh") or ""),
                            "amount_usd": round(float(tr.get("amount_usd") or 0.0), 2),
                        }
                        for tr in (comb_ct.get("transfers") or ct.get("transfers") or [])
                    ],
                    "rebates": [
                        {
                            "venue_id": str(rb.get("exchange") or "gate").lower(),
                            "venue_name": str(rb.get("exchange_zh") or rb.get("exchange") or "Gate"),
                            "time_str": str(rb.get("datetime_bj") or rb.get("time_bj") or ""),
                            "destination": str(rb.get("credited_to_zh") or "Main Account"),
                            "est_gross_fee_usd": round(float(rb.get("est_gross_fee_usd") or 0.0), 3),
                            "rebate_usd": round(float(rb.get("rebate_usd") or 0.0), 4),
                        }
                        for rb in (comb_ct.get("rebates") or ct.get("rebates") or [])
                    ],
                },
                "stocks": {
                    "enabled": prefs.modules.stocks,
                    "equity_usd": round(float(stk.get("equity_usd") or 0.0), 2),
                    "unrealized_pnl_usd": round(float(stk.get("position_pnl_usd") or 0.0), 2),
                    "non_perp_fees_usd": round(float(stk.get("net_stock_fees_usd") or 0.0), 4),
                    "fee_rate_range": (
                        f"{min(float(x.get('maker_fee_pct', 0)) for x in (stk.get('platform_fee_rates') or [])):g}% ~ "
                        f"{max(float(x.get('taker_fee_pct', 0)) for x in (stk.get('platform_fee_rates') or [])):g}%"
                        if (stk.get("platform_fee_rates") or [])
                        else f"{stk.get('maker_fee_pct', 0)}%"
                    ),
                    "venue_fee_rates": [
                        {
                            "venue_id": str(x.get("exchange") or "gate").lower(),
                            "venue_name": str(x.get("exchange_zh") or x.get("exchange") or "Gate"),
                            "summary": str(x.get("summary_zh") or ""),
                        }
                        for x in (stk.get("platform_fee_rates") or [])
                    ],
                    "holdings": [
                        {
                            "venue_id": str(sp.get("platform") or "gate").lower(),
                            "venue_name": str(sp.get("platform_zh") or sp.get("platform") or "Gate"),
                            "symbol": str(sp.get("symbol") or ""),
                            "name": str(sp.get("name") or ""),
                            "channel": str(sp.get("channel_zh") or "Equity"),
                            "channel_zh": str(sp.get("channel_zh") or "美股正股"),
                            "shares": float(sp.get("shares") or 0.0),
                            "avg_cost_usd": round(float(sp.get("avg_cost_usd") or 0.0), 2),
                            "current_price_usd": round(float(sp.get("current_price_usd") or 0.0), 2),
                            "market_value_usd": round(float(sp.get("market_value_usd") or 0.0), 2),
                            "pnl_usd": round(float(sp.get("pnl_usd") or 0.0), 2),
                            "pnl_pct": round(float(sp.get("pnl_pct") or 0.0), 2),
                        }
                        for sp in (stk.get("positions") or [])
                    ],
                    "cash_ledger": [
                        {
                            "venue_id": str(tx.get("platform") or "gate").lower(),
                            "venue_name": str(tx.get("platform_zh") or tx.get("platform") or "Gate"),
                            "time_str": str(tx.get("time_bj") or ""),
                            "type": str(tx.get("type_desc") or ""),
                            "symbol": str(tx.get("symbol") or ""),
                            "change": f"{tx.get('change', '')} {tx.get('unit', '')}".strip(),
                        }
                        for tx in (stk.get("transactions") or [])
                    ],
                    "perpetuals": [
                        {
                            "venue_id": str(row.get("exchange") or "easicoin").lower(),
                            "venue_name": str(row.get("exchange_zh") or row.get("exchange") or ""),
                            "symbol": str(row.get("contract") or ""),
                            "category": str(row.get("category_zh") or ""),
                            "trade_count": int(row.get("trade_count") or 0),
                            "qty": float(row.get("closed_qty") or 0.0),
                            "maker_bps": round(float(row.get("observed_maker_bps") or 0.0), 2),
                            "taker_bps": round(float(row.get("observed_taker_bps") or 0.0), 2),
                            "gross_fee_usd": round(float(row.get("gross_fee_usd") or 0.0), 4),
                            "rebate_usd": round(float(row.get("rebate_usd") or 0.0), 4),
                            "net_fee_usd": round(float(row.get("net_fee_usd") or 0.0), 4),
                            "net_pnl_usd": round(float(row.get("realized_net_pnl_usd") or 0.0), 2),
                        }
                        for row in stk_perps
                    ],
                },
            },
        }
