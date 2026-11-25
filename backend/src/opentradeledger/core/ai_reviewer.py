"""Generic OpenAI-compatible AI trade & cost reviewer with model discovery and full prompt transparency."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from opentradeledger.config import UserPreferences

_BLOCKED_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
    "fd00:ec2::254",
}


def _normalize_ai_base(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/models"):
        if b.endswith(suffix):
            b = b[: -len(suffix)].rstrip("/")
    return b


def validate_ai_base_url(base_url: str) -> str:
    base = _normalize_ai_base(base_url)
    if not base:
        raise ValueError("AI Base URL is required")
    parts = urlsplit(base)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("AI Base URL must start with http:// or https:// and include a valid host")
    host = parts.hostname.lower().strip("[]")
    if host in _BLOCKED_METADATA_HOSTS or host.startswith("169.254."):
        raise ValueError("Blocked cloud metadata address in AI Base URL")
    return base


def is_local_ai_host(base_url: str) -> bool:
    parts = urlsplit(_normalize_ai_base(base_url))
    host = (parts.hostname or "").lower().strip("[]")
    return host in {"localhost", "127.0.0.1", "::1"}


def _extract_responses_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for block in item.get("content") or []:
            if isinstance(block, dict) and block.get("type") in ("output_text", "text"):
                txt = str(block.get("text") or "").strip()
                if txt:
                    chunks.append(txt)
    return "\n".join(chunks).strip()


async def list_ai_models(
    base_url: str,
    api_key: str = "",
    *,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Fetch available model IDs from any OpenAI-compatible `/models` endpoint (or Ollama `/api/tags`)."""
    base = validate_ai_base_url(base_url)

    headers: dict[str, str] = {"Accept": "application/json"}
    if (api_key or "").strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    endpoint = f"{base}/models"
    if client is not None:
        resp = await client.get(endpoint, headers=headers)
    else:
        async with httpx.AsyncClient(timeout=15.0) as c:
            resp = await c.get(endpoint, headers=headers)

    if resp.status_code >= 400:
        raise RuntimeError(f"GET {endpoint} HTTP {resp.status_code}: {resp.text[:220]}")

    payload = resp.json()
    ids: set[str] = set()
    if isinstance(payload, dict):
        for item in payload.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                ids.add(str(item["id"]).strip())
            elif isinstance(item, str) and item.strip():
                ids.add(item.strip())
        for item in payload.get("models") or []:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("name")
                if mid:
                    ids.add(str(mid).strip())
            elif isinstance(item, str) and item.strip():
                ids.add(item.strip())
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and (item.get("id") or item.get("name")):
                ids.add(str(item.get("id") or item.get("name")).strip())
            elif isinstance(item, str) and item.strip():
                ids.add(item.strip())

    return sorted(ids)


