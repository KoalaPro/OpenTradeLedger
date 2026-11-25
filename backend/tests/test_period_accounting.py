from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from opentradeledger.adapters.okx.client import normalize_okx_closed_trades
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ai_reviewer import build_ai_context_payload
from opentradeledger.core.demo_provider import build_demo_dashboard
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


def stamp(day, hour=12):
    return int(datetime.fromisoformat(f"2026-{day}T{hour}:00").replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)


def close(day, gross):
    return {"instId": "BTC-USDT-SWAP", "posId": day, "cTime": str(stamp(day)), "uTime": str(stamp(day, 13)),
            "openAvgPx": "10000", "closeAvgPx": str(10000 + gross), "closeTotalPos": "1",
            "pnl": str(gross), "realizedPnl": str(gross - 10), "fee": "-10"}


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("opentradeledger.core.ledger_service.time.time", lambda: stamp("11-25") / 1000)
    store = LedgerStore(tmp_path)
    positions = [close("10-15", 200), close("11-08", -30), close("11-13", 100)]
    # The first rebate settles the next day. The wallet transfer is not income.
    rebates = [{"billId": str(i), "type": "15", "subType": "rebate", "instId": "BTC-USDT-SWAP",
                "ccy": "USDT", "balChg": "2", "ts": str(stamp(day))}
               for i, day in enumerate(("10-16", "11-08", "11-13"), start=1)]
    store.upsert_plugin_snapshot("okx", {
        "balances": [{"totalEq": "10296"}], "deposits": [{"ccy": "USDT", "amt": "10000"}],
        "positions_history": positions, "bills": rebates,
        "asset_bills": [{"billId": "earn", "type": "400", "ccy": "USDT", "balChg": "50", "ts": str(stamp("11-23"))},
                        {"billId": "transfer", "type": "130", "ccy": "USDT", "balChg": "500", "ts": str(stamp("11-23"))}],
        "trade_fee": [{"makerU": "-0.0002", "takerU": "-0.0005"}], "observed_rebate_ratio": 0.20,
    })
    return LedgerService(store, CredentialVault(tmp_path), build_default_registry())


def test_dated_interest_and_rebates_bridge_the_daily_curve(ledger):
    dash = ledger.get_dashboard()
    daily = {r["date"]: r["net_pnl_usd"] for r in dash["calendar_days"]}
    assert daily == {"2026-10-15": 190, "2026-10-16": 2, "2026-11-08": -38, "2026-11-13": 92, "2026-11-23": 50}
    assert dash["equity_curve"][-1]["cum_net_usd"] == dash["summary"]["whole_net_pnl_usd"] == 296
    for period, expected in (("7d", 50), ("30d", 104), ("2026-11", 104)):
        d = ledger.get_dashboard(range_key=period)
        assert d["summary"]["range_whole_net_pnl_usd"] == expected
        comp = {r["key"]: r["amount_usd"] for r in d["reconciliation"]["range_items"]}
        assert comp["okx_earn_spot"] == 50
        assert sum(comp.values()) == expected


def test_ai_summary_exposes_period_and_portfolio_lifetime_separately(ledger):
    ctx = build_ai_context_payload(ledger.get_dashboard(range_key="30d"))
    assert ctx["range_key"] == "30d"
    assert ctx["summary"]["range_whole_net_pnl_usd"] == 104
    assert ctx["summary"]["range_whole_roi_pct"] == 1.04
    assert ctx["summary"]["whole_net_pnl_usd"] == 296


def test_okx_total_only_is_preserved_without_inventing_per_leg_fees(ledger):
    dash = ledger.get_dashboard()
    trade = dash["trades"][0]
    assert (trade["total_fee_usd"], trade["total_rebate_usd"], trade["net_fee_usd"], trade["net_pnl_usd"]) == (10, 2, 8, 92)
    for leg in (trade["entry_leg"], trade["exit_leg"]):
        assert leg["fee_known"] is False and leg["role"] == "UNKNOWN"
    assert build_ai_context_payload(dash)["recent_20_trades"][0]["entry_gross_fee"] is None
    # When only an entry fill was retained, preserve the full position fee.
    raw = close("11-13", 100)
    [part] = normalize_okx_closed_trades([raw], [{"instId": raw["instId"], "ts": raw["cTime"],
                                                   "side": "buy", "execType": "M", "fee": "-2"}])
    assert part["total_fee_usd"] == 10 and part["entry_leg"]["gross_fee_usd"] == 2
    assert part["exit_leg"]["fee_known"] is False


def test_zero_samples_distinguish_configured_rate_from_observed(ledger):
    dash = ledger.get_dashboard(range_key="7d")
    [row] = dash["fee_matrix"]
    assert row["trade_count"] == 0
    assert row["maker_rate_source"] == row["taker_rate_source"] == "configured"
    assert row["maker_gross_bps"] == 2 and row["maker_net_bps"] is None
    assert row["combined_relief_pct"] == 0
    assert build_ai_context_payload(dash)["fee_matrix"][0]["maker_rate_source"] == "configured"


