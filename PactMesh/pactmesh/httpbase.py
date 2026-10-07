"""Minimal stdlib JSON HTTP server/client used by relay, ledger and API."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

MAX_BODY = 12 * 1024 * 1024


class HttpError(Exception):
    def __init__(self, status: int, code: str, detail: str = ""):
        super().__init__(f"{status} {code} {detail}")
        self.status, self.code, self.detail = status, code, detail


Route = tuple[str, re.Pattern, Callable]


class App:
    def __init__(self):
        self.routes: list[Route] = []

    def route(self, method: str, pattern: str):
        def deco(fn):
            self.routes.append((method, re.compile(f"^{pattern}$"), fn))
            return fn

        return deco

    def serve(self, host: str, port: int) -> ThreadingHTTPServer:
        app = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # keep stdout clean; no request logging of payloads
                pass

            def _handle(self, method: str):
                path, _, query = self.path.partition("?")
                params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                length = int(self.headers.get("content-length") or 0)
                try:
                    if length > MAX_BODY:
                        raise HttpError(413, "TOO_LARGE")
                    raw = self.rfile.read(length) if length else b""
                    for m, rx, fn in app.routes:
                        mt = rx.match(path)
                        if m == method and mt:
                            req = Request(method, path, params, dict(self.headers), raw)
                            out = fn(req, *mt.groups())
                            break
                    else:
                        raise HttpError(404, "NOT_FOUND")
                    status, ctype, body = 200, "application/json", None
                    if isinstance(out, Raw):
                        status, ctype, body = out.status, out.ctype, out.body
                    else:
                        body = json.dumps(out).encode()
                except HttpError as e:
                    status, ctype = e.status, "application/json"
                    body = json.dumps({"error": e.code, "detail": e.detail}).encode()
                except Exception as e:  # never leak internals
                    status, ctype = 500, "application/json"
                    body = json.dumps({"error": "INTERNAL", "detail": type(e).__name__}).encode()
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(body)))
                self.send_header("cache-control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

        srv = ThreadingHTTPServer((host, port), H)
        srv.daemon_threads = True
        return srv


class Request:
    def __init__(self, method, path, params, headers, raw):
        self.method, self.path, self.params, self.headers, self.raw = method, path, params, headers, raw

    def json(self):
        try:
            return json.loads(self.raw or b"null")
        except ValueError as e:
            raise HttpError(400, "BAD_JSON") from e

    def header(self, name: str) -> str | None:
        for k, v in self.headers.items():
            if k.lower() == name.lower():
                return v
        return None


class Raw:
    def __init__(self, body: bytes, ctype: str = "application/octet-stream", status: int = 200):
        self.body, self.ctype, self.status = body, ctype, status


def run_in_thread(srv: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return t


def call(method: str, url: str, body=None, headers: dict | None = None, timeout: float = 10.0, raw: bool = False):
    data = None
    h = dict(headers or {})
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
            h.setdefault("content-type", "application/octet-stream")
        else:
            data = json.dumps(body).encode()
            h.setdefault("content-type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = r.read()
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
        except ValueError:
            err = {"error": "HTTP_" + str(e.code)}
        raise HttpError(e.code, err.get("error", ""), err.get("detail", "")) from None
    return content if raw else json.loads(content or b"null")
