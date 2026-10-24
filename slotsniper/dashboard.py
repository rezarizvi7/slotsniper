"""Local web dashboard: start/stop watchers, see live slots, alerts and screenshots, edit settings.

Binds to 127.0.0.1 only. It can trigger real bookings, so it must not be reachable from the network.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import yaml
from aiohttp import web
from playwright.async_api import BrowserContext, Playwright, async_playwright

from .config import Config, TargetCfg, load
from .notify import Notifier
from .watcher import TargetWatcher, make_vlm, open_context

log = logging.getLogger("slotsniper.dashboard")
PAGE = Path(__file__).parent / "web" / "index.html"


class Dashboard:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.cfg: Config = load(config_path)
        self.notifier = Notifier(self.cfg.notify)
        self.vlm = make_vlm(self.cfg)
        self.pw: Playwright | None = None
        self.watchers: dict[str, TargetWatcher] = {}  # kept after stop so the last results stay visible
        self.tasks: dict[str, asyncio.Task] = {}
        self.starting: set[str] = set()
        self.start_errors: dict[str, str] = {}
        self.login_windows: dict[str, BrowserContext] = {}
        self.resume_after_login: set[str] = set()

    def target(self, name: str) -> TargetCfg:
        for t in self.cfg.targets:
            if t.name == name:
                return t
        raise web.HTTPNotFound(text=f"no target {name!r}")

    # ---- lifecycle -------------------------------------------------------

    async def start(self, name: str) -> None:
        if name in self.tasks or name in self.starting or name in self.login_windows:
            return
        t = self.target(name)
        self.starting.add(name)
        self.start_errors.pop(name, None)
        try:
            ctx = await open_context(self.pw, self.cfg, t)
        except Exception as e:
            self.start_errors[name] = f"browser failed to start: {str(e).splitlines()[0]}"
            return
        finally:
            self.starting.discard(name)
        w = TargetWatcher(self.cfg, t, self.notifier, self.vlm)
        self.watchers[name] = w
        self.tasks[name] = asyncio.create_task(self._run(name, w, ctx))

    async def _run(self, name: str, w: TargetWatcher, ctx: BrowserContext) -> None:
        try:
            await w.run(ctx)
        finally:
            self.tasks.pop(name, None)
            await ctx.close()

    async def stop(self, name: str) -> None:
        task = self.tasks.get(name)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def open_login(self, name: str) -> None:
        # A visible browser on the same profile the watcher uses: the human signs in
        # (SSO, Duo, whatever) and the cookies stay in the profile for headless polling.
        if name in self.login_windows:
            return
        was_running = name in self.tasks
        await self.stop(name)  # a profile can only be open in one browser at a time
        t = self.target(name)
        ctx = await open_context(self.pw, self.cfg, t, headless=False)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(t.url)
        self.login_windows[name] = ctx
        ctx.on("close", lambda _: self.login_windows.pop(name, None))
        if was_running:
            self.resume_after_login.add(name)

    async def finish_login(self, name: str) -> None:
        ctx = self.login_windows.pop(name, None)
        if ctx:
            await ctx.close()
        if name in self.resume_after_login:
            self.resume_after_login.discard(name)
            await self.start(name)

    # ---- views -----------------------------------------------------------

    def target_status(self, t: TargetCfg) -> dict:
        w = self.watchers.get(t.name)
        running = t.name in self.tasks
        if t.name in self.login_windows:
            status = "logging in"
        elif t.name in self.starting:
            status = "starting"
        elif t.name in self.start_errors:
            status = "error"
        elif not running:
            status = "stopped"
        elif w and w.session_expired:
            status = "login needed"
        elif w and w.last_error:
            status = "error"
        elif w and w.last_poll is None:
            status = "starting"
        else:
            status = "watching"
        return {
            "name": t.name,
            "url": t.url,
            "status": status,
            "running": running,
            "error": self.start_errors.get(t.name) or (w.last_error if w else None),
            "last_poll": w.last_poll if w else None,
            "has_shot": bool(w and w.last_shot),
            "booked": w.state.booked if w else [],
            "slots": [{"id": s.id, "label": s.label, "status": s.status, "available": s.available, "source": s.source}
                      for s in (w.slots if w else [])],
            "settings": {
                "url": t.url,
                "every_seconds": t.poll.every_seconds,
                "label_matches": t.label_matches or "",
                "action": t.action.kind,
                "dry_run": t.action.dry_run,
                "max_bookings": t.action.max_bookings,
            },
        }

    def status(self) -> dict:
        return {
            "targets": [self.target_status(t) for t in self.cfg.targets],
            "channels": self.notifier.channels,
            "vision": self.vlm is not None,
            "alerts": list(reversed(self.notifier.history))[:50],
        }

    def save_settings(self, name: str, s: dict) -> None:
        t = self.target(name)
        updated = t.model_copy(deep=True)
        updated.url = str(s.get("url") or t.url).strip()
        updated.poll.every_seconds = max(float(s.get("every_seconds") or t.poll.every_seconds), t.poll.min_seconds)
        updated.label_matches = (s.get("label_matches") or "").strip() or None
        updated.action.kind = "book" if s.get("action") == "book" else "notify"
        updated.action.dry_run = bool(s.get("dry_run", True))
        updated.action.max_bookings = max(1, int(s.get("max_bookings") or 1))
        TargetCfg.model_validate(updated.model_dump())  # reject anything the schema wouldn't load
        self.cfg.targets = [updated if x.name == name else x for x in self.cfg.targets]
        data = self.cfg.model_dump(mode="json", exclude_defaults=True)
        self.config_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def build_app(d: Dashboard) -> web.Application:
    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(_):
        return web.FileResponse(PAGE)

    @routes.get("/api/status")
    async def status(_):
        return web.json_response(d.status())

    @routes.post("/api/targets/{name}/start")
    async def start(req):
        await d.start(req.match_info["name"])
        return web.json_response({"ok": True})

    @routes.post("/api/targets/{name}/stop")
    async def stop(req):
        await d.stop(req.match_info["name"])
        return web.json_response({"ok": True})

    @routes.post("/api/targets/{name}/login")
    async def login(req):
        try:
            await d.open_login(req.match_info["name"])
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e).splitlines()[0]}, status=500)
        return web.json_response({"ok": True})

    @routes.post("/api/targets/{name}/login-done")
    async def login_done(req):
        await d.finish_login(req.match_info["name"])
        return web.json_response({"ok": True})

    @routes.post("/api/targets/{name}/check")
    async def check(req):
        w = d.watchers.get(req.match_info["name"])
        if w and req.match_info["name"] in d.tasks:
            w.wake.set()
        return web.json_response({"ok": True})

    @routes.post("/api/targets/{name}/settings")
    async def settings(req):
        name = req.match_info["name"]
        try:
            d.save_settings(name, await req.json())
        except (ValueError, TypeError) as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        if name in d.tasks:  # restart so the new settings take effect
            await d.stop(name)
            await d.start(name)
        return web.json_response({"ok": True})

    @routes.post("/api/test-alert")
    async def test_alert(_):
        await d.notifier.send("slotsniper test notification 👋")
        return web.json_response({"ok": True})

    @routes.get("/api/targets/{name}/screenshot")
    async def screenshot(req):
        w = d.watchers.get(req.match_info["name"])
        if not (w and w.last_shot):
            raise web.HTTPNotFound()
        return web.Response(body=w.last_shot, content_type="image/png", headers={"Cache-Control": "no-store"})

    @web.middleware
    async def same_origin_only(req, handler):
        # Blocks other websites in your browser from POSTing "book now" to the dashboard.
        if req.method == "POST" and req.headers.get("Origin") not in (None, f"http://{req.host}"):
            raise web.HTTPForbidden()
        return await handler(req)

    app = web.Application(middlewares=[same_origin_only])
    app.add_routes(routes)
    return app


async def serve(config_path: Path, port: int, open_browser: bool, start_all: bool) -> None:
    d = Dashboard(config_path)
    async with async_playwright() as pw:
        d.pw = pw
        runner = web.AppRunner(build_app(d), access_log=None)  # the UI polls every 1.5s; don't spam the console
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        url = f"http://127.0.0.1:{port}/"
        print(f"slotsniper dashboard running at {url}  (Ctrl+C to stop)", flush=True)
        if start_all:
            for t in d.cfg.targets:
                await d.start(t.name)
        if open_browser and not os.environ.get("SLOTSNIPER_NO_BROWSER"):
            import webbrowser
            webbrowser.open(url)
        try:
            await asyncio.Event().wait()
        finally:
            for name in list(d.tasks):
                await d.stop(name)
            for name in list(d.login_windows):
                await d.finish_login(name)
            await runner.cleanup()
            await d.notifier.close()
