# -*- coding: utf-8 -*-
"""Async alert delivery to a Telegram or Discord webhook.

Set ``SAHILOPS_WEBHOOK_URL`` to a Discord webhook URL or a Telegram bot
``sendMessage`` URL; for Telegram also set ``SAHILOPS_TELEGRAM_CHAT_ID``. When no
URL is configured every call is a cheap no-op, so the bot runs unchanged.

Alerts are queued and sent by one background worker, so a slow or dead webhook
never blocks a match, a login, or shutdown. Message text is scrubbed of
token-shaped strings before it leaves the process.
"""

import asyncio
import os
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import httpx

WEBHOOK_URL = os.environ.get("SAHILOPS_WEBHOOK_URL", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("SAHILOPS_TELEGRAM_CHAT_ID", "").strip()
WEBHOOK_TIMEOUT = float(os.environ.get("SAHILOPS_WEBHOOK_TIMEOUT", "5") or 5)
MIN_INTERVAL = float(os.environ.get("SAHILOPS_WEBHOOK_MIN_INTERVAL", "1.0") or 0)
QUEUE_SIZE = 200

EVENT_LABELS = {
    "level_up": "LEVEL UP",
    "match_complete": "MATCH COMPLETE",
    "session_error": "SESSION ERROR",
    "team_failure": "TEAM FORMATION FAILED",
    "suspended": "ACCOUNT SUSPENDED",
    "quarantine": "ACCOUNT QUARANTINED",
    "account_error": "ACCOUNT ERROR",
    "startup": "BOT STARTED",
}

_SECRET_PATTERNS = [
    (re.compile(r"eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{2,}"),
     "[REDACTED_TOKEN]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api[_\-]?key|jwt|proxy)\b"
                r"\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
]


def scrub(text: str) -> str:
    value = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def format_alert(event: str, message: str, uid: Optional[str] = None, **fields: Any) -> str:
    label = EVENT_LABELS.get(str(event), str(event).replace("_", " ").upper())
    parts = [f"[SahilOps] {label}"]
    if uid:
        parts.append(f"account {uid}")
    text = " | ".join(parts)
    if message:
        text += f"\n{message}"
    for key, value in fields.items():
        if value in (None, ""):
            continue
        text += f"\n{str(key).replace('_', ' ').title()}: {value}"
    text += f"\nTime: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    return scrub(text)


def classify_webhook(url: str) -> str:
    """Return ``telegram``, ``discord`` or ``generic`` for a webhook URL."""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "generic"
    host = (parts.hostname or "").lower()
    if host.endswith("telegram.org"):
        return "telegram"
    if host in ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"):
        return "discord"
    return "generic"


def build_payload(url: str, text: str, chat_id: Optional[str] = None,
                  event: Optional[str] = None) -> Dict[str, Any]:
    kind = classify_webhook(url)
    if kind == "telegram":
        return {
            "chat_id": chat_id or "",
            "text": text,
            "disable_web_page_preview": True,
        }
    if kind == "discord":
        return {"content": text[:1900], "username": "SahilOps"}
    return {"event": event or "alert", "text": text}


class Notifier:
    """Queue + worker that fans alerts out to one webhook endpoint."""

    def __init__(self, url: Optional[str] = None, chat_id: Optional[str] = None,
                 timeout: float = WEBHOOK_TIMEOUT, min_interval: float = MIN_INTERVAL):
        self.url = str(url if url is not None else WEBHOOK_URL).strip()
        self.chat_id = str(chat_id if chat_id is not None else TELEGRAM_CHAT_ID).strip()
        self.timeout = timeout
        self.min_interval = max(0.0, min_interval)
        self.last_error: Optional[str] = None
        self.sent_count = 0
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._task: Optional[asyncio.Task] = None

    @property
    def kind(self) -> str:
        return classify_webhook(self.url) if self.url else ""

    @property
    def enabled(self) -> bool:
        if not self.url:
            return False
        return self.kind != "telegram" or bool(self.chat_id)

    def _ensure_worker(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker(), name="sahilops-webhook")

    async def notify(self, event: str, message: str = "", uid: Optional[str] = None,
                     **fields: Any) -> bool:
        """Queue an alert. Never raises and never blocks on the network."""
        if not self.enabled:
            return False
        text = format_alert(event, message, uid=uid, **fields)
        if self._task is None or self._task.done():
            self._ensure_worker()
        try:
            self._queue.put_nowait((event, text))
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except Exception:
                pass
            try:
                self._queue.put_nowait((event, text))
            except asyncio.QueueFull:
                return False
        return True

    async def _worker(self) -> None:
        try:
            while True:
                event, text = await self._queue.get()
                try:
                    ok = await self._deliver(text, event)
                    if not ok:
                        await asyncio.sleep(2.0)
                        await self._deliver(text, event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                finally:
                    self._queue.task_done()
                if self.min_interval:
                    await asyncio.sleep(self.min_interval)
        except asyncio.CancelledError:
            raise

    async def _deliver(self, text: str, event: Optional[str] = None) -> bool:
        payload = build_payload(self.url, text, self.chat_id, event)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                res = await client.post(self.url, json=payload)
            if res.status_code >= 400:
                self.last_error = f"webhook HTTP {res.status_code}"
                return False
            self.sent_count += 1
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    async def flush(self, timeout: float = 10.0) -> None:
        """Wait for queued alerts to finish sending (tests and shutdown)."""
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except (asyncio.TimeoutError, RuntimeError):
            pass

    async def close(self, timeout: float = 5.0) -> None:
        if self._task is None:
            return
        await self.flush(timeout=timeout)
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None


notifier = Notifier()


async def alert(event: str, message: str = "", uid: Optional[str] = None, **fields: Any) -> bool:
    """Module-level convenience wrapper around the shared notifier."""
    return await notifier.notify(event, message, uid=uid, **fields)
