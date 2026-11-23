from pathlib import Path

from fastapi.testclient import TestClient

from opentradeledger.adapters.base import AdapterMeta, BaseExchangeAdapter
from opentradeledger.adapters.registry import build_default_registry
from opentradeledger.api.app import create_app
from opentradeledger.core.venue_health import humanize_exchange_error
from opentradeledger.security.local_auth import AUTH_COOKIE_NAME, LocalAuthManager


def test_local_auth_lifecycle_rate_limit_session_rotation_and_reset(tmp_path: Path):
    app = create_app(data_dir=tmp_path, demo_mode=False, enable_background_sync=False)
    client = TestClient(app)

    # 1. Health & auth/status are open; data APIs require first-time setup or skip
    assert client.get("/api/v1/health").status_code == 200
    st0 = client.get("/api/v1/auth/status").json()
    assert st0["require_setup"] is True
    assert st0["authenticated"] is False
    assert st0["can_skip"] is True

    blocked = client.get("/api/v1/dashboard")
    assert blocked.status_code == 401
    assert blocked.json()["require_setup"] is True

    # Unauthenticated caller cannot hit change-password
    assert client.post("/api/v1/auth/change-password", json={"new_password": "hacked123"}).status_code == 401

    # 2. Skip setup allows local passwordless access
    skip_res = client.post("/api/v1/auth/skip")
    assert skip_res.status_code == 200
    assert client.get("/api/v1/settings").status_code == 200

    # 3. Enable password from Settings via change-password when currently skipped
    enable_res = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "", "new_password": "nas-secret-2026", "remember": True},
    )
    assert enable_res.status_code == 200
    auth_file = tmp_path / ".auth.json"
    assert auth_file.exists()
    raw_disk = auth_file.read_text(encoding="utf-8")
    assert "nas-secret-2026" not in raw_disk
    assert (auth_file.stat().st_mode & 0o777) == 0o600

    old_cookie = client.cookies.get(AUTH_COOKIE_NAME)
    assert old_cookie
    assert client.get("/api/v1/settings").status_code == 200

    # 4. Logout locks API access immediately
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/settings").status_code == 401

    # 5. Wrong password attempts trigger 401 then 429 brute-force cooldown on 5th failure
    for i in range(4):
        bad = client.post("/api/v1/auth/login", json={"password": f"wrong-{i}"})
        assert bad.status_code == 401
    locked = client.post("/api/v1/auth/login", json={"password": "wrong-final"})
    assert locked.status_code == 429
    assert locked.json()["retry_after_sec"] > 0

    # 6. Fresh app instance (cleared in-memory cooldown) verifies login & token_version rotation
    app2 = create_app(data_dir=tmp_path, demo_mode=False, enable_background_sync=False)
    c2 = TestClient(app2)
    assert c2.post("/api/v1/auth/login", json={"password": "nas-secret-2026", "remember": True}).status_code == 200
    token_v1 = c2.cookies.get(AUTH_COOKIE_NAME)
    assert c2.get("/api/v1/settings").status_code == 200

    # Change password rotates token_version -> old cookie rejected
    rot = c2.post(
        "/api/v1/auth/change-password",
        json={"current_password": "nas-secret-2026", "new_password": "new-nas-password", "remember": True},
    )
    assert rot.status_code == 200
    token_v2 = c2.cookies.get(AUTH_COOKIE_NAME)
    assert token_v2 != token_v1

    c_stolen = TestClient(app2)
    c_stolen.cookies.set(AUTH_COOKIE_NAME, token_v1)
    assert c_stolen.get("/api/v1/settings").status_code == 401

    # 7. Disable password requires current password
    assert c2.post("/api/v1/auth/disable", json={"current_password": "wrong"}).status_code == 400
    assert c2.post("/api/v1/auth/disable", json={"current_password": "new-nas-password"}).status_code == 200
    c_fresh = TestClient(app2)
    assert c_fresh.get("/api/v1/settings").status_code == 200

    # 8. Emergency CLI reset clears .auth.json cleanly
    mgr = LocalAuthManager(tmp_path)
    assert mgr.reset_password() is True
    assert not auth_file.exists()


