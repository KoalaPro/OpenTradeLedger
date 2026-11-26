"""Full retest unit & actuarial regression suite for BingX, HTX, Hyperliquid, and KuCoin adapters."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from collections.abc import Callable, Awaitable
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from opentradeledger.adapters.bingx.accounting import (
    _is_completed_bingx_deposit,
    _is_completed_bingx_withdrawal,
)
from opentradeledger.adapters.bingx.client import BingxApiError, BingxReadOnlyClient
from opentradeledger.adapters.bingx.sync import extract_bingx_snapshot_activity, sync_bingx_venue
from opentradeledger.adapters.htx.client import HtxApiError, HtxReadOnlyClient
from opentradeledger.adapters.htx.sync import sync_htx_venue
from opentradeledger.adapters.hyperliquid.client import (
    HYPERLIQUID_ALLOWED_INFO_TYPES,
    HYPERLIQUID_ALLOWED_POST_TYPES,
    HyperliquidApiError,
    HyperliquidReadOnlyClient,
)
from opentradeledger.adapters.hyperliquid.sync import sync_hyperliquid_venue
from opentradeledger.adapters.kucoin.client import KucoinApiError, KucoinReadOnlyClient
from opentradeledger.adapters.kucoin.sync import sync_kucoin_venue
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


def test_bingx_v3_bare_list_strict_status_pagination_and_fatal_vs_permission_warnings(tmp_path: Path) -> None:
    """Verifies BingX v3 bare JSON arrays, status=6 filtering, 1y/500-fill/income/offset pagination, and error handling."""
    # 1. Deposit & withdrawal status helpers (strictly exclude deposit status=1; keep None/"" synthetic compat)
    assert _is_completed_bingx_deposit({"coin": "USDT", "amount": "100"}) is True
    assert _is_completed_bingx_deposit({"coin": "USDT", "amount": "100", "status": ""}) is True
    assert _is_completed_bingx_deposit({"coin": "USDT", "amount": "100", "status": 0}) is False
    assert _is_completed_bingx_deposit({"coin": "USDT", "amount": "100", "status": 1}) is False
    assert _is_completed_bingx_deposit({"coin": "USDT", "amount": "100", "status": "1"}) is False
    assert _is_completed_bingx_deposit({"coin": "USDT", "amount": "100", "status": 2}) is False
    assert _is_completed_bingx_deposit({"coin": "USDT", "amount": "100", "status": 6}) is True
    assert _is_completed_bingx_deposit({"coin": "USDT", "amount": "100", "status": "completed"}) is True

    assert _is_completed_bingx_withdrawal({"coin": "USDT", "amount": "50"}) is True
    assert _is_completed_bingx_withdrawal({"coin": "USDT", "amount": "50", "status": 4}) is False
    assert _is_completed_bingx_withdrawal({"coin": "USDT", "amount": "50", "status": 5}) is False
    assert _is_completed_bingx_withdrawal({"coin": "USDT", "amount": "50", "status": 6}) is True

    now_ms = 1_760_000_000_000
    day_ms = 86_400_000

    # Prepare 520 fills inside window #1 (sub-window pagination >500) + 2 fills 200 days ago (within 1-year 53-window lookback)
    recent_base_ts = now_ms - 2 * day_ms
    win1_fills = [
        {
            "tradeId": f"w1_{i}",
            "orderId": f"o1_{i}",
            "symbol": "BTC-USDT",
            "side": "BUY" if i % 2 == 0 else "SELL",
            "positionSide": "LONG",
            "price": "100" if i % 2 == 0 else "101",
            "qty": "0.1",
            "commission": "-0.01",
            "realizedPnl": "0" if i % 2 == 0 else "0.1",
            "role": "MAKER",
            "filledTm": recent_base_ts - i * 1000,
        }
        for i in range(520)
    ]
    old_200d_ts = now_ms - 200 * day_ms
    old_fills = [
        {
            "tradeId": "old_open",
            "orderId": "oo_1",
            "symbol": "ETH-USDT",
            "side": "BUY",
            "positionSide": "LONG",
            "price": "2000",
            "qty": "1",
            "commission": "-0.5",
            "realizedPnl": "0",
            "role": "MAKER",
            "filledTm": old_200d_ts,
        },
        {
            "tradeId": "old_close",
            "orderId": "oc_1",
            "symbol": "ETH-USDT",
            "side": "SELL",
            "positionSide": "LONG",
            "price": "2050",
            "qty": "1",
            "commission": "-0.5",
            "realizedPnl": "50",
            "role": "TAKER",
            "filledTm": old_200d_ts + 60_000,
        },
    ]
    all_fills = win1_fills + old_fills

    # 620 income records across >1 page of 500
    all_income = [
        {
            "tranId": f"inc_{i}",
            "symbol": "BTC-USDT",
            "incomeType": "FUNDING_FEE",
            "income": "0.01",
            "time": now_ms - 210 * day_ms + i * 60_000,
        }
        for i in range(620)
    ]

    # 230 v3 bare-array deposits across offset=0 (200) + offset=200 (30) plus status=1/0 uncredited rows & 150d-old deposit
    v3_deposits: list[dict[str, Any]] = [
        {
            "txId": f"dep_{i}",
            "coin": "USDT",
            "amount": "10.0",
            "status": 6,
            "insertTime": now_ms - 10 * day_ms - i * 1000,
        }
        for i in range(230)
    ]
    v3_deposits.append({"txId": "dep_not_credited", "coin": "USDT", "amount": "999.0", "status": 1, "insertTime": now_ms - day_ms})
    v3_deposits.append({"txId": "dep_pending", "coin": "USDT", "amount": "888.0", "status": 0, "insertTime": now_ms - day_ms})
    v3_deposits.append({"txId": "dep_150d_ago", "coin": "USDT", "amount": "500.0", "status": 6, "insertTime": now_ms - 150 * day_ms})

    # Bare-array v3 withdrawals where mock ignores offset (verifies dedup anti-infinite-loop!)
    v3_withdrawals_static_dup = [
        {
            "id": f"wd_{i}",
            "coin": "USDT",
            "amount": "1.0",
            "transactionFee": "0.0",
            "status": 6 if i == 0 else 4,  # only wd_0 (1.0 U) is completed!
            "applyTime": now_ms - 5 * day_ms,
        }
        for i in range(200)
    ]

    mode = {"spot_403": False, "rate_limit": False}
    wd_calls = {"count": 0}

    def bx_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        q = req.url.params
        if mode["rate_limit"] and p == "/openApi/swap/v2/user/positions":
            return httpx.Response(200, json={"code": 100410, "msg": "frequency limit exceeded"})
        if p == "/openApi/swap/v3/user/balance":
            return httpx.Response(200, json={"code": 0, "data": [{"asset": "USDT", "equity": "3000.0", "availableMargin": "3000.0"}]})
        if p == "/openApi/spot/v1/account/balance":
            if mode["spot_403"]:
                return httpx.Response(403, json={"code": 100413, "msg": "spot permission denied"})
            return httpx.Response(200, json={"code": 0, "data": {"balances": [{"asset": "USDT", "free": "50.0", "locked": "0"}]}})
        if p == "/openApi/wallets/v1/capital/deposit/hisrec":
            return httpx.Response(404, text="not found")
        if p == "/openApi/api/v3/capital/deposit/hisrec":
            s_t = int(q.get("startTime") or 0)
            e_t = int(q.get("endTime") or now_ms)
            off = int(q.get("offset") or 0)
            lim = int(q.get("limit") or 200)
            in_win = [r for r in v3_deposits if s_t <= int(r["insertTime"]) <= e_t]
            # Official v3 endpoint returns a BARE JSON LIST!
            return httpx.Response(200, json=in_win[off : off + lim])
        if p == "/openApi/wallets/v1/capital/withdraw/history":
            return httpx.Response(404, text="not found")
        if p == "/openApi/api/v3/capital/withdraw/history":
            wd_calls["count"] += 1
            return httpx.Response(200, json=v3_withdrawals_static_dup)
        if p == "/openApi/swap/v2/user/income":
            lim = int(q.get("limit") or 500)
            s_t = int(q.get("startTime") or 0)
            e_t = int(q.get("endTime") or (now_ms + day_ms))
            filtered = [r for r in sorted(all_income, key=lambda x: x["time"], reverse=True) if s_t <= int(r["time"]) <= e_t]
            return httpx.Response(200, json={"code": 0, "data": filtered[:lim]})
        if p == "/openApi/swap/v2/trade/allFillOrders":
            s_t = int(q["startTs"])
            e_t = int(q["endTs"])
            lim = int(q.get("limit") or 500)
            matched = [r for r in sorted(all_fills, key=lambda x: x["filledTm"], reverse=True) if s_t <= int(r["filledTm"]) <= e_t]
            return httpx.Response(200, json={"code": 0, "data": {"fill_orders": matched[:lim]}})
        return httpx.Response(200, json={"code": 0, "data": []})

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(bx_handler)) as hc:
            client = BingxReadOnlyClient(api_key="k", api_secret="s", client=hc, clock_ms=lambda: now_ms)
            outcome1 = await sync_bingx_venue(client, store, now_s=now_ms // 1000, force_full=True)
            assert outcome1.equity_usd == 3050.0
            assert wd_calls["count"] <= 3  # Static duplicate mock broken cleanly without infinite loop!

            snap1 = store.venue_scope("bingx").get_kv("snapshot")
            assert len(snap1["fills"]) == 522  # 520 sub-paginated + 2 from 200 days ago!
            assert len(snap1["income"]) == 620
            assert len(snap1["deposits"]) == 233

            # Flip spot to 403 permission denied -> cached 50 U spot preserved, warning surfaced in Outcome!
            mode["spot_403"] = True
            outcome2 = await sync_bingx_venue(client, store, now_s=now_ms // 1000 + 60, force_full=False)
            assert any(w.startswith("spot_balances:") for w in outcome2.warnings)
            assert outcome2.equity_usd == 3050.0

            # Fatal business rate-limit code=100410 must raise, NEVER be swallowed!
            mode["rate_limit"] = True
            with pytest.raises(BingxApiError):
                await sync_bingx_venue(client, store, now_s=now_ms // 1000 + 120, force_full=False)

    asyncio.run(_run())

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    bx_card = next(e for e in dash["exchanges"] if e["venue_id"] == "bingx")
    # Principal: 230 * 10 + 500 (status=6) - 1.0 (status=6 withdrawal) = 2799.00 U (status=1 999U & status=0 888U & status=4 199U excluded!)
    assert bx_card["net_principal_usd"] == 2799.00
    assert bx_card["equity_usd"] == 3050.00
    assert bx_card["trade_count"] == 261  # 260 BTC pairs + 1 ETH pair from 200 days ago
    assert dash["reconciliation"]["residual_usd"] == 0.0


def test_htx_from_id_pagination_48h_chunking_point_exclusion_and_withdraw_states(tmp_path: Path) -> None:
    """Verifies HTX multi-page from_id loops, >48h incremental chunking, POINT exclusion, and safe/completed withdrawals."""
    now_ms = 1_760_000_000_000
    hour_ms = 3600 * 1000

    # 120 match rows (60 open/close pairs -> 60 closed trades): 8 pairs 100h ago (>48h) + 52 pairs (104 rows > 2 pages) in last 40h
    matches_db: list[dict[str, Any]] = []
    for i in range(60):
        base_t = (now_ms - (100 * hour_ms) + i * (30 * 60_000)) if i < 8 else (now_ms - (40 * hour_ms) + (i - 8) * (30 * 60_000))
        matches_db.append(
            {
                "id": 1000 + i * 2,
                "match_id": 5000 + i * 2,
                "contract_code": "BTC-USDT",
                "direction": "buy",
                "offset": "open",
                "trade_price": "90000",
                "trade_turnover": "900",
                "trade_volume": "1",
                "trade_fee": "-0.1",
                "real_profit": "0",
                "role": "maker",
                "create_date": base_t,
            }
        )
        matches_db.append(
            {
                "id": 1000 + i * 2 + 1,
                "match_id": 5000 + i * 2 + 1,
                "contract_code": "BTC-USDT",
                "direction": "sell",
                "offset": "close",
                "trade_price": "90100",
                "trade_turnover": "901",
                "trade_volume": "1",
                "trade_fee": "-0.1",
                "real_profit": "1.0",
                "role": "taker",
                "create_date": base_t + 60_000,
            }
        )

    fin_db = [
        {
            "id": 3000 + i,
            "type": 30,
            "amount": "0.05",
            "contract_code": "BTC-USDT",
            "ts": now_ms - (95 * hour_ms) + i * (45 * 60_000),
        }
        for i in range(115)
    ]

    dep_db = [
        {
            "id": 8000 + i,
            "currency": "usdt",
            "amount": "10.0",
            "state": "safe",
            "created-at": now_ms - i * 60_000,
        }
        for i in range(245)
    ]
    wd_db = [
        {"id": 9001, "currency": "usdt", "amount": "100.0", "fee": "1.0", "state": "safe", "created-at": now_ms - 30_000},
        {"id": 9002, "currency": "usdt", "amount": "49.0", "fee": "0.0", "state": "completed", "created-at": now_ms - 20_000},
        {"id": 9003, "currency": "usdt", "amount": "500.0", "fee": "1.0", "state": "submitted", "created-at": now_ms - 10_000},
    ]

    def htx_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        q = req.url.params
        if p == "/linear-swap-api/v1/swap_cross_account_info":
            return httpx.Response(200, json={"status": "ok", "data": [{"margin_balance": "2300.0", "margin_available": "2300.0"}]})
        if p == "/v1/account/accounts":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "data": [
                        {"id": 11, "type": "spot"},
                        {"id": 12, "type": "point"},
                        {"id": 13, "type": "super-margin"},
                    ],
                },
            )
        if p == "/v1/account/accounts/11/balance":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "data": {
                        "list": [
                            {"currency": "usdt", "type": "trade", "balance": "80.0"},
                            {"currency": "usdt", "type": "frozen", "balance": "20.0"},
                            {"currency": "usdt", "type": "transfer-out-available", "balance": "80.0"},
                        ]
                    },
                },
            )
        if p == "/v1/account/accounts/12/balance":
            # POINT account must NEVER be valued as USD!
            return httpx.Response(200, json={"status": "ok", "data": {"list": [{"currency": "usdt", "type": "trade", "balance": "9999.0"}]}})
        if p == "/v1/account/accounts/13/balance":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "data": {
                        "list": [
                            {"currency": "usdt", "type": "trade", "balance": "50.0"},
                            {"currency": "usdt", "type": "loan", "balance": "300.0"},
                        ]
                    },
                },
            )
        if p == "/linear-swap-api/v1/swap_cross_matchresults_exact":
            body = json.loads(req.content.decode("utf-8"))
            s_t = body.get("start_time")
            e_t = body.get("end_time")
            # Enforce strict HTX <= 48h rule whenever start_time is supplied!
            if s_t is not None:
                eff_end = int(e_t) if e_t is not None else now_ms
                if eff_end - int(s_t) > 48 * hour_ms:
                    return httpx.Response(200, json={"status": "error", "err_code": 1056, "err_msg": "query window exceeds 48h"})
            rows = sorted(matches_db, key=lambda r: int(r["id"]), reverse=True)
            if s_t is not None:
                rows = [r for r in rows if int(r["create_date"]) >= int(s_t)]
            if e_t is not None:
                rows = [r for r in rows if int(r["create_date"]) <= int(e_t)]
            if body.get("from_id") is not None:
                rows = [r for r in rows if int(r["id"]) < int(body["from_id"])]
            return httpx.Response(200, json={"status": "ok", "data": {"trades": rows[: int(body.get("size") or 50)]}})
        if p == "/linear-swap-api/v1/swap_financial_record_exact":
            body = json.loads(req.content.decode("utf-8"))
            s_t = body.get("start_time")
            e_t = body.get("end_time")
            if s_t is not None:
                eff_end = int(e_t) if e_t is not None else now_ms
                if eff_end - int(s_t) > 48 * hour_ms:
                    return httpx.Response(200, json={"status": "error", "err_code": 1056, "err_msg": "query window exceeds 48h"})
            rows = sorted(fin_db, key=lambda r: int(r["id"]), reverse=True)
            if s_t is not None:
                rows = [r for r in rows if int(r["ts"]) >= int(s_t)]
            if e_t is not None:
                rows = [r for r in rows if int(r["ts"]) <= int(e_t)]
            if body.get("from_id") is not None:
                rows = [r for r in rows if int(r["id"]) < int(body["from_id"])]
            return httpx.Response(200, json={"status": "ok", "data": {"financial_record": rows[: int(body.get("size") or 50)]}})
        if p == "/v1/query/deposit-withdraw":
            tp = q.get("type")
            src = dep_db if tp == "deposit" else wd_db
            ordered = sorted(src, key=lambda r: int(r["id"]), reverse=True)
            if q.get("from") is not None:
                ordered = [r for r in ordered if int(r["id"]) < int(q["from"])]
            return httpx.Response(200, json={"status": "ok", "data": ordered[: int(q.get("size") or 200)]})
        return httpx.Response(200, json={"status": "ok", "data": []})

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    # Seed an old snapshot whose _last_sync_ms was 90 hours ago to trigger >48h incremental sync!
    store.venue_scope("htx").set_kv("snapshot", {"venue_id": "htx", "_last_sync_ms": now_ms - 90 * hour_ms})

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(htx_handler)) as hc:
            client = HtxReadOnlyClient(api_key="k", api_secret="s", client=hc, clock_ms=lambda: now_ms)
            # Incremental sync spans 90h + 36h overlap = 126h -> automatically sliced into 47h chunks without err=1056!
            outcome = await sync_htx_venue(client, store, now_s=now_ms // 1000, force_full=False)
            # Equity: 2300 swap + 100 spot (80 trade + 20 frozen) + 50 super-margin trade = 2450.0 (POINT 9999 & loan 300 excluded!)
            assert outcome.equity_usd == 2450.0

            snap = store.venue_scope("htx").get_kv("snapshot")
            assert len(snap["matches"]) == 120
            assert len(snap["financials"]) == 115
            assert len(snap["deposits"]) == 245

            # Also verify truncation warning when max_pages is capped
            trunc_warns: list[str] = []
            await client.get_swap_matches(since_ms=0, max_pages=2, warnings_out=trunc_warns)
            assert any("pagination cap" in w for w in trunc_warns)

    asyncio.run(_run())

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    htx_card = next(e for e in dash["exchanges"] if e["venue_id"] == "htx")
    assert htx_card["equity_usd"] == 2450.00
    # Principal: 245 * 10 = 2450 deposits - (100+1 safe + 49 completed) = 2300.00 U (submitted 500 U ignored!)
    assert htx_card["net_principal_usd"] == 2300.00
    assert htx_card["trade_count"] == 60
    assert dash["reconciliation"]["residual_usd"] == 0.0


def test_hyperliquid_backward_and_same_ms_fills_funding_ledger_pagination_and_vault_equity(tmp_path: Path) -> None:
    """Verifies Hyperliquid >2,000 backward fill paging, same-ms incremental safety, >500 funding/ledger paging, and HLP/Vaults."""
    assert "userVaultEquities" in HYPERLIQUID_ALLOWED_INFO_TYPES
    assert "userVaultEquities" in HYPERLIQUID_ALLOWED_POST_TYPES

    wallet = "0x" + "c" * 40
    base_ts = 1_700_000_000_000  # >1 year ago to prove removal of 365-day funding hard cutoff!

    # 2,500 fills total: userFills returns latest 2,000 (500..2499), userFillsByTime fetches older 500 (0..499)
    full_fills = [
        {
            "tid": i + 1,
            "oid": i + 1,
            "coin": "ETH",
            "px": "3000" if i % 2 == 0 else "3002",
            "sz": "0.1",
            "side": "B" if i % 2 == 0 else "A",
            "dir": "Open Long" if i % 2 == 0 else "Close Long",
            "closedPnl": "0" if i % 2 == 0 else "0.2",
            "fee": "0.01",
            "crossed": True,
            "time": base_ts + i * 1000,
        }
        for i in range(2500)
    ]

    # 620 funding rows (>500 single-page cap) starting >1 year ago
    funding_db = [
        {
            "hash": f"0xfund_{i}",
            "time": base_ts + i * 3600_000,
            "delta": {"coin": "ETH", "usdc": "0.05"},
        }
        for i in range(620)
    ]

    # 550 ledger updates (>500 single-page cap) + internal vaultDeposit/vaultWithdraw that must NOT alter external principal!
    ledger_db = [
        {
            "hash": f"0xdep_{i}",
            "time": base_ts + i * 1000,
            "delta": {"type": "deposit", "usdc": "10.0"},
        }
        for i in range(550)
    ]
    ledger_db.append({"hash": "0xvd", "time": base_ts + 900_000, "delta": {"type": "vaultDeposit", "vault": "0x" + "9" * 40, "usdc": "2000.0"}})
    ledger_db.append({"hash": "0xvw", "time": base_ts + 950_000, "delta": {"type": "vaultWithdraw", "vault": "0x" + "9" * 40, "usdc": "500.0"}})

    def hl_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        tp = body.get("type")
        if tp == "clearinghouseState":
            return httpx.Response(200, json={"marginSummary": {"accountValue": "4000.0", "withdrawable": "4000.0"}, "assetPositions": []})
        if tp == "spotClearinghouseState":
            return httpx.Response(200, json={"balances": [{"coin": "USDC", "token": 0, "total": "200.0"}]})
        if tp == "userVaultEquities":
            return httpx.Response(200, json=[{"vaultAddress": "0x" + "9" * 40, "equity": "1650.50"}])
        if tp == "allMids":
            return httpx.Response(200, json={"ETH": "3000.0"})
        if tp == "spotMeta":
            return httpx.Response(200, json={"tokens": [{"name": "USDC", "index": 0}], "universe": []})
        if tp == "userFills":
            return httpx.Response(200, json=list(reversed(full_fills[-2000:])))
        if tp == "userFillsByTime":
            s_t = int(body.get("startTime") or 0)
            e_t = body.get("endTime")
            rows = [r for r in full_fills if int(r["time"]) >= s_t and (e_t is None or int(r["time"]) <= int(e_t))]
            return httpx.Response(200, json=rows[:2000])
        if tp == "userFunding":
            s_t = int(body.get("startTime") or 0)
            rows = [r for r in funding_db if int(r["time"]) >= s_t]
            return httpx.Response(200, json=rows[:500])
        if tp == "userNonFundingLedgerUpdates":
            s_t = int(body.get("startTime") or 0)
            rows = [r for r in ledger_db if int(r["time"]) >= s_t]
            return httpx.Response(200, json=rows[:500])
        return httpx.Response(200, json=[])

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(hl_handler)) as hc:
            client = HyperliquidReadOnlyClient(wallet_address=wallet, client=hc)
            outcome = await sync_hyperliquid_venue(client, store, now_s=int(time.time()), force_full=True)
            # 4000 perp + 200 spot + 1650.50 vault = 5850.50!
            assert outcome.equity_usd == 5850.50
            # All 2,500 fills fetched completely -> no false 2,000 truncation warning!
            assert not any("2,000" in w for w in outcome.warnings)

            snap = store.venue_scope("hyperliquid").get_kv("snapshot")
            assert len(snap["fills"]) == 2500
            assert len(snap["funding_history"]) == 620
            assert len(snap["ledger_updates"]) == 552

        # Verify incremental same-millisecond boundary does NOT drop fill #2001 sharing timestamp with fill #2000!
        same_ms_ts = 1_760_000_050_000
        inc_fills = [
            {
                "tid": 90000 + i,
                "oid": 90000 + i,
                "coin": "BTC",
                "px": "90000",
                "sz": "0.01",
                "side": "B",
                "dir": "Open Long",
                "closedPnl": "0",
                "fee": "0.01",
                "time": (1_760_000_000_000 + i * 10) if i < 1999 else same_ms_ts,
            }
            for i in range(2005)
        ]

        def same_ms_handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode("utf-8"))
            s_t = int(body.get("startTime") or 0)
            rows = [r for r in inc_fills if int(r["time"]) >= s_t]
            return httpx.Response(200, json=rows[:2000])

        async with httpx.AsyncClient(transport=httpx.MockTransport(same_ms_handler)) as hc2:
            c2 = HyperliquidReadOnlyClient(wallet_address=wallet, client=hc2)
            got_inc = await c2.get_user_fills(since_ms=1_760_000_000_000)
            assert len(got_inc) == 2005

    asyncio.run(_run())

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    hl_card = next(e for e in dash["exchanges"] if e["venue_id"] == "hyperliquid")
    assert hl_card["equity_usd"] == 5850.50
    # External principal = 550 * 10.0 = 5500.00 U (vaultDeposit 2000 & vaultWithdraw 500 ignored!)
    assert hl_card["net_principal_usd"] == 5500.00
    assert hl_card["net_pnl_usd"] == 350.50
    assert hl_card["trade_count"] == 1250
    assert dash["reconciliation"]["residual_usd"] == 0.0


def test_kucoin_http_401_v3_key_negotiation_and_multipage_fills_tx_cashflows_params(tmp_path: Path) -> None:
    """Verifies KuCoin HTTP 401 code=400009 key-version switch, multi-page fills/tx/deposits/withdrawals, and clean history-positions params."""
    now_ms = 1_760_000_000_000

    fills_db = [
        {
            "tradeId": f"kf_{i}",
            "symbol": "XBTUSDTM",
            "side": "buy" if i % 2 == 0 else "sell",
            "fee": "0.02",
            "liquidity": "maker" if i % 2 == 0 else "taker",
            "createdAt": now_ms - 500_000 + i * 100,
        }
        for i in range(140)
    ]
    tx_db = [
        {
            "offset": 5000 - i,
            "time": now_ms - i * 1000,
            "type": "Rebate",
            "amount": 0.01,
            "currency": "USDT",
        }
        for i in range(135)
    ]
    dep_db = [
        {
            "walletTxId": f"kdep_{i}",
            "currency": "USDT",
            "amount": "10.0",
            "status": "SUCCESS",
            "createdAt": now_ms - i * 1000,
        }
        for i in range(125)
    ]
    wd_db = [
        {
            "id": f"kwd_{i}",
            "currency": "USDT",
            "amount": "1.0",
            "fee": "0.0",
            "status": "SUCCESS",
            "createdAt": now_ms - i * 1000,
        }
        for i in range(110)
    ]

    seen_hist_params: list[dict[str, str]] = []

    def kc_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        q = dict(req.url.params)
        # Real KuCoin returns HTTP 401 with {"code": "400009"} when v3 key is called with KC-API-KEY-VERSION=2!
        if p != "/api/v1/market/allTickers" and req.headers.get("KC-API-KEY-VERSION") != "3":
            return httpx.Response(401, json={"code": "400009", "msg": "KC-API-KEY-VERSION error"})

        if p == "/api/v1/accounts":
            return httpx.Response(200, json={"code": "200000", "data": [{"type": "main", "currency": "USDT", "balance": "140.0"}]})
        if p == "/api/v1/account-overview":
            return httpx.Response(200, json={"code": "200000", "data": {"accountEquity": 1050.0, "availableBalance": 1050.0}})
        if p == "/api/v1/history-positions":
            seen_hist_params.append(q)
            # Must NOT pass redundant currentPage or pageSize!
            assert "currentPage" not in q and "pageSize" not in q
            assert q.get("limit") == "250"
            pg = int(q.get("pageId") or 1)
            if pg == 1:
                return httpx.Response(
                    200,
                    json={
                        "code": "200000",
                        "data": {
                            "items": [
                                {
                                    "closeId": "kp_main",
                                    "symbol": "XBTUSDTM",
                                    "side": "long",
                                    "closeSize": 10,
                                    "multiplier": 0.001,
                                    "openPrice": 90000,
                                    "closePrice": 95000,
                                    "openTime": now_ms - 600_000,
                                    "closeTime": now_ms - 100_000,
                                    "tradeFee": 2.8,
                                    "fundingFee": 0.0,
                                    "pnl": 47.2,
                                }
                            ],
                            "hasMore": False,
                        },
                    },
                )
            return httpx.Response(200, json={"code": "200000", "data": {"items": [], "hasMore": False}})
        if p == "/api/v1/fills":
            pg = int(q.get("currentPage") or 1)
            chunk = fills_db[(pg - 1) * 100 : pg * 100]
            return httpx.Response(200, json={"code": "200000", "data": {"items": chunk, "totalPage": 2}})
        if p == "/api/v1/transaction-history":
            off = q.get("offset")
            rows = [r for r in tx_db if int(r["offset"]) < int(off)] if off is not None else tx_db
            chunk = rows[:100]
            has_more = len(rows) > 100
            return httpx.Response(200, json={"code": "200000", "data": {"dataList": chunk, "hasMore": has_more}})
        if p == "/api/v1/deposits":
            pg = int(q.get("currentPage") or 1)
            chunk = dep_db[(pg - 1) * 100 : pg * 100]
            return httpx.Response(200, json={"code": "200000", "data": {"items": chunk, "totalPage": 2}})
        if p == "/api/v1/withdrawals":
            pg = int(q.get("currentPage") or 1)
            chunk = wd_db[(pg - 1) * 100 : pg * 100]
            return httpx.Response(200, json={"code": "200000", "data": {"items": chunk, "totalPage": 2}})
        return httpx.Response(200, json={"code": "200000", "data": {"items": [], "dataList": [], "ticker": []}})

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(kc_handler)) as hc:
            client = KucoinReadOnlyClient(
                api_key="k",
                api_secret="s",
                passphrase="p",
                key_version="2",
                client=hc,
                clock_ms=lambda: now_ms,
            )
            outcome = await sync_kucoin_venue(client, store, now_s=now_ms // 1000, force_full=True)
            assert client.key_version == "3"
            assert outcome.equity_usd == 1190.0
            assert 1 <= len(seen_hist_params) <= 2  # Static mock ignoring from/to broken cleanly on window #2!
            for hp in seen_hist_params:
                assert set(hp.keys()) == {"from", "to", "pageId", "limit"}
                assert "currentPage" not in hp and "pageSize" not in hp
                assert hp["pageId"] == "1" and hp["limit"] == "250"
                assert 0 < int(hp["to"]) - int(hp["from"]) <= 7 * 86_400_000

            snap = store.venue_scope("kucoin").get_kv("snapshot")
            assert len(snap["fills"]) == 140
            assert len(snap["tx_history"]) == 135
            assert len(snap["deposits"]) == 125
            assert len(snap["withdrawals"]) == 110

            # Also verify non-400009 HTTP 401 still raises KucoinApiError
            def unauth_handler(_req: httpx.Request) -> httpx.Response:
                return httpx.Response(401, json={"code": "400003", "msg": "KC-API-KEY not exists"})

            async with httpx.AsyncClient(transport=httpx.MockTransport(unauth_handler)) as hc_bad:
                bad_client = KucoinReadOnlyClient(api_key="bad", api_secret="s", passphrase="p", client=hc_bad)
                with pytest.raises(KucoinApiError):
                    await bad_client.get_spot_accounts()

    asyncio.run(_run())

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    kc_card = next(e for e in dash["exchanges"] if e["venue_id"] == "kucoin")
    assert kc_card["equity_usd"] == 1190.00
    # Principal: 125 * 10 - 110 * 1 = 1140.00 U
    assert kc_card["net_principal_usd"] == 1140.00
    assert kc_card["net_pnl_usd"] == 50.00
    assert kc_card["trade_count"] == 1
    assert round(dash["trades"][0]["total_rebate_usd"], 2) == 1.35  # 135 * 0.01 from multi-page tx_history!
    assert dash["reconciliation"]["residual_usd"] == 0.0


def test_four_venues_malformed_http200_balance_payloads_rejected_and_never_shrink_known_equity(tmp_path: Path) -> None:
    """HTTP 200 with malformed balance schemas (e.g. {'unexpected': 'invalid balance record'}) must raise ApiError,
    block initial-bind snapshot poisoning, and NEVER shrink cached known equity on subsequent syncs."""
    now_ms = 1_760_000_000_000
    hl_wallet = "0x" + "d" * 40
    bad_obj = {"unexpected": "invalid balance record"}

    state = {
        "bx_swap_bad": False,
        "bx_spot_bad": False,
        "htx_swap_bad": False,
        "htx_spot_bad": False,
        "hl_ch_bad": False,
        "hl_spot_bad": False,
        "hl_vault_bad": False,
        "kc_spot_bad": False,
        "kc_fut_bad": False,
    }

    def bx_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p in {"/openApi/swap/v3/user/balance", "/openApi/swap/v2/user/balance"}:
            if state["bx_swap_bad"]:
                return httpx.Response(200, json={"code": 0, "data": bad_obj})
            return httpx.Response(200, json={"code": 0, "data": [{"asset": "USDT", "equity": "100.0", "availableMargin": "100.0"}]})
        if p == "/openApi/spot/v1/account/balance":
            if state["bx_spot_bad"]:
                return httpx.Response(200, json={"code": 0, "data": bad_obj})
            return httpx.Response(200, json={"code": 0, "data": {"balances": [{"asset": "USDT", "free": "25.0", "locked": "0"}]}})
        if p == "/openApi/wallets/v1/capital/deposit/hisrec":
            return httpx.Response(200, json={"code": 0, "data": [{"id": "bd1", "coin": "USDT", "amount": "125.0", "status": 6, "insertTime": now_ms}]})
        return httpx.Response(200, json={"code": 0, "data": []})

    def htx_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/linear-swap-api/v1/swap_cross_account_info":
            if state["htx_swap_bad"]:
                return httpx.Response(200, json={"status": "ok", "data": bad_obj})
            return httpx.Response(200, json={"status": "ok", "data": [{"margin_balance": "100.0", "margin_available": "100.0"}]})
        if p == "/v1/account/accounts":
            if state["htx_spot_bad"]:
                return httpx.Response(200, json={"status": "ok", "data": bad_obj})
            return httpx.Response(200, json={"status": "ok", "data": [{"id": 9, "type": "spot"}]})
        if p == "/v1/account/accounts/9/balance":
            return httpx.Response(200, json={"status": "ok", "data": {"list": [{"currency": "usdt", "type": "trade", "balance": "30.0"}]}})
        if p == "/v1/query/deposit-withdraw":
            if req.url.params.get("type") == "deposit":
                return httpx.Response(200, json={"status": "ok", "data": [{"id": 1, "currency": "usdt", "amount": "130.0", "state": "safe", "created-at": now_ms}]})
            return httpx.Response(200, json={"status": "ok", "data": []})
        return httpx.Response(200, json={"status": "ok", "data": []})

    def hl_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        tp = body.get("type")
        if tp == "clearinghouseState":
            if state["hl_ch_bad"]:
                return httpx.Response(200, json=bad_obj)
            return httpx.Response(200, json={"marginSummary": {"accountValue": "100.0", "withdrawable": "100.0"}, "assetPositions": []})
        if tp == "spotClearinghouseState":
            if state["hl_spot_bad"]:
                return httpx.Response(200, json=bad_obj)
            return httpx.Response(200, json={"balances": [{"coin": "USDC", "token": 0, "total": "35.0"}]})
        if tp == "userVaultEquities":
            if state["hl_vault_bad"]:
                return httpx.Response(200, json=bad_obj)
            return httpx.Response(200, json=[{"vaultAddress": "0x" + "8" * 40, "equity": "15.0"}])
        if tp == "allMids":
            return httpx.Response(200, json={"BTC": "95000.0"})
        if tp == "spotMeta":
            return httpx.Response(200, json={"tokens": [{"name": "USDC", "index": 0}], "universe": []})
        if tp == "userNonFundingLedgerUpdates":
            return httpx.Response(200, json=[{"hash": "0xdep1", "time": now_ms, "delta": {"type": "deposit", "usdc": "150.0"}}])
        return httpx.Response(200, json=[])

    def kc_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/v1/accounts":
            if state["kc_spot_bad"]:
                return httpx.Response(200, json={"code": "200000", "data": bad_obj})
            return httpx.Response(200, json={"code": "200000", "data": [{"type": "main", "currency": "USDT", "balance": "40.0"}]})
        if p == "/api/v1/account-overview":
            if state["kc_fut_bad"]:
                return httpx.Response(200, json={"code": "200000", "data": bad_obj})
            return httpx.Response(200, json={"code": "200000", "data": {"accountEquity": 100.0, "availableBalance": 100.0}})
        if p == "/api/v1/deposits":
            return httpx.Response(200, json={"code": "200000", "data": {"items": [{"walletTxId": "kd1", "currency": "USDT", "amount": "140.0", "createdAt": now_ms}]}})
        return httpx.Response(200, json={"code": "200000", "data": {"items": [], "dataList": [], "ticker": []}})

    init_store = LedgerStore(tmp_path / "init_reject")
    good_store = LedgerStore(tmp_path / "known_assets")
    vault = CredentialVault(tmp_path / "known_assets")
    reg = build_default_registry()

    async def _run() -> None:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(bx_handler)) as bx_hc,
            httpx.AsyncClient(transport=httpx.MockTransport(htx_handler)) as htx_hc,
            httpx.AsyncClient(transport=httpx.MockTransport(hl_handler)) as hl_hc,
            httpx.AsyncClient(transport=httpx.MockTransport(kc_handler)) as kc_hc,
        ):
            bx = BingxReadOnlyClient(api_key="k", api_secret="s", client=bx_hc, clock_ms=lambda: now_ms)
            htx = HtxReadOnlyClient(api_key="k", api_secret="s", client=htx_hc, clock_ms=lambda: now_ms)
            hl = HyperliquidReadOnlyClient(wallet_address=hl_wallet, client=hl_hc, clock_ms=lambda: now_ms)
            kc = KucoinReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=kc_hc, clock_ms=lambda: now_ms)

            # 1) Direct client calls & initial sync (`not existing_snap`) MUST reject HTTP 200 malformed schemas!
            state["bx_spot_bad"] = True
            with pytest.raises(BingxApiError, match="Malformed"):
                await bx.get_spot_balances()
            with pytest.raises(BingxApiError, match="Malformed"):
                await sync_bingx_venue(bx, init_store, now_s=now_ms // 1000, force_full=True)
            assert init_store.venue_scope("bingx").get_kv("snapshot") is None
            state["bx_spot_bad"] = False

            state["bx_swap_bad"] = True
            with pytest.raises(BingxApiError, match="Malformed"):
                await bx.get_swap_balance()
            state["bx_swap_bad"] = False

            state["htx_spot_bad"] = True
            with pytest.raises(HtxApiError, match="Malformed"):
                await htx.get_spot_balances()
            with pytest.raises(HtxApiError, match="Malformed"):
                await sync_htx_venue(htx, init_store, now_s=now_ms // 1000, force_full=True)
            assert init_store.venue_scope("htx").get_kv("snapshot") is None
            state["htx_spot_bad"] = False

            state["htx_swap_bad"] = True
            with pytest.raises(HtxApiError, match="Malformed"):
                await htx.get_swap_accounts()
            state["htx_swap_bad"] = False

            state["hl_spot_bad"] = True
            with pytest.raises(HyperliquidApiError, match="Malformed"):
                await hl.get_spot_state()
            with pytest.raises(HyperliquidApiError, match="Malformed"):
                await sync_hyperliquid_venue(hl, init_store, now_s=now_ms // 1000, force_full=True)
            assert init_store.venue_scope("hyperliquid").get_kv("snapshot") is None
            state["hl_spot_bad"] = False

            state["hl_ch_bad"] = True
            with pytest.raises(HyperliquidApiError, match="Malformed"):
                await hl.get_clearinghouse_state()
            state["hl_ch_bad"] = False

            state["hl_vault_bad"] = True
            with pytest.raises(HyperliquidApiError, match="Malformed"):
                await hl.get_vault_equities()
            with pytest.raises(HyperliquidApiError, match="Malformed"):
                await sync_hyperliquid_venue(hl, init_store, now_s=now_ms // 1000, force_full=True)
            state["hl_vault_bad"] = False

            state["kc_spot_bad"] = True
            with pytest.raises(KucoinApiError, match="Malformed"):
                await kc.get_spot_accounts()
            with pytest.raises(KucoinApiError, match="Malformed"):
                await sync_kucoin_venue(kc, init_store, now_s=now_ms // 1000, force_full=True)
            assert init_store.venue_scope("kucoin").get_kv("snapshot") is None
            state["kc_spot_bad"] = False

            state["kc_fut_bad"] = True
            with pytest.raises(KucoinApiError, match="Malformed"):
                await kc.get_futures_overview()
            state["kc_fut_bad"] = False

            # 2) Seed valid known assets across all 4 venues: 125 + 130 + 150 + 140 = 545.00 U
            assert (await sync_bingx_venue(bx, good_store, now_s=now_ms // 1000, force_full=True)).equity_usd == 125.0
            assert (await sync_htx_venue(htx, good_store, now_s=now_ms // 1000, force_full=True)).equity_usd == 130.0
            assert (await sync_hyperliquid_venue(hl, good_store, now_s=now_ms // 1000, force_full=True)).equity_usd == 150.0
            assert (await sync_kucoin_venue(kc, good_store, now_s=now_ms // 1000, force_full=True)).equity_usd == 140.0

            # 3) Flip sub-wallets to HTTP 200 malformed payloads -> cached sub-wallet balances MUST be preserved!
            state["bx_spot_bad"] = True
            state["htx_spot_bad"] = True
            state["hl_spot_bad"] = True
            state["hl_vault_bad"] = True
            state["kc_spot_bad"] = True

            o_bx = await sync_bingx_venue(bx, good_store, now_s=now_ms // 1000 + 60, force_full=False)
            o_htx = await sync_htx_venue(htx, good_store, now_s=now_ms // 1000 + 60, force_full=False)
            o_hl = await sync_hyperliquid_venue(hl, good_store, now_s=now_ms // 1000 + 60, force_full=False)
            o_kc = await sync_kucoin_venue(kc, good_store, now_s=now_ms // 1000 + 60, force_full=False)

            assert o_bx.equity_usd == 125.0 and any("Malformed" in w for w in o_bx.warnings)
            assert o_htx.equity_usd == 130.0 and any("Malformed" in w for w in o_htx.warnings)
            assert o_hl.equity_usd == 150.0 and any("Malformed" in w for w in o_hl.warnings)
            assert o_kc.equity_usd == 140.0 and any("Malformed" in w for w in o_kc.warnings)

    asyncio.run(_run())

    svc = LedgerService(good_store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    assert dash["summary"]["total_equity_usd"] == 545.00
    assert dash["reconciliation"]["residual_usd"] == 0.0


def test_kucoin_91d_7d_windowed_history_positions_fetches_today_and_20d_old_close(tmp_path: Path) -> None:
    """KuCoin GET /api/v1/history-positions defaults to 7d and caps single queries at 7*24h; 13x7d windowing must
    recover both today's closed trade and a 20-day-old closed trade across an empty intermediate window."""
    now_ms = 1_760_000_000_000
    day_ms = 86_400_000
    seven_days_ms = 7 * day_ms

    positions_db = [
        {
            "closeId": "700000000099900001",
            "symbol": "ETHUSDTM",
            "side": "long",
            "closeSize": 10,
            "multiplier": 0.1,
            "openPrice": 3000.0,
            "closePrice": 3050.0,
            "openTime": now_ms - 7200_000,
            "closeTime": now_ms - 3600_000,
            "tradeFee": 1.0,
            "fundingFee": 0.0,
            "pnl": 49.0,
        },
        {
            "closeId": "700000000036305466",
            "symbol": "XBTUSDTM",
            "side": "short",
            "closeSize": 10,
            "multiplier": 0.001,
            "openPrice": 95000.0,
            "closePrice": 92000.0,
            "openTime": now_ms - 20 * day_ms - 3600_000,
            "closeTime": now_ms - 20 * day_ms,
            "tradeFee": 1.5,
            "fundingFee": 0.5,
            "pnl": 29.0,
        },
    ]

    queried_windows: list[tuple[int, int]] = []

    def kc_strict_7d_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        q = dict(req.url.params)
        if p == "/api/v1/accounts":
            return httpx.Response(200, json={"code": "200000", "data": []})
        if p == "/api/v1/account-overview":
            return httpx.Response(200, json={"code": "200000", "data": {"accountEquity": 1078.0, "availableBalance": 1078.0}})
        if p == "/api/v1/deposits":
            return httpx.Response(200, json={"code": "200000", "data": {"items": [{"walletTxId": "d1", "currency": "USDT", "amount": "1000.0", "createdAt": now_ms - 30 * day_ms}]}})
        if p == "/api/v1/history-positions":
            assert set(q.keys()) == {"from", "to", "limit", "pageId"}
            w_from = int(q["from"])
            w_to = int(q["to"])
            if w_to - w_from > seven_days_ms:
                return httpx.Response(400, json={"code": "400100", "msg": "time range exceeds 7 days"})
            queried_windows.append((w_from, w_to))
            pg = int(q["pageId"])
            matched = [r for r in positions_db if w_from <= int(r["closeTime"]) <= w_to]
            chunk = matched[(pg - 1) * 250 : pg * 250]
            return httpx.Response(200, json={"code": "200000", "data": {"items": chunk, "hasMore": False}})
        return httpx.Response(200, json={"code": "200000", "data": {"items": [], "dataList": [], "ticker": []}})

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(kc_strict_7d_handler)) as hc:
            client = KucoinReadOnlyClient(api_key="k", api_secret="s", passphrase="p", client=hc, clock_ms=lambda: now_ms)
            outcome = await sync_kucoin_venue(client, store, now_s=now_ms // 1000, force_full=True)
            assert outcome.equity_usd == 1078.0
            assert len(queried_windows) == 13  # Scanned full 91 days across empty intermediate windows!

    asyncio.run(_run())

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    assert len(dash["trades"]) == 2
    tids = {t["trade_id"] for t in dash["trades"]}
    assert any("700000000036305466" in tid for tid in tids)
    assert any("700000000099900001" in tid for tid in tids)
    assert dash["reconciliation"]["residual_usd"] == 0.0


def test_htx_90d_47h_windowed_matches_and_financials_fetch_today_and_20d_old_trades(tmp_path: Path) -> None:
    """HTX exact endpoints only return last 10 days when start_time/end_time are omitted; initial sync must slice
    90 days into <=48h windows and recover 20-day-old matches (81101/81102) and financial records."""
    now_ms = 1_760_000_000_000
    day_ms = 86_400_000
    hour_ms = 3600_000

    matches_all = [
        {
            "id": 91101,
            "match_id": 91101,
            "contract_code": "ETH-USDT",
            "direction": "buy",
            "offset": "open",
            "trade_price": "3000",
            "trade_turnover": "3000",
            "trade_volume": "1",
            "trade_fee": "-0.5",
            "real_profit": "0",
            "role": "maker",
            "create_date": now_ms - 2 * hour_ms,
        },
        {
            "id": 91102,
            "match_id": 91102,
            "contract_code": "ETH-USDT",
            "direction": "sell",
            "offset": "close",
            "trade_price": "3020",
            "trade_turnover": "3020",
            "trade_volume": "1",
            "trade_fee": "-0.5",
            "real_profit": "20.0",
            "role": "taker",
            "create_date": now_ms - hour_ms,
        },
        {
            "id": 81101,
            "match_id": 81101,
            "contract_code": "BTC-USDT",
            "direction": "buy",
            "offset": "open",
            "trade_price": "90000",
            "trade_turnover": "9000",
            "trade_volume": "1",
            "trade_fee": "-1.0",
            "real_profit": "0",
            "role": "maker",
            "create_date": now_ms - 20 * day_ms - hour_ms,
        },
        {
            "id": 81102,
            "match_id": 81102,
            "contract_code": "BTC-USDT",
            "direction": "sell",
            "offset": "close",
            "trade_price": "90500",
            "trade_turnover": "9050",
            "trade_volume": "1",
            "trade_fee": "-1.0",
            "real_profit": "50.0",
            "role": "taker",
            "create_date": now_ms - 20 * day_ms,
        },
    ]
    fin_all = [
        {
            "id": 70001,
            "type": 30,
            "amount": "-0.25",
            "contract_code": "BTC-USDT",
            "ts": now_ms - 20 * day_ms - 1800_000,
        }
    ]

    def htx_90d_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/linear-swap-api/v1/swap_cross_account_info":
            return httpx.Response(200, json={"status": "ok", "data": [{"margin_balance": "1066.75", "margin_available": "1066.75"}]})
        if p == "/v1/account/accounts":
            return httpx.Response(200, json={"status": "ok", "data": []})
        if p == "/v1/query/deposit-withdraw":
            if req.url.params.get("type") == "deposit":
                return httpx.Response(200, json={"status": "ok", "data": [{"id": 1, "currency": "usdt", "amount": "1000.0", "state": "safe", "created-at": now_ms - 30 * day_ms}]})
            return httpx.Response(200, json={"status": "ok", "data": []})
        if p == "/linear-swap-api/v1/swap_cross_matchresults_exact":
            body = json.loads(req.content.decode("utf-8"))
            # Real HTX defaults to last 10 days if start_time/end_time omitted, and enforces <= 48h when provided!
            s_t = int(body["start_time"]) if body.get("start_time") is not None else (now_ms - 10 * day_ms)
            e_t = int(body["end_time"]) if body.get("end_time") is not None else now_ms
            if body.get("start_time") is not None and (e_t - s_t > 48 * hour_ms):
                return httpx.Response(200, json={"status": "error", "err_code": 1056, "err_msg": "exceeds 48h"})
            rows = [r for r in matches_all if s_t <= int(r["create_date"]) <= e_t]
            return httpx.Response(200, json={"status": "ok", "data": {"trades": rows}})
        if p == "/linear-swap-api/v1/swap_financial_record_exact":
            body = json.loads(req.content.decode("utf-8"))
            s_t = int(body["start_time"]) if body.get("start_time") is not None else (now_ms - 10 * day_ms)
            e_t = int(body["end_time"]) if body.get("end_time") is not None else now_ms
            if body.get("start_time") is not None and (e_t - s_t > 48 * hour_ms):
                return httpx.Response(200, json={"status": "error", "err_code": 1056, "err_msg": "exceeds 48h"})
            rows = [r for r in fin_all if s_t <= int(r["ts"]) <= e_t]
            return httpx.Response(200, json={"status": "ok", "data": {"financial_record": rows}})
        return httpx.Response(200, json={"status": "ok", "data": []})

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(htx_90d_handler)) as hc:
            client = HtxReadOnlyClient(api_key="k", api_secret="s", client=hc, clock_ms=lambda: now_ms)
            outcome = await sync_htx_venue(client, store, now_s=now_ms // 1000, force_full=True)
            assert outcome.equity_usd == 1066.75

    asyncio.run(_run())

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    assert len(dash["trades"]) == 2
    by_sym = {t["symbol"]: t for t in dash["trades"]}
    assert set(by_sym.keys()) == {"ETH_USDT", "BTC_USDT"}
    assert by_sym["BTC_USDT"]["funding_pnl_usd"] == -0.25
    assert dash["reconciliation"]["residual_usd"] == 0.0


def test_hyperliquid_2001_same_millisecond_closes_and_boundary_overlap_zero_drop(tmp_path: Path) -> None:
    """When 2,001 closes occur in the exact same millisecond T, userFills returns descending 12001..10002 while
    userFillsByTime(T, T) returns ascending 10001..12000; union must capture all 2,001 closes including tid=10001!"""
    wallet = "0x" + "e" * 40
    burst_ts = 1_760_000_000_000

    # 2,001 closes all at exact timestamp `burst_ts` (tid=10001..12001)
    same_ms_2001 = [
        {
            "tid": 10001 + i,
            "oid": 10001 + i,
            "coin": "SOL",
            "px": "202.0",
            "sz": "0.1",
            "side": "A",
            "dir": "Close Long",
            "closedPnl": "0.2",
            "fee": "0.01",
            "crossed": True,
            "time": burst_ts,
        }
        for i in range(2001)
    ]

    def hl_2001_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        tp = body.get("type")
        if tp == "clearinghouseState":
            return httpx.Response(200, json={"marginSummary": {"accountValue": "1380.19", "withdrawable": "1380.19"}, "assetPositions": []})
        if tp == "spotClearinghouseState":
            return httpx.Response(200, json={"balances": []})
        if tp == "userVaultEquities":
            return httpx.Response(200, json=[])
        if tp == "allMids":
            return httpx.Response(200, json={"SOL": "202.0"})
        if tp == "userFills":
            # Latest 2,000 in descending order -> tid 12001 down to 10002 (drops earliest tid=10001!)
            return httpx.Response(200, json=list(reversed(same_ms_2001[-2000:])))
        if tp == "userFillsByTime":
            s_t = int(body.get("startTime") or 0)
            e_t = body.get("endTime")
            rows = [r for r in same_ms_2001 if int(r["time"]) >= s_t and (e_t is None or int(r["time"]) <= int(e_t))]
            # Earliest 2,000 in ascending order -> tid 10001 up to 12000!
            return httpx.Response(200, json=rows[:2000])
        if tp == "userNonFundingLedgerUpdates":
            return httpx.Response(200, json=[{"hash": "0xd1", "time": burst_ts - 60_000, "delta": {"type": "deposit", "usdc": "1000.0"}}])
        return httpx.Response(200, json=[])

    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(hl_2001_handler)) as hc:
            client = HyperliquidReadOnlyClient(wallet_address=wallet, client=hc)
            outcome = await sync_hyperliquid_venue(client, store, now_s=burst_ts // 1000, force_full=True)
            assert not any("2,000" in w for w in outcome.warnings)

        # Also verify >4,000 fills in a single millisecond (disjoint prefix 2,000 & suffix 2,000) flags truncation!
        same_ms_4005 = [
            {"tid": 50000 + i, "oid": 50000 + i, "coin": "SOL", "px": "200", "sz": "0.1", "side": "A", "dir": "Close Long", "closedPnl": "0.1", "fee": "0", "time": burst_ts}
            for i in range(4005)
        ]

        def hl_4005_handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode("utf-8"))
            tp = body.get("type")
            if tp == "userFills":
                return httpx.Response(200, json=list(reversed(same_ms_4005[-2000:])))
            if tp == "userFillsByTime":
                s_t = int(body.get("startTime") or 0)
                e_t = body.get("endTime")
                rows = [r for r in same_ms_4005 if int(r["time"]) >= s_t and (e_t is None or int(r["time"]) <= int(e_t))]
                return httpx.Response(200, json=rows[:2000])
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(hl_4005_handler)) as hc2:
            c2 = HyperliquidReadOnlyClient(wallet_address=wallet, client=hc2)
            warns_4005: list[str] = []
            got_4005 = await c2.get_user_fills(since_ms=0, warnings_out=warns_4005)
            assert len(got_4005) == 4000
            assert any("2,000" in w for w in warns_4005)

    asyncio.run(_run())

    snap = store.venue_scope("hyperliquid").get_kv("snapshot")
    assert len(snap["fills"]) == 2001
    assert any(int(f["tid"]) == 10001 for f in snap["fills"])

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    assert len(dash["trades"]) == 2001
    assert any("hl_10001_" in t["trade_id"] for t in dash["trades"])
    assert dash["reconciliation"]["residual_usd"] == 0.0



