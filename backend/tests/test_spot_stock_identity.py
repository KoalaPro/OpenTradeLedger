from pathlib import Path

import pytest

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.adapters.stock_channels import classify_spot_stock_token
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault


@pytest.mark.parametrize(("venue", "asset"), [
    ("HYPERLIQUID", "MAX"), ("HYPERLIQUID", "QQQSTOCK"), ("HYPERLIQUID", "NVDAX"),
    ("BYBIT", "MAX"), ("GATE", "UNLISTEDSTOCK"), ("GATE", "ONDO"), ("GATE", "MAG7XON"), ("KUCOIN", "PLTRON"),
    ("BINANCE", "UNKNOWNB"), ("BITGET", "RSQ"), ("HTX", "AAPLON"),
    ("EASICOIN", "SPCXSTOCK"), ("MEXC", "NVDAX_USDT"),
])
def test_symbol_without_venue_stock_identity_stays_spot(venue, asset):
    assert classify_spot_stock_token(asset, exchange=venue) is None


@pytest.mark.parametrize(("venue", "asset", "underlying"), [
    ("GATE", "MAX", "MA"), ("KUCOIN", "AAPLON", "AAPL"), ("BITGET", "rSGOV", "SGOV"),
    ("BINANCE", "NVDAB", "NVDA"), ("BYBIT", "AAPLX", "AAPL"), ("OKX", "XNVDA", "NVDA"),
    ("HTX", "AAPL", "AAPL"), ("BITGET", "RFBTC", "FBTC"),
    ("BINGX", "QQQON", "QQQ"), ("KUCOIN", "ANTX", "ANT"),
])
def test_verified_spot_listings_are_scoped_to_the_venue(venue, asset, underlying):
    assert classify_spot_stock_token(asset, exchange=venue)["underlying"] == underlying


def test_positive_exchange_metadata_can_identify_a_colliding_ticker():
    stock = classify_spot_stock_token(
        "MAX", exchange="MEXC",
        dynamic_catalog={"MAX": {"underlying": "MA", "name": "Mastercard", "series": "xStocks"}},
    )
    assert (stock["token_symbol"], stock["underlying"], stock["name"]) == ("MAX", "MA", "Mastercard")


