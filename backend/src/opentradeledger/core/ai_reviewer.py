"""Generic OpenAI-compatible AI trade & cost reviewer with model discovery and full prompt transparency."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from typing import Any

import httpx

from opentradeledger.config import UserPreferences


def _normalize_ai_base(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/models"):
        if b.endswith(suffix):
            b = b[: -len(suffix)].rstrip("/")
    return b


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
    base = _normalize_ai_base(base_url)
    if not base:
        raise ValueError("AI Base URL is required")

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

    entry_maker_cnt = sum(1 for t in trades if (t.get("entry_leg") or {}).get("role") == "MAKER")
    exit_maker_cnt = sum(1 for t in trades if (t.get("exit_leg") or {}).get("role") == "MAKER")
    n_trades = max(1, len(trades))

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
            "maker_gross_net_bps": f"{r.get('maker_gross_bps', 0)}/{r.get('maker_net_bps', 0)}",
            "taker_gross_net_bps": f"{r.get('taker_gross_bps', 0)}/{r.get('taker_net_bps', 0)}",
            "gross_fee_usd": r.get("gross_fee_usd"),
            "discount_saved_usd": r.get("discount_saved_usd"),
            "rebate_usd": r.get("rebate_usd"),
            "net_fee_usd": r.get("net_fee_usd"),
            "combined_relief_pct": r.get("combined_relief_pct"),
        }
        for r in (dashboard.get("fee_matrix") or [])
    ]

    sym_sorted = sorted(
        dashboard.get("symbol_attribution") or [],
        key=lambda r: abs(float(r.get("net_pnl_usd") or 0.0)),
        reverse=True,
    )[:15]
    top_symbols = [
        {
            "venue": r.get("venue_name"),
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
            "venue": p.get("venue_name"),
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
            "venue": sp.get("venue_name"),
            "asset": sp.get("asset"),
            "qty": sp.get("qty"),
            "market_value_usd": sp.get("market_value_usd"),
        }
        for sp in (dashboard.get("spot_holdings") or [])
    ]

    ext = dashboard.get("extensions") or {}
    cp = ext.get("copy_trading") or {}
    stk = ext.get("stocks") or {}

    recent_20_trades = [
        {
            "venue": t.get("venue_name"),
            "symbol": t.get("symbol"),
            "side": t.get("side"),
            "close_time": t.get("close_time_str"),
            "holding_min": round(float(t.get("holding_sec") or 0) / 60.0, 1),
            "entry_mode": (t.get("entry_leg") or {}).get("mode_code"),
            "entry_bps": (t.get("entry_leg") or {}).get("fee_bps"),
            "entry_gross_fee": (t.get("entry_leg") or {}).get("gross_fee_usd"),
            "exit_mode": (t.get("exit_leg") or {}).get("mode_code"),
            "exit_bps": (t.get("exit_leg") or {}).get("fee_bps"),
            "exit_gross_fee": (t.get("exit_leg") or {}).get("gross_fee_usd"),
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
            "futures_net_pnl_usd": s.get("futures_net_pnl_usd"),
            "trade_count": s.get("trade_count"),
            "win_rate_pct": s.get("win_rate_pct"),
            "long_trades": len(long_trades),
            "long_win_rate_pct": long_wr,
            "short_trades": len(short_trades),
            "short_win_rate_pct": short_wr,
            "avg_win_hold_min": avg_win_min,
            "avg_loss_hold_min": avg_loss_min,
            "entry_maker_ratio_pct": round((entry_maker_cnt / n_trades) * 100.0, 1),
            "exit_maker_ratio_pct": round((exit_maker_cnt / n_trades) * 100.0, 1),
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
            "holdings_count": len(stk.get("holdings") or []),
            "perps_count": len(stk.get("perpetuals") or []),
        },
        "goal": dashboard.get("goal"),
        "recent_20_trades": recent_20_trades,
    }


def build_local_highlights(dashboard: dict[str, Any]) -> list[dict[str, str]]:
    s = dashboard.get("summary") or {}
    trade_count = int(s.get("trade_count") or 0)
    win_rate = float(s.get("win_rate_pct") or 0.0)
    net_fee = float(s.get("net_fee_usd") or 0.0)
    relief = float(s.get("combined_relief_pct") or 0.0)
    pot = float(s.get("potential_maker_savings_usd") or 0.0)

    trades = dashboard.get("trades") or []
    win_holds = [int(t.get("holding_sec") or 0) for t in trades if float(t.get("net_pnl_usd") or 0) > 0]
    loss_holds = [int(t.get("holding_sec") or 0) for t in trades if float(t.get("net_pnl_usd") or 0) < 0]
    avg_win_m = int(sum(win_holds) / len(win_holds) // 60) if win_holds else 0
    avg_loss_m = int(sum(loss_holds) / len(loss_holds) // 60) if loss_holds else 0

    return [
        {
            "title": "Net Fee Burden & Relief",
            "title_zh": "净手续费磨损与减免率",
            "metric": f"-{net_fee:.2f} U ({relief:.1f}%)",
            "body": f"Across {trade_count} closed trades, fee rebates & token discounts covered {relief:.1f}% of gross fees.",
            "body_zh": f"共 {trade_count} 笔平仓，返佣与平台币抵扣覆盖了 {relief:.1f}% 的原始手续费，实付净费 -{net_fee:.2f} U。",
        },
        {
            "title": "Win Rate & Hold Discipline",
            "title_zh": "胜率与持仓纪律",
            "metric": f"{win_rate:.1f}%",
            "body": f"Average winning hold is {avg_win_m}m vs losing hold {avg_loss_m}m.",
            "body_zh": f"整体胜率 {win_rate:.1f}%；盈利单平均持仓 {avg_win_m} 分钟，亏损单平均持仓 {avg_loss_m} 分钟。",
        },
        {
            "title": "Maker Execution Upside",
            "title_zh": "吃单改挂单增厚空间",
            "metric": f"+{pot:.2f} U",
            "body": f"Shifting discretionary taker legs to passive limit orders could retain an extra +{pot:.2f} USDT.",
            "body_zh": f"将可控吃单腿改为被动限价挂单，按各平台实测费率差预计可多留存 +{pot:.2f} USDT 纯利润。",
        },
    ]


async def generate_ai_review(
    dashboard: dict[str, Any],
    prefs: UserPreferences,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    highlights = build_local_highlights(dashboard)
    ctx = build_ai_context_payload(dashboard)
    s = dashboard.get("summary") or {}
    equity = float(s.get("total_equity_usd") or 0.0)

    commentary = ""
    model_used = "local-actuarial"

    base = _normalize_ai_base(prefs.ai_base_url or "")
    api_key = (prefs.ai_api_key or "").strip()
    model = (prefs.ai_model or "").strip() or "gpt-4o-mini"

    if base and (api_key or "127.0.0.1" in base or "localhost" in base):
        lang_hint = "简体中文" if prefs.locale == "zh-CN" else "English"
        prompt = (
            "You are a disciplined quantitative trading & fee-optimization risk coach.\n"
            f"Analyze the sanitized multi-exchange ledger JSON below and output 4 concrete, data-backed numbered action items in {lang_hint} "
            "(Markdown numbered list with bold lead-ins, zero fluff):\n"
            "1) Long/Short win-rate asymmetry & holding-duration discipline (cut losers vs let winners run).\n"
            "2) Per-leg Maker vs Taker fee drag & cross-venue fee/rebate routing optimization.\n"
            "3) Top winning/losing symbols concentration & stop-loss posture on open positions.\n"
            "4) Capital allocation across futures, copy trading, equities/spot, and goal pacing (if enabled).\n\n"
            "Ledger JSON:\n"
            + json.dumps(ctx, ensure_ascii=False)
        )
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async def _call_gateway(http_c: httpx.AsyncClient) -> str:
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
            return ""

        try:
            if client is not None:
                commentary = await _call_gateway(client)
            else:
                async with httpx.AsyncClient(timeout=45.0) as c:
                    commentary = await _call_gateway(c)
            if commentary:
                model_used = model
        except Exception:
            commentary = ""

    if not commentary:
        commentary_zh = (
            f"1. **压缩出场吃单磨损**：当前净手续费 -{float(s.get('net_fee_usd') or 0):.2f} U（进场挂单占比 {ctx['summary']['entry_maker_ratio_pct']}%，出场挂单占比 {ctx['summary']['exit_maker_ratio_pct']}%），优先在目标位分批挂限价止盈，预计可额外留存 *+{float(s.get('potential_maker_savings_usd') or 0):.2f} USDT*。\n"
            f"2. **校准多空与持仓纪律**：当前做多胜率 {ctx['summary']['long_win_rate_pct']}%（{ctx['summary']['long_trades']} 笔）、做空胜率 {ctx['summary']['short_win_rate_pct']}%（{ctx['summary']['short_trades']} 笔）；盈利单平均持仓 {ctx['summary']['avg_win_hold_min']} 分钟 vs 亏损单 {ctx['summary']['avg_loss_hold_min']} 分钟，警惕亏损扛单放大回撤。\n"
            "3. **接入任意 OpenAI 兼容大模型**：可在「设置与连接」填入 Base URL / API Key 并点「获取模型列表」选择模型，获得结合最近 20 笔逐笔进出场与复盘笔记的深度点评。"
        )
        commentary_en = (
            f"1. **Reduce exit taker drag**: Net fees are -{float(s.get('net_fee_usd') or 0):.2f} U (Entry maker {ctx['summary']['entry_maker_ratio_pct']}% vs Exit maker {ctx['summary']['exit_maker_ratio_pct']}%). Passive limit exits could retain an extra *+{float(s.get('potential_maker_savings_usd') or 0):.2f} USDT*.\n"
            f"2. **Tighten directional & hold discipline**: Long win rate {ctx['summary']['long_win_rate_pct']}% ({ctx['summary']['long_trades']} trades) vs Short {ctx['summary']['short_win_rate_pct']}% ({ctx['summary']['short_trades']} trades); average winning hold {ctx['summary']['avg_win_hold_min']}m vs losing {ctx['summary']['avg_loss_hold_min']}m.\n"
            "3. **Connect any OpenAI-compatible LLM**: Configure Base URL & API Key in Settings and click Fetch Models for deep coaching over your top symbols and recent 20 trades."
        )
        commentary = commentary_en
    else:
        commentary_zh = commentary

    input_summary = {
        "exchanges_count": len(ctx["exchanges"]),
        "fee_matrix_count": len(ctx["fee_matrix"]),
        "top_symbols_count": len(ctx["top_symbols"]),
        "recent_trades_count": len(ctx["recent_20_trades"]),
        "open_positions_count": len(ctx["open_positions"]),
        "spot_holdings_count": len(ctx["spot_holdings"]),
        "has_copy_trading": bool(ctx["copy_trading"].get("invested_usd")),
        "has_stocks": bool(ctx["stocks"].get("equity_usd") or ctx["stocks"].get("perps_count")),
        "excluded_secrets": ["API Key / Secret / Passphrase", "Exchange UID", "Wallet Addresses"],
    }

    return {
        "generated_at": generated_at,
        "generated_ms": now_ms,
        "model": model_used,
        "equity_snapshot_usd": equity,
        "stale": False,
        "highlights": highlights,
        "markdown_commentary": commentary,
        "markdown_commentary_zh": commentary_zh,
        "input_summary": input_summary,
        "context_preview": ctx,
    }
