"""Fan a message (and optional screenshot) out to console, Telegram and Discord."""
from __future__ import annotations

import logging
import os

import httpx

from .config import NotifyCfg

log = logging.getLogger("slotsniper.notify")


class Notifier:
    def __init__(self, cfg: NotifyCfg):
        self.console = cfg.console
        self.tg_token = os.environ.get(cfg.telegram_bot_token_env)
        self.tg_chat = os.environ.get(cfg.telegram_chat_id_env)
        self.discord = os.environ.get(cfg.discord_webhook_env)
        self.http = httpx.AsyncClient(timeout=15)

    @property
    def channels(self) -> list[str]:
        return [n for n, on in [("console", self.console), ("telegram", self.tg_token and self.tg_chat),
                                ("discord", self.discord)] if on]

    async def send(self, text: str, image: bytes | None = None) -> None:
        if self.console:
            print(f"\n🔔 {text}\n", flush=True)
        if self.tg_token and self.tg_chat:
            await self._safe(self._telegram, text, image)
        if self.discord:
            await self._safe(self._discord, text, image)

    async def _safe(self, fn, text, image):
        # A flaky notifier must never kill the watch loop.
        try:
            await fn(text, image)
        except Exception as e:
            log.warning("%s failed: %s", fn.__name__, e)

    async def _telegram(self, text: str, image: bytes | None) -> None:
        base = f"https://api.telegram.org/bot{self.tg_token}"
        if image:
            r = await self.http.post(f"{base}/sendPhoto", data={"chat_id": self.tg_chat, "caption": text[:1024]},
                                     files={"photo": ("shot.png", image, "image/png")})
        else:
            r = await self.http.post(f"{base}/sendMessage", json={"chat_id": self.tg_chat, "text": text})
        r.raise_for_status()

    async def _discord(self, text: str, image: bytes | None) -> None:
        if image:
            r = await self.http.post(self.discord, data={"content": text[:2000]},
                                     files={"file": ("shot.png", image, "image/png")})
        else:
            r = await self.http.post(self.discord, json={"content": text[:2000]})
        r.raise_for_status()

    async def close(self) -> None:
        await self.http.aclose()
