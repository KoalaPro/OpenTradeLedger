"""Regression tests for v1.3.6: accurate `goal.today_delta_usd` and pre-snapshot historical PnL attribution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from opentradeledger.adapters.gate.sync import sync_gate_venue
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.config import UserPreferences
from opentradeledger.core.demo_provider import build_demo_dashboard
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault

BJ = timezone(timedelta(hours=8))


def _bj_ts(y: int, m: int, d: int, hh: int = 12, mm: int = 0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=BJ).timestamp())


def test_goal_today_delta_is_zero_when_no_activity_today_not_last_historical_trade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the last closed trade was days ago and today has 0 activity, `today_delta_usd` must be 0.0."""
    now_ts = _bj_ts(2026, 11, 25, 11, 0)
    monkeypatch.setattr("opentradeledger.core.ledger_service.time.time", lambda: float(now_ts))

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    t_open_ms = _bj_ts(2026, 11, 20, 10, 0) * 1000
    t_close_ms = _bj_ts(2026, 11, 20, 14, 0) * 1000
    store.upsert_plugin_snapshot(
        "okx",
        {
            "balances": [{"totalEq": "2911.50", "details": [{"ccy": "USDT", "availBal": "2911.50"}]}],
            "deposits": [{"depId": "okx_d1", "ccy": "USDT", "amt": "3000.00", "state": "2"}],
            "withdrawals": [],
            "positions_history": [
                {
                    "posId": "okx_p1",
                    "instId": "ADA-USDT-SWAP",
                    "direction": "long",
                    "openAvgPx": "1.00",
                    "closeAvgPx": "0.915",
                    "closeTotalPos": "1000",
                    "cTime": str(t_open_ms),
                    "uTime": str(t_close_ms),
                    "pnl": "-85.00",
                    "fee": "-3.50",
                    "realizedPnl": "-88.50",
                }
            ],
            "fills": [],
            "observed_rebate_ratio": 0.0,
        },
    )

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    svc.update_preferences({"goal_enabled": True, "goal_label": "回本目标", "goal_target_usd": 9000.0})

    dash = svc.get_dashboard(range_key="all")
    assert len(dash["equity_curve"]) == 1
    assert dash["equity_curve"][0]["date"] == "2026-11-20"
    assert dash["equity_curve"][0]["delta_usd"] == pytest.approx(-88.5, abs=0.02)
    assert dash["goal"]["today_delta_usd"] == 0.0

    prefs = UserPreferences(goal_enabled=True, goal_label="Demo Goal", goal_target_usd=30000.0)
    demo_dash = build_demo_dashboard(
        range_key="all",
        venue_filter="ALL",
        symbol_filter="ALL",
        prefs=prefs,
        journals={},
        latest_ai_review=None,
        now_dt=datetime(2026, 12, 15, 12, 0, tzinfo=BJ),
    )
    assert demo_dash["goal"]["today_delta_usd"] == 0.0


