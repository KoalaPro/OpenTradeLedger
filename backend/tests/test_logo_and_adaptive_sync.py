"""Tests for automatic instrument logo resolution and adaptive idle-account backoff polling."""

from __future__ import annotations

import asyncio
from pathlib import Path
import struct

import httpx

from opentradeledger.core.logo_cache import InstrumentLogos
from opentradeledger.core.sync_manager import (
    IDLE_BACKOFF_LADDER_SEC,
    should_sync_venue,
    update_venue_schedule,
)


def _fake_png(width: int = 32, height: int = 32) -> bytes:
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(ihdr))
        + b"IHDR"
        + ihdr
        + b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_logo_resolver_handles_tokenized_stocks_multipliers_and_cross_venue_crypto(tmp_path: Path):
    png_bytes = _fake_png()
    catalog = {
        "NASDAQ:NVDA": {"exchange": "NASDAQ", "type": "stock", "logoid": "nvidia"},
        "NASDAQ:AAPL": {"exchange": "NASDAQ", "type": "stock", "logoid": "apple"},
        "BINANCE:PEPEUSDT.P": {"exchange": "BINANCE", "type": "swap", "base_currency_logoid": "crypto/XTVCPEPE"},
        "BINANCE:SUIUSDT.P": {"exchange": "BINANCE", "type": "swap", "base_currency_logoid": "crypto/XTVCSUI"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "scanner.tradingview.com":
            sym = request.url.params.get("symbol", "")
            if sym in catalog:
                return httpx.Response(200, json=catalog[sym])
            return httpx.Response(404, json={"code": "symbol_not_exists"})
        if request.url.host == "s3-symbol-logo.tradingview.com":
            return httpx.Response(200, content=png_bytes, headers={"content-type": "image/png"})
        return httpx.Response(404)

    cache = InstrumentLogos(tmp_path, transport=httpx.MockTransport(handler))

    async def _run():
        p1 = await cache.find("MEXC", "NVDAX_USDT")
        p2 = await cache.find("US", "AAPLX")
        p3 = await cache.find("BYBIT", "1000PEPE_USDT")
        p4 = await cache.find("MEXC", "SUI_USDT")
        return p1, p2, p3, p4

    p1, p2, p3, p4 = asyncio.run(_run())
    assert p1 is not None and p1.is_file()
    assert p2 is not None and p2.is_file()
    assert p3 is not None and p3.is_file()
    assert p4 is not None and p4.is_file()


def test_adaptive_idle_backoff_ladder_and_instant_wakeup():
    schedule: dict = {}
    t = 1_700_000_000

    # Initial sync is always due
    assert should_sync_venue(schedule, "mexc", now_s=t) is True

    # Active venue (>= 20 U) stays at base 20s interval
    e_active = update_venue_schedule(
        schedule,
        "binance",
        now_s=t,
        equity_usd=500.0,
        has_open_activity=False,
        fp_before=(0, 10),
        fp_after=(0, 10),
        base_interval_sec=20,
    )
    assert e_active["stage"] == 0
    assert e_active["mode"] == "active"
    assert e_active["interval_sec"] == 20

    # Idle venue (< 20 U, no open position, no delta) climbs the backoff ladder: 15m -> 30m -> 1h -> 2h -> 4h
    for idx, expected_iv in enumerate(IDLE_BACKOFF_LADDER_SEC, start=1):
        entry = update_venue_schedule(
            schedule,
            "mexc",
            now_s=t,
            equity_usd=3.5,
            has_open_activity=False,
            fp_before=(0, 2),
            fp_after=(0, 2),
            base_interval_sec=20,
        )
        assert entry["stage"] == idx
        assert entry["mode"] == "idle_backoff"
        assert entry["interval_sec"] == expected_iv
        assert should_sync_venue(schedule, "mexc", now_s=t + 60) is False
        assert should_sync_venue(schedule, "mexc", now_s=t + 60, force_run=True) is True
        t += expected_iv

    # Cap stays at 4h (14400s)
    capped = update_venue_schedule(
        schedule,
        "mexc",
        now_s=t,
        equity_usd=3.5,
        has_open_activity=False,
        fp_before=(0, 2),
        fp_after=(0, 2),
        base_interval_sec=20,
    )
    assert capped["interval_sec"] == 14400
    assert capped["stage"] == len(IDLE_BACKOFF_LADDER_SEC)

    # Small balance change (>= 0.05 U) wakes up immediately to 20s and enters 15m hot observation window
    woken = update_venue_schedule(
        schedule,
        "mexc",
        now_s=t + 100,
        equity_usd=5.0,
        has_open_activity=False,
        fp_before=(0, 2),
        fp_after=(0, 2),
        base_interval_sec=20,
    )
    assert woken["stage"] == 0
    assert woken["mode"] == "active"
    assert woken["interval_sec"] == 20
    assert woken["hot_until_sec"] == t + 100 + 900

    # Remains at 20s during the 15-minute hot observation window even without further change
    still_hot = update_venue_schedule(
        schedule,
        "mexc",
        now_s=t + 400,
        equity_usd=5.0,
        has_open_activity=False,
        fp_before=(0, 2),
        fp_after=(0, 2),
        base_interval_sec=20,
    )
    assert still_hot["stage"] == 0
    assert still_hot["interval_sec"] == 20


def test_okx_and_bybit_snapshots_degrade_gracefully_when_optional_endpoints_fail():
    from opentradeledger.adapters.bybit.client import BybitReadOnlyClient
    from opentradeledger.adapters.okx.client import OkxReadOnlyClient

    def okx_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v5/account/balance":
            return httpx.Response(200, json={"code": "0", "data": [{"totalEq": "1234.56", "details": []}]})
        # Simulate restricted read-only key lacking deposit/withdrawal scope
        return httpx.Response(200, json={"code": "50110", "msg": "Permission denied"})

    def bybit_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v5/account/wallet-balance":
            return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"totalEquity": "2500.00"}]}})
        return httpx.Response(200, json={"retCode": 10005, "retMsg": "Permission denied"})

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(okx_handler)) as c_okx:
            okx = OkxReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=c_okx)
            snap_okx = await okx.fetch_full_snapshot()
        async with httpx.AsyncClient(transport=httpx.MockTransport(bybit_handler)) as c_byb:
            byb = BybitReadOnlyClient(api_key="k", api_secret="s", client=c_byb)
            snap_byb = await byb.fetch_full_snapshot()
        return snap_okx, snap_byb

    snap_okx, snap_byb = asyncio.run(_run())
    assert snap_okx["balances"][0]["totalEq"] == "1234.56"
    assert len(snap_okx["warnings"]) >= 1
    assert snap_byb["wallet"]["list"][0]["totalEquity"] == "2500.00"
    assert len(snap_byb["warnings"]) >= 1


