"""HTTP, proxy, session cookie, client fingerprint, and SVG helper utilities."""

from __future__ import annotations

import hashlib
import ipaddress
import os
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from fastapi.responses import Response

from opentradeledger.core.copy_watcher import extract_portfolio_ids
from opentradeledger.core.store import LedgerStore
from opentradeledger.security.local_auth import AUTH_COOKIE_NAME

_HOST_PROXY_ENV = {
    name: os.environ.get(name)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
}
_SAFE_LAN_NO_PROXY = ",".join(
    ("127.0.0.1", "localhost", "::1", f"{192}.{168}.0.0/16", f"{10}.0.0.0/8", f"{172}.{16}.0.0/12")
)


class OutboundProxyError(ValueError):
    def __init__(self, message: str, message_zh: str) -> None:
        super().__init__(message)
        self.message_zh = message_zh


def _validate_outbound_proxy(raw_proxy: str) -> str:
    cleaned = raw_proxy.strip()
    if not cleaned:
        return ""
    try:
        parts = urlsplit(cleaned)
        if (parts.scheme not in {"http", "https", "socks5", "socks5h"}
                or not parts.hostname or parts.netloc.endswith(":") or parts.port == 0
                or parts.path not in ("", "/") or "?" in cleaned or "#" in cleaned
                or "\\" in cleaned or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in cleaned)):
            raise ValueError
        # Validate with the parser used by the transport as well (e.g. bracketed IPv6).
        httpx.Proxy(cleaned)
    except (ValueError, httpx.InvalidURL) as exc:
        raise OutboundProxyError(
            "Invalid proxy address. Enter an HTTP, HTTPS, SOCKS5 or SOCKS5H host with an optional port (1–65535).",
            "代理地址无效。请填写 HTTP、HTTPS、SOCKS5 或 SOCKS5H 的主机地址，可选端口为 1–65535。",
        ) from exc
    if parts.scheme.startswith("socks") and find_spec("socksio") is None:
        raise OutboundProxyError(
            "This installation lacks SOCKS support. Update the Docker image or install the project dependencies.",
            "当前安装缺少 SOCKS 支持。请更新 Docker 镜像，源码部署请安装项目依赖。",
        )
    return cleaned


def _apply_outbound_proxy(cleaned: str) -> None:
    if not cleaned:
        for name, original in _HOST_PROXY_ENV.items():
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
        return

    # urllib/HTTPX prefer lowercase settings when the host provides both forms.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[name] = cleaned
    no_proxy = ",".join(dict.fromkeys(filter(None, (
        _HOST_PROXY_ENV["no_proxy"], _HOST_PROXY_ENV["NO_PROXY"], _SAFE_LAN_NO_PROXY,
    ))))
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = no_proxy


def _import_dotenv_watcher_targets_once(data_path: Path, store: LedgerStore) -> None:
    if store.get_meta("copy_watcher_dotenv_imported", False):
        return
    env_file = data_path / ".env"
    if not env_file.is_file():
        return
    try:
        kv: dict[str, str] = {}
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                kv[k.strip()] = v.strip().strip('"').strip("'")
        raw_ids = kv.get("BINANCE_WATCH_PORTFOLIO_IDS", "")
        pids = extract_portfolio_ids(raw_ids)
        prefs = store.get_preferences()
        if pids and not prefs.copy_watcher_targets:
            prefs.copy_watcher_targets = list(pids)
            store.save_preferences(prefs)
        store.set_meta("copy_watcher_dotenv_imported", True)
    except Exception:
        pass


def _is_trusted_local_or_private_host(host: str) -> bool:
    clean = (host or "").strip().lower()
    if not clean or clean == "localhost":
        return True
    try:
        ip_obj = ipaddress.ip_address(clean)
        return bool(ip_obj.is_loopback or ip_obj.is_private)
    except ValueError:
        return False


def _is_https_request(request: Request | None) -> bool:
    if request is None:
        return False
    if request.url.scheme.lower() == "https":
        return True
    xf_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return xf_proto == "https"


def _set_session_cookie(
    resp: Response,
    token: str,
    max_age_sec: int,
    remember: bool,
    request: Request | None = None,
) -> None:
    kwargs: dict[str, Any] = {
        "key": AUTH_COOKIE_NAME,
        "value": token,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
    }
    if _is_https_request(request):
        kwargs["secure"] = True
    if remember:
        kwargs["max_age"] = max_age_sec
    resp.set_cookie(**kwargs)


def resolve_client_key(request: Request) -> str:
    raw_host = request.client.host if request.client and request.client.host else "local"
    if _is_trusted_local_or_private_host(raw_host):
        xff = request.headers.get("x-forwarded-for", "").strip()
        if xff:
            first_xff = xff.split(",", 1)[0].strip()
            if first_xff:
                return f"{raw_host}|xff:{first_xff}"
        ua = request.headers.get("user-agent", "").strip()
        if ua:
            ua_hash = hashlib.sha256(ua.encode("utf-8")).hexdigest()[:8]
            return f"{raw_host}|ua:{ua_hash}"
    return raw_host


def build_monogram_svg(instrument: str) -> str:
    base = "".join(ch for ch in instrument.upper().split("_")[0] if ch.isalnum())[:2] or "?"
    palette = ("#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ec4899", "#06b6d4", "#6366f1")
    color = palette[sum(ord(c) for c in base) % len(palette)]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        f'<circle cx="16" cy="16" r="15" fill="{color}" fill-opacity="0.18" stroke="{color}" stroke-opacity="0.55" stroke-width="1.2"/>'
        f'<text x="16" y="20.5" text-anchor="middle" font-family="-apple-system,Inter,sans-serif" '
        f'font-size="{11 if len(base) > 1 else 13}" font-weight="700" fill="{color}">{base}</text>'
        f"</svg>"
    )


__all__ = [
    "_SAFE_LAN_NO_PROXY",
    "_import_dotenv_watcher_targets_once",
    "_is_https_request",
    "_is_trusted_local_or_private_host",
    "_set_session_cookie",
    "OutboundProxyError",
    "_apply_outbound_proxy",
    "_validate_outbound_proxy",
    "build_monogram_svg",
    "resolve_client_key",
]