def test_require_auth_env_forbids_skip_and_setup_works(tmp_path: Path):
    app = create_app(
        data_dir=tmp_path,
        demo_mode=False,
        enable_background_sync=False,
        require_auth=True,
    )
    client = TestClient(app)
    st = client.get("/api/v1/auth/status").json()
    assert st["require_setup"] is True
    assert st["can_skip"] is False
    assert client.post("/api/v1/auth/skip").status_code == 400

    setup = client.post("/api/v1/auth/setup", json={"password": "strong-pass", "remember": True})
    assert setup.status_code == 200
    assert client.get("/api/v1/settings").status_code == 200
    assert client.post("/api/v1/auth/disable", json={"current_password": "strong-pass"}).status_code == 400


def test_exchange_health_never_shows_connected_on_error_and_surfaces_reasons(tmp_path: Path):
    should_fail = {"flag": True}

    class FlakyMockAdapter(BaseExchangeAdapter):
        meta = AdapterMeta(
            venue_id="mockex",
            display_name="MockEx",
            display_name_zh="模拟所",
            color_token="gate",
        )

        def create_client(self, cred):
            return self

        async def test_connection(self, cred):
            if should_fail["flag"]:
                raise RuntimeError("HTTP 401: -2015 Invalid API-key, IP, or permissions for action")
            return {"ok": True, "venue_id": "mockex", "read_only": True}

    reg = build_default_registry()
    reg.register(FlakyMockAdapter())

    app = create_app(
        data_dir=tmp_path,
        demo_mode=True,
        registry=reg,
        enable_background_sync=False,
    )
    client = TestClient(app)

    # 1. Right after saving credentials, health is 'unverified' (NOT 'connected'!)
    up = client.post(
        "/api/v1/exchanges",
        json={"venue_id": "mockex", "api_key": "bad_key_123456", "api_secret": "bad_sec_123456"},
    )
    assert up.status_code == 200
    assert up.json()["health"]["status"] == "unverified"

    # 2. Failed connection test flips health to 'error' with exact reason & increments error_count
    t_fail = client.post("/api/v1/exchanges/mockex/test")
    assert t_fail.status_code == 502
    fail_body = t_fail.json()
    assert fail_body["error_code"] == "AUTH_OR_IP_REJECTED"
    assert "IP" in fail_body["detail"]
    assert "白名单" in fail_body["detail_zh"]

    ex_list = client.get("/api/v1/exchanges").json()
    assert ex_list["error_count"] == 1
    mock_entry = next(a for a in ex_list["adapters"] if a["venue_id"] == "mockex")
    assert mock_entry["health"]["status"] == "error"
    assert mock_entry["health"]["error_code"] == "AUTH_OR_IP_REJECTED"

    # 3. Once fixed, test_connection flips status cleanly to 'connected' and clears error_count
    should_fail["flag"] = False
    t_ok = client.post("/api/v1/exchanges/mockex/test")
    assert t_ok.status_code == 200
    assert t_ok.json()["health"]["status"] == "connected"

    ex_list_ok = client.get("/api/v1/exchanges").json()
    assert ex_list_ok["error_count"] == 0
    mock_ok = next(a for a in ex_list_ok["adapters"] if a["venue_id"] == "mockex")
    assert mock_ok["health"]["status"] == "connected"
    assert mock_ok["health"]["error_reason"] == ""

    # 4. If background sync subsequently fails, status flips back to 'error' immediately
    should_fail["flag"] = True
    sync_fail = client.post("/api/v1/sync", json={"force_all": True, "venue_id": "mockex"}).json()
    assert sync_fail["venues"]["mockex"]["ok"] is False
    assert sync_fail["venues"]["mockex"]["error_code"] == "AUTH_OR_IP_REJECTED"

    ex_after_sync = client.get("/api/v1/exchanges").json()
    assert ex_after_sync["error_count"] == 1
    mock_err2 = next(a for a in ex_after_sync["adapters"] if a["venue_id"] == "mockex")
    assert mock_err2["health"]["status"] == "error"


def test_error_humanizer_classifications():
    assert humanize_exchange_error("okx", "code 50105 Passphrase incorrect")["code"] == "PASSPHRASE_ERROR"
    assert humanize_exchange_error("binance", "-1021 Timestamp for this request is outside of the recvWindow")["code"] == "CLOCK_DRIFT"
    assert humanize_exchange_error("gate", "HTTP 429 Too Many Requests")["code"] == "RATE_LIMITED"
    assert humanize_exchange_error("bybit", "ConnectTimeout: timed out")["code"] == "NETWORK_UNREACHABLE"
