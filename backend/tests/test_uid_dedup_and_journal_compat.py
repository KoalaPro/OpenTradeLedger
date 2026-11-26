"""UID deduplication, identity-outage rotation safety, deterministic secondary journal recovery, and goal rounding tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import httpx

from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.api.app import create_app
from opentradeledger.core.ai_reviewer import generate_ai_review
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.vault import CredentialVault, ExchangeCredentialRecord


def _make_bybit_mock_registry(
    *,
    identity_up: dict[str, bool],
    uid_by_key: dict[str, tuple[str, bool]],
    snapshot_by_uid: dict[str, dict[str, Any]],
):
    reg = build_default_registry()

    class ConfigurableBybitClient:
        def __init__(self, cred: ExchangeCredentialRecord) -> None:
            self.key = cred.api_key

        async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
            uid, _ = uid_by_key[self.key]
            snap = snapshot_by_uid[uid]
            return snap["wallet"]

        async def get_api_key_info(self) -> dict[str, Any]:
            if not identity_up.get("ok", True):
                raise RuntimeError("Bybit GET /v5/user/query-api HTTP 503: upstream unavailable")
            uid, is_master = uid_by_key[self.key]
            return {"userID": uid, "isMaster": is_master, "readOnly": 1}

        async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
            uid, _ = uid_by_key[self.key]
            return dict(snapshot_by_uid[uid])

        async def aclose(self) -> None:
            pass

    byb = reg.get("bybit")
    assert byb is not None
    byb.create_client = ConfigurableBybitClient  # type: ignore[method-assign]
    return reg


def _bybit_snap(eq: str, dep: str, order_id: str, sym: str, pnl: str) -> dict[str, Any]:
    return {
        "venue_id": "bybit",
        "wallet": {"list": [{"totalEquity": eq, "totalAvailableBalance": eq, "coin": []}]},
        "funding": {},
        "positions": [],
        "closed_pnl": [
            {
                "orderId": order_id,
                "symbol": sym,
                "side": "Sell",
                "qty": "1",
                "closedSize": "1",
                "avgEntryPrice": "1000",
                "avgExitPrice": "1100",
                "cumEntryValue": "1000",
                "cumExitValue": "1100",
                "openFee": "0.0",
                "closeFee": "0.0",
                "closedPnl": pnl,
                "createdTime": "1732200000000",
                "updatedTime": "1732203600000",
            }
        ],
        "executions": [],
        "fee_rates": [],
        "tx_logs": [],
        "deposits": [{"id": f"dep-{order_id}", "coin": "USDT", "amount": dep, "successAt": "1732100000000"}],
        "internal_deposits": [],
        "withdrawals": [],
        "tickers": {},
        "observed_rebate_ratio": 0.0,
        "warnings": [],
    }


def test_archived_account_blocks_duplicate_add_preserves_custom_label_and_allows_rebind(tmp_path: Path) -> None:
    identity_up = {"ok": True}
    uid_by_key = {
        "k-main": ("770011", True),
        "k-sub-1": ("770022", False),
        "k-sub-2": ("770022", False),
    }
    snaps = {
        "770011": _bybit_snap("1100.00", "1000.00", "ord-btc", "BTCUSDT", "100.00"),
        "770022": _bybit_snap("2200.00", "2000.00", "ord-eth", "ETHUSDT", "200.00"),
    }
    reg = _make_bybit_mock_registry(identity_up=identity_up, uid_by_key=uid_by_key, snapshot_by_uid=snaps)
    app = create_app(data_dir=tmp_path, registry=reg, auto_prompt_setup=False, enable_background_sync=False)
    http = TestClient(app)

    r1 = http.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "label": "主账户", "api_key": "k-main", "api_secret": "s1"}},
    )
    assert r1.status_code == 200

    r2 = http.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "bybit",
                "create_new": True,
                "label": "示例子账户",
                "api_key": "k-sub-1",
                "api_secret": "s2",
            }
        },
    )
    assert r2.status_code == 200
    sub_acct_id = r2.json()["account_id"]

    dash_before = http.get("/api/v1/dashboard").json()
    assert dash_before["summary"]["total_equity_usd"] == 3300.00

    # Archive sub-account with keep_data=true
    http.delete(f"/api/v1/exchanges/{sub_acct_id}?keep_data=true").raise_for_status()

    # Custom label "示例子账户" must be preserved in both /api/v1/exchanges and /api/v1/dashboard!
    ex_list = http.get("/api/v1/exchanges").json()
    byb_ad = next(a for a in ex_list["adapters"] if a["venue_id"] == "bybit")
    sub_row = next(a for a in byb_ad["accounts"] if a["account_id"] == sub_acct_id)
    assert sub_row["label"] == "示例子账户"
    dash_arch = http.get("/api/v1/dashboard").json()
    arch_card = next(e for e in dash_arch["exchanges"] if e["venue_id"] == sub_acct_id)
    assert "示例子账户" in arch_card["display_name_zh"]

    # Adding the SAME UID via create_new=True while archived MUST return 409 ARCHIVED_ACCOUNT_UID_EXISTS!
    dup_ver = http.post(
        "/api/v1/exchanges/verify",
        json={"venue_id": "bybit", "create_new": True, "label": "新子号", "api_key": "k-sub-2", "api_secret": "s3"},
    )
    assert dup_ver.status_code == 409
    assert dup_ver.json()["error_code"] == "ARCHIVED_ACCOUNT_UID_EXISTS"

    dup_sync = http.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "bybit",
                "create_new": True,
                "label": "新子号",
                "api_key": "k-sub-2",
                "api_secret": "s3",
            }
        },
    )
    assert dup_sync.status_code == 409
    assert dup_sync.json()["error_code"] == "ARCHIVED_ACCOUNT_UID_EXISTS"
    assert http.get("/api/v1/dashboard").json()["summary"]["total_equity_usd"] == 3300.00

    # Rebinding on the archived row itself succeeds and keeps total at 3,300 U!
    rebind = http.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "bybit",
                "account_id": sub_acct_id,
                "label": "示例子账户",
                "api_key": "k-sub-2",
                "api_secret": "s3",
                "expected_revision": "absent",
            }
        },
    )
    assert rebind.status_code == 200
    assert http.get("/api/v1/dashboard").json()["summary"]["total_equity_usd"] == 3300.00


def test_identity_outage_blocks_foreign_key_rotation_and_legacy_137_upgrade_safety(tmp_path: Path) -> None:
    identity_up = {"ok": True}
    uid_by_key = {
        "k-main-old": ("770011", True),
        "k-sub-foreign": ("770022", False),
    }
    snaps = {
        "770011": _bybit_snap("1100.00", "1000.00", "ord-btc", "BTCUSDT", "100.00"),
        "770022": _bybit_snap("2200.00", "2000.00", "ord-sol", "SOLUSDT", "200.00"),
    }

    # Part A: Bound account rejects key rotation when identity API is unavailable (503)
    d1 = tmp_path / "bound_outage"
    reg1 = _make_bybit_mock_registry(identity_up=identity_up, uid_by_key=uid_by_key, snapshot_by_uid=snaps)
    http1 = TestClient(create_app(data_dir=d1, registry=reg1, auto_prompt_setup=False, enable_background_sync=False))
    http1.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "label": "主号", "api_key": "k-main-old", "api_secret": "s1"}},
    ).raise_for_status()

    identity_up["ok"] = False
    ver_fail = http1.post(
        "/api/v1/exchanges/verify",
        json={"venue_id": "bybit", "account_id": "bybit", "label": "主号", "api_key": "k-sub-foreign", "api_secret": "s2"},
    )
    assert ver_fail.status_code == 409
    assert ver_fail.json()["error_code"] == "ACCOUNT_UID_UNAVAILABLE"

    sync_fail = http1.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "bybit",
                "account_id": "bybit",
                "label": "主号",
                "api_key": "k-sub-foreign",
                "api_secret": "s2",
            }
        },
    )
    assert sync_fail.status_code == 409
    assert sync_fail.json()["error_code"] == "ACCOUNT_UID_UNAVAILABLE"
    assert http1.get("/api/v1/dashboard").json()["summary"]["total_equity_usd"] == 1100.00

    # Part B: Legacy 1.3.7 database (credential has remote_uid="") rejects foreign key edit BEFORE first sync,
    # AND recovers UID backfill after a transient identity outage on sync #1!
    d2 = tmp_path / "legacy_137"
    store2 = LedgerStore(d2)
    vault2 = CredentialVault(d2)
    vault2.upsert(ExchangeCredentialRecord(venue_id="bybit", label="Bybit", api_key="k-main-old", api_secret="s1"))
    store2.upsert_plugin_snapshot("bybit", snaps["770011"])

    reg2 = _make_bybit_mock_registry(identity_up=identity_up, uid_by_key=uid_by_key, snapshot_by_uid=snaps)
    http2 = TestClient(create_app(data_dir=d2, registry=reg2, auto_prompt_setup=False, enable_background_sync=False))

    # Identity is still down -> sync #1 succeeds for balances, does NOT poison identity_backfilled_v1,
    # AND editing key while identity is down is rejected!
    rot_down = http2.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "api_key": "k-sub-foreign", "api_secret": "s2"}},
    )
    assert rot_down.status_code == 409
    assert rot_down.json()["error_code"] == "ACCOUNT_UID_UNAVAILABLE"

    http2.post("/api/v1/sync", json={"force_all": True}).raise_for_status()
    assert vault2.get_decrypted("bybit").remote_uid == ""

    # Identity recovers! Editing legacy credential directly with foreign UID must be caught (409 ACCOUNT_UID_MISMATCH)
    identity_up["ok"] = True
    rot_mismatch = http2.post(
        "/api/v1/exchanges/verify",
        json={"venue_id": "bybit", "api_key": "k-sub-foreign", "api_secret": "s2"},
    )
    assert rot_mismatch.status_code == 409
    assert rot_mismatch.json()["error_code"] == "ACCOUNT_UID_MISMATCH"

    # Subsequent sync #2 backfills UID 770011 cleanly, blocking duplicate +Add Account!
    http2.post("/api/v1/sync", json={"force_all": True}).raise_for_status()
    assert vault2.get_decrypted("bybit").remote_uid == "770011"
    dup_legacy = http2.post(
        "/api/v1/exchanges/verify",
        json={"venue_id": "bybit", "create_new": True, "api_key": "k-main-old", "api_secret": "s1"},
    )
    assert dup_legacy.status_code == 409


def test_deterministic_secondary_id_restores_journal_after_purge_and_isolates_okx_journals_and_ai(tmp_path: Path) -> None:
    # 1. Purge & re-import same sub-account UID restores preserved journal note
    identity_up = {"ok": True}
    uid_by_key = {"k-main": ("770011", True), "k-sub": ("770022", False), "k-sub-re": ("770022", False)}
    snaps = {
        "770011": _bybit_snap("1100.00", "1000.00", "ord-btc", "BTCUSDT", "100.00"),
        "770022": _bybit_snap("2200.00", "2000.00", "ord-eth", "ETHUSDT", "200.00"),
    }
    reg = _make_bybit_mock_registry(identity_up=identity_up, uid_by_key=uid_by_key, snapshot_by_uid=snaps)
    http = TestClient(create_app(data_dir=tmp_path / "byb", registry=reg, auto_prompt_setup=False, enable_background_sync=False))
    http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "bybit", "api_key": "k-main", "api_secret": "s1"}}).raise_for_status()
    ver1 = http.post("/api/v1/exchanges/verify", json={"venue_id": "bybit", "create_new": True, "api_key": "k-sub", "api_secret": "s2"}).json()
    sub_id_1 = ver1["account_id"]
    http.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "account_id": sub_id_1, "api_key": "k-sub", "api_secret": "s2", "expected_revision": "absent"}},
    ).raise_for_status()

    sub_trades = http.get(f"/api/v1/dashboard?venue={sub_id_1}").json()["trades"]
    assert len(sub_trades) == 1
    tid = sub_trades[0]["trade_id"]
    http.put(f"/api/v1/journal/{tid}", json={"tags": ["复盘保留"], "note": "子账户专属ETH笔记"}).raise_for_status()

    # Purge sub-account data completely, then re-add the same UID via create_new=True
    http.delete(f"/api/v1/exchanges/{sub_id_1}?keep_data=false").raise_for_status()
    ver2 = http.post("/api/v1/exchanges/verify", json={"venue_id": "bybit", "create_new": True, "api_key": "k-sub-re", "api_secret": "s3"}).json()
    assert ver2["account_id"] == sub_id_1
    http.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "account_id": ver2["account_id"], "api_key": "k-sub-re", "api_secret": "s3", "expected_revision": "absent"}},
    ).raise_for_status()
    reimported_trade = http.get(f"/api/v1/dashboard?venue={sub_id_1}").json()["trades"][0]
    assert reimported_trade["note"] == "子账户专属ETH笔记"
    assert reimported_trade["tags"] == ["复盘保留"]

    # 2. Cross-account OKX journal & AI review isolation (plus IPv6 [::1] local AI host!)
    d_okx = tmp_path / "okx_iso"
    store = LedgerStore(d_okx)
    vault = CredentialVault(d_okx)
    reg_okx = build_default_registry()
    vault.upsert(ExchangeCredentialRecord(venue_id="okx", label="OKX 主号", api_key="k1", api_secret="s1", passphrase="p1", remote_uid="9001"))
    vault.upsert(ExchangeCredentialRecord(venue_id="okx__acceptb", adapter_id="okx", label="OKX 子号", api_key="k2", api_secret="s2", passphrase="p2", remote_uid="9002"))

    shared_okx_snap = {
        "venue_id": "okx",
        "balances": [{"totalEq": "1050.0", "details": [{"ccy": "USDT", "eq": "1050.0", "availEq": "1050.0"}]}],
        "funding": [],
        "positions": [],
        "instruments": [{"instId": "BTC-USDT-SWAP", "ctVal": "0.01", "ctValCcy": "BTC"}],
        "positions_history": [
            {
                "instId": "BTC-USDT-SWAP",
                "posId": "shared-101",
                "direction": "long",
                "openAvgPx": "90000",
                "closeAvgPx": "95000",
                "closeTotalPos": "1",
                "pnl": "50.0",
                "realizedPnl": "50.0",
                "fee": "0",
                "fundingFee": "0",
                "cTime": "1794630000000",
                "uTime": "1794632400000",
            }
        ],
        "fills": [],
        "trade_fee": [],
        "bills": [],
        "asset_bills": [],
        "deposits": [{"depId": "d1", "ccy": "USDT", "amt": "1000.0", "state": "2", "ts": "1794600000000"}],
        "withdrawals": [],
        "copy_positions": [],
        "copy_history": [],
        "tickers": {},
    }
    store.upsert_plugin_snapshot("okx", shared_okx_snap)
    store.upsert_plugin_snapshot("okx__acceptb", shared_okx_snap)
    store.save_journal("okx_shared-101_1794632400000", tags=["planned"], note="primary-only-review-140", demo_mode=False)

    svc = LedgerService(store, vault, reg_okx, default_demo_mode=False)
    dash = svc.get_dashboard(range_key="all")
    trades_by_venue = {t["venue_id"]: t for t in dash["trades"]}
    assert trades_by_venue["okx"]["note"] == "primary-only-review-140"
    assert trades_by_venue["okx"]["tags"] == ["planned"]
    assert trades_by_venue["okx__acceptb"]["note"] == ""
    assert trades_by_venue["okx__acceptb"]["tags"] == []

    # Verify [::1] local IPv6 AI endpoint receives keyless request and secondary trade has empty note!
    captured_prompts: list[str] = []

    def ai_mock(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        captured_prompts.append(body["messages"][0]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "1. **纪律良好**：测试通过。"}}]})

    prefs = svc.update_preferences({"ai_base_url": "http://[::1]:11434/v1", "ai_model": "qwen-local"})

    async def _run_ai():
        async with httpx.AsyncClient(transport=httpx.MockTransport(ai_mock)) as hc:
            return await generate_ai_review(dash, prefs, client=hc)

    rev = asyncio.run(_run_ai())
    assert rev["execution_mode"] == "remote_llm"
    assert len(captured_prompts) == 1
    assert captured_prompts[0].count("primary-only-review-140") == 1


def test_goal_progress_decimal_rounding_avoids_binary_float_truncation(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()
    store.upsert_plugin_snapshot(
        "bybit",
        _bybit_snap("32022.00", "30000.00", "ord-goal", "BTCUSDT", "2022.00"),
    )
    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    svc.update_preferences({"goal_enabled": True, "goal_target_usd": 40000.0, "goal_label": "40k"})
    dash = svc.get_dashboard(range_key="all")
    # 32,022 / 40,000 = 80.055% -> rounds to 80.1%, NOT 80.0%!
    assert dash["goal"]["progress_pct"] == 80.1
