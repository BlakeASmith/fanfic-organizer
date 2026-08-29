"""Local HTTP UI for Meltdown: The Colander Clash."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from meltdown.engine import IllegalPlay
from meltdown.run import RunState, new_run

WEB_ROOT = Path(__file__).resolve().parent / "web"

_SESSION: RunState | None = None


def current_run() -> RunState:
    global _SESSION
    if _SESSION is None:
        _SESSION = new_run(seed=1)
    return _SESSION


def reset_run(seed: int = 1) -> RunState:
    global _SESSION
    _SESSION = new_run(seed=seed)
    return _SESSION


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(current_run().public_state())
            return
        if path in {"/", "/index.html"}:
            data = (WEB_ROOT / "index.html").read_bytes()
            self._bytes(data, "text/html; charset=utf-8")
            return
        rel = path.lstrip("/")
        target = (WEB_ROOT / rel).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            self._json({"error": "not found"}, 404)
            return
        if target.is_file():
            ctype = {
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".svg": "image/svg+xml",
            }.get(target.suffix.lower(), "application/octet-stream")
            self._bytes(target.read_bytes(), ctype)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        payload = self._read_json()
        try:
            state = self._dispatch(path, payload)
        except (IllegalPlay, ValueError, KeyError) as exc:
            self._json({"error": str(exc), "state": current_run().public_state()}, 400)
            return
        self._json(state)

    def _dispatch(self, path: str, payload: dict) -> dict:
        if path == "/api/new":
            seed = int(payload.get("seed") or 1)
            return reset_run(seed).public_state()
        run = current_run()
        if path == "/api/stance":
            run.choose_stance(str(payload.get("stance") or "Sieve"))
            return run.public_state()
        if path == "/api/play":
            if run.combat is None:
                raise IllegalPlay("No combat")
            run.combat.play(str(payload["uid"]), payload.get("target_id"))
            if run.combat.over:
                run.after_combat()
            return run.public_state()
        if path == "/api/end_turn":
            if run.combat is None:
                raise IllegalPlay("No combat")
            run.combat.end_hero_turn()
            if run.combat.over:
                run.after_combat()
            return run.public_state()
        if path == "/api/reward":
            run.take_reward(payload.get("card_id"))
            return run.public_state()
        if path == "/api/relic":
            run.take_relic(payload.get("relic_id"))
            return run.public_state()
        if path == "/api/upgrade":
            run.upgrade_card(payload.get("card_id"))
            return run.public_state()
        raise ValueError(f"Unknown route {path}")


def serve(host: str = "127.0.0.1", port: int = 8766) -> None:
    reset_run(1)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.serve_forever()
