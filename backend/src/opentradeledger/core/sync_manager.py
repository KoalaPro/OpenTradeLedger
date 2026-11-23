"""Multi-exchange read-only sync manager with adaptive idle-account backoff polling.

Polling cadence per exchange:
- Active venues (equity >= 20 USDT, open positions, or recent balance/fill change):
  polls every `base_interval_sec` (default 20s).
- Idle venues (equity < 20 USDT, no open positions, no balance/row delta):
  progressively backs off along `IDLE_BACKOFF_LADDER_SEC`:
  15m -> 30m -> 1h -> 2h -> 4h (900s -> 1800s -> 3600s -> 7200s -> 14400s).
- Instant wake-up:
  any balance delta >= 0.05 USDT, new fill/position activity, or manual Sync Now
  snaps the venue immediately back to 20s (with a 15-minute hot observation window).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from opentradeledger.adapters.registry import AdapterRegistry
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.legacy_venue_sync import LEGACY_VENUE_SYNCERS
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.venue_health import VenueHealthTracker
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
    """Extract (equity_usd, has_open_activity, fingerprint) from a plugin snapshot."""
    equity_usd = 0.0
    open_count = 0
    fills_count = 0
    closed_count = 0

    if venue_id == "okx":
        for bal in snap.get("balances") or []:
            equity_usd += float(bal.get("totalEq") or 0.0)
        for fb in snap.get("funding") or []:
            if str(fb.get("ccy") or "").upper() in {"USDT", "USDC"}:
                equity_usd += float(fb.get("bal") or 0.0)
        open_count = sum(1 for p in (snap.get("positions") or []) if abs(float(p.get("pos") or 0.0)) > 0)
        fills_count = len(snap.get("fills") or [])
        closed_count = len(snap.get("positions_history") or [])
    elif venue_id == "bybit":
        wallet = snap.get("wallet") or {}
        for acct in wallet.get("list") or []:
            equity_usd += float(acct.get("totalEquity") or 0.0)
        funding = snap.get("funding") or {}
        for coin in funding.get("balance") or []:
            if str(coin.get("coin") or "").upper() in {"USDT", "USDC"}:
                equity_usd += float(coin.get("walletBalance") or 0.0)
        open_count = sum(1 for p in (snap.get("positions") or []) if abs(float(p.get("size") or 0.0)) > 0)
        fills_count = len(snap.get("executions") or [])
        closed_count = len(snap.get("closed_pnl") or [])
    else:
        equity_usd = float(snap.get("equity_usd") or 0.0)
        open_count = len(snap.get("positions") or [])
        fills_count = len(snap.get("fills") or snap.get("executions") or [])
        closed_count = len(snap.get("closed_trades") or snap.get("closed_pnl") or [])

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

    def _cached_venue_activity(self, venue_id: str) -> tuple[float, bool, tuple[int, ...]]:
        """Read last known equity & open-position activity for venues backed by local SQLite."""
        try:
            dash = self.ledger_service.get_dashboard()
            eq = 0.0
            tcount = 0
            for ex in dash.get("exchanges") or []:
                if str(ex.get("venue_id") or "").lower() == venue_id:
                    eq = float(ex.get("equity_usd") or 0.0)
                    tcount = int(ex.get("trade_count") or 0)
                    break
            open_cnt = sum(
                1 for p in (dash.get("open_positions") or []) if str(p.get("venue_id") or "").lower() == venue_id
            )
            return round(eq, 4), open_cnt > 0, (open_cnt, tcount)
        except Exception:
            return 0.0, False, (0, 0)

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
                    fp_before = tuple(int(x) for x in (prev_entry.get("fingerprint") or []))
                    client: Any = None
                    try:
                        client = adapter.create_client(cred)
                        if vid in LEGACY_VENUE_SYNCERS:
                            eq_usd, has_open, legacy_fp_before, fp_after = await LEGACY_VENUE_SYNCERS[vid](
                                client,
                                self.store.legacy,
                                now_s=now_s,
                                force_full=force_this,
                            )
                            sched = update_venue_schedule(
                                schedule,
                                vid,
                                now_s=now_s,
                                equity_usd=eq_usd,
                                has_open_activity=has_open,
                                fp_before=legacy_fp_before,
                                fp_after=fp_after,
                                base_interval_sec=base_iv,
                            )
                            results[vid] = {
                                "ok": True,
                                "mode": "full_ledger_sync",
                                "equity_usd": round(eq_usd, 2),
                                "poll_mode": sched["mode"],
                                "interval_sec": sched["interval_sec"],
                                "stage": sched["stage"],
                            }
                        elif hasattr(client, "fetch_full_snapshot"):
                            snap = await client.fetch_full_snapshot()
                            self.store.upsert_plugin_snapshot(vid, snap)
                            eq_usd, has_open, fp_after = _extract_snapshot_activity(vid, snap)
                            sched = update_venue_schedule(
                                schedule,
                                vid,
                                now_s=now_s,
                                equity_usd=eq_usd,
                                has_open_activity=has_open,
                                fp_before=fp_before or fp_after,
                                fp_after=fp_after,
                                base_interval_sec=base_iv,
                            )
                            results[vid] = {
                                "ok": True,
                                "mode": "plugin_snapshot",
                                "equity_usd": round(eq_usd, 2),
                                "poll_mode": sched["mode"],
                                "interval_sec": sched["interval_sec"],
                                "stage": sched["stage"],
                                "warnings": snap.get("warnings") or [],
                            }
                        else:
                            conn = await adapter.test_connection(cred)
                            eq_usd, has_open, fp_after = self._cached_venue_activity(vid)
                            sched = update_venue_schedule(
                                schedule,
                                vid,
                                now_s=now_s,
                                equity_usd=eq_usd,
                                has_open_activity=has_open,
                                fp_before=fp_before or fp_after,
                                fp_after=fp_after,
                                base_interval_sec=base_iv,
                            )
                            results[vid] = {
                                "ok": True,
                                "mode": "verified",
                                "detail": conn,
                                "poll_mode": sched["mode"],
                                "interval_sec": sched["interval_sec"],
                                "stage": sched["stage"],
                            }
                        self.health_tracker.record_success(vid)
                        any_synced = True
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
