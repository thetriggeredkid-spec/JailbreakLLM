#!/usr/bin/env python3
"""Localhost-only live dashboard server. Re-renders when the latest
transcript or the campaign state changes; serves docs/index.html on
127.0.0.1:8737. Supports ?run=run-XXX.jsonl to view a specific attempt.
Also serves /state.json (per-run battlespace state for the 3D map) and the
vendored three.js module."""

import importlib
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import render

HOST, PORT = "127.0.0.1", 8737
RUN_NAME_RE = re.compile(r"^run-[\d-]+\.jsonl$")
DOCS_DIR = os.path.dirname(os.path.abspath(__file__))
THREE_PATH = os.path.join(DOCS_DIR, "vendor", "three.module.js")

# The page is re-rendered on each request (cheap, single file), so ?run=
# views and live updates both stay correct without a watcher thread.
# render.py itself is reloaded per request, so edits to it take effect
# without restarting this server.


def _valid_run(query):
    run = parse_qs(query).get("run", [None])[0]
    if run and not RUN_NAME_RE.match(run):
        return None, False
    return run, True


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # never serve stale frames
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        run, ok = _valid_run(parsed.query)
        if not ok:
            self.send_error(400, "invalid run name")
            return

        if parsed.path == "/state.json":
            try:
                importlib.reload(render)
                body = json.dumps(render.state_json(transcript_name=run)).encode()
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode()
            self._send(body, "application/json; charset=utf-8")
            return

        if parsed.path == "/vendor/three.module.js":
            # realpath guard: the vendored file must resolve inside docs/vendor
            base = os.path.realpath(os.path.join(DOCS_DIR, "vendor")) + os.sep
            if not os.path.realpath(THREE_PATH).startswith(base):
                self.send_error(404)
                return
            try:
                with open(THREE_PATH, "rb") as fh:
                    body = fh.read()
            except OSError:
                self.send_error(404)
                return
            self._send(body, "text/javascript; charset=utf-8")
            return

        if parsed.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            importlib.reload(render)
            render.render(transcript_name=run)
            with open(render.OUT_PATH, "rb") as fh:
                body = fh.read()
        except Exception as exc:
            body = f"<pre>render error: {exc}</pre>".encode()
        self._send(body, "text/html; charset=utf-8")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    render.render()
    print(f"live dashboard on http://{HOST}:{PORT}  (Ctrl-C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
