import sqlite3

from fastapi.testclient import TestClient

from opentradeledger.api.app import create_app
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault, ExchangeCredentialRecord


def test_public_health_omits_account_state_even_while_demo_is_selected(tmp_path, monkeypatch):
    monkeypatch.setenv("OTL_INITIAL_PASSWORD", "local-test-password")
    store = LedgerStore(tmp_path)
    store.set_meta("sync_schedule", {"okx": {"last_equity_usd": 1050}})
    client = TestClient(create_app(data_dir=tmp_path, demo_mode=True))
    health = client.get("/api/v1/health").json()
    assert health["status"] == "ok" and health["version"]
    assert {"adaptive_polling", "has_live_data", "demo_mode"}.isdisjoint(health)


def test_review_belongs_to_effective_dashboard_source_after_restart(tmp_path):
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("okx", {"balances": [{"totalEq": "1050", "details": []}]})
    def open_client():
        return TestClient(create_app(data_dir=tmp_path, demo_mode=False, auto_prompt_setup=False,
                                     enable_background_sync=False))
    client = open_client()
    report = client.post("/api/v1/ai-review", json={"mode": "local_rules", "range_key": "7d"}).json()
    assert report["model"]
    client.post("/api/v1/demo", json={"enabled": True}).raise_for_status()
    demo = client.get("/api/v1/dashboard?range=7d").json()
    assert demo["ai_review"].get("equity_snapshot_usd") != report["equity_snapshot_usd"]
    demo_report = client.post("/api/v1/ai-review", json={"mode": "local_rules", "range_key": "7d"}).json()
    client.post("/api/v1/demo", json={"enabled": False}).raise_for_status()
    live = open_client().get("/api/v1/dashboard?range=7d").json()
    assert live["ai_review"]["equity_snapshot_usd"] == report["equity_snapshot_usd"]
    assert live["ai_review"]["equity_snapshot_usd"] != demo_report["equity_snapshot_usd"]


