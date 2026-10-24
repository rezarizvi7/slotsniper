"""The poll loop: load page -> check session -> extract -> diff -> notify/book -> sleep."""
from __future__ import annotations

import asyncio
import logging
import random

from playwright.async_api import BrowserContext, Page, Playwright

from .book import book
from .config import Config, TargetCfg
from .extract import Slot, VlmExtractor, dom_extract
from .locators import rx
from .notify import Notifier
from .state import State

log = logging.getLogger("slotsniper")


async def open_context(pw: Playwright, cfg: Config, target: TargetCfg, headless: bool | None = None) -> BrowserContext:
    # One persistent profile per target keeps cookies/SSO tokens across runs, so a
    # human logs in once (`slotsniper login`) and the watcher reuses that session.
    # Keep state_dir short on Windows: Chromium aborts if profile paths pass MAX_PATH.
    b = cfg.browser
    return await pw.chromium.launch_persistent_context(
        user_data_dir=str((b.state_dir / "profiles" / target.name).resolve()),
        channel=b.channel,
        headless=b.headless if headless is None else headless,
        viewport={"width": b.viewport_width, "height": b.viewport_height},
        timeout=60_000,
    )


async def session_expired(page: Page, target: TargetCfg) -> bool:
    if any(frag in page.url for frag in target.session.expired_if_url_contains):
        return True
    if target.session.expired_if_text:
        return bool(rx(target.session.expired_if_text).search(await page.locator("body").inner_text()))
    return False


class TargetWatcher:
    def __init__(self, cfg: Config, target: TargetCfg, notifier: Notifier, vlm: VlmExtractor | None):
        self.cfg, self.t, self.notify, self.vlm = cfg, target, notifier, vlm
        self.state = State(cfg.browser.state_dir / f"{target.name}.json")
        self.failures = 0
        self.warned: set[str] = set()  # one-shot warnings, so a broken portal doesn't spam your phone

    async def _warn_once(self, key: str, text: str) -> None:
        if key not in self.warned:
            self.warned.add(key)
            await self.notify.send(f"[{self.t.name}] {text}")

    async def extract(self, page: Page) -> list[Slot]:
        ex = self.t.extractor
        slots = await dom_extract(page, ex) if ex.kind == "dom" else None
        if slots is None and ex.kind == "dom":
            if not (ex.fallback_to_vlm and self.vlm):
                raise RuntimeError("no item locator matched; the portal layout probably changed")
            await self._warn_once("dom-broke", "⚠️ DOM selectors matched nothing; falling back to vision model.")
        if slots is None:
            if not self.vlm:
                raise RuntimeError("vision extractor needs Anthropic credentials (ANTHROPIC_API_KEY)")
            slots = await self.vlm.extract(page, ex.vlm_hint)
        else:
            self.warned.discard("dom-broke")
        if self.t.label_matches:
            want = rx(self.t.label_matches)
            slots = [s for s in slots if want.search(s.label)]
        return slots

    async def poll(self, page: Page) -> list[Slot]:
        await page.goto(self.t.url, wait_until="domcontentloaded", timeout=30_000)
        if await session_expired(page, self.t):
            await self._warn_once("session", f"🔒 Session expired. Run: python -m slotsniper login --target {self.t.name}")
            return []
        self.warned.discard("session")

        slots = await self.extract(page)
        fresh = self.state.newly_open(slots)
        log.info("[%s] %d slots, %d open, %d newly open", self.t.name, len(slots),
                 sum(s.available for s in slots), len(fresh))
        for slot in fresh:
            await self.on_open(page, slot)
        self.state.save()
        return slots

    async def on_open(self, page: Page, slot: Slot) -> None:
        act = self.t.action
        msg = f"[{self.t.name}] ✅ {slot.label} just opened ({slot.status})\n{self.t.url}"
        if act.kind != "book" or len(self.state.booked) >= act.max_bookings:
            await self.notify.send(msg, await page.screenshot())
            return
        result = await book(page, slot, act, self.t.extractor)
        if result.ok and not act.dry_run:
            self.state.booked.append(slot.id)
        await self.notify.send(f"{msg}\n🤖 Booking: {result.detail}", result.screenshot)

    def next_delay(self) -> float:
        p = self.t.poll
        base = max(p.min_seconds, p.every_seconds + random.uniform(-p.jitter_seconds, p.jitter_seconds))
        return base * min(2 ** self.failures, 16)  # exponential backoff on repeated errors

    async def run(self, ctx: BrowserContext, once: bool = False) -> None:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        while True:
            try:
                await self.poll(page)
                self.failures = 0
            except Exception as e:
                self.failures += 1
                log.warning("[%s] poll failed (%d in a row): %s", self.t.name, self.failures, e)
                if self.failures == 3:
                    await self.notify.send(f"[{self.t.name}] ❌ 3 polls failed in a row; backing off. Last error: {e}")
            if once:
                return
            await asyncio.sleep(self.next_delay())


def make_vlm(cfg: Config) -> VlmExtractor | None:
    needed = any(t.extractor.kind == "vlm" or t.extractor.fallback_to_vlm for t in cfg.targets)
    if not needed:
        return None
    try:
        return VlmExtractor(cfg.vlm)
    except Exception as e:  # no credentials configured
        log.info("vision extractor disabled: %s", e)
        return None


def select(cfg: Config, name: str | None) -> list[TargetCfg]:
    targets = [t for t in cfg.targets if name is None or t.name == name]
    if not targets:
        raise SystemExit(f"no target named {name!r}; have: {', '.join(t.name for t in cfg.targets)}")
    return targets