def test_persisted_hyperliquid_wallet_uses_token_index_and_keeps_each_value(tmp_path: Path):
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("hyperliquid", {
        "wallet_address": "0x" + "2" * 40,
        "clearinghouse": {"marginSummary": {"accountValue": "0"}, "assetPositions": []},
        "spot": {"balances": [
            {"coin": "MAX", "token": 734, "total": "200"},
            {"coin": "QQQX", "token": 847, "total": "1"},
            {"coin": "QQQX", "token": 9999, "total": "40"},
            {"coin": "QQQX", "token": 9998, "total": "3"},
        ]},
        "has_spot_meta": True,
        "spot_token_prices": {"734": 0.1, "847": 500.0, "9999": 0.25, "9998": 1.0},
        "spot_markets": {
            "847": {"asset": "QQQX", "pair": "@200", "quote_asset": "USDC", "price": 500.0, "volume_24h_quote": 900.0},
            "9999": {"asset": "QQQX", "pair": "@9000", "quote_asset": "USDC", "price": 0.25, "volume_24h_quote": 0.0},
        },
    })
    svc = LedgerService(store, CredentialVault(tmp_path), build_default_registry(), default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    holdings = dash["extensions"]["stocks"]["holdings"]
    assert [(h["symbol"], h["shares"], h["market_value_usd"]) for h in holdings] == [("QQQX", 1.0, 500.0)]
    assert holdings[0]["spot_market"]["liquidity"] == "unflagged"
    for field in ("spot_holdings", "spot_wallet_balances"):
        spot = {h["spot_token_index"]: h for h in dash[field]}
        assert (spot[734]["qty"], spot[734]["market_value_usd"]) == (200, 20)
        assert (spot[9999]["qty"], spot[9999]["market_value_usd"]) == (40, 10)
        assert (spot[9998]["qty"], spot[9998]["market_value_usd"]) == (3, 3)
        assert spot[9999]["spot_market"]["liquidity"] == "zero_volume"
    assert dash["summary"]["total_equity_usd"] == 533.0
    assert dash["equity_breakdown"]["stocks_equity_usd"] == 500.0
    assert dash["equity_breakdown"]["spot_and_other_usd"] == 33.0


@pytest.mark.asyncio
async def test_mexc_stock_catalog_requires_matching_spot_pair_and_explicit_product_type():
    import httpx
    from opentradeledger.adapters.compat_creds import MexcCredentials
    from opentradeledger.adapters.mexc.client import MexcReadOnlyClient

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/exchangeInfo":
            asset = request.url.params["symbol"].removesuffix("USDT")
            if asset == "WRONG":
                asset = "ALIAS"
            category = "Tokenized Stocks" if asset != "THEME" else "Stock Market Memes"
            return httpx.Response(200, json={"symbols": [{
                "baseAsset": asset, "symbol": asset + "USDT", "conceptPlates": [category],
                "fullName": "Verified stock token", "underlyingSymbol": "AAPL",
            }]})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = MexcReadOnlyClient(MexcCredentials(api_key="test", api_secret="test"), http_client=http)
        bundle = await client._fetch_stock_bundle([{"asset": x} for x in ("ALIAS", "WRONG", "THEME")])
    catalog = bundle["tokenized_catalog"]
    assert set(catalog) == {"ALIAS"}
    assert catalog["ALIAS"]["underlying"] == "AAPL"
    assert set(bundle["spot_stock_trades"]) == {"ALIAS"}


def test_old_mexc_derived_metadata_without_market_identity_uses_verified_listings_only():
    from opentradeledger.adapters.mexc.accounting import _build_mexc_stock_section

    old_catalog = {asset: {"series": "MEXC 美股代币", "underlying": base, "name": base}
                   for asset, base in [("THEME", "THEME"), ("NVDAX", "NVDA")]}
    live = {
        "spot_balances": [{"asset": key, "free": "1"} for key in old_catalog],
        "spot_tickers": {"THEME": 2, "NVDAX": 100},
        "stock_bundle": {"tokenized_catalog": old_catalog},
    }
    stocks, *_ = _build_mexc_stock_section(live, [], build_default_registry().get("mexc").meta)
    assert [(row["symbol"], row["market_value_usd"]) for row in stocks.holdings] == [("NVDAX", 100)]


def test_141_hyperliquid_snapshot_reclassifies_without_fresh_market_metadata(tmp_path: Path):
    store = LedgerStore(tmp_path)
    snapshot = {
        "clearinghouse": {"marginSummary": {"accountValue": "0"}, "assetPositions": []},
        "spot": {"balances": [{"coin": "MAX", "token": 734, "total": "200"}]},
        "spot_token_prices": {"734": 0.1}, "has_spot_meta": True,
    }
    store.upsert_plugin_snapshot("hyperliquid", snapshot)
    saved = store.venue_scope("hyperliquid").get_kv("snapshot")
    svc = LedgerService(store, CredentialVault(tmp_path), build_default_registry(), default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    assert dash["extensions"]["stocks"]["holdings"] == []
    assert dash["equity_breakdown"]["stocks_equity_usd"] == 0.0
    assert dash["summary"]["total_equity_usd"] == 20.0
    [spot] = dash["spot_holdings"]
    assert (spot["asset"], spot["spot_token_index"], spot["market_value_usd"]) == ("MAX", 734, 20)
    assert spot["spot_market"]["liquidity"] == "unknown"
    assert store.venue_scope("hyperliquid").get_kv("snapshot") == saved


def test_preupgrade_raw_wallets_retain_confirmed_gate_bitget_and_mexc_stock_tokens(tmp_path: Path):
    gate_tokens = ["NFLXON", "NFLXX", "TQQQX", "CRWDX", "AZNX"]
    bitget_tokens = ("TSLAON NVDAON AAPLON GOOGLON MSFTON AMZNON METAON AMDON SPYON "
                     "IVVON QQQON IWMON ITOTON IAUON SLVON").split()
    mexc_tokens = ["TSLAON", "AAPLON", "GOOGLON", "QQQON", "SPCXX"]
    store = LedgerStore(tmp_path)
    store.venue_scope("gate").set_kv("live_bundle", {
        "wallet_total": {"total": {"amount": "50"}},
        "unified": {"unified_account_total_equity": "50", "balances": {
            a: {"available": "1", "equity": "10"} for a in gate_tokens}},
        "spot_stock_bundle": {"tickers": dict.fromkeys(gate_tokens, "10")},
    })
    store.venue_scope("bitget").set_kv("live_bundle", {
        "account_mode": "CLASSIC", "uid": "classic",
        "classic_balances": [{"accountType": "spot", "usdtBalance": "160"},
                             {"accountType": "futures", "usdtBalance": "15"}],
        "futures_positions": [{"symbol": "ETHUSDT", "holdSide": "long", "total": "1",
                               "openPriceAvg": "12", "markPrice": "15", "unrealizedPL": "3"}],
        "spot_assets": [{"coin": a, "available": "0.5", "frozen": "0.3", "locked": "0.2"} for a in bitget_tokens]
                       + [{"coin": "USDT", "available": "10"}],
        "spot_tickers": dict.fromkeys(bitget_tokens, "10"),
    })
    store.venue_scope("mexc").set_kv("live_bundle", {
        "spot_balances": [{"asset": a, "free": "1"} for a in mexc_tokens],
        "spot_tickers": dict.fromkeys(mexc_tokens, "10"),
        "stock_bundle": {"tokenized_catalog": {
            a: {"name": a, "series": "MEXC 美股代币"} for a in mexc_tokens}},
    })
    svc = LedgerService(store, CredentialVault(tmp_path), build_default_registry(), default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    stocks = {(h["venue_id"], h["symbol"]): h["market_value_usd"] for h in dash["extensions"]["stocks"]["holdings"]}
    expected = {(v, a): 10.0 for v, assets in [
        ("gate", gate_tokens), ("bitget", bitget_tokens), ("mexc", mexc_tokens)] for a in assets}
    assert stocks == expected
    assert next(c for c in dash["exchanges"] if c["venue_id"] == "bitget")["equity_usd"] == 175.0
    assert not (set(expected) & {(h["venue_id"], h["asset"]) for h in dash["spot_wallet_balances"]})

    # The old Classic collector persisted the same account totals but discarded spot detail.
    classic = store.venue_scope("bitget").get_kv("live_bundle")
    classic.pop("spot_assets")
    store.venue_scope("bitget").set_kv("live_bundle", classic)
    old = svc.get_dashboard(range_key="all")
    assert old["summary"]["total_equity_usd"] == dash["summary"]["total_equity_usd"]
    assert old["reconciliation"] == dash["reconciliation"]
    assert old["open_positions"] == dash["open_positions"]
    assert any(p["symbol"] == "ETH_USDT" for p in old["open_positions"])
    assert not any(s["venue_id"] == "bitget" for s in old["extensions"]["stocks"]["holdings"])


@pytest.mark.asyncio
async def test_binance_native_bstock_catalog_preserves_official_token_identity():
    import httpx
    from opentradeledger.adapters.binance.accounting import _build_binance_stock_section
    from opentradeledger.adapters.binance.client import BinanceReadOnlyClient
    from opentradeledger.adapters.compat_creds import BinanceCredentials

    def respond(req):
        if req.url.path == "/sapi/v1/equity/market/tokenized-assets":
            assert req.headers.get("X-MBX-APIKEY") == "fixture"
            assert "signature" not in req.url.params
            return httpx.Response(200, json=[{
                "assetCode": "TESTB", "assetName": "Issuer stock", "underlyingEquitySymbol": "TEST",
            }])
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = BinanceReadOnlyClient(BinanceCredentials(api_key="fixture", api_secret="fixture"), client=http)
        balances = [{"asset": "TESTB", "free": "1"}]
        bundle = await client._fetch_stock_bundle(balances)
    assert "TESTB" in bundle["spot_stock_trades"]
    stocks, *_ = _build_binance_stock_section(
        {"spot_balances": balances, "stock_bundle": bundle, "spot_tickers": {"TESTB": 10}},
        build_default_registry().get("binance").meta,
    )
    assert [(r["symbol"], r["name"]) for r in stocks.holdings] == [("TESTB", "Issuer stock")]


@pytest.mark.asyncio
async def test_bitget_classic_fetch_preserves_asset_rows():
    import httpx
    from opentradeledger.adapters.bitget.client import BitgetReadOnlyClient
    from opentradeledger.adapters.compat_creds import BitgetCredentials

    spot_rows = [{"coin": "MSFTON", "available": "1", "frozen": "0", "locked": "0"}]

    def respond(req):
        path = req.url.path
        if path == "/api/v3/account/assets":
            return httpx.Response(400, json={"code": "40099", "msg": "Classic account"})
        data = {
            "/api/v2/account/all-account-balance": [{"accountType": "spot", "usdtBalance": "25"}],
            "/api/v2/spot/account/assets": spot_rows,
        }.get(path, [])
        return httpx.Response(200, json={"code": "00000", "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = BitgetReadOnlyClient(BitgetCredentials("fixture", "fixture", "fixture"), http_client=http)
        bundle = await client.fetch_live_overview_bundle()
    assert bundle["spot_assets"] == spot_rows
    assert bundle["classic_balances"] == [{"accountType": "spot", "usdtBalance": "25"}]
