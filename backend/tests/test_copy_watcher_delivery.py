"""Public positions, trade cursor and durable Bark delivery across polls and restarts."""

import asyncio
import json
import time
from urllib.parse import unquote

import httpx
import pytest

from opentradeledger.core.copy_watcher import CopyWatcherService
from opentradeledger.core.store import LedgerStore


A, B = "3900000000000000001", "3900000000000000002"


def fill(at, qty="0.01", side="BUY", pside="LONG", **extra):
    return dict(
        orderTime=at,
        symbol="BTCUSDT",
        side=side,
        positionSide=pside,
        type="MARKET",
        executedQty=qty,
        avgPrice="90000",
        totalPnl="0",
        **extra,
    )


class PublicFeed:
    def __init__(self, directory):
        self.store = LedgerStore(directory)
        prefs = self.store.get_preferences()
        prefs.notifications.bark_push_key = "sample-device"
        self.store.save_preferences(prefs)
        self.at = int(time.time() * 1000) - 60000
        self.orders = {pid: [fill(self.at)] for pid in (A, B)}
        self.full = {A: True, B: True}
        self.fail_history = set()
        self.fail_detail = set()
        self.reject = set()
        self.pushes = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.route))
        self.restart()

    def restart(self):
        self.service = CopyWatcherService(store=self.store, http_client=self.client)

    def route(self, req):
        path = req.url.path
        if req.url.host == "api.day.app":
            _, _, title, body = unquote(path).split("/", 3)
            group = req.url.params["group"]
            status = 503 if group in self.reject else 200
            self.pushes.append((group, title, body, status))
            return httpx.Response(status, json={"code": status})
        if path.endswith("/detail"):
            pid = req.url.params["portfolioId"]
            if pid in self.fail_detail:
                return httpx.Response(503)
            data = dict(
                leadPortfolioId=pid,
                nickname=pid[-2:],
                status="ACTIVE",
                portfolioType="PUBLIC",
                currentCopyCount=400 if self.full[pid] else 399,
                maxCopyCount=400,
            )
        elif path.endswith("ticker/price"):
            return httpx.Response(200, json={"price": "91000"})
        else:
            pid = json.loads(req.content)["portfolioId"]
            if path.endswith("/order-history") and pid in self.fail_history:
                return httpx.Response(503)
            data = {"list": self.orders[pid] if path.endswith("/order-history") else []}
        return httpx.Response(200, json={"code": "000000", "data": data})

    def add(self, pids=A):
        return asyncio.run(self.service.add_targets(pids))

    def poll(self):
        return {
            row["portfolio_id"]: row
            for row in asyncio.run(self.service.poll_once(force_trades=True))["items"]
        }


@pytest.fixture
def feed(tmp_path):
    f = PublicFeed(tmp_path)
    yield f
    asyncio.run(f.client.aclose())


def test_one_way_fill_does_not_claim_flat_or_close_without_a_baseline(feed):
    feed.orders[A] = []
    feed.add()
    feed.orders[A] = [fill(int(time.time() * 1000) + 1, pside="BOTH")]
    row = feed.poll()[A]
    assert row.get("uncertain_symbols") == ["BTCUSDT"]
    assert row["inferred_positions"] == [] and row["recent_orders"][0]["action_zh"] == "买入"
    assert len(feed.pushes) == 1 and "已平清" not in feed.pushes[0][2]
    feed.orders[A].append({**feed.orders[A][0], "side": "SELL", "totalPnl": "20"})
    assert feed.poll()[A]["recent_orders"][-1]["action_zh"] == "卖出"


def test_hedged_notification_describes_its_own_side(feed):
    feed.add()
    feed.orders[A].append(fill(feed.at + 1, "0.005", "SELL", "SHORT"))
    assert len(feed.poll()[A]["inferred_positions"]) == 2
    assert "在途空 0.005" in feed.pushes[-1][2]


def test_failed_history_preserves_snapshot_age_across_readd_and_recovery(feed):
    feed.add()
    good = feed.poll()[A]
    feed.fail_history.add(A)
    for stale in (feed.poll()[A], feed.add()[0]):
        assert stale.get("trade_stale") is True
        assert stale["trade_updated_at_ms"] == good["trade_updated_at_ms"] > 0
        assert stale["inferred_positions"] == good["inferred_positions"]
        assert stale["recent_orders"] == good["recent_orders"]
    feed.fail_history.clear()
    assert feed.poll()[A]["trade_stale"] is False


