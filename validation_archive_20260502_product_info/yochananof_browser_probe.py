#!/usr/bin/env python3
"""
Capture Yochananof browser network traffic with local Chrome.

This is a diagnostics-only tool. It launches Google Chrome headless, drives it
through the Chrome DevTools Protocol, searches product terms on yochananof.co.il,
and writes the relevant request/response metadata needed to compare against the
aiohttp GraphQL scraper.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]


DEFAULT_TERMS = [
    "חלב",
    "ביצים",
    "חזה עוף",
    "שווארמה עוף",
    "כרעיים",
    "בשר טחון 20%",
    "שמן זית כתית",
    "יוגורט יווני",
]

DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
REPORT_PATH = PROJECT_ROOT / "output_dir/validation/yochananof_browser_probe.json"


class WebSocket:
    def __init__(self, ws_url: str, timeout: float = 15.0) -> None:
        parsed = urlparse(ws_url)
        if parsed.scheme != "ws":
            raise ValueError(f"Only ws:// URLs are supported: {ws_url}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path
        if parsed.query:
            self.path += f"?{parsed.query}"
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._handshake()

    def _handshake(self) -> None:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"WebSocket handshake failed: {response[:200]!r}")

        expected = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        )
        if expected not in response:
            raise RuntimeError("WebSocket accept key mismatch")

    def send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = bytearray([0x81])
        if len(data) < 126:
            header.append(0x80 | len(data))
        elif len(data) < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", len(data)))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", len(data)))
        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        self.sock.sendall(bytes(header) + mask + masked)

    def recv_json(self) -> dict[str, Any]:
        while True:
            first = self.sock.recv(2)
            if len(first) < 2:
                raise RuntimeError("WebSocket closed")
            opcode = first[0] & 0x0F
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]

            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))

            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))
            if opcode == 0x8:
                raise RuntimeError("WebSocket close frame received")
            if opcode == 0x9:
                self._send_pong(payload)

    def _send_pong(self, payload: bytes) -> None:
        header = bytearray([0x8A, 0x80 | len(payload)])
        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.sock.recv(size - len(chunks))
            if not chunk:
                raise RuntimeError("WebSocket closed while reading frame")
            chunks.extend(chunk)
        return bytes(chunks)

    def close(self) -> None:
        self.sock.close()


class CDP:
    def __init__(self, ws_url: str) -> None:
        self.ws = WebSocket(ws_url)
        self.next_id = 1
        self.events: list[dict[str, Any]] = []

    def send(self, method: str, params: dict[str, Any] | None = None) -> int:
        command_id = self.next_id
        self.next_id += 1
        payload: dict[str, Any] = {"id": command_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.ws.send_json(payload)
        return command_id

    def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 15.0
    ) -> dict[str, Any]:
        command_id = self.send(method, params)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.ws.recv_json()
            if message.get("id") == command_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result", {})
            self.events.append(message)
        raise TimeoutError(f"Timed out waiting for {method}")

    def collect(self, seconds: float) -> list[dict[str, Any]]:
        deadline = time.monotonic() + seconds
        old_timeout = self.ws.sock.gettimeout()
        self.ws.sock.settimeout(0.5)
        try:
            while time.monotonic() < deadline:
                try:
                    self.events.append(self.ws.recv_json())
                except socket.timeout:
                    continue
        finally:
            self.ws.sock.settimeout(old_timeout)
        events, self.events = self.events, []
        return events

    def close(self) -> None:
        self.ws.close()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def fetch_json(url: str, timeout: float = 10.0) -> Any:
    parsed = urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    if resp.status >= 400:
        raise RuntimeError(f"HTTP {resp.status} from {url}: {body[:200]!r}")
    return json.loads(body.decode("utf-8"))


def wait_for_debugger(port: int, timeout: float = 15.0) -> str:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            version = fetch_json(f"http://127.0.0.1:{port}/json/version")
            return str(version["webSocketDebuggerUrl"])
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"Chrome debugger did not start: {last_error}")


def get_page_ws_url(port: int) -> str:
    pages = fetch_json(f"http://127.0.0.1:{port}/json/list")
    for page in pages:
        if page.get("type") == "page":
            return str(page["webSocketDebuggerUrl"])
    created = fetch_json(f"http://127.0.0.1:{port}/json/new?about:blank")
    return str(created["webSocketDebuggerUrl"])


def redact_headers(headers: dict[str, Any], include_sensitive: bool) -> dict[str, Any]:
    if include_sensitive:
        return dict(headers)
    redacted = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in {"cookie", "authorization", "x-magento-cache-id"}:
            redacted[key] = f"<redacted len={len(str(value))}>"
        else:
            redacted[key] = value
    return redacted


def relevant_url(url: str) -> bool:
    return "yochananof.co.il" in url or "graphql" in url


def summarize_events(
    cdp: CDP, events: list[dict[str, Any]], include_sensitive: bool
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}

    for event in events:
        method = event.get("method")
        params = event.get("params", {})
        request_id = params.get("requestId")
        if not request_id:
            continue

        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            url = str(request.get("url", ""))
            if not relevant_url(url):
                continue
            entry = by_id.setdefault(request_id, {"request_id": request_id})
            entry.update(
                {
                    "url": url,
                    "method": request.get("method"),
                    "request_headers": redact_headers(
                        request.get("headers", {}), include_sensitive
                    ),
                }
            )
            if "postData" in request:
                entry["post_data"] = request["postData"][:2000]

        elif method == "Network.requestWillBeSentExtraInfo":
            entry = by_id.setdefault(request_id, {"request_id": request_id})
            entry["request_extra_headers"] = redact_headers(
                params.get("headers", {}), include_sensitive
            )

        elif method == "Network.responseReceived":
            response = params.get("response", {})
            url = str(response.get("url", ""))
            if not relevant_url(url):
                continue
            entry = by_id.setdefault(request_id, {"request_id": request_id})
            entry.update(
                {
                    "url": url,
                    "status": response.get("status"),
                    "mime_type": response.get("mimeType"),
                    "response_headers": redact_headers(
                        response.get("headers", {}), include_sensitive
                    ),
                }
            )

        elif method == "Network.responseReceivedExtraInfo":
            entry = by_id.setdefault(request_id, {"request_id": request_id})
            entry["response_extra_headers"] = redact_headers(
                params.get("headers", {}), include_sensitive
            )

        elif method == "Network.loadingFinished":
            entry = by_id.get(request_id)
            if not entry or not relevant_url(str(entry.get("url", ""))):
                continue
            try:
                body = cdp.call("Network.getResponseBody", {"requestId": request_id})
                text = body.get("body", "")
                if body.get("base64Encoded"):
                    text = base64.b64decode(text).decode("utf-8", "replace")
                entry["body_excerpt"] = " ".join(text.split())[:1200]
            except Exception as exc:
                entry["body_error"] = str(exc)

    return [entry for entry in by_id.values() if relevant_url(str(entry.get("url", "")))]


def runtime_search_expression(term: str) -> str:
    encoded = json.dumps(term, ensure_ascii=False)
    return f"""
(() => {{
  const term = {encoded};
  const descriptors = input => [
    input.type, input.placeholder, input.ariaLabel, input.name, input.id,
    typeof input.className === 'string' ? input.className : ''
  ].join(' ');
  const inputs = [...document.querySelectorAll('input')];
  const input = inputs.find(i => /search|חיפוש/.test(descriptors(i))) || inputs[0];
  if (!input) return {{ok: false, reason: 'no input found'}};
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value'
  ).set;
  input.focus();
  setter.call(input, term);
  input.dispatchEvent(new Event('input', {{bubbles: true}}));
  input.dispatchEvent(new Event('change', {{bubbles: true}}));
  input.dispatchEvent(new KeyboardEvent('keydown', {{
    key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
  }}));
  const form = input.closest('form');
  if (form) form.dispatchEvent(new Event('submit', {{bubbles: true, cancelable: true}}));
  return {{ok: true, value: input.value, input: descriptors(input)}};
}})()
"""


def launch_chrome(
    chrome: str, port: int, user_data_dir: str, *, headless: bool
) -> subprocess.Popen:
    command = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-popup-blocking",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    if headless:
        command.insert(1, "--headless=new")
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome", default=os.environ.get("CHROME_PATH", DEFAULT_CHROME))
    parser.add_argument("--output", default=str(REPORT_PATH))
    parser.add_argument("--term", action="append", dest="terms")
    parser.add_argument("--include-sensitive", action="store_true")
    parser.add_argument("--keep-profile", action="store_true")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    chrome = args.chrome
    if not Path(chrome).exists():
        raise SystemExit(f"Chrome binary not found: {chrome}")

    terms = args.terms or DEFAULT_TERMS
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    port = free_port()
    profile_dir = tempfile.mkdtemp(prefix="yochananof-chrome-")
    proc = launch_chrome(chrome, port, profile_dir, headless=args.headless)
    browser_cdp: CDP | None = None
    page_cdp: CDP | None = None

    try:
        wait_for_debugger(port)
        page_cdp = CDP(get_page_ws_url(port))
        page_cdp.call("Network.enable", {"maxPostDataSize": 200000})
        page_cdp.call("Page.enable")
        page_cdp.call("Runtime.enable")

        report: dict[str, Any] = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "chrome": chrome,
            "terms": [],
        }

        page_cdp.call("Page.navigate", {"url": "https://www.yochananof.co.il/"})
        home_events = page_cdp.collect(10)
        report["home"] = summarize_events(
            page_cdp, home_events, include_sensitive=args.include_sensitive
        )

        for term in terms:
            term_report: dict[str, Any] = {"term": term}

            search_url = (
                "https://www.yochananof.co.il/catalogsearch/result/?q="
                + quote(term)
            )
            page_cdp.call("Page.navigate", {"url": search_url})
            nav_events = page_cdp.collect(8)

            eval_result = page_cdp.call(
                "Runtime.evaluate",
                {
                    "expression": runtime_search_expression(term),
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
            term_report["input_search_result"] = (
                eval_result.get("result", {}).get("value")
            )
            input_events = page_cdp.collect(8)
            term_report["network"] = summarize_events(
                page_cdp,
                nav_events + input_events,
                include_sensitive=args.include_sensitive,
            )
            report["terms"].append(term_report)

        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Wrote {output_path}")
        return 0
    finally:
        if page_cdp:
            page_cdp.close()
        if browser_cdp:
            browser_cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not args.keep_profile:
            shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