@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["kucoin_spot", "kucoin_futures", "htx_swap", "bingx_spot"])
async def test_partial_amount_is_not_total_equity(case: str) -> None:
    responses = {
        "kucoin_spot": {"code": "200000", "data": [{"currency": "USDT", "available": 18}]},
        "kucoin_futures": {"code": "200000", "data": {"currency": "USDT", "availableBalance": 12}},
        "htx_swap": {"status": "ok", "data": [{"margin_account": "USDT", "margin_available": 12}]},
        "bingx_spot": {"code": 0, "data": {"balances": [{"asset": "USDT", "locked": 2}]}},
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=responses[case]))
    async with httpx.AsyncClient(transport=transport) as http:
        auth = dict(api_key="test", api_secret="test", client=http)
        kc = KucoinReadOnlyClient(**auth, passphrase="test")
        calls = {
            "kucoin_spot": (kc.get_spot_accounts, KucoinApiError),
            "kucoin_futures": (kc.get_futures_overview, KucoinApiError),
            "htx_swap": (HtxReadOnlyClient(**auth).get_swap_accounts, HtxApiError),
            "bingx_spot": (BingxReadOnlyClient(**auth).get_spot_balances, BingxApiError),
        }
        fetch, error = calls[case]
        with pytest.raises(error, match="Malformed"):
            await fetch()


