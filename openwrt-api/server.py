#!/usr/bin/env python3
"""Connection map of an OpenWrt router, read over ubus (HTTP), directly or through an HTTP proxy.

Starts the collector in a background thread and serves:
  /             the map (files from ./web)
  /api/state    everything known right now, as JSON
  /api/events   server-sent events: one message per poll with added, updated and removed connections

Standard library only. Runs on any machine that reaches the router. Settings come from .env (see README.md).
"""
import json
import logging
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import config
from collector import Collector
from geo import Asn, Geo
from names import Names
from oui import Vendors

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
# Explicit types: on Windows `mimetypes` reads the registry, which sometimes maps .js/.css to text/plain,
# and the browser then refuses the stylesheet.
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain"}
log = logging.getLogger("server")


def make_handler(collector, cfg):
    class Handler(BaseHTTPRequestHandler):
        server_version = "openwrt-map"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._send(200, json.dumps(collector.snapshot(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
            if path == "/api/events":
                return self._events()
            if path == "/":
                path = "/index.html"
            file = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
            if not file.startswith(WEB + os.sep) or not os.path.isfile(file):
                return self._send(404, b"not found", "text/plain")
            ctype = TYPES.get(os.path.splitext(file)[1].lower()) or mimetypes.guess_type(file)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(file, "rb") as f:
                self._send(200, f.read(), ctype)

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = collector.subscribe()
            try:
                while True:
                    try:
                        msg = q.get(timeout=15)
                        data = json.dumps(msg, ensure_ascii=False)
                        self.wfile.write(f"data: {data}\n\n".encode())
                    except Exception as e:  # queue.Empty: keep the connection alive through proxies
                        if e.__class__.__name__ != "Empty":
                            raise
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                collector.unsubscribe(q)

    return Handler


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    cfg = config.load()
    host = cfg["http_host"]
    geo = Geo(os.path.join(HERE, "geo")).load()
    asn = Asn(os.path.join(HERE, "geo")).load()
    vendors = Vendors(os.path.join(HERE, "oui", "oui.txt")).load()
    names = Names(cfg["names_file"], vendors)
    collector = Collector(cfg, geo, asn, names)
    threading.Thread(target=collector.run, name="collector", daemon=True).start()
    httpd = ThreadingHTTPServer((host, cfg["http_port"]), make_handler(collector, cfg))
    httpd.daemon_threads = True
    log.info("router %s via %s, poll every %d s", cfg["router_url"], cfg["proxy"] or "direct connection", cfg["poll_interval"])
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    lines = [f"Карта: http://{shown}:{cfg['http_port']}  — откройте в браузере"]
    if host in ("0.0.0.0", ""):
        lines.append(f"Из сети: http://<адрес этой машины>:{cfg['http_port']}")
    lines.append("Остановить: Ctrl+C")
    width = max(len(x) for x in lines) + 4
    print("\n" + "=" * width + "\n" + "\n".join(f"  {x}" for x in lines) + "\n" + "=" * width + "\n", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        log.info("stopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    main()
