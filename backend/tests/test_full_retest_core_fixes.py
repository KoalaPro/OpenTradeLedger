"""Comprehensive regression suite for core backend & multi-account security retest fixes."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import math
from pathlib import Path
import time
from typing import Any

from fastapi.testclient import TestClient
import httpx
import pytest

from opentradeledger.adapters.base import (
    AdapterMeta,
    BaseExchangeAdapter,
    VenueLedgerBundle,
    VenueSyncOutcome,
)
from opentradeledger.adapters.bitget.client import BitgetReadOnlyClient
from opentradeledger.adapters.bybit.client import BybitReadOnlyClient
from opentradeledger.adapters.okx.client import OkxReadOnlyClient
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.adapters.stock_channels import _f2
from opentradeledger.api.app import create_app
from opentradeledger.core.account_partition import (
    AccountIdentityError,
    stamp_bundle_for_account,
)
from opentradeledger.core.copy_watcher import PublicCopyWatcherClient
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.sync_manager import SyncManager
from opentradeledger.core.venue_health import (
    IdentityProbeTransientError,
    VenueHealthTracker,
    is_transient_upstream_error,
)
from opentradeledger.core.venue_primitives import f2
from opentradeledger.security.local_auth import AUTH_COOKIE_NAME, LocalAuthManager
from opentradeledger.security.readonly_guard import ReadOnlyViolationError, assert_readonly_request
from opentradeledger.security.vault import CredentialVault, ExchangeCredentialRecord


def test_decimal_half_up_rounding_f2_and_stock_f2() -> None:
    """f2 and _f2 must quantize via Decimal ROUND_HALF_UP without IEEE-754 binary float drift."""
    assert f2(Decimal("80.35"), 1) == 80.4
    assert f2(80.35, 1) == 80.4
    assert f2(Decimal("1.005"), 2) == 1.01
    assert f2(1.005, 2) == 1.01
    assert _f2(Decimal("80.35"), 1) == 80.4
    assert _f2(80.35, 1) == 80.4
    assert _f2(Decimal("1.005"), 2) == 1.01
    assert _f2(1.005, 2) == 1.01

    z1 = f2(Decimal("-0.00001"), 2)
    z2 = _f2(Decimal("-0.00001"), 2)
    assert z1 == 0.0 and math.copysign(1.0, z1) == 1.0
    assert z2 == 0.0 and math.copysign(1.0, z2) == 1.0


def test_transient_upstream_detection_aclose_and_bitget_uta_auth_not_downgraded() -> None:
    """Verify transient upstream classifier, client.aclose() on OKX/Bybit, identity 503 propagation, and Bitget UTA auth."""
    assert is_transient_upstream_error(TimeoutError("read timed out")) is True
    assert is_transient_upstream_error(httpx.ConnectTimeout("connect timed out")) is True
    assert is_transient_upstream_error(RuntimeError("HTTP 503 Service Unavailable")) is True
    assert is_transient_upstream_error(RuntimeError("HTTP 429 Too Many Requests")) is True
    assert is_transient_upstream_error(RuntimeError("Binance code -1003 Way too many requests")) is True
    assert is_transient_upstream_error(RuntimeError("OKX code 50011 Rate limit reached")) is True
    assert is_transient_upstream_error(RuntimeError("Bybit retCode=10006 too many visits")) is True
    assert is_transient_upstream_error(RuntimeError("KuCoin code 429000 rate limit")) is True
    assert is_transient_upstream_error(RuntimeError("HTTP 401 -2015 Invalid API-key")) is False
    assert is_transient_upstream_error(RuntimeError("Bitget 40014 Invalid passphrase")) is False

    reg = build_default_registry()
    closed_log: list[str] = []

    class TrackingOkxClient:
        async def get_account_balance(self) -> list[dict[str, Any]]:
            return [{"totalEq": "100"}]

        async def request_readonly(self, method: str, path: str) -> list[dict[str, Any]]:
            raise RuntimeError("OKX GET /api/v5/account/config HTTP 503: Service Unavailable")

        async def aclose(self) -> None:
            closed_log.append("okx")

    class TrackingBybitClient:
        async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
            return {"list": [{"totalEquity": "200"}]}

        async def get_api_key_info(self) -> dict[str, Any]:
            raise RuntimeError("Bybit GET /v5/user/query-api retCode=10006: rate limit")

        async def aclose(self) -> None:
            closed_log.append("bybit")

    okx_ad = reg.get("okx")
    byb_ad = reg.get("bybit")
    bg_ad = reg.get("bitget")
    assert okx_ad is not None and byb_ad is not None and bg_ad is not None
    okx_ad.create_client = lambda cred: TrackingOkxClient()  # type: ignore[method-assign]
    byb_ad.create_client = lambda cred: TrackingBybitClient()  # type: ignore[method-assign]

    bg_paths: list[str] = []

    def bg_handler(req: httpx.Request) -> httpx.Response:
        bg_paths.append(req.url.path)
        if req.headers.get("ACCESS-KEY") == "transient-503":
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(400, json={"code": "40014", "msg": "Invalid ACCESS-PASSPHRASE"})

    async def _run() -> None:
        okx_cred = ExchangeCredentialRecord(venue_id="okx", api_key="k", api_secret="s", passphrase="p")
        byb_cred = ExchangeCredentialRecord(venue_id="bybit", api_key="k", api_secret="s")

        with pytest.raises(IdentityProbeTransientError):
            await okx_ad.test_connection(okx_cred)
        with pytest.raises(IdentityProbeTransientError):
            await byb_ad.test_connection(byb_cred)
        assert closed_log == ["okx", "bybit"]

        probed = await byb_ad.probe_identity(byb_cred)
        assert "_transient_error" in probed
        assert "10006" in probed["_transient_error"]
        with pytest.raises(IdentityProbeTransientError):
            await byb_ad.probe_identity(byb_cred, raise_on_transient=True)

        async with httpx.AsyncClient(transport=httpx.MockTransport(bg_handler)) as hc:
            from opentradeledger.adapters.compat_creds import BitgetCredentials

            bg_ad.create_client = lambda cred: BitgetReadOnlyClient(  # type: ignore[method-assign]
                BitgetCredentials(api_key=cred.api_key, api_secret=cred.api_secret, passphrase=cred.passphrase),
                http_client=hc,
            )
            with pytest.raises(Exception) as exc_auth:
                await bg_ad.test_connection(
                    ExchangeCredentialRecord(venue_id="bitget", api_key="bad-pass", api_secret="s", passphrase="wrong")
                )
            assert "40014" in str(exc_auth.value)
            # Must NOT fall back to /api/v2/spot/account/assets on 40014 auth error!
            assert bg_paths == ["/api/v3/account/assets"]

            bg_paths.clear()
            with pytest.raises(Exception):
                await bg_ad.test_connection(
                    ExchangeCredentialRecord(venue_id="bitget", api_key="transient-503", api_secret="s", passphrase="p")
                )
            assert bg_paths == ["/api/v3/account/assets"]

    asyncio.run(_run())


def test_identity_outage_blocks_first_save_and_add_account_with_502(tmp_path: Path) -> None:
    """When identity endpoint returns 503 on initial connect or +Add Account, verify/sync/save must fail (502) and never save."""
    id_state = {"up": False}
    reg = build_default_registry()

    class OutageBybitClient:
        def __init__(self, cred: ExchangeCredentialRecord) -> None:
            self.key = cred.api_key

        async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
            return {"list": [{"totalEquity": "1500.00", "totalAvailableBalance": "1500.00", "coin": []}]}

        async def get_api_key_info(self) -> dict[str, Any]:
            if not id_state["up"]:
                raise RuntimeError("Bybit GET /v5/user/query-api HTTP 503: Service Unavailable")
            return {"userID": "9001" if self.key == "k1" else "9002", "isMaster": True, "readOnly": 1}

        async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
            return {
                "venue_id": "bybit",
                "wallet": {"list": [{"totalEquity": "1500.00", "totalAvailableBalance": "1500.00", "coin": []}]},
                "funding": {},
                "positions": [],
                "closed_pnl": [],
                "executions": [],
                "fee_rates": [],
                "tx_logs": [],
                "deposits": [{"id": f"d-{self.key}", "coin": "USDT", "amount": "1500.00", "successAt": "1732100000000"}],
                "internal_deposits": [],
                "withdrawals": [],
                "tickers": {},
                "warnings": [],
            }

        async def aclose(self) -> None:
            pass

    reg.get("bybit").create_client = OutageBybitClient  # type: ignore[union-attr, method-assign]
    http = TestClient(create_app(data_dir=tmp_path, registry=reg, auto_prompt_setup=False, enable_background_sync=False))

    # 1. Brand-new first connect while identity endpoint returns 503 -> HTTP 502, nothing saved!
    v1 = http.post("/api/v1/exchanges/verify", json={"venue_id": "bybit", "api_key": "k1", "api_secret": "s1"})
    assert v1.status_code == 502
    s1 = http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "bybit", "api_key": "k1", "api_secret": "s1"}})
    assert s1.status_code == 502
    assert CredentialVault(tmp_path).list_decrypted() == []

    # 2. Identity recovers -> k1 connects cleanly; then goes down again -> +Add Account (create_new=True) rejected!
    id_state["up"] = True
    assert http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "bybit", "api_key": "k1", "api_secret": "s1"}}).status_code == 200

    id_state["up"] = False
    v2 = http.post("/api/v1/exchanges/verify", json={"venue_id": "bybit", "create_new": True, "api_key": "k2", "api_secret": "s2"})
    assert v2.status_code == 502
    s2 = http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "bybit", "create_new": True, "api_key": "k2", "api_secret": "s2"}})
    assert s2.status_code == 502
    e2 = http.post("/api/v1/exchanges", json={"venue_id": "bybit", "create_new": True, "api_key": "k2", "api_secret": "s2"})
    assert e2.status_code == 502
    assert len(CredentialVault(tmp_path).list_decrypted()) == 1


def test_legacy_137_live_bundle_uid_prevents_duplicate_add_and_unattributed_archive_blocks_foreign_key(tmp_path: Path) -> None:
    """1.3.7 legacy live_bundle UID blocks duplicate add, and UID-less archive enforces historical fingerprint overlap."""
    # Part A: Legacy 1.3.7 Gate partition has UID inside live_bundle.account_detail.user_id (no account_uid KV)
    d_gate = tmp_path / "gate_137"
    store_g = LedgerStore(d_gate)
    vault_g = CredentialVault(d_gate)
    vault_g.upsert(ExchangeCredentialRecord(venue_id="gate", label="Gate 1.3.7", api_key="old-g-key", api_secret="s1"))
    store_g.venue_scope("gate").set_kv(
        "live_bundle",
        {
            "account_detail": {"user_id": 66554433},
            "wallet_total": {"total": {"amount": "2000.00"}, "details": {"spot": {"amount": "2000.00"}}},
            "unified": {"unified_account_total_equity": "2000.00", "balances": {"USDT": {"equity": "2000.00"}}},
            "futures_account": {"available": "2000.00", "total": "2000.00"},
        },
    )

    reg_g = build_default_registry()

    class MockGateClient:
        def __init__(self, cred: ExchangeCredentialRecord) -> None:
            self.key = cred.api_key

        async def raw_get(self, path: str) -> Any:
            if path == "/wallet/total_balance":
                return {"total": {"amount": "2000.00"}}
            if path == "/account/detail":
                return {"user_id": 66554433}
            if path == "/sub_accounts":
                return []
            return {}

        async def aclose(self) -> None:
            pass

    reg_g.get("gate").create_client = MockGateClient  # type: ignore[union-attr, method-assign]
    http_g = TestClient(create_app(data_dir=d_gate, registry=reg_g, auto_prompt_setup=False, enable_background_sync=False))

    for endpoint, payload in (
        ("/api/v1/exchanges/verify", {"venue_id": "gate", "create_new": True, "api_key": "new-dup-g", "api_secret": "s2"}),
        ("/api/v1/exchanges", {"venue_id": "gate", "create_new": True, "api_key": "new-dup-g", "api_secret": "s2"}),
        ("/api/v1/sync", {"candidate_credential": {"venue_id": "gate", "create_new": True, "api_key": "new-dup-g", "api_secret": "s2"}}),
    ):
        resp = http_g.post(endpoint, json=payload)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "DUPLICATE_ACCOUNT_UID"

    # Part B: Legacy 1.3.7 Bybit unattributed archive (NO UID anywhere, has BTC + ancient DOGE trades)
    d_byb = tmp_path / "bybit_137"
    store_b = LedgerStore(d_byb)
    old_btc_trade = {
        "orderId": "btc-ord-100",
        "symbol": "BTCUSDT",
        "side": "Sell",
        "qty": "0.1",
        "closedSize": "0.1",
        "avgEntryPrice": "80000",
        "avgExitPrice": "82000",
        "cumEntryValue": "8000",
        "cumExitValue": "8200",
        "openFee": "0",
        "closeFee": "0",
        "closedPnl": "200.00",
        "createdTime": "1720000000000",
        "updatedTime": "1720003600000",
    }
    ancient_doge_trade = {
        "orderId": "doge-ancient-001",
        "symbol": "DOGEUSDT",
        "side": "Sell",
        "qty": "1000",
        "closedSize": "1000",
        "avgEntryPrice": "0.10",
        "avgExitPrice": "0.15",
        "cumEntryValue": "100",
        "cumExitValue": "150",
        "openFee": "0",
        "closeFee": "0",
        "closedPnl": "50.00",
        "createdTime": "1700000000000",
        "updatedTime": "1700003600000",
    }
    store_b.upsert_plugin_snapshot(
        "bybit",
        {
            "venue_id": "bybit",
            "wallet": {"list": [{"totalEquity": "1250.00", "totalAvailableBalance": "1250.00", "coin": []}]},
            "closed_pnl": [ancient_doge_trade, old_btc_trade],
            "deposits": [{"id": "dep-1000", "coin": "USDT", "amount": "1000.00", "successAt": "1699000000000"}],
            "withdrawals": [],
            "executions": [],
        },
    )
    snap_before = store_b.snapshot_venue_partition("bybit")

    reg_b = build_default_registry()

    class MockBybitUpgradeClient:
        def __init__(self, cred: ExchangeCredentialRecord) -> None:
            self.key = cred.api_key

        async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
            eq = "3000.00" if self.key == "foreign-eth-key" else "1350.00"
            return {"list": [{"totalEquity": eq, "totalAvailableBalance": eq, "coin": []}]}

        async def get_api_key_info(self) -> dict[str, Any]:
            if self.key == "foreign-eth-key":
                return {"userID": "2002", "isMaster": False, "readOnly": 1}
            return {"userID": "1001", "isMaster": True, "readOnly": 1}

        async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
            if self.key == "foreign-eth-key":
                return {
                    "venue_id": "bybit",
                    "wallet": {"list": [{"totalEquity": "3000.00", "totalAvailableBalance": "3000.00", "coin": []}]},
                    "closed_pnl": [
                        {
                            "orderId": "eth-ord-999",
                            "symbol": "ETHUSDT",
                            "side": "Sell",
                            "qty": "1",
                            "closedSize": "1",
                            "avgEntryPrice": "3000",
                            "avgExitPrice": "3100",
                            "cumEntryValue": "3000",
                            "cumExitValue": "3100",
                            "openFee": "0",
                            "closeFee": "0",
                            "closedPnl": "100.00",
                            "createdTime": "1732200000000",
                            "updatedTime": "1732203600000",
                        }
                    ],
                    "deposits": [{"id": "dep-2900", "coin": "USDT", "amount": "2900.00", "successAt": "1732100000000"}],
                    "withdrawals": [],
                    "executions": [],
                }
            # Same BTC owner: returns overlapping `old_btc_trade` + new SOL trade (ancient DOGE is outside API window!)
            return {
                "venue_id": "bybit",
                "wallet": {"list": [{"totalEquity": "1350.00", "totalAvailableBalance": "1350.00", "coin": []}]},
                "closed_pnl": [
                    old_btc_trade,
                    {
                        "orderId": "sol-new-101",
                        "symbol": "SOLUSDT",
                        "side": "Sell",
                        "qty": "10",
                        "closedSize": "10",
                        "avgEntryPrice": "200",
                        "avgExitPrice": "210",
                        "cumEntryValue": "2000",
                        "cumExitValue": "2100",
                        "openFee": "0",
                        "closeFee": "0",
                        "closedPnl": "100.00",
                        "createdTime": "1732300000000",
                        "updatedTime": "1732303600000",
                    },
                ],
                "deposits": [{"id": "dep-1000", "coin": "USDT", "amount": "1000.00", "successAt": "1699000000000"}],
                "withdrawals": [],
                "executions": [],
            }

        async def aclose(self) -> None:
            pass

    reg_b.get("bybit").create_client = MockBybitUpgradeClient  # type: ignore[union-attr, method-assign]
    http_b = TestClient(create_app(data_dir=d_byb, registry=reg_b, auto_prompt_setup=False, enable_background_sync=False))

    # 1) Foreign ETH key has zero fingerprint overlap -> 409 ARCHIVE_OWNER_UNVERIFIED & exact partition rollback!
    bad_rebind = http_b.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "api_key": "foreign-eth-key", "api_secret": "s-eth"}},
    )
    assert bad_rebind.status_code == 409
    assert bad_rebind.json()["error_code"] == "ARCHIVE_OWNER_UNVERIFIED"
    assert store_b.snapshot_venue_partition("bybit") == snap_before
    assert CredentialVault(d_byb).list_decrypted() == []

    # 2) Original BTC owner key proves overlap via `old_btc_trade`, restores `ancient_doge_trade`, merges `sol-new-101`, and binds UID 1001!
    good_rebind = http_b.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "api_key": "owner-btc-key", "api_secret": "s-btc"}},
    )
    assert good_rebind.status_code == 200
    assert CredentialVault(d_byb).get_decrypted("bybit").remote_uid == "1001"  # type: ignore[union-attr]
    dash_b = http_b.get("/api/v1/dashboard").json()
    syms = {t["symbol"] for t in dash_b["trades"]}
    assert syms == {"DOGE_USDT", "BTC_USDT", "SOL_USDT"}
    assert dash_b["summary"]["total_equity_usd"] == 1350.00
    assert dash_b["reconciliation"]["residual_usd"] == 0.0


def test_sync_all_detects_preexisting_duplicate_uid_and_skips_double_counting_in_dashboard(tmp_path: Path) -> None:
    """If two partitions already hold keys for the same UID, sync_all flags the secondary one and dashboard skips it."""
    reg = build_default_registry()

    class SameUidBybitClient:
        def __init__(self, cred: ExchangeCredentialRecord) -> None:
            self.key = cred.api_key

        async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
            return {"list": [{"totalEquity": "2000.00", "totalAvailableBalance": "2000.00", "coin": []}]}

        async def get_api_key_info(self) -> dict[str, Any]:
            return {"userID": "880011", "isMaster": True, "readOnly": 1}

        async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
            return {
                "venue_id": "bybit",
                "wallet": {"list": [{"totalEquity": "2000.00", "totalAvailableBalance": "2000.00", "coin": []}]},
                "closed_pnl": [],
                "deposits": [{"id": "d1", "coin": "USDT", "amount": "2000.00", "successAt": "1732100000000"}],
                "withdrawals": [],
                "executions": [],
            }

        async def aclose(self) -> None:
            pass

    reg.get("bybit").create_client = SameUidBybitClient  # type: ignore[union-attr, method-assign]
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    vault.upsert(ExchangeCredentialRecord(venue_id="bybit", label="Bybit 主号", api_key="k1", api_secret="s1"))
    vault.upsert(ExchangeCredentialRecord(venue_id="bybit__dup01", adapter_id="bybit", label="误重复主号", api_key="k2", api_secret="s2"))

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    sm = SyncManager(store=store, vault=vault, registry=reg, ledger_service=svc)
    res = asyncio.run(sm.sync_all(force_all=True))

    assert res["venues"]["bybit"]["ok"] is True
    assert res["venues"]["bybit__dup01"]["ok"] is False
    assert res["venues"]["bybit__dup01"]["error_code"] == "DUPLICATE_ACCOUNT_UID"
    assert store.venue_scope("bybit__dup01").get_kv("duplicate_uid_conflict") is True

    dash = svc.get_dashboard(range_key="all")
    assert dash["summary"]["total_equity_usd"] == 2000.00
    assert [e["venue_id"] for e in dash["exchanges"]] == ["bybit"]


def test_warnings_pipeline_disabled_account_exclusion_crash_isolation_and_secondary_journal_preservation(tmp_path: Path) -> None:
    """Verify warnings contract, disabled account exclusion, single-venue crash isolation, and secondary note retention."""
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    reg = build_default_registry()

    # 1. VenueHealthTracker warnings persistence & resolve_state contract
    ht = VenueHealthTracker(store)
    h_ok = ht.record_success("okx", warnings=["Spot wallet 403 permission denied", "Spot wallet 403 permission denied"])
    assert h_ok["status"] == "connected"
    assert h_ok["warnings"] == ["Spot wallet 403 permission denied"]
    assert ht.resolve_state("okx", configured=True, enabled=True)["warnings"] == ["Spot wallet 403 permission denied"]
    assert ht.resolve_state("bybit", configured=False, enabled=True)["warnings"] == []
    assert ht.resolve_state("bybit", configured=True, enabled=False)["warnings"] == []

    # 2. Seed OKX (enabled, with permission warning), Hyperliquid (enabled, with 2,000 cap warning),
    #    Bybit (DISABLED -> must NOT be counted or marked archived), and a CrashingAdapter (must be isolated!)
    vault.upsert(ExchangeCredentialRecord(venue_id="okx", api_key="k1", api_secret="s1", passphrase="p1", enabled=True))
    vault.upsert(ExchangeCredentialRecord(venue_id="hyperliquid", api_key="0x" + "a" * 40, api_secret="", enabled=True))
    vault.upsert(ExchangeCredentialRecord(venue_id="bybit", api_key="k-dis", api_secret="s-dis", enabled=False))

    store.upsert_plugin_snapshot(
        "okx",
        {
            "balances": [{"totalEq": "1000.00", "details": []}],
            "deposits": [{"depId": "od1", "ccy": "USDT", "amt": "1000.00", "state": "2"}],
            "withdrawals": [],
            "positions_history": [],
            "fills": [],
            "warnings": ["Deposit history HTTP 403 missing wallet read permission"],
        },
    )
    store.upsert_plugin_snapshot(
        "hyperliquid",
        {
            "wallet_address": "0x" + "a" * 40,
            "clearinghouse": {"marginSummary": {"accountValue": "500.00", "withdrawable": "500.00"}, "assetPositions": []},
            "spot": {"balances": []},
            "fills": [],
            "funding_history": [],
            "ledger_updates": [{"hash": "0xh1", "time": 1732000000000, "delta": {"type": "deposit", "usdc": "500.00"}}],
            "warnings": ["userFills reached 2,000 fill cap; older fills may be truncated"],
        },
    )
    store.upsert_plugin_snapshot(
        "bybit",
        {
            "wallet": {"list": [{"totalEquity": "9999.00", "totalAvailableBalance": "9999.00", "coin": []}]},
            "deposits": [{"id": "bd1", "coin": "USDT", "amount": "9999.00"}],
        },
    )

    class CrashingAdapter(BaseExchangeAdapter):
        meta = AdapterMeta(venue_id="crashex", display_name="CrashEx", display_name_zh="损坏所", color_token="gate")

        def create_client(self, cred: Any) -> Any:
            return None

        async def test_connection(self, cred: Any) -> dict[str, Any]:
            return {"ok": True}

        def has_venue_data(self, s: LedgerStore) -> bool:
            return True

        def build_venue_ledger(self, s: LedgerStore, **kwargs: Any) -> VenueLedgerBundle | None:
            raise ValueError("corrupted JSON float NaN in snapshot")

    reg.register(CrashingAdapter())
    vault.upsert(ExchangeCredentialRecord(venue_id="crashex", api_key="ck", api_secret="cs", enabled=True))

    svc = LedgerService(store, vault, reg, default_demo_mode=False)
    demo_dash = svc.get_dashboard(force_demo=True, venue_filter="okx", symbol_filter="btc_usdt")
    live_dash = svc.get_dashboard(force_demo=False, venue_filter="ALL", symbol_filter="ALL")

    assert set(demo_dash.keys()) == set(live_dash.keys())
    assert demo_dash["venue_filter"] == "okx"
    assert demo_dash["symbol_filter"] == "BTC_USDT"
    assert demo_dash["warnings"] == []

    # Disabled Bybit (9999 U) excluded & CrashingAdapter isolated -> total equity is strictly 1000 + 500 = 1500.00 U!
    assert live_dash["venue_filter"] == "ALL"
    assert live_dash["symbol_filter"] == "ALL"
    assert live_dash["summary"]["total_equity_usd"] == 1500.00
    assert {e["venue_id"] for e in live_dash["exchanges"]} == {"okx", "hyperliquid"}
    ex_by_vid = {e["venue_id"]: e for e in live_dash["exchanges"]}
    assert len(ex_by_vid["okx"]["warnings"]) == 2
    assert len(ex_by_vid["hyperliquid"]["warnings"]) == 1

    warn_by_vid = {w["venue_id"]: w for w in live_dash["warnings"]}
    assert {"okx", "hyperliquid", "crashex"}.issubset(warn_by_vid.keys())
    assert "部分子账户或充提流水未读取" in warn_by_vid["okx"]["summary_zh"]
    assert "分页上限" in warn_by_vid["hyperliquid"]["summary_zh"]
    assert "隔离" in warn_by_vid["crashex"]["summary_zh"]

    # 3. AccountScopedStore partition helpers & secondary account fallback note preservation
    scoped = store.for_account("okx__sub01", "okx")
    scoped.venue_scope("okx").set_kv("custom_k", "v1")
    snap_sub = scoped.snapshot_venue_partition("okx")
    scoped.purge_venue_data("okx")
    assert scoped.venue_scope("okx").get_kv("custom_k") is None
    scoped.restore_venue_partition("okx", snap_sub)
    assert scoped.venue_scope("okx").get_kv("custom_k") == "v1"

    dummy_bundle = VenueLedgerBundle(
        venue_id="okx",
        exchange_card={"venue_id": "okx", "display_name": "OKX", "equity_usd": 100.0, "net_principal_usd": 100.0, "net_pnl_usd": 0.0},
        trades=[
            {
                "trade_id": "okx_pos1_1732000000000",
                "symbol": "BTC_USDT",
                "tags": ["adapter_fallback_tag"],
                "note": "适配器已通过base_rk恢复的副账号笔记",
            }
        ],
        open_positions=[],
        spot_holdings=[],
        reconcile_streams=[],
    )
    stamp_bundle_for_account(
        dummy_bundle,
        account_id="okx__sub01",
        meta=reg.get("okx").meta,  # type: ignore[union-attr]
        cred=None,
        journals={},
    )
    assert dummy_bundle.trades[0]["trade_id"] == "okx__sub01__okx_pos1_1732000000000"
    assert dummy_bundle.trades[0]["tags"] == ["adapter_fallback_tag"]
    assert dummy_bundle.trades[0]["note"] == "适配器已通过base_rk恢复的副账号笔记"


def test_settings_secret_clear_forged_cookie_rejection_proxy_rate_limit_readonly_prefix_and_one_way_copy_watcher(tmp_path: Path) -> None:
    """Verify settings secret clearing, forged cookie rejection, reverse-proxy rate limit & Secure cookie, readonly prefix boundary, and BOTH copy positions."""
    # 1. Forged 4-segment session token must be rejected even before password setup (`enabled=False`)
    auth_mgr = LocalAuthManager(tmp_path / "auth_uninit")
    forged_cookie = f"v1.{int(time.time()) + 3600}.deadbeef.{'0' * 64}"
    assert auth_mgr.verify_session_token(forged_cookie) is False

    app = create_app(data_dir=tmp_path / "app_sec", demo_mode=False, enable_background_sync=False)
    uninit_client = TestClient(app)
    uninit_client.cookies.set(AUTH_COOKIE_NAME, forged_cookie)
    h_resp = uninit_client.get("/api/v1/health").json()
    assert "adaptive_polling" not in h_resp
    assert "has_live_data" not in h_resp

    # Setup password over simulated HTTPS reverse proxy -> Cookie must carry `Secure`
    setup_https = uninit_client.post(
        "/api/v1/auth/setup",
        json={"password": "secure-pass-123", "remember": True},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert setup_https.status_code == 200
    assert "secure" in setup_https.headers.get("set-cookie", "").lower()

    # 2. Reverse-proxy X-Forwarded-For rate-limit isolation (scanner IP locked -> normal user IP still logs in!)
    proxy_app = create_app(
        data_dir=tmp_path / "app_sec",
        demo_mode=False,
        enable_background_sync=False,
    )
    scanner = TestClient(proxy_app, client=("127.0.0.1", 50001))
    legit_user = TestClient(proxy_app, base_url="https://testserver", client=("127.0.0.1", 50002))
    for i in range(4):
        assert scanner.post(
            "/api/v1/auth/login",
            json={"password": f"wrong-{i}"},
            headers={"X-Forwarded-For": "203.0.113.99"},
        ).status_code == 401
    assert scanner.post(
        "/api/v1/auth/login",
        json={"password": "wrong-5"},
        headers={"X-Forwarded-For": "203.0.113.99"},
    ).status_code == 429

    # Legit user behind the same 127.0.0.1 reverse proxy with a different X-Forwarded-For succeeds!
    legit_login = legit_user.post(
        "/api/v1/auth/login",
        json={"password": "secure-pass-123", "remember": True},
        headers={"X-Forwarded-For": "198.51.100.77", "X-Forwarded-Proto": "https"},
    )
    assert legit_login.status_code == 200
    assert "secure" in legit_login.headers.get("set-cookie", "").lower()

    # 3. Settings secret clearing (`clear_ai_api_key` / `__CLEAR__` & `clear_bark_push_key` / `__CLEAR__`)
    s_set = legit_user.put(
        "/api/v1/settings",
        json={
            "ai_api_key": "sk-live-secret-to-clear",
            "notifications": {"bark_server_url": "https://api.day.app", "bark_push_key": "BarkSecretKey999"},
        },
    ).json()
    assert s_set["ai_api_key_set"] is True
    assert s_set["notifications"]["bark_push_key_set"] is True

    s_cleared_1 = legit_user.put(
        "/api/v1/settings",
        json={"clear_ai_api_key": True, "notifications": {"clear_bark_push_key": True}},
    ).json()
    assert s_cleared_1["ai_api_key_set"] is False
    assert s_cleared_1["notifications"]["bark_push_key_set"] is False

    # Re-set and test `__CLEAR__` sentinel
    legit_user.put(
        "/api/v1/settings",
        json={"ai_api_key": "sk-again", "notifications": {"bark_push_key": "BarkAgain"}},
    )
    s_cleared_2 = legit_user.put(
        "/api/v1/settings",
        json={"ai_api_key": "__CLEAR__", "notifications": {"bark_push_key": "__CLEAR__"}},
    ).json()
    assert s_cleared_2["ai_api_key_set"] is False
    assert s_cleared_2["notifications"]["bark_push_key_set"] is False

    # 4. Read-only guard boundary-safe path prefix check
    assert_readonly_request("GET", "/api/v1/accounts", ("/api/v1/accounts",))
    assert_readonly_request("GET", "/api/v1/accounts/4/balance", ("/api/v1/accounts",))
    with pytest.raises(ReadOnlyViolationError):
        assert_readonly_request("GET", "/api/v1/accounts_evil", ("/api/v1/accounts",))
    with pytest.raises(ReadOnlyViolationError):
        assert_readonly_request("GET", "/api/v1/accounts-transfer", ("/api/v1/accounts",))

    # 5. CopyWatcher one-way mode (`positionSide == "BOTH"`) net position inference (buy +, sell -, flip < 0 -> SHORT)
    now_ms = 1732400000000

    def cw_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/position-history"):
            return httpx.Response(
                200,
                json={
                    "code": "000000",
                    "data": {
                        "list": [
                            {"symbol": "BTCUSDT", "side": "BOTH", "status": "All Closed", "closed": now_ms - 60000},
                            {"symbol": "ETHUSDT", "side": "BOTH", "status": "All Closed", "closed": now_ms - 60000},
                        ]
                    },
                },
            )
        if p.endswith("/order-history"):
            return httpx.Response(
                200,
                json={
                    "code": "000000",
                    "data": {
                        "list": [
                            # BTCUSDT: BUY 1.0 @ 90000 + BUY 1.0 @ 92000 - SELL 0.5 @ 93000 => net +1.5 LONG @ avg 91000
                            {"orderId": "1", "orderTime": now_ms - 50000, "symbol": "BTCUSDT", "side": "BUY", "positionSide": "BOTH", "type": "LIMIT", "executedQty": "1.0", "avgPrice": "90000", "totalPnl": "0"},
                            {"orderId": "2", "orderTime": now_ms - 40000, "symbol": "BTCUSDT", "side": "BUY", "positionSide": "BOTH", "type": "LIMIT", "executedQty": "1.0", "avgPrice": "92000", "totalPnl": "0"},
                            {"orderId": "3", "orderTime": now_ms - 30000, "symbol": "BTCUSDT", "side": "SELL", "positionSide": "BOTH", "type": "MARKET", "executedQty": "0.5", "avgPrice": "93000", "totalPnl": "1000"},
                            # ETHUSDT: BUY 2.0 @ 3000 then SELL 5.0 @ 3100 => flips across zero to net -3.0 SHORT @ 3100
                            {"orderId": "4", "orderTime": now_ms - 20000, "symbol": "ETHUSDT", "side": "BUY", "positionSide": "BOTH", "type": "LIMIT", "executedQty": "2.0", "avgPrice": "3000", "totalPnl": "0"},
                            {"orderId": "5", "orderTime": now_ms - 10000, "symbol": "ETHUSDT", "side": "SELL", "positionSide": "BOTH", "type": "MARKET", "executedQty": "5.0", "avgPrice": "3100", "totalPnl": "200"},
                        ]
                    },
                },
            )
        if p.endswith("/fapi/v1/ticker/price"):
            sym = req.url.params.get("symbol")
            return httpx.Response(200, json={"price": "94000" if sym == "BTCUSDT" else "3000"})
        return httpx.Response(404)

    async def _run_cw() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(cw_handler)) as hc:
            cw = PublicCopyWatcherClient(client=hc)
            act = await cw.fetch_trade_activity("3988776655443322110", now_ms=now_ms)
            by_sym = {p.symbol: p for p in act.inferred_positions}
            assert set(by_sym.keys()) == {"BTCUSDT", "ETHUSDT"}

            btc_p = by_sym["BTCUSDT"]
            assert btc_p.position_side == "LONG"
            assert btc_p.side_zh == "多"
            assert btc_p.qty == 1.5
            assert btc_p.avg_entry_price == 91000.0
            assert btc_p.unrealized_pnl_usd == 4500.0

            eth_p = by_sym["ETHUSDT"]
            assert eth_p.position_side == "SHORT"
            assert eth_p.side_zh == "空"
            assert eth_p.qty == 3.0
            assert eth_p.avg_entry_price == 3100.0
            assert eth_p.unrealized_pnl_usd == 300.0
            assert act.uncertain_symbols == ()

    asyncio.run(_run_cw())


def test_first_bind_on_empty_ledger_requires_non_empty_uid_with_409_and_binance_uid_probe(tmp_path: Path) -> None:
    """First-time bind on an empty ledger must reject HTTP 200 missing-UID responses with 409 (never saving UID-less keys)."""
    reg = build_default_registry()
    uid_mode = {"return_uid": False}

    class MissingUidBybitClient:
        def __init__(self, cred: ExchangeCredentialRecord) -> None:
            self.key = cred.api_key

        async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
            return {"list": [{"totalEquity": "1100.00", "totalAvailableBalance": "1100.00", "coin": []}]}

        async def get_api_key_info(self) -> dict[str, Any]:
            if not uid_mode["return_uid"]:
                return {"isMaster": True, "readOnly": 1}  # HTTP 200, but missing userID!
            return {"userID": "770011", "isMaster": True, "readOnly": 1}

        async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
            return {
                "venue_id": "bybit",
                "wallet": {"list": [{"totalEquity": "1100.00", "totalAvailableBalance": "1100.00", "coin": []}]},
                "closed_pnl": [],
                "deposits": [{"id": "d-1100", "coin": "USDT", "amount": "1100.00", "successAt": "1732100000000"}],
                "withdrawals": [],
                "executions": [],
            }

        async def aclose(self) -> None:
            pass

    reg.get("bybit").create_client = MissingUidBybitClient  # type: ignore[union-attr, method-assign]
    http = TestClient(create_app(data_dir=tmp_path / "byb_first", registry=reg, auto_prompt_setup=False, enable_background_sync=False))

    # 1. HTTP 200 without userID on brand-new empty ledger -> 409 ACCOUNT_UID_UNAVAILABLE, committed=False
    v_res = http.post("/api/v1/exchanges/verify", json={"venue_id": "bybit", "api_key": "k1", "api_secret": "s1"})
    assert v_res.status_code == 409
    assert v_res.json()["error_code"] == "ACCOUNT_UID_UNAVAILABLE"
    assert v_res.json()["committed"] is False

    s_res = http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "bybit", "api_key": "k1", "api_secret": "s1"}})
    assert s_res.status_code == 409
    assert s_res.json()["error_code"] == "ACCOUNT_UID_UNAVAILABLE"
    assert s_res.json()["committed"] is False
    assert CredentialVault(tmp_path / "byb_first").list_decrypted() == []
    assert LedgerStore(tmp_path / "byb_first").snapshot_venue_partition("bybit") == {"kv": [], "records": [], "snapshots": []}

    # Once valid userID is returned, first bind succeeds and blocks second key of same UID
    uid_mode["return_uid"] = True
    s_ok = http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "bybit", "api_key": "k1", "api_secret": "s1"}})
    assert s_ok.status_code == 200
    s_dup = http.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "bybit", "create_new": True, "api_key": "k2", "api_secret": "s2"}})
    assert s_dup.status_code == 409
    assert s_dup.json()["error_code"] == "DUPLICATE_ACCOUNT_UID"
    assert http.get("/api/v1/dashboard").json()["summary"]["total_equity_usd"] == 1100.00

    # 2. BinanceAdapter.test_connection extracts /api/v3/account uid and rejects uid=0 / propagates 503
    bn_ad = reg.get("binance")
    assert bn_ad is not None

    class MockBinanceConnClient:
        def __init__(self, cred: ExchangeCredentialRecord) -> None:
            self.key = cred.api_key

        async def raw_get(self, path: str, query_params: Any = ()) -> Any:
            if path == "/api/v3/account":
                if self.key == "bn-503":
                    raise RuntimeError("Binance GET /api/v3/account failed (503): Service Unavailable")
                if self.key == "bn-zero":
                    return {"accountType": "SPOT", "uid": 0}
                return {"accountType": "SPOT", "uid": 358899001}
            return {}

        async def aclose(self) -> None:
            pass

    bn_ad.create_client = MockBinanceConnClient  # type: ignore[method-assign]
    http_bn = TestClient(create_app(data_dir=tmp_path / "bn_first", registry=reg, auto_prompt_setup=False, enable_background_sync=False))

    bn_503 = http_bn.post("/api/v1/exchanges/verify", json={"venue_id": "binance", "api_key": "bn-503", "api_secret": "s"})
    assert bn_503.status_code == 502

    bn_zero = http_bn.post("/api/v1/exchanges/verify", json={"venue_id": "binance", "api_key": "bn-zero", "api_secret": "s"})
    assert bn_zero.status_code == 409
    assert bn_zero.json()["error_code"] == "ACCOUNT_UID_UNAVAILABLE"

    bn_valid = http_bn.post("/api/v1/exchanges/verify", json={"venue_id": "binance", "api_key": "bn-ok", "api_secret": "s"})
    assert bn_valid.status_code == 200
    assert bn_valid.json()["masked_uid"] == "358***9001"


def test_legacy_137_uidless_active_and_archived_partitions_never_double_count_to_2200_and_self_heal(tmp_path: Path) -> None:
    """1.3.7 UID-less active/archived partitions must block +Add Account duplication (1,100 -> 2,200 U) and self-heal pre-existing duplicates."""
    btc_trade = {
        "orderId": "btc-137-001",
        "symbol": "BTCUSDT",
        "side": "Sell",
        "qty": "0.1",
        "closedSize": "0.1",
        "avgEntryPrice": "80000",
        "avgExitPrice": "81000",
        "cumEntryValue": "8000",
        "cumExitValue": "8100",
        "openFee": "0",
        "closeFee": "0",
        "closedPnl": "100.00",
        "createdTime": "1732200000000",
        "updatedTime": "1732203600000",
    }
    snap_1100 = {
        "venue_id": "bybit",
        "wallet": {"list": [{"totalEquity": "1100.00", "totalAvailableBalance": "1100.00", "coin": []}]},
        "closed_pnl": [btc_trade],
        "deposits": [{"id": "dep-1000", "coin": "USDT", "amount": "1000.00", "successAt": "1732100000000"}],
        "withdrawals": [],
        "executions": [],
    }

    old_key_behavior = {"mode": "503"}  # "503" | "revoked_10003" | "ok"

    def _make_reg():
        reg = build_default_registry()

        class LegacyBybitClient:
            def __init__(self, cred: ExchangeCredentialRecord) -> None:
                self.key = cred.api_key

            async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
                if self.key == "old-137-key" and old_key_behavior["mode"] == "revoked_10003":
                    raise RuntimeError("Bybit retCode=10003: Invalid api_key")
                return snap_1100["wallet"]

            async def get_api_key_info(self) -> dict[str, Any]:
                if self.key == "old-137-key":
                    if old_key_behavior["mode"] == "503":
                        raise RuntimeError("Bybit GET /v5/user/query-api HTTP 503: Service Unavailable")
                    if old_key_behavior["mode"] == "revoked_10003":
                        raise RuntimeError("Bybit GET /v5/user/query-api retCode=10003: Invalid api_key")
                return {"userID": "770011", "isMaster": True, "readOnly": 1}

            async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
                return dict(snap_1100)

            async def aclose(self) -> None:
                pass

        reg.get("bybit").create_client = LegacyBybitClient  # type: ignore[union-attr, method-assign]
        return reg

    # Scenario A: Active 1.3.7 UID-less partition ("bybit" has 1,100 U, remote_uid="")
    d_active = tmp_path / "scene_a_active"
    store_a = LedgerStore(d_active)
    vault_a = CredentialVault(d_active)
    vault_a.upsert(ExchangeCredentialRecord(venue_id="bybit", label="Bybit 1.3.7", api_key="old-137-key", api_secret="s-old"))
    store_a.upsert_plugin_snapshot("bybit", snap_1100)
    http_a = TestClient(create_app(data_dir=d_active, registry=_make_reg(), auto_prompt_setup=False, enable_background_sync=False))

    # A1: Old key returns 503 during identity probe -> +Add Account fails with 502!
    old_key_behavior["mode"] = "503"
    assert http_a.post("/api/v1/exchanges/verify", json={"venue_id": "bybit", "create_new": True, "api_key": "new-valid-key", "api_secret": "s-new"}).status_code == 502
    assert http_a.post("/api/v1/exchanges", json={"venue_id": "bybit", "create_new": True, "api_key": "new-valid-key", "api_secret": "s-new"}).status_code == 502
    assert http_a.post("/api/v1/sync", json={"candidate_credential": {"venue_id": "bybit", "create_new": True, "api_key": "new-valid-key", "api_secret": "s-new"}}).status_code == 502

    # A2: Old key is revoked (retCode=10003), user clicks +Add Account with new key of SAME account -> 409 DUPLICATE_ACCOUNT_UID!
    old_key_behavior["mode"] = "revoked_10003"
    dup_sync_a = http_a.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "create_new": True, "label": "误点添加", "api_key": "new-valid-key", "api_secret": "s-new"}},
    )
    assert dup_sync_a.status_code == 409
    assert dup_sync_a.json()["error_code"] == "DUPLICATE_ACCOUNT_UID"

    dup_ex_a = http_a.post(
        "/api/v1/exchanges",
        json={"venue_id": "bybit", "create_new": True, "label": "误点添加", "api_key": "new-valid-key", "api_secret": "s-new"},
    )
    assert dup_ex_a.status_code == 409
    assert dup_ex_a.json()["error_code"] == "DUPLICATE_ACCOUNT_UID"
    assert store_a.list_partition_account_ids("bybit") == ["bybit"]
    assert http_a.get("/api/v1/dashboard").json()["summary"]["total_equity_usd"] == 1100.00

    # Rotating key on the original "bybit" row succeeds cleanly and keeps equity at 1,100.00 U!
    rot_ok = http_a.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "account_id": "bybit", "label": "Bybit 1.3.7", "api_key": "new-valid-key", "api_secret": "s-new"}},
    )
    assert rot_ok.status_code == 200
    assert http_a.get("/api/v1/dashboard").json()["summary"]["total_equity_usd"] == 1100.00

    # Scenario B: 1.3.7 UID-less partition archived FIRST (keep_data=true), then user clicks +Add Account -> 409 ARCHIVED_ACCOUNT_UID_EXISTS!
    d_arch = tmp_path / "scene_b_archived"
    store_b = LedgerStore(d_arch)
    vault_b = CredentialVault(d_arch)
    vault_b.upsert(ExchangeCredentialRecord(venue_id="bybit", label="Bybit旧封存", api_key="old-137-key", api_secret="s-old"))
    store_b.upsert_plugin_snapshot("bybit", snap_1100)
    http_b = TestClient(create_app(data_dir=d_arch, registry=_make_reg(), auto_prompt_setup=False, enable_background_sync=False))

    http_b.delete("/api/v1/exchanges/bybit?keep_data=true").raise_for_status()
    dup_arch_sync = http_b.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "create_new": True, "label": "误重复添加", "api_key": "new-valid-key", "api_secret": "s-new"}},
    )
    assert dup_arch_sync.status_code == 409
    assert dup_arch_sync.json()["error_code"] == "ARCHIVED_ACCOUNT_UID_EXISTS"

    dup_arch_ex = http_b.post(
        "/api/v1/exchanges",
        json={"venue_id": "bybit", "create_new": True, "label": "误重复添加", "api_key": "new-valid-key", "api_secret": "s-new"},
    )
    assert dup_arch_ex.status_code == 409
    assert dup_arch_ex.json()["error_code"] == "ARCHIVED_ACCOUNT_UID_EXISTS"
    assert store_b.list_partition_account_ids("bybit") == ["bybit"]
    assert http_b.get("/api/v1/dashboard").json()["summary"]["total_equity_usd"] == 1100.00

    # Rebinding on the archived "bybit" row itself succeeds and keeps equity at 1,100.00 U!
    rebind_ok = http_b.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "account_id": "bybit", "label": "Bybit旧封存", "api_key": "new-valid-key", "api_secret": "s-new", "expected_revision": "absent"}},
    )
    assert rebind_ok.status_code == 200
    assert http_b.get("/api/v1/dashboard").json()["summary"]["total_equity_usd"] == 1100.00

    # Scenario C: Self-healing of pre-existing doubled ledger (UID-less archived "bybit" + active "bybit__29170bc9")
    d_heal = tmp_path / "scene_c_self_heal"
    store_c = LedgerStore(d_heal)
    vault_c = CredentialVault(d_heal)
    store_c.upsert_plugin_snapshot("bybit", snap_1100)  # UID-less archived legacy partition
    store_c.upsert_plugin_snapshot("bybit__29170bc9", snap_1100)  # Active secondary partition of same account
    store_c.venue_scope("bybit__29170bc9").set_kv("account_uid", "770011")
    vault_c.upsert(
        ExchangeCredentialRecord(
            venue_id="bybit__29170bc9",
            adapter_id="bybit",
            label="Bybit 新接入",
            api_key="new-valid-key",
            api_secret="s-new",
            remote_uid="770011",
        )
    )
    http_c = TestClient(create_app(data_dir=d_heal, registry=_make_reg(), auto_prompt_setup=False, enable_background_sync=False))
    dash_c = http_c.get("/api/v1/dashboard").json()
    assert dash_c["summary"]["total_equity_usd"] == 1100.00
    assert dash_c["summary"]["net_principal_usd"] == 1000.00
    assert len(dash_c["trades"]) == 1
    assert [e["venue_id"] for e in dash_c["exchanges"]] == ["bybit__29170bc9"]
    assert dash_c["reconciliation"]["residual_usd"] == 0.0

    # Syncing & restarting still stays converged at 1,100.00 U!
    http_c.post("/api/v1/sync", json={"force_all": True}).raise_for_status()
    http_c_restart = TestClient(create_app(data_dir=d_heal, registry=_make_reg(), auto_prompt_setup=False, enable_background_sync=False))
    assert http_c_restart.get("/api/v1/dashboard").json()["summary"]["total_equity_usd"] == 1100.00


def test_legacy_random_secondary_partition_purge_and_reconnect_recovers_trade_journals_and_archive_preserves_timestamps(tmp_path: Path) -> None:
    """Purging old random-suffix secondary partitions (with or without UID) and reconnecting same UID restores trade notes; keep_data=true never mutates unchanged KV updated_at."""
    eth_trade = {
        "orderId": "ord-eth-77",
        "symbol": "ETHUSDT",
        "side": "Sell",
        "qty": "1",
        "closedSize": "1",
        "avgEntryPrice": "3000",
        "avgExitPrice": "3200",
        "cumEntryValue": "3000",
        "cumExitValue": "3200",
        "openFee": "0",
        "closeFee": "0",
        "closedPnl": "200.00",
        "createdTime": "1732200000000",
        "updatedTime": "1732203600000",
    }
    sub_snap = {
        "venue_id": "bybit",
        "wallet": {"list": [{"totalEquity": "2200.00", "totalAvailableBalance": "2200.00", "coin": []}]},
        "closed_pnl": [eth_trade],
        "deposits": [{"id": "d-2000", "coin": "USDT", "amount": "2000.00", "successAt": "1732100000000"}],
        "withdrawals": [],
        "executions": [],
    }
    main_snap = {
        "venue_id": "bybit",
        "wallet": {"list": [{"totalEquity": "1000.00", "totalAvailableBalance": "1000.00", "coin": []}]},
        "closed_pnl": [],
        "deposits": [{"id": "d-1000", "coin": "USDT", "amount": "1000.00", "successAt": "1732100000000"}],
        "withdrawals": [],
        "executions": [],
    }

    reg = build_default_registry()

    class JournalBybitClient:
        def __init__(self, cred: ExchangeCredentialRecord) -> None:
            self.key = cred.api_key

        async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
            return main_snap["wallet"] if self.key == "k-main" else sub_snap["wallet"]

        async def get_api_key_info(self) -> dict[str, Any]:
            if self.key == "k-main":
                return {"userID": "770011", "isMaster": True, "readOnly": 1}
            return {"userID": "770022", "isMaster": False, "readOnly": 1}

        async def fetch_full_snapshot(self, *, since_ms: int = 0) -> dict[str, Any]:
            return dict(main_snap if self.key == "k-main" else sub_snap)

        async def aclose(self) -> None:
            pass

    reg.get("bybit").create_client = JournalBybitClient  # type: ignore[union-attr, method-assign]

    # Case 1: Legacy random suffix `bybit__2881cd1a` WITH known UID -> delete(keep_data=false) migrates prefix immediately
    d1 = tmp_path / "journal_with_uid"
    store1 = LedgerStore(d1)
    vault1 = CredentialVault(d1)
    vault1.upsert(ExchangeCredentialRecord(venue_id="bybit", label="主号", api_key="k-main", api_secret="s1", remote_uid="770011"))
    vault1.upsert(
        ExchangeCredentialRecord(
            venue_id="bybit__2881cd1a",
            adapter_id="bybit",
            label="旧随机子号",
            api_key="k-sub-old",
            api_secret="s2",
            remote_uid="770022",
            account_role="subaccount",
        )
    )
    store1.upsert_plugin_snapshot("bybit", main_snap)
    store1.upsert_plugin_snapshot("bybit__2881cd1a", sub_snap)
    sc_rand = store1.venue_scope("bybit__2881cd1a")
    sc_rand.set_kv("account_label", "旧随机子号")
    sc_rand.set_kv("account_uid", "770022")
    sc_rand.set_kv("account_role", "subaccount")
    # Force an older updated_at timestamp to verify keep_data=true does NOT touch unchanged KV rows!
    with store1._connect() as conn:
        conn.execute("UPDATE otl_venue_kv SET updated_at = 1700000000 WHERE venue_id = 'bybit__2881cd1a'")
    snap_ts_before = store1.snapshot_venue_partition("bybit__2881cd1a")

    http1 = TestClient(create_app(data_dir=d1, registry=reg, auto_prompt_setup=False, enable_background_sync=False))
    old_tid = http1.get("/api/v1/dashboard?venue=bybit__2881cd1a").json()["trades"][0]["trade_id"]
    assert old_tid == "bybit__2881cd1a__bybit_ord-eth-77_1732203600000"
    http1.put(f"/api/v1/journal/{old_tid}", json={"tags": ["旧版复盘"], "note": "随机分区历史笔记"}).raise_for_status()

    # First archive with keep_data=true -> KV updated_at timestamps MUST remain 1700000000 (zero cross-second drift!)
    http1.delete("/api/v1/exchanges/bybit__2881cd1a?keep_data=true").raise_for_status()
    assert store1.snapshot_venue_partition("bybit__2881cd1a") == snap_ts_before

    # Now purge completely (keep_data=false) and reconnect same UID via create_new=True -> deterministic ID `bybit__29170bc9`
    http1.delete("/api/v1/exchanges/bybit__2881cd1a?keep_data=false").raise_for_status()
    re_res = http1.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "create_new": True, "label": "重接子号", "api_key": "k-sub-new", "api_secret": "s3"}},
    )
    assert re_res.status_code == 200
    det_acct_id = re_res.json()["account_id"]
    assert det_acct_id != "bybit__2881cd1a"
    rec_tr = http1.get(f"/api/v1/dashboard?venue={det_acct_id}").json()["trades"][0]
    assert rec_tr["note"] == "随机分区历史笔记"
    assert rec_tr["tags"] == ["旧版复盘"]

    # Case 2: Pre-existing orphan journal from a UID-less or already-deleted random partition `bybit__deadbeef`
    # while another active secondary partition `bybit__keep9999` stays untouched!
    d2 = tmp_path / "journal_orphan_claim"
    store2 = LedgerStore(d2)
    vault2 = CredentialVault(d2)
    vault2.upsert(ExchangeCredentialRecord(venue_id="bybit", label="主号", api_key="k-main", api_secret="s1", remote_uid="770011"))
    vault2.upsert(
        ExchangeCredentialRecord(
            venue_id="bybit__keep9999",
            adapter_id="bybit",
            label="在用保留子号",
            api_key="k-keep",
            api_secret="sk",
            remote_uid="770099",
             enabled=False,
        )
    )
    store2.upsert_plugin_snapshot("bybit", main_snap)
    store2.save_journal("bybit__deadbeef__bybit_ord-eth-77_1732203600000", ["孤儿恢复"], "无UID随机分区历史笔记")
    store2.save_journal("bybit__keep9999__bybit_ord-eth-77_1732203600000", ["勿动"], "另一个禁用子号的笔记")

    http2 = TestClient(create_app(data_dir=d2, registry=reg, auto_prompt_setup=False, enable_background_sync=False))
    re_res2 = http2.post(
        "/api/v1/sync",
        json={"candidate_credential": {"venue_id": "bybit", "create_new": True, "label": "重接子号2", "api_key": "k-sub-new", "api_secret": "s3"}},
    )
    assert re_res2.status_code == 200
    det_id2 = re_res2.json()["account_id"]
    rec_tr2 = http2.get(f"/api/v1/dashboard?venue={det_id2}").json()["trades"][0]
    assert rec_tr2["note"] == "无UID随机分区历史笔记"
    assert rec_tr2["tags"] == ["孤儿恢复"]
    # Disabled sibling `bybit__keep9999`'s journal MUST NOT be stolen!
    all_j2 = store2.load_journals()
    assert all_j2["bybit__keep9999__bybit_ord-eth-77_1732203600000"]["note"] == "另一个禁用子号的笔记"


def test_hyperliquid_batch_subaccount_import_skips_already_connected_wallets_and_copy_watcher_11012005_retry(tmp_path: Path) -> None:
    """Hyperliquid include_subaccounts skips already-connected sub-wallets; CopyWatcher retries 11012005 once and humanizes clearly."""
    from opentradeledger.adapters.hyperliquid.client import HyperliquidReadOnlyClient
    from opentradeledger.core.copy_watcher import CopyWatcherError, CopyWatcherService, humanize_watcher_error
    import json as _json

    master_addr = "0x" + "1" * 40
    sub1_addr = "0x" + "2" * 40
    sub2_addr = "0x" + "3" * 40

    def hl_handler(req: httpx.Request) -> httpx.Response:
        body = _json.loads(req.content.decode("utf-8"))
        tp = body.get("type")
        usr = str(body.get("user") or "").lower()
        if tp == "clearinghouseState":
            eq = "1000.0" if usr == master_addr else ("400.0" if usr == sub1_addr else "600.0")
            return httpx.Response(200, json={"marginSummary": {"accountValue": eq, "withdrawable": eq}, "assetPositions": []})
        if tp == "spotClearinghouseState":
            return httpx.Response(200, json={"balances": []})
        if tp == "allMids":
            return httpx.Response(200, json={})
        if tp == "userNonFundingLedgerUpdates":
            dep = "1000.0" if usr == master_addr else ("400.0" if usr == sub1_addr else "600.0")
            return httpx.Response(200, json=[{"hash": f"0x{usr[:8]}", "time": 1732100000000, "delta": {"type": "deposit", "usdc": dep}}])
        return httpx.Response(200, json=[])

    reg = build_default_registry()
    reg.get("hyperliquid").create_client = lambda cred: HyperliquidReadOnlyClient(  # type: ignore[union-attr, method-assign]
        wallet_address=cred.api_key,
        client=httpx.AsyncClient(transport=httpx.MockTransport(hl_handler)),
    )
    http = TestClient(create_app(data_dir=tmp_path / "hl_subs", registry=reg, auto_prompt_setup=False, enable_background_sync=False))

    # Step 1: Connect master + Sub1
    r1 = http.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "hyperliquid",
                "label": "HL主钱包",
                "wallet_address": master_addr,
                "include_subaccounts": [{"name": "Sub1", "address": sub1_addr}],
            }
        },
    ).json()
    assert len(r1["added_subaccounts"]) == 1
    assert "failed_subaccounts" not in r1

    # Step 2: Later add Sub2 while keeping Sub1 checked -> Sub1 skipped cleanly (NO failed_subaccounts!), only Sub2 added!
    r2 = http.post(
        "/api/v1/sync",
        json={
            "candidate_credential": {
                "venue_id": "hyperliquid",
                "account_id": "hyperliquid",
                "label": "HL主钱包",
                "wallet_address": master_addr,
                "include_subaccounts": [
                    {"name": "Sub1", "address": sub1_addr},
                    {"name": "Sub2", "address": sub2_addr},
                ],
            }
        },
    ).json()
    assert len(r2["added_subaccounts"]) == 1
    assert "failed_subaccounts" not in r2
    assert http.get("/api/v1/dashboard").json()["summary"]["total_equity_usd"] == 2000.00

    # CopyWatcher 11012005 retry & humanized message
    pid = "3900000000000000999"
    now_ms = int(time.time() * 1000)
    state_cw = {"order_hist_calls": 0, "always_busy": False}

    def cw_busy_handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/detail"):
            return httpx.Response(
                200,
                json={
                    "code": "000000",
                    "data": {
                        "leadPortfolioId": pid,
                        "nickname": "BusyTrader",
                        "status": "ACTIVE",
                        "portfolioType": "PUBLIC",
                        "currentCopyCount": 100,
                        "maxCopyCount": 200,
                    },
                },
            )
        if p.endswith("/position-history"):
            return httpx.Response(200, json={"code": "000000", "data": {"list": []}})
        if p.endswith("/order-history"):
            state_cw["order_hist_calls"] += 1
            if state_cw["always_busy"] or state_cw["order_hist_calls"] == 1:
                return httpx.Response(200, json={"code": "11012005", "message": "The system is currently busy, please try again later."})
            return httpx.Response(
                200,
                json={
                    "code": "000000",
                    "data": {
                        "list": [
                            {
                                "orderId": "o1",
                                "orderTime": now_ms - 5000,
                                "symbol": "BTCUSDT",
                                "side": "BUY",
                                "positionSide": "LONG",
                                "type": "MARKET",
                                "executedQty": "0.1",
                                "avgPrice": "90000",
                                "totalPnl": "0",
                            }
                        ]
                    },
                },
            )
        if p.endswith("/fapi/v1/ticker/price"):
            return httpx.Response(200, json={"price": "91000"})
        return httpx.Response(404)

    async def _run_busy_cw() -> None:
        store_cw = LedgerStore(tmp_path / "cw_busy")
        async with httpx.AsyncClient(transport=httpx.MockTransport(cw_busy_handler)) as hc:
            # 1) Transient single 11012005 recovers on automatic retry #2!
            cw_client = PublicCopyWatcherClient(client=hc)
            act = await cw_client.fetch_trade_activity(pid, now_ms=now_ms)
            assert state_cw["order_hist_calls"] == 2
            assert len(act.inferred_positions) == 1
            assert act.inferred_positions[0].symbol == "BTCUSDT"

            # Seed snapshot via service, then turn on persistent 11012005 -> preserves snapshot & humanizes error
            svc = CopyWatcherService(store=store_cw, http_client=hc)
            state_cw["order_hist_calls"] = 0
            await svc.add_targets(pid)
            assert len(svc.get_status()["items"][0]["inferred_positions"]) == 1

            state_cw["always_busy"] = True
            with pytest.raises(CopyWatcherError) as exc_info:
                await cw_client.fetch_trade_activity(pid, now_ms=now_ms)
            assert "11012005" in str(exc_info.value)
            _en, zh_msg = humanize_watcher_error(exc_info.value)
            assert zh_msg == "币安公开订单历史服务繁忙（11012005），请稍后重试（已保留上次调仓快照）"

            polled = await svc.poll_once(force_trades=True)
            assert len(polled["items"][0]["inferred_positions"]) == 1
            assert polled["items"][0]["trade_stale"] is True

    asyncio.run(_run_busy_cw())
