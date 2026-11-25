"""Universal zero-terminal OTA self-updater & persistent `/app/data/.ota` overlay loader."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sys
import tarfile
import time
from typing import Any, Callable

import httpx

from opentradeledger.core.version_checker import (
    VersionCheckService,
    is_newer_version,
    normalize_version_tag,
    parse_semver,
)

logger = logging.getLogger("opentradeledger.self_updater")

OTA_ARCHIVE_NAME = "opentradeledger-ota.tar.gz"
OTA_SHA256_NAME = "opentradeledger-ota.sha256"
OTA_SHA_NOTE_RE = re.compile(r"OTA-SHA256:\s*`?([0-9a-fA-F]{64})`?")
_INIT_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')

MAX_ARCHIVE_BYTES = 60 * 1024 * 1024  # 60 MB
MAX_UNPACKED_BYTES = 120 * 1024 * 1024  # 120 MB
DEFAULT_MIRROR_PREFIXES: tuple[str, ...] = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
)


class SelfUpdateError(RuntimeError):
    """Raised when an OTA self-update cannot be safely completed."""

    def __init__(self, message_en: str, message_zh: str | None = None) -> None:
        super().__init__(message_en)
        self.message_en = message_en
        self.message_zh = message_zh or message_en


def extract_ota_sha256_from_text(text: str) -> str:
    """Extract 64-char hex SHA-256 from release body (`OTA-SHA256: <hex>`) or `.sha256` file."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    m = OTA_SHA_NOTE_RE.search(raw)
    if m:
        return m.group(1).lower()
    first_tok = raw.split()[0] if raw.split() else ""
    if re.fullmatch(r"[0-9a-fA-F]{64}", first_tok):
        return first_tok.lower()
    return ""


def read_base_project_version(base_root: Path) -> str:
    """Read `__version__` directly from `base_root/backend/src/opentradeledger/__init__.py`."""
    init_file = base_root / "backend" / "src" / "opentradeledger" / "__init__.py"
    if not init_file.is_file():
        return "0.0.0"
    content = init_file.read_text(encoding="utf-8", errors="ignore")
    m = _INIT_VERSION_RE.search(content)
    return normalize_version_tag(m.group(1)) if m else "0.0.0"


