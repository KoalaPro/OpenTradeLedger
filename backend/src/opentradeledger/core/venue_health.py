"""Per-exchange live connectivity health tracking and bilingual API error diagnostics.

Ensures OpenTradeLedger NEVER displays a misleading 'Connected' badge when an exchange's
latest API test or background sync failed, and surfaces clear, actionable reasons.
"""

from __future__ import annotations

import re
import time
from typing import Any

from opentradeledger.core.store import LedgerStore

VENUE_HEALTH_META_KEY = "venue_health"

_SENSITIVE_TOKEN_RE = re.compile(r"([A-Za-z0-9_\-]{28,})")


def _sanitize_raw_error(raw: Any, max_len: int = 280) -> str:
    txt = str(raw or "").strip()
    txt = re.sub(r"\s+", " ", txt)
    # Strip HTML error pages (e.g., nginx / openresty / CloudFront) down to title or status
    if "<html" in txt.lower() or "<title>" in txt.lower():
        m = re.search(r"<title>(.*?)</title>", txt, flags=re.IGNORECASE)
        if m:
            prefix = txt.split("<")[0].strip().rstrip(":")
            title = m.group(1).strip()
            txt = f"{prefix}: {title}" if prefix else title
        else:
            txt = re.sub(r"<[^>]+>", " ", txt)
            txt = re.sub(r"\s+", " ", txt).strip()

    def _mask_long(match: re.Match[str]) -> str:
        tok = match.group(1)
        if tok.startswith("http") or "/" in tok:
            return tok
        return f"{tok[:4]}…{tok[-4:]}"

    txt = _SENSITIVE_TOKEN_RE.sub(_mask_long, txt)
    if len(txt) > max_len:
        return txt[: max_len - 1] + "…"
    return txt or "Unknown API error"


