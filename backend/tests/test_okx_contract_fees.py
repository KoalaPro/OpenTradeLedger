"""OKX contract denominations and signed fill cash flows through the live ledger."""
import httpx
import pytest

from opentradeledger.adapters.okx.client import OkxReadOnlyClient
from opentradeledger.adapters.okx.sync import sync_okx_venue
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault

INST = "BTC-USDT-SWAP"
SPEC = {"instId": INST, "instType": "SWAP", "ctType": "linear", "ctVal": "0.01", "ctValCcy": "BTC"}
START = 1795478400000
END = START + 3600000


def snapshot(entry_fee="-20", exit_fee="-50.50", specs=True):
    total = float(entry_fee) + float(exit_fee)
    return {
        "instruments": [SPEC] if specs else [],
        "positions": [{"instId": INST, "pos": "100", "markPx": "100000", "notionalUsd": "100000"}],
        "positions_history": [{
            "instId": INST, "posId": "p1", "cTime": START, "uTime": END, "direction": "long",
            "openAvgPx": "100000", "closeAvgPx": "101000", "closeTotalPos": "100",
            "pnl": "1000", "fee": str(total), "realizedPnl": str(1000 + total),
        }],
        "fills": [
            {"instId": INST, "side": "buy", "execType": "M", "fee": entry_fee, "feeCcy": "USDT",
             "fillSz": "100", "fillPx": "100000", "ts": START, "billId": "f1", "tradeId": "1"},
            {"instId": INST, "side": "sell", "execType": "T", "fee": exit_fee, "feeCcy": "USDT",
             "fillSz": "100", "fillPx": "101000", "ts": END, "billId": "f2", "tradeId": "2"},
        ],
    }


def dashboard(tmp_path, snap):
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("okx", snap)
    return LedgerService(store, CredentialVault(tmp_path), build_default_registry()).get_dashboard(force_demo=False)


def test_fill_quantities_and_weighted_rates_use_each_execution(tmp_path):
    snap = snapshot()
    # Only 90 of 100 entry contracts remain in fill history; pair the 18 fee with those 90.
    snap["fills"][0].update(fillSz="80", fillPx="100000", fee="-16")
    snap["fills"].append({**snap["fills"][0], "billId": "f3", "tradeId": "3", "fillSz": "10", "fillPx": "100000", "fee": "-2"})
    d = dashboard(tmp_path, snap)
    [trade] = d["trades"]
    assert (trade["qty"], trade["contract_count"]) == (1, 100)
    assert (d["open_positions"][0]["qty"], d["open_positions"][0]["contract_count"]) == (1, 100)
    assert (trade["entry_leg"]["notional_usd"], trade["entry_leg"]["fee_bps"]) == (90000, 2)
    assert (trade["exit_leg"]["notional_usd"], trade["exit_leg"]["fee_bps"]) == (101000, 5)
    assert trade["net_pnl_usd"] == 929.5
    [matrix] = d["fee_matrix"]
    assert (matrix["maker_net_bps"], matrix["taker_net_bps"]) == (2, 5)


@pytest.mark.parametrize("realized", [True, False])
def test_maker_credit_is_settled_once_alongside_realized_pnl_and_bills(tmp_path, realized):
    snap = snapshot("+20", "-50.5")
    if not realized:
        del snap["positions_history"][0]["realizedPnl"]
    snap["bills"] = [
        {"instId": INST, "billId": "f1", "type": "2", "fee": "20", "balChg": "20", "ts": START},
        {"instId": INST, "billId": "affiliate", "type": "15", "subType": "rebate", "balChg": "3", "ts": END + 1000},
    ]
    d = dashboard(tmp_path, snap)
    [tr] = d["trades"]
    assert (tr["total_fee_usd"], tr["total_rebate_usd"], tr["net_fee_usd"], tr["net_pnl_usd"]) == (50.5, 23, 27.5, 972.5)
    assert tr["entry_leg"]["fee_bps"] == -2
    assert tr["entry_leg"]["net_fee_usd"] == -20
    assert tr["entry_leg"]["rebate_status"] == "SETTLED"
    assert d["fee_matrix"][0]["maker_net_bps"] == -2
    assert d["summary"]["whole_net_pnl_usd"] == d["equity_curve"][-1]["cum_net_usd"] == 972.5


@pytest.mark.parametrize("retain_fills", [True, False])
def test_rebate_only_position_can_have_negative_total_cost(tmp_path, retain_fills):
    snap = snapshot("20", "0")
    if not retain_fills:
        snap["fills"] = []
    del snap["positions_history"][0]["realizedPnl"]
    d = dashboard(tmp_path, snap)
    [tr] = d["trades"]
    assert (tr["total_fee_usd"], tr["total_rebate_usd"], tr["net_fee_usd"], tr["net_pnl_usd"]) == (0, 20, -20, 1020)
    assert d["summary"]["net_fee_usd"] == -20
    assert d["equity_curve"][-1]["cum_net_usd"] == 1020


