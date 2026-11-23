"""Pluggable notification dispatcher supporting Webhook, Telegram Bot, and Bark."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from opentradeledger.config import NotificationSettings


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

    async def _run(c: httpx.AsyncClient) -> None:
        if settings.webhook_url.strip():
            try:
                r = await c.post(
                    settings.webhook_url.strip(),
                    json={"title": title, "body": body, "url": url},
                )
                r.raise_for_status()
                sent_channels.append("webhook")
            except Exception as exc:
                errors["webhook"] = str(exc)

        if settings.telegram_bot_token.strip() and settings.telegram_chat_id.strip():
            try:
                tg_url = f"https://api.telegram.org/bot{settings.telegram_bot_token.strip()}/sendMessage"
                text = f"{title}\n{body}" + (f"\n{url}" if url else "")
                r = await c.post(tg_url, json={"chat_id": settings.telegram_chat_id.strip(), "text": text})
                r.raise_for_status()
                sent_channels.append("telegram")
            except Exception as exc:
                errors["telegram"] = str(exc)

        if settings.bark_push_key.strip():
            try:
                base = (settings.bark_server_url or "https://api.day.app").rstrip("/")
                bark_url = f"{base}/{quote(settings.bark_push_key.strip(), safe='')}/{quote(title, safe='')}/{quote(body, safe='')}"
                params = {"url": url} if url else None
                r = await c.get(bark_url, params=params)
                r.raise_for_status()
                sent_channels.append("bark")
            except Exception as exc:
                errors["bark"] = str(exc)

    if client is not None:
        await _run(client)
    else:
        async with httpx.AsyncClient(timeout=8.0) as c:
            await _run(c)

    return {"ok": bool(sent_channels) and not errors, "sent": sent_channels, "errors": errors}
