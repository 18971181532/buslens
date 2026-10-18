"""Local HTTP server and JSON API for the BusLens workbench.

Endpoints
---------
GET  /api/health              service banner
GET  /api/demos               built-in captures
POST /api/analyze             decode a demo, an uploaded VCD/CSV or a simulation
POST /api/simulate            generate a capture from a plan and decode it
GET  /                        the single-page UI
"""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from . import __version__, demos, devices, i2c
from .analyze import analyze
from .signal import Capture, parse_csv, parse_vcd

__all__ = ["serve", "make_server"]

WEB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_STARTED = time.time()
_MAX_BODY = 16 << 20


def _analyze_demo(demo_id: str, overrides: Optional[dict] = None) -> dict:
    built = demos.build_demo(demo_id)
    config = dict(built["config"])
    config.update(overrides or {})
    return analyze(built["capture"], built["protocol"], config,
                   source="demo:" + demo_id, description=built["description"])


def _simulate(payload: dict) -> dict:
    clock = float(payload.get("clockHz", 400_000))
    device_name = payload.get("device", "eeprom")
    dev = devices.device_by_name(device_name)
    plan = payload.get("plan") or {}
    if not plan.get("transactions"):
        raise ValueError("plan.transactions is empty")
    sim = i2c.I2CSim(dev, clock_hz=clock,
                     stretch_ns=float(payload.get("stretchNs", 0.0)))
    cap = sim.run(plan)
    result = analyze(cap, "i2c", {"scl": "SCL", "sda": "SDA"},
                     source="simulated",
                     description="由虚拟设备 %s 生成的捕获" % (device_name,))
    result["device"] = device_name
    result["deviceState"] = {
        "address": "0x%02X" % dev.address if dev else None,
        "registers": {"0x%02X" % k: "0x%02X" % v for k, v in
                      (dev.registers.items() if dev else [])},
    }
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "BusLens/" + __version__
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- helpers
    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > _MAX_BODY:
            raise ValueError("request body too large")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        safe = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
        if safe.startswith("..") or os.path.isabs(safe):
            self._error("forbidden", 403)
            return
        full = os.path.join(WEB_ROOT, safe)
        if not os.path.isfile(full):
            self._error("not found: %s" % path, 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ verbs
    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/api/health":
                self._json({"ok": True, "version": __version__,
                            "uptime": round(time.time() - _STARTED, 1),
                            "demos": len(demos.demo_list())})
                return
            if path == "/api/demos":
                self._json({"ok": True, "demos": demos.demo_list()})
                return
            self._static(path)
        except Exception as exc:
            traceback.print_exc()
            self._error("%s: %s" % (type(exc).__name__, exc), 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/analyze":
                self._json(self._handle_analyze(payload))
            elif path == "/api/simulate":
                self._json(_simulate(payload))
            else:
                self._error("no such endpoint", 404)
        except (ValueError, KeyError) as exc:
            self._error(str(exc), 400)
        except Exception as exc:
            traceback.print_exc()
            self._error("%s: %s" % (type(exc).__name__, exc), 500)

    def _handle_analyze(self, payload: dict) -> dict:
        overrides = payload.get("config") or {}
        if payload.get("demo"):
            return _analyze_demo(payload["demo"], overrides)
        if payload.get("vcd"):
            cap = parse_vcd(payload["vcd"])
            protocol = payload.get("protocol") or _guess(cap)
            return analyze(cap, protocol, overrides, source="upload:vcd",
                           description=payload.get("description", ""))
        if payload.get("csv"):
            cap = parse_csv(payload["csv"],
                            sample_rate_hz=payload.get("sampleRate"))
            protocol = payload.get("protocol") or _guess(cap)
            return analyze(cap, protocol, overrides, source="upload:csv",
                           description=payload.get("description", ""))
        raise ValueError("provide demo, vcd or csv")

    def log_message(self, fmt: str, *args) -> None:
        if os.environ.get("BUSLENS_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def _guess(cap: Capture) -> str:
    names = {n.upper() for n in cap.channels}
    if {"SCL", "SDA"} <= names:
        return "i2c"
    if {"SCK", "MOSI"} <= names:
        return "spi"
    if names & {"DIN", "LED"}:
        return "ws2812"
    return "uart"


def make_server(host: str = "127.0.0.1", port: int = 8777) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def serve(host: str = "127.0.0.1", port: int = 8777, open_browser: bool = True) -> None:
    httpd = make_server(host, port)
    url = "http://%s:%d/" % (host, port)
    print("BusLens %s" % __version__)
    print("  serving %s" % WEB_ROOT)
    print("  open %s  (Ctrl+C to stop)" % url)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
