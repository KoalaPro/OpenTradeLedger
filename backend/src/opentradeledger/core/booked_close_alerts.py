"""Durable delivery of private Bark alerts derived from settled ledger rows."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import NamedTuple
from uuid import uuid4

from opentradeledger.adapters.base import BaseExchangeAdapter
from opentradeledger.config import NotificationSettings
from opentradeledger.core.account_partition import AccountScopedStore
from opentradeledger.core.notifier import bark_destination, dispatch_notification
from opentradeledger.core.store import PRIVATE_CLOSE_ALERTS_KEY, LedgerStore

logger = logging.getLogger("opentradeledger.sync")


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


class BookedCloseAlerts:
    """Keep the settlement cursor and its pending batches in one account-scoped transaction."""

    def __init__(self, store: LedgerStore, account_id: str, settings: NotificationSettings) -> None:
        self.store = store
        self.scope = store.venue_scope(account_id)
        self.settings = settings
        self.destination = bark_destination(settings)
        self.subscription = ""

    @staticmethod
    def reset_baseline(store: LedgerStore, account_id: str) -> None:
        with store.venue_scope(account_id).edit_kv_dict(PRIVATE_CLOSE_ALERTS_KEY) as state:
            state.pop("receipts", None)

    def prepare(self, adapter: BaseExchangeAdapter, scoped_store: AccountScopedStore, now_s: int) -> None:
        if not self.destination:
            return
        state = self.scope.get_kv(PRIVATE_CLOSE_ALERTS_KEY, {})
        if state.get("destination") == self.destination and "receipts" in state:
            self.subscription = state["subscription"]
            return
        # Seed before the adapter writes. First import has no prior history to compare.
        baseline = _booked_exits(adapter, scoped_store, now_s)
        with self.scope.edit_kv_dict(PRIVATE_CLOSE_ALERTS_KEY) as state:
            if self.settings != self.store.get_preferences().notifications:
                return
            if state.get("destination") != self.destination:
                state.clear()
                state.update(destination=self.destination, subscription=uuid4().hex, pending=[])
            if "receipts" not in state:
                state["receipts"] = None if baseline is None else [[*k, *r] for k, r in baseline.items()]
            self.subscription = state["subscription"]

    def observe(
        self, adapter: BaseExchangeAdapter, scoped_store: AccountScopedStore, now_s: int, account_title: str, equity: float
    ) -> None:
        if not self.subscription:
            return
        current = _booked_exits(adapter, scoped_store, now_s)
        if current is None:
            return
        with self.scope.edit_kv_dict(PRIVATE_CLOSE_ALERTS_KEY) as state:
            if self.subscription != state.get("subscription"):
                return
            saved = state.get("receipts")
            if saved is not None:
                previous = {tuple(row[:4]): ExitReceipt(*row[4:]) for row in saved}
                count = _new_booked_exits(previous, current)
                if count:
                    state["pending"].append({
                        "title": f"OpenTradeLedger · {account_title} 新平仓入账",
                        "body": f"{account_title} 新增 {count} 笔减仓或平仓入账记录，当前权益 {equity:.2f} USDT。",
                    })
                # A truncated response or an older concurrent sync cannot unbook an exit.
                current = {**previous, **current}
            state["receipts"] = [[*k, *r] for k, r in current.items()]

    async def deliver(self) -> None:
        if not self.destination:
            return
        while True:
            with self.scope.edit_kv_dict(PRIVATE_CLOSE_ALERTS_KEY) as state:
                if self.destination != state.get("destination") or not state["pending"]:
                    return
                batch = state["pending"][0]
                if batch.get("lease_until", 0) > time.time():
                    return
                claim = uuid4().hex
                batch.update(claim=claim, lease_until=time.time() + 60)
            acknowledged = False
            try:
                # Bound the entire request below the lease, including a slow streaming response.
                async with asyncio.timeout(30):
                    result = await dispatch_notification(self.settings, title=batch["title"], body=batch["body"])
                acknowledged = result["ok"]
            except Exception:
                logger.exception("Could not deliver private Bark notification for %s", self.scope.venue_id)
            finally:
                with self.scope.edit_kv_dict(PRIVATE_CLOSE_ALERTS_KEY) as state:
                    queue = state.get("pending")
                    if queue and queue[0].get("claim") == claim:
                        if acknowledged:
                            queue.pop(0)
                        else:
                            queue[0].pop("claim", None)
                            queue[0].pop("lease_until", None)
            if not acknowledged:
                return
