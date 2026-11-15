"""
The HandsFree Studio window: a local web page that draws the UI.

app.py keeps doing all the work (camera, mouth clicks, MIDI, voice, HUM).
Each frame it calls WebUI.publish(state) with a small JSON-able dict; the
page in web/ redraws from it. The page reports where its buttons are
(WebUI.hit) so that app.py can tell what the head pointer is over, and it
sends keys and mouse input back (WebUI.poll_input).

Standard library only: no extra packages to install.
"""

import json
import os
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css",
         ".js": "text/javascript", ".woff2": "font/woff2", ".svg": "image/svg+xml",
         ".png": "image/png"}
HIT_SLOP = 6          # px: a hit area reaches this far past its edges
CURRENT = {"ui": None, "url": None}    # set by start(); used by desktop.py


class WebUI:
    def __init__(self, port=8765):
        self.port = port
        self._state = "{}"
        self._seq = 0
        self._cond = threading.Condition()
        self._layouts = {}                 # view -> list of (hit, x, y, w, h)
        self.layout_version = 0            # goes up whenever a layout changes
        self._lock = threading.Lock()
        self.inputs = queue.Queue()
        self.server = None

    # ---- lifecycle --------------------------------------------------------
    def start(self, open_window=True):
        ui = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/events":
                    return ui._stream(self)
                if path == "/":
                    path = "/index.html"
                full = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
                if not full.startswith(WEB_DIR) or not os.path.isfile(full):
                    return self._send(404, b"not found")
                with open(full, "rb") as f:
                    body = f.read()
                ctype = TYPES.get(os.path.splitext(full)[1], "application/octet-stream")
                self._send(200, body, ctype)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._send(400, b"bad json")
                if self.path == "/layout":
                    ui._set_layout(data)
                elif self.path == "/input":
                    ui.inputs.put(data)
                self._send(204)

        self.port = _free_port(self.port)
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d/" % self.port
        print("HandsFree Studio UI: " + url)
        CURRENT.update(ui=self, url=url)
        if open_window:
            _open_window(url)

    def close(self):
        self.publish({"closed": True})
        time.sleep(0.15)
        if self.server:
            self.server.shutdown()

    # ---- state out --------------------------------------------------------
    def publish(self, state):
        text = json.dumps(state, separators=(",", ":"))
        with self._cond:
            self._state = text
            self._seq += 1
            self._cond.notify_all()

    def _stream(self, handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()
        seen = -1
        try:
            while True:
                with self._cond:
                    if self._seq == seen:
                        self._cond.wait(timeout=1.0)
                    seq, text = self._seq, self._state
                if seq == seen:
                    handler.wfile.write(b": ping\n\n")
                else:
                    seen = seq
                    handler.wfile.write(b"data: " + text.encode() + b"\n\n")
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ---- layout in --------------------------------------------------------
    def _set_layout(self, data):
        view = data.get("view")
        targets = []
        for t in data.get("targets", []):
            try:
                targets.append((tuple(_tuple(t["hit"])), float(t["x"]), float(t["y"]),
                                float(t["w"]), float(t["h"])))
            except (KeyError, TypeError, ValueError):
                continue
        with self._lock:
            self._layouts[view] = targets
            self.layout_version += 1

    def has_layout(self, view):
        with self._lock:
            return view in self._layouts

    def hit(self, view, px, py):
        """What is under (px, py) in this view: the hit tuple the page gave
        the element (e.g. ("button", "PLAY"), ("step", 1, 6), ("cmd",
        ("toggle", "dwell"))), ("pads", x, y) with x, y in 0..1 of the pad
        area, or None. Later targets sit on top of earlier ones."""
        with self._lock:
            targets = self._layouts.get(view, [])
        best = None
        for hit, x, y, w, h in reversed(targets):
            if x <= px <= x + w and y <= py <= y + h:
                best = (hit, x, y, w, h)
                break
        if best is None:                       # forgiving edges, nearest wins
            d_best = HIT_SLOP
            for hit, x, y, w, h in reversed(targets):
                if hit[0] in ("none", "pads"):
                    continue
                dx = max(x - px, 0, px - (x + w))
                dy = max(y - py, 0, py - (y + h))
                d = max(dx, dy)
                if d <= d_best:
                    best, d_best = (hit, x, y, w, h), d
        if best is None:
            return None
        hit, x, y, w, h = best
        if hit[0] == "none":
            return None
        if hit[0] == "pads":
            return ("pads", min(1.0, max(0.0, (px - x) / w)),
                    min(1.0, max(0.0, (py - y) / h)))
        return hit

    def targets(self, view):
        """The click targets of a view that can be reached right now, as
        (hit, x, y, w, h): everything whose centre is not covered by
        something on top. The pad area comes back as one ("pads",) target,
        covered or not.
        Used by the eye pointer to snap to a target."""
        with self._lock:
            targets = list(self._layouts.get(view, []))
        out = []
        for hit, x, y, w, h in targets:
            if hit[0] == "none":
                continue
            # The pad area always counts: the caller checks pad by pad.
            if hit[0] == "pads" or self.hit(view, x + w / 2, y + h / 2) == hit:
                out.append((hit, x, y, w, h))
        return out

    # ---- input in ---------------------------------------------------------
    def poll_input(self):
        out = []
        while not self.inputs.empty():
            out.append(self.inputs.get())
        return out


def _tuple(v):
    if isinstance(v, list):
        return tuple(_tuple(x) for x in v)
    return v


def _free_port(start):
    for port in range(start, start + 50):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def _open_window(url):
    """A clean app window (no tabs or address bar) in Chrome, Edge or Brave
    if one is installed; otherwise the default browser."""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for exe in candidates:
        if os.path.exists(exe):
            profile = os.path.join(tempfile.gettempdir(), "handsfree-studio-window")
            try:
                subprocess.Popen([exe, "--app=" + url, "--window-size=1280,780",
                                  "--user-data-dir=" + profile, "--no-first-run",
                                  "--no-default-browser-check",
                                  "--autoplay-policy=no-user-gesture-required"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
    webbrowser.open(url)
