import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import view
from match import Match
from topology import LEFT, RIGHT

STATIC = os.path.dirname(__file__)


class Game:
    def __init__(self, mode="pvp"):
        self.lock = threading.Lock()
        self.mode = mode
        self.match = Match(mode=mode)

    def reset(self):
        with self.lock:
            self.match = Match(mode=self.mode)

    def state(self, side):
        with self.lock:
            self.match.mark_seen(side)
            return view.state_for(self.match, side)

    def command(self, side, body):
        cmd = body.get("cmd")
        with self.lock:
            m = self.match
            if cmd == "draft":
                return m.draft_pick(side, body["hero"])
            if cmd == "place":
                return m.place(side, body["hero"], body["cell"])
            if cmd == "unplace":
                return m.unplace(side, body["cell"])
            if cmd == "lock":
                return m.lock_force(side)
            if cmd == "opening":
                return m.opening_choose(side, body)
            if cmd == "select":
                return m.select_hero(side, body["entity"])
            if cmd == "deselect":
                return m.deselect(side)
            if cmd == "commit":
                return m.commit(side, body.get("payload") or {})
            if cmd == "victim":
                return m.choose_victim(side, body["entity"])
            return "Unknown command."


GAME = Game()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Show who is connecting, so you can tell whether a remote seat's
        # requests are actually reaching this machine.
        print(f"[{self.address_string()}] {fmt % args}")

    def _send(self, code, payload, ctype="application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            with open(os.path.join(STATIC, "index.html"), "rb") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")
        if url.path.startswith("/image/"):
            fname = os.path.basename(url.path)  # basename blocks path traversal
            fpath = os.path.join(STATIC, "image", fname)
            if fname.lower().endswith(".png") and os.path.isfile(fpath):
                with open(fpath, "rb") as fh:
                    return self._send(200, fh.read(), "image/png")
            return self._send(404, {"error": "not found"})
        if url.path == "/api/state":
            q = parse_qs(url.query)
            side = (q.get("side") or ["L"])[0]
            side = LEFT if side.upper().startswith("L") else RIGHT
            return self._send(200, GAME.state(side))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if url.path == "/api/reset":
            GAME.reset()
            return self._send(200, {"ok": True})
        if url.path == "/api/cmd":
            side = LEFT if str(body.get("side", "L")).upper().startswith("L") else RIGHT
            err = GAME.command(side, body)
            return self._send(200, {"ok": err is None, "error": err})
        return self._send(404, {"error": "not found"})


def serve(host="127.0.0.1", port=8000, mode="pvp"):
    GAME.mode = mode
    GAME.reset()
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd
