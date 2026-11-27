import json
from collections import Counter

import httpx
import pytest

from opentradeledger.adapters.hyperliquid.client import HyperliquidReadOnlyClient
from opentradeledger.adapters.hyperliquid.sync import sync_hyperliquid_venue
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


@pytest.mark.asyncio
async def test_volume_is_bound_to_quoted_spot_pair_and_survives_ledger_rebuild(tmp_path):
    meta = {
        "tokens": [{"index": i, "name": n} for i, n in [(0, "USDC"), (6, "USDT"), (277, "RUB"),
                                                         (8, "SOME"), (847, "QQQX"), (14, "OTHER")]],
        "universe": [
            {"name": "@173", "index": 173, "tokens": [277, 0]},
            {"name": "@188", "index": 188, "tokens": [277, 6]},
            {"name": "SOME/USDC", "index": 20, "tokens": [8, 0]},
            {"name": "@21", "index": 21, "tokens": [847, 0]},
            {"name": "@22", "index": 22, "tokens": [14, 0]},
        ],
    }
    contexts = [
        {"coin": "@188", "dayNtlVlm": "5000000000000"},
        {"coin": "@21", "dayNtlVlm": "12"},
        {"coin": "SOME/USDC", "dayNtlVlm": "5000"},
        {"dayNtlVlm": "0"},
        {"coin": "@173", "dayNtlVlm": "0.0", "markPx": "16001"},
    ]
    calls = Counter()
    contexts_down = False

    def exchange(request):
        kind = json.loads(request.content)["type"]
        calls[kind] += 1
        if kind == "spotMetaAndAssetCtxs" and contexts_down:
            return httpx.Response(503)
        payload = {
            "allMids": {"@173": "16438", "@188": "16000", "SOME/USDC": "7", "@21": "200", "@22": "2"},
            "spotMetaAndAssetCtxs": [meta, contexts], "spotMeta": meta,
            "clearinghouseState": {"marginSummary": {"accountValue": "50"}},
            "spotClearinghouseState": {"balances": [
                {"coin": name, "token": token, "total": qty} for name, token, qty in
                [("RUB", 277, "100000000"), ("SOME", 8, "2"), ("QQQX", 847, "1"), ("OTHER", 14, "4")]
            ]},
        }.get(kind, [])
        return httpx.Response(200, json=payload)

    def dashboard():
        return LedgerService(LedgerStore(tmp_path), CredentialVault(tmp_path), build_default_registry(),
                             default_demo_mode=False).get_dashboard(range_key="all")

    async with httpx.AsyncClient(transport=httpx.MockTransport(exchange)) as http:
        client = HyperliquidReadOnlyClient(wallet_address="0x" + "1" * 40, client=http)
        await sync_hyperliquid_venue(client, LedgerStore(tmp_path), now_s=1)
        result = dashboard()
        assert result["summary"]["total_equity_usd"] == 1_643_800_000_272
        held = {row["asset"]: row for row in result["spot_holdings"]}
        market = held["RUB"].get("spot_market")
        assert market is not None, "Persist the official market evidence alongside the quoted estimate"
        assert (market["pair"], market["quote_asset"], market["price"]) == ("@173", "USDC", 16438)
        assert (market["volume_24h_quote"], market["liquidity"]) == (0, "zero_volume")
        assert market["source"] == "Hyperliquid spotMetaAndAssetCtxs"
        assert held["RUB"]["market_value_usd"] == 1_643_800_000_000
        assert held["SOME"]["spot_market"]["liquidity"] == "unflagged"
        assert held["OTHER"]["spot_market"]["liquidity"] == "unknown"
        assert held["OTHER"]["spot_market"]["volume_24h_quote"] is None
        [stock] = result["extensions"]["stocks"]["holdings"]
        assert stock["spot_market"]["liquidity"] == "holding_exceeds_volume"
        assert (calls["spotMetaAndAssetCtxs"], calls["spotMeta"]) == (1, 0)
        # Context unavailable on the next sync: quote still counts, old volume must expire.
        contexts_down = True
        await sync_hyperliquid_venue(client, LedgerStore(tmp_path), now_s=2)
        refreshed = dashboard()
        assert refreshed["summary"]["total_equity_usd"] == result["summary"]["total_equity_usd"]
        [rub] = [row for row in refreshed["spot_holdings"] if row["asset"] == "RUB"]
        assert rub["spot_market"]["volume_24h_quote"] is None
        assert rub["spot_market"]["liquidity"] == "unknown"


def test_older_saved_quotes_do_not_imply_zero_volume(tmp_path):
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("hyperliquid", {"spot": {"balances": [{"coin": "RUB", "token": 277, "total": "2"}]},
                                                "spot_token_prices": {"277": 16438}, "has_spot_meta": True})
    adapter = build_default_registry().get("hyperliquid")
    [row] = adapter.build_venue_ledger(store, journals={}, now_s=100).spot_holdings
    assert row["market_value_usd"] == 32876
    assert row.get("spot_market", {}).get("liquidity") == "unknown"

def test_small_quoted_price_survives_projection_to_spot_holding(tmp_path):
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("hyperliquid", {
        "spot": {"balances": [{"coin": "MAX", "token": 734, "total": "200000000"}]},
        "spot_token_prices": {"734": 0.0000001}, "has_spot_meta": True,
    })
    [row] = build_default_registry().get("hyperliquid").build_venue_ledger(store, journals={}, now_s=100).spot_holdings
    assert row["market_value_usd"] == 20
    assert row["price_usd"] == pytest.approx(0.0000001, abs=1e-14)


def test_unpriced_token_cannot_use_another_tokens_named_quote_or_turnover():
    from opentradeledger.adapters.hyperliquid.client import (
        build_hyperliquid_spot_prices, resolve_hyperliquid_spot_price,
    )

    meta = {"tokens": [{"name": "USDC", "index": 0}, {"name": "DUP", "index": 11}, {"name": "DUP", "index": 19}],
            "universe": [{"name": "DUP/USDC", "tokens": [11, 0], "index": 5},
                         {"name": "@9", "tokens": [19, 0], "index": 9}]}
    token_px, coin_px, _, markets = build_hyperliquid_spot_prices(
        {"DUP/USDC": 100}, meta, [{"coin": "DUP/USDC", "dayNtlVlm": "9999999"}])
    snap = {"has_spot_meta": True, "spot_tickers": coin_px, "spot_token_prices": token_px, "spot_markets": markets}
    assert resolve_hyperliquid_spot_price({"coin": "DUP", "token": 19}, snap) == 0
    assert resolve_hyperliquid_spot_price({"coin": "DUP", "token": 11}, snap) == 100
    assert resolve_hyperliquid_spot_price({"coin": "DUP", "token": 99}, snap) == 0
    assert resolve_hyperliquid_spot_price({"coin": "DUP"}, snap) == 0
    assert "19" not in markets
