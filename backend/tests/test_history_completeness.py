"""Regression cases from large or sparse read-only account histories."""

import httpx
import pytest

from opentradeledger.adapters.binance.client import BinanceApiError, BinanceReadOnlyClient
from opentradeledger.adapters.bybit.client import BybitApiError, BybitReadOnlyClient
from opentradeledger.adapters.compat_creds import BinanceCredentials, EasiCoinCredentials
from opentradeledger.adapters.easicoin.client import EasiCoinReadOnlyClient

NOW = 1_790_000_000_000
DAY = 86_400_000


@pytest.mark.asyncio
async def test_bybit_sparse_closed_pnl_scans_past_empty_months():
    history = [NOW - days * DAY for days in (98, 220, 729)]

    def serve(request):
        params = request.url.params
        start = int(params.get("startTime", NOW - 7 * DAY))
        end = int(params.get("endTime", NOW))
        assert 0 <= end - start <= 7 * DAY
        assert start >= NOW - 730 * DAY
        rows = [
            {"symbol": "ETHUSDT", "orderId": str(ts), "updatedTime": str(ts)}
            for ts in history if start <= ts <= end
        ]
        return httpx.Response(200, json={"retCode": 0, "result": {"list": rows}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        client = BybitReadOnlyClient(api_key="k", api_secret="s", client=http, clock_ms=lambda: NOW)
        assert {int(r["updatedTime"]) for r in await client.get_closed_pnl()} == set(history)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["orderId", "id"])
async def test_binance_dense_incremental_window_keeps_going_while_cursor_advances(field):
    # Using a two-row page exercises 100 overlapping pages with just 102 records.
    times = list(range(NOW - 103, NOW - 1))

    def serve(request):
        params = request.url.params
        start, end = int(params["startTime"]), int(params["endTime"])
        rows = [
            {field: order_id, "time": timestamp}
            for order_id, timestamp in enumerate(times, 1) if start <= timestamp <= end
        ]
        return httpx.Response(200, json=rows[:int(params["limit"])])

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        client = BinanceReadOnlyClient(BinanceCredentials(api_key="k", api_secret="s"), client=http)
        records = {}
        await client._fetch_um_symbol_time_windows(
            "/fapi/v1/userTrades" if field == "id" else "/fapi/v1/allOrders",
            "BTCUSDT",
            start_ms=NOW - 200, end_ms=NOW, id_field=field,
            time_field="time", page_limit=2, dedup=records,
        )
        assert len(records) == len(times)


@pytest.mark.asyncio
@pytest.mark.parametrize("send_total", [True, False])
async def test_easicoin_continues_past_page_50_when_pages_advance(send_total):

    def serve(request):
        start = (int(request.url.params["page_num"]) - 1) * 100
        data = {"list": [
            {"transfer_id": str(i), "time": NOW // 1000 - i}
            for i in range(start, min(start + 100, 5001))
        ]}
        if send_total:
            data["total"] = 5001
        return httpx.Response(200, json={"code": 0, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        client = EasiCoinReadOnlyClient(EasiCoinCredentials(api_key="k", api_secret="s"), client=http, clock_ms=lambda: NOW)
        rows = await client.fetch_transfers(since_sec=NOW // 1000 - 86400, max_windows=1)
        assert len(rows) == 5001
        assert not client._transfer_warnings


@pytest.mark.asyncio
async def test_easicoin_does_not_silently_finish_on_a_repeated_final_page():
    def repeat_page(request):
        rows = [{"transfer_id": str(i), "time": NOW // 1000} for i in range(100)]
        return httpx.Response(200, json={"code": 0, "data": {"total": 200, "list": rows}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(repeat_page)) as http:
        client = EasiCoinReadOnlyClient(EasiCoinCredentials(api_key="k", api_secret="s"), client=http, clock_ms=lambda: NOW)
        rows = await client.fetch_transfers(since_sec=NOW // 1000 - 86400, max_windows=1)
        assert len(rows) == 100
        assert client._transfer_warnings


@pytest.mark.asyncio
async def test_closed_pnl_continues_a_dense_week_and_reports_a_stuck_cursor():
    stuck = False

    def serve(request):
        page = int(request.url.params.get("cursor", "0"))
        next_cursor = str(page + 1) if page < 3 else ""
        result = {
            "list": [{"symbol": "ETHUSDT", "orderId": str(page), "updatedTime": str(NOW)}],
            "nextPageCursor": "1" if stuck else next_cursor,
        }
        return httpx.Response(200, json={"retCode": 0, "result": result})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        client = BybitReadOnlyClient(api_key="k", api_secret="s", client=http, clock_ms=lambda: NOW, max_pages=2)
        assert len(await client.get_closed_pnl(since_ms=NOW - DAY)) == 4
        stuck = True
        with pytest.raises(BybitApiError, match="cursor"):
            await client.get_closed_pnl(since_ms=NOW - DAY)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_timestamp", [False, True])
async def test_binance_unavailable_continuation_fails_history_fetch(same_timestamp):
    first_request = True

    def serve(request):
        nonlocal first_request
        if request.url.path.endswith("/time"):
            return httpx.Response(200, json={"serverTime": NOW})
        if first_request:
            first_request = False
            rows = [
                {"id": 1, "time": NOW - 2},
                {"id": 2, "time": NOW - 2 if same_timestamp else NOW - 1},
            ]
            return httpx.Response(200, json=rows)
        return httpx.Response(503, json={"code": -1008, "msg": "Service unavailable"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        client = BinanceReadOnlyClient(BinanceCredentials(api_key="k", api_secret="s"), client=http)
        records = {}
        with pytest.raises(BinanceApiError, match="503"):
            await client._fetch_um_symbol_time_windows(
                "/fapi/v1/userTrades", "BTCUSDT",
                start_ms=NOW - 2, end_ms=NOW, id_field="id",
                time_field="time", page_limit=2, dedup=records,
            )
        assert set(records) == {"1", "2"}


@pytest.mark.asyncio
async def test_bybit_weekly_history_reuses_and_closes_its_transport(monkeypatch):
    owned_clients = []
    original_client = httpx.AsyncClient

    def create_client(**kwargs):
        def serve(request):
            return httpx.Response(200, json={"retCode": 0, "result": {"list": []}})

        http = original_client(transport=httpx.MockTransport(serve), **kwargs)
        owned_clients.append(http)
        return http

    monkeypatch.setattr(httpx, "AsyncClient", create_client)
    bybit = BybitReadOnlyClient(api_key="k", api_secret="s", max_windows=3)
    await bybit.get_closed_pnl()
    assert len(owned_clients) == 1
    await bybit.aclose()
    assert owned_clients[0].is_closed

    async with original_client() as shared:
        bybit = BybitReadOnlyClient(api_key="k", api_secret="s", client=shared)
        await bybit.aclose()
        assert not shared.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize(("request_delay_ms", "late_rejection"), [(230, False), (230, True), (10_000, False)])
async def test_bybit_moving_retention_and_partial_history_survive_sync(tmp_path, request_delay_ms, late_rejection):
    from opentradeledger.adapters.bybit.sync import sync_bybit_venue
    from opentradeledger.core.store import LedgerStore

    clock = NOW
    starts = []
    checked_last_pages = []
    inject = late_rejection
    records = {
        "recent": NOW - DAY,
        "middle": NOW - 300 * DAY,
        "boundary": NOW - 730 * DAY + 105 * request_delay_ms + 17 * 60_000,
    }
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("bybit", {"closed_pnl": [
        {"symbol": "ETHUSDT", "orderId": "archived", "updatedTime": str(NOW - 800 * DAY)},
    ]})

    def serve(request):
        nonlocal clock
        path = request.url.path
        params = request.url.params
        result = {"list": [], "rows": []}
        if path.endswith("/closed-pnl"):
            clock += request_delay_ms
            start = int(params.get("startTime", clock - 7 * DAY))
            end = int(params.get("endTime", clock))
            if not params.get("cursor"):
                starts.append(start)
            if start < clock - 730 * DAY:
                return httpx.Response(200, json={"retCode": 10001, "retMsg": "startTime is too old"})
            result["list"] = [
                {"symbol": "ETHUSDT", "orderId": key, "updatedTime": str(ts)}
                for key, ts in records.items() if start <= ts <= end
            ]
            if start < NOW - 729 * DAY:
                checked_last_pages.append(start)
                if params.get("cursor"):
                    if inject:
                        return httpx.Response(200, json={"retCode": 10001, "retMsg": "invalid cursor"})
                    result["list"] = [{"symbol": "ETHUSDT", "orderId": "last", "updatedTime": str(end)}]
                else:
                    result["nextPageCursor"] = "tail"
        elif path.endswith("/wallet-balance"):
            result = {"list": [{"totalEquity": "120", "coin": []}]}
        elif path.endswith("/query-account-coins-balance"):
            result = {"balance": []}
        return httpx.Response(200, json={"retCode": 0, "result": result})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        client = BybitReadOnlyClient(api_key="k", api_secret="s", client=http, clock_ms=lambda: clock)
        outcome = await sync_bybit_venue(client, store, now_s=clock // 1000)
        snap = store.venue_scope("bybit").get_kv("snapshot")
        expected = {"archived", *records}
        assert {r["orderId"] for r in snap["closed_pnl"]} == (expected if inject else expected | {"last"})
        assert len(starts) == 105 and len(checked_last_pages) == 2
        assert snap["_closed_pnl_backfilled_v2"] is (not inject)
        assert any("closed_pnl" in w for w in outcome.warnings) is inject
        if inject:
            inject = False
            await sync_bybit_venue(client, store, now_s=clock // 1000)
            recovered = store.venue_scope("bybit").get_kv("snapshot")
            assert recovered["_closed_pnl_backfilled_v2"]
            assert len(recovered["closed_pnl"]) == len(expected) + 1
            assert {r["orderId"] for r in recovered["closed_pnl"]} == expected | {"last"}
