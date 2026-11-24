"""Tests for zero-telemetry GitHub release version checker and /api/v1/version-check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import httpx
import pytest

from opentradeledger.api.app import create_app
from opentradeledger.core.store import LedgerStore
from opentradeledger.core.version_checker import (
    VersionCheckService,
    is_newer_version,
    normalize_version_tag,
)


def test_semver_comparison_rules() -> None:
    assert normalize_version_tag("v1.2.0") == "1.2.0"
    assert normalize_version_tag("V2.0.1") == "2.0.1"

    assert is_newer_version("1.2.0", "1.1.0") is True
    assert is_newer_version("v1.10.0", "1.2.0") is True
    assert is_newer_version("2.0.0", "1.9.9") is True
    assert is_newer_version("1.2.0", "1.2.0-rc.2") is True
    assert is_newer_version("1.2.0-rc.10", "1.2.0-rc.2") is True

    assert is_newer_version("1.2.0", "1.2.0") is False
    assert is_newer_version("v1.1.0", "1.2.0") is False
    assert is_newer_version("not-a-version", "1.2.0") is False


@pytest.mark.asyncio
async def test_version_checker_caching_and_zero_telemetry(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET"
        assert dict(request.url.params) == {}
        return httpx.Response(
            200,
            json={
                "tag_name": "v1.5.0",
                "name": "v1.5.0 — NAS Update Check",
                "html_url": "https://github.com/KoalaPro/OpenTradeLedger/releases/tag/v1.5.0",
                "body": "- Added new version reminder\n- Multi-arch GHCR image",
                "published_at": "2026-11-24T04:00:00Z",
                "draft": False,
                "prerelease": False,
            },
        )

    svc = VersionCheckService(
        store,
        current_version="1.2.0",
        github_repo="KoalaPro/OpenTradeLedger",
        transport=httpx.MockTransport(handler),
    )

    first = await svc.check(force_refresh=False, check_enabled=True)
    assert first["current_version"] == "1.2.0"
    assert first["latest_version"] == "1.5.0"
    assert first["update_available"] is True
    assert first["stale"] is False
    assert first["error"] == ""
    assert len(calls) == 1

    # Second call within 6h TTL must hit cache without another HTTP request
    second = await svc.check(force_refresh=False, check_enabled=True)
    assert second["update_available"] is True
    assert second["latest_version"] == "1.5.0"
    assert len(calls) == 1

    # Explicit force_refresh bypasses TTL
    third = await svc.check(force_refresh=True, check_enabled=True)
    assert third["update_available"] is True
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_version_checker_honors_opt_out_and_surfaces_errors(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path)
    calls = 0

    def rate_limit_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, json={"message": "API rate limit exceeded"})

    svc = VersionCheckService(
        store,
        current_version="1.2.0",
        transport=httpx.MockTransport(rate_limit_handler),
    )

    # Background check disabled -> 0 HTTP requests
    disabled = await svc.check(force_refresh=False, check_enabled=False)
    assert calls == 0
    assert disabled["check_enabled"] is False
    assert disabled["update_available"] is False

    # Manual check when rate-limited -> surfaces honest error, never fakes success
    manual_err = await svc.check(force_refresh=True, check_enabled=False)
    assert calls == 1
    assert manual_err["stale"] is True
    assert "rate limit" in manual_err["error"].lower()
    assert "频率受限" in manual_err["error_zh"]


def test_version_check_api_endpoint(tmp_path: Path) -> None:
    app = create_app(
        data_dir=tmp_path,
        demo_mode=True,
        enable_background_sync=False,
    )

    def mock_gh(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tag_name": "v9.9.0",
                "name": "v9.9.0",
                "html_url": "https://github.com/KoalaPro/OpenTradeLedger/releases/tag/v9.9.0",
                "body": "Major update",
                "published_at": "2026-11-24T04:00:00Z",
                "draft": False,
                "prerelease": False,
            },
        )

    app.state.version_service.transport = httpx.MockTransport(mock_gh)

    with TestClient(app) as client:
        res = client.get("/api/v1/version-check")
        assert res.status_code == 200
        body: dict[str, Any] = res.json()
        assert body["latest_version"] == "9.9.0"
        assert body["update_available"] is True

        # Opting out via PUT /api/v1/settings persists update_check_enabled=False
        put_res = client.put("/api/v1/settings", json={"update_check_enabled": False})
        assert put_res.status_code == 200
        assert put_res.json()["update_check_enabled"] is False
