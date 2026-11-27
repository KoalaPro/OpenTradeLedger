"""Zero-telemetry GitHub release version checker with 6h SQLite cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import re
import time
from typing import Any

import httpx

from opentradeledger import __version__
from opentradeledger.core.network_errors import HTTP_CLIENT_ERRORS, SocksProtocolError
from opentradeledger.core.store import LedgerStore

logger = logging.getLogger("opentradeledger.version_checker")
TZ_UTC8 = timezone(timedelta(hours=8))

DEFAULT_GITHUB_REPO = "KoalaPro/OpenTradeLedger"
CACHE_TTL_SEC = 6 * 3600  # 6 hours
OTA_ARCHIVE_ASSET_NAME = "opentradeledger-ota.tar.gz"
OTA_SHA256_ASSET_NAME = "opentradeledger-ota.sha256"
_OTA_SHA_BODY_RE = re.compile(r"OTA-SHA256:\s*`?([0-9a-fA-F]{64})`?")

_SEMVER_RE = re.compile(
    r"^[vV]?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def normalize_version_tag(raw: str) -> str:
    """Strip leading 'v'/'V' and whitespace while keeping clean version text."""
    cleaned = str(raw or "").strip()
    if cleaned.startswith(("v", "V")) and len(cleaned) > 1 and cleaned[1].isdigit():
        return cleaned[1:]
    return cleaned


def parse_semver(raw: str) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]] | None:
    """Parse SemVer into comparable tuple; final releases sort above pre-releases."""
    m = _SEMVER_RE.match(str(raw or "").strip())
    if not m:
        return None
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    prerelease = m.group(4) or ""
    # is_final: 1 for stable release, 0 for -rc/-beta/-alpha pre-release
    is_final = 0 if prerelease else 1
    pre_parts: tuple[tuple[int, int | str], ...] = (
        tuple((0, int(p)) if p.isdigit() else (1, p) for p in prerelease.split("."))
        if prerelease
        else ()
    )
    return (major, minor, patch, is_final, pre_parts)


def is_newer_version(candidate: str, current: str) -> bool:
    """Return True strictly when candidate SemVer is newer than current SemVer."""
    cand_parsed = parse_semver(candidate)
    curr_parsed = parse_semver(current)
    if cand_parsed is None or curr_parsed is None:
        return False
    return cand_parsed > curr_parsed


def _format_iso_to_utc8(iso_str: str) -> str:
    text = str(iso_str or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(TZ_UTC8).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text[:16]


class VersionCheckService:
    """Checks GitHub Releases via unauthenticated read-only GET with zero telemetry."""

    def __init__(
        self,
        store: LedgerStore,
        *,
        current_version: str = __version__,
        github_repo: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        ttl_sec: int = CACHE_TTL_SEC,
    ) -> None:
        self.store = store
        self.current_version = normalize_version_tag(current_version)
        self.github_repo = (
            github_repo or os.getenv("OTL_GITHUB_REPO", DEFAULT_GITHUB_REPO)
        ).strip() or DEFAULT_GITHUB_REPO
        self.transport = transport
        self.ttl_sec = ttl_sec
        self._mem_cache: dict[str, Any] | None = None

    @property
    def releases_page_url(self) -> str:
        return f"https://github.com/{self.github_repo}/releases"

    @property
    def api_latest_url(self) -> str:
        return f"https://api.github.com/repos/{self.github_repo}/releases/latest"

    def _load_cached(self) -> dict[str, Any] | None:
        if self._mem_cache is not None:
            return self._mem_cache
        saved = self.store.get_meta("version_check_cache", None)
        if isinstance(saved, dict) and saved.get("latest_version"):
            self._mem_cache = saved
            return saved
        return None

    def _enrich(
        self,
        base: dict[str, Any],
        *,
        check_enabled: bool,
        stale: bool = False,
        error: str = "",
        error_zh: str = "",
    ) -> dict[str, Any]:
        latest = normalize_version_tag(str(base.get("latest_version") or ""))
        has_update = bool(latest and is_newer_version(latest, self.current_version))
        ota_asset_url = str(base.get("ota_asset_url") or "")
        ota_sha256_url = str(base.get("ota_sha256_url") or "")
        ota_sha256 = str(base.get("ota_sha256") or "")
        return {
            "current_version": self.current_version,
            "latest_version": latest or self.current_version,
            "update_available": has_update,
            "ota_ready": bool(has_update and (ota_asset_url or ota_sha256)),
            "ota_asset_url": ota_asset_url,
            "ota_sha256_url": ota_sha256_url,
            "ota_sha256": ota_sha256,
            "check_enabled": check_enabled,
            "github_repo": self.github_repo,
            "release_url": str(base.get("release_url") or self.releases_page_url),
            "release_name": str(base.get("release_name") or ""),
            "release_notes": str(base.get("release_notes") or ""),
            "published_at": str(base.get("published_at") or ""),
            "checked_sec": int(base.get("checked_sec") or 0),
            "checked_at": str(base.get("checked_at") or ""),
            "stale": stale,
            "error": error,
            "error_zh": error_zh,
        }

    async def _fetch_latest_release(self) -> tuple[dict[str, Any] | None, str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "OpenTradeLedger-VersionCheck",
        }
        resp: httpx.Response | None = None
        last_exc: Exception | None = None
        for _attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=8.0,
                    transport=self.transport,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(self.api_latest_url, headers=headers)
                break
            except HTTP_CLIENT_ERRORS as exc:
                last_exc = exc
                continue
        if resp is None:
            if isinstance(last_exc, (SocksProtocolError, httpx.ProxyError)):
                return None, "Proxy handshake failed while connecting to GitHub", "代理握手失败，未能连接 GitHub。请检查代理服务后重试。"
            if isinstance(last_exc, httpx.TimeoutException):
                return None, "GitHub API request timed out", "连接 GitHub 超时，请稍后重试"
            exc_name = last_exc.__class__.__name__ if last_exc else "HTTPError"
            return (
                None,
                f"Network error reaching GitHub ({exc_name})",
                f"无法连接 GitHub 接口（{exc_name}）",
            )

        if resp.status_code == 404:
            return (
                None,
                "No published GitHub Release found yet",
                "仓库暂无已发布的正式 Release 版本",
            )
        if resp.status_code == 403:
            return (
                None,
                "GitHub API rate limit reached (try again later)",
                "GitHub 匿名请求频率受限，请稍后再试",
            )
        if resp.status_code != 200:
            return (
                None,
                f"GitHub API returned HTTP {resp.status_code}",
                f"GitHub 接口返回异常状态码 HTTP {resp.status_code}",
            )

        try:
            data = resp.json()
        except ValueError:
            return None, "Invalid JSON from GitHub API", "GitHub 返回数据格式异常"

        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            return None, "Latest release is not a stable release", "未找到正式发布版本"

        raw_tag = str(data.get("tag_name") or "").strip()
        latest_ver = normalize_version_tag(raw_tag)
        if not latest_ver or parse_semver(latest_ver) is None:
            return (
                None,
                f"Unrecognized release tag format: {raw_tag or 'empty'}",
                f"无法解析远端版本号格式：{raw_tag or '空'}",
            )

        now_s = int(time.time())
        raw_body = str(data.get("body") or "").strip()
        m_sha = _OTA_SHA_BODY_RE.search(raw_body)
        ota_sha256 = m_sha.group(1).lower() if m_sha else ""
        # Strip machine-only OTA-SHA256 footer line from human-facing release notes
        clean_body = re.sub(r"\n*(?:---\s*\n+)?OTA-SHA256:\s*`?[0-9a-fA-F]{64}`?\s*", "", raw_body).strip()
        if len(clean_body) > 2400:
            clean_body = clean_body[:2400].rstrip() + "\n…"

        ota_asset_url = ""
        ota_sha256_url = ""
        raw_assets = data.get("assets")
        if isinstance(raw_assets, list):
            for asset in raw_assets:
                if not isinstance(asset, dict):
                    continue
                a_name = str(asset.get("name") or "").strip()
                dl_url = str(asset.get("browser_download_url") or "").strip()
                if a_name == OTA_ARCHIVE_ASSET_NAME and dl_url:
                    ota_asset_url = dl_url
                elif a_name == OTA_SHA256_ASSET_NAME and dl_url:
                    ota_sha256_url = dl_url

        payload = {
            "latest_version": latest_ver,
            "release_url": str(data.get("html_url") or self.releases_page_url),
            "release_name": str(data.get("name") or f"v{latest_ver}"),
            "release_notes": clean_body,
            "ota_asset_url": ota_asset_url,
            "ota_sha256_url": ota_sha256_url,
            "ota_sha256": ota_sha256,
            "published_at": _format_iso_to_utc8(str(data.get("published_at") or "")),
            "checked_sec": now_s,
            "checked_at": datetime.fromtimestamp(now_s, tz=TZ_UTC8).strftime("%Y-%m-%d %H:%M"),
        }
        return payload, "", ""

    async def check(
        self,
        *,
        force_refresh: bool = False,
        check_enabled: bool = True,
    ) -> dict[str, Any]:
        cached = self._load_cached()

        # Respect user opt-out for automatic background checks unless manually clicked
        if not check_enabled and not force_refresh:
            if cached is not None:
                return self._enrich(cached, check_enabled=False, stale=True)
            return self._enrich({}, check_enabled=False, stale=False)

        now_s = int(time.time())
        if (
            not force_refresh
            and cached is not None
            and (now_s - int(cached.get("checked_sec") or 0)) < self.ttl_sec
        ):
            return self._enrich(cached, check_enabled=check_enabled, stale=False)

        live, err_en, err_zh = await self._fetch_latest_release()
        if live is not None:
            self._mem_cache = live
            self.store.set_meta("version_check_cache", live)
            return self._enrich(live, check_enabled=check_enabled, stale=False)

        logger.debug("GitHub version check failed: %s", err_en)
        if cached is not None:
            return self._enrich(
                cached,
                check_enabled=check_enabled,
                stale=True,
                error=err_en,
                error_zh=err_zh,
            )

        return self._enrich(
            {},
            check_enabled=check_enabled,
            stale=True,
            error=err_en,
            error_zh=err_zh,
        )
