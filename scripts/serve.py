#!/usr/bin/env python3
"""Local static server with SPA fallback for the TraceBench website.

Usage:
  cd /Users/tbe/repos/tracebench.github.io
  python3 scripts/serve.py

Then open http://localhost:8000/. Direct nested routes such as
/results/agent-x-2026-05-01/ are served through index.html.
"""

from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = 8000




class FastThreadingHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # Avoid reverse-DNS lookup in HTTPServer.server_bind, which can be slow
        # or unavailable in minimal container environments.
        TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


class SpaHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        parsed_path = unquote(urlparse(path).path)
        parts = [part for part in PurePosixPath(parsed_path).parts if part not in ("/", "", ".", "..")]
        return str(ROOT.joinpath(*parts))

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        request_path = urlparse(self.path).path
        file_path = Path(self.translate_path(request_path))
        if not file_path.exists() and "." not in Path(request_path).name:
            self.path = "/index.html"
        return super().do_GET()


if __name__ == "__main__":
    server = FastThreadingHTTPServer((HOST, PORT), SpaHandler)
    print(f"Serving TraceBench website at http://localhost:{PORT}/")
    server.serve_forever()