def test_legacy_venue_syncers_registered_for_all_five_legacy_exchanges():
    from opentradeledger.core.legacy_venue_sync import LEGACY_VENUE_SYNCERS

    assert set(LEGACY_VENUE_SYNCERS.keys()) == {"gate", "easicoin", "binance", "bitget", "mexc"}


def test_fx_rate_service_fetches_caches_and_falls_back_to_secondary(tmp_path: Path):
    from opentradeledger.core.fx_rates import FxRateService
    from opentradeledger.core.store import LedgerStore

    calls = {"primary": 0, "secondary": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if "open.er-api.com" in str(req.url):
            calls["primary"] += 1
            return httpx.Response(503)
        if "frankfurter.dev" in str(req.url):
            calls["secondary"] += 1
            return httpx.Response(
                200,
                json={"rates": {"CNY": 6.85, "GBP": 0.77, "EUR": 0.91, "HKD": 7.82, "JPY": 156.2}},
            )
        return httpx.Response(404)

    store = LedgerStore(tmp_path)
    svc = FxRateService(store, transport=httpx.MockTransport(handler))

    res1 = asyncio.run(svc.get_rates())
    assert res1["source"] == "frankfurter.dev"
    assert res1["rates"]["CNY"] == 6.85
    assert res1["rates"]["GBP"] == 0.77
    assert res1["stale"] is False
    assert calls["secondary"] == 1

    # Second call within TTL hits cache without calling network again
    res2 = asyncio.run(svc.get_rates())
    assert res2["rates"]["CNY"] == 6.85
    assert calls["secondary"] == 1


def test_ai_model_list_fetcher_and_sanitized_context_payload():
    from opentradeledger.core.ai_reviewer import build_ai_context_payload, list_ai_models
    from opentradeledger.core.demo_provider import build_demo_dashboard

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-4o-mini"}, {"id": "deepseek-chat"}, {"id": "claude-sonnet"}]},
        )

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await list_ai_models("https://api.example.com/v1/chat/completions", "test_key", client=c)

    models = asyncio.run(_run())
    assert models == ["claude-sonnet", "deepseek-chat", "gpt-4o-mini"]

    demo = build_demo_dashboard()
    ctx = build_ai_context_payload(demo)
    assert len(ctx["exchanges"]) == 6
    assert len(ctx["fee_matrix"]) == 6
    assert len(ctx["recent_20_trades"]) == 12
    assert ctx["recent_20_trades"][0]["entry_mode"]
    assert ctx["recent_20_trades"][0]["exit_mode"]
    raw_text = str(ctx).lower()
    assert "api_secret" not in raw_text and "passphrase" not in raw_text
