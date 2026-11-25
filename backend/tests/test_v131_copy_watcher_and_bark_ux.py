"""TDD test suite for v1.3.1: Built-in Public Copy Watcher, Smart Bark URL parsing, Save-on-Test & Network Timeout Diagnostics."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from opentradeledger.api.app import create_app
from opentradeledger.config import NotificationSettings
from opentradeledger.core.copy_watcher import (
    CopyWatcherService,
    PublicCopyWatcherClient,
    extract_portfolio_ids,
)
from opentradeledger.core.notifier import dispatch_notification, parse_bark_input
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.venue_health import humanize_exchange_error
from opentradeledger.security.readonly_guard import ReadOnlyGuardError


def test_parse_bark_input_handles_plain_key_and_full_app_urls():
    # Plain device key
    srv, key = parse_bark_input("https://api.day.app", "AbCdEfGh123456")
    assert srv == "https://api.day.app"
    assert key == "AbCdEfGh123456"

    # Full default sample URL copied straight from iOS Bark App
    srv2, key2 = parse_bark_input(
        "https://api.day.app",
        "https://api.day.app/MyDeviceKey98765/这里改成你自己的推送内容?sound=bell",
    )
    assert srv2 == "https://api.day.app"
    assert key2 == "MyDeviceKey98765"

    # Self-hosted Bark URL pasted into either server or key box
    srv3, key3 = parse_bark_input(
        "https://api.day.app",
        "https://bark.example.org:8443/SelfHostKey777/",
    )
    assert srv3 == "https://bark.example.org:8443"
    assert key3 == "SelfHostKey777"

    # User accidentally pasted full Bark URL into server box and left key empty
    srv4, key4 = parse_bark_input(
        "https://bark.example.org/KeyInServerBox888/hello",
        "",
    )
    assert srv4 == "https://bark.example.org"
    assert key4 == "KeyInServerBox888"


def test_dispatch_notification_normalizes_full_bark_url_without_double_encoding():
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, json={"code": 200, "message": "success"})

    transport = httpx.MockTransport(handler)

    async def _run():
        async with httpx.AsyncClient(transport=transport) as client:
            return await dispatch_notification(
                NotificationSettings(
                    bark_server_url="https://api.day.app",
                    bark_push_key="https://api.day.app/RealKeyXYZ123/示例推送文字",
                ),
                title="测试标题",
                body="测试内容",
                client=client,
            )

    res = asyncio.run(_run())
    assert res["ok"] is True
    assert res["sent"] == ["bark"]
    assert len(captured_urls) == 1
    assert captured_urls[0].startswith("https://api.day.app/RealKeyXYZ123/")
    assert "https%3A" not in captured_urls[0]


def test_extract_portfolio_ids_from_share_text_urls_and_digits():
    raw = (
        "我在币安发现一位超稳带单员，快来看看："
        "https://www.binance.com/zh-CN/copy-trading/lead-details/3988776655443322110?timeRange=30D\n"
        "备用组合: 4111222233334444555, 3988776655443322110"
    )
    pids = extract_portfolio_ids(raw)
    assert pids == ("3988776655443322110", "4111222233334444555")


def test_public_copy_watcher_guard_blocks_auth_headers_and_non_whitelisted_post():
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "000000", "data": {"list": []}})

    async def _run_forbidden_path():
        async with httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)) as hc:
            watcher_client = PublicCopyWatcherClient(client=hc)
            await watcher_client._public_post("https://www.binance.com/fapi/v1/order", {"symbol": "BTCUSDT"})

    with pytest.raises(ReadOnlyGuardError):
        asyncio.run(_run_forbidden_path())

    async def _run_forbidden_header():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(ok_handler),
            headers={"X-MBX-APIKEY": "SHOULD_NEVER_BE_SENT"},
        ) as hc:
            watcher_client = PublicCopyWatcherClient(client=hc)
            await watcher_client._public_post(
                "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-portfolio/order-history",
                {"portfolioId": "3988776655443322110"},
            )

    with pytest.raises(ReadOnlyGuardError):
        asyncio.run(_run_forbidden_header())


def test_copy_watcher_infers_positions_and_respects_granular_bark_toggles(tmp_path: Path):
    bark_calls: list[httpx.Request] = []
    open_slots = {"current": 400, "max": 400}
    orders_state: list[dict[str, Any]] = [
        {
            "orderTime": 1700000100000,
            "symbol": "BTCUSDT",
            "side": "BUY",
            "positionSide": "LONG",
            "type": "LIMIT",
            "executedQty": "0.5",
            "avgPrice": "90000",
            "totalPnl": "0",
        }
    ]

    def mock_router(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "api.day.app" in url_str:
            bark_calls.append(request)
            return httpx.Response(200, json={"code": 200, "message": "success"})
        if "/lead-portfolio/detail" in url_str:
            return httpx.Response(
                200,
                json={
                    "code": "000000",
                    "data": {
                        "leadPortfolioId": "3988776655443322110",
                        "nickname": "AlphaQuant",
                        "status": "ACTIVE",
                        "portfolioType": "PUBLIC",
                        "currentCopyCount": open_slots["current"],
                        "maxCopyCount": open_slots["max"],
                        "fixedAmountMinCopyUsd": 100,
                        "profitSharingRate": "10",
                    },
                },
            )
        if "/lead-portfolio/position-history" in url_str:
            return httpx.Response(200, json={"code": "000000", "data": {"list": []}})
        if "/lead-portfolio/order-history" in url_str:
            return httpx.Response(200, json={"code": "000000", "data": {"list": list(orders_state)}})
        if "/fapi/v1/ticker/price" in url_str:
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "92000"})
        return httpx.Response(404)

    store = LedgerStore(tmp_path)
    prefs = store.get_preferences()
    prefs.notifications.bark_server_url = "https://api.day.app"
    prefs.notifications.bark_push_key = "TestBarkKey12345"
    store.save_preferences(prefs)

    transport = httpx.MockTransport(mock_router)

    async def _scenario():
        async with httpx.AsyncClient(transport=transport) as hc:
            svc = CopyWatcherService(store=store, http_client=hc)
            # Add target with slot_alert=True, trade_alert=True
            added = await svc.add_targets("https://www.binance.com/zh-CN/copy-trading/lead-details/3988776655443322110")
            assert len(added) == 1
            assert added[0]["nickname"] == "AlphaQuant"

            # Cycle 1 (initial sight): full slots (400/400), historical order anchored silently -> 0 Bark alerts!
            st1 = await svc.poll_once(force_trades=True)
            assert len(bark_calls) == 0
            item1 = st1["items"][0]
            assert len(item1["inferred_positions"]) == 1
            pos = item1["inferred_positions"][0]
            assert pos["symbol"] == "BTCUSDT"
            assert pos["qty"] == 0.5
            assert pos["unrealized_pnl_usd"] == 1000.0

            # Cycle 2: trader opens +0.5 BTC, AND 2 copy slots open up (398/400) -> 1 slot alarm + 1 trade chime!
            open_slots["current"] = 398
            orders_state.insert(
                0,
                {
                    "orderTime": 1700000200000,
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "positionSide": "LONG",
                    "type": "MARKET",
                    "executedQty": "0.5",
                    "avgPrice": "91000",
                    "totalPnl": "0",
                },
            )
            await svc.poll_once(force_trades=True)
            assert len(bark_calls) == 2
            slot_req_url = str(bark_calls[0].url)
            trade_req_url = str(bark_calls[1].url)
            assert "sound=alarm" in slot_req_url and "call=1" in slot_req_url
            assert "sound=bell" in trade_req_url and "call=1" not in trade_req_url

            # Disable trade_alert for this target -> new order should NOT trigger trade Bark push
            svc.update_target("3988776655443322110", trade_alert=False)
            orders_state.insert(
                0,
                {
                    "orderTime": 1700000300000,
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "positionSide": "LONG",
                    "type": "MARKET",
                    "executedQty": "1.0",
                    "avgPrice": "93000",
                    "totalPnl": "2500",
                },
            )
            await svc.poll_once(force_trades=True)
            # Slot alert still in 180s cooldown and trade_alert is False -> count stays 2!
            assert len(bark_calls) == 2

    asyncio.run(_scenario())


def test_api_copy_watcher_demo_and_save_on_test_notify(tmp_path: Path):
    app = create_app(
        data_dir=tmp_path,
        demo_mode=True,
        enable_background_sync=False,
        auto_prompt_setup=False,
    )
    with TestClient(app) as client:
        r_demo = client.get("/api/v1/extensions/copy-watcher")
        assert r_demo.status_code == 200
        body = r_demo.json()
        assert body["demo_mode"] is True
        assert len(body["items"]) >= 2
        assert any(it["has_open_slot"] for it in body["items"])

        # Switch off demo mode -> empty list for brand-new user (zero personal hardcodes)
        client.post("/api/v1/demo", json={"enabled": False})
        r_live = client.get("/api/v1/extensions/copy-watcher")
        assert r_live.status_code == 200
        assert r_live.json()["items"] == []


def test_humanize_exchange_error_recognizes_status_zero_connection_failures():
    diag = humanize_exchange_error("binance", "Binance GET /api/v3/account failed (0):")
    assert diag["code"] == "NETWORK_UNREACHABLE"
    assert "网络或代理超时" in diag["reason_zh"]


def test_copy_watcher_custom_polling_intervals_and_validation(tmp_path: Path):
    app = create_app(
        data_dir=tmp_path,
        demo_mode=False,
        enable_background_sync=False,
        auto_prompt_setup=False,
    )
    with TestClient(app) as client:
        # Default intervals: 10s slot, 30s trade, 180s cooldown
        st0 = client.get("/api/v1/extensions/copy-watcher").json()
        assert st0["interval_sec"] == 10
        assert st0["trade_poll_sec"] == 30
        assert st0["cooldown_sec"] == 180

        # Reject dangerously fast (< 3s) interval to protect user IP from Binance ban
        r_bad = client.put(
            "/api/v1/extensions/copy-watcher/config",
            json={"interval_sec": 1, "trade_interval_sec": 30, "cooldown_sec": 180},
        )
        assert r_bad.status_code == 400

        # Update to custom intervals: 5s slot, 20s trade, 120s cooldown
        r_ok = client.put(
            "/api/v1/extensions/copy-watcher/config",
            json={"interval_sec": 5, "trade_interval_sec": 20, "cooldown_sec": 120},
        )
        assert r_ok.status_code == 200
        body = r_ok.json()
        assert body["interval_sec"] == 5
        assert body["trade_poll_sec"] == 20
        assert body["cooldown_sec"] == 120

        # Persists in GET /api/v1/settings as well
        prefs = client.get("/api/v1/settings").json()
        assert prefs["watcher_interval_sec"] == 5
        assert prefs["watcher_trade_interval_sec"] == 20
        assert prefs["watcher_cooldown_sec"] == 120

