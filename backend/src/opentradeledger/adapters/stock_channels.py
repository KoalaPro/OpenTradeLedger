"""Multi-exchange US/HK stock (`正股`) & 1:1 tokenized stock (`现货股票代币`) normalization and cost-basis engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from opentradeledger.adapters.stock_listings import HYPERCORE_STOCK_LISTINGS, VENUE_STOCK_LISTINGS

if TYPE_CHECKING:
    from opentradeledger.adapters.base import VenueLedgerBundle

TZ_BEIJING = timezone(timedelta(hours=8))

US_EQUITY_TICKERS: dict[str, str] = {
    "AAPL": "Apple 苹果",
    "NVDA": "NVIDIA 英伟达",
    "TSLA": "Tesla 特斯拉",
    "MSFT": "Microsoft 微软",
    "AMZN": "Amazon 亚马逊",
    "GOOG": "Alphabet 谷歌C",
    "GOOGL": "Alphabet 谷歌A",
    "META": "Meta Platforms",
    "AVGO": "Broadcom 博通",
    "TSM": "TSMC 台积电",
    "ASML": "ASML 阿斯麦",
    "AMD": "AMD 超威半导体",
    "INTC": "Intel 英特尔",
    "MU": "Micron 美光",
    "ARM": "Arm Holdings",
    "SMCI": "Super Micro 超微电脑",
    "DELL": "Dell 戴尔",
    "SNDK": "SanDisk 闪迪",
    "ORCL": "Oracle 甲骨文",
    "CRM": "Salesforce",
    "PLTR": "Palantir",
    "SNOW": "Snowflake",
    "PANW": "Palo Alto Networks",
    "CRWD": "CrowdStrike",
    "NOW": "ServiceNow",
    "ADBE": "Adobe",
    "IBM": "IBM",
    "CSCO": "Cisco 思科",
    "QCOM": "Qualcomm 高通",
    "TXN": "Texas Instruments 德州仪器",
    "AMAT": "Applied Materials 应用材料",
    "LRCX": "Lam Research 泛林",
    "KLAC": "KLA 科磊",
    "MRVL": "Marvell 迈威尔",
    "NFLX": "Netflix 奈飞",
    "DIS": "Disney 迪士尼",
    "SPOT": "Spotify",
    "RBLX": "Roblox",
    "UBER": "Uber 优步",
    "ABNB": "Airbnb 爱彼迎",
    "DASH": "DoorDash",
    "SHOP": "Shopify",
    "MELI": "MercadoLibre",
    "SE": "Sea Limited",
    "BABA": "Alibaba 阿里巴巴",
    "PDD": "PDD 拼多多",
    "JD": "JD.com 京东",
    "BIDU": "Baidu 百度",
    "NIO": "NIO 蔚来",
    "XPEV": "XPeng 小鹏",
    "LI": "Li Auto 理想",
    "FUTU": "Futu 富途",
    "TIGR": "UP Fintech 老虎证券",
    "COIN": "Coinbase",
    "MSTR": "Strategy (MicroStrategy)",
    "HOOD": "Robinhood",
    "MARA": "MARA Holdings",
    "RIOT": "Riot Platforms",
    "CLSK": "CleanSpark",
    "CORZ": "Core Scientific",
    "IREN": "IREN",
    "HUT": "Hut 8",
    "CRCL": "Circle",
    "RDDT": "Reddit",
    "APP": "AppLovin",
    "HIMS": "Hims & Hers",
    "VST": "Vistra",
    "CEG": "Constellation Energy",
    "OKLO": "Oklo",
    "SMR": "NuScale Power",
    "IONQ": "IonQ",
    "RGTI": "Rigetti",
    "SOUN": "SoundHound AI",
    "TEM": "Tempus AI",
    "SPCX": "SpaceX 指数/份额",
    "SQ": "Block",
    "XYZ": "Block",
    "PYPL": "PayPal",
    "V": "Visa",
    "MA": "Mastercard",
    "AXP": "American Express 美国运通",
    "JPM": "JPMorgan 摩根大通",
    "BAC": "Bank of America 美国银行",
    "GS": "Goldman Sachs 高盛",
    "MS": "Morgan Stanley 摩根士丹利",
    "C": "Citigroup 花旗",
    "WFC": "Wells Fargo 富国银行",
    "BRKB": "Berkshire Hathaway B 伯克希尔",
    "BLK": "BlackRock 贝莱德",
    "SCHW": "Charles Schwab 嘉信理财",
    "UNH": "UnitedHealth 联合健康",
    "LLY": "Eli Lilly 礼来",
    "NVO": "Novo Nordisk 诺和诺德",
    "JNJ": "Johnson & Johnson 强生",
    "PFE": "Pfizer 辉瑞",
    "MRK": "Merck 默沙东",
    "ABBV": "AbbVie 艾伯维",
    "ISRG": "Intuitive Surgical 直觉外科",
    "TMO": "Thermo Fisher 赛默飞",
    "WMT": "Walmart 沃尔玛",
    "COST": "Costco 开市客",
    "HD": "Home Depot 家得宝",
    "TGT": "Target 塔吉特",
    "NKE": "Nike 耐克",
    "SBUX": "Starbucks 星巴克",
    "MCD": "McDonald's 麦当劳",
    "KO": "Coca-Cola 可口可乐",
    "PEP": "PepsiCo 百事",
    "PG": "Procter & Gamble 宝洁",
    "XOM": "ExxonMobil 埃克森美孚",
    "CVX": "Chevron 雪佛龙",
    "BA": "Boeing 波音",
    "CAT": "Caterpillar 卡特彼勒",
    "GE": "GE Aerospace 通用电气",
    "LMT": "Lockheed Martin 洛克希德马丁",
    "SPY": "SPDR S&P 500 标普500 ETF",
    "VOO": "Vanguard S&P 500 ETF",
    "IVV": "iShares S&P 500 ETF",
    "QQQ": "Invesco QQQ 纳指100 ETF",
    "QQQM": "Invesco NASDAQ 100 ETF",
    "TQQQ": "ProShares 3倍做多纳指 ETF",
    "SQQQ": "ProShares 3倍做空纳指 ETF",
    "DIA": "SPDR 道琼斯指数 ETF",
    "IWM": "iShares 罗素2000 ETF",
    "SOXX": "iShares 半导体 ETF",
    "SMH": "VanEck 半导体 ETF",
    "SOXL": "Direxion 3倍做多半导体 ETF",
    "SOXS": "Direxion 3倍做空半导体 ETF",
    "ARKK": "ARK Innovation ETF",
    "TLT": "iShares 20+年期美债 ETF",
    "SGOV": "iShares 0-3月短债 ETF",
    "SHV": "iShares 短期美债 ETF",
    "GLD": "SPDR Gold 黄金 ETF",
    "IAU": "iShares Gold 黄金 ETF",
    "SLV": "iShares Silver 白银 ETF",
    "USO": "United States Oil 原油 ETF",
    "IBIT": "iShares Bitcoin 比特币现货 ETF",
    "FBTC": "Fidelity 比特币现货 ETF",
    "ETHA": "iShares 以太坊现货 ETF",
}

_KNOWN_CRYPTO_EXCLUSIONS: frozenset[str] = frozenset(
    {
        "USDT", "USDC", "FDUSD", "USD1", "USDE", "BUSD", "DAI", "TUSD", "PYUSD", "USD",
        "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "TRX", "TON", "AVAX",
        "SHIB", "DOT", "LINK", "BCH", "NEAR", "LTC", "MATIC", "POL", "UNI", "ICP",
        "PEPE", "APT", "FET", "KAS", "ETC", "XLM", "MON", "XMR", "OKB", "BGB",
        "MX", "GT", "WLFI", "STX", "FIL", "ARB", "OP", "SUI", "SEI", "TIA",
        "INJ", "IMX", "HBAR", "ATOM", "VET", "MNT", "WIF", "BONK", "FLOKI", "TRUMP",
        "RNDR", "RENDER", "GRT", "AAVE", "MKR", "ALGO", "RUNE", "THETA", "FTM", "FLOW",
        "SAND", "AXS", "MANA", "GALA", "LDO", "ONDO", "ENA", "PENDLE", "JUP", "PYTH",
        "STRK", "ZRO", "ZK", "BLAST", "EIGEN", "WLD", "ORDI", "SATS", "BOME", "MEME",
        "RAY", "RSR", "RPL", "ROSE", "RON", "RVN", "RDNT", "REZ", "RIF", "RLC",
        "RARE", "RAD", "REN", "REQ", "REEF", "RACA", "RSS3", "SNX", "GMX", "CFX",
        "IOTX", "CVX", "ICX", "ZRX", "BNX", "MDX", "VGX", "STRAX", "WAXP", "AGIX",
        "TRB", "CKB", "PHB", "RLB", "NFB", "ARB", "AMB", "NEON", "AEON", "SOON",
        "MXPOINT",
    }
)

_STOCK_SUBACCOUNT_NAMES: frozenset[str] = frozenset(
    {"STOCK", "STOCKS", "REALSTOCK", "REALSTOCKS", "EQUITY", "EQUITIES", "TRADFI", "US_STOCK", "STOCKPLUS"}
)


def _dec(val: Any, default: str = "0") -> Decimal:
    if val in (None, ""):
        return Decimal(default)
    try:
        d = Decimal(str(val))
        return d if d.is_finite() else Decimal(default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _f2(val: Decimal | float | int | str, digits: int = 4) -> float:
    d = _dec(val)
    if digits <= 0:
        q = d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    else:
        q = d.quantize(Decimal(10) ** (-digits), rounding=ROUND_HALF_UP)
    res = float(q)
    return 0.0 if res == 0.0 else res


def _norm_ms(raw_ts: Any) -> int:
    val = _dec(raw_ts)
    if val <= 0:
        return 0
    if val > Decimal("10000000000"):
        return int(val)
    return int((val * Decimal("1000")).to_integral_value(rounding=ROUND_HALF_UP))


def _norm_sec(raw_ts: Any) -> float:
    ms = _norm_ms(raw_ts)
    return float(ms) / 1000.0 if ms > 0 else 0.0


def _fmt_bj(ts_sec: float | int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not ts_sec or ts_sec <= 0:
        return "—"
    sec = float(ts_sec) / 1000.0 if float(ts_sec) > 10_000_000_000 else float(ts_sec)
    return datetime.fromtimestamp(sec, TZ_BEIJING).strftime(fmt)


def classify_spot_stock_token(
    asset: str,
    *,
    exchange: str,
    dynamic_catalog: Mapping[str, Mapping[str, Any]] | None = None,
    token_index: int | str | None = None,
) -> dict[str, str] | None:
    """Look up an exact venue spot listing before attaching an equity identity."""
    symbol = (asset or "").strip().upper()
    venue = (exchange or "").strip().upper()
    if venue == "HYPERLIQUID":
        listing = HYPERCORE_STOCK_LISTINGS.get((str(token_index), symbol))
    else:
        listing = VENUE_STOCK_LISTINGS.get(venue, {}).get(symbol)

    metadata = (dynamic_catalog or {}).get(symbol) if venue != "HYPERLIQUID" else None
    if isinstance(metadata, Mapping) and metadata:
        underlying = str(metadata.get("underlying") or (listing[0] if listing else symbol)).strip().upper()
        series = str(metadata.get("series") or (listing[1] if listing else "1:1 美股代币"))
        name = str(metadata.get("name") or US_EQUITY_TICKERS.get(underlying, underlying))
    elif listing:
        underlying, series = listing
        name = US_EQUITY_TICKERS.get(underlying, underlying)
    else:
        return None
    return {
        "token_symbol": symbol,
        "underlying": underlying,
        "name": name,
        "series": series,
        "channel": "SPOT_TOKEN",
        "channel_zh": "1:1 美股代币",
    }


def _extract_fill_fields(
    raw: Mapping[str, Any],
    *,
    token_symbol: str = "",
    quote_price_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    side_raw = str(raw.get("side") or raw.get("tradeSide") or "").strip().upper()
    if not side_raw and "isBuyer" in raw:
        side_raw = "BUY" if bool(raw.get("isBuyer")) else "SELL"
    is_buy = side_raw.startswith("B")

    qty = abs(
        _dec(
            raw.get("qty")
            or raw.get("amount")
            or raw.get("size")
            or raw.get("fillQty")
            or raw.get("quantity")
            or raw.get("baseVolume")
        )
    )
    px = _dec(
        raw.get("price")
        or raw.get("priceAvg")
        or raw.get("fillPrice")
        or raw.get("execPrice")
    )
    if px <= 0 and qty > 0:
        q_val = abs(_dec(raw.get("quoteQty") or raw.get("fillAmount") or raw.get("value")))
        if q_val > 0:
            px = q_val / qty

    fee_detail = raw.get("feeDetail")
    if isinstance(fee_detail, Mapping):
        fee_qty = abs(_dec(fee_detail.get("totalFee") or fee_detail.get("fee")))
        fee_asset = str(fee_detail.get("feeCoin") or "USDT").strip().upper()
    elif isinstance(fee_detail, list) and fee_detail and isinstance(fee_detail[0], Mapping):
        fee_qty = sum((abs(_dec(x.get("totalFee") or x.get("fee"))) for x in fee_detail if isinstance(x, Mapping)), Decimal("0"))
        fee_asset = str(fee_detail[0].get("feeCoin") or "USDT").strip().upper()
    else:
        fee_qty = abs(_dec(raw.get("fee") or raw.get("commission") or raw.get("fillFee")))
        fee_asset = str(
            raw.get("fee_asset")
            or raw.get("fee_currency")
            or raw.get("commissionAsset")
            or raw.get("feeCoin")
            or "USDT"
        ).strip().upper()

    tok_u = (token_symbol or "").strip().upper()
    if fee_qty <= 0:
        fee_usd = Decimal("0")
    elif fee_asset in ("USDT", "USDC", "FDUSD", "USD1", "USDE", "BUSD", "USD", ""):
        fee_usd = fee_qty
    elif tok_u and fee_asset == tok_u:
        fee_usd = fee_qty * px
    elif quote_price_map and fee_asset in quote_price_map:
        fee_usd = fee_qty * _dec(quote_price_map.get(fee_asset))
    else:
        fee_usd = fee_qty

    role_raw = str(raw.get("role") or raw.get("tradeScope") or "").strip().lower()
    if "maker" in role_raw or bool(raw.get("isMaker")):
        role_zh = "限价 Maker"
    else:
        role_zh = "市价 Taker"

    ts_ms = _norm_ms(
        raw.get("time_ms")
        or raw.get("time")
        or raw.get("cTime")
        or raw.get("create_time")
        or raw.get("timestamp")
        or raw.get("uTime")
    )
    fid = str(raw.get("id") or raw.get("tradeId") or raw.get("execId") or f"{ts_ms}_{qty}_{px}")
    oid = str(raw.get("order_id") or raw.get("orderId") or "")
    return {
        "id": fid,
        "order_id": oid,
        "is_buy": is_buy,
        "side_zh": "买入" if is_buy else "卖出",
        "role_zh": role_zh,
        "qty": qty,
        "price": px,
        "fee_qty": fee_qty,
        "fee_asset": fee_asset,
        "fee_usd": fee_usd,
        "time_ms": ts_ms,
    }


def reconstruct_spot_cost_basis(
    *,
    fills: Sequence[Mapping[str, Any]],
    current_qty: Decimal,
    current_price: Decimal,
    token_symbol: str = "",
    quote_price_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Moving-average cost basis reconstruction with exact cash-flow conservation."""
    tok_u = (token_symbol or "").strip().upper()
    parsed = [
        _extract_fill_fields(f, token_symbol=tok_u, quote_price_map=quote_price_map)
        for f in fills
    ]
    parsed = [p for p in parsed if p["qty"] > 0 and p["price"] >= 0]
    parsed.sort(key=lambda x: (x["time_ms"], x["id"]))

    pool_qty = Decimal("0")
    pool_cost = Decimal("0")
    realized_pnl = Decimal("0")
    total_fee_usd = Decimal("0")

    for p in parsed:
        q = p["qty"]
        px = p["price"]
        total_fee_usd += p["fee_usd"]
        if p["is_buy"]:
            net_q = q - p["fee_qty"] if (tok_u and p["fee_asset"] == tok_u and p["fee_qty"] < q) else q
            pool_cost += net_q * px
            pool_qty += net_q
        else:
            avg_c = (pool_cost / pool_qty) if pool_qty > 0 else px
            sell_q = min(q, pool_qty) if pool_qty > 0 else Decimal("0")
            if sell_q > 0:
                realized_pnl += (px - avg_c) * sell_q
                pool_cost -= avg_c * sell_q
                pool_qty = max(Decimal("0"), pool_qty - sell_q)

    mkt_val = current_qty * current_price
    if current_qty > 0:
        if pool_qty > 0:
            avg_cost = pool_cost / pool_qty
            cost_basis = avg_cost * current_qty
            unrealized_pnl = mkt_val - cost_basis
        elif parsed:
            avg_cost = current_price
            cost_basis = mkt_val
            unrealized_pnl = Decimal("0")
        else:
            # Zero purchase fills (e.g., free airdropped stock token) -> cost basis 0 preserves cash conservation
            avg_cost = Decimal("0")
            cost_basis = Decimal("0")
            unrealized_pnl = mkt_val
    else:
        avg_cost = (pool_cost / pool_qty) if pool_qty > 0 else Decimal("0")
        cost_basis = Decimal("0")
        unrealized_pnl = Decimal("0")

    pnl_pct = ((unrealized_pnl / cost_basis) * Decimal("100")) if cost_basis > 0 else Decimal("0")
    net_pnl = realized_pnl + unrealized_pnl - total_fee_usd
    return {
        "net_qty_from_fills": pool_qty,
        "avg_cost_usd": avg_cost,
        "cost_basis_usd": cost_basis,
        "market_value_usd": mkt_val,
        "unrealized_pnl_usd": unrealized_pnl,
        "pnl_pct": pnl_pct,
        "realized_pnl_usd": realized_pnl,
        "total_fee_usd": total_fee_usd,
        "net_pnl_usd": net_pnl,
        "parsed_fills": parsed,
    }