def humanize_exchange_error(venue_id: str, raw_err: Any) -> dict[str, str]:
    """Classify cryptic exchange HTTP/API errors into clear bilingual explanations + sanitized raw detail."""
    raw_clean = _sanitize_raw_error(raw_err)
    low = raw_clean.lower()

    # 1. Passphrase issues (OKX / Bitget)
    if any(
        k in low
        for k in (
            "passphrase",
            "50105",
            "40014",
            "400172",
            "access-passphrase",
        )
    ):
        return {
            "code": "PASSPHRASE_ERROR",
            "reason": "Incorrect or missing API Passphrase — verify the passphrase set when creating this API key.",
            "reason_zh": "API 口令（Passphrase）错误或缺失，请核对在交易所创建该 API Key 时设置的口令。",
            "raw": raw_clean,
        }

    # 2. Clock / timestamp drift
    if any(
        k in low
        for k in (
            "-1021",
            "recvwindow",
            "timestamp for this request",
            "request timestamp",
            "50102",
            "40002",
            "request expired",
            "timestamp expired",
        )
    ):
        return {
            "code": "CLOCK_DRIFT",
            "reason": "System clock drift exceeds exchange recvWindow — enable NTP automatic time sync on this host/NAS.",
            "reason_zh": "本机或 NAS 系统时间偏差超出交易所允许范围，请开启系统 NTP 自动网络对时后重试。",
            "raw": raw_clean,
        }

    # 3. Binance -2015 combined key / IP / permission error
    if "-2015" in low or ("invalid api-key" in low and "ip" in low):
        return {
            "code": "AUTH_OR_IP_REJECTED",
            "reason": "Invalid API key, missing Read-Only permission, or your host/NAS public IP is not whitelisted.",
            "reason_zh": "API Key 无效、未勾选只读权限，或当前本机/NAS 的出口 IP 未加入交易所 API 白名单。",
            "raw": raw_clean,
        }

    # 4. IP whitelist explicit rejection
    if any(
        k in low
        for k in (
            "ip not in whitelist",
            "invalid ip",
            "unmatched ip",
            "50110",
            "10010",
            "ip address",
            "whitelist",
        )
    ):
        return {
            "code": "IP_NOT_WHITELISTED",
            "reason": "Current public IP is rejected by the exchange API whitelist — add this host/NAS IP or relax IP restriction.",
            "reason_zh": "当前出口 IP 不在交易所 API 白名单内，请将该设备/NAS 公网 IP 加入白名单。",
            "raw": raw_clean,
        }

    # 5. Invalid API Key / Secret / HMAC Signature
    if any(
        k in low
        for k in (
            "invalid key",
            "invalid api",
            "api-key format invalid",
            "-2014",
            "-1022",
            "signature",
            "sign",
            "50111",
            "50113",
            "40009",
            "40012",
            "10003",
            "10004",
            "700004",
            "http 401",
            "401 unauthorized",
            "apikey invalid",
            "access key",
        )
    ):
        return {
            "code": "INVALID_CREDENTIALS",
            "reason": "API Key or Secret rejected (authentication/signature failed) — verify the key is complete, unrevoked, and Read-Only.",
            "reason_zh": "API Key 或 Secret 校验失败（签名或密钥无效/已过期），请检查密钥是否完整复制且未失效。",
            "raw": raw_clean,
        }

    # 6. Rate limiting
    if any(k in low for k in ("http 429", "429 too many", "-1003", "50011", "rate limit", "too frequent")):
        return {
            "code": "RATE_LIMITED",
            "reason": "Exchange API rate limit reached (HTTP 429) — automatic sync will back off and retry shortly.",
            "reason_zh": "触发交易所接口限频（HTTP 429），后台轮询将自动等待后重试。",
            "raw": raw_clean,
        }

    # 7. Regional / WAF block (403 / 451 / CloudFront / Cloudflare)
    if any(k in low for k in ("http 403", "http 451", "cloudfront", "cloudflare", "forbidden", "restricted location")):
        return {
            "code": "REGIONAL_OR_WAF_BLOCK",
            "reason": "Request blocked by exchange WAF or regional restriction (HTTP 403/451) — check proxy region or API permissions.",
            "reason_zh": "请求被交易所防火墙或地区限制拦截（HTTP 403/451），请检查网络代理节点地区或 API 权限。",
            "raw": raw_clean,
        }

    # 8. Network / DNS / Proxy / Timeout
    if any(
        k in low
        for k in (
            "timeout",
            "timed out",
            "connecttimeout",
            "readtimeout",
            "connecterror",
            "failed (0)",
            "connection refused",
            "network is unreachable",
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
            "proxy",
            "ssl",
            "eof occurred",
            "all connection attempts failed",
        )
    ):
        return {
            "code": "NETWORK_UNREACHABLE",
            "reason": f"Cannot reach {venue_id.upper()} API (network/proxy timeout) — check internet access or HTTPS_PROXY.",
            "reason_zh": f"无法连接 {venue_id.upper()} 接口（网络或代理超时），请检查本机/NAS 网络连通性或 HTTPS_PROXY 配置。",
            "raw": raw_clean,
        }

    # 9. Missing permission
    if any(k in low for k in ("permission", "unauthorized", "no authority", "forbidden")):
        return {
            "code": "PERMISSION_DENIED",
            "reason": "API key lacks required Read-Only account/futures query permission on this exchange.",
            "reason_zh": "该 API Key 缺少账户或合约只读查询权限，请在交易所后台勾选只读读取权限。",
            "raw": raw_clean,
        }

    return {
        "code": "API_ERROR",
        "reason": f"{venue_id.upper()} API returned an error: {raw_clean}",
        "reason_zh": f"{venue_id.upper()} 接口返回异常：{raw_clean}",
        "raw": raw_clean,
    }