@pytest.mark.parametrize("amount", [0, "0"])
def test_four_venues_respect_zero_available_margin(tmp_path: Path, amount: int | str) -> None:
    snapshots = {
        "hyperliquid": {"clearinghouse": {"marginSummary": {"accountValue": 100}, "withdrawable": amount}},
        "kucoin": {"futures_overview": {"accountEquity": 100, "availableBalance": amount}},
        "bingx": {"swap_balances": [{"asset": "USDT", "equity": 100, "availableMargin": amount}]},
        "htx": {"swap_accounts": [{"margin_balance": 100, "margin_available": amount}]},
    }
    store = LedgerStore(tmp_path)
    registry = build_default_registry()
    reported = {}
    for venue, snapshot in snapshots.items():
        store.venue_scope(venue).set_kv("snapshot", snapshot)
        bundle = registry.get(venue).build_venue_ledger(store, journals={}, now_s=0)
        assert bundle.exchange_card["equity_usd"] == 100
        reported[venue] = bundle.exchange_card["available_margin_usd"]
    assert reported == dict.fromkeys(snapshots, 0)


@pytest.mark.parametrize(
    ("spot_row", "equity"),
    [({"balance": 20, "locked": 2}, 20), ({"balance": 0, "locked": 2}, 0),
     ({"balance": 20}, 20), ({"free": 18, "locked": 2}, 20)],
)
def test_bingx_sync_and_dashboard_use_explicit_total(tmp_path: Path, spot_row: dict, equity: int) -> None:
    snapshot = {"spot_balances": [{"asset": "USDT", **spot_row}]}
    store = LedgerStore(tmp_path)
    store.venue_scope("bingx").set_kv("snapshot", snapshot)
    bundle = build_default_registry().get("bingx").build_venue_ledger(store, journals={}, now_s=0)
    assert (extract_bingx_snapshot_activity(snapshot)[0], bundle.exchange_card["equity_usd"]) == (equity, equity)


