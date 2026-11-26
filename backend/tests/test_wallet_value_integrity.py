"""Regression coverage for invalid amounts inside an otherwise successful wallet response."""
from pathlib import Path

import httpx
import pytest

from opentradeledger.adapters.binance.client import valid_wallet_balances
from opentradeledger.adapters.bitget.client import valid_funding_assets
from opentradeledger.adapters.bybit.client import BybitApiError, BybitReadOnlyClient
from opentradeledger.adapters.bybit.sync import sync_bybit_venue
from opentradeledger.adapters.compat_creds import MexcCredentials
from opentradeledger.adapters.easicoin.client import _extract_easicoin_spot_balances
from opentradeledger.adapters.gate.client import _is_valid_gate_spot_accounts
from opentradeledger.adapters.mexc.client import MexcReadOnlyClient
from opentradeledger.adapters.okx.client import OkxApiError, OkxReadOnlyClient
from opentradeledger.adapters.okx.sync import sync_okx_venue
from opentradeledger.core.store import LedgerStore


@pytest.mark.parametrize(("validate", "name", "amount"), [
    (valid_wallet_balances, "walletName", "balance"),
    (valid_funding_assets, "coin", "balance"),
    (_is_valid_gate_spot_accounts, "currency", "available"),
    (lambda rows: _extract_easicoin_spot_balances({"data": rows}) is not None, "token_name", "balance"),
])
def test_nonempty_wallet_rows_require_currency_and_finite_amount(validate, name, amount):
    assert validate([])
    for qty in (0, "0", "2.01"):
        assert validate([{name: "USDT", amount: qty}])
    for bad in ({name: "USDT"}, {amount: "3"}, {name: "", amount: "1"}, *(
        {name: "USDT", amount: value} for value in (None, "", False, "NaN", "Infinity", "unavailable")
    )):
        assert not validate([{name: "USDC", amount: "7"}, bad]), bad


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["bybit", "okx"])
async def test_repair_stored_invalid_amount_after_restart_without_losing_history(tmp_path: Path, venue):
    store = LedgerStore(tmp_path)
    calls = []
    if venue == "okx":
        core = {"balances": [{"totalEq": "100"}]}
        old = {"funding": [{"ccy": "USDT", "bal": "unavailable"}], "fills": [{"billId": "kept"}]}
        history_field = "fills"
    else:
        core = {"wallet": {"list": [{"totalEquity": "100"}]}}
        old = {"funding": {"balance": [{"coin": "USDT", "walletBalance": "unavailable"}]},
               "closed_pnl": [{"orderId": "kept", "symbol": "BTCUSDT", "updatedTime": "123"}]}
        history_field = "closed_pnl"
    store.upsert_plugin_snapshot(venue, {**core, **old})

    def upstream(req):
        p = req.url.path
        calls.append(p)
        if venue == "okx":
            data = [{"totalEq": "100"}] if p.endswith("/account/balance") else []
            if p.endswith("/asset/balances"):
                data = [{"ccy": "USDT", "bal": "20"}]
            return httpx.Response(200, json={"code": "0", "data": data})
        result = {}
        if p.endswith("wallet-balance"):
            result = core["wallet"]
        if p.endswith("query-account-coins-balance"):
            result = {"balance": [{"coin": "USDT", "walletBalance": "20"}]}
        return httpx.Response(200, json={"retCode": 0, "result": result})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        if venue == "okx":
            outcome = await sync_okx_venue(OkxReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=client),
                                           store, now_s=1765000000)
        else:
            outcome = await sync_bybit_venue(BybitReadOnlyClient(api_key="k", api_secret="s", client=client),
                                             store, now_s=1765000000)
    assert calls and outcome.equity_usd == 120
    snap = store.venue_scope(venue).get_kv("snapshot")
    assert snap[history_field] == old[history_field]
    assert "unavailable" not in str(snap["funding"])


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["bybit", "okx", "mexc"])
async def test_non_numeric_wallet_payload_never_becomes_a_clean_zero(venue):
    amount = None
    def upstream(req):
        p = req.url.path
        if venue == "okx":
            return httpx.Response(200, json={"code": "0", "data": [{"ccy": "USDT", "bal": amount}]})
        if venue == "bybit":
            return httpx.Response(200, json={"retCode": 0, "result": {"balance": [{"coin": "USDT", "walletBalance": amount}]}})
        if p == "/api/v3/account":
            return httpx.Response(200, json={"balances": [{"asset": "USDT", "free": amount}]})
        if p.startswith("/api/v1/"):
            return httpx.Response(200, json={"success": True, "code": 0, "data": []})
        return httpx.Response(200, json=[])
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        for amount in (None, "NaN", False, "unavailable"):
            if venue == "okx":
                c = OkxReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=client)
                with pytest.raises(OkxApiError):
                    await c.get_funding_balances()
            elif venue == "bybit":
                c = BybitReadOnlyClient(api_key="k", api_secret="s", client=client)
                with pytest.raises(BybitApiError):
                    await c.get_funding_balance()
            else:
                c = MexcReadOnlyClient(MexcCredentials(api_key="k", api_secret="s"), http_client=client)
                assert "spot_balances" in (await c.fetch_live_overview_bundle()).get("_failed_fields", [])