def test_unknown_contract_size_uses_only_authoritative_fill_notional(tmp_path):
    snap = snapshot(specs=False)
    snap["fills"][0]["fillNotionalUsd"] = "100000"
    # Order-level notional may exceed this fill, so cannot supply the denominator.
    snap["fills"][1]["notionalUsd"] = "202000"
    d = dashboard(tmp_path, snap)
    [tr] = d["trades"]
    assert tr["contract_count"] == 100 and tr["qty"] is None
    assert (tr["entry_leg"]["fee_bps"], tr["exit_leg"]["fee_bps"]) == (2, None)
    assert tr["exit_leg"]["fee_known"] and tr["exit_leg"]["gross_fee_usd"] == 50.5
    [matrix] = d["fee_matrix"]
    assert matrix["maker_rate_source"] == "observed" and matrix["taker_rate_source"] == "unknown"
    assert matrix["blended_net_bps"] is None


@pytest.mark.asyncio
async def test_incremental_sync_backfills_historical_contract_and_retains_cached_spec(tmp_path):
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("okx", snapshot(specs=False))
    metadata = [SPEC]

    def handler(request):
        rows = metadata if request.url.path == "/api/v5/public/instruments" else []
        if rows:
            assert request.url.params["instType"] == "SWAP"
            assert "OK-ACCESS-KEY" not in request.headers
        return httpx.Response(200, json={"code": "0", "data": rows})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OkxReadOnlyClient(api_key="a", api_secret="s", passphrase="p", client=http)
        for _ in range(2):
            await sync_okx_venue(client, store, now_s=END // 1000)
            d = LedgerService(store, CredentialVault(tmp_path), build_default_registry()).get_dashboard(force_demo=False)
            assert d["trades"][0]["qty"] == 1
            assert d["trades"][0]["entry_leg"]["fee_bps"] == 2
            metadata.clear()


@pytest.mark.parametrize(("entry_fee", "charged", "rebated", "net"), [
    ("-20", 70.50, 0, 929.50), ("+20", 50.50, 20, 969.50),
])
def test_original_hundred_contract_examples(tmp_path, entry_fee, charged, rebated, net):
    snap = snapshot(entry_fee)
    d = dashboard(tmp_path, snap)
    [tr] = d["trades"]
    assert (tr["contract_count"], tr["qty"]) == (100, 1)
    assert (tr["total_fee_usd"], tr["total_rebate_usd"], tr["net_pnl_usd"]) == (charged, rebated, net)
    assert tr["entry_leg"]["fee_bps"] == (2 if entry_fee.startswith("-") else -2)
    assert tr["exit_leg"]["fee_bps"] == 5


def test_a_bill_mirroring_a_positive_fill_is_not_a_second_credit(tmp_path):
    snap = snapshot("+20", "-50.5")
    snap["bills"] = [{"billId": "f1", "instId": INST, "subType": "rebate", "fee": "20", "balChg": "20", "ts": START}]
    d = dashboard(tmp_path, snap)
    assert d["summary"]["total_rebate_usd"] == 20
    assert d["summary"]["whole_net_pnl_usd"] == d["summary"]["futures_net_pnl_usd"] == 969.5


def test_rebate_only_futures_in_equity_perpetuals_and_ai_highlights(tmp_path):
    from opentradeledger.core.ai_reviewer import build_local_highlights

    snap = snapshot("+20", "0", specs=False)
    for key in ("positions", "positions_history", "fills"):
        for row in snap[key]:
            row["instId"] = "TSLA-USDT-SWAP"
    d = dashboard(tmp_path, snap)
    [stock] = d["extensions"]["stocks"]["perpetuals"]
    assert stock["net_fee_usd"] == -20
    assert stock["contract_count"] == 100 and stock["qty"] is None
    assert stock["maker_bps"] is None
    highlights = build_local_highlights(d)
    assert next(h for h in highlights if h["kind"] == "net_fee")["metric"].startswith("+20.00")


def test_inverse_usd_contract_fees_use_execution_price_and_coin_settlement(tmp_path):
    snap = snapshot("+0.00002", "-0.00005")
    for key in ("positions", "positions_history", "fills"):
        for row in snap[key]:
            row["instId"] = "BTC-USD-SWAP"
    snap["instruments"] = [dict(SPEC, instId="BTC-USD-SWAP", ctType="inverse", ctVal="100", ctValCcy="USD", settleCcy="BTC")]
    snap["positions_history"][0].update(pnl="0.001", fee="-0.00003", realizedPnl="0.00097")
    for row in snap["fills"]:
        row["feeCcy"] = "BTC"
    d = dashboard(tmp_path, snap)
    [tr] = d["trades"]
    assert tr["qty"] == pytest.approx(10000/101000)
    assert tr["entry_leg"]["notional_usd"] == tr["exit_leg"]["notional_usd"] == 10000
    assert tr["entry_leg"]["fee_bps"] == -2 and tr["exit_leg"]["fee_bps"] == 5.05
    assert tr["total_fee_usd"] == 5.05 and tr["total_rebate_usd"] == 2
    assert tr["net_pnl_usd"] == 97.97


def test_adjacent_positions_attach_clock_skewed_fill_once(tmp_path):
    snap = snapshot()
    first = snap["positions_history"][0]
    first.update(cTime=START - 20000, uTime=START - 500, closeAvgPx="100000", fee="-5", pnl="10", realizedPnl="5")
    second = {**first, "posId": "p2", "cTime": START, "uTime": END, "fee": "-70.50", "pnl": "1000", "realizedPnl": "929.50"}
    snap["positions_history"].append(second)
    snap["fills"][0]["fillTime"] = START - 5  # Opening fill predates the position record by 5 ms.
    snap["fills"].append({**snap["fills"][1], "tradeId": "old", "billId": "old", "ts": START - 500, "fee": "-5"})
    latest, earlier = dashboard(tmp_path, snap)["trades"]
    assert latest["entry_leg"]["gross_fee_usd"] == 20
    assert latest["exit_leg"]["gross_fee_usd"] == 50.5
    assert earlier["entry_leg"]["fee_known"] is False
    assert earlier["exit_leg"]["gross_fee_usd"] == 5


def test_hedged_fills_and_mixed_roles_keep_their_own_rate_denominators(tmp_path):
    snap = snapshot()
    snap["fills"][0].update(fillSz="75", fee="-15", posSide="long")
    snap["fills"].extend([
        {**snap["fills"][0], "billId": "m2", "tradeId": "m2", "execType": "T", "fillSz": "25", "fee": "-12.5"},
        {**snap["fills"][0], "billId": "s", "tradeId": "s", "posSide": "short", "fee": "-200"},
    ])
    snap["positions_history"][0]["fee"] = "-78"
    d = dashboard(tmp_path, snap)
    [tr] = d["trades"]
    assert tr["entry_leg"]["fee_bps"] == 2.75
    assert tr["entry_leg"]["role"] == "UNKNOWN"
    assert d["fee_matrix"][0]["maker_net_bps"] == 2
    assert d["fee_matrix"][0]["taker_net_bps"] == 5


def test_ambiguous_fills_stay_unattributed_until_position_id_resolves_them(tmp_path):
    snap = snapshot("+20", "-50.5")
    other = {**snap["positions_history"][0], "posId": "other", "pnl": "10", "fee": "-4", "realizedPnl": "6"}
    snap["positions_history"].append(other)
    snap["fills"][1]["posId"] = "p1"
    d = dashboard(tmp_path, snap)
    trades = {t["trade_id"].split("_")[1]: t for t in d["trades"]}
    assert all(not t["entry_leg"]["fee_known"] for t in trades.values())
    assert trades["p1"]["exit_leg"]["gross_fee_usd"] == 50.5
    assert not trades["other"]["exit_leg"]["fee_known"]
    assert (trades["p1"]["net_pnl_usd"], trades["other"]["net_pnl_usd"]) == (969.5, 6)
    assert d["summary"]["total_rebate_usd"] == 20


def test_invalid_listing_dates_do_not_override_historical_contract(tmp_path):
    snap = snapshot()
    snap["instruments"] = [dict(SPEC, ctVal="100", listTime="broken"), dict(SPEC, listTime=str(START - 1000)),
                           dict(SPEC, ctVal="50", listTime=str(END + 1))]
    d = dashboard(tmp_path, snap)
    assert d["trades"][0]["qty"] == 1
    assert d["trades"][0]["entry_leg"]["fee_bps"] == 2


@pytest.mark.parametrize(("value", "known"), [("NaN", False), ("+", False), ("-0", True)])
def test_invalid_fill_fee_is_unknown_and_signed_zero_is_zero(tmp_path, value, known):
    snap = snapshot("0")
    snap["fills"][0]["fee"] = value
    d = dashboard(tmp_path, snap)
    [tr] = d["trades"]
    assert tr["entry_leg"]["fee_known"] is known
    assert tr["entry_leg"]["fee_bps"] == (0 if known else None)
    assert tr["total_rebate_usd"] == 0 and tr["net_fee_usd"] == 50.5


def test_estimated_cash_rebate_has_no_settlement_timestamp(tmp_path):
    snap = snapshot()
    snap["observed_rebate_ratio"] = 0.1
    [tr] = dashboard(tmp_path, snap)["trades"]
    assert tr["entry_leg"]["rebate_status"] == "PENDING"
    assert tr["entry_leg"]["settled_at"] == ""