@pytest.mark.asyncio
@pytest.mark.parametrize("coverage", ["duplicate", "partial_overlap", "unrecoverable"])
async def test_hyperliquid_repeated_full_boundary_page(coverage: str) -> None:
    timestamp = 1_760_000_000_000
    count = 2003 if coverage == "partial_overlap" else 2001
    rows = [{"tid": i, "time": timestamp} for i in range(1, count + 1)]
    latest = rows[-2000:][::-1]

    def reply(req: httpx.Request) -> httpx.Response:
        query = json.loads(req.content)
        if query["type"] == "userFills":
            result = latest
        elif query["endTime"] < timestamp:
            result = []
        elif query["startTime"] < timestamp and coverage != "unrecoverable":
            result = rows[:2000]
        elif coverage == "partial_overlap":
            result = [rows[2], *latest[:1999]]
        else:
            result = latest
        return httpx.Response(200, json=result)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        client = HyperliquidReadOnlyClient(wallet_address="0x" + "e" * 40, client=http)
        warnings: list[str] = []
        result = await client.get_user_fills(warnings_out=warnings)
    if coverage != "unrecoverable":
        assert {r["tid"] for r in result} == {r["tid"] for r in rows}
    else:
        assert len(result) == 2000
        assert any("truncat" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_hyperliquid_saturated_incremental_millisecond_warns_and_continues() -> None:
    timestamp = 1_760_000_000_000
    burst = [{"tid": i, "time": timestamp} for i in range(1, 2002)]
    tail = {"tid": 3000, "time": timestamp + 1}

    def reply(req: httpx.Request) -> httpx.Response:
        query = json.loads(req.content)
        if query.get("endTime") == timestamp and query["startTime"] < timestamp:
            return httpx.Response(200, json=burst[-2000:])
        rows = [r for r in [*burst, tail] if r["time"] >= query["startTime"]]
        return httpx.Response(200, json=rows[:2000])

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        client = HyperliquidReadOnlyClient(wallet_address="0x" + "e" * 40, client=http)
        warnings: list[str] = []
        fills = await client.get_user_fills(since_ms=timestamp, warnings_out=warnings)
    assert {r["tid"] for r in fills} == {r["tid"] for r in [*burst, tail]}
    assert any("truncat" in w.lower() for w in warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("venue", "old", "fresh", "sync"),
    [
        (
            "kucoin",
            {"futures_overview": {"availableBalance": 12}, "spot_accounts": [{"currency": "USDT", "available": 18}]},
            {"futures_overview": {"marginBalance": 100}, "spot_accounts": [{"currency": "USDT", "available": 18, "holds": 2}]},
            sync_kucoin_venue,
        ),
        (
            "htx",
            {"swap_accounts": [{"margin_available": 12}]},
            {"swap_accounts": [{"margin_static": 100}], "spot_balances": [{"currency": "usdt", "balance": 20}]},
            sync_htx_venue,
        ),
        (
            "bingx",
            {"swap_balances": [{"asset": "USDT", "equity": 100}], "spot_balances": [{"asset": "USDT", "locked": 2}]},
            {"swap_balances": [{"asset": "USDT", "equity": 100}], "spot_balances": [{"asset": "USDT", "balance": 20}]},
            sync_bingx_venue,
        ),
    ],
)
async def test_preexisting_partial_snapshot_warns_until_healthy_sync(
    tmp_path: Path, venue: str, old: dict[str, Any], fresh: dict[str, Any],
    sync: Callable[..., Awaitable[Any]],
) -> None:
    store = LedgerStore(tmp_path)
    # The previous position count is still usable even when its old wallet is incomplete.
    old = {**old, "positions": [{"currentQty": 1, "volume": 1, "positionAmt": 1}]}
    store.upsert_plugin_snapshot(venue, old, merge_specs={})
    service = LedgerService(store, CredentialVault(tmp_path), build_default_registry(), default_demo_mode=False)
    before = service.get_dashboard(range_key="all")
    assert any(w["venue_id"] == venue for w in before["warnings"])
    assert all(c["venue_id"] != venue for c in before["exchanges"])
    client = AsyncMock()
    client.fetch_full_snapshot.return_value = fresh
    outcome = await sync(client, store, now_s=0)
    assert outcome.equity_usd == 120
    assert outcome.fp_before[0] == 1 and outcome.fp_after[0] == 0
    after = service.get_dashboard(range_key="all")
    assert not after["warnings"]
    assert next(c for c in after["exchanges"] if c["venue_id"] == venue)["equity_usd"] == 120
