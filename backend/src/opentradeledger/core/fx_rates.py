"""Real-time fiat FX rate resolver with dual public sources, 1h TTL cache, and SQLite persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any

import httpx

from opentradeledger.core.store import LedgerStore

logger = logging.getLogger("opentradeledger.fx")
TZ_UTC8 = timezone(timedelta(hours=8))

SUPPORTED_CURRENCIES: tuple[str, ...] = ("USDT", "USD", "CNY", "GBP", "EUR", "HKD", "JPY")
CACHE_TTL_SEC = 3600

# Offline emergency reference fallback (only used if first boot has zero internet & empty cache)
FALLBACK_RATES: dict[str, float] = {
    "USDT": 1.0,
    "USD": 1.0,
    "CNY": 7.20,
    "GBP": 0.78,
    "EUR": 0.92,
    "HKD": 7.80,
    "JPY": 155.0,
}

PRIMARY_URL = "https://open.er-api.com/v6/latest/USD"
SECONDARY_URL = "https://api.frankfurter.dev/v1/latest?base=USD"


def _normalize_rates(raw_rates: dict[str, Any]) -> dict[str, float] | None:
    out: dict[str, float] = {"USDT": 1.0, "USD": 1.0}
    for code in ("CNY", "GBP", "EUR", "HKD", "JPY"):
        val = raw_rates.get(code)
        try:
            fval = float(val)
        except (TypeError, ValueError):
            return None
        if fval <= 0:
            return None
        out[code] = round(fval, 6)
    return out


class FxRateService:
    def __init__(
        self,
        store: LedgerStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        ttl_sec: int = CACHE_TTL_SEC,
    ) -> None:
        self.store = store
        self.transport = transport
        self.ttl_sec = ttl_sec
        self._mem_cache: dict[str, Any] | None = None

    def _load_cached(self) -> dict[str, Any] | None:
        if self._mem_cache is not None:
            return self._mem_cache
        saved = self.store.get_meta("fx_rates_cache", None)
        if isinstance(saved, dict) and isinstance(saved.get("rates"), dict):
            self._mem_cache = saved
            return saved
        return None

    async def _fetch_live(self) -> tuple[dict[str, float], str] | None:
        async with httpx.AsyncClient(timeout=6.0, transport=self.transport) as client:
            for url, source in ((PRIMARY_URL, "open.er-api.com"), (SECONDARY_URL, "frankfurter.dev")):
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    rates = _normalize_rates(data.get("rates") or {})
                    if rates is not None:
                        return rates, source
                except (httpx.HTTPError, ValueError) as exc:
                    logger.debug("FX fetch from %s failed: %s", source, exc)
        return None

    async def get_rates(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now_s = int(time.time())
        cached = self._load_cached()
        if (
            not force_refresh
            and cached is not None
            and (now_s - int(cached.get("fetched_sec") or 0)) < self.ttl_sec
        ):
            return {**cached, "stale": False}

        live = await self._fetch_live()
        if live is not None:
            rates, source = live
            payload = {
                "base": "USD",
                "source": source,
                "fetched_sec": now_s,
                "updated_at": datetime.fromtimestamp(now_s, tz=TZ_UTC8).strftime("%Y-%m-%d %H:%M"),
                "rates": rates,
                "stale": False,
            }
            self._mem_cache = payload
            self.store.set_meta("fx_rates_cache", payload)
            return payload

        if cached is not None:
            return {**cached, "stale": True}

        return {
            "base": "USD",
            "source": "offline-fallback",
            "fetched_sec": 0,
            "updated_at": "",
            "rates": dict(FALLBACK_RATES),
            "stale": True,
        }