def test_demo_period_drawdown_and_costs_follow_sample():
    dash = build_demo_dashboard(range_key="7d")
    assert dash["summary"]["max_drawdown_usd"] == pytest.approx(175.79)
    assert sum(r["trade_count"] for r in dash["fee_matrix"]) == dash["summary"]["trade_count"] == 7
    assert dash["summary"]["range_whole_net_pnl_usd"] == dash["equity_curve"][-1]["cum_net_usd"]
    assert sum(r["amount_usd"] for r in dash["reconciliation"]["range_items"]) == dash["summary"]["range_whole_net_pnl_usd"]
    assert any(r["date"].startswith("2026-10") for r in dash["calendar_days"])


def test_trading_account_funding_and_borrow_bills_are_not_fee_rebates(tmp_path):
    store = LedgerStore(tmp_path)
    position = close("11-13", 100)
    position.update({"fundingFee": "3", "realizedPnl": "93"})
    bills = [{"billId": "funding", "type": "8", "subType": "174", "balChg": "3",
              "instId": position["instId"], "ts": position["uTime"]},
             {"billId": "loan", "type": "7", "subType": "212", "balChg": "500",
              "instId": position["instId"], "ts": position["uTime"]}]
    store.upsert_plugin_snapshot("okx", {"balances": [{"totalEq": "1093"}],
                              "deposits": [{"ccy": "USDT", "amt": "1000"}],
                              "positions_history": [position], "bills": bills})
    d = LedgerService(store, CredentialVault(tmp_path), build_default_registry()).get_dashboard()
    assert d["trades"][0]["total_rebate_usd"] == 0
    assert d["equity_curve"][-1]["cum_net_usd"] == 93


def test_period_components_survive_when_interest_offsets_a_trade_on_same_day(ledger):
    snap = ledger.store.venue_scope("okx").get_kv("snapshot")
    snap["asset_bills"].append({"billId": "offset", "type": "400", "ccy": "USDT",
                               "balChg": "38", "ts": str(stamp("11-08"))})
    snap["balances"][0]["totalEq"] = "10334"
    ledger.store.upsert_plugin_snapshot("okx", snap)
    dash = ledger.get_dashboard(range_key="30d")
    contrib = {r["key"]: r["amount_usd"] for r in dash["reconciliation"]["range_items"]}
    assert contrib == {"okx_futures": 54, "okx_earn_spot": 88}


def test_unlinked_funding_wallet_fee_rebate_has_its_own_dated_contribution(tmp_path):
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("okx", {
        "balances": [{"totalEq": "10002"}],
        "deposits": [{"ccy": "USDT", "amt": "10000"}],
        "asset_bills": [{"billId": "fee-refund", "type": "173", "ccy": "USDT",
                         "balChg": "2", "ts": str(stamp("11-23"))}],
    })
    service = LedgerService(store, CredentialVault(tmp_path), build_default_registry())
    for period in ("all", "2026-11"):
        dash = service.get_dashboard(range_key=period)
        assert dash["summary"]["futures_net_pnl_usd"] == 0
        assert dash["summary"]["range_whole_net_pnl_usd"] == 2
        assert [(row["key"], row["amount_usd"]) for row in dash["reconciliation"]["range_items"]] == [("okx_fee_rebates", 2)]


def test_missing_maker_samples_do_not_promise_zero_cost_maker_fills(tmp_path):
    store = LedgerStore(tmp_path)
    trade = close("11-13", 100)
    store.upsert_plugin_snapshot("okx", {
        "balances": [{"totalEq": "1090"}],
        "positions_history": [trade],
        "fills": [
            {"instId": trade["instId"], "side": "buy", "execType": "T", "fee": "-2", "ts": trade["cTime"]},
            {"instId": trade["instId"], "side": "sell", "execType": "T", "fee": "-8", "ts": trade["uTime"]},
        ],
    })
    service = LedgerService(store, CredentialVault(tmp_path), build_default_registry())
    [row] = service.get_dashboard()["fee_matrix"]
    assert row["maker_rate_source"] == "unknown" and row["taker_rate_source"] == "observed"
    assert row["potential_maker_savings_usd"] == 0


@pytest.mark.asyncio
async def test_okx_funding_bills_fetch_previous_page_by_timestamp():
    import httpx
    from opentradeledger.adapters.okx.client import OkxReadOnlyClient

    latest = {"billId": "225", "ts": str(stamp("11-24")), "type": "130"}
    interest = {"billId": "207", "ts": str(stamp("11-23")), "type": "400", "balChg": "50"}
    pages = {None: [latest], latest["ts"]: [interest]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": "0", "data": pages.get(request.url.params.get("after"), [])})
    )) as http:
        adapter = OkxReadOnlyClient(api_key="local", api_secret="local", passphrase="local", client=http, page_limit=1)
        assert await adapter.get_asset_bills() == [latest, interest]
