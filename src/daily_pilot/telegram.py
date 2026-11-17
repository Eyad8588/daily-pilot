"""Telegram Bot API sender with a dry-run mode that prints instead of sending."""

from __future__ import annotations

import html
import logging
import sys
from typing import TextIO

import httpx

log = logging.getLogger(__name__)

MAX_LEN = 4096  # Telegram message limit


def esc(text: str) -> str:
    """Escape text for Telegram's HTML parse mode."""
    return html.escape(text or "", quote=False)


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on line boundaries so no chunk exceeds Telegram's limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # a single giant line
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return [c.rstrip("\n") for c in chunks if c.strip()]


class TelegramError(RuntimeError):
    pass


class Telegram:
    """Send HTML-formatted messages to one chat.

    In dry-run mode nothing is sent: messages are printed to `out` and kept in
    `self.sent` (handy for tests).
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        dry_run: bool = True,
        client: httpx.Client | None = None,
        out: TextIO | None = None,
    ) -> None:
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self.client = client or httpx.Client(timeout=30)
        self.out = out or sys.stdout
        self.sent: list[str] = []
        if not dry_run and not (token and chat_id):
            raise TelegramError(
                "Live mode needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env "
                "(run `daily-pilot telegram-chat-id` after messaging your bot)."
            )

    @property
    def api(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def send(self, text: str, silent: bool = False) -> None:
        for chunk in split_message(text):
            self.sent.append(chunk)
            if self.dry_run:
                print("\n----- [dry-run] Telegram message -----", file=self.out)
                print(chunk, file=self.out)
                print("--------------------------------------", file=self.out)
                continue
            r = self.client.post(
                f"{self.api}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "disable_notification": silent,
                },
            )
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code != 200 or not data.get("ok"):
                raise TelegramError(f"Telegram sendMessage failed: {r.status_code} {data.get('description', r.text)}")

    def discover_chat_ids(self) -> list[tuple[str, str]]:
        """Return (chat_id, name) pairs from recent updates (message your bot first)."""
        if not self.token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        r = self.client.get(f"{self.api}/getUpdates")
        data = r.json()
        if not data.get("ok"):
            raise TelegramError(f"getUpdates failed: {data.get('description')}")
        seen: dict[str, str] = {}
        for upd in data.get("result", []):
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat = msg.get("chat") or {}
            if "id" in chat:
                name = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
                seen[str(chat["id"])] = name
        return list(seen.items())


def make_telegram(settings) -> Telegram:  # noqa: ANN001
    return Telegram(settings.telegram_bot_token, settings.telegram_chat_id, dry_run=settings.dry_run)
