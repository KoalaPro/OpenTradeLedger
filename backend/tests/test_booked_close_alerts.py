"""Private account Bark notifications follow the settled trade rows visible in the ledger."""
from unittest.mock import AsyncMock
from urllib.parse import unquote

import httpx

import pytest

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.notifier import dispatch_notification
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.sync_manager import SyncManager
from opentradeledger.security.vault import CredentialVault, ExchangeCredentialRecord
from test_scaleout_cycle_evidence import START, _snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["binance", "bingx", "bybit", "kucoin", "htx", "hyperliquid"])
async def test_private_alerts_only_for_new_booked_exits(tmp_path, monkeypatch, venue):
    store = LedgerStore(tmp_path)
    prefs = store.get_preferences()
    prefs.notifications.bark_push_key = "synthetic"
    store.save_preferences(prefs)
    vault = CredentialVault(tmp_path)
    vault.upsert(ExchangeCredentialRecord(venue_id=venue, api_key="fake", api_secret="fake", remote_uid="fixture", account_role="master"))
    registry = build_default_registry()
    adapter = registry.get(venue)
    service = LedgerService(store, vault, registry)
    manager = SyncManager(store=store, vault=vault, registry=registry, ledger_service=service)
    client = AsyncMock()
    client.fetch_um_orders_for_symbols.return_value = []
    client.fetch_um_commission_rates.return_value = {}
    client.fetch_bnb_daily_prices.return_value = {}
    client.fetch_cashflows.return_value = []
    client.fetch_um_income.return_value = [{"symbol": "ETHUSDT", "time": START, "incomeType": "COMMISSION", "income": "-1"}]
    monkeypatch.setattr(adapter, "create_client", lambda _: client)
    messages = []

    def bark_handler(request):
        _, _, title, body = unquote(request.url.path).split("/", 3)
        messages.append((title, body))
        return httpx.Response(200, json={"code": 200})

    async def capture(settings, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(bark_handler)) as bark:
            return await dispatch_notification(settings, client=bark, **kwargs)

    monkeypatch.setattr("opentradeledger.core.sync_manager.dispatch_notification", capture)
    # Existing round trip, then opening, zero-PnL scale-out, additional entry and two exits.
    history = [(1, 100, 0), (-1, 105, 5), (3, 100, 0), (-1, 100, 0), (1, 100, 0), (-1, 101, 1), (-2, 102, 4)]
    for n, expected_total in [(2, 0), (3, 0), (4, 1), (4, 1), (5, 1), (7, 2), (7, 2)]:
        actions = history[:n]
        remaining = sum(q for q, _, _ in actions)
        if venue == "binance":
            positions = [{"symbol": "ETHUSDT", "positionSide": "LONG", "positionAmt": remaining, "entryPrice": 100}] if remaining else []
            client.fetch_live_overview_bundle.return_value = {"pm_account": {"accountEquity": "1000"}, "futures_positions": positions}
            client.fetch_um_trades_for_symbols.return_value = [
                dict(id=i, orderId=i, symbol="ETHUSDT", side="BUY" if q > 0 else "SELL", positionSide="LONG",
                     qty=abs(q), price=price, realizedPnl=gross, commission=0.1, commissionAsset="USDT", time=START+i*60000)
                for i, (q, price, gross) in enumerate(actions, 1)
            ]
        else:
            snapshot = _snapshot(venue, actions)
            if venue == "bingx":
                snapshot["positions"] = [{"symbol": "ETH-USDT", "positionSide": "LONG", "positionAmt": remaining, "entryPrice": 100}] if remaining else []
                snapshot["swap_balances"] = [{"asset": "USDT", "equity": "1000"}]
            client.fetch_full_snapshot.return_value = snapshot
        result = await manager.sync_all(force_all=True)
        assert result["venues"][venue]["ok"], result
        assert len(messages) == expected_total, (venue, n, messages)
    assert "1 笔" in messages[0][1] and "2 笔" in messages[1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("issue", ["unreadable", "replaced", "cleared", "rejected"])
async def test_private_alert_needs_evidence_and_same_device_throughout_sync(tmp_path, monkeypatch, issue):
    from opentradeledger.adapters.base import VenueSyncOutcome

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    registry = build_default_registry()
    service = LedgerService(store, vault, registry)
    service.update_preferences({"notifications": {"bark_push_key": "initial-device"}})
    vault.upsert(ExchangeCredentialRecord(venue_id="bingx", api_key="fake", api_secret="fake", remote_uid="fixture", account_role="master"))
    adapter = registry.get("bingx")
    manager = SyncManager(store=store, vault=vault, registry=registry, ledger_service=service)
    history = [(2, 100, 0), (-1, 100, 0), (-1, 105, 5)]
    store.upsert_plugin_snapshot("bingx", _snapshot("bingx", history[:2]))
    monkeypatch.setattr(adapter, "create_client", lambda _: None)
    stage = 0

    async def sync(*args, **kwargs):
        nonlocal stage
        stage += 1
        store.upsert_plugin_snapshot("bingx", _snapshot("bingx", history))
        if stage == 1 and issue in {"replaced", "cleared"}:
            service.update_preferences({"notifications": {"bark_push_key": "" if issue == "cleared" else "new-device"}})
        # This is deliberately wrong: a raw fill count must never authorize delivery.
        return VenueSyncOutcome(equity_usd=123, has_open_activity=False, fp_before=(1,), fp_after=(2,), new_closed_delta=999)

    if issue == "unreadable":
        def broken(*args, **kwargs):
            raise ValueError("No usable settlement evidence")
        monkeypatch.setattr(adapter, "build_venue_ledger", broken)
    monkeypatch.setattr(adapter, "sync_venue", sync)
    requests = []

    def reject(request):
        requests.append(request)
        return httpx.Response(503)

    async def send(settings, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as client:
            return await dispatch_notification(settings, client=client, **kwargs)

    monkeypatch.setattr("opentradeledger.core.sync_manager.dispatch_notification", send)
    for _ in range(2):
        assert (await manager.sync_all(force_all=True))["venues"]["bingx"]["ok"]
    assert len(requests) == (1 if issue == "rejected" else 0)


@pytest.mark.asyncio
async def test_private_sync_health_alerts_and_recovery(tmp_path, monkeypatch):
    from opentradeledger.adapters.base import VenueSyncOutcome

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    registry = build_default_registry()
    service = LedgerService(store, vault, registry)
    service.update_preferences({"notifications": {"bark_push_key": "device"}})
    vault.upsert(ExchangeCredentialRecord(venue_id="bybit", api_key="dummy", api_secret="dummy", remote_uid="u", account_role="master"))
    adapter = registry.get("bybit")
    monkeypatch.setattr(adapter, "create_client", lambda _: None)
    manager = SyncManager(store=store, vault=vault, registry=registry, ledger_service=service)
    events = []

    def bark(request):
        events.append(unquote(request.url.path))
        return httpx.Response(200, json={"code": 200})

    async def send(settings, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(bark)) as client:
            return await dispatch_notification(settings, client=client, **kwargs)
    monkeypatch.setattr("opentradeledger.core.sync_manager.dispatch_notification", send)

    async def sync(*args, **kwargs):
        if mode == "timeout":
            raise httpx.ReadTimeout("timeout")
        if mode == "invalid":
            raise RuntimeError("Bybit HTTP 401: Invalid API key")
        warnings = ["funding: temporarily unavailable"] if mode == "partial" else []
        return VenueSyncOutcome(equity_usd=100, has_open_activity=False, fp_before=(1,), fp_after=(1,), warnings=warnings)
    monkeypatch.setattr(adapter, "sync_venue", sync)
    phases = [("healthy", 0), ("timeout", 0), ("timeout", 0), ("timeout", 1), ("timeout", 1), ("healthy", 1),
              ("partial", 1), ("healthy", 1), ("invalid", 2), ("invalid", 2), ("healthy", 2)]
    for mode, expected in phases:
        await manager.sync_all(force_all=True)
        assert len(events) == expected, mode
    assert all("同步异常" in e for e in events)


@pytest.mark.parametrize("venue", ["gate", "bitget", "mexc", "okx", "easicoin"])
def test_split_exit_orders_are_counted_once_across_statement_updates(tmp_path, venue):
    from opentradeledger.core.sync_manager import _booked_exits, _new_booked_exits
    from test_native_position_costs import BASE, add_position
    from test_scaleout_cycle_evidence import _record_native_fills

    store = LedgerStore(tmp_path)
    adapter = build_default_registry().get(venue)
    scope = store.for_account(venue, venue)

    def receipts():
        return _booked_exits(adapter, scope, BASE // 1000 + 3600)

    if venue == "easicoin":
        first = [(3, 100, 0), (-1, 100, 0), (1, 102, 0)]
        _record_native_fills(store, venue, first, native_history=True)
        previous = receipts()
        _record_native_fills(store, venue, first + [(-3, 102, 4)], native_history=True)
    else:
        # Two partial fills of the same exit order form one settled order.
        first = [(0, 3, 100, "opening", None), (1000, -1, 100, "first", 0), (2000, -1, 101, "first", 1)]
        add_position(store, venue, first, reported_gross=1)
        previous = receipts()
        add_position(store, venue, first + [(3000, -1, 102, "last", 2)], reported_gross=3)
    latest = receipts()
    assert previous and latest
    assert _new_booked_exits({}, previous) == 1
    assert _new_booked_exits(previous, latest) == 1
    assert _new_booked_exits(latest, receipts()) == 0