def build_ota_bundle(
    project_root: Path,
    out_dir: Path,
    *,
    version: str,
    min_base_version: str = "1.3.0",
) -> tuple[Path, Path, str]:
    """Package `backend/src/opentradeledger` + `web/dist` + `manifest.json` into deterministic OTA `.tar.gz`."""
    clean_ver = normalize_version_tag(version)
    if not clean_ver or parse_semver(clean_ver) is None:
        raise ValueError(f"Invalid semantic version for OTA bundle: {version}")

    pkg_dir = project_root / "backend" / "src" / "opentradeledger"
    dist_dir = project_root / "web" / "dist"
    if not (pkg_dir / "__init__.py").is_file():
        raise FileNotFoundError(f"Missing backend package at {pkg_dir}")
    if not (dist_dir / "index.html").is_file():
        raise FileNotFoundError(f"Missing built frontend at {dist_dir / 'index.html'}")

    out_dir.mkdir(parents=True, exist_ok=True)
    tar_path = out_dir / OTA_ARCHIVE_NAME
    sha_path = out_dir / OTA_SHA256_NAME

    manifest = {
        "version": clean_ver,
        "tag": f"v{clean_ver}",
        "min_base_version": normalize_version_tag(min_base_version) or "1.3.0",
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")

    with tarfile.open(tar_path, mode="w:gz") as tf:
        m_info = tarfile.TarInfo(name="manifest.json")
        m_info.size = len(manifest_bytes)
        m_info.mode = 0o644
        tf.addfile(m_info, io.BytesIO(manifest_bytes))

        for path in sorted(pkg_dir.rglob("*")):
            if "__pycache__" in path.parts or path.name.endswith((".pyc", ".pyo")) or path.name == ".DS_Store":
                continue
            if path.is_file():
                rel = path.relative_to(project_root).as_posix()
                tf.add(path, arcname=rel, recursive=False)

        for path in sorted(dist_dir.rglob("*")):
            if path.name == ".DS_Store":
                continue
            if path.is_file():
                rel = path.relative_to(project_root).as_posix()
                tf.add(path, arcname=rel, recursive=False)

    sha_hex = hashlib.sha256(tar_path.read_bytes()).hexdigest().lower()
    sha_path.write_text(f"{sha_hex}  {OTA_ARCHIVE_NAME}\n", encoding="utf-8")
    return tar_path, sha_path, sha_hex


def _cleanup_ota_root(ota_root: Path) -> None:
    for child_name in ("active.json", "releases"):
        target = ota_root / child_name
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink(missing_ok=True)
        except OSError:
            pass


def activate_ota_overlay_if_newer(
    data_dir: Path,
    base_root: Path,
    *,
    apply_sys_path: bool = True,
) -> dict[str, Any] | None:
    """Check `<data_dir>/.ota/active.json` and activate it if strictly newer than `base_root`."""
    ota_root = Path(data_dir) / ".ota"
    active_file = ota_root / "active.json"
    if not active_file.is_file():
        return None

    base_ver = read_base_project_version(base_root)
    try:
        meta = json.loads(active_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    ota_ver = normalize_version_tag(str(meta.get("version") or ""))
    rel_name = str(meta.get("dir") or f"v{ota_ver}").strip()
    if not ota_ver or parse_semver(ota_ver) is None or "/" in rel_name or ".." in rel_name:
        return None

    # If the user updated the underlying Docker image to >= ota_ver, supersede & clean up OTA
    if base_ver != "0.0.0" and not is_newer_version(ota_ver, base_ver):
        _cleanup_ota_root(ota_root)
        return None

    rel_dir = ota_root / "releases" / rel_name
    manifest_file = rel_dir / "manifest.json"
    if manifest_file.is_file():
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            min_base = normalize_version_tag(str(manifest.get("min_base_version") or "1.3.0"))
            if base_ver != "0.0.0" and min_base and is_newer_version(min_base, base_ver):
                return None
        except (OSError, ValueError):
            return None

    src_dir = rel_dir / "backend" / "src"
    web_dist_dir = rel_dir / "web" / "dist"
    if not (src_dir / "opentradeledger" / "__init__.py").is_file():
        return None
    if not (web_dist_dir / "index.html").is_file():
        return None

    if apply_sys_path:
        src_str = str(src_dir.resolve())
        if src_str in sys.path:
            sys.path.remove(src_str)
        sys.path.insert(0, src_str)
        os.environ["OTL_WEB_DIST_DIR"] = str(web_dist_dir.resolve())
        os.environ["OTL_BASE_IMAGE_VERSION"] = base_ver
        os.environ["OTL_OTA_ACTIVE"] = "1"

    return {
        "version": ota_ver,
        "base_version": base_ver,
        "src_dir": str(src_dir.resolve()),
        "web_dist_dir": str(web_dist_dir.resolve()),
    }


def _safe_extract_ota_tarball(tar_bytes: bytes, dest_dir: Path) -> dict[str, Any]:
    """Validate and extract OTA `.tar.gz` into `dest_dir`, blocking traversal, links, and unexpected paths."""
    dest_resolved = dest_dir.resolve()
    total_bytes = 0

    try:
        tf = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz")
    except tarfile.TarError as exc:
        raise SelfUpdateError(
            f"Invalid OTA archive format ({exc})",
            "升级包压缩格式损坏，已取消安装",
        ) from exc

    with tf:
        members = tf.getmembers()
        if not members:
            raise SelfUpdateError("Empty OTA archive", "升级包为空，已取消安装")

        for member in members:
            raw_name = member.name.lstrip("./")
            if not raw_name:
                continue
            posix = PurePosixPath(raw_name)
            if posix.is_absolute() or ".." in posix.parts:
                raise SelfUpdateError(
                    f"Unsafe path in OTA archive: {member.name}",
                    "升级包包含不安全路径，已拦截安装",
                )
            norm = posix.as_posix()
            allowed = (
                norm == "manifest.json"
                or norm in {"backend", "backend/src", "web", "web/dist"}
                or norm.startswith("backend/src/opentradeledger/")
                or norm == "backend/src/opentradeledger"
                or norm.startswith("web/dist/")
            )
            if not allowed:
                raise SelfUpdateError(
                    f"Unexpected file in OTA archive: {norm}",
                    f"升级包包含非预期文件（{norm}），已拦截安装",
                )
            if not (member.isfile() or member.isdir()):
                raise SelfUpdateError(
                    f"Disallowed tar entry type in OTA archive: {norm}",
                    "升级包包含符号链接或特殊文件，已拦截安装",
                )
            if member.size < 0 or (total_bytes + member.size) > MAX_UNPACKED_BYTES:
                raise SelfUpdateError(
                    "OTA archive exceeds maximum unpacked size limit",
                    "升级包解压体积超出安全上限，已拦截安装",
                )
            total_bytes += member.size

        for member in members:
            raw_name = member.name.lstrip("./")
            if not raw_name:
                continue
            target = (dest_resolved / PurePosixPath(raw_name)).resolve()
            if not target.is_relative_to(dest_resolved):
                raise SelfUpdateError("Path traversal blocked", "检测到路径越界，已拦截安装")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                target.write_bytes(extracted.read())

    manifest_path = dest_resolved / "manifest.json"
    if not manifest_path.is_file():
        raise SelfUpdateError("OTA archive missing manifest.json", "升级包缺少版本清单 manifest.json")
    if not (dest_resolved / "backend" / "src" / "opentradeledger" / "__init__.py").is_file():
        raise SelfUpdateError("OTA archive missing backend package", "升级包缺少后端核心代码")
    if not (dest_resolved / "web" / "dist" / "index.html").is_file():
        raise SelfUpdateError("OTA archive missing frontend bundle", "升级包缺少前端页面资源")

    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SelfUpdateError("Malformed manifest.json in OTA archive", "升级包清单格式异常") from exc


class SelfUpdateService:
    """Downloads, SHA-256-verifies, stages, and activates official OTA release bundles."""

    def __init__(
        self,
        *,
        data_dir: Path,
        version_service: VersionCheckService,
        base_version: str,
        transport: httpx.AsyncBaseTransport | None = None,
        mirror_prefixes: tuple[str, ...] = DEFAULT_MIRROR_PREFIXES,
        restart_callback: Callable[[], None] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.version_service = version_service
        self.base_version = normalize_version_tag(base_version) or "0.0.0"
        self.transport = transport
        self.mirror_prefixes = mirror_prefixes
        self._restart_callback = restart_callback
        self._lock = asyncio.Lock()

    def _default_schedule_restart(self) -> None:
        def _send_sigterm() -> None:
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except OSError:
                pass

        try:
            loop = asyncio.get_running_loop()
            loop.call_later(0.6, _send_sigterm)
        except RuntimeError:
            _send_sigterm()

    async def _download_with_mirrors(self, direct_url: str) -> bytes:
        candidates = [direct_url]
        if direct_url.startswith("https://github.com/"):
            candidates.extend(f"{prefix}{direct_url}" for prefix in self.mirror_prefixes)

        last_err = ""
        headers = {"User-Agent": "OpenTradeLedger-SelfUpdater"}
        for idx, url in enumerate(candidates):
            timeout_s = 15.0 if idx == 0 else 25.0
            try:
                async with httpx.AsyncClient(
                    timeout=timeout_s,
                    transport=self.transport,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url, headers=headers)
                if resp.status_code == 200 and resp.content:
                    if len(resp.content) > MAX_ARCHIVE_BYTES:
                        raise SelfUpdateError(
                            "Downloaded OTA archive exceeds size limit",
                            "下载的升级包体积超过安全限制",
                        )
                    return resp.content
                last_err = f"HTTP {resp.status_code}"
            except SelfUpdateError:
                raise
            except httpx.HTTPError as exc:
                last_err = exc.__class__.__name__
                continue

        raise SelfUpdateError(
            f"Failed to download OTA package ({last_err or 'network unreachable'})",
            f"下载在线升级包失败（{last_err or '网络超时'}），请稍后重试或在「网络代理配置」填写局域网代理",
        )

    async def _fetch_authoritative_sha256(self, sha_url: str) -> str:
        if not sha_url:
            return ""
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                transport=self.transport,
                follow_redirects=True,
            ) as client:
                resp = await client.get(sha_url, headers={"User-Agent": "OpenTradeLedger-SelfUpdater"})
            if resp.status_code == 200:
                return extract_ota_sha256_from_text(resp.text)
        except httpx.HTTPError:
            return ""
        return ""

    async def perform_update(self) -> dict[str, Any]:
        async with self._lock:
            rel = await self.version_service.check(force_refresh=True, check_enabled=True)
            if rel.get("error") and not rel.get("update_available"):
                raise SelfUpdateError(
                    str(rel.get("error") or "Version check failed"),
                    str(rel.get("error_zh") or rel.get("error") or "检查新版本失败，请稍后重试"),
                )
            if not rel.get("update_available"):
                raise SelfUpdateError(
                    f"Already on latest version v{rel.get('current_version')}",
                    f"当前 v{rel.get('current_version')} 已是最新版本，无需重复升级",
                )

            latest = normalize_version_tag(str(rel.get("latest_version") or ""))
            repo = str(rel.get("github_repo") or "KoalaPro/OpenTradeLedger").strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
                raise SelfUpdateError("Invalid GitHub repository identifier", "远端仓库标识异常")

            asset_url = str(rel.get("ota_asset_url") or "").strip()
            if not asset_url:
                asset_url = f"https://github.com/{repo}/releases/download/v{latest}/{OTA_ARCHIVE_NAME}"

            expected_sha = extract_ota_sha256_from_text(str(rel.get("ota_sha256") or ""))
            if not expected_sha:
                expected_sha = extract_ota_sha256_from_text(str(rel.get("release_notes") or ""))
            if not expected_sha:
                sha_url = str(rel.get("ota_sha256_url") or "").strip() or (
                    f"https://github.com/{repo}/releases/download/v{latest}/{OTA_SHA256_NAME}"
                )
                expected_sha = await self._fetch_authoritative_sha256(sha_url)

            if not expected_sha:
                raise SelfUpdateError(
                    f"Release v{latest} requires a full Docker image update",
                    f"新版本 v{latest} 涉及底层镜像更新，请按下方指引在 NAS Docker 图形界面重新拉取一次镜像",
                )

            tar_bytes = await self._download_with_mirrors(asset_url)
            actual_sha = hashlib.sha256(tar_bytes).hexdigest().lower()
            if actual_sha != expected_sha.lower():
                raise SelfUpdateError(
                    "OTA package SHA-256 checksum mismatch — aborted for safety",
                    "升级包 SHA-256 完整性校验不匹配，已安全拦截本次升级",
                )

            ota_root = self.data_dir / ".ota"
            releases_dir = ota_root / "releases"
            staging_dir = ota_root / f"staging-{int(time.time() * 1000)}"
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            staging_dir.mkdir(parents=True, exist_ok=True)

            try:
                manifest = _safe_extract_ota_tarball(tar_bytes, staging_dir)
                min_base = normalize_version_tag(str(manifest.get("min_base_version") or "1.3.0"))
                if self.base_version != "0.0.0" and min_base and is_newer_version(min_base, self.base_version):
                    raise SelfUpdateError(
                        f"Release v{latest} requires base Docker image >= v{min_base}",
                        f"v{latest} 包含新底层运行库（需基础镜像 >= v{min_base}），请在 NAS Docker 图形界面重新拉取一次镜像",
                    )

                releases_dir.mkdir(parents=True, exist_ok=True)
                target_dir = releases_dir / f"v{latest}"
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                staging_dir.rename(target_dir)
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)

            active_payload = {
                "version": latest,
                "dir": f"v{latest}",
                "sha256": actual_sha,
                "installed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            active_tmp = ota_root / "active.json.tmp"
            active_tmp.write_text(json.dumps(active_payload, indent=2), encoding="utf-8")
            try:
                os.chmod(active_tmp, 0o600)
            except OSError:
                pass
            active_tmp.replace(ota_root / "active.json")

            # Prune older OTA releases cleanly
            for item in releases_dir.iterdir():
                if item.name != f"v{latest}" and item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)

            restart_flag = ota_root / ".restart_requested"
            restart_flag.write_text(latest, encoding="utf-8")

            if self._restart_callback is not None:
                self._restart_callback()
            else:
                self._default_schedule_restart()

            return {
                "ok": True,
                "updated_to": latest,
                "restarting": True,
            }
