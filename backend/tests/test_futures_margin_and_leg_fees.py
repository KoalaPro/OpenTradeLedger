"""TDD reproduction & regression tests for the 4 v1.3.5 actuarial & logic bugs."""

from __future__ import annotations

import asyncio
from pathlib import Path
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.api.app import create_app
from opentradeledger.config import UserPreferences
from opentradeledger.core.ai_reviewer import generate_ai_review
from opentradeledger.core.copy_watcher import CopyWatcherService
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


def test_bug1_futures_equity_never_misclassified_as_spot_when_available_margin_drops(tmp_path: Path) -> None:
    """2,500 USDT pure futures equity (0 spot) must stay 2,500 in futures_margin_usd when available=1,500 or 0."""
    for avail_str in ("1500.00", "0.00"):
        case_dir = tmp_path / f"gate_avail_{avail_str}"
        store = LedgerStore(case_dir)
        vault = CredentialVault(case_dir)
        g_scope = store.venue_scope("gate")
        g_scope.set_kv(
            "live_bundle",
            {
                "wallet_total": {
                    "total": {"amount": "2500.00", "unrealised_pnl": "0.0"},
                    "details": {"futures": {"amount": "2500.00"}},
                },
                "futures_account": {
                    "total": "2500.00",
                    "available": avail_str,
                    "order_margin": str(2500.0 - float(avail_str)),
                    "position_margin": "0.00",
                    "unrealised_pnl": "0.00",
                },
                "unified": {},
            },
        )
        svc = LedgerService(store, vault, build_default_registry(), default_demo_mode=False)
        dash = svc.get_dashboard()
        assert dash["summary"]["total_equity_usd"] == 2500.0
        eb = dash["equity_breakdown"]
        assert eb["futures_margin_usd"] == 2500.0, f"Expected 2500 futures when available={avail_str}, got {eb}"
        assert eb["spot_and_other_usd"] == 0.0, f"Expected 0 spot when available={avail_str}, got {eb}"

    # Gate Unified Margin (enable_credit=True, fa.total="0.000000007", available=1500 or 0, uni USDT equity=2500)
    for avail_str in ("1500.00", "0.00"):
        u_dir = tmp_path / f"gate_uni_{avail_str}"
        u_store = LedgerStore(u_dir)
        u_store.venue_scope("gate").set_kv(
            "live_bundle",
            {
                "wallet_total": {
                    "total": {"amount": "2500.00"},
                    "details": {"futures": {"amount": "0"}, "spot": {"amount": "2500.00"}},
                },
                "futures_account": {
                    "total": "0.000000007",
                    "available": avail_str,
                    "position_margin": "0",
                    "order_margin": "0",
                    "unrealised_pnl": "0",
                    "enable_credit": True,
                },
                "unified": {
                    "mode": "multi_currency",
                    "unified_account_total_equity": "2500.00",
                    "balances": {"USDT": {"available": avail_str, "freeze": str(2500.0 - float(avail_str)), "equity": "2500.00"}},
                },
            },
        )
        u_dash = LedgerService(u_store, CredentialVault(u_dir), build_default_registry(), default_demo_mode=False).get_dashboard()
        assert u_dash["summary"]["total_equity_usd"] == 2500.0
        assert u_dash["equity_breakdown"]["futures_margin_usd"] == 2500.0
        assert u_dash["equity_breakdown"]["spot_and_other_usd"] == 0.0

    # Also verify across Binance / OKX / Bybit unified/PM accounts with 2,500 futures equity and 0 available
    b_dir = tmp_path / "bn_zero_avail"
    b_store = LedgerStore(b_dir)
    b_store.venue_scope("binance").set_kv(
        "live_bundle",
        {
            "uid": "10001",
            "pm_account": {"accountEquity": "2500.00", "totalAvailableBalance": "0.00"},
            "spot_balances": [],
            "wallet_balances": [],
            "futures_positions": [],
        },
    )
    b_dash = LedgerService(b_store, CredentialVault(b_dir), build_default_registry(), default_demo_mode=False).get_dashboard()
    assert b_dash["summary"]["total_equity_usd"] == 2500.0
    assert b_dash["equity_breakdown"]["futures_margin_usd"] == 2500.0
    assert b_dash["equity_breakdown"]["spot_and_other_usd"] == 0.0

    o_dir = tmp_path / "okx_partial_avail"
    o_store = LedgerStore(o_dir)
    o_store.venue_scope("okx").set_kv(
        "snapshot",
        {
            "balances": [
                {
                    "totalEq": "2500.00",
                    "details": [{"ccy": "USDT", "eq": "2500.00", "eqUsd": "2500.00", "availBal": "1500.00"}],
                }
            ],
            "funding": [],
            "positions": [],
            "positions_history": [],
            "fills": [],
            "bills": [],
            "deposits": [{"ccy": "USDT", "amt": "2500.00", "state": "2"}],
            "withdrawals": [],
        },
    )
    o_dash = LedgerService(o_store, CredentialVault(o_dir), build_default_registry(), default_demo_mode=False).get_dashboard()
    assert o_dash["summary"]["total_equity_usd"] == 2500.0
    assert o_dash["equity_breakdown"]["futures_margin_usd"] == 2500.0
    assert o_dash["equity_breakdown"]["spot_and_other_usd"] == 0.0