def build_ai_context_payload(dashboard: dict[str, Any]) -> dict[str, Any]:
    """Build strictly sanitized structured context fed to the AI model (zero API keys, UIDs, or addresses)."""
    s = dashboard.get("summary") or {}
    trades = dashboard.get("trades") or []
    long_trades = [t for t in trades if str(t.get("side") or "").upper() == "LONG"]
    short_trades = [t for t in trades if str(t.get("side") or "").upper() == "SHORT"]
    long_wr = round((sum(1 for t in long_trades if float(t.get("net_pnl_usd") or 0) > 0) / len(long_trades)) * 100, 1) if long_trades else 0.0
    short_wr = round((sum(1 for t in short_trades if float(t.get("net_pnl_usd") or 0) > 0) / len(short_trades)) * 100, 1) if short_trades else 0.0
    win_holds = [int(t.get("holding_sec") or 0) for t in trades if float(t.get("net_pnl_usd") or 0) > 0]
    loss_holds = [int(t.get("holding_sec") or 0) for t in trades if float(t.get("net_pnl_usd") or 0) < 0]
    avg_win_min = round(sum(win_holds) / len(win_holds) / 60.0, 1) if win_holds else 0.0
    avg_loss_min = round(sum(loss_holds) / len(loss_holds) / 60.0, 1) if loss_holds else 0.0

    known_entry_legs = [
        t for t in trades
        if (t.get("entry_leg") or {}).get("fee_known", True)
        and str((t.get("entry_leg") or {}).get("role") or "").upper() in ("MAKER", "TAKER")
    ]
    known_exit_legs = [
        t for t in trades
        if (t.get("exit_leg") or {}).get("fee_known", True)
        and str((t.get("exit_leg") or "").get("role") or "").upper() in ("MAKER", "TAKER")
    ]
    entry_maker_cnt = sum(1 for t in known_entry_legs if str((t.get("entry_leg") or {}).get("role") or "").upper() == "MAKER")
    exit_maker_cnt = sum(1 for t in known_exit_legs if str((t.get("exit_leg") or {}).get("role") or "").upper() == "MAKER")
    entry_maker_ratio = round((entry_maker_cnt / len(known_entry_legs)) * 100.0, 1) if known_entry_legs else None
    exit_maker_ratio = round((exit_maker_cnt / len(known_exit_legs)) * 100.0, 1) if known_exit_legs else None

    exchanges = [
        {
            "venue": ex.get("display_name") or ex.get("venue_id"),
            "equity_usd": ex.get("equity_usd"),
            "net_principal_usd": ex.get("net_principal_usd"),
            "net_pnl_usd": ex.get("net_pnl_usd"),
            "trade_count": ex.get("trade_count"),
            "rebate_rate_pct": ex.get("rebate_rate_pct"),
            "discount_rate_pct": ex.get("discount_rate_pct"),
        }
        for ex in (dashboard.get("exchanges") or [])
    ]

    fee_matrix = [
        {
            "venue": r.get("display_name") or r.get("venue_id"),
            "trades": r.get("trade_count"),
            "maker_rate_source": r.get("maker_rate_source", "observed" if r.get("maker_bps_known", True) else "unknown"),
            "taker_rate_source": r.get("taker_rate_source", "observed" if r.get("taker_bps_known", True) else "unknown"),
            "maker_configured_gross_bps": r.get("maker_gross_bps") if r.get("maker_rate_source") == "configured" else None,
            "taker_configured_gross_bps": r.get("taker_gross_bps") if r.get("taker_rate_source") == "configured" else None,
            "maker_gross_net_bps": (
                f"{r.get('maker_gross_bps', 0)}/{r.get('maker_net_bps', 0)}"
                if r.get("maker_bps_known", True)
                else "unknown"
            ),
            "taker_gross_net_bps": (
                f"{r.get('taker_gross_bps', 0)}/{r.get('taker_net_bps', 0)}"
                if r.get("taker_bps_known", True)
                else "unknown"
            ),
            "blended_gross_bps": r.get("blended_gross_bps"),
            "blended_net_bps": r.get("blended_net_bps"),
            "gross_fee_usd": r.get("gross_fee_usd"),
            "discount_saved_usd": r.get("discount_saved_usd"),
            "rebate_usd": r.get("rebate_usd"),
            "net_fee_usd": r.get("net_fee_usd"),
            "combined_relief_pct": r.get("combined_relief_pct") if r.get("gross_fee_usd") or r.get("discount_saved_usd") else None,
        }
        for r in (dashboard.get("fee_matrix") or [])
    ]

    sym_sorted = sorted(
        dashboard.get("symbol_attribution") or [],
        key=lambda r: abs(float(r.get("net_pnl_usd") or 0.0)),
        reverse=True,
    )[:15]
    venue_display_by_id = {
        str(ex.get("venue_id") or "").lower(): (ex.get("display_name") or ex.get("venue_id") or "")
        for ex in (dashboard.get("exchanges") or [])
    }

    top_symbols = [
        {
            "venue": venue_display_by_id.get(str(r.get("venue_id") or "").lower()) or r.get("venue_name"),
            "symbol": r.get("symbol"),
            "trades": r.get("trade_count"),
            "win_rate_pct": r.get("win_rate_pct"),
            "gross_fee_usd": r.get("gross_fee_usd"),
            "rebate_usd": r.get("rebate_usd"),
            "net_pnl_usd": r.get("net_pnl_usd"),
        }
        for r in sym_sorted
    ]

    open_positions = [
        {
            "venue": venue_display_by_id.get(str(p.get("venue_id") or "").lower()) or p.get("venue_name"),
            "symbol": p.get("symbol"),
            "side": p.get("side"),
            "leverage": p.get("leverage"),
            "notional_usd": p.get("notional_usd"),
            "unrealized_pnl_usd": p.get("unrealized_pnl_usd"),
            "has_stop_order": p.get("has_stop_order"),
        }
        for p in (dashboard.get("open_positions") or [])
    ]

    spot_holdings = [
        {
            "venue": venue_display_by_id.get(str(sp.get("venue_id") or "").lower()) or sp.get("venue_name"),
            "asset": sp.get("asset"),
            "qty": sp.get("qty"),
            "market_value_usd": sp.get("market_value_usd"),
        }
        for sp in (dashboard.get("spot_holdings") or [])
    ]

    ext = dashboard.get("extensions") or {}
    cp = ext.get("copy_trading") or {}
    stk = ext.get("stocks") or {}
    stock_holdings = [
        {
            "venue": venue_display_by_id.get(str(h.get("venue_id") or "").lower()) or h.get("venue_name"),
            "symbol": h.get("symbol"),
            "shares": h.get("shares"),
            "market_value_usd": h.get("market_value_usd"),
            "pnl_usd": h.get("pnl_usd"),
        }
        for h in (stk.get("holdings") or [])
    ]

    recent_20_trades = [
        {
            "venue": venue_display_by_id.get(str(t.get("venue_id") or "").lower()) or t.get("venue_name"),
            "symbol": t.get("symbol"),
            "side": t.get("side"),
            "close_time": t.get("close_time_str"),
            "holding_sec": int(t.get("holding_sec") or 0),
            "holding_min": round(float(t.get("holding_sec") or 0) / 60.0, 2),
            "entry_mode": (t.get("entry_leg") or {}).get("mode_code"),
            "entry_bps": (t.get("entry_leg") or {}).get("fee_bps") if (t.get("entry_leg") or {}).get("fee_known", True) else None,
            "entry_gross_fee": (t.get("entry_leg") or {}).get("gross_fee_usd") if (t.get("entry_leg") or {}).get("fee_known", True) else None,
            "exit_mode": (t.get("exit_leg") or {}).get("mode_code"),
            "exit_bps": (t.get("exit_leg") or {}).get("fee_bps") if (t.get("exit_leg") or {}).get("fee_known", True) else None,
            "exit_gross_fee": (t.get("exit_leg") or {}).get("gross_fee_usd") if (t.get("exit_leg") or {}).get("fee_known", True) else None,
            "total_fee_usd": t.get("total_fee_usd"),
            "net_fee_usd": t.get("net_fee_usd"),
            "rebate_usd": t.get("total_rebate_usd"),
            "discount_saved_usd": t.get("total_discount_saved_usd"),
            "net_pnl_usd": t.get("net_pnl_usd"),
            "tags": t.get("tags") or [],
            "note": t.get("note") or "",
        }
        for t in trades[:20]
    ]

    return {
        "range_key": dashboard.get("range_key", "all"),
        "summary": {
            "total_equity_usd": s.get("total_equity_usd"),
            "net_principal_usd": s.get("net_principal_usd"),
            "whole_net_pnl_usd": s.get("whole_net_pnl_usd"),
            "whole_roi_pct": s.get("whole_roi_pct"),
            "range_whole_net_pnl_usd": s.get("range_whole_net_pnl_usd"),
            "range_whole_roi_pct": s.get("range_whole_roi_pct"),
            "futures_net_pnl_usd": s.get("futures_net_pnl_usd"),
            "trade_count": s.get("trade_count"),
            "win_rate_pct": s.get("win_rate_pct"),
            "long_trades": len(long_trades),
            "long_win_rate_pct": long_wr,
            "short_trades": len(short_trades),
            "short_win_rate_pct": short_wr,
            "avg_win_hold_min": avg_win_min,
            "avg_loss_hold_min": avg_loss_min,
            "entry_maker_ratio_pct": entry_maker_ratio,
            "exit_maker_ratio_pct": exit_maker_ratio,
            "total_fee_usd": s.get("total_fee_usd"),
            "total_rebate_usd": s.get("total_rebate_usd"),
            "total_discount_saved_usd": s.get("total_discount_saved_usd"),
            "net_fee_usd": s.get("net_fee_usd"),
            "combined_relief_pct": s.get("combined_relief_pct"),
            "potential_maker_savings_usd": s.get("potential_maker_savings_usd"),
            "max_drawdown_usd": s.get("max_drawdown_usd"),
        },
        "exchanges": exchanges,
        "fee_matrix": fee_matrix,
        "top_symbols": top_symbols,
        "open_positions": open_positions,
        "spot_holdings": spot_holdings,
        "copy_trading": {
            "invested_usd": cp.get("total_invested_usd"),
            "active_equity_usd": cp.get("active_equity_usd"),
            "rebate_total_usd": cp.get("rebate_total_usd"),
            "combined_pnl_usd": cp.get("combined_pnl_usd"),
        },
        "stocks": {
            "equity_usd": stk.get("equity_usd"),
            "unrealized_pnl_usd": stk.get("unrealized_pnl_usd"),
            "non_perp_fees_usd": stk.get("non_perp_fees_usd"),
            "holdings_count": len(stock_holdings),
            "holdings": stock_holdings,
        },
        "stock_commodity_perp_history_count": len(stk.get("perpetuals") or []),
        "goal": dashboard.get("goal"),
        "recent_20_trades": recent_20_trades,
    }


