from decimal import Decimal

import pytest

from opentradeledger.core.position_lifecycle import build_position_events, trade_daily_amounts

DAY1 = 1794916800000
DAY2 = DAY1 + 86_400_000

def fill(t, entry, price, qty, fee):
    return dict(time_ms=t, is_entry=entry, price=price, qty=qty, fee_usd=fee)


def test_scale_out_add_then_reduce_preserves_cost_and_actual_fees():
    events = build_position_events([
        fill(DAY1, True, 100, 2, "0.10"), fill(DAY1 + 1, False, 110, 1, ".055"),
        fill(DAY1 + 2, True, 98, 1, ".049"), fill(DAY2, False, 109, 1, ".0545"),
    ], side="LONG")
    assert [e["action"] for e in events] == ["OPEN", "REDUCE", "ADD", "REDUCE"]
    assert [e["remaining_qty"] for e in events] == [2, 1, 2, 1]
    assert [e["avg_entry_price"] for e in events] == [100, 100, 99, 99]
    assert events[1]["net_pnl_usd"] == pytest.approx(9.895)
    assert events[3]["net_pnl_usd"] == pytest.approx(9.896)
    assert events[3]["allocated_entry_fee_usd"] == pytest.approx(.0495)


def test_calendar_posts_cash_once_on_actual_date():
    events = build_position_events([
        fill(DAY1, True, 100, 2, ".2"), fill(DAY1+3, False, 110, 1, ".1"),
        fill(DAY2, False, 130, 1, ".1"),
    ], side="LONG")
    tr = dict(position_events=events, close_time_ms=DAY2, close_date="2026-11-18")
    assert sorted(trade_daily_amounts(tr, Decimal("39.6")).values()) == [Decimal("9.7"), Decimal("29.9")]


def test_incomplete_history_cannot_create_false_cost_or_reopen():
    assert not build_position_events([fill(DAY1, False, 100, 1, 0)], side="SHORT")
    assert not build_position_events([fill(DAY1, True, 100, 1, 0), fill(DAY2, False, 99, 2, 0)], side="SHORT")
    assert not build_position_events([fill(DAY1, True, 100, 1, 0), fill(DAY1, False, 99, 1, 0),
                                      fill(DAY2, True, 98, 1, 0)], side="SHORT")
    ev = build_position_events([fill(DAY1, True, 100, 1, None), fill(DAY2, False, 99, 1, 0)], side="SHORT")
    assert ev[-1]["gross_pnl_usd"] == 1
    assert ev[-1]["net_pnl_usd"] is None


def test_open_reductions_backtrack_from_live_balance_and_survive_reopen():
    from opentradeledger.core.open_reductions import open_reduction_record
    fills = [
        fill(DAY1-4, True, 85, 1, .02), fill(DAY1-3, False, 87, 1, .03),
        fill(DAY1, True, 100, 2, .1), fill(DAY1+1, False, 110, 1, .055),
        fill(DAY1+2, True, 98, 1, .049), fill(DAY2, False, 109, 1, .0545),
    ]
    args = dict(venue="gate", symbol="ETH_USDT", side="LONG", fills=fills,
                remaining_qty=1, avg_entry_price=99, journals={}, history=[])
    row = open_reduction_record(**args)
    assert [e["action"] for e in row["position_events"]] == ["OPEN", "REDUCE", "ADD", "REDUCE"]
    assert row["gross_pnl_usd"] == 20
    assert row["total_fee_usd"] == pytest.approx(.2585)
    assert row["realized_net_pnl_usd"] == pytest.approx(19.7415)
    assert open_reduction_record(**{**args, "avg_entry_price": 90}) is None
    assert open_reduction_record(**{**args, "history": [row]}) is None


