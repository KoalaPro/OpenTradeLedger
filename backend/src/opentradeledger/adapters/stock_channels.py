"""Multi-exchange US/HK stock (`正股`) & 1:1 tokenized stock (`现货股票代币`) normalization and cost-basis engine."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

TZ_BEIJING = timezone(timedelta(hours=8))

EXCHANGE_LABELS_ZH: dict[str, str] = {
    "GATE": "Gate",
    "BITGET": "Bitget",
    "BINANCE": "币安",
    "MEXC": "MEXC",
    "EASICOIN": "易币",
}

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
) -> dict[str, str] | None:
    """Identify whether a spot wallet asset is a 1:1 tokenized stock/ETF (`SPOT_TOKEN`)."""
    raw = (asset or "").strip()
    if not raw:
        return None
    asset_u = raw.upper()
    if asset_u in _KNOWN_CRYPTO_EXCLUSIONS:
        return None

    ex_u = (exchange or "").strip().upper()
    if dynamic_catalog and asset_u in dynamic_catalog:
        meta = dynamic_catalog[asset_u]
        und = str(meta.get("underlying") or "").strip().upper()
        if not und:
            if asset_u.endswith("ON") and len(asset_u) > 3:
                und = asset_u[:-2]
            elif asset_u.endswith(("X", "B")) and len(asset_u) > 2:
                und = asset_u[:-1]
            elif asset_u.startswith("R") and len(asset_u) > 2:
                und = asset_u[1:]
            else:
                und = asset_u
        name = str(meta.get("name") or US_EQUITY_TICKERS.get(und) or und)
        return {
            "token_symbol": asset_u,
            "underlying": und,
            "name": name,
            "series": str(meta.get("series") or "1:1 美股代币"),
            "channel": "SPOT_TOKEN",
            "channel_zh": "1:1 美股代币",
        }

    # 1. Ondo Global Markets (`<TICKER>ON`, e.g. NVDAON, AAPLON, SPYON)
    if asset_u.endswith("ON") and len(asset_u) >= 4:
        base = asset_u[:-2]
        if base in US_EQUITY_TICKERS:
            return {
                "token_symbol": asset_u,
                "underlying": base,
                "name": US_EQUITY_TICKERS[base],
                "series": "Ondo 美股代币",
                "channel": "SPOT_TOKEN",
                "channel_zh": "1:1 美股代币",
            }

    # 2. xStocks (`<TICKER>X`, e.g. NVDAX, TSLAX, AAPLX, QQQX)
    if asset_u.endswith("X") and len(asset_u) >= 3:
        base = asset_u[:-1]
        if base in US_EQUITY_TICKERS:
            return {
                "token_symbol": asset_u,
                "underlying": base,
                "name": US_EQUITY_TICKERS[base],
                "series": "xStocks 美股代币",
                "channel": "SPOT_TOKEN",
                "channel_zh": "1:1 美股代币",
            }

    # 3. Bitget Reality (`r<TICKER>` / `R<TICKER>`, e.g. rNVDA, rAAPL, rSPY)
    if ex_u == "BITGET" and asset_u.startswith("R") and len(asset_u) >= 3:
        base = asset_u[1:]
        if base in US_EQUITY_TICKERS:
            return {
                "token_symbol": asset_u,
                "underlying": base,
                "name": US_EQUITY_TICKERS[base],
                "series": "Bitget rStock",
                "channel": "SPOT_TOKEN",
                "channel_zh": "1:1 美股代币",
            }

    # 4. Binance bStocks (`<TICKER>B`, e.g. NVDAB, TSLAB, QQQB)
    if ex_u == "BINANCE" and asset_u.endswith("B") and len(asset_u) >= 3:
        base = asset_u[:-1]
        if base in US_EQUITY_TICKERS:
            return {
                "token_symbol": asset_u,
                "underlying": base,
                "name": US_EQUITY_TICKERS[base],
                "series": "币安 bStock",
                "channel": "SPOT_TOKEN",
                "channel_zh": "1:1 美股代币",
            }

    # 5. `<TICKER>STOCK` explicit suffix
    if asset_u.endswith("STOCK") and len(asset_u) > 5:
        base = asset_u[:-5]
        return {
            "token_symbol": asset_u,
            "underlying": base,
            "name": US_EQUITY_TICKERS.get(base, base),
            "series": "1:1 美股代币",
            "channel": "SPOT_TOKEN",
            "channel_zh": "1:1 美股代币",
        }

    return None


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


def build_multi_exchange_stock_ledger(
    *,
    gate_live_bundle: Mapping[str, Any],
    gate_stock_deals: Sequence[Mapping[str, Any]],
    gate_stock_transactions: Sequence[Mapping[str, Any]],
    bitget_live_bundle: Mapping[str, Any] | None = None,
    binance_live_bundle: Mapping[str, Any] | None = None,
    mexc_live_bundle: Mapping[str, Any] | None = None,
    mexc_transfers: Sequence[Mapping[str, Any]] = (),
    easicoin_live_bundle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate real US/HK stocks & 1:1 tokenized spot stocks across all 5 exchanges."""
    positions: list[dict[str, Any]] = []
    deals: list[dict[str, Any]] = []
    transactions: list[dict[str, Any]] = []

    spot_stock_val_by_ex: dict[str, Decimal] = defaultdict(Decimal)
    stock_net_pnl_by_ex: dict[str, Decimal] = defaultdict(Decimal)
    isolated_stock_equity_by_ex: dict[str, Decimal] = defaultdict(Decimal)
    platform_equity_by_ex: dict[str, Decimal] = defaultdict(Decimal)
    total_fees_usd = Decimal("0")
    total_position_pnl_usd = Decimal("0")

    def _append_spot_token_holding(
        *,
        ex_code: str,
        token_info: Mapping[str, str],
        qty: Decimal,
        live_val_usd: Decimal,
        live_px_usd: Decimal,
        raw_fills: Sequence[Mapping[str, Any]],
        quote_map: Mapping[str, Any] | None = None,
    ) -> None:
        nonlocal total_fees_usd, total_position_pnl_usd
        if qty <= 0:
            return
        px_use = live_px_usd if live_px_usd > 0 else ((live_val_usd / qty) if live_val_usd > 0 else Decimal("0"))
        val_use = live_val_usd if live_val_usd > 0 else (qty * px_use)
        tok_sym = token_info["token_symbol"]
        und_sym = token_info["underlying"]
        recon = reconstruct_spot_cost_basis(
            fills=raw_fills,
            current_qty=qty,
            current_price=px_use,
            token_symbol=tok_sym,
            quote_price_map=quote_map,
        )
        spot_stock_val_by_ex[ex_code] += val_use
        platform_equity_by_ex[ex_code] += val_use
        stock_net_pnl_by_ex[ex_code] += recon["net_pnl_usd"]
        total_fees_usd += recon["total_fee_usd"]
        total_position_pnl_usd += recon["unrealized_pnl_usd"]

        positions.append(
            {
                "platform": ex_code,
                "platform_zh": EXCHANGE_LABELS_ZH.get(ex_code, ex_code),
                "channel": "SPOT_TOKEN",
                "channel_zh": "1:1 美股代币",
                "symbol": tok_sym,
                "underlying_symbol": und_sym,
                "name": token_info["name"],
                "exchange": "US",
                "trade_status": "SPOT_1_TO_1",
                "shares": _f2(qty, 6),
                "avg_cost_usd": _f2(recon["avg_cost_usd"], 4),
                "current_price_usd": _f2(px_use, 4),
                "market_value_usd": _f2(val_use, 2),
                "pnl_usd": _f2(recon["unrealized_pnl_usd"], 2),
                "pnl_pct": _f2(recon["pnl_pct"], 2),
            }
        )
        for pf in recon["parsed_fills"]:
            ts_s = _norm_sec(pf["time_ms"])
            deals.append(
                {
                    "id": f"{ex_code.lower()}_sp_{pf['id']}",
                    "order_id": pf["order_id"],
                    "platform": ex_code,
                    "platform_zh": EXCHANGE_LABELS_ZH.get(ex_code, ex_code),
                    "channel_zh": "1:1 美股代币",
                    "side_zh": pf["side_zh"],
                    "symbol": tok_sym,
                    "underlying_symbol": und_sym,
                    "role_zh": f"{pf['side_zh']} · {pf['role_zh']}",
                    "price_usd": _f2(pf["price"], 4),
                    "shares": _f2(pf["qty"], 6),
                    "value_usd": _f2(pf["qty"] * pf["price"], 2),
                    "fee_usd": _f2(pf["fee_usd"], 4),
                    "_time_ms": pf["time_ms"],
                    "time_bj": _fmt_bj(ts_s) if ts_s > 0 else "—",
                }
            )

    # ── 1. Gate: Real Stock Sub-Account + Spot xStocks / Ondo ──────────────────
    gate_stk_assets = gate_live_bundle.get("stock_assets") or {}
    gate_wt_details = ((gate_live_bundle.get("wallet_total") or {}).get("details") or {})
    gate_real_eq = _dec(gate_stk_assets.get("equity") or (gate_wt_details.get("tradfi_spot") or {}).get("amount"))
    platform_equity_by_ex["GATE"] += gate_real_eq
    total_position_pnl_usd += _dec(gate_stk_assets.get("position_pnl"))

    for sp in gate_live_bundle.get("stock_positions") or []:
        vol = _dec(sp.get("volume"))
        if vol <= 0:
            continue
        cost_px = _dec(sp.get("avg_cost_price"))
        pos_pnl = _dec(sp.get("position_pnl"))
        cost_val = vol * cost_px
        mkt_val = cost_val + pos_pnl
        cur_px = (mkt_val / vol) if vol > 0 else cost_px
        mkt_code = str(sp.get("exchange") or "us").upper()
        chan_lbl = "港股正股" if mkt_code == "HK" else "美股正股"
        sym_g = str(sp.get("symbol") or "").strip().upper()
        positions.append(
            {
                "platform": "GATE",
                "platform_zh": "Gate",
                "channel": "REAL_STOCK",
                "channel_zh": chan_lbl,
                "symbol": sym_g,
                "underlying_symbol": sym_g,
                "name": str(sp.get("symbol_desc") or US_EQUITY_TICKERS.get(sym_g) or sym_g),
                "exchange": mkt_code,
                "trade_status": str(sp.get("trade_status") or ""),
                "shares": _f2(vol, 4),
                "avg_cost_usd": _f2(cost_px, 4),
                "current_price_usd": _f2(cur_px, 4),
                "market_value_usd": _f2(mkt_val, 2),
                "pnl_usd": _f2(pos_pnl, 2),
                "pnl_pct": _f2(_dec(sp.get("pnl_rate")), 2),
            }
        )

    for sd in gate_stock_deals:
        fee = abs(_dec(sd.get("fee")))
        px = _dec(sd.get("price"))
        amt = _dec(sd.get("amount") or sd.get("quantity"))
        total_fees_usd += fee
        ts_ms = _norm_ms(sd.get("time") or sd.get("create_time"))
        ts_d = _norm_sec(ts_ms)
        sym_sd = str(sd.get("symbol") or "").strip().upper()
        deals.append(
            {
                "id": str(sd.get("id") or f"gate_{ts_ms}"),
                "order_id": str(sd.get("order_id") or ""),
                "platform": "GATE",
                "platform_zh": "Gate",
                "channel_zh": "美股正股",
                "side_zh": "卖出" if str(sd.get("side") or "").lower() == "sell" else "买入",
                "symbol": sym_sd,
                "underlying_symbol": sym_sd,
                "role_zh": "限价 Maker" if str(sd.get("role") or "").lower() == "maker" else "市价 Taker",
                "price_usd": _f2(px, 4),
                "shares": _f2(amt, 4),
                "value_usd": _f2(_dec(sd.get("value")) or (px * amt), 2),
                "fee_usd": _f2(fee, 4),
                "_time_ms": ts_ms,
                "time_bj": _fmt_bj(ts_d) if ts_d > 0 else "—",
            }
        )

    for tx in gate_stock_transactions:
        ts_ms = _norm_ms(tx.get("time"))
        ts_t = _norm_sec(ts_ms)
        transactions.append(
            {
                "ref_id": str(tx.get("ref_id") or f"gate_tx_{ts_ms}"),
                "platform": "GATE",
                "platform_zh": "Gate",
                "channel_zh": "美股正股",
                "_time_ms": ts_ms,
                "time_bj": _fmt_bj(ts_t),
                "type": str(tx.get("type") or ""),
                "type_desc": str(tx.get("type_desc") or tx.get("type") or ""),
                "symbol": str(tx.get("symbol") or tx.get("asset") or "USDT"),
                "change": str(tx.get("change") or "0"),
                "unit": str(tx.get("unit_text") or tx.get("asset") or ""),
            }
        )

    gate_uni_bals = ((gate_live_bundle.get("unified") or {}).get("balances") or {})
    gate_sp_bundle = gate_live_bundle.get("spot_stock_bundle") or {}
    gate_sp_tickers = gate_sp_bundle.get("tickers") or {}
    gate_sp_trades = gate_sp_bundle.get("trades") or {}
    if isinstance(gate_uni_bals, Mapping):
        for ccy_k, b_obj in gate_uni_bals.items():
            if not isinstance(b_obj, Mapping):
                continue
            t_info = classify_spot_stock_token(str(ccy_k), exchange="GATE")
            if not t_info:
                continue
            qty_g = _dec(b_obj.get("available")) + _dec(b_obj.get("freeze") or b_obj.get("locked"))
            if qty_g <= 0:
                continue
            eq_g = _dec(b_obj.get("equity"))
            px_g = _dec(gate_sp_tickers.get(t_info["token_symbol"])) or ((eq_g / qty_g) if eq_g > 0 else Decimal("0"))
            val_g = eq_g if eq_g > 0 else (qty_g * px_g)
            _append_spot_token_holding(
                ex_code="GATE",
                token_info=t_info,
                qty=qty_g,
                live_val_usd=val_g,
                live_px_usd=px_g,
                raw_fills=gate_sp_trades.get(t_info["token_symbol"]) or [],
            )

    # ── 2. Bitget: Stock+ Real US Stocks + Spot rTokens / Ondo / xStocks ───────
    bg_lb = bitget_live_bundle or {}
    bg_sb = bg_lb.get("stock_bundle") or {}
    bg_tickers = bg_lb.get("spot_tickers") or {}
    bg_sp_acc = bg_sb.get("stockplus_account") or {}
    bg_sp_positions = bg_sb.get("stockplus_positions") or []
    bg_sp_execs = bg_sb.get("stockplus_executions") or []
    bg_sp_cfs = bg_sb.get("stockplus_cashflows") or []
    bg_sp_trs = bg_sb.get("stockplus_transfers") or []

    bg_pos_mkt_sum = Decimal("0")
    bg_pos_unr_sum = Decimal("0")
    for bp in bg_sp_positions:
        if not isinstance(bp, Mapping):
            continue
        vol_b = _dec(bp.get("qty") or bp.get("position") or bp.get("holdVol") or bp.get("availableQty") or bp.get("volume"))
        if vol_b <= 0:
            continue
        cost_b = _dec(bp.get("avgCostPrice") or bp.get("openAvgPrice") or bp.get("costPrice") or bp.get("avg_cost_price"))
        cur_b = _dec(bp.get("marketPrice") or bp.get("lastPrice") or bp.get("currentPrice"))
        mkt_b = _dec(bp.get("marketValue") or bp.get("positionValue")) or (vol_b * (cur_b or cost_b))
        if cur_b <= 0 and vol_b > 0:
            cur_b = mkt_b / vol_b
        unr_b = _dec(bp.get("unrealizedPnl") or bp.get("unrealisedPnl") or bp.get("positionPnl")) or (mkt_b - vol_b * cost_b)
        pct_b = _dec(bp.get("unrealizedPnlRate") or bp.get("pnlRate"))
        if pct_b == 0 and cost_b > 0:
            pct_b = (unr_b / (vol_b * cost_b)) * Decimal("100")
        bg_pos_mkt_sum += mkt_b
        bg_pos_unr_sum += unr_b
        sym_b = str(bp.get("symbol") or "").strip().upper()
        positions.append(
            {
                "platform": "BITGET",
                "platform_zh": "Bitget",
                "channel": "REAL_STOCK",
                "channel_zh": "美股正股",
                "symbol": sym_b,
                "underlying_symbol": sym_b,
                "name": str(bp.get("name") or US_EQUITY_TICKERS.get(sym_b) or sym_b),
                "exchange": "US",
                "trade_status": "STOCK_PLUS",
                "shares": _f2(vol_b, 4),
                "avg_cost_usd": _f2(cost_b, 4),
                "current_price_usd": _f2(cur_b, 4),
                "market_value_usd": _f2(mkt_b, 2),
                "pnl_usd": _f2(unr_b, 2),
                "pnl_pct": _f2(pct_b, 2),
            }
        )

    bg_sp_eq = Decimal("0")
    if isinstance(bg_sp_acc, Mapping) and bg_sp_acc:
        bg_sp_eq = _dec(
            bg_sp_acc.get("totalEquity")
            or bg_sp_acc.get("netLiquidationValue")
            or bg_sp_acc.get("usdtEquity")
            or bg_sp_acc.get("equity")
        )
        if bg_sp_eq <= 0:
            bg_sp_eq = _dec(bg_sp_acc.get("cashBalance") or bg_sp_acc.get("available")) + bg_pos_mkt_sum
    elif isinstance(bg_sp_acc, list):
        for item_a in bg_sp_acc:
            if isinstance(item_a, Mapping):
                bg_sp_eq += _dec(item_a.get("totalEquity") or item_a.get("equity") or item_a.get("cashBalance") or item_a.get("balance"))
    elif bg_pos_mkt_sum > 0:
        bg_sp_eq = bg_pos_mkt_sum

    bg_exec_fees = Decimal("0")
    for bex in bg_sp_execs:
        if not isinstance(bex, Mapping):
            continue
        pf = _extract_fill_fields(bex, quote_price_map=bg_tickers)
        bg_exec_fees += pf["fee_usd"]
        total_fees_usd += pf["fee_usd"]
        ts_s = _norm_sec(pf["time_ms"])
        sym_ex = str(bex.get("symbol") or "").strip().upper()
        deals.append(
            {
                "id": f"bg_sp_{pf['id']}",
                "order_id": pf["order_id"],
                "platform": "BITGET",
                "platform_zh": "Bitget",
                "channel_zh": "美股正股",
                "side_zh": pf["side_zh"],
                "symbol": sym_ex,
                "underlying_symbol": sym_ex,
                "role_zh": f"{pf['side_zh']} · Stock+",
                "price_usd": _f2(pf["price"], 4),
                "shares": _f2(pf["qty"], 4),
                "value_usd": _f2(pf["qty"] * pf["price"], 2),
                "fee_usd": _f2(pf["fee_usd"], 4),
                "_time_ms": pf["time_ms"],
                "time_bj": _fmt_bj(ts_s) if ts_s > 0 else "—",
            }
        )

    bg_div_sum = Decimal("0")
    for bcf in bg_sp_cfs:
        if not isinstance(bcf, Mapping):
            continue
        ts_ms = _norm_ms(bcf.get("cTime") or bcf.get("ts") or bcf.get("time"))
        amt_cf = _dec(bcf.get("amount") or bcf.get("change"))
        tp_cf = str(bcf.get("type") or bcf.get("bizType") or "CASH_FLOW").upper()
        if "DIV" in tp_cf:
            bg_div_sum += amt_cf
        transactions.append(
            {
                "ref_id": f"bg_cf_{bcf.get('id') or ts_ms}",
                "platform": "BITGET",
                "platform_zh": "Bitget",
                "channel_zh": "美股正股",
                "_time_ms": ts_ms,
                "time_bj": _fmt_bj(_norm_sec(ts_ms)),
                "type": tp_cf,
                "type_desc": "美股现金分红" if "DIV" in tp_cf else tp_cf,
                "symbol": str(bcf.get("symbol") or bcf.get("currency") or "USD"),
                "change": f"{amt_cf:+.2f}",
                "unit": str(bcf.get("currency") or "USD"),
            }
        )

    bg_tr_net_in = Decimal("0")
    has_bg_sp_tr = False
    for btr in bg_sp_trs:
        if not isinstance(btr, Mapping):
            continue
        amt_tr = abs(_dec(btr.get("amount") or btr.get("size")))
        if amt_tr <= 0:
            continue
        has_bg_sp_tr = True
        dir_tr = str(btr.get("direction") or btr.get("type") or btr.get("toType") or "IN").upper()
        is_out = dir_tr in ("OUT", "WITHDRAW", "TO_SPOT", "TO_UTA") or "OUT" in dir_tr
        bg_tr_net_in += (-amt_tr if is_out else amt_tr)
        ts_ms = _norm_ms(btr.get("cTime") or btr.get("ts") or btr.get("time"))
        transactions.append(
            {
                "ref_id": f"bg_tr_{btr.get('id') or ts_ms}",
                "platform": "BITGET",
                "platform_zh": "Bitget",
                "channel_zh": "美股正股",
                "_time_ms": ts_ms,
                "time_bj": _fmt_bj(_norm_sec(ts_ms)),
                "type": "STOCKPLUS_TRANSFER",
                "type_desc": "Stock+ 资金转出" if is_out else "转入 Stock+ 股票账户",
                "symbol": str(btr.get("coin") or btr.get("currency") or "USDC"),
                "change": f"{(-amt_tr if is_out else amt_tr):+.2f}",
                "unit": str(btr.get("coin") or btr.get("currency") or "USDC"),
            }
        )

    if bg_sp_eq > 0 or has_bg_sp_tr or bg_pos_unr_sum != 0:
        isolated_stock_equity_by_ex["BITGET"] += bg_sp_eq
        platform_equity_by_ex["BITGET"] += bg_sp_eq
        total_position_pnl_usd += bg_pos_unr_sum
        if has_bg_sp_tr:
            stock_net_pnl_by_ex["BITGET"] += (bg_sp_eq - bg_tr_net_in)
        else:
            stock_net_pnl_by_ex["BITGET"] += (bg_pos_unr_sum + bg_div_sum - bg_exec_fees)

    # Bitget Spot rTokens in UTA / Funding
    bg_spot_qty_val: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    bg_uta = bg_lb.get("uta_assets")
    uta_arr = (bg_uta.get("assets") or bg_uta.get("list") or []) if isinstance(bg_uta, Mapping) else (bg_uta if isinstance(bg_uta, list) else [])
    for ca in uta_arr:
        if not isinstance(ca, Mapping):
            continue
        c_nm = str(ca.get("coin") or "").strip()
        if not classify_spot_stock_token(c_nm, exchange="BITGET"):
            continue
        q_c = _dec(ca.get("equity") or ca.get("available") or ca.get("balance"))
        cu = c_nm.upper()
        px_c = _dec(bg_tickers.get(cu))
        v_c = _dec(ca.get("usdtEquity")) or (q_c * px_c)
        if q_c > 0:
            bg_spot_qty_val[cu][0] += q_c
            bg_spot_qty_val[cu][1] += v_c
    for fa_r in bg_lb.get("funding_assets") or []:
        if not isinstance(fa_r, Mapping):
            continue
        c_nm = str(fa_r.get("coin") or "").strip()
        if not classify_spot_stock_token(c_nm, exchange="BITGET"):
            continue
        q_f = _dec(fa_r.get("balance") or fa_r.get("available"))
        cu = c_nm.upper()
        v_f = q_f * _dec(bg_tickers.get(cu))
        if q_f > 0:
            bg_spot_qty_val[cu][0] += q_f
            bg_spot_qty_val[cu][1] += v_f

    bg_sp_fills_map = bg_sb.get("spot_stock_fills") or {}
    for cu, (q_tot, v_tot) in sorted(bg_spot_qty_val.items()):
        t_info = classify_spot_stock_token(cu, exchange="BITGET")
        if t_info and q_tot > 0:
            _append_spot_token_holding(
                ex_code="BITGET",
                token_info=t_info,
                qty=q_tot,
                live_val_usd=v_tot,
                live_px_usd=_dec(bg_tickers.get(cu)),
                raw_fills=bg_sp_fills_map.get(cu) or [],
                quote_map=bg_tickers,
            )

    # ── 3. Binance: `/sapi/v1/equity/*` Real US Stocks + Spot bStocks ──────────
    bn_lb = binance_live_bundle or {}
    bn_sb = bn_lb.get("stock_bundle") or {}
    bn_tickers = bn_lb.get("spot_tickers") or {}
    bn_dyn_cat: dict[str, dict[str, Any]] = {}
    for ta in bn_sb.get("tokenized_assets") or []:
        if isinstance(ta, Mapping):
            a_sym = str(ta.get("asset") or ta.get("tokenSymbol") or ta.get("symbol") or "").strip().upper()
            u_sym = str(ta.get("underlying") or ta.get("underlyingSymbol") or "").strip().upper()
            if a_sym:
                bn_dyn_cat[a_sym] = {
                    "underlying": u_sym or a_sym.rstrip("B"),
                    "name": str(ta.get("name") or US_EQUITY_TICKERS.get(u_sym, u_sym or a_sym)),
                    "series": "币安 bStock",
                }

    bn_eq_trades = bn_sb.get("equity_trades") or []
    bn_eq_quotes = bn_sb.get("equity_quotes") or {}
    bn_trades_by_sym: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for bt in bn_eq_trades:
        if isinstance(bt, Mapping) and bt.get("symbol"):
            bn_trades_by_sym[str(bt["symbol"]).strip().upper()].append(bt)

    bn_has_equity_wallet_row = any(
        any(k in str(w.get("walletName") or "").upper() for k in ("EQUITY", "STOCK"))
        and _dec(w.get("balance")) > 0
        for w in (bn_lb.get("wallet_balances") or [])
        if isinstance(w, Mapping)
    )
    for sym_bn, sym_trades in sorted(bn_trades_by_sym.items()):
        # Compute net shares from trade history
        tmp = reconstruct_spot_cost_basis(
            fills=sym_trades,
            current_qty=Decimal("0"),
            current_price=Decimal("0"),
            token_symbol=sym_bn,
            quote_price_map=bn_tickers,
        )
        net_sh = tmp["net_qty_from_fills"]
        q_obj = bn_eq_quotes.get(sym_bn) or {}
        live_q_px = _dec(
            q_obj.get("lastPrice") or q_obj.get("price") or q_obj.get("bidPrice") or q_obj.get("askPrice")
        )
        recon_bn = reconstruct_spot_cost_basis(
            fills=sym_trades,
            current_qty=net_sh,
            current_price=live_q_px if live_q_px > 0 else tmp["avg_cost_usd"],
            token_symbol=sym_bn,
            quote_price_map=bn_tickers,
        )
        stock_net_pnl_by_ex["BINANCE"] += recon_bn["net_pnl_usd"]
        total_fees_usd += recon_bn["total_fee_usd"]
        if net_sh > Decimal("1e-8"):
            mkt_bn = recon_bn["market_value_usd"]
            platform_equity_by_ex["BINANCE"] += mkt_bn
            total_position_pnl_usd += recon_bn["unrealized_pnl_usd"]
            if not bn_has_equity_wallet_row:
                isolated_stock_equity_by_ex["BINANCE"] += mkt_bn
            positions.append(
                {
                    "platform": "BINANCE",
                    "platform_zh": "币安",
                    "channel": "REAL_STOCK",
                    "channel_zh": "美股正股",
                    "symbol": sym_bn,
                    "underlying_symbol": sym_bn,
                    "name": US_EQUITY_TICKERS.get(sym_bn, sym_bn),
                    "exchange": "US",
                    "trade_status": "BINANCE_EQUITY",
                    "shares": _f2(net_sh, 4),
                    "avg_cost_usd": _f2(recon_bn["avg_cost_usd"], 4),
                    "current_price_usd": _f2(live_q_px if live_q_px > 0 else recon_bn["avg_cost_usd"], 4),
                    "market_value_usd": _f2(mkt_bn, 2),
                    "pnl_usd": _f2(recon_bn["unrealized_pnl_usd"], 2),
                    "pnl_pct": _f2(recon_bn["pnl_pct"], 2),
                }
            )
        for pf in recon_bn["parsed_fills"]:
            ts_s = _norm_sec(pf["time_ms"])
            deals.append(
                {
                    "id": f"bn_eq_{pf['id']}",
                    "order_id": pf["order_id"],
                    "platform": "BINANCE",
                    "platform_zh": "币安",
                    "channel_zh": "美股正股",
                    "side_zh": pf["side_zh"],
                    "symbol": sym_bn,
                    "underlying_symbol": sym_bn,
                    "role_zh": f"{pf['side_zh']} · 币安正股",
                    "price_usd": _f2(pf["price"], 4),
                    "shares": _f2(pf["qty"], 4),
                    "value_usd": _f2(pf["qty"] * pf["price"], 2),
                    "fee_usd": _f2(pf["fee_usd"], 4),
                    "_time_ms": pf["time_ms"],
                    "time_bj": _fmt_bj(ts_s) if ts_s > 0 else "—",
                }
            )

    bn_sp_trades_map = bn_sb.get("spot_stock_trades") or {}
    for sb_bn in bn_lb.get("spot_balances") or []:
        if not isinstance(sb_bn, Mapping):
            continue
        a_bn = str(sb_bn.get("asset") or "").strip().upper()
        q_bn = _dec(sb_bn.get("free")) + _dec(sb_bn.get("locked"))
        if q_bn <= 0:
            continue
        t_info = classify_spot_stock_token(a_bn, exchange="BINANCE", dynamic_catalog=bn_dyn_cat)
        if not t_info:
            continue
        px_bn = _dec(bn_tickers.get(a_bn))
        _append_spot_token_holding(
            ex_code="BINANCE",
            token_info=t_info,
            qty=q_bn,
            live_val_usd=q_bn * px_bn,
            live_px_usd=px_bn,
            raw_fills=bn_sp_trades_map.get(a_bn) or [],
            quote_map=bn_tickers,
        )

    # ── 4. MEXC: RealStocks Transfer Conservation + Spot Tokenized Stocks ──────
    mx_lb = mexc_live_bundle or {}
    mx_sb = mx_lb.get("stock_bundle") or {}
    mx_tickers = mx_lb.get("spot_tickers") or {}
    mx_dyn_cat = mx_sb.get("tokenized_catalog") if isinstance(mx_sb.get("tokenized_catalog"), Mapping) else {}
    mx_sp_trades_map = mx_sb.get("spot_stock_trades") or {}

    for sb_mx in mx_lb.get("spot_balances") or []:
        if not isinstance(sb_mx, Mapping):
            continue
        a_mx = str(sb_mx.get("asset") or "").strip().upper()
        q_mx = _dec(sb_mx.get("free")) + _dec(sb_mx.get("locked"))
        if q_mx <= 0:
            continue
        t_info = classify_spot_stock_token(a_mx, exchange="MEXC", dynamic_catalog=mx_dyn_cat)
        if not t_info:
            continue
        px_mx = _dec(mx_tickers.get(a_mx))
        _append_spot_token_holding(
            ex_code="MEXC",
            token_info=t_info,
            qty=q_mx,
            live_val_usd=q_mx * px_mx,
            live_px_usd=px_mx,
            raw_fills=mx_sp_trades_map.get(a_mx) or [],
            quote_map=mx_tickers,
        )

    mx_rs_net_in = Decimal("0")
    for mtr in mexc_transfers:
        if not isinstance(mtr, Mapping):
            continue
        if str(mtr.get("_source") or "") != "universal_transfer":
            continue
        st_m = str(mtr.get("status") or "SUCCESS").upper()
        if st_m not in ("SUCCESS", "COMPLETED", "CONFIRMED", ""):
            continue
        from_acc = str(mtr.get("fromAccountType") or "").strip().upper()
        to_acc = str(mtr.get("toAccountType") or "").strip().upper()
        if from_acc not in _STOCK_SUBACCOUNT_NAMES and to_acc not in _STOCK_SUBACCOUNT_NAMES:
            continue
        amt_m = abs(_dec(mtr.get("amount")))
        cur_m = str(mtr.get("asset") or mtr.get("coin") or "USDT").strip().upper()
        px_m = Decimal("1") if cur_m in ("USDT", "USDC", "USDE", "FDUSD", "USD") else _dec(mx_tickers.get(cur_m))
        val_m = amt_m * px_m
        if val_m <= 0:
            continue
        is_into_stock = to_acc in _STOCK_SUBACCOUNT_NAMES and from_acc not in _STOCK_SUBACCOUNT_NAMES
        mx_rs_net_in += (val_m if is_into_stock else -val_m)
        ts_ms = _norm_ms(mtr.get("_time_ms") or mtr.get("timestamp"))
        transactions.append(
            {
                "ref_id": f"mx_rs_{mtr.get('tranId') or ts_ms}",
                "platform": "MEXC",
                "platform_zh": "MEXC",
                "channel_zh": "美股正股",
                "_time_ms": ts_ms,
                "time_bj": _fmt_bj(_norm_sec(ts_ms)),
                "type": "REALSTOCKS_TRANSFER",
                "type_desc": "划入 RealStocks 美股账户" if is_into_stock else "RealStocks 美股账户转回",
                "symbol": cur_m,
                "change": f"{(val_m if is_into_stock else -val_m):+.2f}",
                "unit": cur_m,
            }
        )

    mx_live_rs_eq = _dec((mx_sb.get("realstocks_account") or {}).get("totalEquity"))
    if mx_live_rs_eq > 0:
        isolated_stock_equity_by_ex["MEXC"] += mx_live_rs_eq
        platform_equity_by_ex["MEXC"] += mx_live_rs_eq
        if mx_rs_net_in > 0:
            stock_net_pnl_by_ex["MEXC"] += (mx_live_rs_eq - mx_rs_net_in)
    elif mx_rs_net_in > Decimal("0.01"):
        isolated_stock_equity_by_ex["MEXC"] += mx_rs_net_in
        platform_equity_by_ex["MEXC"] += mx_rs_net_in
        positions.append(
            {
                "platform": "MEXC",
                "platform_zh": "MEXC",
                "channel": "REAL_STOCK",
                "channel_zh": "美股正股",
                "symbol": "REALSTOCKS",
                "underlying_symbol": "SPY",
                "name": "MEXC RealStocks 美股账户在途资产",
                "exchange": "US",
                "trade_status": "IN_TRANSIT",
                "shares": _f2(mx_rs_net_in, 2),
                "avg_cost_usd": 1.0,
                "current_price_usd": 1.0,
                "market_value_usd": _f2(mx_rs_net_in, 2),
                "pnl_usd": 0.0,
                "pnl_pct": 0.0,
            }
        )

    # ── 5. EasiCoin: Spot / Funding xStocks / Ondo ─────────────────────────────
    ec_lb = easicoin_live_bundle or {}
    ec_sb = ec_lb.get("stock_bundle") or {}
    ec_px_map: dict[str, Decimal] = {}
    for tk in ec_lb.get("tickers") or []:
        if isinstance(tk, Mapping):
            su = str(tk.get("symbol") or "").upper()
            if su.endswith("USDT") and tk.get("last_price"):
                ec_px_map[su[:-4]] = _dec(tk.get("last_price"))

    ec_qty_by_tok: dict[str, Decimal] = defaultdict(Decimal)
    for fnd in ec_lb.get("funding_balances") or []:
        if isinstance(fnd, Mapping):
            c_f = str(fnd.get("coin") or "").strip().upper()
            q_f = _dec(fnd.get("total_equity") or fnd.get("available_balance"))
            if q_f > 0 and classify_spot_stock_token(c_f, exchange="EASICOIN"):
                ec_qty_by_tok[c_f] += q_f
    for spb in ec_lb.get("spot_balances") or []:
        if isinstance(spb, Mapping):
            c_s = str(spb.get("token_name") or spb.get("coin") or "").strip().upper()
            q_s = _dec(spb.get("balance") or spb.get("available"))
            if q_s > 0 and classify_spot_stock_token(c_s, exchange="EASICOIN"):
                ec_qty_by_tok[c_s] += q_s

    ec_sp_fills = ec_sb.get("spot_stock_fills") or {}
    for c_ec, q_ec in sorted(ec_qty_by_tok.items()):
        t_info = classify_spot_stock_token(c_ec, exchange="EASICOIN")
        if t_info and q_ec > 0:
            px_ec = ec_px_map.get(c_ec, Decimal("0"))
            _append_spot_token_holding(
                ex_code="EASICOIN",
                token_info=t_info,
                qty=q_ec,
                live_val_usd=q_ec * px_ec,
                live_px_usd=px_ec,
                raw_fills=ec_sp_fills.get(c_ec) or [],
                quote_map=ec_px_map,
            )

    deals.sort(key=lambda x: int(x.get("_time_ms") or 0), reverse=True)
    for d in deals:
        d.pop("_time_ms", None)
    transactions.sort(key=lambda x: int(x.get("_time_ms") or 0), reverse=True)
    for tx in transactions:
        tx.pop("_time_ms", None)

    def _fmt_pct_clean(v: Decimal) -> str:
        fv = _f2(v, 3)
        if fv == int(fv):
            return f"{int(fv)}%"
        s = f"{fv:.3f}".rstrip("0").rstrip(".")
        return f"{s}%"

    platform_fee_rates: list[dict[str, Any]] = []

    # 1) MEXC live Tokenized Stocks fee rate (`exchangeInfo` + observed fills fallback)
    mx_fr_raw = mx_sb.get("spot_stock_fee_rate") if isinstance(mx_sb.get("spot_stock_fee_rate"), Mapping) else {}
    mx_obs_not = sum((_dec(d.get("value_usd")) for d in deals if d.get("platform") == "MEXC"), Decimal("0"))
    mx_obs_fee = sum((_dec(d.get("fee_usd")) for d in deals if d.get("platform") == "MEXC"), Decimal("0"))
    mx_obs_pct = (mx_obs_fee / mx_obs_not * Decimal("100")) if mx_obs_not > 0 else None
    has_mx_fr = bool(mx_fr_raw and (mx_fr_raw.get("makerCommission") is not None or mx_fr_raw.get("takerCommission") is not None))
    if has_mx_fr or mx_obs_pct is not None:
        mx_m_pct = _dec(mx_fr_raw.get("makerCommission")) * Decimal("100") if has_mx_fr else (mx_obs_pct or Decimal("0"))
        mx_t_pct = _dec(mx_fr_raw.get("takerCommission")) * Decimal("100") if has_mx_fr else (mx_obs_pct or Decimal("0"))
        mx_sum = (
            f"MEXC {_fmt_pct_clean(mx_m_pct)}"
            if mx_m_pct == mx_t_pct
            else f"MEXC 挂{_fmt_pct_clean(mx_m_pct)}/吃{_fmt_pct_clean(mx_t_pct)}"
        )
        platform_fee_rates.append(
            {
                "exchange": "MEXC",
                "exchange_zh": "MEXC",
                "maker_fee_pct": _f2(mx_m_pct, 3),
                "taker_fee_pct": _f2(mx_t_pct, 3),
                "summary_zh": mx_sum,
            }
        )

    # 2) Bitget live rStock fee rate (`/api/v3/account/fee-rate`) + observed Stock+ execution rate
    bg_fr_raw = bg_sb.get("spot_stock_fee_rate") if isinstance(bg_sb.get("spot_stock_fee_rate"), Mapping) else {}
    bg_exec_notional = sum(
        (_dec(d.get("value_usd")) for d in deals if d.get("platform") == "BITGET" and d.get("channel_zh") == "美股正股"),
        Decimal("0"),
    )
    bg_obs_real_pct = (bg_exec_fees / bg_exec_notional * Decimal("100")) if bg_exec_notional > 0 else None
    has_bg_fr = bool(bg_fr_raw and (bg_fr_raw.get("makerFeeRate") is not None or bg_fr_raw.get("takerFeeRate") is not None))
    if has_bg_fr or bg_obs_real_pct is not None:
        bg_m_pct = _dec(bg_fr_raw.get("makerFeeRate")) * Decimal("100") if has_bg_fr else (bg_obs_real_pct or Decimal("0"))
        bg_t_pct = _dec(bg_fr_raw.get("takerFeeRate")) * Decimal("100") if has_bg_fr else (bg_obs_real_pct or Decimal("0"))
        bg_parts: list[str] = []
        if has_bg_fr:
            bg_parts.append(
                f"rStock {_fmt_pct_clean(bg_m_pct)}"
                if bg_m_pct == bg_t_pct
                else f"rStock 挂{_fmt_pct_clean(bg_m_pct)}/吃{_fmt_pct_clean(bg_t_pct)}"
            )
        if bg_obs_real_pct is not None:
            bg_parts.append(f"Stock+实测{_fmt_pct_clean(bg_obs_real_pct)}")
        bg_entry: dict[str, Any] = {
            "exchange": "BITGET",
            "exchange_zh": "Bitget",
            "maker_fee_pct": _f2(bg_m_pct, 3),
            "taker_fee_pct": _f2(bg_t_pct, 3),
            "summary_zh": f"Bitget {' · '.join(bg_parts)}",
        }
        if bg_obs_real_pct is not None:
            bg_entry["real_stock_fee_pct"] = _f2(bg_obs_real_pct, 4)
        platform_fee_rates.append(bg_entry)

    # 3) Gate live VIP stock fee rate (`/stock/fee-rate` matched by live `account_detail.tier`, fallback `wallet_fee`)
    gate_tier = int((gate_live_bundle.get("account_detail") or {}).get("tier") or 0)
    gate_sfr_list = gate_live_bundle.get("stock_fee_rates") or []
    gate_matched_fr = next(
        (r for r in gate_sfr_list if isinstance(r, Mapping) and int(r.get("vip_level") or -1) == gate_tier),
        None,
    )
    gate_wf = gate_live_bundle.get("wallet_fee") or {}
    if isinstance(gate_matched_fr, Mapping) and (
        gate_matched_fr.get("maker_fee") is not None or gate_matched_fr.get("taker_fee") is not None
    ):
        g_m_pct = _dec(gate_matched_fr.get("maker_fee")) * Decimal("100")
        g_t_pct = _dec(gate_matched_fr.get("taker_fee")) * Decimal("100")
    elif isinstance(gate_wf, Mapping) and (gate_wf.get("maker_fee") is not None or gate_wf.get("taker_fee") is not None):
        g_m_pct = _dec(gate_wf.get("maker_fee")) * Decimal("100")
        g_t_pct = _dec(gate_wf.get("taker_fee")) * Decimal("100")
    else:
        g_m_pct = None
        g_t_pct = None
    if g_m_pct is not None and g_t_pct is not None:
        g_sum = (
            f"Gate {_fmt_pct_clean(g_m_pct)}"
            if g_m_pct == g_t_pct
            else f"Gate 挂{_fmt_pct_clean(g_m_pct)}/吃{_fmt_pct_clean(g_t_pct)}"
        )
        platform_fee_rates.append(
            {
                "exchange": "GATE",
                "exchange_zh": "Gate",
                "maker_fee_pct": _f2(g_m_pct, 3),
                "taker_fee_pct": _f2(g_t_pct, 3),
                "summary_zh": g_sum,
            }
        )

    # 4) Binance live Spot bStock commission rate (`/api/v3/account` + `bnb_burn`) + observed Equity fill rate
    bn_cr_raw = bn_sb.get("spot_commission_rates") if isinstance(bn_sb.get("spot_commission_rates"), Mapping) else {}
    bn_eq_notional = sum(
        (_dec(d.get("value_usd")) for d in deals if d.get("platform") == "BINANCE" and d.get("channel_zh") == "美股正股"),
        Decimal("0"),
    )
    bn_eq_fees = sum(
        (_dec(d.get("fee_usd")) for d in deals if d.get("platform") == "BINANCE" and d.get("channel_zh") == "美股正股"),
        Decimal("0"),
    )
    bn_obs_real_pct = (bn_eq_fees / bn_eq_notional * Decimal("100")) if bn_eq_notional > 0 else None
    has_bn_cr = bool(bn_cr_raw and (bn_cr_raw.get("maker") is not None or bn_cr_raw.get("taker") is not None))
    if has_bn_cr or bn_obs_real_pct is not None:
        bn_m_pct = _dec(bn_cr_raw.get("maker")) * Decimal("100") if has_bn_cr else (bn_obs_real_pct or Decimal("0"))
        bn_t_pct = _dec(bn_cr_raw.get("taker")) * Decimal("100") if has_bn_cr else (bn_obs_real_pct or Decimal("0"))
        bn_bnb_on = bool((bn_lb.get("bnb_burn") or {}).get("spotBNBBurn"))
        bn_lbl = (
            f"币安 {_fmt_pct_clean(bn_m_pct)}"
            if bn_m_pct == bn_t_pct
            else f"币安 挂{_fmt_pct_clean(bn_m_pct)}/吃{_fmt_pct_clean(bn_t_pct)}"
        )
        if bn_bnb_on:
            bn_lbl += " (BNB抵扣)"
        bn_entry: dict[str, Any] = {
            "exchange": "BINANCE",
            "exchange_zh": "币安",
            "maker_fee_pct": _f2(bn_m_pct, 3),
            "taker_fee_pct": _f2(bn_t_pct, 3),
            "spot_bnb_burn": bn_bnb_on,
            "summary_zh": bn_lbl,
        }
        if bn_obs_real_pct is not None:
            bn_entry["real_stock_fee_pct"] = _f2(bn_obs_real_pct, 4)
        platform_fee_rates.append(bn_entry)

    combined_stock_equity_usd = sum(platform_equity_by_ex.values(), Decimal("0"))
    platform_breakdown = [
        {
            "exchange": ex_k,
            "exchange_zh": EXCHANGE_LABELS_ZH.get(ex_k, ex_k),
            "equity_usd": _f2(platform_equity_by_ex[ex_k], 2),
        }
        for ex_k in ("GATE", "BITGET", "BINANCE", "MEXC", "EASICOIN")
        if platform_equity_by_ex.get(ex_k, Decimal("0")) > Decimal("0.005")
    ]

    return {
        "positions": positions,
        "deals": deals,
        "transactions": transactions,
        "combined_stock_equity_usd": combined_stock_equity_usd,
        "total_position_pnl_usd": total_position_pnl_usd,
        "total_fees_usd": total_fees_usd,
        "spot_stock_val_by_ex": dict(spot_stock_val_by_ex),
        "stock_net_pnl_by_ex": dict(stock_net_pnl_by_ex),
        "isolated_stock_equity_by_ex": dict(isolated_stock_equity_by_ex),
        "platform_breakdown": platform_breakdown,
        "platform_fee_rates": platform_fee_rates,
    }