def test_full_order_batch_same_ms_late_identity_and_identical_fills(feed):
    feed.add()
    feed.orders[A] += [fill(feed.at + i) for i in range(1, 19)]
    assert len(feed.poll()[A]["recent_orders"]) == 15
    assert "18笔合并" in feed.pushes[-1][2]
    feed.orders[A] += [fill(feed.at + 18), fill(feed.at + 18, orderId="late-id")]
    feed.restart()
    feed.poll()
    assert "2笔合并" in feed.pushes[-1][2]
    feed.orders[A].append(fill(feed.at + 17, "0.021", orderId="another-id"))
    feed.poll()
    assert "数量 0.021" in feed.pushes[-1][2]
    feed.poll()
    assert len(feed.pushes) == 3


def test_bark_failure_retries_missing_trade_after_restart_and_history_rolloff(feed):
    feed.add()
    feed.full[A] = False
    feed.orders[A].append(fill(feed.at + 1, "0.025"))
    feed.reject.update({"binance-copy-slot", "binance-copy-trade"})
    row = feed.poll()[A]
    assert row["last_alert_ms"] == 0 and row.get("pending_alert_count") == 2
    feed.orders[A].clear()
    feed.reject.clear()
    feed.restart()
    row = feed.poll()[A]
    assert row["last_alert_ms"] > 0 and row["pending_alert_count"] == 0
    assert [push[3] for push in feed.pushes] == [503, 503, 200, 200]
    feed.restart()
    feed.poll()
    assert len(feed.pushes) == 4


def test_cooldown_persists_across_duplicate_save_and_restart_but_reopen_rings(feed):
    feed.add()
    feed.full[A] = False
    sent_ms = feed.poll()[A]["last_alert_ms"]
    assert sent_ms > 0
    assert feed.add()[0]["last_alert_ms"] == sent_ms
    feed.restart()
    feed.poll()
    assert len(feed.pushes) == 1
    feed.full[A] = True
    feed.poll()
    feed.full[A] = False
    feed.poll()
    assert len(feed.pushes) == 2


def test_pause_cancels_only_that_targets_pending_and_peer_failure_is_isolated(feed):
    feed.add(A + " " + B)
    feed.reject.add("binance-copy-trade")
    for pid in (A, B):
        feed.orders[pid].append(fill(feed.at + 1))
    first = feed.poll()
    assert [first[p]["pending_alert_count"] for p in (A, B)] == [1, 1]
    feed.service.update_target(A, enabled=False)
    feed.fail_detail.add(A)
    feed.reject.clear()
    feed.poll()
    assert [x[1] for x in feed.pushes if x[3] == 200] == ["📊 02 BTCUSDT 开多"]
    feed.service.update_target(A, enabled=True)
    feed.poll()
    assert len(feed.pushes) == 3


@pytest.mark.parametrize(
    "response,accepted",
    [
        (httpx.Response(200, json={"code": 200}), True),
        (httpx.Response(200, json={"code": "200"}), True),
        (httpx.Response(200, json={"code": 400, "message": "invalid sample-device"}), False),
        (httpx.Response(200, json={}), False),
        (httpx.Response(200, text="<html>Sign in</html>"), False),
        (httpx.Response(204), False),
    ],
)
def test_bark_requires_protocol_acknowledgement(feed, response, accepted):
    from opentradeledger.core.notifier import dispatch_notification

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
            return await dispatch_notification(
                feed.store.get_preferences().notifications, title="signal", body="test", client=client
            )

    result = asyncio.run(run())
    assert result["ok"] == accepted
    assert "sample-device" not in str(result["errors"])


def test_rebinding_drops_old_device_backlog_but_new_events_can_alert(feed):
    feed.add()
    feed.reject.update({"binance-copy-trade", "binance-copy-slot"})
    feed.full[A] = False
    feed.orders[A].append(fill(feed.at + 1, "0.02"))
    feed.poll()
    assert len(feed.pushes) == 2
    prefs = feed.store.get_preferences()
    prefs.notifications.bark_push_key = "different-device"
    feed.store.save_preferences(prefs)
    feed.reject.clear()
    feed.restart()
    item = feed.poll()[A]
    assert len(feed.pushes) == 2 and item["pending_alert_count"] == 0
    feed.orders[A].append(fill(feed.at + 2, "0.03"))
    feed.poll()
    assert len(feed.pushes) == 3 and "数量 0.03" in feed.pushes[-1][2]
    feed.full[A] = True
    feed.poll()
    feed.full[A] = False
    feed.poll()
    assert feed.pushes[-1][0] == "binance-copy-slot"