@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["okx", "bybit"])
async def test_unusable_merged_snapshot_does_not_replace_either_sqlite_copy(tmp_path, venue):
    store = LedgerStore(tmp_path)
    if venue == "okx":
        sync = sync_okx_venue
        key = "balances"
        valid = {key: [{"totalEq": "100"}], "funding": [{"ccy": "USDT", "bal": "20"}]}
        broken = {key: valid[key], "funding": [{"ccy": "USDT", "bal": "unavailable"}]}
    else:
        sync = sync_bybit_venue
        key = "wallet"
        valid = {key: {"list": [{"totalEquity": "100"}]}, "funding": {"balance": [{"coin": "USDT", "walletBalance": "20"}]}}
        broken = {key: valid[key], "funding": {"balance": [{"coin": "USDT", "walletBalance": "unavailable"}]}}
    store.upsert_plugin_snapshot(venue, valid)
    before = store.snapshot_venue_partition(venue)
    class Snapshot:
        async def fetch_full_snapshot(self, **kwargs):
            return broken
    with pytest.raises(ValueError):
        await sync(Snapshot(), store, now_s=1765000000)
    assert store.snapshot_venue_partition(venue) == before

@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["okx", "bybit"])
async def test_first_sync_with_malformed_funding_row_has_no_trusted_equity(tmp_path, venue):
    def upstream(req):
        path = req.url.path
        if venue == "okx":
            data = []
            if path.endswith("/account/balance"):
                data = [{"totalEq": "100"}]
            elif path.endswith("/asset/balances"):
                data = [{"ccy": "USDT", "bal": None}]
            return httpx.Response(200, json={"code": "0", "data": data})
        result = {}
        if path.endswith("wallet-balance"):
            result = {"list": [{"totalEquity": "100"}]}
        elif path.endswith("query-account-coins-balance"):
            result = {"balance": [{"coin": "USDT", "walletBalance": None}]}
        return httpx.Response(200, json={"retCode": 0, "result": result})
    store = LedgerStore(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        if venue == "okx":
            sync = sync_okx_venue
            account = OkxReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=client)
        else:
            sync = sync_bybit_venue
            account = BybitReadOnlyClient(api_key="k", api_secret="s", client=client, max_windows=1)
        with pytest.raises(ValueError, match="funding"):
            await sync(account, store, now_s=1765000000)
    assert store.venue_scope(venue).get_kv("snapshot") is None

