"""Pluggable notification dispatcher supporting Webhook, Telegram Bot, and Bark."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from opentradeledger.config import NotificationSettings


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
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    sent_channels: list[str] = []
    errors: dict[str, str] = {}
    wh = settings.webhook_url.strip()
    tg_tok = settings.telegram_bot_token.strip()
    bark_k = settings.bark_push_key.strip()
    secrets_list = [tg_tok, bark_k]

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
                base = (settings.bark_server_url or "https://api.day.app").rstrip("/")
                bark_url = f"{base}/{quote(bark_k, safe='')}/{quote(title, safe='')}/{quote(body, safe='')}"
                params = {"url": url} if url else None
                r = await c.get(bark_url, params=params)
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
