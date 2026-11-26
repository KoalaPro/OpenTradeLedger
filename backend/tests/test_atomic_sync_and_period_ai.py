"""Regression tests for the 3 black-box QA re-test scenarios:
1. Atomic credential update rollback when auth passes but historical sync times out.
2. Period-isolated (`range_key`) AI reviews & data-driven staleness (no cross-period 115-vs-8 trade bleed).
3. Explicit local-fallback vs AI mode metadata & human-readable scope fields.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from opentradeledger.adapters.base import AdapterMeta, BaseExchangeAdapter, VenueLedgerBundle, VenueSyncOutcome
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.api.app import create_app
from opentradeledger.core.store import LedgerStore


def test_atomic_exchange_connect_rolls_back_vault_and_partition_on_sync_timeout(tmp_path: Path) -> None:
    """When updating an API key, if auth passes but sync times out, old key & old SQLite partition must stay intact."""
    mode = {"fail_sync": False}

    class MockEasiAdapter(BaseExchangeAdapter):
        meta = AdapterMeta(
            venue_id="mockec",
            display_name="MockEC",
            display_name_zh="模拟易币",
            color_token="easicoin",
        )

        def create_client(self, cred):
            return cred

        async def test_connection(self, cred):
            if cred.api_key == "bad_key_0000":
                raise RuntimeError("HTTP 401: Invalid API key")
            return {"ok": True, "venue_id": "mockec", "read_only": True}

        async def sync_venue(self, client, store: LedgerStore, *, now_s: int, force_full: bool = False) -> VenueSyncOutcome:
            scope = store.venue_scope("mockec")
            # Simulate partial write before network timeout mid-backfill!
            scope.set_kv("live_bundle", {"owner_key": client.api_key, "equity": 999.0 if mode["fail_sync"] else 1058.6})
            scope.upsert_records(
                "closed_pnl",
                [{"id": f"tr_{client.api_key}", "ts": float(now_s)}],
                key_fn=lambda r: str(r["id"]),
                ts_fn=lambda r: float(r["ts"]),
            )
            if mode["fail_sync"]:
                raise TimeoutError("httpx.ReadTimeout: timed out fetching historical fills page 3")
            return VenueSyncOutcome(
                equity_usd=1058.6,
                has_open_activity=False,
                fp_before=(0,),
                fp_after=(1,),
                new_closed_delta=1,
            )

        def has_venue_data(self, store: LedgerStore) -> bool:
            return bool(store.venue_scope("mockec").get_kv("live_bundle"))

        def extract_spot_prices(self, store: LedgerStore) -> dict[str, float]:
            return {}

        def build_venue_ledger(self, store: LedgerStore, *, journals, now_s: float, global_spot_prices=None):
            lb = store.venue_scope("mockec").get_kv("live_bundle")
            if not lb:
                return None
            eq = float(lb.get("equity") or 0.0)
            return VenueLedgerBundle(
                venue_id="mockec",
                exchange_card={
                    "venue_id": "mockec",
                    "display_name": "MockEC",
                    "display_name_zh": "模拟易币",
                    "color_token": "easicoin",
                    "equity_usd": eq,
                    "available_margin_usd": eq,
                    "net_principal_usd": 1000.0,
                    "net_pnl_usd": round(eq - 1000.0, 2),
                    "trade_count": 1,
                    "rebate_rate_pct": 0.0,
                    "discount_rate_pct": 0.0,
                    "capabilities": {},
                },
                trades=[],
                open_positions=[],
                spot_holdings=[],
                reconcile_streams=[
                    {"key": "mockec_futures", "title_zh": "模拟易币净盈亏", "amount_usd": round(eq - 1000.0, 2)}
                ],
            )

        def build_fee_matrix_row(self, bundle, range_trades):
            return None

    reg = build_default_registry()
    reg.register(MockEasiAdapter())

    app = create_app(
        data_dir=tmp_path,
        demo_mode=False,
        registry=reg,
        enable_background_sync=False,
        auto_prompt_setup=False,
    )
    client = TestClient(app)

    # 1. Initial connect with old_key_1111 succeeds atomically
    r1 = client.post(
        "/api/v1/sync",
        json={
            "force_all": True,
            "venue_id": "mockec",
            "candidate_credential": {
                "venue_id": "mockec",
                "label": "OldKey",
                "api_key": "old_key_1111",
                "api_secret": "old_sec_1111",
            },
        },
    )
    assert r1.status_code == 200
    assert r1.json()["committed"] is True

    ex1 = next(a for a in client.get("/api/v1/exchanges").json()["adapters"] if a["venue_id"] == "mockec")
    assert ex1["configured"] is True
    assert ex1["credential"]["masked_api_key"] == "old_...1111"
    assert ex1["health"]["status"] == "connected"

    # 2. Update to new_key_2222 where verify PASSES, but sync_venue TIMES OUT mid-write!
    mode["fail_sync"] = True
    v_ok = client.post(
        "/api/v1/exchanges/verify",
        json={"venue_id": "mockec", "api_key": "new_key_2222", "api_secret": "new_sec_2222"},
    )
    assert v_ok.status_code == 200

    r_timeout = client.post(
        "/api/v1/sync",
        json={
            "force_all": True,
            "venue_id": "mockec",
            "candidate_credential": {
                "venue_id": "mockec",
                "label": "NewKey",
                "api_key": "new_key_2222",
                "api_secret": "new_sec_2222",
            },
        },
    )
    assert r_timeout.status_code == 502

    # Old credential AND old SQLite partition MUST remain 100% untouched!
    ex_after_cancel = next(a for a in client.get("/api/v1/exchanges").json()["adapters"] if a["venue_id"] == "mockec")
    assert ex_after_cancel["credential"]["masked_api_key"] == "old_...1111"
    assert ex_after_cancel["health"]["status"] == "connected"

    store = LedgerStore(tmp_path)
    assert store.venue_scope("mockec").get_kv("live_bundle")["owner_key"] == "old_key_1111"
    recs = store.venue_scope("mockec").load_records("closed_pnl")
    assert [r["id"] for r in recs] == ["tr_old_key_1111"]


def test_ai_review_isolated_by_range_key_and_data_driven_staleness(tmp_path: Path) -> None:
    """7d and all-time AI reviews must be stored & retrieved per range_key without cross-period bleed or false 2h stale."""
    import time

    now_s = int(time.time())
    app = create_app(
        data_dir=tmp_path,
        demo_mode=False,
        enable_background_sync=False,
        auto_prompt_setup=False,
    )
    client = TestClient(app)

    store = LedgerStore(tmp_path)
    g_scope = store.venue_scope("gate")
    g_scope.set_kv(
        "live_bundle",
        {
            "wallet_total": {"total": {"amount": "5000.00", "unrealised_pnl": "0.0"}, "details": {"spot": {"amount": "5000.00"}}},
            "futures_account": {"available": "5000.00"},
            "unified": {"unified_account_total_equity": "5000.00", "balances": {"USDT": {"available": "5000.00", "equity": "5000.00"}}},
        },
    )
    g_scope.upsert_records(
        "spot_account_book",
        [{"id": "dep1", "time": now_s - 40 * 86400, "currency": "USDT", "type": "deposit", "change": "4850.00"}],
        key_fn=lambda r: str(r["id"]),
        ts_fn=lambda r: float(r["time"]),
    )
    # 1 old trade (20 days ago) + 1 recent trade (2 days ago) -> all=2 trades, 7d=1 trade
    g_scope.upsert_records(
        "futures_closes",
        [
            {
                "contract": "BTC_USDT",
                "side": "long",
                "first_open_time": now_s - 20 * 86400 - 600,
                "time": now_s - 20 * 86400,
                "max_size": "100",
                "long_price": "90000",
                "short_price": "91020",
                "pnl_pnl": "102.00",
                "pnl_fee": "-2.00",
                "pnl_fund": "0.00",
                "pnl": "100.00",
            },
            {
                "contract": "ETH_USDT",
                "side": "short",
                "first_open_time": now_s - 2 * 86400 - 600,
                "time": now_s - 2 * 86400,
                "max_size": "50",
                "long_price": "3000",
                "short_price": "3051",
                "pnl_pnl": "51.00",
                "pnl_fee": "-1.00",
                "pnl_fund": "0.00",
                "pnl": "50.00",
            },
        ],
        key_fn=lambda r: f"{r['contract']}_{r['time']}",
        ts_fn=lambda r: float(r["time"]),
    )

    # 1. Generate review for 'all' (2 trades)
    rev_all = client.post("/api/v1/ai-review", json={"range_key": "all"}).json()
    assert rev_all["range_key"] == "all"
    assert rev_all["trade_count_snapshot"] == 2

    # 2. Switching to '7d' (1 trade) MUST NOT bleed the 2-trade 'all' report!
    dash_7d_before = client.get("/api/v1/dashboard?range=7d").json()
    assert dash_7d_before["summary"]["trade_count"] == 1
    assert dash_7d_before["ai_review"] is None

    # 3. Generating review on '7d' saves a separate 7d report (1 trade) without overwriting 'all' (2 trades)
    rev_7d = client.post("/api/v1/ai-review", json={"range_key": "7d"}).json()
    assert rev_7d["range_key"] == "7d"
    assert rev_7d["trade_count_snapshot"] == 1

    dash_7d_after = client.get("/api/v1/dashboard?range=7d").json()
    assert dash_7d_after["ai_review"]["range_key"] == "7d"
    assert dash_7d_after["ai_review"]["trade_count_snapshot"] == 1
    assert dash_7d_after["ai_review"]["stale"] is False

    # Even if background sync runs 5 hours later with 0 new trades, 'all' stays fresh (stale=False)!
    store.set_meta("last_sync_ms", int((now_s + 5 * 3600) * 1000))
    dash_all = client.get("/api/v1/dashboard?range=all").json()
    assert dash_all["ai_review"]["range_key"] == "all"
    assert dash_all["ai_review"]["trade_count_snapshot"] == 2
    assert dash_all["ai_review"]["stale"] is False

    # 4. Once a new 3rd trade arrives, 'all' and '7d' accurately detect staleness with specific reason!
    g_scope.upsert_records(
        "futures_closes",
        [
            {
                "contract": "SOL_USDT",
                "side": "long",
                "first_open_time": now_s - 3600,
                "time": now_s - 1800,
                "max_size": "10",
                "long_price": "200",
                "short_price": "201.1",
                "pnl_pnl": "11.00",
                "pnl_fee": "-1.00",
                "pnl_fund": "0.00",
                "pnl": "10.00",
            }
        ],
        key_fn=lambda r: f"{r['contract']}_{r['time']}",
        ts_fn=lambda r: float(r["time"]),
    )
    dash_all_stale = client.get("/api/v1/dashboard?range=all").json()
    assert dash_all_stale["ai_review"]["stale"] is True
    assert "2" in dash_all_stale["ai_review"]["stale_reason_zh"]
    assert "3" in dash_all_stale["ai_review"]["stale_reason_zh"]


def test_first_verified_account_leaves_demo_and_survives_password_setup(tmp_path, monkeypatch):
    from opentradeledger.security.vault import CredentialVault

    registry = build_default_registry()
    adapter = registry.get("easicoin")
    monkeypatch.setattr(adapter, "create_client", lambda cred: cred)

    async def fixture_sync(client, store, **kwargs):
        if client.api_key == "reject":
            raise TimeoutError("backfill timed out")
        store.venue_scope("easicoin").set_kv("audit_balance", 42)
        return VenueSyncOutcome(42, False, (), (1,))

    monkeypatch.setattr(adapter, "sync_venue", fixture_sync)
    app = create_app(data_dir=tmp_path, demo_mode=True, registry=registry, enable_background_sync=False)
    client = TestClient(app)
    candidate = {"venue_id": "easicoin", "api_key": "reject", "api_secret": "test-secret"}
    assert client.post("/api/v1/sync", json={"candidate_credential": candidate}).status_code == 502
    assert client.get("/api/v1/auth/status").json()["demo_bypass"]
    assert CredentialVault(tmp_path).get_decrypted("easicoin") is None

    candidate["api_key"] = "fixture-accept"
    accepted = client.post("/api/v1/sync", json={"candidate_credential": candidate}).json()
    assert accepted["committed"] is True
    assert client.get("/api/v1/auth/status").json()["require_setup"]
    assert accepted["auth_status"]["require_setup"]
    assert client.get("/api/v1/dashboard").status_code == 401

    restarted = TestClient(create_app(data_dir=tmp_path, demo_mode=True, registry=registry, enable_background_sync=False))
    assert restarted.get("/api/v1/auth/status").json()["require_setup"]
    assert restarted.post("/api/v1/auth/setup", json={"password": "test-password"}).status_code == 200
    assert restarted.get("/api/v1/dashboard").json()["demo_mode"] is False
    assert CredentialVault(tmp_path).get_decrypted("easicoin").api_key == "fixture-accept"
    assert LedgerStore(tmp_path).venue_scope("easicoin").get_kv("audit_balance") == 42

    # A user who has already entered the real ledger can edit a key in the demo's
    # settings without unexpectedly revealing their real balances on screen.
    restarted.post("/api/v1/demo", json={"enabled": True}).raise_for_status()
    assert restarted.post("/api/v1/sync", json={"candidate_credential": candidate}).json()["committed"]
    assert restarted.get("/api/v1/dashboard").json()["explicit_demo_mode"] is True


def test_invalid_goal_amount_keeps_previous_goal(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path, demo_mode=True, enable_background_sync=False))
    assert client.put("/api/v1/settings", json={"goal_enabled": True, "goal_target_usd": 12345}).is_success
    for invalid in [-1, 0, "nan", "unparseable"]:
        rejected = client.put("/api/v1/settings", json={"goal_target_usd": invalid, "goal_label": "new"})
        assert 400 <= rejected.status_code < 500
        previous = client.get("/api/v1/settings").json()
        assert previous["goal_target_usd"] == 12345
        assert previous["goal_label"] == ""


def test_local_metadata_save_failure_keeps_previous_credential_and_sync_state(tmp_path, monkeypatch):
    from opentradeledger.security.vault import CredentialVault

    registry = build_default_registry()
    adapter = registry.get("easicoin")
    monkeypatch.setattr(adapter, "create_client", lambda credential: credential)

    async def sync_fixture(client, store, **kwargs):
        store.venue_scope("easicoin").set_kv("label", client.label)
        return VenueSyncOutcome(len(client.label), False, (), (len(client.label),))

    monkeypatch.setattr(adapter, "sync_venue", sync_fixture)
    http = TestClient(create_app(data_dir=tmp_path, registry=registry, auto_prompt_setup=False, enable_background_sync=False))
    candidate = {"venue_id": "easicoin", "api_key": "fixture-key-one", "api_secret": "fixture-secret", "label": "Old"}
    assert http.post("/api/v1/sync", json={"candidate_credential": candidate}).json()["committed"]
    store = LedgerStore(tmp_path)
    previous = {k: store.get_meta(k) for k in ("sync_schedule", "venue_health", "last_sync_ms")}
    vault_bytes = (tmp_path / "vault.enc").read_bytes()

    original_save = LedgerStore.set_meta
    def fail_last_sync(self, key, value):
        if key == "last_sync_ms":
            raise OSError("fixture local write failure")
        return original_save(self, key, value)

    monkeypatch.setattr(LedgerStore, "set_meta", fail_last_sync)
    candidate.update(label="Replacement", api_key="fixture-key-two")
    failed = http.post("/api/v1/sync", json={"candidate_credential": candidate})
    assert failed.status_code >= 400
    assert failed.json().get("committed") is False
    assert (tmp_path / "vault.enc").read_bytes() == vault_bytes
    assert CredentialVault(tmp_path).get_decrypted("easicoin").label == "Old"
    assert store.venue_scope("easicoin").get_kv("label") == "Old"
    assert {k: store.get_meta(k) for k in previous} == previous
    assert failed.json()["error_code"] == "LOCAL_SAVE_FAILED"

    def fail_rollback(self, venue_id, snapshot):
        raise OSError("fixture rollback failure")

    monkeypatch.setattr(LedgerStore, "restore_venue_partition", fail_rollback)
    uncertain = http.post("/api/v1/sync", json={"candidate_credential": candidate})
    assert uncertain.status_code == 500
    assert uncertain.json()["error_code"] == "LOCAL_ROLLBACK_FAILED"
    assert "committed" not in uncertain.json()
