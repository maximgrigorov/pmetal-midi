"""Lightweight HTTP status server (runs inside the container on port 8100)."""

from __future__ import annotations

import json
import os
import platform
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DATA_DIR = Path(os.environ.get("PMETAL_DATA_DIR", "/data"))
START_TIME = time.time()


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/status" or self.path == "/":
            self._send_json(self._status())
        elif self.path == "/files":
            self._send_json(self._list_files())
        elif self.path == "/health":
            self._send_json({"status": "ok"})
        else:
            self.send_error(404)

    def _status(self) -> dict:
        return {
            "service": "pmetal-midi",
            "version": "2.0.0",
            "uptime_s": int(time.time() - START_TIME),
            "python": platform.python_version(),
            "arch": platform.machine(),
            "data_dir": str(DATA_DIR),
            "input_files": len(list((DATA_DIR / "input").glob("*"))) if (DATA_DIR / "input").exists() else 0,
            "output_files": len(list((DATA_DIR / "output").glob("*"))) if (DATA_DIR / "output").exists() else 0,
        }

    def _list_files(self) -> dict:
        result: dict[str, list[str]] = {}
        for subdir in ("input", "output", "logs"):
            p = DATA_DIR / subdir
            if p.exists():
                result[subdir] = sorted(f.name for f in p.iterdir() if f.is_file())
            else:
                result[subdir] = []
        return result

    def _send_json(self, data: dict) -> None:
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: ARG002
        pass  # suppress default stderr logging


def main() -> None:
    port = int(os.environ.get("STATUS_PORT", "8100"))
    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    print(f"Status server listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
