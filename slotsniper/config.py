"""Typed config loaded from YAML. Every knob has a sane default so a target can be a few lines."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class BrowserCfg(BaseModel):
    # "msedge" / "chrome" use an installed browser; null uses Playwright's bundled Chromium.
    channel: str | None = "msedge"
    headless: bool = True
    state_dir: Path = Path(".state")
    viewport_width: int = 1280
    viewport_height: int = 900


class PollCfg(BaseModel):
    every_seconds: float = 30
    jitter_seconds: float = 5
    # Hard floor so a typo can't hammer a campus server.
    min_seconds: float = 10


class SessionCfg(BaseModel):
    # If the page lands somewhere matching these, the login cookie has expired.
    expired_if_url_contains: list[str] = Field(default_factory=lambda: ["/login", "/sso", "/cas/"])
    expired_if_text: str | None = r"(?i)sign in to continue|session (has )?expired"


class ExtractorCfg(BaseModel):
    kind: Literal["dom", "vlm"] = "dom"
    wait_for: str | None = None
    # Locator specs tried in order until one matches; first hit wins. See locators.py.
    items: list[str] = Field(default_factory=lambda: ["css:[data-slot]", "role:listitem", "role:row"])
    label: list[str] = Field(default_factory=lambda: ["css:[data-label]", "css:.name", "css:span", "text"])
    available_if: str = r"(?i)\b(available|free|open|vacant)\b"
    unavailable_if: str | None = r"(?i)\b(in use|full|booked|reserved|closed)\b"
    # When every DOM spec matches nothing (portal redesign), ask the vision model instead.
    fallback_to_vlm: bool = True
    vlm_hint: str = ""


class ActionCfg(BaseModel):
    kind: Literal["notify", "book"] = "notify"
    button: list[str] = Field(default_factory=lambda: [r"role:button:(?i)book|reserve|claim|sign ?up", "css:button"])
    confirm_text: str = r"(?i)confirmed|booked|reserved|success"
    dry_run: bool = True
    max_bookings: int = 1


class TargetCfg(BaseModel):
    name: str
    url: str
    poll: PollCfg = Field(default_factory=PollCfg)
    session: SessionCfg = Field(default_factory=SessionCfg)
    extractor: ExtractorCfg = Field(default_factory=ExtractorCfg)
    label_matches: str | None = None
    action: ActionCfg = Field(default_factory=ActionCfg)


class NotifyCfg(BaseModel):
    console: bool = True
    telegram_bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    telegram_chat_id_env: str = "TELEGRAM_CHAT_ID"
    discord_webhook_env: str = "DISCORD_WEBHOOK_URL"


class VlmCfg(BaseModel):
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"


class Config(BaseModel):
    browser: BrowserCfg = Field(default_factory=BrowserCfg)
    notify: NotifyCfg = Field(default_factory=NotifyCfg)
    vlm: VlmCfg = Field(default_factory=VlmCfg)
    targets: list[TargetCfg]


def load(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