@pytest.mark.asyncio
async def test_bybit_isolated_margin_wallet_with_unavailable_account_wide_equity():
    rows = [{"coin": "USDT", "walletBalance": "2", "usdValue": "2"},
            {"coin": "ETH", "walletBalance": "0.1", "usdValue": "350.25"}]
    result = {"list": [{"accountType": "UNIFIED", "totalEquity": "", "coin": rows}]}
    def upstream(_):
        return httpx.Response(200, json={"retCode": 0, "result": result})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        client = BybitReadOnlyClient(api_key="k", api_secret="s", client=http)
        wallet = await client.get_wallet_balance()
        assert float(wallet["list"][0]["totalEquity"]) == 352.25
        del rows[0]["usdValue"]
        with pytest.raises(BybitApiError):
            await client.get_wallet_balance()


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["bybit", "okx"])
async def test_bad_component_valuation_cannot_break_dashboard_with_valid_account_total(venue):
    def upstream(_):
        if venue == "okx":
            return httpx.Response(200, json={"code": "0", "data": [{"totalEq": "100", "details": [
                {"ccy": "USDT", "eq": "100", "eqUsd": "unavailable"}]}]})
        return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"totalEquity": "100", "coin": [
            {"coin": "USDT", "walletBalance": "100", "usdValue": "unavailable"}]}]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        if venue == "okx":
            c = OkxReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=http)
            with pytest.raises(OkxApiError):
                await c.get_account_balance()
        else:
            c = BybitReadOnlyClient(api_key="k", api_secret="s", client=http)
            with pytest.raises(BybitApiError):
                await c.get_wallet_balance()


