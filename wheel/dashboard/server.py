"""
server.py - the wheel control panel. Python standard library only.

    python3 wheel/dashboard/server.py                   # staging, http://127.0.0.1:8090
    WHEEL_MODE=live python3 wheel/dashboard/server.py   # the account read by `python3 -m wheel.agent read`

It never contacts a broker. Approving an order writes it to the outbox; `python3 -m wheel.agent send`
is what passes outbox orders to the broker, and only when WHEEL_SEND=1.

Access: binds to 127.0.0.1. To listen on another address (for example on a server reached over a
private network) set WHEEL_HOST and WHEEL_TOKEN; without a token it refuses to leave localhost.
Every request must then carry the token (?token=... once, which sets a cookie, or X-Token).
"""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core  # noqa: E402
import scenarios  # noqa: E402

MODE = os.environ.get("WHEEL_MODE", "staging")
HOST = os.environ.get("WHEEL_HOST", "127.0.0.1")
PORT = int(os.environ.get("WHEEL_PORT", "8090"))
TOKEN = os.environ.get("WHEEL_TOKEN", "")


def act(path, b):
    if path == "/api/decide":
        return core.decide(MODE, b.get("id"), b.get("decision"), b.get("note", ""), b.get("edits"), bool(b.get("override")))
    if path == "/api/manual":
        return core.manual(MODE, b.get("leg") or {}, b.get("note", ""), bool(b.get("override")))
    if path == "/api/check":
        leg = dict(b.get("leg") or {})
        leg["symbol"] = str(leg.get("symbol", "")).upper()
        return {"ok": True, **core.check_order(MODE, core.load_snapshot(MODE), core.rules_for(core.load_settings(MODE)), leg)}
    if path == "/api/cancel":
        return core.cancel(MODE, b.get("order_id"))
    if path == "/api/settings":
        return core.change_settings(MODE, b.get("changes") or {}, b.get("note", ""))
    if path == "/api/preview":
        return core.preview(MODE, b.get("changes") or {})
    if path == "/api/rerun":
        core.record_run(MODE, "you pressed run")
        return {"ok": True}
    if path == "/api/fill":
        return core.simulate_fill(MODE, b.get("order_id"))
    if path == "/api/expiry":
        return core.simulate_expiry(MODE, b.get("symbol"), b.get("strike"), b.get("expiry"), b.get("outcome"))
    if path == "/api/goals":
        return core.set_goals(MODE, b.get("text", ""))
    if path == "/api/note":
        return core.add_note(MODE, b.get("text", ""), "you")
    if path == "/api/suggest":
        return core.add_suggestion(MODE, b.get("title"), b.get("why"), b.get("change"), "you")
    if path == "/api/suggestion":
        return core.suggestion_decide(MODE, b.get("id"), b.get("decision"), b.get("note", ""), "you")
    if path == "/api/scenario":
        if MODE != "staging":
            return {"ok": False, "error": "Scenarios exist only in staging."}
        return scenarios.load(b.get("name"))
    return None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _authed(self, q):
        if not TOKEN:
            return True, {}
        got = self.headers.get("X-Token") or (q.get("token") or [""])[0]
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if part.strip().startswith("wheel_token="):
                got = got or part.strip().split("=", 1)[1]
        ok = hmac.compare_digest(got, TOKEN)
        return ok, ({"Set-Cookie": f"wheel_token={TOKEN}; HttpOnly; SameSite=Strict; Path=/"} if ok else {})

    def do_GET(self):
        u = urlparse(self.path)
        ok, extra = self._authed(parse_qs(u.query))
        if not ok:
            return self._send(401, {"ok": False, "error": "token required"})
        if u.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8", extra)
        if u.path == "/api/state":
            st = core.full_state(MODE)
            st["scenarios"] = [{"name": k, "label": v[0]} for k, v in scenarios.SCENARIOS.items()] if MODE == "staging" else []
            return self._send(200, st)
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        ok, _ = self._authed(parse_qs(u.query))
        if not ok:
            return self._send(401, {"ok": False, "error": "token required"})
        if self.headers.get("X-Wheel") != "1":             # a plain cross-site form cannot set this header
            return self._send(400, {"ok": False, "error": "missing X-Wheel header"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send(400, {"ok": False, "error": "bad JSON"})
        try:
            out = act(u.path, body)
        except Exception as e:                              # never leave the page hanging
            return self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        self._send(404 if out is None else 200, out or {"ok": False, "error": "not found"})


if __name__ == "__main__":
    if HOST not in ("127.0.0.1", "localhost", "::1") and not TOKEN:
        sys.exit("Refusing to listen beyond localhost without WHEEL_TOKEN set.")
    if MODE == "staging" and core.load_snapshot("staging") is None:
        scenarios.load("real")
    print(f"wheel control panel ({MODE}) on http://{HOST}:{PORT}  broker: {core.config.BROKER}  sending: {'ON' if core.SEND_ENABLED and MODE == 'live' else 'OFF'}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
