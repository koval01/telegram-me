"""A fake t.me for integration tests: serves the recorded pages over HTTP.

Runs inside the application image (standard library only), started by
`tests/integration/compose.yaml`; the API containers reach it through
`TELEGRAM_BASE_URL`. Like `tests.conftest.FixtureFetcher` it answers
`<channel>_m<N>` with the pages of `<channel>`, so every test can use
channels no other test has cached, and it counts what it serves:

    GET    /__requests        {"total": 3, "paths": {"GET /s/durov_m1": 1, ...},
                               "cookies": {"issued": 1, "returned": 2}}
    DELETE /__requests        reset the counts
    PUT    /__delay?seconds=1 answer every page request that much later
    GET    /__health          200 once listening

Unrecorded requests are answered 404 and counted under `unmatched`.

Like t.me, every page sets a `stel_ssid` session cookie on a request that does
not carry a valid one (without `secure`: this is plain HTTP), so the tests can
see the client keep it and send it back.
"""

import gzip
import json
import os
import secrets
import sys
import threading
import time
from collections import Counter
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, override
from urllib.parse import parse_qsl, urlsplit

FIXTURES = Path(os.environ.get("FIXTURES_DIR", Path(__file__).parents[1] / "fixtures" / "telegram"))


def _unalias(path: str) -> str:
    """`/s/durov_m17/531` -> `/s/durov/531` (no regex: paths come from clients)."""
    segments = path.split("/")
    for index, segment in enumerate(segments):
        name, separator, number = segment.rpartition("_m")
        if separator and number.isdigit() and name.isalpha() and name.islower():
            segments[index] = name
    return "/".join(segments)


def _key(method: str, path: str, params: dict[str, str]) -> tuple[str, str, tuple[Any, ...]]:
    # `/s/<channel>`, `/s/<channel>/0` and `/s/<channel>/None` are one page.
    for suffix in ("/None", "/0"):
        if path.startswith("/s/") and path.endswith(suffix):
            path = path.removesuffix(suffix)
    return method, path, tuple(sorted(params.items()))


class State:
    """Recorded responses, counters and the configured delay."""

    def __init__(self) -> None:
        manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
        self.responses = {
            _key(item["method"], item["path"], item["params"]): (
                item["status"],
                item["content_type"],
                gzip.decompress((FIXTURES / item["file"]).read_bytes()),
            )
            for item in manifest["responses"]
        }
        self.lock = threading.Lock()
        self.counts: Counter[str] = Counter()
        self.sessions: set[str] = set()
        self.cookies: Counter[str] = Counter()
        self.delay = 0.0


STATE = State()


class Handler(BaseHTTPRequestHandler):
    """Fixture pages plus the control endpoints."""

    protocol_version = "HTTP/1.1"

    @override
    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: the tests read the counters instead."""

    def _send(
        self, status: int, body: bytes, content_type: str = "application/json", cookie: str = ""
    ) -> None:
        self.send_response(status)
        if cookie:
            self.send_header("set-cookie", f"stel_ssid={cookie}; path=/; samesite=None; HttpOnly")
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        if status == 302:
            self.send_header("location", "https://telegram.org")
        self.end_headers()
        self.wfile.write(body)

    def _control(self, method: str, path: str, params: dict[str, str]) -> bool:
        if path == "/__health":
            self._send(200, b"{}")
        elif path == "/__requests" and method == "GET":
            with STATE.lock:
                counts = dict(STATE.counts)
                cookies = dict(STATE.cookies)
            total = sum(n for name, n in counts.items() if name != "unmatched")
            body = {"total": total, "paths": counts, "cookies": cookies}
            self._send(200, json.dumps(body).encode())
        elif path == "/__requests" and method == "DELETE":
            with STATE.lock:
                STATE.counts.clear()
                STATE.cookies.clear()
            self._send(204, b"")
        elif path == "/__delay" and method == "PUT":
            STATE.delay = float(params.get("seconds", "0"))
            self._send(204, b"")
        else:
            return False
        return True

    def _handle(self, method: str) -> None:
        # Request bodies (empty POSTs) are not used; drain them anyway.
        self.rfile.read(int(self.headers.get("content-length") or 0))
        url = urlsplit(self.path)
        params = dict(parse_qsl(url.query))
        if self._control(method, url.path, params):
            return
        if STATE.delay:
            time.sleep(STATE.delay)
        key = _key(method, _unalias(url.path), params)
        response = STATE.responses.get(key)
        with STATE.lock:
            STATE.counts[f"{method} {url.path}" if response else "unmatched"] += 1
        if response is None:
            self._send(404, b"", "text/html")
            return
        status, content_type, body = response
        self._send(status, body, content_type, self._session())

    def _session(self) -> str:
        """A new session cookie, or "" when the request sent back a known one."""
        sent = SimpleCookie(self.headers.get("cookie", "")).get("stel_ssid")
        with STATE.lock:
            if sent is not None and sent.value in STATE.sessions:
                STATE.cookies["returned"] += 1
                return ""
            cookie = secrets.token_hex(16)
            STATE.sessions.add(cookie)
            STATE.cookies["issued"] += 1
        return cookie

    def do_GET(self) -> None:
        """Serve a GET."""
        self._handle("GET")

    def do_POST(self) -> None:
        """Serve a POST."""
        self._handle("POST")

    def do_PUT(self) -> None:
        """Serve a PUT."""
        self._handle("PUT")

    def do_DELETE(self) -> None:
        """Serve a DELETE."""
        self._handle("DELETE")


def main() -> None:
    """Listen on $PORT (default 8080) until stopped."""
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - inside a container
    server.daemon_threads = True
    sys.stdout.write(f"fake t.me on :{port} with {len(STATE.responses)} pages\n")
    sys.stdout.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
