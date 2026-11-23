"""Base protocol and metadata for pluggable read-only exchange adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from opentradeledger.security.vault import ExchangeCredentialRecord


class ExchangeCapabilities(BaseModel):
    futures: bool = True
    rebates: bool = True
    token_discount: bool = False
    copy_trading: bool = False
    stocks: bool = False
    tokenized_stocks: bool = False


class AdapterMeta(BaseModel):
    venue_id: str
    display_name: str
    display_name_zh: str
    color_token: str
    requires_passphrase: bool = False
    capabilities: ExchangeCapabilities = ExchangeCapabilities()
    docs_url: str = ""
    api_base_url: str = ""


@dataclass(frozen=True)
class SimpleExchangeCredentials:
    api_key: str
    api_secret: str
    passphrase: str = ""
    base_url: str = ""
    spot_base_url: str = ""
    futures_base_url: str = ""
    papi_base_url: str = ""
    fapi_base_url: str = ""
    contract_base_url: str = ""
    proxy_url: str = ""


class BaseExchangeAdapter(ABC):
    """Standard plugin interface for read-only exchange integrations.

    To add a new exchange (e.g. Hyperliquid, Kraken, KuCoin), implement this class
    and register an instance in `AdapterRegistry` — zero frontend changes required.
    """

    meta: AdapterMeta

    @abstractmethod
    def create_client(self, cred: ExchangeCredentialRecord) -> Any:
        """Instantiate the exchange's strictly GET-only API client."""

    @abstractmethod
    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        """Verify read-only connectivity via a safe GET endpoint."""