def test_bug2_gate_unsplit_total_fee_marks_leg_fee_unknown(tmp_path: Path) -> None:
    """When Gate raw sample only provides total position fee 1 U without per-fill split, legs must mark fee_known=False."""
    now_s = int(time.time())
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    g_scope = store.venue_scope("gate")
    g_scope.set_kv(
        "live_bundle",
        {
            "wallet_total": {"total": {"amount": "1000.00"}},
            "futures_account": {"total": "1000.00", "available": "1000.00"},
        },
    )
    g_scope.upsert_records(
        "futures_closes",
        [
            {
                "contract": "BTC_USDT",
                "side": "long",
                "first_open_time": now_s - 3600,
                "time": now_s - 600,
                "max_size": "10",
                "long_price": "90000",
                "short_price": "90100",
                "pnl_pnl": "10.00",
                "pnl_fee": "-1.00",
                "pnl_fund": "0.00",
                "pnl": "9.00",
            }
        ],
        key_fn=lambda r: f"{r['contract']}_{r['time']}",
        ts_fn=lambda r: float(r["time"]),
    )
    svc = LedgerService(store, vault, build_default_registry(), default_demo_mode=False)
    dash = svc.get_dashboard()
    assert len(dash["trades"]) == 1
    tr = dash["trades"][0]
    assert tr["total_fee_usd"] == 1.0
    assert tr["net_fee_usd"] == 1.0
    assert tr["entry_leg"]["fee_known"] is False
    assert tr["exit_leg"]["fee_known"] is False
    assert tr["entry_leg"]["mode_code"] == "UNKNOWN"
    assert tr["exit_leg"]["mode_code"] == "UNKNOWN"


@pytest.mark.anyio
async def test_bug3_hiding_copy_watcher_tab_keeps_background_radar_scanning(tmp_path: Path) -> None:
    """Toggling modules.copy_watcher=False only hides the sidebar tab; background radar must keep scanning."""
    pid = "5075281354358777856"
    scans: asyncio.Queue[str] = asyncio.Queue()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "lead-portfolio/detail" in url:
            scans.put_nowait(request.url.params["portfolioId"])
            return httpx.Response(
                200,
                json={
                    "code": "000000",
                    "data": {
                        "leadPortfolioId": pid,
                        "nickname": "TestTrader",
                        "status": "ACTIVE",
                        "portfolioType": "PUBLIC",
                        "currentCopyCount": 500,
                        "maxCopyCount": 500,
                        "fixedAmountMinCopyUsd": 100,
                        "profitSharingRate": "0.1",
                    },
                },
            )
        return httpx.Response(200, json={"code": "000000", "data": {"list": []}})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = LedgerStore(tmp_path)
    store.set_meta("demo_mode_override", False)
    prefs = store.get_preferences()
    prefs.modules.copy_watcher = False  # User hid the sidebar tab!
    store.save_preferences(prefs)

    watcher = CopyWatcherService(
        store=store,
        http_client=mock_client,
        interval_sec=3.0,
    )
    watcher.set_target_ids_bulk([pid])
    await watcher.start()
    try:
        assert await asyncio.wait_for(scans.get(), timeout=5) == pid
        watcher.notify_config_changed()
        assert await asyncio.wait_for(scans.get(), timeout=5) == pid
    finally:
        await watcher.stop()
        await mock_client.aclose()


@pytest.mark.anyio
async def test_bug4_zero_trades_period_returns_empty_sample_notice_without_fake_discipline_advice(tmp_path: Path) -> None:
    """When period has 0 trades (e.g. 7d), local review must report no sample rather than 0.0% win rate / 0m hold advice."""
    now_s = int(time.time())
    app = create_app(
        data_dir=tmp_path,
        demo_mode=False,
        enable_background_sync=False,
        auto_prompt_setup=False,
    )
    store = LedgerStore(tmp_path)
    g_scope = store.venue_scope("gate")
    g_scope.set_kv(
        "live_bundle",
        {
            "wallet_total": {"total": {"amount": "2000.00"}},
            "futures_account": {"total": "2000.00", "available": "2000.00"},
        },
    )
    # Only 1 trade from 25 days ago -> 7d has 0 trades!
    g_scope.upsert_records(
        "futures_closes",
        [
            {
                "contract": "BTC_USDT",
                "side": "long",
                "first_open_time": now_s - 25 * 86400 - 600,
                "time": now_s - 25 * 86400,
                "max_size": "10",
                "long_price": "90000",
                "short_price": "91000",
                "pnl_pnl": "100.00",
                "pnl_fee": "-1.00",
                "pnl_fund": "0.00",
                "pnl": "99.00",
            }
        ],
        key_fn=lambda r: f"{r['contract']}_{r['time']}",
        ts_fn=lambda r: float(r["time"]),
    )

    client = TestClient(app)
    rev_7d = client.post("/api/v1/ai-review", json={"range_key": "7d", "mode": "local_rules"}).json()
    assert rev_7d["trade_count_snapshot"] == 0
    combined_zh = " ".join(
        [rev_7d.get("markdown_commentary_zh") or ""]
        + [h.get("title_zh", "") + " " + h.get("body_zh", "") + " " + h.get("metric", "") for h in rev_7d["highlights"]]
    )
    assert "本周期无成交" in combined_zh and "暂无可分析样本" in combined_zh
    assert "胜率 0.0%" not in combined_zh
    assert "0 分钟" not in combined_zh
    assert "分批挂限价止盈" not in combined_zh
    assert "警惕亏损扛单" not in combined_zh

    # Also verify direct generate_ai_review with an AI base URL configured still short-circuits on 0 trades
    prefs = UserPreferences(ai_base_url="http://127.0.0.1:9999/v1", ai_model="test-model")
    dash_7d = client.get("/api/v1/dashboard?range=7d").json()
    rev_direct = await generate_ai_review(dash_7d, prefs, force_local=False)
    assert "本周期无成交" in (rev_direct.get("markdown_commentary_zh") or "")
