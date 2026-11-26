"""Base protocol, standardized ledger bundles, and metadata for independent read-only exchange adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from pydantic import BaseModel

from opentradeledger.core.account_partition import AccountIdentityError
from opentradeledger.security.vault import ExchangeCredentialRecord

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore


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
    auth_mode: str = "api_key"  # "api_key" | "wallet_address"
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


@dataclass
class VenueSyncOutcome:
    equity_usd: float
    has_open_activity: bool
    fp_before: tuple[int, ...]
    fp_after: tuple[int, ...]
    new_closed_delta: int = 0
    mode: str = "venue_sync"
    warnings: list[str] = field(default_factory=list)


@dataclass
class VenueCopyBundle:
    has_copy_trading: bool = False
    invested_usd: float = 0.0
    active_equity_usd: float = 0.0
    book_pnl_usd: float = 0.0
    rebate_usd: float = 0.0
    combined_pnl_usd: float = 0.0
    platform: dict[str, Any] | None = None
    transfers: list[dict[str, Any]] = field(default_factory=list)
    rebates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VenueStockBundle:
    equity_usd: float = 0.0
    unrealized_pnl_usd: float = 0.0
    non_perp_fees_usd: float = 0.0
    stock_rebates_usd: float = 0.0
    maker_fee_pct: float = 0.0
    taker_fee_pct: float = 0.0
    venue_fee_rate: dict[str, Any] | None = None
    holdings: list[dict[str, Any]] = field(default_factory=list)
    cash_ledger: list[dict[str, Any]] = field(default_factory=list)
    perpetuals: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VenueLedgerBundle:
    venue_id: str
    exchange_card: dict[str, Any]
    trades: list[dict[str, Any]]
    open_positions: list[dict[str, Any]]
    spot_holdings: list[dict[str, Any]]
    reconcile_streams: list[dict[str, Any]]
    daily_pnl: dict[str, Decimal] = field(default_factory=dict)
    daily_components: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    copy_trading: VenueCopyBundle = field(default_factory=VenueCopyBundle)
    stocks: VenueStockBundle = field(default_factory=VenueStockBundle)
    fee_context: dict[str, Any] = field(default_factory=dict)
    spot_wallet_balances: list[dict[str, Any]] = field(default_factory=list)


class BaseExchangeAdapter(ABC):
    """Standard independent interface for read-only exchange integrations.

    Every exchange implements this identical contract with its own isolated
    `client.py`, `sync.py`, and `accounting.py`, storing data strictly inside
    its assigned account partition.
    """

    meta: AdapterMeta

    @abstractmethod
    def create_client(self, cred: ExchangeCredentialRecord) -> Any:
        """Instantiate the exchange's strictly read-only API client."""

    @abstractmethod
    async def test_connection(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        """Verify read-only connectivity and optionally report `remote_uid` & `account_role`."""

    async def probe_identity(self, cred: ExchangeCredentialRecord) -> dict[str, Any]:
        """Best-effort read-only identity check (`remote_uid`, `account_role`, `subaccounts`)."""
        try:
            info = await self.test_connection(cred)
            return info if isinstance(info, dict) else {}
        except AccountIdentityError:
            raise
        except Exception:
            return {}

    async def sync_venue(
        self,
        client: Any,
        store: "LedgerStore",
        *,
        now_s: int,
        force_full: bool = False,
    ) -> VenueSyncOutcome:
        """Pull incremental or full API records into this venue's isolated SQLite partition."""
        return VenueSyncOutcome(
            equity_usd=0.0,
            has_open_activity=False,
            fp_before=(0,),
            fp_after=(0,),
            new_closed_delta=0,
            mode="custom_noop",
        )

    def has_venue_data(self, store: "LedgerStore") -> bool:
        """Return True if this venue has cached live/historical data in its partition."""
        return False

    def extract_spot_prices(self, store: "LedgerStore") -> dict[str, float]:
        """Return latest spot USD tickers observed by this venue."""
        return {}

    def build_venue_ledger(
        self,
        store: "LedgerStore",
        *,
        journals: Mapping[str, Mapping[str, Any]],
        now_s: float,
        global_spot_prices: Mapping[str, float] | None = None,
    ) -> VenueLedgerBundle | None:
        """Compute this venue's self-contained closed-loop ledger (`0.00` residual)."""
        return None

    def build_fee_matrix_row(
        self,
        bundle: VenueLedgerBundle,
        range_trades: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        """Compute this venue's Maker/Taker bps, rebate/discount relief, and Maker savings for `range_trades`."""
        return None

    def build_range_reconcile_streams(
        self,
        bundle: VenueLedgerBundle,
        *,
        period_label_zh: str,
        comp_sums: Mapping[str, Decimal],
        range_trades: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build sub-range (`7d`, `30d`, `YYYY-MM`) reconciliation streams for this venue."""
        items: list[dict[str, Any]] = []
        for s in bundle.reconcile_streams:
            k = str(s["key"])
            if k in comp_sums:
                amt = round(float(comp_sums[k]), 2)
            elif k.endswith("_futures") or k == "manual_futures":
                amt = round(sum(float(t["net_pnl_usd"]) for t in range_trades), 2)
            else:
                amt = 0.0
            if abs(amt) < 0.005 and not (k.endswith("_futures") or k == "manual_futures"):
                continue
            item_out: dict[str, Any] = {
                "key": k,
                "label_zh": str(s.get("title_zh") or s.get("label_zh") or k),
                "detail_zh": f"{period_label_zh} {s.get('title_zh') or s.get('label_zh') or ''}".strip(),
                "amount_usd": amt,
            }
            for meta_k in ("account_id", "account_display", "base_exchange_en"):
                if s.get(meta_k):
                    item_out[meta_k] = s[meta_k]
            items.append(item_out)
        return items
