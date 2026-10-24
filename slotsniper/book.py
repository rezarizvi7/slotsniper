"""Click the book/reserve control for a slot and verify the portal confirmed it."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from playwright.async_api import Page

from .config import ActionCfg, ExtractorCfg
from .extract import Slot
from .locators import first_matching, read_field, resolve, rx


@dataclass
class BookResult:
    ok: bool
    detail: str
    screenshot: bytes | None = None


async def _find_item(page: Page, slot: Slot, ex: ExtractorCfg):
    # Re-find by label, not index: SPAs re-render and reorder between extract and click.
    items = resolve(page, slot.items_spec)
    for i in range(await items.count()):
        item = items.nth(i)
        if (await read_field(item, ex.label)) == slot.label:
            return item
    return None


async def _confirmed(page: Page, pattern: str, timeout_s: float = 10) -> bool:
    want = rx(pattern)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if want.search(await page.locator("body").inner_text()):
            return True
        await asyncio.sleep(0.3)
    return False


async def book(page: Page, slot: Slot, act: ActionCfg, ex: ExtractorCfg) -> BookResult:
    if slot.source == "dom":
        item = await _find_item(page, slot, ex)
        if item is None:
            return BookResult(False, "slot vanished before we could click it")
        found = await first_matching(item, act.button, wait_ms=2_000)
        if not found:
            return BookResult(False, "no book/reserve control on the slot")
        button = found[1].first
        if not await button.is_enabled():
            return BookResult(False, "book control is disabled (someone beat us to it?)")
        if act.dry_run:
            return BookResult(True, f"DRY RUN: would click {found[0]!r}", await page.screenshot())
        await button.click()
    else:
        if not slot.click_xy:
            return BookResult(False, "vision model found no book control for this slot")
        if act.dry_run:
            return BookResult(True, f"DRY RUN: would click at {tuple(round(v) for v in slot.click_xy)}",
                              await page.screenshot())
        await page.mouse.click(*slot.click_xy)

    ok = await _confirmed(page, act.confirm_text)
    return BookResult(ok, "portal confirmed the booking" if ok else "clicked, but saw no confirmation text",
                      await page.screenshot())
