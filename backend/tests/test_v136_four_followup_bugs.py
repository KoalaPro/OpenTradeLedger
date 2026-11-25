"""TDD regression suite for the 4 follow-up audit findings + transient exchange timeout noise suppression."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.config import NotificationSettings, UserPreferences
from opentradeledger.core.ai_reviewer import build_ai_context_payload, generate_ai_review
from opentradeledger.core.copy_watcher import CopyWatcherService
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.sync_manager import SyncManager
from opentradeledger.core.venue_health import VenueHealthTracker, humanize_exchange_error
from opentradeledger.security.vault import CredentialVault, ExchangeCredentialRecord


def test_gate_unsplit_fee_populates_fee_matrix_and_ai_payload_consistently(tmp_path: Path) -> None:
    """Issue 1: Gate trade with total fee -1 U and no per-leg split must report 1 U in fee_matrix & AI context, not 0 U or 0.00 bps."""
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    gate_scope = store.venue_scope("gate")
    gate_scope.upsert_records(
        "spot_account_book",
        [{"id": "dep1", "time": 1732000000000, "type": "deposit", "code": "", "currency": "USDT", "change": "1000"}],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["time"]),
    )
    gate_scope.upsert_records(
        "futures_closes",
        [
            {
                "contract": "BTC_USDT",
                "side": "long",
                "first_open_time": 1732100000,
                "time": 1732103600,
                "pnl": "100.00",
                "pnl_pnl": "101.00",
                "pnl_fee": "-1.00",
                "pnl_fund": "0",
                "accum_size": "200",  # 200 * 0.0001 = 0.02 BTC (~$2000 per leg, ~$4000 total notional)
                "long_price": "99500",
                "short_price": "104550",
            }
        ],
        key_fn=lambda r: f"{r['contract']}_{r['time']}",
        ts_fn=lambda r: float(r["time"]),
    )
    gate_scope.set_kv(
        "live_bundle",
        {
            "wallet_total": {"total": {"amount": "1100.00"}, "details": {"futures": {"amount": "1100.00"}}},
            "futures_account": {"total": "1100.00", "available": "1100.00"},
            "unified": {},
            "futures_positions": [],
        },
    )

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")

    assert len(dash["trades"]) == 1
    tr = dash["trades"][0]
    assert tr["gross_pnl_usd"] == pytest.approx(101.0)
    assert tr["total_fee_usd"] == pytest.approx(1.0)
    assert tr["net_pnl_usd"] == pytest.approx(100.0)
    assert tr["entry_leg"]["fee_known"] is False
    assert tr["exit_leg"]["fee_known"] is False

    gate_fee_row = next(r for r in dash["fee_matrix"] if r["venue_id"] == "gate")
    assert gate_fee_row["gross_fee_usd"] == pytest.approx(1.0)
    assert gate_fee_row["net_fee_usd"] == pytest.approx(1.0)
    assert gate_fee_row["maker_bps_known"] is False
    assert gate_fee_row["taker_bps_known"] is False
    assert gate_fee_row["blended_gross_bps"] > 0

    ctx = build_ai_context_payload(dash)
    ai_sym = next(s for s in ctx["top_symbols"] if "BTC" in str(s["symbol"]))
    ai_fee = next(r for r in ctx["fee_matrix"] if str(r["venue"]).lower().startswith("gate"))
    assert ai_sym["gross_fee_usd"] == pytest.approx(1.0)
    assert ai_fee["gross_fee_usd"] == pytest.approx(1.0)
    assert ai_fee["net_fee_usd"] == pytest.approx(1.0)
    assert ai_fee["maker_gross_net_bps"] == "unknown"
    assert ai_fee["taker_gross_net_bps"] == "unknown"
    assert ai_fee["blended_net_bps"] > 0


def test_legacy_cached_zero_trade_ai_report_is_upgraded_automatically_on_read(tmp_path: Path) -> None:
    """Issue 2: A pre-1.3.5 cached 7d 0-trade report must not keep showing 0% win rate / 0m hold / hold-loser warnings after upgrade."""
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    # Seed an account with 1 old trade 60 days ago so 7d has 0 trades
    store.upsert_plugin_snapshot(
        "okx",
        {
            "balances": [{"totalEq": "1050.00", "details": [{"ccy": "USDT", "availBal": "1050.00"}]}],
            "deposits": [{"depId": "d1", "ccy": "USDT", "amt": "1000.00", "state": "2"}],
            "withdrawals": [],
            "positions_history": [
                {
                    "posId": "p1",
                    "instId": "BTC-USDT-SWAP",
                    "direction": "long",
                    "openAvgPx": "80000",
                    "closeAvgPx": "85000",
                    "closeTotalPos": "1",
                    "cTime": "1700000000000",
                    "uTime": "1700003600000",
                    "pnl": "50.00",
                    "fee": "0",
                    "realizedPnl": "50.00",
                }
            ],
            "fills": [],
            "observed_rebate_ratio": 0.0,
        },
    )

    # Simulate legacy 1.3.3 cached 7d report in SQLite
    store.save_ai_review(
        {
            "generated_at": "2026-11-24 10:00:00 UTC",
            "generated_ms": 1763978400000,
            "model": "local-actuarial",
            "range_key": "7d",
            "range_label": "Last 7 Days",
            "range_label_zh": "近 7 天",
            "trade_count_snapshot": 0,
            "equity_snapshot_usd": 1050.0,
            "highlights": [
                {
                    "title": "Win Rate & Hold Discipline",
                    "title_zh": "胜率与持仓纪律",
                    "metric": "0.0%",
                    "body": "Average winning hold is 0m vs losing hold 0m.",
                    "body_zh": "整体胜率 0.0%；盈利单平均持仓 0 分钟，亏损单平均持仓 0 分钟。",
                }
            ],
            "markdown_commentary": "1. Reduce exit taker drag...",
            "markdown_commentary_zh": "1. 分批挂限价止盈... 2. 警惕亏损扛单放大回撤。",
        },
        1050.0,
    )

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash_7d = svc.get_dashboard(range_key="7d")
    ai = dash_7d["ai_review"]
    assert ai is not None
    assert ai["highlights"][0]["kind"] == "no_trades"
    assert "暂无可分析样本" in ai["markdown_commentary_zh"]
    assert "警惕亏损扛单" not in ai["markdown_commentary_zh"]
    assert ai["execution_mode"] == "zero_trades_local"
    assert ai["data_transmitted"] is False


@pytest.mark.asyncio
async def test_copy_watcher_readding_existing_target_preserves_disabled_toggles(tmp_path: Path) -> None:
    """Issue 3: Re-pasting an already-watched portfolio ID must NOT re-enable disabled monitoring/slot/trade toggles."""
    pid = "5075281354358777856"

    def _handler(request: httpx.Request) -> httpx.Response:
        if "lead-portfolio/detail" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": "000000",
                    "data": {
                        "leadPortfolioId": pid,
                        "nickname": "鹰眼测试员",
                        "status": "ACTIVE",
                        "portfolioType": "PUBLIC",
                        "currentCopyCount": 500,
                        "maxCopyCount": 500,
                        "fixedAmountMinCopyUsd": 100,
                        "profitSharingRate": "10",
                    },
                },
            )
        return httpx.Response(200, json={"code": "000000", "data": {"list": []}})

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        store = LedgerStore(tmp_path)
        watcher = CopyWatcherService(store=store, http_client=mock_http)
        await watcher.add_targets(pid, slot_alert=True, trade_alert=True)
        watcher.update_target(pid, enabled=False, slot_alert=False, trade_alert=False)

        # User pastes the same ID again while form defaults are True/True
        await watcher.add_targets(pid, slot_alert=True, trade_alert=True)
        st = watcher.get_status(demo_mode=False)
        item = next(x for x in st["items"] if x["portfolio_id"] == pid)
        assert item["enabled"] is False
        assert item["slot_alert"] is False
        assert item["trade_alert"] is False
    finally:
        await mock_http.aclose()


@pytest.mark.asyncio
async def test_ai_review_transmission_provenance_across_unconfigured_zero_503_and_200() -> None:
    """Issue 4: Unambiguous `execution_mode` and `data_transmitted` across unconfigured, 0-trade, 503 fallback, and 200 OK."""
    base_dash_with_trade: dict[str, Any] = {
        "range_key": "7d",
        "summary": {
            "total_equity_usd": 1000.0,
            "net_principal_usd": 1000.0,
            "whole_net_pnl_usd": 0.0,
            "whole_roi_pct": 0.0,
            "futures_net_pnl_usd": 10.0,
            "trade_count": 1,
            "all_time_trade_count": 1,
            "win_rate_pct": 100.0,
            "total_fee_usd": 1.0,
            "total_rebate_usd": 0.0,
            "total_discount_saved_usd": 0.0,
            "net_fee_usd": 1.0,
            "combined_relief_pct": 0.0,
            "potential_maker_savings_usd": 0.0,
            "max_drawdown_usd": 0.0,
        },
        "trades": [
            {
                "symbol": "BTC_USDT",
                "side": "LONG",
                "net_pnl_usd": 10.0,
                "holding_sec": 600,
                "total_fee_usd": 1.0,
                "total_rebate_usd": 0.0,
                "total_discount_saved_usd": 0.0,
                "entry_leg": {"role": "MAKER", "mode_code": "LIMIT_MAKER", "fee_known": True, "fee_bps": 2.0, "gross_fee_usd": 0.5},
                "exit_leg": {"role": "TAKER", "mode_code": "MARKET_TAKER", "fee_known": True, "fee_bps": 5.0, "gross_fee_usd": 0.5},
            }
        ],
        "exchanges": [],
        "fee_matrix": [],
        "symbol_attribution": [],
        "open_positions": [],
        "spot_holdings": [],
        "extensions": {},
    }
    zero_trade_dash = {
        **base_dash_with_trade,
        "summary": {**base_dash_with_trade["summary"], "trade_count": 0},
        "trades": [],
    }

    requests_seen: list[str] = []

    def _ai_503_handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(str(req.url))
        return httpx.Response(503, text="Service Unavailable: upstream overloaded")

    def _ai_200_handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(str(req.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "1. **复盘结论**：执行良好。"}}]})

    # 1. Unconfigured / force_local
    unconf_prefs = UserPreferences()
    r_local = await generate_ai_review(base_dash_with_trade, unconf_prefs, force_local=True)
    assert r_local["execution_mode"] == "local_only"
    assert r_local["data_transmitted"] is False

    # 2. Configured + 0 trades => short-circuited locally, 0 requests sent, data_transmitted is False
    conf_prefs = UserPreferences(ai_base_url="https://llm.example.com/v1", ai_api_key="sk-test", ai_model="gpt-4o-mini")
    async with httpx.AsyncClient(transport=httpx.MockTransport(_ai_503_handler)) as c503:
        r_zero = await generate_ai_review(zero_trade_dash, conf_prefs, client=c503)
        assert len(requests_seen) == 0
        assert r_zero["execution_mode"] == "zero_trades_local"
        assert r_zero["data_transmitted"] is False

        # 3. Configured + 1 trade + HTTP 503 => request WAS sent, data_transmitted is True, execution_mode is remote_failed_fallback
        r_503 = await generate_ai_review(base_dash_with_trade, conf_prefs, client=c503)
        assert len(requests_seen) == 1
        assert r_503["execution_mode"] == "remote_failed_fallback"
        assert r_503["data_transmitted"] is True
        assert "503" in r_503["ai_error_zh"]
        assert "已向" in r_503["ai_error_zh"]

    # 4. Configured + 1 trade + HTTP 200 => remote_llm, data_transmitted is True
    async with httpx.AsyncClient(transport=httpx.MockTransport(_ai_200_handler)) as c200:
        r_200 = await generate_ai_review(base_dash_with_trade, conf_prefs, client=c200)
        assert r_200["execution_mode"] == "remote_llm"
        assert r_200["data_transmitted"] is True
        assert r_200["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_empty_httpx_readtimeout_classified_as_network_and_debounced_before_bark_alert(tmp_path: Path) -> None:
    """EasiCoin live issue: empty `httpx.ReadTimeout('')` must classify as NETWORK_UNREACHABLE and debounce single-tick timeouts."""
    diag = humanize_exchange_error("easicoin", httpx.ReadTimeout(""))
    assert diag["code"] == "NETWORK_UNREACHABLE"
    assert "Unknown API error" not in diag["reason_zh"]

    diag_500 = humanize_exchange_error("gate", RuntimeError("Gate GET /wallet/total_balance failed with HTTP 500: tradfi_spot"))
    assert diag_500["code"] == "UPSTREAM_5XX"

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    vault.upsert(ExchangeCredentialRecord(venue_id="easicoin", api_key="k12345678", api_secret="s12345678", enabled=True))
    reg = build_default_registry()
    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    svc.update_preferences({"notifications": NotificationSettings(bark_push_key="bark_test_key").model_dump()})

    adapter = reg.get("easicoin")
    assert adapter is not None

    async def _fail_timeout(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ReadTimeout("")

    sent_titles: list[str] = []

    async def _fake_notify(*, title: str, body: str) -> None:
        sent_titles.append(f"{title}: {body}")

    mgr = SyncManager(store=store, vault=vault, registry=reg, ledger_service=svc, health_tracker=VenueHealthTracker(store))
    mgr._maybe_notify = _fake_notify  # type: ignore[method-assign]
    adapter.sync_venue = _fail_timeout  # type: ignore[method-assign]

    # 1st and 2nd transient timeout: NO Bark spam!
    await mgr.sync_all(force_all=False)
    assert len(sent_titles) == 0
    await mgr.sync_all(force_all=False)
    assert len(sent_titles) == 0

    # 3rd consecutive failure: sends 1 alert
    await mgr.sync_all(force_all=False)
    assert len(sent_titles) == 1
    assert "Unknown API error" not in sent_titles[0]