def test_fresh_nas_install_does_not_dump_historical_copy_stock_or_token_plugs_into_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the live NAS 8013 bug where Gate (-57.50) + EasiCoin (+1.12) = -56.38 showed on 2026-11-25 with 0 trades."""
    now_ts = _bj_ts(2026, 11, 25, 11, 0)
    monkeypatch.setattr("opentradeledger.core.ledger_service.time.time", lambda: float(now_ts))

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    # 1. Seed Gate on a fresh NAS (0 prior eod_snapshots)
    gate_scope = store.venue_scope("gate")
    gate_scope.upsert_records(
        "spot_account_book",
        [
            {"id": "1", "time": _bj_ts(2026, 11, 1, 10, 0) * 1000, "type": "deposit", "code": "", "currency": "USDT", "change": "2000"},
            {"id": "2", "time": _bj_ts(2026, 11, 16, 13, 41) * 1000, "type": "unknown", "code": "150258", "currency": "USDT", "change": "-600"},
            {"id": "3", "time": _bj_ts(2026, 11, 17, 18, 38) * 1000, "type": "fct_out", "code": "3202", "currency": "USDT", "change": "-1000"},
            {"id": "4", "time": _bj_ts(2026, 11, 20, 1, 0) * 1000, "type": "unknown", "code": "150256", "currency": "USDT", "change": "546.8385"},
            {"id": "5", "time": _bj_ts(2026, 11, 23, 15, 0) * 1000, "type": "spot_settle_in", "code": "", "currency": "USDT", "change": "75.00"},
            {"id": "6", "time": _bj_ts(2026, 11, 25, 8, 13) * 1000, "type": "unknown", "code": "150349", "currency": "USDT", "change": "0.3021259"},
        ],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["time"]),
    )
    gate_scope.upsert_records(
        "futures_closes",
        [
            {
                "contract": "QCOM_USDT",
                "side": "long",
                "first_open_time": _bj_ts(2026, 11, 23, 14, 0),
                "time": _bj_ts(2026, 11, 23, 15, 0),
                "pnl": "75.00",
                "pnl_pnl": "76.00",
                "pnl_fee": "-1.00",
                "pnl_fund": "0",
                "accum_size": "10",
                "long_price": "150",
                "short_price": "157.6",
            }
        ],
        key_fn=lambda r: f"{r['contract']}_{r['time']}",
        ts_fn=lambda r: float(r["time"]),
    )
    gate_scope.set_kv(
        "live_bundle",
        {
            "wallet_total": {
                "total": {"amount": "2017.3121259"},
                "details": {
                    "spot": {"amount": "1024.8321259"},
                    "quant": {"amount": "942.53"},
                    "tradfi_spot": {"amount": "49.95"},
                },
            },
            "unified": {
                "mode": "multi_currency",
                "unified_account_total_equity": "1024.8321259",
                "balances": {
                    "USDT": {"available": "1021.9621259", "equity": "1021.9621259", "freeze": "0"},
                    "GT": {"available": "0.3", "equity": "2.87", "freeze": "0"},
                },
            },
            "futures_account": {"enable_credit": True, "available": "1021.9621259", "total": "0"},
            "stock_assets": {"equity": "49.95", "position_pnl": "-3.2115"},
            "futures_positions": [],
        },
    )

    # 2. Seed EasiCoin with historical closed trade on 11-19 and +1.1233 U bonus token / tail adjustment
    ec_scope = store.venue_scope("easicoin")
    ec_scope.set_kv(
        "live_bundle",
        {
            "uid": "ec_1",
            "futures_balances": [
                {"coin": "USDT", "equity": "1002.4545", "available_balance": "1002.4545", "unrealised_pnl": "0"}
            ],
            "funding_balances": [],
            "spot_balances": [],
            "tickers": [],
        },
    )
    ec_scope.upsert_records(
        "transfers",
        [
            {
                "time": _bj_ts(2026, 11, 15, 10, 0),
                "coin": "USDT",
                "amount": "1000.00",
                "from_wallet": "FUNDING",
                "to_wallet": "TRADING",
                "transfer_status": "1",
            }
        ],
        key_fn=lambda r: f"{r['time']}_{r['amount']}",
        ts_fn=lambda r: float(r["time"]),
    )
    ec_scope.upsert_records(
        "closed_pnl",
        [
            {
                "order_id": "ec_ord_1",
                "symbol": "ETHUSDT",
                "side": "Sell",
                "closed_size": "1.0",
                "avg_entry_price": "3000",
                "avg_exit_price": "3002",
                "cum_entry_value": "3000",
                "cum_exit_value": "3002",
                "closed_pnl": "1.3312",
                "created_at": _bj_ts(2026, 11, 19, 16, 30),
            }
        ],
        key_fn=lambda r: str(r["order_id"]),
        ts_fn=lambda r: float(r["created_at"]),
    )

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    svc.update_preferences({"goal_enabled": True, "goal_label": "回本目标", "goal_target_usd": 9000.0})

    dash = svc.get_dashboard(range_key="all")
    assert dash["reconciliation"]["residual_usd"] == 0.0
    assert dash["equity_curve"][-1]["cum_net_usd"] == pytest.approx(dash["reconciliation"]["net_pnl_usd"], abs=0.03)

    # Today (2026-11-25) had 0 trades and ONLY +0.30 U Gate earn interest — NEVER -56.38!
    assert dash["goal"]["today_delta_usd"] == pytest.approx(0.30, abs=0.01)

    cal_by_date = {c["date"]: c for c in dash["calendar_days"]}
    assert cal_by_date["2026-11-25"]["net_pnl_usd"] == pytest.approx(0.30, abs=0.01)
    assert cal_by_date["2026-11-25"]["trade_count"] == 0
    # Copy pool loss (-57.47) belongs on 2026-11-17 (when copy pool was funded), stock (-3.21) on 2026-11-20!
    assert cal_by_date["2026-11-17"]["net_pnl_usd"] == pytest.approx(-57.47, abs=0.02)
    assert cal_by_date["2026-11-20"]["net_pnl_usd"] == pytest.approx(-3.21, abs=0.02)


@pytest.mark.asyncio
async def test_gate_sync_persists_daily_eod_snapshot_and_telescopes_subsequent_day_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate sync persists daily EOD snapshots so subsequent days accurately capture copy/stock day-over-day changes."""

    class _FakeGateClient:
        def __init__(self, total_eq: str, quant_eq: str, stock_eq: str) -> None:
            self.total_eq = total_eq
            self.quant_eq = quant_eq
            self.stock_eq = stock_eq

        async def fetch_live_overview_bundle(self) -> dict[str, Any]:
            return {
                "wallet_total": {
                    "total": {"amount": self.total_eq},
                    "details": {
                        "spot": {"amount": "1000.00"},
                        "quant": {"amount": self.quant_eq},
                        "tradfi_spot": {"amount": self.stock_eq},
                    },
                },
                "unified": {
                    "mode": "multi_currency",
                    "unified_account_total_equity": "1000.00",
                    "balances": {"USDT": {"available": "1000.00", "equity": "1000.00"}},
                },
                "futures_account": {"enable_credit": True, "available": "1000.00", "total": "0"},
                "stock_assets": {"equity": self.stock_eq, "position_pnl": "-3.21"},
                "futures_positions": [],
            }

        async def fetch_contract_specs(self) -> dict[str, Any]:
            return {}

        async def fetch_futures_closes(self, *, since_sec: int) -> list[dict[str, Any]]:
            return []

        async def fetch_futures_trades(self, *, since_sec: int) -> list[dict[str, Any]]:
            return []

        async def fetch_futures_finished_orders(self, *, since_sec: int) -> list[dict[str, Any]]:
            return []

        async def fetch_partner_rebates(self, *, since_sec: int) -> list[dict[str, Any]]:
            return []

        async def fetch_spot_account_book(self, *, since_sec: int, max_pages: int = 4) -> list[dict[str, Any]]:
            return [
                {"id": "1", "time": _bj_ts(2026, 11, 1, 10, 0) * 1000, "type": "deposit", "code": "", "currency": "USDT", "change": "2053.16"},
                {"id": "2", "time": _bj_ts(2026, 11, 16, 13, 0) * 1000, "type": "unknown", "code": "150258", "currency": "USDT", "change": "-53.16"},
                {"id": "3", "time": _bj_ts(2026, 11, 17, 18, 0) * 1000, "type": "fct_out", "code": "3202", "currency": "USDT", "change": "-1000.00"},
            ]

        async def fetch_stock_orders_and_deals(self, *, max_pages: int = 2) -> tuple[list[Any], list[Any]]:
            return [], []

        async def fetch_stock_transactions(self, *, max_pages: int = 2) -> list[Any]:
            return []

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    day1_ts = _bj_ts(2026, 11, 25, 20, 0)
    await sync_gate_venue(_FakeGateClient("1992.48", "942.53", "49.95"), store, now_s=day1_ts, force_full=True)
    snaps_d1 = store.venue_scope("gate").load_records("eod_snapshots", desc=False)
    assert len(snaps_d1) == 1

    # Day 2 (2026-11-26): Copy pool gains +10.00 (942.53 -> 952.53) and Stock gains +2.00 (49.95 -> 51.95)
    day2_ts = _bj_ts(2026, 11, 26, 20, 0)
    await sync_gate_venue(_FakeGateClient("2004.48", "952.53", "51.95"), store, now_s=day2_ts, force_full=False)
    snaps_d2 = store.venue_scope("gate").load_records("eod_snapshots", desc=False)
    assert len(snaps_d2) == 2

    monkeypatch.setattr("opentradeledger.core.ledger_service.time.time", lambda: float(day2_ts))
    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    svc.update_preferences({"goal_enabled": True, "goal_label": "回本目标", "goal_target_usd": 9000.0})
    dash_d2 = svc.get_dashboard(range_key="all")

    assert dash_d2["reconciliation"]["residual_usd"] == 0.0
    assert dash_d2["goal"]["today_delta_usd"] == pytest.approx(12.00, abs=0.02)
    cal = {c["date"]: c["net_pnl_usd"] for c in dash_d2["calendar_days"]}
    assert cal["2026-11-26"] == pytest.approx(12.00, abs=0.02)
    assert cal["2026-11-17"] == pytest.approx(-57.47, abs=0.02)