def test_open_cash_calendar_includes_actual_fee_of_remaining_units():
    events = build_position_events([
        fill(DAY1, True, 100, 2, ".20"), fill(DAY2, False, 110, 1, ".10"),
    ], side="LONG")
    # A venue's partial close statement allocates only half the opening fee.
    trade = {"close_date": "2026-11-18", "position_events": events,
             "entry_leg": {"gross_fee_usd": .1}, "funding_by_day": {"2026-11-17": .3},
             "rebates_by_day": {"2026-11-19": .05}}
    assert trade_daily_amounts(trade, Decimal("10.15")) == {
        "2026-11-17": Decimal(".10"), "2026-11-18": Decimal("9.90"), "2026-11-19": Decimal(".05")}
    assert trade_daily_amounts(trade, Decimal("10.1"), include_rebates=False) == {
        "2026-11-17": Decimal(".1"), "2026-11-18": Decimal("9.9")}


def test_demo_exposes_same_execution_and_calendar_views_as_real_accounts():
    from opentradeledger.config import UserPreferences
    from opentradeledger.core.demo_provider import build_demo_dashboard
    demo = build_demo_dashboard(prefs=UserPreferences())
    trade = next(t for t in demo["trades"] if t["exit_batches"])
    assert [e["action"] for e in trade["position_events"]] == ["OPEN", "REDUCE", "CLOSE"]
    day = next(day for day in demo["calendar_days"] if day["date"] == trade["close_date"])
    assert any(r["trade_id"] == trade["trade_id"] and r["exit_count"] == 2 for r in day["trade_activity"])


def test_native_order_batches_include_proportional_entry_cost_once():
    from opentradeledger.core.position_lifecycle import align_exit_batch_net
    fills = [fill(DAY1, True, 100, 2, ".10"), fill(DAY1+1, False, 110, 1, ".055"),
             fill(DAY1+2, True, 98, 1, ".049"), fill(DAY2, False, 109, 1, ".0545")]
    for idx, item in enumerate(fills):
        item["order_id"] = str(idx+1)
    trade = {"position_events": build_position_events(fills, side="LONG"),
             "exit_batches": [{"order_id":"2","qty":1,"gross_pnl_usd":10},
                              {"order_id":"4","qty":1,"gross_pnl_usd":10}]}
    align_exit_batch_net(trade)
    assert [b["net_pnl_usd"] for b in trade["exit_batches"]] == pytest.approx([9.895,9.896])
    assert {b["net_basis"] for b in trade["exit_batches"]} == {"round_trip"}


def test_symbol_curve_and_calendar_keep_first_exit_when_final_close_is_next_month(tmp_path):
    from opentradeledger.adapters.registry import build_default_registry
    from opentradeledger.core.ledger_service import LedgerService
    from opentradeledger.core.store import LedgerStore
    from opentradeledger.security.vault import CredentialVault

    store = LedgerStore(tmp_path)
    scope = store.venue_scope("binance")
    scope.set_kv("live_bundle", {"pm_account": {"accountEquity": 100, "totalAvailableBalance": 80}})
    fills = [
        dict(id=1, orderId=10, time=DAY1, side="BUY", qty=2, price=100, commission=.2, realizedPnl=0),
        dict(id=2, orderId=20, time=DAY1+10, side="SELL", qty=1, price=110, commission=.1, realizedPnl=10),
        dict(id=3, orderId=30, time=DAY1+18*86_400_000, side="SELL", qty=1, price=130, commission=.1, realizedPnl=30),
    ]
    for f in fills:
        f.update(symbol="ETHUSDT", positionSide="LONG", commissionAsset="USDT")
    scope.upsert_records("fills", fills, key_fn=lambda f: str(f["id"]), ts_fn=lambda f: f["time"])
    svc = LedgerService(store, CredentialVault(tmp_path), build_default_registry(), default_demo_mode=False)
    dash = svc.get_dashboard(venue_filter="binance", symbol_filter="ETH_USDT", range_key="2026-11")
    assert [(d["date"], d["delta_usd"]) for d in dash["equity_curve"]] == [("2026-11-17", 9.7)]
    first = next(d for d in dash["calendar_days"] if d["date"] == "2026-11-17")
    assert first["trade_activity"][0]["amount_usd"] == 9.7
    assert first["trade_activity"][0]["exit_count"] == 1
