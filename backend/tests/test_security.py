import os
import stat
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

from opentradeledger.adapters.base import AdapterMeta, BaseExchangeAdapter
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.api.app import create_app
from opentradeledger.config import NotificationSettings
from opentradeledger.core.ledger_service import LedgerService
from opentradeledger.core.notifier import dispatch_notification
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.readonly_guard import ReadOnlyViolationError, assert_readonly_request
from opentradeledger.security.vault import CredentialVault, ExchangeCredentialRecord, mask_secret


def test_readonly_guard_blocks_non_get_and_unlisted_paths():
    assert_readonly_request("GET", "/api/v5/account/balance", ("/api/v5/account/",))
    assert_readonly_request("get", "/v5/execution/list?category=linear", ("/v5/execution/list",))

    for bad_method in ("POST", "PUT", "DELETE", "PATCH"):
        with pytest.raises(ReadOnlyViolationError):
            assert_readonly_request(bad_method, "/api/v5/account/balance", ("/api/v5/account/",))

    with pytest.raises(ReadOnlyViolationError):
        assert_readonly_request("GET", "/api/v5/trade/order", ("/api/v5/account/", "/api/v5/trade/fills"))


def test_credential_vault_encrypts_at_rest_and_masks_output(tmp_path: Path):
    vault = CredentialVault(tmp_path)
    secret_key = "demo_super_secret_value_for_unit_test_999"
    api_key = "demo_public_key_for_unit_test_12345"

    vault.upsert(
        ExchangeCredentialRecord(
            venue_id="okx",
            label="OKX Main",
            api_key=api_key,
            api_secret=secret_key,
            passphrase="demo_passphrase_value",
        )
    )

    # Key file must be chmod 0600 on POSIX
    key_mode = stat.S_IMODE(os.stat(tmp_path / ".vault.key").st_mode)
    assert key_mode == 0o600

    # Encrypted vault file must not expose plaintext secrets
    raw_bytes = (tmp_path / "vault.enc").read_bytes()
    assert secret_key.encode() not in raw_bytes
    assert api_key.encode() not in raw_bytes

    masked = vault.list_masked()
    assert len(masked) == 1
    assert masked[0]["venue_id"] == "okx"
    assert masked[0]["masked_api_key"] == mask_secret(api_key)
    assert masked[0]["has_passphrase"] is True
    assert "api_secret" not in masked[0]

    loaded = vault.get_decrypted("okx")
    assert loaded is not None
    assert loaded.api_secret == secret_key
    assert loaded.passphrase == "demo_passphrase_value"

    assert vault.delete("okx") is True
    assert vault.list_masked() == []


def test_credential_vault_imports_dotenv_for_all_supported_exchanges(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OKX_API_KEY=okx_demo_key_111111111111",
                "OKX_API_SECRET=okx_demo_secret_111111111111",
                "OKX_API_PASSPHRASE=okx_demo_pass",
                "BYBIT_API_KEY=bybit_demo_key_222222222222",
                "BYBIT_API_SECRET=bybit_demo_secret_222222222222",
                "GATE_API_KEY=your_readonly_gate_api_key",
                "GATE_API_SECRET=your_readonly_gate_api_secret",
            ]
        ),
        encoding="utf-8",
    )
    vault = CredentialVault(tmp_path)
    imported = vault.import_from_dotenv(env_file)
    assert sorted(imported) == ["bybit", "okx"]
    assert vault.get_decrypted("okx") is not None
    assert vault.get_decrypted("bybit") is not None
    # Placeholders must be ignored
    assert vault.get_decrypted("gate") is None


def test_demo_mode_never_bypasses_configured_password_or_require_auth_env(tmp_path: Path):
    # Case A: require_auth=True must NEVER be bypassed even if demo_mode=True
    app_req = create_app(
        data_dir=tmp_path / "req",
        demo_mode=True,
        require_auth=True,
        enable_background_sync=False,
    )
    c_req = TestClient(app_req)
    st = c_req.get("/api/v1/auth/status").json()
    assert st["authenticated"] is False
    assert st["require_setup"] is True
    assert c_req.get("/api/v1/dashboard").status_code == 401

    # Case B: Once a user sets a password, toggling Demo Mode ON must NOT unlock API for unauthenticated callers
    app = create_app(
        data_dir=tmp_path / "live",
        demo_mode=False,
        enable_background_sync=False,
    )
    owner = TestClient(app)
    assert owner.post("/api/v1/auth/setup", json={"password": "owner-pass-99", "remember": True}).status_code == 200
    assert owner.post(
        "/api/v1/exchanges",
        json={"venue_id": "bybit", "api_key": "byb_real_key_12345", "api_secret": "byb_real_sec_12345"},
    ).status_code == 200
    # Owner turns on Demo Mode for screenshots
    assert owner.post("/api/v1/demo", json={"enabled": True}).status_code == 200

    # Unauthenticated LAN visitor must still get 401 on all protected APIs
    stranger = TestClient(app)
    assert stranger.get("/api/v1/exchanges").status_code == 401
    assert stranger.delete("/api/v1/exchanges/bybit").status_code == 401
    assert stranger.put("/api/v1/settings", json={"locale": "en"}).status_code == 401
    assert stranger.get("/api/v1/dashboard").status_code == 401


