"""TDD tests for universal NAS zero-terminal OTA self-updater & overlay loader."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

from fastapi.testclient import TestClient
import httpx
import pytest

from opentradeledger.api.app import create_app
from opentradeledger.core.self_updater import (
    OTA_ARCHIVE_NAME,
    OTA_SHA256_NAME,
    SelfUpdateError,
    SelfUpdateService,
    activate_ota_overlay_if_newer,
    build_ota_bundle,
)
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.version_checker import VersionCheckService


def _make_fake_project(tmp_path: Path, version: str = "1.3.4") -> Path:
    proj = tmp_path / f"proj-{version}"
    pkg = proj / "backend" / "src" / "opentradeledger"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        f'__app_name__ = "OpenTradeLedger"\n__app_name_zh__ = "交易账本"\n__version__ = "{version}"\n',
        encoding="utf-8",
    )
    (pkg / "marker.py").write_text(f'MARKER = "{version}"\n', encoding="utf-8")
    dist = proj / "web" / "dist" / "assets"
    dist.mkdir(parents=True)
    (proj / "web" / "dist" / "index.html").write_text(
        f"<!doctype html><html><body>v{version}</body></html>",
        encoding="utf-8",
    )
    (dist / "app.js").write_text(f'console.log("{version}")\n', encoding="utf-8")
    return proj


@pytest.mark.anyio
async def test_self_update_downloads_verifies_sha256_and_activates_overlay(tmp_path: Path) -> None:
    proj_134 = _make_fake_project(tmp_path, "1.3.4")
    out_dir = tmp_path / "out"
    tar_path, sha_path, sha_hex = build_ota_bundle(
        proj_134,
        out_dir,
        version="1.3.4",
        min_base_version="1.3.0",
    )
    assert tar_path.name == OTA_ARCHIVE_NAME
    assert sha_path.name == OTA_SHA256_NAME
    tar_bytes = tar_path.read_bytes()
    assert hashlib.sha256(tar_bytes).hexdigest() == sha_hex

    asset_url = "https://github.com/KoalaPro/OpenTradeLedger/releases/download/v1.3.4/opentradeledger-ota.tar.gz"
    sha_url = "https://github.com/KoalaPro/OpenTradeLedger/releases/download/v1.3.4/opentradeledger-ota.sha256"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v1.3.4",
                    "name": "v1.3.4 — 一键在线升级",
                    "body": f"支持全品牌 NAS 网页一键升级\n\nOTA-SHA256: `{sha_hex}`",
                    "html_url": "https://github.com/KoalaPro/OpenTradeLedger/releases/tag/v1.3.4",
                    "published_at": "2026-11-25T02:00:00Z",
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {"name": OTA_ARCHIVE_NAME, "browser_download_url": asset_url},
                        {"name": OTA_SHA256_NAME, "browser_download_url": sha_url},
                    ],
                },
            )
        if url == asset_url:
            return httpx.Response(200, content=tar_bytes)
        if url == sha_url:
            return httpx.Response(200, text=f"{sha_hex}  {OTA_ARCHIVE_NAME}\n")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    data_dir = tmp_path / "data"
    store = LedgerStore(data_dir)
    vc = VersionCheckService(store, current_version="1.3.3", transport=transport)
    restarted: list[bool] = []
    updater = SelfUpdateService(
        data_dir=data_dir,
        version_service=vc,
        base_version="1.3.3",
        transport=transport,
        restart_callback=lambda: restarted.append(True),
    )

    res = await updater.perform_update()
    assert res["ok"] is True
    assert res["updated_to"] == "1.3.4"
    assert res["restarting"] is True
    assert restarted == [True]
    assert (data_dir / ".ota" / ".restart_requested").exists()

    # Verify overlay resolution chooses 1.3.4 when base image is 1.3.3
    base_133 = _make_fake_project(tmp_path, "1.3.3")
    overlay = activate_ota_overlay_if_newer(data_dir, base_133, apply_sys_path=False)
    assert overlay is not None
    assert overlay["version"] == "1.3.4"
    assert (Path(overlay["web_dist_dir"]) / "index.html").read_text(encoding="utf-8").endswith("v1.3.4</body></html>")

    # Verify if user later pulls a newer base Docker image (1.3.5), overlay is superseded and cleaned up
    base_135 = _make_fake_project(tmp_path, "1.3.5")
    superseded = activate_ota_overlay_if_newer(data_dir, base_135, apply_sys_path=False)
    assert superseded is None
    assert not (data_dir / ".ota" / "active.json").exists()


@pytest.mark.anyio
async def test_self_update_mirror_fallback_and_sha256_mismatch_rejection(tmp_path: Path) -> None:
    proj_134 = _make_fake_project(tmp_path, "1.3.4")
    tar_path, _, real_sha = build_ota_bundle(proj_134, tmp_path / "out", version="1.3.4")
    tar_bytes = tar_path.read_bytes()
    direct_url = "https://github.com/KoalaPro/OpenTradeLedger/releases/download/v1.3.4/opentradeledger-ota.tar.gz"

    # Case 1: Direct GitHub times out, mirror succeeds and passes SHA-256 check
    def fallback_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v1.3.4",
                    "name": "v1.3.4",
                    "body": f"OTA-SHA256: `{real_sha}`",
                    "draft": False,
                    "prerelease": False,
                    "assets": [{"name": OTA_ARCHIVE_NAME, "browser_download_url": direct_url}],
                },
            )
        if url == direct_url:
            raise httpx.ConnectTimeout("direct github timeout")
        if direct_url in url:
            return httpx.Response(200, content=tar_bytes)
        return httpx.Response(404)

    t1 = httpx.MockTransport(fallback_handler)
    d1 = tmp_path / "d1"
    vc1 = VersionCheckService(LedgerStore(d1), current_version="1.3.3", transport=t1)
    up1 = SelfUpdateService(data_dir=d1, version_service=vc1, base_version="1.3.3", transport=t1, restart_callback=lambda: None)
    r1 = await up1.perform_update()
    assert r1["updated_to"] == "1.3.4"

    # Case 2: Tampered mirror payload is strictly rejected by SHA-256 check
    def tampered_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v1.3.4",
                    "name": "v1.3.4",
                    "body": f"OTA-SHA256: `{real_sha}`",
                    "draft": False,
                    "prerelease": False,
                    "assets": [{"name": OTA_ARCHIVE_NAME, "browser_download_url": direct_url}],
                },
            )
        return httpx.Response(200, content=b"tampered-payload")

    t2 = httpx.MockTransport(tampered_handler)
    d2 = tmp_path / "d2"
    vc2 = VersionCheckService(LedgerStore(d2), current_version="1.3.3", transport=t2)
    up2 = SelfUpdateService(data_dir=d2, version_service=vc2, base_version="1.3.3", transport=t2, restart_callback=lambda: None)
    with pytest.raises(SelfUpdateError, match="SHA-256"):
        await up2.perform_update()
    assert not (d2 / ".ota" / "active.json").exists()


@pytest.mark.anyio
async def test_self_update_rejects_path_traversal_archive(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        evil = b"pwned"
        info = tarfile.TarInfo(name="../../evil.py")
        info.size = len(evil)
        tf.addfile(info, io.BytesIO(evil))
    evil_bytes = buf.getvalue()
    evil_sha = hashlib.sha256(evil_bytes).hexdigest()
    direct_url = "https://github.com/KoalaPro/OpenTradeLedger/releases/download/v9.9.9/opentradeledger-ota.tar.gz"

    def evil_handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/releases/latest"):
            return httpx.Response(
                200,
                json={
                    "tag_name": "v9.9.9",
                    "name": "v9.9.9",
                    "body": f"OTA-SHA256: `{evil_sha}`",
                    "draft": False,
                    "prerelease": False,
                    "assets": [{"name": OTA_ARCHIVE_NAME, "browser_download_url": direct_url}],
                },
            )
        return httpx.Response(200, content=evil_bytes)

    t = httpx.MockTransport(evil_handler)
    d = tmp_path / "d_evil"
    vc = VersionCheckService(LedgerStore(d), current_version="1.3.3", transport=t)
    up = SelfUpdateService(data_dir=d, version_service=vc, base_version="1.3.3", transport=t, restart_callback=lambda: None)
    with pytest.raises(SelfUpdateError):
        await up.perform_update()
    assert not (tmp_path / "evil.py").exists()


def test_self_update_api_endpoint_requires_auth_when_password_enabled(tmp_path: Path) -> None:
    app = create_app(
        data_dir=tmp_path / "live_data",
        demo_mode=False,
        enable_background_sync=False,
    )
    client = TestClient(app)
    # Setup password first
    r_setup = client.post("/api/v1/auth/setup", json={"password": "testpassword123", "remember": True})
    assert r_setup.status_code == 200

    # Unauthenticated client must get 401
    unauth = TestClient(app)
    r_unauth = unauth.post("/api/v1/system/self-update")
    assert r_unauth.status_code == 401
