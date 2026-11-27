"""Multi-exchange & multi-account read-only sync manager with adaptive idle-account backoff polling.

Orchestrates all registered `BaseExchangeAdapter` modules uniformly with zero venue-specific branching.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import time
from typing import Any, NamedTuple

from opentradeledger.adapters.base import BaseExchangeAdapter
from opentradeledger.adapters.easicoin.identity import EasiCoinIdentityError
from opentradeledger.adapters.registry import AdapterRegistry
from opentradeledger.core.account_partition import (
    AccountIdentityError,
    AccountScopedStore,
    is_secondary_account_id,
    mask_uid,
    parse_adapter_id,
)
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.notifier import bark_configured, dispatch_notification
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.venue_health import IdentityProbeTransientError, VenueHealthTracker
from opentradeledger.core.venue_primitives import STABLECOINS
from opentradeledger.security.vault import CredentialEditConflict, CredentialVault

logger = logging.getLogger("opentradeledger.sync")

BASE_ACTIVE_INTERVAL_SEC = 20
ACTIVE_EQUITY_THRESHOLD_USD = 20.0
EQUITY_CHANGE_DEADBAND_USD = 0.05
HOT_OBSERVATION_WINDOW_SEC = 900
IDLE_BACKOFF_LADDER_SEC = (900, 1800, 3600, 7200, 14400)


class CandidateSaveError(RuntimeError):
    """The historical sync succeeded, but its local changes could not be committed."""


class CandidateRollbackError(RuntimeError):
    """Local storage failed while restoring the previous venue state."""


class ExitReceipt(NamedTuple):
    trade_id: str
    opened_ms: int
    time_ms: int


ExitReceipts = dict[tuple[str, str, str, str], ExitReceipt]


def _booked_exits(adapter: BaseExchangeAdapter, scoped_store: AccountScopedStore, now_s: int) -> ExitReceipts | None:
    """Read the same settled trades as the dashboard; only exit orders or statements count."""
    try:
        if not adapter.has_venue_data(scoped_store):
            return None
        bundle = adapter.build_venue_ledger(scoped_store, journals={}, now_s=now_s)
        if bundle is None:
            return None
        receipts = {}
        for trade in bundle.trades:
            symbol, side = trade["symbol"], trade["side"]
            opened_ms = int(trade.get("open_time_ms") or 0)
            exits = [e for e in trade.get("position_events") or [] if e["action"] in {"REDUCE", "CLOSE"}]
            for event in exits or trade.get("exit_batches") or []:
                order = str(event.get("order_id") or "")
                when = int(event.get("time_ms") or 0)
                key = (symbol, side, "order", order) if order else (symbol, side, "time", f"{trade['trade_id']}:{when}")
                receipts[key] = ExitReceipt(trade["trade_id"], opened_ms, when)
            if not exits and not trade.get("exit_batches"):
                receipts[(symbol, side, "statement", trade["trade_id"])] = ExitReceipt(
                    trade["trade_id"], opened_ms, trade["close_time_ms"]
                )
        return receipts
    except Exception:
        logger.exception("Could not read %s settlements for Bark", adapter.meta.venue_id)
        return None


def _new_booked_exits(previous: ExitReceipts, current: ExitReceipts) -> int:
    # A statement may acquire fill evidence later. Previously booked parts do not ring again.
    previous_trade_ids = {r.trade_id for r in previous.values()}
    previous_statements = {r.trade_id: r.time_ms for key, r in previous.items() if key[2] == "statement"}
    old_statement_times = {
        (key[0], key[1], r.opened_ms, r.time_ms)
        for key, r in previous.items() if key[2] == "statement" and r.opened_ms and r.time_ms
    }
    # Some venue statement IDs include their latest close time. Match an actual
    # exit at the old statement boundary before treating earlier fills as covered.
    for key, receipt in current.items():
        if (key[0], key[1], receipt.opened_ms, receipt.time_ms) in old_statement_times:
            previous_statements[receipt.trade_id] = max(
                previous_statements.get(receipt.trade_id, 0), receipt.time_ms
            )
    return sum(
        1 for key, receipt in current.items()
        if key not in previous
        and not (key[2] == "statement" and receipt.trade_id in previous_trade_ids)
        and not (receipt.trade_id in previous_statements and receipt.time_ms <= previous_statements[receipt.trade_id])
    )


def should_sync_venue(
    schedule_map: dict[str, Any],
    venue_id: str,
    *,
    now_s: int,
    force_run: bool = False,
) -> bool:
    if force_run:
        return True
    st = schedule_map.get(venue_id)
    if not isinstance(st, dict) or int(st.get("last_sync_sec") or 0) <= 0:
        return True
    return now_s >= int(st.get("next_due_sec") or 0)


def update_venue_schedule(
    schedule_map: dict[str, Any],
    venue_id: str,
    *,
    now_s: int,
    equity_usd: float,
    has_open_activity: bool,
    fp_before: tuple[int, ...],
    fp_after: tuple[int, ...],
    base_interval_sec: int = BASE_ACTIVE_INTERVAL_SEC,
) -> dict[str, Any]:
    prev = schedule_map.get(venue_id)
    prev_dict = prev if isinstance(prev, dict) else {}
    prev_eq = float(prev_dict["last_equity_usd"]) if "last_equity_usd" in prev_dict else None
    prev_hot_until = int(prev_dict.get("hot_until_sec") or 0)
    prev_stage = int(prev_dict.get("stage") or 0)

    rows_changed = tuple(fp_after) != tuple(fp_before)
    equity_changed = (prev_eq is not None) and (abs(float(equity_usd) - prev_eq) >= EQUITY_CHANGE_DEADBAND_USD)
    changed = rows_changed or equity_changed

    base_iv = max(1, int(round(base_interval_sec)))
    if float(equity_usd) >= ACTIVE_EQUITY_THRESHOLD_USD or has_open_activity:
        stage = 0
        interval = base_iv
        hot_until = 0
    elif changed:
        stage = 0
        interval = base_iv
        hot_until = now_s + HOT_OBSERVATION_WINDOW_SEC
    elif now_s < prev_hot_until:
        stage = 0
        interval = base_iv
        hot_until = prev_hot_until
    else:
        stage = min(len(IDLE_BACKOFF_LADDER_SEC), prev_stage + 1)
        interval = IDLE_BACKOFF_LADDER_SEC[stage - 1]
        hot_until = 0

    entry = {
        "stage": stage,
        "mode": "active" if stage == 0 else "idle_backoff",
        "interval_sec": int(interval),
        "last_sync_sec": int(now_s),
        "next_due_sec": int(now_s + interval),
        "hot_until_sec": int(hot_until),
        "last_equity_usd": round(float(equity_usd), 4),
        "fingerprint": list(fp_after),
    }
    if stage > prev_stage:
        logger.info(
            "Adaptive sync [%s]: idle equity=%.2f U (< %.0f U), extending interval to %ds (stage %d)",
            venue_id,
            equity_usd,
            ACTIVE_EQUITY_THRESHOLD_USD,
            interval,
            stage,
        )
    elif prev_stage > 0 and stage == 0:
        logger.info(
            "Adaptive sync [%s]: activity/balance change detected (equity=%.2f U), snapping back to %ds",
            venue_id,
            equity_usd,
            interval,
        )
    schedule_map[venue_id] = entry
    return entry


def _extract_snapshot_activity(venue_id: str, snap: dict[str, Any]) -> tuple[float, bool, tuple[int, ...]]:
    """Generic structural activity extractor for snapshot payloads (zero venue-id branching)."""
    _ = venue_id
    equity_usd = float(snap.get("equity_usd") or 0.0)
    tickers = snap.get("tickers") if isinstance(snap.get("tickers"), dict) else {}

    def _val_usd(ccy_raw: str, amt: float, explicit_usd: float = 0.0) -> float:
        if explicit_usd > 0:
            return explicit_usd
        c = str(ccy_raw or "").strip().upper()
        if not c or amt == 0:
            return 0.0
        if c in STABLECOINS:
            return amt
        try:
            return amt * float(tickers.get(c) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    for bal in snap.get("balances") or []:
        if isinstance(bal, dict):
            equity_usd += float(bal.get("totalEq") or 0.0)
    wallet = snap.get("wallet")
    if isinstance(wallet, dict):
        for acct in wallet.get("list") or []:
            if isinstance(acct, dict):
                equity_usd += float(acct.get("totalEquity") or 0.0)
    funding = snap.get("funding")
    if isinstance(funding, list):
        for fb in funding:
            if isinstance(fb, dict):
                equity_usd += _val_usd(str(fb.get("ccy") or ""), float(fb.get("bal") or 0.0))
    elif isinstance(funding, dict):
        for coin in funding.get("balance") or []:
            if isinstance(coin, dict):
                equity_usd += _val_usd(
                    str(coin.get("coin") or ""),
                    float(coin.get("walletBalance") or 0.0),
                    explicit_usd=float(coin.get("usdValue") or 0.0),
                )

    positions = snap.get("positions") or []
    open_count = sum(
        1
        for p in positions
        if isinstance(p, dict) and abs(float(p.get("pos") or p.get("size") or p.get("qty") or 0.0)) > 0
    )
    fills_count = len(snap.get("fills") or snap.get("executions") or [])
    closed_count = len(snap.get("positions_history") or snap.get("closed_pnl") or snap.get("closed_trades") or [])
    return round(equity_usd, 4), open_count > 0, (open_count, closed_count, fills_count)


class SyncManager:
    def __init__(
        self,
        *,
        store: LedgerStore,
        vault: CredentialVault,
        registry: AdapterRegistry,
        ledger_service: LedgerService,
        poll_interval_sec: float = BASE_ACTIVE_INTERVAL_SEC,
        health_tracker: VenueHealthTracker | None = None,
    ) -> None:
        self.store = store
        self.vault = vault
        self.registry = registry
        self.ledger_service = ledger_service
        self.health_tracker = health_tracker or VenueHealthTracker(store)
        self.poll_interval_sec = max(1.0, float(poll_interval_sec))
        self._stop_event: asyncio.Event | None = None
        self._bg_task: asyncio.Task[None] | None = None
        self._sync_lock = asyncio.Lock()
        self._last_booked_exits: dict[str, ExitReceipts] = {}

    def _load_schedule(self) -> dict[str, Any]:
        raw = self.store.get_meta("sync_schedule", {})
        return dict(raw) if isinstance(raw, dict) else {}

    def _save_schedule(self, schedule: dict[str, Any]) -> None:
        self.store.set_meta("sync_schedule", schedule)

    def get_schedule_summary(self) -> dict[str, Any]:
        schedule = self._load_schedule()
        return {
            "base_interval_sec": int(round(self.poll_interval_sec)),
            "active_equity_threshold_usd": ACTIVE_EQUITY_THRESHOLD_USD,
            "idle_backoff_ladder_sec": list(IDLE_BACKOFF_LADDER_SEC),
            "background_running": bool(self._bg_task and not self._bg_task.done()),
            "venues": schedule,
        }

    def _occupied_for_adapter(self, adapter_id: str, adapter: Any) -> set[str]:
        ad_id = parse_adapter_id(adapter_id)
        occupied = {c.venue_id.lower() for c in self.vault.list_for_adapter(ad_id)}
        occupied.update(self.store.list_partition_account_ids(ad_id))
        if adapter is not None and adapter.has_venue_data(self.store.for_account(ad_id, ad_id)):
            occupied.add(ad_id)
        return occupied

    async def backfill_unattributed_sibling_uids(
        self,
        adapter_id: str,
        *,
        exclude_acct_id: str = "",
    ) -> None:
        """Probe any active UID-less sibling credentials on `adapter_id` before allowing secondary account creation."""
        ad_id = parse_adapter_id(adapter_id)
        adapter = self.registry.get(ad_id)
        if adapter is None or adapter.meta.identity_mode != "probe":
            return
        excl = (exclude_acct_id or "").strip().lower()
        for other_cred in self.vault.list_decrypted():
            other_acct = other_cred.venue_id.strip().lower()
            if other_acct == excl or other_cred.effective_adapter_id != ad_id:
                continue
            other_sc = self.store.venue_scope(other_acct)
            other_scoped = self.store.for_account(other_acct, ad_id)
            existing_uid = str(
                other_cred.remote_uid
                or other_sc.get_kv("account_uid")
                or adapter.extract_cached_account_uid(other_scoped)
                or ""
            ).strip()
            if existing_uid:
                continue
            other_info = await adapter.probe_identity(other_cred)
            if isinstance(other_info, dict):
                if other_info.get("_transient_error"):
                    raise IdentityProbeTransientError(str(other_info["_transient_error"]))
                found_uid = str(other_info.get("remote_uid") or "").strip()
                found_role = str(other_info.get("account_role") or other_cred.account_role or "").strip()
                if found_uid:
                    other_sc.set_kv("account_uid", found_uid)
                    other_sc.set_kv("identity_backfilled_v1", True)
                    if found_role:
                        other_sc.set_kv("account_role", found_role)
                    self.vault.upsert(
                        other_cred.model_copy(
                            update={
                                "remote_uid": found_uid,
                                "account_role": found_role,
                            }
                        )
                    )

    def has_unattributed_data_partitions(
        self,
        adapter_id: str,
        *,
        exclude_acct_id: str = "",
    ) -> bool:
        """Return True if any active or archived partition under `adapter_id` has data without a resolved UID."""
        ad_id = parse_adapter_id(adapter_id)
        adapter = self.registry.get(ad_id)
        if adapter is None:
            return False
        excl = (exclude_acct_id or "").strip().lower()
        active_creds = {
            c.venue_id.strip().lower(): c
            for c in self.vault.list_decrypted()
            if c.effective_adapter_id == ad_id
        }
        cand_partitions = {ad_id, *self.store.list_partition_account_ids(ad_id)} | set(active_creds.keys())
        for other_acct in sorted(cand_partitions):
            if other_acct == excl:
                continue
            other_scoped = self.store.for_account(other_acct, ad_id)
            if not adapter.has_venue_data(other_scoped):
                continue
            other_cred = active_creds.get(other_acct)
            other_sc = self.store.venue_scope(other_acct)
            other_uid = str(
                (other_cred.remote_uid if other_cred else "")
                or other_sc.get_kv("account_uid")
                or adapter.extract_cached_account_uid(other_scoped)
                or ""
            ).strip()
            if not other_uid:
                return True
        return False

    def check_identity_conflicts(
        self,
        *,
        account_id: str,
        adapter_id: str,
        api_key: str,
        resolved_uid: str,
        bound_uid: str,
        exchange_name: str = "",
        exchange_name_zh: str = "",
        require_verified_uid: bool = False,
        require_uid_for_existing_data: bool = False,
    ) -> None:
        acct_id = account_id.strip().lower()
        ad_id = parse_adapter_id(adapter_id)
        adapter = self.registry.get(ad_id)
        clean_uid = (resolved_uid or "").strip()
        clean_bound = (bound_uid or "").strip()

        if clean_bound:
            if not clean_uid and require_verified_uid:
                raise AccountIdentityError(
                    "ACCOUNT_UID_UNAVAILABLE",
                    masked_uid=mask_uid(clean_bound),
                    exchange_name=exchange_name,
                    exchange_name_zh=exchange_name_zh,
                )
            if clean_uid and clean_bound.lower() != clean_uid.lower():
                raise AccountIdentityError(
                    "ACCOUNT_UID_MISMATCH",
                    masked_uid=mask_uid(clean_uid),
                    exchange_name=exchange_name,
                    exchange_name_zh=exchange_name_zh,
                )
        elif (require_verified_uid or require_uid_for_existing_data) and not clean_uid:
            raise AccountIdentityError(
                "ACCOUNT_UID_UNAVAILABLE",
                exchange_name=exchange_name,
                exchange_name_zh=exchange_name_zh,
            )

        clean_key = (api_key or "").strip()
        active_acct_ids: set[str] = set()
        for other in self.vault.list_decrypted():
            other_acct = other.venue_id.strip().lower()
            active_acct_ids.add(other_acct)
            if other_acct == acct_id or other.effective_adapter_id != ad_id:
                continue
            cached_other_uid = (
                adapter.extract_cached_account_uid(self.store.for_account(other_acct, ad_id))
                if adapter is not None
                else str(self.store.venue_scope(other_acct).get_kv("account_uid") or "")
            )
            other_uid = str(other.remote_uid or cached_other_uid or "").strip()
            if clean_uid and other_uid and clean_uid.lower() == other_uid.lower():
                raise AccountIdentityError(
                    "DUPLICATE_ACCOUNT_UID",
                    masked_uid=mask_uid(clean_uid),
                    existing_label=other.label or other_acct,
                    exchange_name=exchange_name,
                    exchange_name_zh=exchange_name_zh,
                )
            if clean_key and other.api_key.strip() and clean_key.lower() == other.api_key.strip().lower():
                raise AccountIdentityError(
                    "DUPLICATE_ACCOUNT_KEY",
                    existing_label=other.label or other_acct,
                    exchange_name=exchange_name,
                    exchange_name_zh=exchange_name_zh,
                )

        if clean_uid:
            candidate_partitions = {ad_id, *self.store.list_partition_account_ids(ad_id)}
            for other_acct in sorted(candidate_partitions):
                if other_acct == acct_id or other_acct in active_acct_ids:
                    continue
                other_scoped = self.store.for_account(other_acct, ad_id)
                if adapter is not None and not adapter.has_venue_data(other_scoped):
                    continue
                other_sc = self.store.venue_scope(other_acct)
                other_uid = str(
                    (adapter.extract_cached_account_uid(other_scoped) if adapter is not None else other_sc.get_kv("account_uid"))
                    or ""
                ).strip()
                if other_uid and clean_uid.lower() == other_uid.lower():
                    saved_lbl = str(other_sc.get_kv("account_label") or other_acct).strip()
                    raise AccountIdentityError(
                        "ARCHIVED_ACCOUNT_UID_EXISTS",
                        masked_uid=mask_uid(clean_uid),
                        existing_label=saved_lbl,
                        exchange_name=exchange_name,
                        exchange_name_zh=exchange_name_zh,
                    )

    async def start(self) -> None:
        if self._bg_task and not self._bg_task.done():
            return
        self._stop_event = asyncio.Event()
        self._bg_task = asyncio.create_task(self._poll_loop(), name="otl-adaptive-sync")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._bg_task is not None:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
            self._bg_task = None

    async def _poll_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                if not self.ledger_service.is_demo_active() and self.vault.list_decrypted():
                    await self.sync_all(force_all=False)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Background adaptive sync tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_sec)
            except asyncio.TimeoutError:
                continue

    async def sync_all(
        self,
        *,
        force_all: bool = False,
        venue_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._sync_lock:
            self.ledger_service.syncing = True
            now_s = int(time.time())
            base_iv = max(1, int(round(self.poll_interval_sec)))
            schedule = self._load_schedule()
            target_vid = venue_id.strip().lower() if venue_id else None
            results: dict[str, dict[str, Any]] = {}
            any_synced = False
            try:
                all_creds = self.vault.list_decrypted()
                priority_order = {
                    c.venue_id.lower(): (1 if is_secondary_account_id(c.venue_id.lower()) else 0, idx)
                    for idx, c in enumerate(all_creds)
                }
                for cred in all_creds:
                    if not cred.enabled:
                        continue
                    acct_id = cred.venue_id.lower()
                    adapter_id = cred.effective_adapter_id
                    if target_vid and acct_id != target_vid and adapter_id != target_vid:
                        continue
                    adapter = self.registry.get(adapter_id)
                    if adapter is None:
                        continue
                    force_this = force_all or (target_vid in {acct_id, adapter_id})
                    if not should_sync_venue(schedule, acct_id, now_s=now_s, force_run=force_this):
                        st = schedule.get(acct_id) or {}
                        results[acct_id] = {
                            "ok": True,
                            "mode": "skipped_idle_backoff",
                            "stage": int(st.get("stage") or 0),
                            "interval_sec": int(st.get("interval_sec") or base_iv),
                            "next_due_sec": int(st.get("next_due_sec") or 0),
                        }
                        continue

                    prev_entry = schedule.get(acct_id) if isinstance(schedule.get(acct_id), dict) else {}
                    sched_fp_before = tuple(int(x) for x in (prev_entry.get("fingerprint") or []))
                    prev_health = (self.health_tracker.get_all().get(acct_id) or {}).get("status")
                    scoped_store = self.store.for_account(acct_id, adapter_id)
                    notification_settings = self.store.get_preferences().notifications
                    notifying = bark_configured(notification_settings)
                    if not notifying:
                        self._last_booked_exits.pop(acct_id, None)
                    previous_exits = self._last_booked_exits.get(acct_id)
                    if notifying and previous_exits is None:
                        previous_exits = _booked_exits(adapter, scoped_store, now_s)
                    client: Any = None
                    try:
                        client = adapter.create_client(cred)
                        outcome = await adapter.sync_venue(
                            client,
                            scoped_store,
                            now_s=now_s,
                            force_full=force_this,
                        )
                        if outcome.mode == "custom_noop":
                            await adapter.test_connection(cred)
                        eff_fp_before = outcome.fp_before if outcome.fp_before else (sched_fp_before or outcome.fp_after)
                        sched = update_venue_schedule(
                            schedule,
                            acct_id,
                            now_s=now_s,
                            equity_usd=outcome.equity_usd,
                            has_open_activity=outcome.has_open_activity,
                            fp_before=eff_fp_before,
                            fp_after=outcome.fp_after,
                            base_interval_sec=base_iv,
                        )
                        res_row: dict[str, Any] = {
                            "ok": True,
                            "mode": outcome.mode,
                            "equity_usd": round(outcome.equity_usd, 2),
                            "poll_mode": sched["mode"],
                            "interval_sec": sched["interval_sec"],
                            "stage": sched["stage"],
                        }
                        if outcome.warnings:
                            res_row["warnings"] = list(outcome.warnings)
                        results[acct_id] = res_row
                        self.health_tracker.record_success(acct_id, warnings=outcome.warnings)
                        any_synced = True
                        raw_scope = self.store.venue_scope(acct_id)
                        kv_uid = str(
                            raw_scope.get_kv("account_uid")
                            or adapter.extract_cached_account_uid(scoped_store)
                            or ""
                        ).strip()
                        kv_role = str(raw_scope.get_kv("account_role") or "").strip()
                        probed_uid = kv_uid
                        probed_role = kv_role
                        need_uid = not (cred.remote_uid or kv_uid)
                        need_role = not (cred.account_role or kv_role) and not raw_scope.get_kv("identity_backfilled_v1")
                        if need_uid or need_role:
                            info = await adapter.probe_identity(cred)
                            if isinstance(info, dict):
                                probed_uid = str(info.get("remote_uid") or probed_uid or "").strip()
                                probed_role = str(info.get("account_role") or probed_role or "").strip()
                        eff_uid = str(cred.remote_uid or probed_uid or "").strip()
                        eff_role = str(cred.account_role or probed_role or "").strip()

                        duplicate_owner = None
                        if eff_uid:
                            my_prio = priority_order.get(acct_id, (1, 9999))
                            for other_c in self.vault.list_decrypted():
                                other_acct = other_c.venue_id.lower()
                                if other_acct == acct_id or other_c.effective_adapter_id != adapter_id or not other_c.enabled:
                                    continue
                                other_uid = str(
                                    other_c.remote_uid
                                    or adapter.extract_cached_account_uid(self.store.for_account(other_acct, adapter_id))
                                    or ""
                                ).strip()
                                if not other_uid or other_uid.lower() != eff_uid.lower():
                                    continue
                                other_prio = priority_order.get(other_acct, (1, 9999))
                                if other_prio < my_prio:
                                    duplicate_owner = other_c
                                    break
                                if my_prio < other_prio and is_secondary_account_id(other_acct):
                                    other_sc = self.store.venue_scope(other_acct)
                                    other_sc.set_kv("duplicate_uid_conflict", True)
                                    dup_err = AccountIdentityError(
                                        "DUPLICATE_ACCOUNT_UID",
                                        masked_uid=mask_uid(eff_uid),
                                        existing_label=cred.label or acct_id,
                                        exchange_name=adapter.meta.display_name,
                                        exchange_name_zh=adapter.meta.display_name_zh,
                                    )
                                    o_err_state = self.health_tracker.record_error(other_acct, dup_err)
                                    if other_acct in results:
                                        results[other_acct] = {
                                            "ok": False,
                                            "error": o_err_state["error_reason"],
                                            "error_zh": o_err_state["error_reason_zh"],
                                            "error_code": o_err_state["error_code"],
                                            "raw_error": o_err_state["raw_error"],
                                        }

                        if duplicate_owner is not None:
                            raw_scope.set_kv("duplicate_uid_conflict", True)
                            dup_exc = AccountIdentityError(
                                "DUPLICATE_ACCOUNT_UID",
                                masked_uid=mask_uid(eff_uid),
                                existing_label=duplicate_owner.label or duplicate_owner.venue_id,
                                exchange_name=adapter.meta.display_name,
                                exchange_name_zh=adapter.meta.display_name_zh,
                            )
                            err_state = self.health_tracker.record_error(acct_id, dup_exc)
                            results[acct_id] = {
                                "ok": False,
                                "error": err_state["error_reason"],
                                "error_zh": err_state["error_reason_zh"],
                                "error_code": err_state["error_code"],
                                "raw_error": err_state["raw_error"],
                            }
                            continue

                        if raw_scope.get_kv("duplicate_uid_conflict"):
                            raw_scope.set_kv("duplicate_uid_conflict", False)
                        if eff_uid and not raw_scope.get_kv("identity_backfilled_v1"):
                            raw_scope.set_kv("identity_backfilled_v1", True)
                        if eff_uid and eff_uid != str(raw_scope.get_kv("account_uid") or "").strip():
                            raw_scope.set_kv("account_uid", eff_uid)
                        if eff_role and eff_role != kv_role:
                            raw_scope.set_kv("account_role", eff_role)
                        if cred.label and cred.label != raw_scope.get_kv("account_label"):
                            raw_scope.set_kv("account_label", cred.label)
                        if (eff_uid and eff_uid != cred.remote_uid) or (eff_role and eff_role != cred.account_role):
                            self.vault.upsert(
                                cred.model_copy(update={"remote_uid": eff_uid, "account_role": eff_role})
                            )
                        acct_title = cred.label or acct_id.upper()
                        new_exits = 0
                        if notifying:
                            current_exits = _booked_exits(adapter, scoped_store, now_s)
                            if current_exits is not None:
                                if previous_exits is not None:
                                    new_exits = _new_booked_exits(previous_exits, current_exits)
                                # Incomplete fields may temporarily hide previously booked settlements.
                                if previous_exits and outcome.warnings:
                                    current_exits = {**previous_exits, **current_exits}
                                self._last_booked_exits[acct_id] = current_exits
                        if new_exits and self.store.get_preferences().notifications == notification_settings:
                            await self._maybe_notify(
                                title=f"OpenTradeLedger · {acct_title} 新平仓入账",
                                body=f"{acct_title} 新增 {new_exits} 笔减仓或平仓入账记录，当前权益 {outcome.equity_usd:.2f} USDT。",
                            )
                    except Exception as exc:
                        err_state = self.health_tracker.record_error(acct_id, exc)
                        logger.warning("Sync failed for %s: %s", acct_id, err_state["raw_error"])
                        results[acct_id] = {
                            "ok": False,
                            "error": err_state["error_reason"],
                            "error_zh": err_state["error_reason_zh"],
                            "error_code": err_state["error_code"],
                            "raw_error": err_state["raw_error"],
                        }
                        is_transient = err_state["error_code"] in {"NETWORK_UNREACHABLE", "RATE_LIMITED", "UPSTREAM_5XX"}
                        consec = int(err_state.get("consecutive_failures") or 1)
                        should_alert = (consec == 3) if is_transient else (prev_health != "error")
                        if should_alert and self.store.get_preferences().notifications == notification_settings:
                            acct_title = cred.label or acct_id.upper()
                            await self._maybe_notify(
                                title=f"OpenTradeLedger · {acct_title} 同步异常",
                                body=err_state["error_reason_zh"] or err_state["error_reason"],
                            )
                    finally:
                        if client is not None and hasattr(client, "aclose"):
                            try:
                                await client.aclose()
                            except Exception:
                                pass

                if not results:
                    return {
                        "ok": False,
                        "status": "no_accounts",
                        "synced_ms": int(self.store.get_meta("last_sync_ms", 0) or 0),
                        "venues": {},
                        "schedule": self.get_schedule_summary(),
                    }

                self._save_schedule(schedule)
                if any_synced:
                    now_ms = int(time.time() * 1000)
                    self.ledger_service.last_sync_ms = now_ms
                    self.store.set_meta("last_sync_ms", now_ms)
                return {
                    "ok": True,
                    "synced_ms": self.ledger_service.last_sync_ms,
                    "venues": results,
                    "schedule": self.get_schedule_summary(),
                }
            finally:
                self.ledger_service.syncing = False

    @asynccontextmanager
    async def credential_change(self, venue_id: str, expected_revision: str | None):
        expected = self.vault.revision(venue_id) if expected_revision is None else expected_revision
        async with self._sync_lock:
            if self.vault.revision(venue_id) != expected:
                raise CredentialEditConflict()
            self._last_booked_exits.pop(venue_id.lower(), None)
            yield

    async def sync_candidate_and_commit(self, cred: Any, expected_revision: str | None = None) -> dict[str, Any]:
        """Atomically verify identity & sync a candidate API credential BEFORE committing it to CredentialVault."""
        acct_id = cred.venue_id.strip().lower()
        adapter_id = parse_adapter_id(getattr(cred, "adapter_id", "") or acct_id)
        adapter = self.registry.get(adapter_id)
        if adapter is None:
            raise ValueError(f"Unsupported exchange adapter: {adapter_id}")

        async with self.credential_change(acct_id, expected_revision):
            now_s = int(time.time())
            base_iv = max(1, int(round(self.poll_interval_sec)))
            existing_cred = self.vault.get_decrypted(acct_id)
            raw_scope = self.store.venue_scope(acct_id)
            scoped_store = self.store.for_account(acct_id, adapter_id)
            has_existing_data = bool(adapter.has_venue_data(scoped_store))
            prev_bound_uid = str(
                (existing_cred.remote_uid if existing_cred else "")
                or raw_scope.get_kv("account_uid")
                or adapter.extract_cached_account_uid(scoped_store)
                or ""
            ).strip()
            id_mode = adapter.meta.identity_mode
            uses_probe_uid = id_mode == "probe"
            uses_sync_uid = id_mode == "sync_bound"
            partition_snap = self.store.snapshot_venue_partition(acct_id)
            self.ledger_service.syncing = True
            client: Any = None
            try:
                is_rotating_existing = (
                    uses_probe_uid
                    and existing_cred is not None
                    and (
                        existing_cred.api_key.strip() != cred.api_key.strip()
                        or existing_cred.api_secret.strip() != cred.api_secret.strip()
                        or existing_cred.passphrase.strip() != getattr(cred, "passphrase", "").strip()
                    )
                )
                if not prev_bound_uid and is_rotating_existing and existing_cred is not None:
                    prev_info = await adapter.probe_identity(existing_cred)
                    if isinstance(prev_info, dict) and prev_info.get("remote_uid"):
                        prev_bound_uid = str(prev_info["remote_uid"]).strip()

                if uses_probe_uid and is_secondary_account_id(acct_id):
                    await self.backfill_unattributed_sibling_uids(adapter_id, exclude_acct_id=acct_id)

                probed = await adapter.probe_identity(cred)
                early_uid = str(probed.get("remote_uid") or getattr(cred, "remote_uid", "") or "").strip()
                must_verify_uid = bool(uses_probe_uid)
                if (
                    uses_probe_uid
                    and isinstance(probed, dict)
                    and probed.get("_transient_error")
                    and not prev_bound_uid
                    and not (is_rotating_existing and has_existing_data)
                ):
                    raise RuntimeError(str(probed["_transient_error"]))

                self.check_identity_conflicts(
                    account_id=acct_id,
                    adapter_id=adapter_id,
                    api_key=cred.api_key,
                    resolved_uid=early_uid,
                    bound_uid=prev_bound_uid,
                    exchange_name=adapter.meta.display_name,
                    exchange_name_zh=adapter.meta.display_name_zh,
                    require_verified_uid=must_verify_uid,
                    require_uid_for_existing_data=bool(uses_probe_uid and is_rotating_existing and has_existing_data),
                )

                needs_archive_fp_proof = bool(
                    uses_probe_uid
                    and has_existing_data
                    and not prev_bound_uid
                    and (existing_cred is None or is_rotating_existing)
                )
                fresh_fps: set[str] | None = None
                client = adapter.create_client(cred)
                if needs_archive_fp_proof:
                    old_fps = adapter.extract_archive_record_fingerprints(
                        scoped_store,
                        include_legacy_fallback=True,
                    )
                    self.store.clear_venue_partition_only(acct_id)
                    outcome = await adapter.sync_venue(
                        client,
                        scoped_store,
                        now_s=now_s,
                        force_full=True,
                    )
                    fresh_fps = adapter.extract_archive_record_fingerprints(
                        scoped_store,
                        include_legacy_fallback=False,
                    )
                    if (not old_fps) or not (old_fps & fresh_fps):
                        raise AccountIdentityError(
                            "ARCHIVE_OWNER_UNVERIFIED",
                            exchange_name=adapter.meta.display_name,
                            exchange_name_zh=adapter.meta.display_name_zh,
                        )
                    self.store.restore_venue_partition(acct_id, partition_snap)
                    outcome = await adapter.sync_venue(
                        client,
                        scoped_store,
                        now_s=now_s,
                        force_full=True,
                    )
                else:
                    outcome = await adapter.sync_venue(
                        client,
                        scoped_store,
                        now_s=now_s,
                        force_full=True,
                    )

                if outcome.mode == "custom_noop" and not probed:
                    conn_res = await adapter.test_connection(cred)
                    if isinstance(conn_res, dict):
                        probed = conn_res

                post_sync_kv_uid = (
                    str(raw_scope.get_kv("account_uid") or adapter.extract_cached_account_uid(scoped_store) or "").strip()
                    if uses_sync_uid
                    else str(adapter.extract_cached_account_uid(scoped_store) or "").strip()
                )
                resolved_uid = str(
                    probed.get("remote_uid")
                    or getattr(cred, "remote_uid", "")
                    or post_sync_kv_uid
                    or ""
                ).strip()
                resolved_role = str(
                    probed.get("account_role")
                    or getattr(cred, "account_role", "")
                    or (raw_scope.get_kv("account_role") if resolved_uid and resolved_uid == prev_bound_uid else "")
                    or ""
                ).strip()
                self.check_identity_conflicts(
                    account_id=acct_id,
                    adapter_id=adapter_id,
                    api_key=cred.api_key,
                    resolved_uid=resolved_uid,
                    bound_uid=prev_bound_uid,
                    exchange_name=adapter.meta.display_name,
                    exchange_name_zh=adapter.meta.display_name_zh,
                    require_verified_uid=must_verify_uid,
                    require_uid_for_existing_data=bool(uses_probe_uid and is_rotating_existing and has_existing_data),
                )

                if (uses_probe_uid or uses_sync_uid) and not (existing_cred and prev_bound_uid):
                    active_creds_by_acct = {
                        c.venue_id.strip().lower(): c
                        for c in self.vault.list_decrypted()
                        if c.effective_adapter_id == adapter_id
                    }
                    candidate_partitions = (
                        {adapter_id, *self.store.list_partition_account_ids(adapter_id)}
                        | set(active_creds_by_acct.keys())
                    )
                    verified_uid = str(probed.get("remote_uid") or (post_sync_kv_uid if uses_sync_uid else "")).strip()
                    target_owner_verified = needs_archive_fp_proof or (
                        has_existing_data and bool(verified_uid) and verified_uid.lower() == prev_bound_uid.lower()
                    )
                    for other_acct in sorted(candidate_partitions):
                        if other_acct == acct_id:
                            continue
                        other_scoped = self.store.for_account(other_acct, adapter_id)
                        if not adapter.has_venue_data(other_scoped):
                            continue
                        other_cred = active_creds_by_acct.get(other_acct)
                        other_sc = self.store.venue_scope(other_acct)
                        other_uid = str(
                            (other_cred.remote_uid if other_cred else "")
                            or other_sc.get_kv("account_uid")
                            or adapter.extract_cached_account_uid(other_scoped)
                            or ""
                        ).strip()
                        if other_uid:
                            continue
                        if fresh_fps is None:
                            fresh_fps = adapter.extract_archive_record_fingerprints(
                                scoped_store,
                                include_legacy_fallback=False,
                            )
                        other_fps = adapter.extract_archive_record_fingerprints(
                            other_scoped,
                            include_legacy_fallback=not is_secondary_account_id(other_acct),
                        )
                        # Missing or disjoint history cannot establish that a second
                        # credential belongs to a separate exchange account.
                        err_code = "ARCHIVE_OWNER_UNVERIFIED"
                        if other_fps & fresh_fps:
                            err_code = "DUPLICATE_ACCOUNT_UID" if other_cred else "ARCHIVED_ACCOUNT_UID_EXISTS"
                        elif target_owner_verified and other_cred is None:
                            # A verified original row can be rebound independently.
                            # The disjoint archive stays unclaimed until its own rebind.
                            continue
                        raise AccountIdentityError(
                            err_code,
                            masked_uid=mask_uid(resolved_uid),
                            existing_label=(other_cred.label if other_cred else "")
                            or str(other_sc.get_kv("account_label") or other_acct),
                            exchange_name=adapter.meta.display_name,
                            exchange_name_zh=adapter.meta.display_name_zh,
                        )

                if raw_scope.get_kv("duplicate_uid_conflict"):
                    raw_scope.set_kv("duplicate_uid_conflict", False)
                if resolved_uid:
                    raw_scope.set_kv("account_uid", resolved_uid)
                    raw_scope.set_kv("identity_backfilled_v1", True)
                if resolved_role:
                    raw_scope.set_kv("account_role", resolved_role)
                if cred.label:
                    raw_scope.set_kv("account_label", cred.label)

                partition_snap["sync_metadata"] = self.store.snapshot_sync_metadata()
                schedule = self._load_schedule()
                prev_entry = schedule.get(acct_id) if isinstance(schedule.get(acct_id), dict) else {}
                sched_fp_before = tuple(int(x) for x in (prev_entry.get("fingerprint") or []))
                eff_fp_before = outcome.fp_before if outcome.fp_before else (sched_fp_before or outcome.fp_after)
                sched = update_venue_schedule(
                    schedule,
                    acct_id,
                    now_s=now_s,
                    equity_usd=outcome.equity_usd,
                    has_open_activity=outcome.has_open_activity,
                    fp_before=eff_fp_before,
                    fp_after=outcome.fp_after,
                    base_interval_sec=base_iv,
                )
                self._save_schedule(schedule)
                h_state = self.health_tracker.record_success(acct_id, warnings=outcome.warnings)
                now_ms = int(time.time() * 1000)
                self.store.set_meta("last_sync_ms", now_ms)
                updated_cred = cred.model_copy(
                    update={
                        "venue_id": acct_id,
                        "adapter_id": adapter_id,
                        "remote_uid": resolved_uid,
                        "account_role": resolved_role,
                    }
                )
                venue_res_entry: dict[str, Any] = {
                    "ok": True,
                    "mode": outcome.mode,
                    "equity_usd": round(outcome.equity_usd, 2),
                    "poll_mode": sched["mode"],
                    "interval_sec": sched["interval_sec"],
                    "stage": sched["stage"],
                }
                if outcome.warnings:
                    venue_res_entry["warnings"] = list(outcome.warnings)
                result: dict[str, Any] = {
                    "ok": True,
                    "committed": True,
                    "account_id": acct_id,
                    "adapter_id": adapter_id,
                    "health": h_state,
                    "synced_ms": now_ms,
                    "venues": {acct_id: venue_res_entry},
                }
                if probed.get("subaccounts"):
                    result["subaccounts"] = probed["subaccounts"]
                result["credential"] = self.vault.upsert(updated_cred)
                self.ledger_service.last_sync_ms = now_ms
                return result
            except EasiCoinIdentityError:
                raise
            except AccountIdentityError:
                try:
                    self.store.restore_venue_partition(acct_id, partition_snap)
                except Exception as rollback_error:
                    raise CandidateRollbackError("Could not restore venue data") from rollback_error
                raise
            except Exception as exc:
                try:
                    self.store.restore_venue_partition(acct_id, partition_snap)
                except Exception as rollback_error:
                    raise CandidateRollbackError("Could not restore venue data") from rollback_error
                if "sync_metadata" in partition_snap:
                    raise CandidateSaveError("Could not save local account data") from exc
                raise
            finally:
                if client is not None and hasattr(client, "aclose"):
                    try:
                        await client.aclose()
                    except Exception:
                        pass
                self.ledger_service.syncing = False

    async def _maybe_notify(self, *, title: str, body: str) -> None:
        try:
            prefs = self.ledger_service.get_preferences()
            n = prefs.notifications
            if not bark_configured(n):
                return
            await dispatch_notification(n, title=title, body=body)
        except Exception as exc:
            logger.debug("Optional notification dispatch skipped: %s", exc)