def test_removed_target_and_unbound_device_do_not_receive_old_trades(feed):
    feed.add()
    feed.reject.add("binance-copy-trade")
    feed.orders[A].append(fill(feed.at + 1))
    feed.poll()
    feed.service.remove_target(A)
    feed.add()
    feed.reject.clear()
    feed.poll()
    assert len(feed.pushes) == 1
    feed.orders[A].append(fill(feed.at + 2))
    feed.reject.add("binance-copy-trade")
    feed.poll()
    prefs = feed.store.get_preferences()
    prefs.notifications.bark_push_key = ""
    feed.store.save_preferences(prefs)
    assert feed.poll()[A]["pending_alert_count"] == 0
    prefs.notifications.bark_push_key = "sample-device"
    feed.store.save_preferences(prefs)
    feed.reject.clear()
    feed.poll()
    assert len(feed.pushes) == 2


def test_stale_slot_does_not_count_as_available_and_recovery_isolated(feed):
    feed.add(A + " " + B)
    feed.full[A] = feed.full[B] = False
    feed.poll()
    feed.fail_detail.add(A)
    asyncio.run(feed.service.poll_once(force_trades=True))
    st = feed.service.get_status()
    assert st["open_slot_count"] == 1
    a, b = st["items"]
    assert a["probe_error"] and a["slot_updated_at_ms"] > 0
    assert b["has_open_slot"] and not b["probe_error"]


def test_legacy_integer_watermark_does_not_replay_old_records(feed):
    from opentradeledger.core.copy_watcher import WATCHER_META_KEY

    feed.add()
    state = feed.store.get_meta(WATCHER_META_KEY)
    state["watermarks"][A] = feed.at
    feed.store.set_meta(WATCHER_META_KEY, state)
    feed.restart()
    feed.orders[A].append(fill(feed.at + 1))
    feed.poll()
    feed.poll()
    assert len(feed.pushes) == 1 and "笔合并" not in feed.pushes[0][2]


def test_slow_target_does_not_delay_a_healthy_peers_slot_alarm(tmp_path):
    async def scenario():
        feed = PublicFeed(tmp_path)
        original = feed.route
        started, healthy, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def slow(req):
            if req.url.path.endswith("/detail") and req.url.params["portfolioId"] == A:
                started.set()
                await release.wait()
            response = original(req)
            if req.url.host == "api.day.app" and "02 空出" in feed.pushes[-1][1]:
                healthy.set()
            return response

        await feed.client.aclose()
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as hc:
            feed.client = hc
            feed.restart()
            feed.service.set_target_ids_bulk([A, B])
            feed.full[B] = False
            poll = asyncio.create_task(feed.service.poll_once())
            try:
                await asyncio.wait_for(started.wait(), 1)
                await asyncio.wait_for(healthy.wait(), 1)
            finally:
                release.set()
                await poll

    asyncio.run(scenario())


def test_readd_cannot_replace_a_newer_success_while_waiting_on_public_history(tmp_path):
    async def scenario():
        feed = PublicFeed(tmp_path)
        original = feed.route
        waiting, resume = asyncio.Event(), asyncio.Event()
        block_one = False

        async def lagged(req):
            nonlocal block_one
            if block_one and req.url.path.endswith("/order-history"):
                block_one = False
                waiting.set()
                await resume.wait()
                return httpx.Response(503)
            return original(req)

        await feed.client.aclose()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lagged)) as client:
            feed.client = client
            feed.restart()
            await feed.service.add_targets(A)
            block_one = True
            repeat = asyncio.create_task(feed.service.add_targets(A))
            await waiting.wait()
            await asyncio.sleep(0.005)
            feed.orders[A].append(fill(feed.at + 1))
            await feed.service.poll_once(force_trades=True)
            resume.set()
            latest = (await repeat)[0]
            assert latest["inferred_positions"][0]["qty"] == 0.02 and not latest["trade_stale"]

    asyncio.run(scenario())


@pytest.mark.parametrize("history", ["position-history", "order-history"])
def test_malformed_success_envelope_preserves_previous_positions(feed, history):
    feed.add()

    def incomplete(req):
        if req.url.path.endswith("/" + history):
            return httpx.Response(200, json={"code": "000000", "data": {}})
        return feed.route(req)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(incomplete)) as client:
            service = CopyWatcherService(store=feed.store, http_client=client)
            return (await service.poll_once(force_trades=True))["items"][0]

    row = asyncio.run(scenario())
    assert row["trade_stale"] and row["inferred_positions"][0]["qty"] == 0.01
