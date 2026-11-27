"""Stateless financial math, timestamp, leg normalization, and stock/crypto classification primitives.

Shared across independent exchange modules (`adapters/<venue_id>/`) with zero per-venue state.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite
from typing import Any, Mapping, Sequence

TZ_BEIJING = timezone(timedelta(hours=8))

STABLECOINS: frozenset[str] = frozenset(
    {
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
)

# Fallback ledger valuation when a dollar-denominated balance has no quote.
USD_CASH_ASSETS = frozenset({"USD", "USDT", "USDC", "FDUSD", "BUSD", "USDE", "USD1", "TUSD", "PYUSD", "USDP", "DAI"})

DEFAULT_CONTRACTS_MULTIPLIER: dict[str, Decimal] = {
    "BTC_USDT": Decimal("0.0001"),
    "ETH_USDT": Decimal("0.01"),
}

STOCK_COMMODITY_BASES: frozenset[str] = frozenset(
    {
        "SNDK",
        "SKHYNIX",
        "SPCX",
        "DELL",
        "SPCXSTOCK",
        "SNDKSTOCK",
        "DELLSTOCK",
        "AAPL",
        "NVDA",
        "TSLA",
        "MSFT",
        "AMZN",
        "GOOG",
        "GOOGL",
        "META",
        "COIN",
        "MSTR",
        "HOOD",
        "CRCL",
        "AMD",
        "NFLX",
        "PLTR",
        "BABA",
        "INTC",
        "ORCL",
        "CRM",
        "UBER",
        "DIS",
        "PYPL",
        "SQ",
        "SHOP",
        "SMCI",
        "ARM",
        "AVGO",
        "TSM",
        "ASML",
        "MU",
        "QCOM",
        "SPY",
        "QQQ",
        "DIA",
        "IWM",
        "XAU",
        "XAG",
        "PAXG",
        "USO",
        "UKO",
        "WTI",
        "BRENT",
        "NATGAS",
        "CL",
    }
)

COMMODITY_BASES: frozenset[str] = frozenset(
    {"XAU", "XAG", "PAXG", "USO", "UKO", "WTI", "BRENT", "NATGAS", "CL"}
)


def dec(val: Any, default: str = "0") -> Decimal:
    if val in (None, ""):
        return Decimal(default)
    try:
        d = Decimal(str(val))
        return d if d.is_finite() else Decimal(default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def f2(val: Decimal | float | int | str, digits: int = 4) -> float:
    d = dec(val)
    if digits <= 0:
        q = d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    else:
        q = d.quantize(Decimal(10) ** (-digits), rounding=ROUND_HALF_UP)
    res = float(q)
    return 0.0 if res == 0.0 else res


def fmt_bj(ts_sec: float | int, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if not ts_sec or ts_sec <= 0:
        return "—"
    sec = float(ts_sec) / 1000.0 if float(ts_sec) > 10_000_000_000 else float(ts_sec)
    return datetime.fromtimestamp(sec, TZ_BEIJING).strftime(fmt)


def fmt_ms(ms: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ_BEIJING).strftime(fmt)


def norm_sec(raw_ts: Any) -> float:
    val = float(dec(raw_ts))
    if val > 10_000_000_000:
        return val / 1000.0
    return val


def norm_ms(raw_ts: Any) -> int:
    val = dec(raw_ts)
    if val <= 0:
        return 0
    if val > Decimal("10000000000"):
        return int(val)
    return int((val * Decimal("1000")).to_integral_value(rounding=ROUND_HALF_UP))


def contract_mult(contract: str, specs: Mapping[str, Mapping[str, Any]]) -> Decimal:
    spec = specs.get(contract) or {}
    qm = dec(spec.get("quanto_multiplier"), "0")
    if qm > 0:
        return qm
    return DEFAULT_CONTRACTS_MULTIPLIER.get(contract, Decimal("1"))


def format_duration_zh(seconds: int) -> str:
    sec = max(0, int(seconds))
    if sec < 60:
        return f"{sec}秒"
    if sec < 3600:
        m, s = divmod(sec, 60)
        return f"{m}分{s:02d}秒" if s else f"{m}分"
    h, rem = divmod(sec, 3600)
    m = rem // 60
    if h >= 24:
        d, rh = divmod(h, 24)
        return f"{d}天{rh}小时"
    return f"{h}小时{m:02d}分"


def normalize_contract_symbol(raw_symbol: str) -> str:
    sym = (raw_symbol or "").strip().upper()
    if "_" in sym:
        return sym
    for quote in ("USDT", "USDC", "USD"):
        if sym.endswith(quote) and len(sym) > len(quote):
            return f"{sym[:-len(quote)]}_{quote}"
    return sym


def is_stock_or_commodity_base(base: str) -> bool:
    bu = (base or "").strip().upper()
    if not bu:
        return False
    if bu in STOCK_COMMODITY_BASES or (len(bu) > 5 and bu.endswith("STOCK")):
        return True
    if len(bu) >= 3 and bu.endswith("X") and bu[:-1] in STOCK_COMMODITY_BASES:
        return True
    if len(bu) >= 5 and bu.endswith("ON") and bu[:-2] in STOCK_COMMODITY_BASES:
        return True
    return False


def is_tokenized_stock_symbol(ccy: str) -> bool:
    u = (ccy or "").strip().upper()
    if len(u) > 5 and u.endswith("STOCK"):
        return True
    if len(u) >= 3 and u.endswith("X") and u[:-1] in STOCK_COMMODITY_BASES and u[:-1] not in COMMODITY_BASES:
        return True
    if len(u) >= 5 and u.endswith("ON") and u[:-2] in STOCK_COMMODITY_BASES and u[:-2] not in COMMODITY_BASES:
        return True
    return False


def classify_perp_stock_or_commodity(symbol: str, venue_name_en: str = "", venue_name_zh: str = "") -> tuple[bool, str, str]:
    base = (symbol or "").split("_")[0].strip().upper()
    if not base or not is_stock_or_commodity_base(base):
        return False, "", ""
    if base in COMMODITY_BASES:
        return True, "Commodity Perp", "大宗商品合约"
    if base == "SKHYNIX":
        return True, "KR Equity Perp", "韩股永续"
    if base in ("SPCX", "SPCXSTOCK"):
        return True, "Pre-IPO Perp", "独角兽股权永续"
    if venue_name_zh and venue_name_zh in ("Gate", "MEXC", "易币"):
        en_lbl = f"{venue_name_en or venue_name_zh} US Perp"
        return True, en_lbl, f"{venue_name_zh}美股永续"
    return True, "US Equity Perp", "美股永续"


def fmt_pct_clean(v: Decimal) -> str:
    fv = f2(v, 3)
    if fv == int(fv):
        return f"{int(fv)}%"
    s = f"{fv:.3f}".rstrip("0").rstrip(".")
    return f"{s}%"


def merge_legacy_journal_entries(
    journals: Mapping[str, Mapping[str, Any]],
    primary_key: str,
    legacy_keys: Sequence[str] = (),
) -> dict[str, Any]:
    """Return `journals[primary_key]` if saved; otherwise merge tags/notes from legacy split keys."""
    if primary_key in journals:
        return dict(journals[primary_key] or {})
    merged_tags: list[str] = []
    merged_notes: list[str] = []
    for k in legacy_keys:
        if not k or k not in journals:
            continue
        entry = journals[k] or {}
        for tg in entry.get("tags") or ():
            tg_s = str(tg)
            if tg_s and tg_s not in merged_tags:
                merged_tags.append(tg_s)
        nt = str(entry.get("note") or "").strip()
        if nt and nt not in merged_notes:
            merged_notes.append(nt)
    if not merged_tags and not merged_notes:
        return {}
    return {"tags": merged_tags, "note": "\n".join(merged_notes)}


def normalize_internal_leg_to_public(raw: Mapping[str, Any], default_time_ms: int) -> dict[str, Any]:
    gross_fee = float(raw.get("gross_fee_usd") or 0.0)
    disc_saved = round(float(raw.get("bnb_discount_saved_usd") or raw.get("discount_saved_usd") or 0.0), 4)
    if "rebate_usd" in raw and "settled_rebate_usd" not in raw:
        rebate = float(raw.get("rebate_usd") or 0.0)
    else:
        settled_reb = float(raw.get("settled_rebate_usd") or 0.0)
        pending_reb = float(raw.get("pending_rebate_usd") or 0.0)
        rebate = float(dec(settled_reb) + dec(pending_reb))
    native_net_fee = raw.get("net_fee_usd")
    net_fee = float(native_net_fee) if native_net_fee is not None else float(dec(gross_fee) - dec(rebate))
    mode_zh = str(raw.get("mode_zh") or "")
    raw_mode = str(raw.get("mode_code") or raw.get("mode") or "").upper()
    m_not_hint = float(raw.get("maker_notional_usd") or 0.0)
    t_not_hint = float(raw.get("taker_notional_usd") or 0.0)
    is_mixed = raw_mode == "MIXED" or "混合" in mode_zh or (m_not_hint > 0.0 and t_not_hint > 0.0)
    if is_mixed:
        is_maker = m_not_hint > 0.0 and m_not_hint >= t_not_hint
    else:
        is_maker = "挂单" in mode_zh or str(raw.get("role") or "").upper() == "MAKER" or raw_mode == "LIMIT_MAKER"
    if raw.get("mode_code"):
        mode_code = str(raw["mode_code"])
    elif raw_mode == "UNKNOWN":
        mode_code = "UNKNOWN"
    elif is_mixed:
        mode_code = "MIXED"
    elif raw_mode in {"STOP_MARKET_TAKER", "STOP_MARKET"} or "止损" in mode_zh:
        mode_code = "STOP_MARKET"
    elif raw_mode in {"TP_MARKET_TAKER", "TP_MARKET"} or "止盈" in mode_zh:
        mode_code = "TP_MARKET"
    elif "反手" in mode_zh:
        mode_code = "REVERSE_MARKET"
    elif is_maker:
        mode_code = "LIMIT_MAKER"
    else:
        mode_code = "MARKET_TAKER"
    fee_known = bool(raw.get("fee_known", True))
    is_unknown_split = (not fee_known) and (mode_code == "UNKNOWN")
    status = str(raw.get("rebate_status") or "SETTLED").upper()
    if status not in {"SETTLED", "PENDING", "NONE"}:
        status = "PENDING" if "PENDING" in status else ("SETTLED" if gross_fee > 0 else "NONE")
    t_ms = int(raw.get("time_ms") or (int(raw.get("time_sec") or 0) * 1000) or default_time_ms)
    notional_2dp = round(float(raw.get("notional_usd") or 0.0), 2)
    return {
        "role": "UNKNOWN" if is_unknown_split else ("MAKER" if is_maker else "TAKER"),
        "mode_code": mode_code,
        "fee_known": fee_known,
        "fee_bps": None if "fee_bps" in raw and raw["fee_bps"] is None else round(float(raw.get("fee_bps") or 0.0), 2),
        "time_ms": t_ms,
        "time_str": str(raw.get("time_str") or raw.get("time_bj") or fmt_ms(t_ms, "%m-%d %H:%M")),
        "avg_price": float(raw.get("avg_price") or 0.0),
        "notional_usd": None if "notional_usd" in raw and raw["notional_usd"] is None else notional_2dp,
        "maker_notional_usd": 0.0 if is_unknown_split else round(float(raw.get("maker_notional_usd") or (notional_2dp if is_maker else 0.0)), 2),
        "taker_notional_usd": 0.0 if is_unknown_split else round(float(raw.get("taker_notional_usd") or (0.0 if is_maker else notional_2dp)), 2),
        "maker_fee_usd": 0.0 if is_unknown_split else float(raw.get("maker_fee_usd") or (gross_fee if is_maker else 0.0)),
        "taker_fee_usd": 0.0 if is_unknown_split else float(raw.get("taker_fee_usd") or (0.0 if is_maker else gross_fee)),
        "maker_rebate_usd": 0.0 if is_unknown_split else float(raw.get("maker_rebate_usd") or (rebate if is_maker else 0.0)),
        "taker_rebate_usd": 0.0 if is_unknown_split else float(raw.get("taker_rebate_usd") or (0.0 if is_maker else rebate)),
        "gross_fee_usd": gross_fee,
        "discount_saved_usd": disc_saved,
        "discount_asset": str(raw.get("discount_asset") or raw.get("fee_asset") or ""),
        "rebate_usd": rebate,
        "rebate_pct": round(float(raw.get("rebate_pct") or 0.0), 1),
        "net_fee_usd": net_fee,
        "rebate_status": status,
        "settled_at": str(raw.get("settled_at") or raw.get("rebate_settled_bj") or ""),
    }


def normalize_exit_batch_to_public(
    raw: Mapping[str, Any],
    batch_index: int,
    default_time_ms: int,
) -> dict[str, Any]:
    leg = normalize_internal_leg_to_public(raw, default_time_ms)
    raw_qty = raw.get("qty") if raw.get("qty") is not None else raw.get("qty_base")
    qty_val = float(raw_qty) if raw_qty is not None else None
    c_cnt = float(raw["contract_count"]) if raw.get("contract_count") is not None else None
    gpnl = round(float(raw["gross_pnl_usd"]), 4) if raw.get("gross_pnl_usd") is not None else None
    if raw.get("net_pnl_usd") is not None:
        npnl = round(float(raw["net_pnl_usd"]), 4)
    else:
        npnl = round(gpnl - float(leg["net_fee_usd"]), 4) if gpnl is not None and leg["fee_known"] else None
    reb_pct = (
        leg["rebate_pct"]
        if leg["rebate_pct"] > 0
        else (round((leg["rebate_usd"] / leg["gross_fee_usd"]) * 100.0, 1) if leg["gross_fee_usd"] > 0 else 0.0)
    )
    return {
        "batch_index": int(raw.get("batch_index") or batch_index),
        "order_id": str(raw.get("order_id") or ""),
        "net_basis": raw.get("net_basis", "exit_only"),
        "qty": qty_val,
        **({"contract_count": c_cnt} if c_cnt is not None else {}),
        "role": leg["role"],
        "mode_code": leg["mode_code"],
        "fee_known": leg["fee_known"],
        "fee_bps": leg["fee_bps"] if raw.get("fee_bps", leg["fee_bps"]) is not None else None,
        "time_ms": leg["time_ms"],
        "time_str": leg["time_str"],
        "avg_price": leg["avg_price"],
        "notional_usd": leg["notional_usd"] if raw.get("notional_usd", leg["notional_usd"]) is not None else None,
        "gross_fee_usd": leg["gross_fee_usd"],
        "discount_saved_usd": leg["discount_saved_usd"],
        "discount_asset": leg["discount_asset"],
        "rebate_usd": leg["rebate_usd"],
        "rebate_pct": reb_pct,
        "net_fee_usd": leg["net_fee_usd"],
        "gross_pnl_usd": gpnl,
        "net_pnl_usd": npnl,
    }


def normalize_internal_record_to_public_trade(
    r: Mapping[str, Any],
    *,
    venue_id: str,
    venue_name: str,
    venue_name_zh: str,
    color_token: str,
) -> dict[str, Any]:
    open_ms = int(r.get("open_time_ms") or int(float(r.get("open_time_sec") or 0) * 1000))
    close_ms = int(r.get("close_time_ms") or int(float(r.get("close_time_sec") or 0) * 1000))
    entry_leg = normalize_internal_leg_to_public(r.get("entry_leg") or {}, open_ms)
    exit_leg = normalize_internal_leg_to_public(r.get("exit_leg") or {}, close_ms)
    settled_reb = float(r.get("settled_rebate_usd") or 0.0)
    pending_reb = float(r.get("pending_rebate_usd") or 0.0)
    total_reb = float(dec(settled_reb) + dec(pending_reb))
    total_disc = round(float(r.get("bnb_discount_saved_usd") or r.get("total_discount_saved_usd") or 0.0), 4)
    total_fee = float(r.get("total_fee_usd") or 0.0)
    if total_fee > 0 and entry_leg["gross_fee_usd"] <= 0 and exit_leg["gross_fee_usd"] <= 0:
        for unsplit_leg in (entry_leg, exit_leg):
            unsplit_leg["fee_known"] = False
            unsplit_leg["mode_code"] = "UNKNOWN"
            unsplit_leg["role"] = "UNKNOWN"
            unsplit_leg["maker_notional_usd"] = 0.0
            unsplit_leg["taker_notional_usd"] = 0.0
            unsplit_leg["maker_fee_usd"] = 0.0
            unsplit_leg["taker_fee_usd"] = 0.0
            unsplit_leg["maker_rebate_usd"] = 0.0
            unsplit_leg["taker_rebate_usd"] = 0.0
    fallback_net_fee = (
        max(0.0, total_fee - total_reb)
        if (not entry_leg.get("fee_known", True) and not exit_leg.get("fee_known", True))
        else (entry_leg["net_fee_usd"] + exit_leg["net_fee_usd"])
    )
    net_fee = float(r.get("net_fee_usd") if r.get("net_fee_usd") is not None else fallback_net_fee)
    base_fee = total_fee + total_disc
    relief = round(((total_reb + total_disc) / base_fee) * 100.0, 1) if base_fee > 0 else 0.0
    reb_state = str(r.get("rebate_state") or r.get("rebate_status") or "SETTLED")
    base_qty = r.get("qty") if r.get("qty") is not None else r.get("qty_base")
    return {
        "trade_id": str(r.get("trade_id") or r.get("record_key")),
        "venue_id": venue_id,
        "venue_name": venue_name,
        "venue_name_zh": venue_name_zh,
        "color_token": color_token,
        "symbol": str(r.get("symbol") or r.get("contract") or ""),
        "side": "SHORT" if str(r.get("side") or "").upper() == "SHORT" else "LONG",
        "qty": None if base_qty is None and r.get("contract_count") is not None else float(base_qty or 0.0),
        **({"contract_count": r["contract_count"]} if r.get("contract_count") is not None else {}),
        "open_time_ms": open_ms,
        "close_time_ms": close_ms,
        "open_time_str": str(r.get("open_time_str") or r.get("open_datetime_bj") or fmt_ms(open_ms))[:16],
        "close_time_str": str(r.get("close_time_str") or r.get("close_datetime_bj") or fmt_ms(close_ms))[:16],
        "close_date": str(r.get("close_date") or r.get("close_date_bj") or fmt_ms(close_ms, "%Y-%m-%d")),
        "holding_sec": int(
            r.get("holding_seconds")
            or r.get("holding_sec")
            or max(0, (close_ms - open_ms) // 1000)
        ),
        "is_partial_close": bool(r.get("is_partial_close")),
        "gross_pnl_usd": round(float(r.get("gross_pnl_usd") or 0.0), 4),
        "funding_pnl_usd": round(float(r.get("funding_pnl_usd") or 0.0), 4),
        "book_pnl_usd": round(float(r.get("book_pnl_usd") if r.get("book_pnl_usd") is not None else (r.get("gate_book_pnl_usd") or 0.0)), 4),
        "total_fee_known": r.get("total_fee_known", True),
        "net_pnl_known": r.get("net_pnl_known", True),
        "settlement_adjustment_usd": float(r.get("settlement_adjustment_usd") or 0),
        "total_fee_usd": total_fee,
        "settled_rebate_usd": settled_reb,
        "pending_rebate_usd": pending_reb,
        "total_rebate_usd": total_reb,
        "total_discount_saved_usd": total_disc,
        "bnb_fee_qty": round(float(r.get("bnb_fee_qty") or 0.0), 6),
        "net_fee_usd": net_fee,
        "realized_net_pnl_usd": round(float(r.get("realized_net_pnl_usd") if r.get("realized_net_pnl_usd") is not None else (r.get("net_pnl_usd") or 0.0)), 4),
        "net_pnl_usd": round(float(r.get("expected_net_pnl_usd") if r.get("expected_net_pnl_usd") is not None else (r.get("net_pnl_usd") or 0.0)), 4),
        "effective_relief_pct": relief,
        "rebate_status": "PENDING" if reb_state not in ("SETTLED", "NONE") else "SETTLED",
        "entry_leg": entry_leg,
        "exit_leg": exit_leg,
        "position_events": list(r.get("position_events") or ()),
        "funding_by_day": dict(r.get("funding_by_day") or {}),
        "rebates_by_day": dict(r.get("rebates_by_day") or {}),
        "settlements_by_day": dict(r.get("settlements_by_day") or {}),
        "exit_batches": [
            normalize_exit_batch_to_public(b, idx + 1, close_ms)
            for idx, b in enumerate(r.get("exit_batches") or ())
        ],
        "tags": list(r.get("tags") or []),
        "note": str(r.get("note") or ""),
    }


def aggregate_trade_legs_decimal(trades: Sequence[Mapping[str, Any]]) -> dict[str, Decimal]:
    m_not = Decimal("0")
    m_fee = Decimal("0")
    m_reb = Decimal("0")
    t_not = Decimal("0")
    t_fee = Decimal("0")
    t_reb = Decimal("0")
    tot_not = Decimal("0")
    g_fee = Decimal("0")
    tot_reb = Decimal("0")
    disc_saved = Decimal("0")
    disc_qty = Decimal("0")
    for r in trades:
        g_fee += dec(r.get("total_fee_usd"))
        tot_reb += dec(r.get("total_rebate_usd"))
        disc_saved += dec(r.get("total_discount_saved_usd") or r.get("bnb_discount_saved_usd"))
        disc_qty += dec(r.get("bnb_fee_qty"))
        for lk in ("entry_leg", "exit_leg"):
            lg = r.get(lk) or {}
            l_not = dec(lg.get("notional_usd"))
            tot_not += l_not
            role_u = str(lg.get("role") or "").upper()
            if not bool(lg.get("fee_known", True)) or role_u == "UNKNOWN":
                continue
            is_m = role_u == "MAKER"
            l_fee = dec(lg.get("gross_fee_usd"))
            l_reb = dec(lg.get("rebate_usd"))
            m_not += dec(lg.get("maker_notional_usd")) if "maker_notional_usd" in lg else (l_not if is_m else Decimal("0"))
            t_not += dec(lg.get("taker_notional_usd")) if "taker_notional_usd" in lg else (Decimal("0") if is_m else l_not)
            m_fee += dec(lg.get("maker_fee_usd")) if "maker_fee_usd" in lg else (l_fee if is_m else Decimal("0"))
            t_fee += dec(lg.get("taker_fee_usd")) if "taker_fee_usd" in lg else (Decimal("0") if is_m else l_fee)
            m_reb += dec(lg.get("maker_rebate_usd")) if "maker_rebate_usd" in lg else (l_reb if is_m else Decimal("0"))
            t_reb += dec(lg.get("taker_rebate_usd")) if "taker_rebate_usd" in lg else (Decimal("0") if is_m else l_reb)
    return {
        "m_not": m_not,
        "m_fee": m_fee,
        "m_reb": m_reb,
        "m_net": m_fee - m_reb,
        "t_not": t_not,
        "t_fee": t_fee,
        "t_reb": t_reb,
        "t_net": t_fee - t_reb,
        "tot_not": tot_not,
        "g_fee": g_fee,
        "tot_reb": tot_reb,
        "tot_net": g_fee - tot_reb,
        "disc_saved": disc_saved,
        "disc_qty": disc_qty,
    }


def build_venue_stock_perpetuals(
    trades: Sequence[Mapping[str, Any]],
    *,
    venue_id: str,
    venue_name: str,
    venue_name_zh: str,
    extra_rebate_by_sym: Mapping[str, Decimal] | None = None,
) -> list[dict[str, Any]]:
    by_sym: dict[str, dict[str, Any]] = {}
    for tr in trades:
        sym = str(tr.get("symbol") or tr.get("contract") or "")
        is_sc, cat_en, cat_zh = classify_perp_stock_or_commodity(sym, venue_name, venue_name_zh)
        if not is_sc:
            continue
        b = by_sym.setdefault(
            sym,
            {
                "category": cat_en,
                "category_zh": cat_zh,
                "trade_count": 0,
                "qty": Decimal("0"),
                "m_not": Decimal("0"),
                "t_not": Decimal("0"),
                "m_fee": Decimal("0"),
                "t_fee": Decimal("0"),
                "g_fee": Decimal("0"),
                "reb": Decimal("0"),
                "net_pnl": Decimal("0"),
            },
        )
        b["trade_count"] += 1
        if tr.get("total_fee_known") is False:
            b["total_fee_known"] = False
        if tr.get("net_pnl_known") is False:
            b["net_pnl_known"] = False
        if tr.get("contract_count") is not None:
            b["contracts"] = b.get("contracts", Decimal("0")) + dec(tr["contract_count"])
            b["qty_unknown"] = b.get("qty_unknown", False) or tr.get("qty") is None
        b["qty"] += dec(tr.get("qty") if tr.get("qty") is not None else tr.get("qty_base"))
        b["g_fee"] += dec(tr.get("total_fee_usd"))
        b["reb"] += dec(tr.get("total_rebate_usd"))
        b["net_pnl"] += dec(tr.get("net_pnl_usd"))
        for lk in ("entry_leg", "exit_leg"):
            lg = tr.get(lk) or {}
            if "role_samples" in lg:
                for role, sample in lg["role_samples"].items():
                    prefix = "m" if role == "MAKER" else "t"
                    b[prefix + "_not"] += dec(sample["notional"])
                    b[prefix + "_fee"] += dec(sample["paid"]) - dec(sample["credit"])
                continue
            is_m = str(lg.get("role") or "").upper() == "MAKER"
            l_not = dec(lg.get("notional_usd"))
            l_fee = dec(lg.get("gross_fee_usd"))
            b["m_not"] += dec(lg.get("maker_notional_usd")) if "maker_notional_usd" in lg else (l_not if is_m else Decimal("0"))
            b["t_not"] += dec(lg.get("taker_notional_usd")) if "taker_notional_usd" in lg else (Decimal("0") if is_m else l_not)
            b["m_fee"] += dec(lg.get("maker_fee_usd")) if "maker_fee_usd" in lg else (l_fee if is_m else Decimal("0"))
            b["t_fee"] += dec(lg.get("taker_fee_usd")) if "taker_fee_usd" in lg else (Decimal("0") if is_m else l_fee)

    for raw_sym, reb_v in (extra_rebate_by_sym or {}).items():
        if reb_v <= 0:
            continue
        c_norm = normalize_contract_symbol(raw_sym)
        is_sc, cat_en, cat_zh = classify_perp_stock_or_commodity(c_norm, venue_name, venue_name_zh)
        if not is_sc:
            continue
        b = by_sym.setdefault(
            c_norm,
            {
                "category": cat_en,
                "category_zh": cat_zh,
                "trade_count": 0,
                "qty": Decimal("0"),
                "m_not": Decimal("0"),
                "t_not": Decimal("0"),
                "m_fee": Decimal("0"),
                "t_fee": Decimal("0"),
                "g_fee": Decimal("0"),
                "reb": Decimal("0"),
                "net_pnl": Decimal("0"),
            },
        )
        b["reb"] += reb_v
        b["net_pnl"] += reb_v

    rows: list[dict[str, Any]] = []
    for sym in sorted(by_sym.keys()):
        b = by_sym[sym]
        m_bps = (b["m_fee"] / b["m_not"] * Decimal("10000")) if b["m_not"] > 0 else Decimal("0")
        t_bps = (b["t_fee"] / b["t_not"] * Decimal("10000")) if b["t_not"] > 0 else Decimal("0")
        net_fee = b["g_fee"] - b["reb"]
        rows.append(
            {
                "venue_id": venue_id,
                "venue_name": venue_name,
                "venue_name_zh": venue_name_zh,
                "symbol": sym,
                "category": b["category"],
                "category_zh": b["category_zh"],
                "trade_count": b["trade_count"],
                "total_fee_known": b.get("total_fee_known", True),
                "net_pnl_known": b.get("net_pnl_known", True),
                "qty": None if b.get("qty_unknown") else f2(b["qty"], 6),
                **({"contract_count": float(b["contracts"])} if "contracts" in b else {}),
                "maker_bps": None if "contracts" in b and not b["m_not"] else f2(m_bps, 2),
                "taker_bps": None if "contracts" in b and not b["t_not"] else f2(t_bps, 2),
                "gross_fee_usd": f2(b["g_fee"], 4),
                "rebate_usd": f2(b["reb"], 4),
                "net_fee_usd": f2(net_fee, 4),
                "net_pnl_usd": f2(b["net_pnl"], 2),
            }
        )
    return rows


def build_spot_wallet_balances(
    raw_balances: Sequence[tuple[str, float, float, str]],
    *,
    venue_id: str,
    venue_name: str,
    color_token: str,
    spot_prices: Mapping[str, float],
    excluded_symbols: set[str],
) -> list[dict[str, Any]]:
    """Positive balances by wallet, including cash. Unified balances may also back futures."""
    by_wallet: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_asset, qty, explicit_usd, wallet in raw_balances:
        asset = str(raw_asset or "").strip().upper()
        if not asset or not isfinite(qty) or qty <= 0 or asset in excluded_symbols:
            continue
        if venue_id == "binance" and asset.startswith("LD") and len(asset) > 4:
            asset = asset[2:]
            wallet = "EARN"
        if asset in excluded_symbols:
            continue
        price = float(spot_prices.get(asset) or 0)
        if price <= 0 and asset in USD_CASH_ASSETS:
            price = 1.0
        value = explicit_usd if explicit_usd > 0 else qty * price
        key = (wallet, asset)
        if key not in by_wallet:
            by_wallet[key] = {
                "venue_id": venue_id, "venue_name": venue_name, "color_token": color_token,
                "asset": asset, "wallet_type": wallet, "qty": 0.0, "market_value_usd": 0.0,
            }
        row = by_wallet[key]
        row["qty"] += qty
        if value <= 0 or not isfinite(value):
            row["market_value_usd"] = None
        elif row["market_value_usd"] is not None:
            row["market_value_usd"] += value
    return sorted(by_wallet.values(), key=lambda row: row["market_value_usd"] or 0, reverse=True)


def spot_holdings_from_wallets(balances: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the overview's existing one-row-per-asset contract; cash is visible in wallet details."""
    holdings: dict[str, dict[str, Any]] = {}
    for balance in balances:
        asset = balance["asset"]
        value = balance["market_value_usd"]
        if asset in STABLECOINS or value is None or value < 0.01:
            continue
        if asset not in holdings:
            holdings[asset] = dict(balance)
        else:
            combined = holdings[asset]
            combined["qty"] += balance["qty"]
            combined["market_value_usd"] += value
            if combined["wallet_type"] != balance["wallet_type"]:
                combined["wallet_type"] = "MULTIPLE"
    for row in holdings.values():
        row["price_usd"] = round(row["market_value_usd"] / row["qty"], 4)
        row["market_value_usd"] = round(row["market_value_usd"], 2)
        row["qty"] = round(row["qty"], 6)
    return sorted(holdings.values(), key=lambda row: row["market_value_usd"], reverse=True)


def clamp_reconcile_streams_to_target(
    streams: list[dict[str, Any]],
    target_net_pnl_2dp: float,
    *,
    fallback_key: str,
    fallback_label_zh: str,
    fallback_detail_zh: str,
) -> list[dict[str, Any]]:
    """Ensure a venue's reconciliation stream amounts sum strictly to `target_net_pnl_2dp` (0.00 residual)."""
    if not streams:
        if abs(target_net_pnl_2dp) < 0.005:
            return []
        return [
            {
                "key": fallback_key,
                "label_zh": fallback_label_zh,
                "detail_zh": fallback_detail_zh,
                "amount_usd": round(target_net_pnl_2dp, 2),
            }
        ]
    out = [dict(s, amount_usd=round(float(s.get("amount_usd") or 0.0), 2)) for s in streams]
    prev_sum = round(sum(float(x["amount_usd"]) for x in out[:-1]), 2)
    out[-1]["amount_usd"] = round(round(target_net_pnl_2dp, 2) - prev_sum, 2)
    return out
