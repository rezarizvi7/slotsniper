"""CLI: python -m slotsniper {dashboard,watch,once,probe,login,test-notify} -c config.yaml"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from playwright.async_api import async_playwright

from .config import load
from .dashboard import serve
from .notify import Notifier
from .watcher import TargetWatcher, make_vlm, open_context, select


async def cmd_watch(args, once: bool = False, probe: bool = False) -> None:
    cfg = load(args.config)
    notifier = Notifier(cfg.notify)
    vlm = make_vlm(cfg)
    logging.info("notify via: %s | vision fallback: %s", ", ".join(notifier.channels), "on" if vlm else "off")
    async with async_playwright() as pw:
        async def one(target):
            if probe:  # read-only: never book, never touch saved state
                target.action.kind = "notify"
            ctx = await open_context(pw, cfg, target)
            try:
                w = TargetWatcher(cfg, target, notifier, vlm)
                if probe:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                    await page.goto(target.url, wait_until="domcontentloaded")
                    for s in await w.extract(page):
                        print(f"  {'OPEN ' if s.available else 'taken'}  {s.id:<24} {s.status}  [{s.source}]")
                else:
                    await w.run(ctx, once=once)
            finally:
                await ctx.close()
        await asyncio.gather(*(one(t) for t in select(cfg, args.target)))
    await notifier.close()


async def cmd_login(args) -> None:
    # A human does the login (SSO, Duo/2FA, whatever). We never see or store the password;
    # the browser profile keeps the resulting cookies for the headless watcher.
    cfg = load(args.config)
    target = select(cfg, args.target)[0]
    async with async_playwright() as pw:
        ctx = await open_context(pw, cfg, target, headless=False)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(target.url)
        await asyncio.to_thread(input, f"Log in to {target.name} in the browser window, then press Enter here... ")
        await ctx.close()
    print(f"Session saved to {cfg.browser.state_dir / 'profiles' / target.name}")


async def cmd_test_notify(args) -> None:
    n = Notifier(load(args.config).notify)
    await n.send("slotsniper test notification 👋")
    await n.close()
    print("sent via:", ", ".join(n.channels))


def load_dotenv(path: Path = Path(".env")) -> None:
    # Lets non-programmers put their Telegram/Discord/Anthropic keys in a file instead of
    # setting environment variables. Real env vars win over the file.
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#") and value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="slotsniper")
    p.add_argument("command", choices=["dashboard", "watch", "once", "probe", "login", "test-notify"])
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("-t", "--target", help="only this target (default: all)")
    p.add_argument("--port", type=int, default=8000, help="dashboard port")
    p.add_argument("--no-open", action="store_true", help="don't open the dashboard in a browser")
    p.add_argument("--start-all", action="store_true", help="dashboard: start every target right away")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run = {
        "dashboard": lambda: serve(Path(args.config), args.port, not args.no_open, args.start_all),
        "watch": lambda: cmd_watch(args),
        "once": lambda: cmd_watch(args, once=True),
        "probe": lambda: cmd_watch(args, probe=True),
        "login": lambda: cmd_login(args),
        "test-notify": lambda: cmd_test_notify(args),
    }[args.command]
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