def test_auth_setup_blocked_once_initialized_via_skip(tmp_path: Path):
    app = create_app(data_dir=tmp_path, demo_mode=False, enable_background_sync=False)
    client = TestClient(app)
    assert client.post("/api/v1/auth/skip").status_code == 200

    # Unauthenticated /setup endpoint cannot be abused to hijack an already-initialized instance
    hijack = client.post("/api/v1/auth/setup", json={"password": "attacker-lock", "remember": True})
    assert hijack.status_code == 400


def test_ai_models_blocks_saved_api_key_exfiltration_and_unsafe_schemes(tmp_path: Path):
    app = create_app(data_dir=tmp_path, demo_mode=True, enable_background_sync=False)
    client = TestClient(app)

    assert client.put(
        "/api/v1/settings",
        json={
            "ai_base_url": "https://api.openai.com/v1",
            "ai_api_key": "sk-saved-production-secret-key",
            "ai_model": "gpt-4o-mini",
        },
    ).status_code == 200

    # Supplying a different base_url without an explicit api_key must be rejected (no silent credential forwarding)
    exfil = client.post(
        "/api/v1/ai-models",
        json={"base_url": "https://evil-collector.example.com/v1", "api_key": ""},
    )
    assert exfil.status_code == 400

    # Unsafe schemes / cloud metadata IP must be blocked
    bad_scheme = client.post(
        "/api/v1/ai-models",
        json={"base_url": "file:///etc/passwd", "api_key": "sk-temp"},
    )
    assert bad_scheme.status_code == 400

    bad_meta = client.post(
        "/api/v1/ai-models",
        json={"base_url": "http://169.254.169.254/latest/meta-data", "api_key": "sk-temp"},
    )
    assert bad_meta.status_code == 400


def test_preferences_secrets_encrypted_at_rest_in_sqlite_and_db_chmod_600(tmp_path: Path):
    store = LedgerStore(tmp_path)
    vault = CredentialVault(tmp_path)
    svc = LedgerService(store, vault, build_default_registry(), default_demo_mode=True)

    ai_sec = "sk-plaintext-must-not-hit-sqlite-disk-999"
    bark_sec = "BARK_PRIVATE_DEVICE_PUSH_KEY_777"

    svc.update_preferences(
        {
            "ai_base_url": "https://api.deepseek.com/v1",
            "ai_api_key": ai_sec,
            "notifications": {
                "bark_push_key": bark_sec,
            },
        }
    )

    # DB file must have 0600 permissions
    db_mode = stat.S_IMODE(os.stat(store.db_path).st_mode)
    assert db_mode == 0o600

    # Raw SQLite bytes (including WAL) must not contain plaintext secrets
    raw_blob = b""
    for p in tmp_path.glob("opentradeledger.db*"):
        raw_blob += p.read_bytes()
    assert ai_sec.encode() not in raw_blob
    assert bark_sec.encode() not in raw_blob

    # Decrypted read works transparently
    loaded = svc.get_preferences()
    assert loaded.ai_api_key == ai_sec
    assert loaded.notifications.bark_push_key == bark_sec


def test_notifier_sanitizes_bark_key_from_error_messages():
    import asyncio

    def _failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"Unauthorized for {request.url}")

    transport = httpx.MockTransport(_failing_handler)

    async def _run():
        async with httpx.AsyncClient(transport=transport) as c:
            return await dispatch_notification(
                NotificationSettings(
                    bark_server_url="https://api.day.app",
                    bark_push_key="SECRET_BARK_PUSH_KEY_UVW",
                ),
                title="Test",
                body="Ping",
                client=c,
            )

    res = asyncio.run(_run())
    assert res["ok"] is False
    combined_err = " ".join(res["errors"].values())
    assert "SECRET_BARK_PUSH_KEY_UVW" not in combined_err


def test_readonly_guard_blocks_dotdot_path_traversal():
    with pytest.raises(ReadOnlyViolationError):
        assert_readonly_request("GET", "/api/v5/account/balance/../../trade/order", ("/api/v5/account/",))
    with pytest.raises(ReadOnlyViolationError):
        assert_readonly_request("GET", "/api/v5/account/%2e%2e/trade/order", ("/api/v5/account/",))


def test_pre_save_exchange_verify_endpoint_does_not_pollute_vault_on_failure(tmp_path: Path):
    class RejectingAdapter(BaseExchangeAdapter):
        meta = AdapterMeta(
            venue_id="rejectex",
            display_name="RejectEx",
            display_name_zh="拒连所",
            color_token="gate",
        )

        def create_client(self, cred):
            return self

        async def test_connection(self, cred):
            raise RuntimeError("HTTP 401: Invalid API Secret signature")

    reg = build_default_registry()
    reg.register(RejectingAdapter())
    app = create_app(data_dir=tmp_path, demo_mode=True, registry=reg, enable_background_sync=False)
    client = TestClient(app)

    verify_res = client.post(
        "/api/v1/exchanges/verify",
        json={"venue_id": "rejectex", "api_key": "typo_key_1111", "api_secret": "typo_sec_1111"},
    )
    assert verify_res.status_code == 502
    assert verify_res.json()["error_code"] == "INVALID_CREDENTIALS"

    # Vault must remain completely empty!
    ex = client.get("/api/v1/exchanges").json()
    assert ex["configured"] == []


def test_dockerfile_healthcheck_respects_otl_port_env():
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    assert "OTL_PORT" in dockerfile.split("HEALTHCHECK", 1)[1]
