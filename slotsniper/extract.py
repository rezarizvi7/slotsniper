"""Turn a portal page into a list of Slots, via DOM locators or a vision model."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

import anthropic
from playwright.async_api import Page, TimeoutError as PWTimeout

from .config import ExtractorCfg, VlmCfg
from .locators import first_matching, read_field, resolve, rx


@dataclass
class Slot:
    id: str
    label: str
    status: str
    available: bool
    source: str = "dom"
    items_spec: str | None = None  # DOM: the spec that found it, to re-find at booking time
    click_xy: tuple[float, float] | None = None  # VLM: viewport pixel to click to book


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "slot"


def _dedupe(slots: list[Slot]) -> list[Slot]:
    seen: dict[str, int] = {}
    for s in slots:
        n = seen.get(s.id, 0)
        seen[s.id] = n + 1
        if n:
            s.id = f"{s.id}#{n + 1}"
    return slots


async def dom_extract(page: Page, cfg: ExtractorCfg) -> list[Slot] | None:
    """None means no item spec matched at all, i.e. the selectors are probably stale."""
    if cfg.wait_for:
        try:
            await resolve(page, cfg.wait_for).first.wait_for(timeout=15_000)
        except PWTimeout:
            pass
    found = await first_matching(page, cfg.items, wait_ms=10_000)
    if not found:
        return None
    spec, items = found
    avail, unavail = rx(cfg.available_if), rx(cfg.unavailable_if) if cfg.unavailable_if else None
    slots = []
    for i in range(await items.count()):
        item = items.nth(i)
        text = " ".join((await item.inner_text()).split())
        if not text:
            continue
        label = await read_field(item, cfg.label) or text[:60]
        is_open = bool(avail.search(text)) and not (unavail and unavail.search(text))
        slots.append(Slot(slug(label), label, text, is_open, "dom", items_spec=spec))
    return _dedupe(slots)


_VLM_SCHEMA = {
    "type": "object",
    "properties": {
        "slots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "status": {"type": "string"},
                    "available": {"type": "boolean"},
                    "book_x": {"type": ["number", "null"]},
                    "book_y": {"type": ["number", "null"]},
                },
                "required": ["label", "status", "available", "book_x", "book_y"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["slots"],
    "additionalProperties": False,
}

_VLM_PROMPT = """This is a screenshot of a reservation portal (e.g. laundry machines, gym slots, class sections).
List every bookable unit you can see. For each give:
- label: its name as shown (e.g. "Washer 3", "Tue 7:00 PM", "CS 101 Sec 02")
- status: the status text shown for it
- available: true only if it can be booked right now
- book_x, book_y: the centre of its book/reserve control as fractions of image width and height (0-1),
  or null if there is no enabled control.
Report only what is visible; do not guess units that are off screen."""


class VlmExtractor:
    """Grounds UI elements from a screenshot when the DOM is opaque (canvas, iframes, redesigns)."""

    def __init__(self, cfg: VlmCfg):
        self.cfg = cfg
        self.client = anthropic.AsyncAnthropic()

    async def extract(self, page: Page, hint: str = "") -> list[Slot]:
        png = await page.screenshot()
        vp = page.viewport_size or {"width": 1280, "height": 900}
        resp = await self.client.beta.messages.create(
            model=self.cfg.model,
            max_tokens=16_000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.cfg.effort, "format": {"type": "json_schema", "schema": _VLM_SCHEMA}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.standard_b64encode(png).decode()}},
                    {"type": "text", "text": _VLM_PROMPT + (f"\n\nPortal-specific hint: {hint}" if hint else "")},
                ],
            }],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"vision model declined: {resp.stop_details}")
        text = next(b.text for b in resp.content if b.type == "text")
        slots = []
        for s in json.loads(text)["slots"]:
            xy = None
            if s["book_x"] is not None and s["book_y"] is not None:
                xy = (s["book_x"] * vp["width"], s["book_y"] * vp["height"])
            slots.append(Slot(slug(s["label"]), s["label"], s["status"], s["available"], "vlm", click_xy=xy))
        return _dedupe(slots)