def spot_token_holding(
    token: Mapping[str, str], qty: float, mv: float, *, venue_id: str, venue_name: str,
    venue_name_zh: str, spot_market: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the wallet-derived holding when the exchange supplies no cost basis."""
    cur_px = mv / qty
    return {
        "venue_id": venue_id,
        "venue_name": venue_name,
        "venue_name_zh": venue_name_zh,
        "symbol": token["token_symbol"],
        "name": token["name"],
        "channel": "1:1 Stock Token",
        "channel_zh": "1:1美股代币",
        **({"spot_market": spot_market} if spot_market else {}),
        "shares": round(qty, 6),
        "avg_cost_usd": round(cur_px, 2),
        "current_price_usd": round(cur_px, 2),
        "market_value_usd": round(mv, 2),
        "pnl_usd": 0.0,
        "pnl_pct": 0.0,
    }


def promote_spot_stock_tokens_from_wallets(
    bundle: "VenueLedgerBundle",
    *,
    adapter_id: str,
) -> int:
    """Promote 1:1 tokenized stock spot balances into `bundle.stocks.holdings` across any exchange/sub-account without double-counting."""
    source_rows = bundle.spot_wallet_balances or bundle.spot_holdings
    if not source_rows:
        return 0

    ex_code = (adapter_id or bundle.venue_id or "").strip().upper()
    existing_syms = {
        str(h.get("symbol") or "").strip().upper()
        for h in bundle.stocks.holdings
        if isinstance(h, Mapping)
    }

    agg: dict[str, list[Any]] = {}
    for row in source_rows:
        if not isinstance(row, Mapping):
            continue
        asset_u = str(row.get("asset") or "").strip().upper()
        if not asset_u or asset_u in existing_syms:
            continue
        qty = float(row.get("qty") or 0.0)
        raw_mv = row.get("market_value_usd")
        mv = float(raw_mv) if raw_mv is not None else 0.0
        if not isfinite(qty) or qty <= 0.0 or not isfinite(mv) or mv <= 0.0:
            continue
        if asset_u not in agg:
            t_info = classify_spot_stock_token(asset_u, exchange=ex_code)
            if t_info is None:
                continue
            agg[asset_u] = [0.0, 0.0, t_info]
        agg[asset_u][0] += qty
        agg[asset_u][1] += mv

    if not agg:
        return 0

    ex_card = bundle.exchange_card
    venue_id = str(bundle.venue_id or ex_card.get("venue_id") or adapter_id)
    venue_name = str(ex_card.get("display_name") or ex_code)
    venue_name_zh = str(ex_card.get("display_name_zh") or venue_name)

    spot_markets = {r["asset"]: r["spot_market"] for r in source_rows if r.get("spot_market")}
    promoted_rows: list[dict[str, Any]] = []
    promoted_syms: set[str] = set()
    promoted_mv_total = 0.0
    for asset_u, (qty, mv, t_info) in agg.items():
        if qty <= 0.0 or mv < 0.5:
            continue
        promoted_syms.add(asset_u)
        promoted_mv_total += round(mv, 2)
        promoted_rows.append(spot_token_holding(
            t_info, qty, mv, venue_id=venue_id, venue_name=venue_name, venue_name_zh=venue_name_zh,
            spot_market=spot_markets.get(asset_u),
        ))

    if not promoted_syms:
        return 0

    promoted_rows.sort(key=lambda r: float(r["market_value_usd"]), reverse=True)
    bundle.stocks.holdings = [*bundle.stocks.holdings, *promoted_rows]
    bundle.stocks.equity_usd = round(float(bundle.stocks.equity_usd or 0.0) + promoted_mv_total, 2)
    bundle.spot_wallet_balances = [
        r for r in bundle.spot_wallet_balances
        if str(r.get("asset") or "").strip().upper() not in promoted_syms
    ]
    bundle.spot_holdings = [
        r for r in bundle.spot_holdings
        if str(r.get("asset") or "").strip().upper() not in promoted_syms
    ]
    return len(promoted_rows)
