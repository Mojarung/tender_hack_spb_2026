"""Notification fan-out via ntfy + Apprise.

ntfy delivers a native push to phone/browser (no auth, mobile app exists).
Apprise routes the same message to Telegram, Discord, Slack, email, etc.
Used by SearchOrchestrator and arq tasks for: source-down alerts, captcha
storms, cost-cap exceedance, demo-time annotations.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

import httpx


class Priority(StrEnum):
    MIN = "min"
    LOW = "low"
    DEFAULT = "default"
    HIGH = "high"
    MAX = "max"


class Notifier:
    """Thin async client. Failures here MUST NEVER bubble up — notifications
    are best-effort; a search must not break because Telegram is down."""

    def __init__(
        self,
        ntfy_url: str | None = "http://ntfy/pricepulse-alerts",
        apprise_url: str | None = "http://apprise:8000/notify/pricepulse",
        timeout_s: float = 4.0,
    ) -> None:
        self._ntfy_url = ntfy_url
        self._apprise_url = apprise_url
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(
        self,
        message: str,
        *,
        title: str = "PricePulse",
        priority: Priority = Priority.DEFAULT,
        tags: list[str] | None = None,
        click_url: str | None = None,
    ) -> None:
        await self._ntfy(message, title=title, priority=priority, tags=tags, click_url=click_url)
        await self._apprise(message, title=title, priority=priority)

    async def _ntfy(
        self,
        message: str,
        *,
        title: str,
        priority: Priority,
        tags: list[str] | None,
        click_url: str | None,
    ) -> None:
        if not self._ntfy_url:
            return
        headers: dict[str, str] = {"Title": title, "Priority": priority.value}
        if tags:
            headers["Tags"] = ",".join(tags)
        if click_url:
            headers["Click"] = click_url
        try:
            await self._http.post(self._ntfy_url, content=message.encode("utf-8"), headers=headers)
        except httpx.HTTPError:
            pass

    async def _apprise(self, message: str, *, title: str, priority: Priority) -> None:
        if not self._apprise_url:
            return
        body = {
            "title": title,
            "body": message,
            "type": _apprise_type(priority),
            "format": "text",
        }
        try:
            await self._http.post(self._apprise_url, json=body)
        except httpx.HTTPError:
            pass


def _apprise_type(p: Priority) -> Literal["info", "success", "warning", "failure"]:
    match p:
        case Priority.MAX | Priority.HIGH:
            return "failure"
        case Priority.DEFAULT:
            return "warning"
        case Priority.LOW | Priority.MIN:
            return "info"
