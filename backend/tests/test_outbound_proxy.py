import asyncio
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from opentradeledger.api.app import create_app
from opentradeledger.core.store import LedgerStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    return TestClient(create_app(data_dir=tmp_path, auto_prompt_setup=False, enable_background_sync=False))


@pytest.mark.parametrize("invalid", [
    "http://", "http://127.0.0.1:bad", "https://host:0", "http://host:65536",
    "socks5://host:", "http://[::1", "http://a b:80", "https://host/path",
    "http://host?token=private", "ftp://host", "http://a\\b:80", "http://[::1]extra:80",
])
def test_bad_proxy_leaves_working_settings_and_routing_intact(client, invalid):
    path = "/api/v1/settings"
    working = "http://user:p%40ss@[::1]:7890"
    client.put(path, json={"outbound_proxy_url": working}).raise_for_status()
    response = client.put(path, json={"outbound_proxy_url": invalid, "locale": "en"})
    assert response.status_code == 400
    assert response.json()["detail_zh"] and "private" not in response.text
    saved = client.get(path).json()
    assert saved["outbound_proxy_url"] == working and saved["locale"] == "zh-CN"
    assert os.environ["HTTP_PROXY"] == os.environ["HTTPS_PROXY"] == working
    assert "::1" in os.environ["NO_PROXY"]


@pytest.mark.parametrize("scheme", ["socks5", "socks5h"])
def test_socks_setting_constructs_real_sync_and_async_transports(client, scheme):
    path = "/api/v1/settings"
    url = f"{scheme}://localhost:1080"
    client.put(path, json={"outbound_proxy_url": url}).raise_for_status()
    with httpx.Client():
        pass
    async def construct():
        async with httpx.AsyncClient():
            pass
    asyncio.run(construct())
    assert client.get(path).json()["outbound_proxy_url"] == url
    client.put(path, json={"outbound_proxy_url": ""}).raise_for_status()
    assert os.environ.get("HTTP_PROXY") != url


def test_startup_recovers_previously_saved_invalid_proxy(tmp_path, client):
    store = LedgerStore(tmp_path)
    prefs = store.get_preferences()
    prefs.outbound_proxy_url = "http://proxy:bad"
    store.save_preferences(prefs)
    restarted = TestClient(create_app(data_dir=tmp_path, auto_prompt_setup=False, enable_background_sync=False))
    assert restarted.get("/api/v1/settings").json()["outbound_proxy_url"] == ""
    assert os.environ.get("HTTP_PROXY") != prefs.outbound_proxy_url
    with httpx.Client():
        pass


def test_explicit_proxy_overrides_lowercase_host_env_and_clear_restores_it(tmp_path, monkeypatch):
    from urllib.request import getproxies_environment
    from opentradeledger.api.http_helpers import _HOST_PROXY_ENV

    for name, original in _HOST_PROXY_ENV.items():
        monkeypatch.setenv(name, original or "")
    for name, value in {"http_proxy": "http://host-default:81", "https_proxy": "http://host-default:82",
                        "no_proxy": "internal.test"}.items():
        monkeypatch.setitem(_HOST_PROXY_ENV, name, value)
    http = TestClient(create_app(data_dir=tmp_path, auto_prompt_setup=False, enable_background_sync=False))
    url = "http://specified:8081"
    http.put("/api/v1/settings", json={"outbound_proxy_url": url}).raise_for_status()
    routes = getproxies_environment()
    assert routes["http"] == routes["https"] == url
    assert "internal.test" in routes["no"] and "localhost" in routes["no"]
    http.put("/api/v1/settings", json={"outbound_proxy_url": ""}).raise_for_status()
    assert getproxies_environment()["http"] == "http://host-default:81"
    assert getproxies_environment()["no"] == "internal.test"


def test_missing_socks_extra_gives_actionable_error_and_preserves_routing(client, monkeypatch):
    monkeypatch.setattr("opentradeledger.api.http_helpers.find_spec", lambda name: None)
    saved = os.environ.get("HTTP_PROXY")
    response = client.put("/api/v1/settings", json={"outbound_proxy_url": "socks5://proxy:1080"})
    assert response.status_code == 400 and "Docker" in response.json()["detail_zh"]
    assert client.get("/api/v1/settings").json()["outbound_proxy_url"] == ""
    assert os.environ.get("HTTP_PROXY") == saved


def test_ota_overlay_exposes_socks_dependency_to_old_base(tmp_path):
    import shutil
    import subprocess
    import sys
    import zipfile
    from pathlib import Path
    import opentradeledger

    pkg = tmp_path / "opentradeledger"
    wheels = pkg / "_ota_wheels"
    wheels.mkdir(parents=True)
    shutil.copy(Path(opentradeledger.__file__), pkg / "__init__.py")
    with zipfile.ZipFile(wheels / "socksio-1.0.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("socksio/__init__.py", "overlay_dependency = True")
    subprocess.run(
        [sys.executable, "-S", "-c", "import opentradeledger, socksio; assert socksio.overlay_dependency"],
        cwd=tmp_path, env={**os.environ, "OTL_OTA_ACTIVE": "1", "PYTHONPATH": ""}, check=True,
    )
