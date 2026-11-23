"""Credential dataclasses shared by migrated read-only exchange clients (zero personal defaults)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateManualCredentials:
    api_key: str
    api_secret: str
    base_url: str = "https://api.gateio.ws"
    proxy_url: str = ""


@dataclass(frozen=True)
class BinanceCredentials:
    api_key: str
    api_secret: str
    base_url: str = "https://api.binance.com"
    papi_base_url: str = "https://papi.binance.com"
    fapi_base_url: str = "https://fapi.binance.com"
    spot_base_url: str = "https://api.binance.com"
    proxy_url: str = ""

    def __post_init__(self) -> None:
        if self.base_url == "https://api.binance.com" and self.spot_base_url != "https://api.binance.com":
            object.__setattr__(self, "base_url", self.spot_base_url)


@dataclass(frozen=True)
class BitgetCredentials:
    api_key: str
    api_secret: str
    passphrase: str = ""
    api_passphrase: str = ""
    base_url: str = "https://api.bitget.com"
    proxy_url: str = ""

    def __post_init__(self) -> None:
        effective = self.api_passphrase or self.passphrase
        object.__setattr__(self, "passphrase", effective)
        object.__setattr__(self, "api_passphrase", effective)


@dataclass(frozen=True)
class MexcCredentials:
    api_key: str
    api_secret: str
    spot_base_url: str = "https://api.mexc.com"
    futures_base_url: str = "https://contract.mexc.com"
    contract_base_url: str = "https://contract.mexc.com"
    proxy_url: str = ""

    def __post_init__(self) -> None:
        if self.futures_base_url == "https://contract.mexc.com" and self.contract_base_url != "https://contract.mexc.com":
            object.__setattr__(self, "futures_base_url", self.contract_base_url)


@dataclass(frozen=True)
class EasiCoinCredentials:
    api_key: str
    api_secret: str
    base_url: str = "https://api.easicoin.io"
    proxy_url: str = ""
