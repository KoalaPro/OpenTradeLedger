"""Full retest unit & actuarial regression suite for BingX, HTX, Hyperliquid, and KuCoin adapters."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from typing import Any

import httpx
import pytest

from opentradeledger.adapters.bingx.accounting import (
    _is_completed_bingx_deposit,
    _is_completed_bingx_withdrawal,
)
from opentradeledger.adapters.bingx.client import BingxApiError, BingxReadOnlyClient
from opentradeledger.adapters.bingx.sync import sync_bingx_venue
from opentradeledger.adapters.htx.client import HtxApiError, HtxReadOnlyClient
from opentradeledger.adapters.htx.sync import sync_htx_venue
from opentradeledger.adapters.hyperliquid.client import (
    HYPERLIQUID_ALLOWED_INFO_TYPES,
    HYPERLIQUID_ALLOWED_POST_TYPES,
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

    # 120 match rows (60 open/close pairs -> 60 closed trades) spanning 100 hours ago to 1 hour ago
    matches_db: list[dict[str, Any]] = []
    for i in range(60):
        base_t = now_ms - (100 * hour_ms) + i * (90 * 60_000)
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
            client = KucoinReadOnlyClient(api_key="k", api_secret="s", passphrase="p", key_version="2", client=hc)
            outcome = await sync_kucoin_venue(client, store, now_s=now_ms // 1000, force_full=True)
            assert client.key_version == "3"
            assert outcome.equity_usd == 1190.0
            assert seen_hist_params == [{"pageId": "1", "limit": "250"}]

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
