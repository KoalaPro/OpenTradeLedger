"""Base protocol, standardized ledger bundles, and metadata for independent read-only exchange adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from pydantic import BaseModel

from opentradeledger.core.account_partition import AccountIdentityError
from opentradeledger.core.venue_health import is_transient_upstream_error
from opentradeledger.security.vault import ExchangeCredentialRecord

if TYPE_CHECKING:
    from opentradeledger.core.store import LedgerStore

_SNAPSHOT_HISTORY_FIELDS: tuple[str, ...] = (
    "closed_pnl",
    "positions_history",
    "history_positions",
    "executions",
    "fills",
    "matches",
    "deposits",
    "internal_deposits",
    "withdrawals",
    "ledger_updates",
    "tx_logs",
    "bills",
    "asset_bills",
    "tx_history",
    "income",
    "financials",
    "copy_history",
)

_ARCHIVE_HISTORY_STREAMS: tuple[str, ...] = (
    "futures_closes",
    "futures_trades",
    "futures_orders",
    "partner_rebates",
    "spot_account_book",
    "stock_orders",
    "stock_deals",
    "stock_transactions",
    "closed_pnl",
    "fills",
    "orders",
    "transfers",
    "income",
    "cashflows",
    "positions_history",
    "bills",
    "deals",
    "funding",
)


def _clean_uid_candidate(val: Any) -> str:
    if val is None or isinstance(val, bool):
        return ""
    s = str(val).strip()
    if not s or s.lower() in {"0", "null", "none", "nan"}:
        return ""
    return s


def _norm_fp_val(val: Any) -> str:
    if val is None or isinstance(val, bool):
        return ""
    s = str(val).strip()
    if not s:
        return ""
    try:
        d = Decimal(s)
        if d.is_finite():
            txt = f"{d:f}"
            if "." in txt:
                txt = txt.rstrip("0").rstrip(".")
            return "0" if txt in {"", "-0"} else txt
    except (InvalidOperation, ValueError):
        pass
    return s


def _record_fingerprint(source: str, row: Mapping[str, Any]) -> str:
    id_val = ""
    for k in (
        "execId",
        "tradeId",
        "tid",
        "match_id",
        "billId",
        "closeId",
        "subPosId",
        "depId",
        "wdId",
        "withdrawId",
        "walletTxId",
        "txID",
        "txId",
        "tx-hash",
        "txid",
        "hash",
        "tranId",
        "cf_key",
        "_rebate_uid",
        "ref_id",
        "posId",
        "positionId",
        "orderId",
        "order_id",
        "ordId",
        "oid",
        "id",
    ):
        v = row.get(k)
        if v not in (None, "") and not isinstance(v, bool):
            id_val = str(v).strip()
            if id_val:
                break

    delta = row.get("delta") if isinstance(row.get("delta"), Mapping) else {}
    sym_val = str(
        row.get("symbol")
        or row.get("instId")
        or row.get("contract")
        or row.get("contract_code")
        or row.get("coin")
        or row.get("ccy")
        or row.get("currency")
        or row.get("asset")
        or delta.get("coin")
        or delta.get("type")
        or ""
    ).strip().upper()

    ts_val = ""
    for tk in (
        "updatedTime",
        "uTime",
        "closeTimestamp",
        "closeTime",
        "time_ms",
        "time",
        "ts",
        "execTime",
        "createdAt",
        "filledTm",
        "create_date",
        "create_time",
        "created_at",
        "createTime",
        "insertTime",
        "successAt",
        "applyTime",
        "created-at",
        "settleTime",
        "commission_time",
        "transactionTime",
    ):
        v = row.get(tk)
        if v not in (None, "") and not isinstance(v, bool):
            ts_val = _norm_fp_val(v)
            if ts_val and ts_val != "0":
                break

    amt_val = ""
    for ak in (
        "closedPnl",
        "closed_pnl",
        "realizedPnl",
        "netProfit",
        "closeProfitLoss",
        "real_profit",
        "pnl",
        "amount",
        "amt",
        "change",
        "balChg",
        "cashFlow",
        "income",
        "commission_amount",
        "qty",
        "size",
        "sz",
        "closeSize",
        "closeTotalPos",
        "max_size",
    ):
        v = row.get(ak)
        if v not in (None, "") and not isinstance(v, bool):
            amt_val = _norm_fp_val(v)
            if amt_val:
                break
    if not amt_val and delta.get("usdc") not in (None, ""):
        amt_val = _norm_fp_val(delta.get("usdc"))

    if not id_val and not (ts_val and (sym_val or amt_val)):
        return ""
    return f"{source}:{id_val}:{sym_val}:{ts_val}:{amt_val}"


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
    identity_mode: str = "none"  # "none" | "probe" | "sync_bound"
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

    async def probe_identity(
        self,
        cred: ExchangeCredentialRecord,
        *,
        raise_on_transient: bool = False,
    ) -> dict[str, Any]:
        """Best-effort read-only identity check (`remote_uid`, `account_role`, `subaccounts`)."""
        try:
            info = await self.test_connection(cred)
            return info if isinstance(info, dict) else {}
        except AccountIdentityError:
            raise
        except Exception as exc:
            if is_transient_upstream_error(exc):
                if raise_on_transient:
                    raise
                return {"_transient_error": str(exc)}
            return {}

    def extract_cached_account_uid(self, store: "LedgerStore") -> str:
        """Extract cached UID/address from partition KV, plugin snapshot, or legacy live_bundle."""
        sc = store.venue_scope(self.meta.venue_id)
        kv_uid = _clean_uid_candidate(sc.get_kv("account_uid"))
        if kv_uid:
            return kv_uid
        snap = sc.get_kv("snapshot")
        if isinstance(snap, Mapping):
            for k in ("account_uid", "wallet_address", "userid", "uid"):
                cand = _clean_uid_candidate(snap.get(k))
                if cand:
                    return cand
        lb = sc.get_kv("live_bundle")
        if isinstance(lb, Mapping):
            for k in ("account_uid", "userid", "uid"):
                cand = _clean_uid_candidate(lb.get(k))
                if cand:
                    return cand
            det = lb.get("account_detail")
            if isinstance(det, Mapping):
                cand = _clean_uid_candidate(det.get("user_id"))
                if cand:
                    return cand
            fut = lb.get("futures_account")
            if isinstance(fut, Mapping):
                cand = _clean_uid_candidate(fut.get("user"))
                if cand:
                    return cand
        return ""

    def extract_archive_record_fingerprints(
        self,
        store: "LedgerStore",
        *,
        include_legacy_fallback: bool = True,
    ) -> set[str]:
        """Extract immutable historical trade/cashflow record fingerprints for ownership verification."""
        fps: set[str] = set()
        sc = store.venue_scope(self.meta.venue_id)
        snap = (
            sc.get_kv("snapshot")
            if include_legacy_fallback
            else store.load_partition_kv_only(self.meta.venue_id, "snapshot")
        )
        if isinstance(snap, Mapping):
            for field_name in _SNAPSHOT_HISTORY_FIELDS:
                rows = snap.get(field_name)
                if not isinstance(rows, list):
                    continue
                for r in rows:
                    if isinstance(r, Mapping):
                        fp = _record_fingerprint(f"snap.{field_name}", r)
                        if fp:
                            fps.add(fp)

        if include_legacy_fallback:
            for stream in _ARCHIVE_HISTORY_STREAMS:
                for r in sc.load_records(stream):
                    if isinstance(r, Mapping):
                        fp = _record_fingerprint(f"rec.{stream}", r)
                        if fp:
                            fps.add(fp)
        else:
            rec_map = store.load_partition_records_only(self.meta.venue_id)
            for stream, rows in rec_map.items():
                if stream == "eod_snapshots":
                    continue
                for r in rows:
                    if isinstance(r, Mapping):
                        fp = _record_fingerprint(f"rec.{stream}", r)
                        if fp:
                            fps.add(fp)
        return fps

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