class VenueHealthTracker:
    """Persists real connection health per exchange in SQLite meta (`venue_health`)."""

    def __init__(self, store: LedgerStore) -> None:
        self.store = store

    def get_all(self) -> dict[str, dict[str, Any]]:
        raw = self.store.get_meta(VENUE_HEALTH_META_KEY, {})
        return dict(raw) if isinstance(raw, dict) else {}

    def _save_all(self, data: dict[str, dict[str, Any]]) -> None:
        self.store.set_meta(VENUE_HEALTH_META_KEY, data)

    def mark_unverified(self, venue_id: str) -> dict[str, Any]:
        vid = venue_id.strip().lower()
        all_h = self.get_all()
        now_ms = int(time.time() * 1000)
        entry = {
            "status": "unverified",
            "last_attempt_ms": now_ms,
            "last_ok_ms": int((all_h.get(vid) or {}).get("last_ok_ms") or 0),
            "error_code": "",
            "error_reason": "",
            "error_reason_zh": "",
            "raw_error": "",
        }
        all_h[vid] = entry
        self._save_all(all_h)
        return entry

    def record_success(self, venue_id: str) -> dict[str, Any]:
        vid = venue_id.strip().lower()
        all_h = self.get_all()
        now_ms = int(time.time() * 1000)
        entry = {
            "status": "connected",
            "last_attempt_ms": now_ms,
            "last_ok_ms": now_ms,
            "error_code": "",
            "error_reason": "",
            "error_reason_zh": "",
            "raw_error": "",
        }
        all_h[vid] = entry
        self._save_all(all_h)
        return entry

    def record_error(self, venue_id: str, raw_err: Any) -> dict[str, Any]:
        vid = venue_id.strip().lower()
        diag = humanize_exchange_error(vid, raw_err)
        all_h = self.get_all()
        prev = all_h.get(vid) if isinstance(all_h.get(vid), dict) else {}
        now_ms = int(time.time() * 1000)
        entry = {
            "status": "error",
            "last_attempt_ms": now_ms,
            "last_ok_ms": int(prev.get("last_ok_ms") or 0),
            "error_code": diag["code"],
            "error_reason": diag["reason"],
            "error_reason_zh": diag["reason_zh"],
            "raw_error": diag["raw"],
        }
        all_h[vid] = entry
        self._save_all(all_h)
        return entry

    def remove(self, venue_id: str) -> None:
        vid = venue_id.strip().lower()
        all_h = self.get_all()
        if vid in all_h:
            all_h.pop(vid, None)
            self._save_all(all_h)

    def resolve_state(
        self,
        venue_id: str,
        *,
        configured: bool,
        enabled: bool = True,
        poll_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not configured:
            return {
                "status": "not_configured",
                "last_attempt_ms": 0,
                "last_ok_ms": 0,
                "error_code": "",
                "error_reason": "",
                "error_reason_zh": "",
                "raw_error": "",
            }
        if not enabled:
            return {
                "status": "disabled",
                "last_attempt_ms": 0,
                "last_ok_ms": 0,
                "error_code": "",
                "error_reason": "",
                "error_reason_zh": "",
                "raw_error": "",
            }

        vid = venue_id.strip().lower()
        recorded = self.get_all().get(vid)
        if isinstance(recorded, dict) and recorded.get("status") in {"connected", "error", "unverified"}:
            return dict(recorded)

        # Backward compatibility: if adaptive sync schedule already recorded a clean sync timestamp
        last_sync_sec = int((poll_state or {}).get("last_sync_sec") or 0)
        if last_sync_sec > 0:
            return {
                "status": "connected",
                "last_attempt_ms": last_sync_sec * 1000,
                "last_ok_ms": last_sync_sec * 1000,
                "error_code": "",
                "error_reason": "",
                "error_reason_zh": "",
                "raw_error": "",
            }

        return {
            "status": "unverified",
            "last_attempt_ms": 0,
            "last_ok_ms": 0,
            "error_code": "",
            "error_reason": "",
            "error_reason_zh": "",
            "raw_error": "",
        }
