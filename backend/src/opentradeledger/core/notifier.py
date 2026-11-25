"""Pluggable notification dispatcher supporting Webhook, Telegram Bot, and Bark (with smart URL parsing)."""

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


def _sanitize_notify_error(raw_err: Any, secrets_list: list[str], webhook_url: str = "") -> str:
    msg = str(raw_err or "Notification request failed")
    for sec in secrets_list:
        s = (sec or "").strip()
        if not s:
            continue
        msg = msg.replace(s, "***")
        encoded = quote(s, safe="")
        if encoded and encoded != s:
            msg = msg.replace(encoded, "***")
    wh = (webhook_url or "").strip()
    if wh:
        parts = urlsplit(wh)
        safe_wh = f"{parts.scheme}://{parts.netloc}/***" if parts.scheme and parts.netloc else "***"
        msg = msg.replace(wh, safe_wh)
        if parts.path and len(parts.path) > 1:
            msg = msg.replace(parts.path, "/***")
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
    sent_channels: list[str] = []
    errors: dict[str, str] = {}
    wh = settings.webhook_url.strip()
    tg_tok = settings.telegram_bot_token.strip()
    raw_bark_srv = settings.bark_server_url
    raw_bark_k = settings.bark_push_key
    bark_srv, bark_k = parse_bark_input(raw_bark_srv, raw_bark_k)
    secrets_list = [tg_tok, bark_k, raw_bark_k.strip()]

    async def _run(c: httpx.AsyncClient) -> None:
        if wh:
            try:
                r = await c.post(
                    wh,
                    json={"title": title, "body": body, "url": url},
                )
                r.raise_for_status()
                sent_channels.append("webhook")
            except Exception as exc:
                errors["webhook"] = _sanitize_notify_error(exc, secrets_list, webhook_url=wh)

        if tg_tok and settings.telegram_chat_id.strip():
            try:
                tg_url = f"https://api.telegram.org/bot{tg_tok}/sendMessage"
                text = f"{title}\n{body}" + (f"\n{url}" if url else "")
                r = await c.post(tg_url, json={"chat_id": settings.telegram_chat_id.strip(), "text": text})
                r.raise_for_status()
                sent_channels.append("telegram")
            except Exception as exc:
                errors["telegram"] = _sanitize_notify_error(exc, secrets_list)

        if bark_k:
            try:
                safe_title = (title or "").replace("/", "／").strip()
                safe_body = (body or "").replace("/", "／").strip()
                bark_url = f"{bark_srv}/{quote(bark_k, safe='')}/{quote(safe_title, safe='')}/{quote(safe_body, safe='')}"
                params: dict[str, str] = {}
                if bark_params:
                    params.update({k: str(v) for k, v in bark_params.items() if v is not None and str(v) != ""})
                if url and "url" not in params:
                    params["url"] = url
                r = await c.get(bark_url, params=params or None)
                r.raise_for_status()
                sent_channels.append("bark")
            except Exception as exc:
                errors["bark"] = _sanitize_notify_error(exc, secrets_list)

    if client is not None:
        await _run(client)
    else:
        async with httpx.AsyncClient(timeout=8.0) as c:
            await _run(c)

    return {"ok": bool(sent_channels) and not errors, "sent": sent_channels, "errors": errors}
