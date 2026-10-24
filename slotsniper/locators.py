"""Locator specs: short strings that describe *what* an element is rather than *where* it sits.

    css:SELECTOR          raw CSS (brittle; keep as a first, fast guess)
    text:REGEX            any element whose text matches
    role:ROLE             ARIA role, e.g. role:listitem
    role:ROLE:REGEX       ARIA role with accessible name, e.g. role:button:(?i)reserve
    label:REGEX           form control by its <label>
    testid:ID             data-testid
    attr:NAME             (fields only) an attribute of the item itself
    text                  (fields only) the item's whole visible text

Role/text specs survive the class-name churn of React/Angular builds, which is the
usual reason portal scrapers break. A list of specs is tried in order.
"""
from __future__ import annotations

import asyncio
import re
import time

from playwright.async_api import Locator, Page


def rx(pattern: str) -> re.Pattern[str]:
    # Playwright ships patterns to the browser as JS regexes, which reject inline
    # "(?i)", so turn a leading one into a real flag.
    flags = 0
    if pattern.startswith("(?i)"):
        pattern, flags = pattern[4:], re.IGNORECASE
    return re.compile(pattern, flags)


def resolve(root: Page | Locator, spec: str) -> Locator:
    kind, _, rest = spec.partition(":")
    if kind == "css":
        return root.locator(rest)
    if kind == "text":
        return root.get_by_text(rx(rest))
    if kind == "role":
        role, _, name = rest.partition(":")
        return root.get_by_role(role, name=rx(name)) if name else root.get_by_role(role)
    if kind == "label":
        return root.get_by_label(rx(rest))
    if kind == "testid":
        return root.get_by_test_id(rest)
    raise ValueError(f"unknown locator spec {spec!r}")


async def first_matching(root: Page | Locator, specs: list[str], wait_ms: int = 0) -> tuple[str, Locator] | None:
    """Return the first spec with at least one match, polling up to wait_ms for late SPA renders."""
    deadline = time.monotonic() + wait_ms / 1000
    while True:
        for spec in specs:
            loc = resolve(root, spec)
            if await loc.count() > 0:
                return spec, loc
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.25)


async def read_field(item: Locator, specs: list[str]) -> str | None:
    for spec in specs:
        if spec == "text":
            value = await item.inner_text()
        elif spec.startswith("attr:"):
            value = await item.get_attribute(spec[5:])
        else:
            loc = resolve(item, spec)
            value = await loc.first.inner_text() if await loc.count() else None
        if value and value.strip():
            return " ".join(value.split())
    return None