@pytest.mark.asyncio
@pytest.mark.parametrize("assets", [{"totalEquity": "100"}, [{"accountEquity": "70"}, {"usdtEquity": "30"}]])
async def test_bitget_accepted_totals_reach_both_sync_and_dashboard(tmp_path, assets):
    from opentradeledger.adapters.bitget.accounting import build_bitget_venue_ledger
    from opentradeledger.adapters.bitget.client import BitgetReadOnlyClient
    from opentradeledger.adapters.bitget.sync import sync_bitget_venue
    from opentradeledger.adapters.compat_creds import BitgetCredentials
    from opentradeledger.adapters.registry import BitgetAdapter

    def upstream(req):
        data = {"/api/v3/account/assets": assets, "/api/v3/account/settings": {"uid": "qa"},
                "/api/v3/account/funding-assets": [{"coin": "USDT", "balance": "20"}]}.get(req.url.path, [])
        return httpx.Response(200, json={"code": "00000", "data": data})

    store = LedgerStore(tmp_path)
    store.venue_scope("bitget").set_kv("backfill_completed", True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        client = BitgetReadOnlyClient(BitgetCredentials(api_key="k", api_secret="s", passphrase="p"), http_client=http)
        outcome = await sync_bitget_venue(client, store, now_s=1765000000)
    assert outcome.equity_usd == 120
    bundle = build_bitget_venue_ledger(store, BitgetAdapter.meta, journals={}, now_s=1765000000)
    assert bundle.exchange_card["equity_usd"] == 120


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["okx", "bybit"])
@pytest.mark.parametrize("previous", ["NaN", "", "Infinity", "-0", "20", None])
async def test_funding_fallback_only_reuses_trusted_values(tmp_path, venue, previous):
    store = LedgerStore(tmp_path)
    shape = "broken"
    is_okx = venue == "okx"

    def funding(qty):
        return [{"ccy": "USDT", "bal": qty}] if is_okx else {"balance": [{"coin": "USDT", "walletBalance": qty}]}

    def upstream(req):
        path = req.url.path
        data = [] if is_okx else {}
        if path.endswith("/account/balance"):
            data = [{"totalEq": "100"}]
        elif path.endswith("/wallet-balance"):
            data = {"list": [{"totalEquity": "100"}]}
        elif path.endswith(("/asset/balances", "/query-account-coins-balance")):
            if shape == "denied":
                return httpx.Response(403, json={"msg": "Permission denied"})
            data = {} if shape == "broken" else funding("20")
        elif path.endswith("closed-pnl"):
            data = {"list": []}
        return httpx.Response(200, json={"code": "0", "data": data} if is_okx else {"retCode": 0, "result": data})

    if previous is not None:
        store.upsert_plugin_snapshot(venue, {"funding": funding(previous)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        if is_okx:
            client = OkxReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=http)
            sync = sync_okx_venue
        else:
            client = BybitReadOnlyClient(api_key="k", api_secret="s", client=http, max_windows=1)
            sync = sync_bybit_venue
        for shape in ("broken", "denied"):
            before = store.snapshot_venue_partition(venue)
            if previous in ("NaN", "", "Infinity") or (previous is None and shape == "broken"):
                with pytest.raises(ValueError, match="funding"):
                    await sync(client, store, now_s=1765000000)
                assert store.snapshot_venue_partition(venue) == before
            else:
                outcome = await sync(client, store, now_s=1765000000)
                assert outcome.equity_usd == (120 if previous == "20" else 100)
                assert any("funding" in warning for warning in outcome.warnings)
        shape = "healthy"
        outcome = await sync(client, store, now_s=1765000000)
        assert outcome.equity_usd == 120
        assert store.venue_scope(venue).get_kv("snapshot")["funding"] == funding("20")


@pytest.mark.parametrize("venue", ["bybit", "okx"])
@pytest.mark.parametrize("invalid_amount", ["NaN", ""])
def test_dashboard_isolates_bad_historical_funding_until_new_balance(tmp_path, venue, invalid_amount):
    from opentradeledger.adapters.registry import build_default_registry
    from opentradeledger.core.ledger_service import LedgerService
    from opentradeledger.security.vault import CredentialVault

    def snapshot(v, main, qty):
        if v == "okx":
            return {"balances": [{"totalEq": main}], "funding": [{"ccy": "USDT", "bal": qty}]}
        return {"wallet": {"list": [{"totalEquity": main}]}, "funding": {"balance": [{"coin": "USDT", "walletBalance": qty}]}}

    store = LedgerStore(tmp_path)
    other = "okx" if venue == "bybit" else "bybit"
    store.upsert_plugin_snapshot(other, snapshot(other, "25", "0"))
    store.upsert_plugin_snapshot(venue, snapshot(venue, "100", invalid_amount))
    service = LedgerService(store, CredentialVault(tmp_path), build_default_registry(), default_demo_mode=False)
    before = service.get_dashboard(range_key="all")
    assert before["summary"]["total_equity_usd"] == 25
    assert {card["venue_id"] for card in before["exchanges"]} == {other}
    assert any(w["venue_id"] == venue and "余额待重新同步" in w["summary_zh"] for w in before["warnings"])
    store.upsert_plugin_snapshot(venue, snapshot(venue, "100", "20"))
    after = service.get_dashboard(range_key="all")
    assert after["summary"]["total_equity_usd"] == 145
    assert all(w["venue_id"] != venue for w in after["warnings"])


@pytest.mark.parametrize(("available_fields", "expected"), [
    ({"available": 0, "usdtAvailable": "23"}, 0),
    ({"available": "0", "usdtAvailable": "23"}, 0),
    ({"usdtAvailable": 0}, 0),
    ({"available": None, "usdtAvailable": "23"}, 23),
    ({"available": "", "usdtAvailable": "23"}, 23),
    ({}, 120),
])
def test_bitget_account_available_margin_preserves_zero(tmp_path, available_fields, expected):
    from opentradeledger.adapters.registry import build_default_registry

    store = LedgerStore(tmp_path)
    uta = {"accountEquity": "120", **available_fields}
    store.venue_scope("bitget").set_kv("live_bundle", {"uta_assets": uta})
    adapter = build_default_registry().get("bitget")
    card = adapter.build_venue_ledger(store, journals={}, now_s=1765000000).exchange_card
    assert card["equity_usd"] == 120
    assert card["available_margin_usd"] == expected


@pytest.mark.parametrize("live", [
    {"uta_assets": {"assets": [{"coin": "USDT", "equity": "120", "available": 0}]}},
    {"uta_assets": {"available": 0, "assets": [{"coin": "USDT", "equity": "120", "available": "60"}]}},
    {"futures_accounts": [{"available": 0, "crossedMaxAvailable": "37", "usdtEquity": "120"}]},
])
def test_bitget_zero_available_margin_in_fallback_wallets(tmp_path, live):
    from opentradeledger.adapters.registry import build_default_registry

    store = LedgerStore(tmp_path)
    store.venue_scope("bitget").set_kv("live_bundle", live)
    card = build_default_registry().get("bitget").build_venue_ledger(store, journals={}, now_s=1765000000).exchange_card
    assert card["available_margin_usd"] == 0
