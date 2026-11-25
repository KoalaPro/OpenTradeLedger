"""Multi-exchange read-only sync manager with adaptive idle-account backoff polling.

Orchestrates all registered `BaseExchangeAdapter` modules uniformly with zero venue-specific branching.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from opentradeledger.adapters.registry import AdapterRegistry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.notifier import bark_configured, dispatch_notification
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.venue_health import VenueHealthTracker
from opentradeledger.core.venue_primitives import STABLECOINS
from opentradeledger.security.vault import CredentialVault

logger = logging.getLogger("opentradeledger.sync")

BASE_ACTIVE_INTERVAL_SEC = 20
ACTIVE_EQUITY_THRESHOLD_USD = 20.0
EQUITY_CHANGE_DEADBAND_USD = 0.05
HOT_OBSERVATION_WINDOW_SEC = 900
IDLE_BACKOFF_LADDER_SEC = (900, 1800, 3600, 7200, 14400)


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
                for cred in self.vault.list_decrypted():
                    if not cred.enabled:
                        continue
                    vid = cred.venue_id.lower()
                    if target_vid and vid != target_vid:
                        continue
                    adapter = self.registry.get(vid)
                    if adapter is None:
                        continue
                    force_this = force_all or (target_vid == vid)
                    if not should_sync_venue(schedule, vid, now_s=now_s, force_run=force_this):
                        st = schedule.get(vid) or {}
                        results[vid] = {
                            "ok": True,
                            "mode": "skipped_idle_backoff",
                            "stage": int(st.get("stage") or 0),
                            "interval_sec": int(st.get("interval_sec") or base_iv),
                            "next_due_sec": int(st.get("next_due_sec") or 0),
                        }
                        continue

                    prev_entry = schedule.get(vid) if isinstance(schedule.get(vid), dict) else {}
                    sched_fp_before = tuple(int(x) for x in (prev_entry.get("fingerprint") or []))
                    prev_health = (self.health_tracker.get_all().get(vid) or {}).get("status")
                    client: Any = None
                    try:
                        client = adapter.create_client(cred)
                        outcome = await adapter.sync_venue(
                            client,
                            self.store,
                            now_s=now_s,
                            force_full=force_this,
                        )
                        if outcome.mode == "custom_noop":
                            await adapter.test_connection(cred)
                        eff_fp_before = outcome.fp_before if outcome.fp_before else (sched_fp_before or outcome.fp_after)
                        sched = update_venue_schedule(
                            schedule,
                            vid,
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
                            res_row["warnings"] = outcome.warnings
                        results[vid] = res_row
                        self.health_tracker.record_success(vid)
                        any_synced = True
                        if outcome.new_closed_delta > 0:
                            await self._maybe_notify(
                                title=f"OpenTradeLedger · {vid.upper()} 新平仓入账",
                                body=f"{vid.upper()} 新增 {outcome.new_closed_delta} 笔交割记录，当前权益 {outcome.equity_usd:.2f} USDT。",
                            )
                    except Exception as exc:
                        logger.warning("Sync failed for %s: %s", vid, exc)
                        err_state = self.health_tracker.record_error(vid, exc)
                        results[vid] = {
                            "ok": False,
                            "error": err_state["error_reason"],
                            "error_zh": err_state["error_reason_zh"],
                            "error_code": err_state["error_code"],
                            "raw_error": err_state["raw_error"],
                        }
                        if prev_health != "error":
                            await self._maybe_notify(
                                title=f"OpenTradeLedger · {vid.upper()} 同步异常",
                                body=err_state["error_reason_zh"] or err_state["error_reason"],
                            )
                    finally:
                        if client is not None and hasattr(client, "aclose"):
                            try:
                                await client.aclose()
                            except Exception:
                                pass

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

    async def sync_candidate_and_commit(self, cred: Any) -> dict[str, Any]:
        """Atomically sync a candidate API credential BEFORE committing it to CredentialVault.

        If historical backfill times out or fails mid-way, restores the venue's SQLite
        partition snapshot and leaves any previously saved credential & health untouched.
        """
        vid = cred.venue_id.strip().lower()
        adapter = self.registry.get(vid)
        if adapter is None:
            raise ValueError(f"Unsupported exchange adapter: {vid}")

        async with self._sync_lock:
            self.ledger_service.syncing = True
            now_s = int(time.time())
            base_iv = max(1, int(round(self.poll_interval_sec)))
            partition_snap = self.store.snapshot_venue_partition(vid)
            client: Any = None
            try:
                client = adapter.create_client(cred)
                outcome = await adapter.sync_venue(
                    client,
                    self.store,
                    now_s=now_s,
                    force_full=True,
                )
                if outcome.mode == "custom_noop":
                    await adapter.test_connection(cred)

                masked = self.vault.upsert(cred)
                schedule = self._load_schedule()
                prev_entry = schedule.get(vid) if isinstance(schedule.get(vid), dict) else {}
                sched_fp_before = tuple(int(x) for x in (prev_entry.get("fingerprint") or []))
                eff_fp_before = outcome.fp_before if outcome.fp_before else (sched_fp_before or outcome.fp_after)
                sched = update_venue_schedule(
                    schedule,
                    vid,
                    now_s=now_s,
                    equity_usd=outcome.equity_usd,
                    has_open_activity=outcome.has_open_activity,
                    fp_before=eff_fp_before,
                    fp_after=outcome.fp_after,
                    base_interval_sec=base_iv,
                )
                self._save_schedule(schedule)
                h_state = self.health_tracker.record_success(vid)
                now_ms = int(time.time() * 1000)
                self.ledger_service.last_sync_ms = now_ms
                self.store.set_meta("last_sync_ms", now_ms)
                return {
                    "ok": True,
                    "committed": True,
                    "credential": masked,
                    "health": h_state,
                    "synced_ms": now_ms,
                    "venues": {
                        vid: {
                            "ok": True,
                            "mode": outcome.mode,
                            "equity_usd": round(outcome.equity_usd, 2),
                            "poll_mode": sched["mode"],
                            "interval_sec": sched["interval_sec"],
                            "stage": sched["stage"],
                        }
                    },
                }
            except Exception:
                self.store.restore_venue_partition(vid, partition_snap)
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