def build_local_highlights(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    s = dashboard.get("summary") or {}
    trade_count = int(s.get("trade_count") or 0)
    all_time_cnt = int(s.get("all_time_trade_count") or 0)
    trades = dashboard.get("trades") or []

    if trade_count <= 0 or not trades:
        sub_zh = (
            f"本周期无成交，暂无可分析样本（全历史累计 {all_time_cnt} 笔交割，可切换至「30天」或「全部」查看历史费率与执行诊断）。"
            if all_time_cnt > 0
            else "本周期无成交，暂无可分析样本。待产生已平仓交割后再生成胜率、持仓时长与挂吃单成本复盘。"
        )
        sub_en = (
            f"No closed trades in this period — insufficient sample for execution analysis ({all_time_cnt} all-time trades available under 30D / All-Time)."
            if all_time_cnt > 0
            else "No closed trades in this period — insufficient sample for execution & fee analysis."
        )
        return [
            {
                "kind": "no_trades",
                "title": "No Closed Trades in Period",
                "title_zh": "本周期无成交",
                "metric": "0",
                "trade_count": 0,
                "body": sub_en,
                "body_zh": sub_zh,
            }
        ]

    win_rate = float(s.get("win_rate_pct") or 0.0)
    net_fee = float(s.get("net_fee_usd") or 0.0)
    relief = float(s.get("combined_relief_pct") or 0.0)
    pot = float(s.get("potential_maker_savings_usd") or 0.0)

    win_holds = [int(t.get("holding_sec") or 0) for t in trades if float(t.get("net_pnl_usd") or 0) > 0]
    loss_holds = [int(t.get("holding_sec") or 0) for t in trades if float(t.get("net_pnl_usd") or 0) < 0]
    avg_win_m = int(sum(win_holds) / len(win_holds) // 60) if win_holds else 0
    avg_loss_m = int(sum(loss_holds) / len(loss_holds) // 60) if loss_holds else 0

    return [
        {
            "kind": "net_fee",
            "title": "Net Fee Burden & Relief",
            "title_zh": "净手续费磨损与减免率",
            "metric": f"-{net_fee:.2f} U ({relief:.1f}%)",
            "amount_usd": -round(net_fee, 2),
            "pct": round(relief, 1),
            "trade_count": trade_count,
            "body": f"Across {trade_count} closed trades, fee rebates & token discounts covered {relief:.1f}% of gross fees.",
            "body_zh": f"共 {trade_count} 笔平仓，返佣与平台币抵扣覆盖了 {relief:.1f}% 的原始手续费，实付净费 -{net_fee:.2f} U。",
        },
        {
            "kind": "win_rate",
            "title": "Win Rate & Hold Discipline",
            "title_zh": "胜率与持仓纪律",
            "metric": f"{win_rate:.1f}%",
            "pct": round(win_rate, 1),
            "avg_win_m": avg_win_m,
            "avg_loss_m": avg_loss_m,
            "body": f"Average winning hold is {avg_win_m}m vs losing hold {avg_loss_m}m.",
            "body_zh": f"整体胜率 {win_rate:.1f}%；盈利单平均持仓 {avg_win_m} 分钟，亏损单平均持仓 {avg_loss_m} 分钟。",
        },
        {
            "kind": "maker_upside",
            "title": "Maker Execution Upside",
            "title_zh": "吃单改挂单增厚空间",
            "metric": f"+{pot:.2f} U",
            "amount_usd": round(pot, 2),
            "body": f"Shifting discretionary taker legs to passive limit orders could retain an extra +{pot:.2f} USDT.",
            "body_zh": f"将可控吃单腿改为被动限价挂单，按各平台实测费率差预计可多留存 +{pot:.2f} USDT 纯利润。",
        },
    ]


def resolve_range_labels(range_key: str) -> tuple[str, str, str]:
    rk = (range_key or "all").strip().lower() or "all"
    if rk == "all":
        return "all", "All-Time", "全部历史"
    if rk == "30d":
        return "30d", "Last 30 Days", "近 30 天"
    if rk == "7d":
        return "7d", "Last 7 Days", "近 7 天"
    if len(rk) == 7 and rk[4] == "-":
        return rk, rk, f"{rk} 月度"
    return rk, rk.upper(), rk.upper()


def build_zero_trade_texts(
    *,
    all_time_cnt: int,
    range_label_en: str,
    range_label_zh: str,
) -> tuple[list[dict[str, Any]], str, str]:
    highlights = build_local_highlights(
        {"summary": {"trade_count": 0, "all_time_trade_count": all_time_cnt}, "trades": []}
    )
    commentary_zh = (
        f"**本周期无成交，暂无可分析样本。** 当前统计区间（{range_label_zh}）内共有 0 笔已平仓合约记录，不生成胜率、持仓时长或挂吃单纪律建议。"
        + (
            f" 账户全历史累计已有 {all_time_cnt} 笔交割，可切换右上角时间区间至「30天」或「全部」生成复盘。"
            if all_time_cnt > 0
            else ""
        )
    )
    commentary_en = (
        f"**No closed trades in this period ({range_label_en}) — insufficient sample for analysis.** "
        "Win-rate, hold-duration, and maker/taker execution coaching require at least one closed trade."
        + (
            f" Switch the period selector to 30D or All-Time to review your {all_time_cnt} historical trades."
            if all_time_cnt > 0
            else ""
        )
    )
    return highlights, commentary_en, commentary_zh


async def generate_ai_review(
    dashboard: dict[str, Any],
    prefs: UserPreferences,
    *,
    client: httpx.AsyncClient | None = None,
    force_local: bool = False,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    highlights = build_local_highlights(dashboard)
    ctx = build_ai_context_payload(dashboard)
    s = dashboard.get("summary") or {}
    equity = float(s.get("total_equity_usd") or 0.0)
    trade_count = int(s.get("trade_count") or 0)
    rk_norm, range_label_en, range_label_zh = resolve_range_labels(str(dashboard.get("range_key") or "all"))

    commentary = ""
    model_used = "local-actuarial"
    execution_mode = "local_only"
    data_transmitted = False
    ai_error = ""
    ai_error_zh = ""

    base = "" if force_local else _normalize_ai_base(prefs.ai_base_url or "")
    api_key = "" if force_local else (prefs.ai_api_key or "").strip()
    model = (prefs.ai_model or "").strip() or "gpt-4o-mini"

    has_closed_trades = trade_count > 0 and bool(ctx["recent_20_trades"])
    input_summary = {
        "exchanges_count": len(ctx["exchanges"]),
        "fee_matrix_count": len(ctx["fee_matrix"]),
        "top_symbols_count": len(ctx["top_symbols"]),
        "recent_trades_count": len(ctx["recent_20_trades"]) if has_closed_trades else 0,
        "open_positions_count": len(ctx["open_positions"]),
        "spot_holdings_count": len(ctx["spot_holdings"]),
        "has_copy_trading": bool(ctx["copy_trading"].get("invested_usd")),
        "has_stocks": bool(ctx["stocks"]["equity_usd"] or ctx["stocks"]["holdings_count"]),
        "stock_commodity_perp_history_count": ctx["stock_commodity_perp_history_count"],
        "excluded_secrets": ["API Key / Secret / Passphrase", "Exchange UID", "Wallet Addresses"],
    }

    if not has_closed_trades:
        all_time_cnt = int(s.get("all_time_trade_count") or 0)
        z_highlights, commentary, commentary_zh = build_zero_trade_texts(
            all_time_cnt=all_time_cnt,
            range_label_en=range_label_en,
            range_label_zh=range_label_zh,
        )
        return {
            "generated_at": generated_at,
            "generated_ms": now_ms,
            "model": "local-actuarial",
            "execution_mode": "zero_trades_local",
            "data_transmitted": False,
            "range_key": rk_norm,
            "range_label": range_label_en,
            "range_label_zh": range_label_zh,
            "trade_count_snapshot": 0,
            "equity_snapshot_usd": equity,
            "stale": False,
            "stale_reason": "",
            "stale_reason_zh": "",
            "highlights": z_highlights,
            "markdown_commentary": commentary,
            "markdown_commentary_zh": commentary_zh,
            "ai_error": "",
            "ai_error_zh": "",
            "input_summary": input_summary,
            "context_preview": None,
        }

    if base and (api_key or "127.0.0.1" in base or "localhost" in base):
        data_transmitted = True
        lang_hint = "简体中文" if prefs.locale == "zh-CN" else "English"
        prompt = (
            "You are a disciplined quantitative trading & fee-optimization risk coach.\n"
            f"Analyze the sanitized multi-exchange ledger JSON below and output 4 concrete, data-backed numbered action items in {lang_hint} "
            "(Markdown numbered list with bold lead-ins, zero fluff):\n"
            "1) Long/Short win-rate asymmetry & holding-duration discipline (cut losers vs let winners run).\n"
            "2) Per-leg Maker vs Taker fee drag & cross-venue fee/rebate routing optimization.\n"
            "3) Top winning/losing symbols concentration & stop-loss posture on open positions.\n"
            "4) Capital allocation across futures, copy trading, equities/spot, and goal pacing (if enabled). "
            "For the selected period use range_whole_net_pnl_usd and range_whole_roi_pct. whole_net_pnl_usd and whole_roi_pct are lifetime totals. "
            "Configured gross rates are fee schedules and have no observed net rate for that side. "
            "Use current holdings and equity for stock allocation. stock_commodity_perp_history_count counts "
            "historical derivatives records grouped by venue and symbol.\n\n"
            "Ledger JSON:\n"
            + json.dumps(ctx, ensure_ascii=False)
        )
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async def _call_gateway(http_c: httpx.AsyncClient) -> str:
            nonlocal ai_error, ai_error_zh
            # 1. Standard OpenAI Chat Completions API
            chat_resp = await http_c.post(
                f"{base}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            if chat_resp.status_code < 400:
                payload = chat_resp.json()
                choices = payload.get("choices") or []
                if choices:
                    txt = str((choices[0].get("message") or {}).get("content") or "").strip()
                    if txt:
                        return txt
            # 2. Fallback to OpenAI Responses API (`/responses`)
            if chat_resp.status_code in (400, 404, 405):
                resp_api = await http_c.post(
                    f"{base}/responses",
                    headers=headers,
                    json={"model": model, "input": prompt},
                )
                if resp_api.status_code < 400:
                    txt = _extract_responses_text(resp_api.json())
                    if txt:
                        return txt
                snippet = resp_api.text[:180].strip()
                ai_error = f"Sanitized request was sent to AI endpoint, which returned HTTP {resp_api.status_code}: {snippet} — fell back to local actuarial rules."
                ai_error_zh = f"本次已向所配 AI 接口发送脱敏汇总请求，但模型返回异常（HTTP {resp_api.status_code}：{snippet}）；下方报告已自动回退为本地精算结果。"
                return ""
            snippet = chat_resp.text[:180].strip()
            ai_error = f"Sanitized request was sent to AI endpoint, which returned HTTP {chat_resp.status_code}: {snippet} — fell back to local actuarial rules."
            ai_error_zh = f"本次已向所配 AI 接口发送脱敏汇总请求，但模型返回异常（HTTP {chat_resp.status_code}：{snippet}）；下方报告已自动回退为本地精算结果。"
            return ""

        try:
            if client is not None:
                commentary = await _call_gateway(client)
            else:
                async with httpx.AsyncClient(timeout=45.0) as c:
                    commentary = await _call_gateway(c)
            if commentary:
                model_used = model
                execution_mode = "remote_llm"
                ai_error = ""
                ai_error_zh = ""
            else:
                execution_mode = "remote_failed_fallback"
        except Exception as exc:
            commentary = ""
            execution_mode = "remote_failed_fallback"
            err_txt = str(exc).strip() or type(exc).__name__
            err_txt = err_txt[:180]
            ai_error = f"Sanitized request attempted to AI endpoint failed ({err_txt}) — fell back to local actuarial rules."
            ai_error_zh = f"本次已尝试向所配 AI 接口发起脱敏汇总请求，但连接失败（{err_txt}）；下方报告已自动回退为本地精算结果。"
    elif base and not api_key:
        ai_error = "AI Base URL is configured without an API Key — generated locally without sending data."
        ai_error_zh = "已填写 AI 接口地址但尚未配置 API Key，本次未外发数据，已直接使用本地精算规则生成。"

    if not commentary:
        en_mk = ctx["summary"]["entry_maker_ratio_pct"]
        ex_mk = ctx["summary"]["exit_maker_ratio_pct"]
        mk_clause_zh = (
            f"进场挂单占比 {en_mk}%，出场挂单占比 {ex_mk}%"
            if (en_mk is not None and ex_mk is not None)
            else "分腿挂吃比例未提供明细拆分"
        )
        mk_clause_en = (
            f"Entry maker {en_mk}% vs Exit maker {ex_mk}%"
            if (en_mk is not None and ex_mk is not None)
            else "per-leg maker/taker split unavailable"
        )
        commentary_zh = (
            f"1. **压缩出场吃单磨损**：当前净手续费 -{float(s.get('net_fee_usd') or 0):.2f} U（{mk_clause_zh}），优先在目标位分批挂限价止盈，预计可额外留存 *+{float(s.get('potential_maker_savings_usd') or 0):.2f} USDT*。\n"
            f"2. **校准多空与持仓纪律**：当前做多胜率 {ctx['summary']['long_win_rate_pct']}%（{ctx['summary']['long_trades']} 笔）、做空胜率 {ctx['summary']['short_win_rate_pct']}%（{ctx['summary']['short_trades']} 笔）；盈利单平均持仓 {ctx['summary']['avg_win_hold_min']} 分钟 vs 亏损单 {ctx['summary']['avg_loss_hold_min']} 分钟，警惕亏损扛单放大回撤。\n"
            "3. **接入任意 OpenAI 兼容大模型**：可在「设置与连接」填入 Base URL / API Key 并点「获取模型列表」选择模型，获得结合最近 20 笔逐笔进出场与复盘笔记的深度点评。"
        )
        commentary_en = (
            f"1. **Reduce exit taker drag**: Net fees are -{float(s.get('net_fee_usd') or 0):.2f} U ({mk_clause_en}). Passive limit exits could retain an extra *+{float(s.get('potential_maker_savings_usd') or 0):.2f} USDT*.\n"
            f"2. **Tighten directional & hold discipline**: Long win rate {ctx['summary']['long_win_rate_pct']}% ({ctx['summary']['long_trades']} trades) vs Short {ctx['summary']['short_win_rate_pct']}% ({ctx['summary']['short_trades']} trades); average winning hold {ctx['summary']['avg_win_hold_min']}m vs losing {ctx['summary']['avg_loss_hold_min']}m.\n"
            "3. **Connect any OpenAI-compatible LLM**: Configure Base URL & API Key in Settings and click Fetch Models for deep coaching over your top symbols and recent 20 trades."
        )
        commentary = commentary_en
    else:
        commentary_zh = commentary

    return {
        "generated_at": generated_at,
        "generated_ms": now_ms,
        "model": model_used,
        "execution_mode": execution_mode,
        "data_transmitted": data_transmitted,
        "range_key": rk_norm,
        "range_label": range_label_en,
        "range_label_zh": range_label_zh,
        "trade_count_snapshot": trade_count,
        "equity_snapshot_usd": equity,
        "stale": False,
        "stale_reason": "",
        "stale_reason_zh": "",
        "highlights": highlights,
        "markdown_commentary": commentary,
        "markdown_commentary_zh": commentary_zh,
        "ai_error": ai_error,
        "ai_error_zh": ai_error_zh,
        "input_summary": input_summary,
        "context_preview": ctx,
    }