def test_scope_review_lookup_keeps_unattributed_rows_and_finds_older_period(tmp_path):
    store = LedgerStore(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("""INSERT INTO otl_ai_reviews
            (generated_at,generated_ms,model,equity_snapshot_usd,payload_json)
            VALUES ('old',1,'test',123,'{"range_key":"all","markdown_commentary":"old note"}')""")
    assert store.load_latest_ai_review("all") is None
    store.save_ai_review({"range_key": "30d", "generated_ms": 100}, 10)
    for n in range(100):
        store.save_ai_review({"range_key": "7d", "generated_ms": 200 + n}, 20)
    assert store.load_latest_ai_review("30d")["generated_ms"] == 100
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM otl_ai_reviews WHERE generated_ms=1").fetchone()[0] == 1


def test_same_trade_key_can_have_independent_mode_notes_and_stale_tab_is_rejected(tmp_path):
    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("okx", {"balances": [{"totalEq": "1050", "details": []}]})
    client = TestClient(create_app(data_dir=tmp_path, demo_mode=True, auto_prompt_setup=False,
                                   enable_background_sync=False))
    path = "/api/v1/journal/demo_example"
    client.put(path, json={"demo_mode": True, "note": "sample"}).raise_for_status()
    client.post("/api/v1/demo", json={"enabled": False}).raise_for_status()
    assert client.put(path, json={"demo_mode": True, "note": "stale"}).status_code == 409
    client.put(path, json={"demo_mode": False, "note": "personal"}).raise_for_status()
    assert store.load_journals(demo_mode=True)["demo_example"]["note"] == "sample"
    assert store.load_journals()["demo_example"]["note"] == "personal"
    assert client.post("/api/v1/ai-review", json={"demo_mode": True, "mode": "local_rules"}).status_code == 409


def test_inflight_review_saves_to_source_at_start_of_generation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    store = LedgerStore(tmp_path)
    store.upsert_plugin_snapshot("okx", {"balances": [{"totalEq": "1050", "details": []}]})
    client = TestClient(create_app(data_dir=tmp_path, demo_mode=False, auto_prompt_setup=False,
                                   enable_background_sync=False))
    started, release = Event(), Event()

    async def slow_model(dashboard, prefs, *, force_local):
        assert dashboard["demo_mode"] is False
        started.set()
        assert release.wait(5)
        return {"range_key": "7d", "model": "test-model", "markdown_commentary": "formal"}

    monkeypatch.setattr("opentradeledger.api.app.generate_ai_review", slow_model)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(client.post, "/api/v1/ai-review", json={"range_key": "7d", "demo_mode": False})
        try:
            assert started.wait(5)
            client.post("/api/v1/demo", json={"enabled": True}).raise_for_status()
        finally:
            release.set()
        request.result(timeout=5).raise_for_status()
    assert store.load_latest_ai_review("7d")["markdown_commentary"] == "formal"
    assert store.load_latest_ai_review("7d", demo_mode=True) is None


def test_existing_trade_notes_keep_their_original_mode_on_upgrade(tmp_path):
    with sqlite3.connect(tmp_path / "opentradeledger.db") as conn:
        conn.execute("CREATE TABLE trade_journals(record_key TEXT PRIMARY KEY, tags_json TEXT, note TEXT, updated_at INTEGER)")
        conn.executemany("INSERT INTO trade_journals VALUES (?, '[]', ?, 1)", [
            ("demo_okx_BTC_USDT_1", "sample note"), ("ETH_USDT_1_2_long", "formal note"),
        ])
    store = LedgerStore(tmp_path)
    assert set(store.load_journals()) == {"ETH_USDT_1_2_long"}
    assert set(store.load_journals(demo_mode=True)) == {"demo_okx_BTC_USDT_1"}
    store.save_journal("demo_okx_BTC_USDT_1", [], "revised", demo_mode=True)
    restarted = LedgerStore(tmp_path)
    assert restarted.load_journals(demo_mode=True)["demo_okx_BTC_USDT_1"]["note"] == "revised"


def test_turning_off_explicit_demo_opens_empty_live_ledger_even_after_restart(tmp_path):
    def open_client():
        return TestClient(create_app(data_dir=tmp_path, demo_mode=True, auto_prompt_setup=False,
                                     enable_background_sync=False))

    client = open_client()
    demo = client.get("/api/v1/dashboard").json()
    assert demo["demo_mode"] and demo["summary"]["total_equity_usd"] > 0 and demo["trades"]
    client.post("/api/v1/demo", json={"enabled": False}).raise_for_status()
    for live_client in (client, open_client()):
        dash = live_client.get("/api/v1/dashboard").json()
        assert dash["demo_mode"] is False
        assert dash["has_live_data"] is False
        assert dash["summary"]["total_equity_usd"] == 0
        assert dash["exchanges"] == dash["trades"] == dash["open_positions"] == []
        assert dash["last_sync_ms"] == 0
    client.post("/api/v1/demo", json={"enabled": True}).raise_for_status()
    assert client.get("/api/v1/dashboard").json()["demo_mode"] is True


def test_manual_sync_without_enabled_accounts_reports_no_accounts_and_keeps_sync_time(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path, auto_prompt_setup=False,
                                   enable_background_sync=False))
    store = LedgerStore(tmp_path)
    for archived in (False, True):
        prior_sync = 1234 if archived else 0
        if archived:
            store.set_meta("last_sync_ms", prior_sync)
            store.upsert_plugin_snapshot("okx", {"balances": [{"totalEq": "1050", "details": []}]})
        sync = client.post("/api/v1/sync", json={"force_all": True}).json()
        assert sync["ok"] is False and sync["status"] == "no_accounts"
        assert sync["venues"] == {}
        assert sync["synced_ms"] == prior_sync
        dash = client.get("/api/v1/dashboard").json()
        assert dash["last_sync_ms"] == prior_sync
        assert dash["has_live_data"] is archived
        if archived:
            assert dash["summary"]["total_equity_usd"] == 1050
            assert dash["exchanges"][0]["health_status"] == "archived"


def test_configured_account_without_snapshot_and_zero_balance_are_not_first_accounts(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.upsert(ExchangeCredentialRecord(venue_id="okx", api_key="test", api_secret="test"))
    store = LedgerStore(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path, auto_prompt_setup=False,
                                   enable_background_sync=False))
    store.set_meta("venue_health", {"okx": {"status": "error"}})
    unverified = client.get("/api/v1/dashboard").json()
    assert unverified["demo_mode"] is False and unverified["has_live_data"] is True
    assert unverified["exchanges"] == []
    store.upsert_plugin_snapshot("okx", {"balances": [{"totalEq": "0", "details": []}]})
    store.set_meta("venue_health", {"okx": {"status": "connected"}})
    zero = client.get("/api/v1/dashboard").json()
    assert zero["has_live_data"] is True and zero["summary"]["total_equity_usd"] == 0
    assert zero["exchanges"][0]["connected"] is True
