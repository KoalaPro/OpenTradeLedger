"""Bark notifications with support for pasted device URLs and self-hosted servers."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from opentradeledger.config import NotificationSettings

DEFAULT_BARK_SERVER_URL = "https://api.day.app"


def parse_bark_input(raw_server: str, raw_key: str) -> tuple[str, str]:
    """Normalize user-entered Bark server URL and push key.

    New iOS Bark users frequently copy the full sample URL straight from the Bark app
    (e.g. `https://api.day.app/AbCdEfGh123/这里改成你自己的推送内容`) and paste it into
    either the Push Key field or the Server URL field. This helper cleanly extracts
    `(server_base_url, device_push_key)` in all cases.
    """
    srv = (raw_server or "").strip()
    key = (raw_key or "").strip()

    # Case 1: Full URL pasted into the Push Key input
    if key.lower().startswith(("http://", "https://")):
        parts = urlsplit(key)
        if parts.scheme and parts.netloc:
            srv = f"{parts.scheme}://{parts.netloc}"
        segs = [seg for seg in parts.path.split("/") if seg.strip()]
        key = segs[0].strip() if segs else ""
    else:
        key = key.strip("/")
        # If user pasted `Key/sample_body` without scheme
        if "/" in key:
            key = key.split("/", 1)[0].strip()

    # Case 2: Full URL (including device key path segment) pasted into Server URL input
    if srv.lower().startswith(("http://", "https://")):
        s_parts = urlsplit(srv)
        if s_parts.scheme and s_parts.netloc:
            s_segs = [seg for seg in s_parts.path.split("/") if seg.strip()]
            srv = f"{s_parts.scheme}://{s_parts.netloc}"
            if s_segs and not key:
                key = s_segs[0].strip()
    elif srv:
        srv = f"https://{srv.strip('/')}"

    final_server = (srv or DEFAULT_BARK_SERVER_URL).rstrip("/")
    return final_server, key


def bark_configured(settings: NotificationSettings) -> bool:
    _, device_key = parse_bark_input(settings.bark_server_url, settings.bark_push_key)
    return bool(device_key)


def _sanitize_notify_error(raw_err: Any, secrets_list: list[str]) -> str:
    msg = str(raw_err or "Notification request failed")
    for sec in secrets_list:
        s = (sec or "").strip()
        if not s:
            continue
        msg = msg.replace(s, "***")
        encoded = quote(s, safe="")
        if encoded and encoded != s:
            msg = msg.replace(encoded, "***")
    return msg


async def dispatch_notification(
    settings: NotificationSettings,
    *,
    title: str,
    body: str,
    url: str = "",
    bark_params: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    bark_srv, bark_key = parse_bark_input(settings.bark_server_url, settings.bark_push_key)
    if not bark_key:
        return {"ok": False, "sent": [], "errors": {}}

    safe_title = (title or "").replace("/", "／").strip()
    safe_body = (body or "").replace("/", "／").strip()
    bark_url = f"{bark_srv}/{quote(bark_key, safe='')}/{quote(safe_title, safe='')}/{quote(safe_body, safe='')}"
    params = {k: str(v) for k, v in (bark_params or {}).items() if v is not None and str(v) != ""}
    if url:
        params.setdefault("url", url)

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=8.0) as bark_client:
                response = await bark_client.get(bark_url, params=params)
        else:
            response = await client.get(bark_url, params=params)
        response.raise_for_status()
    except Exception as exc:
        error = _sanitize_notify_error(exc, [bark_key, settings.bark_push_key])
        return {"ok": False, "sent": [], "errors": {"bark": error}}
    return {"ok": True, "sent": ["bark"], "errors": {}}
