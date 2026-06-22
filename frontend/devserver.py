"""Tiny static dev server for the frontend with caching disabled.

The browser must always re-fetch modules during development so edits are picked up on reload
(plain `http.server` lets the browser cache ES modules). Not used in production — nginx
serves the SPA there.

    python devserver.py [port]   # serves the current directory (run from frontend/)
"""

from __future__ import annotations

import functools
import http.server
import os
import socketserver
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5501
# Serve this file's directory (the frontend root), independent of the working directory.
ROOT = os.path.dirname(os.path.abspath(__file__))


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    handler = functools.partial(NoCacheHandler, directory=ROOT)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"Frontend dev server (no-cache) on http://localhost:{PORT} serving {ROOT}")
        httpd.serve_forever()
