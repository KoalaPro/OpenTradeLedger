"""Resolve public instrument artwork by listing and retain it independently of account data.

Resolution strategy (cached locally for 30 days):
1. Exact exchange contract/stock lookup on TradingView public catalog.
2. Cross-venue crypto fallback (Binance / OKX / Bybit / Gate swap & spot, crypto/ logoid only),
   including multiplier-prefix stripping (e.g. 1000PEPE_USDT -> PEPEUSDT).
3. Tokenized-equity / stock-perp fallback (e.g. NVDAX_USDT -> NVDA, AAPLX -> AAPL,
   DELLSTOCK_USDT -> DELL) on US stock markets (NASDAQ / NYSE / AMEX).
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
from pathlib import Path
import re
import struct
from tempfile import NamedTemporaryFile
import time

import httpx


DERIVATIVES = {"GATE", "BINANCE", "OKX", "BYBIT", "MEXC", "BITGET", "EASICOIN"}
CROSS_CRYPTO_VENUES = ("BINANCE", "OKX", "BYBIT", "GATE")
US_MARKETS = ("NASDAQ", "NYSE", "AMEX")
PAIR = re.compile(r"([A-Z0-9\u3400-\u9fff]{1,32})_([A-Z0-9]{2,12})")
STOCK = re.compile(r"[A-Z0-9]{1,12}(?:\.[A-Z0-9]{1,3})?")
LOGO_ID = re.compile(r"(?:[A-Za-z0-9_-]+/){0,2}[A-Za-z0-9_-]+")
MULTIPLIER_PREFIX = re.compile(r"^(?:1000000|100000|10000|1000|1M)([A-Z]{2,12})$")
PUBLIC_CATALOG = "https://scanner.tradingview.com/symbol"
PNG_PREFIX = b"\x89PNG\r\n\x1a\n"
logger = logging.getLogger(__name__)


def _stock_candidates(ticker: str) -> list[str]:
    """Derive possible underlying US equity tickers from tokenized/stock-perp symbols."""
    raw = ticker.upper().strip()
    out: list[str] = []
    for cand in (raw,):
        if cand and cand not in out:
            out.append(cand)
    if raw.endswith("STOCK") and len(raw) > 5:
        stripped = raw[:-5]
        if stripped not in out:
            out.append(stripped)
    elif raw.endswith("X") and len(raw) >= 4:
        # e.g. NVDAX -> NVDA, AAPLX -> AAPL, TSLAX -> TSLA, COINX -> COIN
        stripped = raw[:-1]
        if stripped not in out:
            out.append(stripped)
    elif raw.endswith("ON") and len(raw) >= 5:
        # e.g. Ondo tokenized equities NVDAON -> NVDA
        stripped = raw[:-2]
        if stripped not in out:
            out.append(stripped)
    return out


async def _listing(client: httpx.AsyncClient, venue: str, symbol: str, kind: str) -> str | None:
    response = await client.get(
        PUBLIC_CATALOG,
        params={
            "symbol": f"{venue}:{symbol}",
            "fields": "base_currency_logoid,logoid,type,exchange",
        },
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    row = response.json()
    if not isinstance(row, dict) or row.get("exchange") != venue or row.get("type") != kind:
        return None
    logo = row.get("base_currency_logoid") or row.get("logoid")
    return logo if isinstance(logo, str) and LOGO_ID.fullmatch(logo) else None


async def _resolve_us_stock(client: httpx.AsyncClient, symbol: str) -> str | None:
    for cand in _stock_candidates(symbol):
        for market in US_MARKETS:
            logo = await _listing(client, market, cand, "stock")
            if logo:
                return logo
    return None


async def _resolve_cross_crypto(client: httpx.AsyncClient, base: str, quote: str, skip_venue: str) -> str | None:
    bases = [base]
    m = MULTIPLIER_PREFIX.fullmatch(base)
    if m and m.group(1) not in bases:
        bases.append(m.group(1))
    quotes = [quote] + (["USDT"] if quote != "USDT" else [])
    for b in bases:
        for q in quotes:
            pair = b + q
            for fallback_venue in CROSS_CRYPTO_VENUES:
                if fallback_venue == skip_venue and b == base and q == quote:
                    continue
                logo = await _listing(client, fallback_venue, pair + ".P", "swap")
                if logo and logo.startswith("crypto/"):
                    return logo
            spot_logo = await _listing(client, "BINANCE", pair, "spot")
            if spot_logo and spot_logo.startswith("crypto/"):
                return spot_logo
    return None


async def _resolve(client: httpx.AsyncClient, venue: str, instrument: str) -> str | None:
    if venue == "US":
        return await _resolve_us_stock(client, instrument)
    if venue in US_MARKETS:
        for cand in _stock_candidates(instrument):
            logo = await _listing(client, venue, cand, "stock")
            if logo:
                return logo
        return None

    base, quote = instrument.split("_")
    pair = base + quote

    # 1. Exact venue contract lookup
    if venue != "EASICOIN":
        exact = await _listing(client, venue, pair + ".P", "swap")
        if exact:
            return exact

    # 2. EasiCoin verified public listing check (if live) before cross-venue crypto fallback
    if venue == "EASICOIN":
        try:
            response = await client.get(
                "https://api.easicoin.io/futures/public/v1/instruments",
                params={"symbol": pair},
            )
            if response.status_code == 200:
                payload = response.json()
                rows = (payload.get("data") or {}).get("list") if isinstance(payload, dict) else None
                if isinstance(rows, list) and any(
                    isinstance(item, dict) and item.get("symbol") == pair for item in rows
                ):
                    crypto_logo = await _resolve_cross_crypto(client, base, quote, skip_venue="EASICOIN")
                    if crypto_logo:
                        return crypto_logo
        except (httpx.HTTPError, ValueError):
            pass

    # 3. Cross-venue crypto fallback (handles MEXC/Bitget/new swaps & 1000PEPE-style multipliers)
    crypto_logo = await _resolve_cross_crypto(client, base, quote, skip_venue=venue)
    if crypto_logo:
        return crypto_logo

    # 4. Tokenized equity / stock perpetual fallback (e.g. NVDAX_USDT -> NASDAQ:NVDA, SNDK_USDT -> SNDK)
    return await _resolve_us_stock(client, base)


async def _download(client: httpx.AsyncClient, logo: str) -> bytes | None:
    for size in (96, 600):
        url = f"https://s3-symbol-logo.tradingview.com/{logo}--{size}.png"
        async with client.stream("GET", url) as response:
            if response.status_code in (403, 404):
                continue
            response.raise_for_status()
            if response.headers.get("content-type", "").split(";")[0].lower() != "image/png":
                continue
            image = bytearray()
            async for block in response.aiter_bytes(32768):
                image.extend(block)
                if len(image) > 1024 * 1024:
                    return None
            if (
                len(image) >= 45
                and image.startswith(PNG_PREFIX)
                and image[12:16] == b"IHDR"
                and all(0 < n <= 1024 for n in struct.unpack(">II", image[16:24]))
                and image[-8:-4] == b"IEND"
            ):
                return bytes(image)
    return None


class InstrumentLogos:
    def __init__(self, directory: Path, *, transport: httpx.AsyncBaseTransport | None = None):
        self.directory = directory
        self.transport = transport
        self.locks = [asyncio.Lock() for _ in range(8)]
        self.retry_after: OrderedDict[str, float] = OrderedDict()

    async def find(self, venue: str, instrument: str) -> Path | None:
        if venue in DERIVATIVES:
            valid = PAIR.fullmatch(instrument)
        elif venue in (*US_MARKETS, "US"):
            valid = STOCK.fullmatch(instrument)
        else:
            return None
        if not valid:
            return None

        key = f"{venue}-{instrument}"
        path = self.directory / (key + ".png")
        async with self.locks[hash(key) % len(self.locks)]:
            available = path.is_file()
            if available and time.time() - path.stat().st_mtime < 30 * 86400:
                return path
            if self.retry_after.get(key, 0) > time.monotonic():
                return path if available else None
            try:
                async with httpx.AsyncClient(timeout=5, transport=self.transport) as client:
                    logo = await _resolve(client, venue, instrument)
                    image = await _download(client, logo) if logo else None
                if image:
                    self.directory.mkdir(parents=True, exist_ok=True)
                    with NamedTemporaryFile(dir=self.directory, delete=False) as tmp:
                        tmp_path = Path(tmp.name)
                        try:
                            tmp.write(image)
                            tmp.flush()
                            tmp_path.replace(path)
                        finally:
                            tmp_path.unlink(missing_ok=True)
                    self.retry_after.pop(key, None)
                    return path
                delay = 6 * 3600
            except (httpx.HTTPError, ValueError, OSError) as exc:
                logger.debug("Instrument artwork temporarily unavailable for %s (%s)", key, type(exc).__name__)
                delay = 60
            self.retry_after[key] = time.monotonic() + delay
            self.retry_after.move_to_end(key)
            if len(self.retry_after) > 2048:
                self.retry_after.popitem(last=False)
            return path if available else None
