"""A deliberately annoying fake laundry portal for testing slotsniper offline.

- SPA: the list renders client-side after a delay, then re-renders every 2s.
- Every render uses fresh random CSS class names (like a minified React build), so
  class selectors break; role/text locators keep working.
- Machines flip between "In use" and "Available" on a timer.
- Optional login wall (--require-login) that sets a session cookie.

    python mock_portal/server.py --port 8765 --flip-every 6
"""
from __future__ import annotations

import argparse
import json
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
MACHINES = {f"{kind} {i}": "In use · 30 min left" for kind, n in [("Washer", 4), ("Dryer", 3)] for i in range(1, n + 1)}

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>WashLife Campus Portal</title>
<style>body{font-family:system-ui;max-width:640px;margin:2rem auto}li{display:flex;gap:1rem;align-items:center;
padding:.6rem;border-bottom:1px solid #ddd}li span:first-child{width:7rem;font-weight:600}li span:nth-child(2){flex:1}
#banner{padding:.6rem;background:#d1fae5;display:none}</style></head><body>
<h1>Machines — West Hall</h1><div id="banner"></div><div id="app">Loading…</div>
<script>
const rid = () => 'c' + Math.random().toString(36).slice(2, 8);
async function render() {
  const data = await (await fetch('/api/machines')).json();
  const ul = document.createElement('ul'); ul.className = rid(); ul.setAttribute('role', 'list');
  for (const [name, status] of Object.entries(data)) {
    const li = document.createElement('li'); li.className = rid();
    const a = document.createElement('span'); a.className = rid(); a.textContent = name;
    const b = document.createElement('span'); b.className = rid(); b.textContent = status;
    const btn = document.createElement('button'); btn.className = rid(); btn.textContent = 'Reserve';
    btn.disabled = status !== 'Available';
    btn.onclick = async () => {
      const r = await (await fetch('/api/reserve/' + encodeURIComponent(name), {method: 'POST'})).json();
      const el = document.getElementById('banner'); el.style.display = 'block';
      el.textContent = r.ok ? 'Reservation confirmed for ' + name : 'Sorry, ' + name + ' was just taken';
      render();
    };
    li.append(a, b, btn); ul.append(li);
  }
  document.getElementById('app').replaceChildren(ul);
}
setTimeout(() => { render(); setInterval(render, 2000); }, 800 + Math.random() * 700);
</script></body></html>"""

LOGIN = """<!doctype html><html><body style="font-family:system-ui;max-width:420px;margin:4rem auto">
<h2>Sign in to continue</h2><form method="post" action="/login"><button>Sign in with Campus SSO</button></form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    require_login = False

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html", headers=()):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        return not self.require_login or "sid=ok" in (self.headers.get("Cookie") or "")

    def do_GET(self):
        if self.path == "/login":
            return self._send(200, LOGIN)
        if not self._authed():
            return self._send(302, "", headers=[("Location", "/login")])
        if self.path == "/api/machines":
            with LOCK:
                return self._send(200, json.dumps(MACHINES), "application/json")
        self._send(200, PAGE)

    def do_POST(self):
        if self.path == "/login":
            return self._send(302, "", headers=[("Set-Cookie", "sid=ok; Path=/"), ("Location", "/")])
        if not self._authed():
            return self._send(401, "{}", "application/json")
        m = re.fullmatch(r"/api/reserve/(.+)", self.path)
        if not m:
            return self._send(404, "{}", "application/json")
        name = m.group(1).replace("%20", " ")
        with LOCK:
            ok = MACHINES.get(name) == "Available"
            if ok:
                MACHINES[name] = "Reserved by you"
        print(f"[portal] reserve {name!r}: {'OK' if ok else 'rejected'}", flush=True)
        self._send(200, json.dumps({"ok": ok}), "application/json")


def flipper(every: float):
    while True:
        time.sleep(every)
        with LOCK:
            busy = [k for k, v in MACHINES.items() if v.startswith("In use")]
            free = [k for k, v in MACHINES.items() if v == "Available"]
            if busy:
                k = random.choice(busy)
                MACHINES[k] = "Available"
                print(f"[portal] {k} -> Available", flush=True)
            if free and random.random() < 0.5:
                k = random.choice(free)
                MACHINES[k] = f"In use · {random.randint(5, 55)} min left"
                print(f"[portal] {k} -> In use", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--flip-every", type=float, default=6)
    ap.add_argument("--require-login", action="store_true")
    a = ap.parse_args()
    Handler.require_login = a.require_login
    threading.Thread(target=flipper, args=(a.flip_every,), daemon=True).start()
    print(f"[portal] http://localhost:{a.port}/  (login wall: {a.require_login})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
