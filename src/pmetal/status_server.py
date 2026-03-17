"""Lightweight HTTP status + upload server (runs inside the container on port 8100)."""

from __future__ import annotations

import html
import json
import os
import platform
import re
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DATA_DIR = Path(os.environ.get("PMETAL_DATA_DIR", "/data"))
START_TIME = time.time()

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB

_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._\-()]")

CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0d1117; color: #c9d1d9; min-height: 100vh;
       display: flex; flex-direction: column; align-items: center; padding: 2rem; }
h1 { color: #58a6ff; margin-bottom: 0.3rem; }
.sub { color: #8b949e; margin-bottom: 1.5rem; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
        padding: 1.5rem; width: 100%; max-width: 620px; margin-bottom: 1.5rem; }
.card h2 { color: #58a6ff; font-size: 1rem; margin-bottom: 1rem; }
input[type="file"] { margin-bottom: 0.8rem; color: #c9d1d9; }
select { background: #0d1117; color: #c9d1d9; border: 1px solid #30363d;
         border-radius: 4px; padding: 0.4rem; margin-bottom: 0.8rem; }
label { color: #8b949e; font-size: 0.85rem; margin-right: 0.5rem; }
button, input[type="submit"] {
  background: #238636; color: #fff; border: none; border-radius: 6px;
  padding: 0.6rem 1.2rem; font-size: 0.95rem; cursor: pointer; }
button:hover, input[type="submit"]:hover { background: #2ea043; }
.msg { margin-top: 0.8rem; padding: 0.8rem; border-radius: 6px; }
.msg.ok { background: #0d2818; border: 1px solid #238636; color: #3fb950; }
.msg.err { background: #2d1117; border: 1px solid #da3633; color: #f85149; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; color: #8b949e; font-size: 0.8rem; padding: 0.3rem 0.5rem;
     border-bottom: 1px solid #30363d; }
td { padding: 0.35rem 0.5rem; font-family: monospace; font-size: 0.85rem; }
td.name { color: #c9d1d9; word-break: break-all; }
td.size { color: #8b949e; white-space: nowrap; }
td.actions { white-space: nowrap; }
td.actions a { color: #58a6ff; text-decoration: none; margin-right: 0.8rem; }
td.actions a:hover { text-decoration: underline; }
td.actions a.del { color: #da3633; }
.empty { color: #484f58; font-style: italic; padding: 0.5rem 0; }
"""


def _sanitize(name: str) -> str:
    name = Path(name).name
    name = _SAFE_FILENAME_RE.sub("_", name)
    return name[:200] if name else "upload"


def _human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024:.1f} KB"


def _build_page(message: str = "", msg_class: str = "") -> str:
    """Build the full HTML page with server-side rendered file listing."""
    file_rows = ""
    for subdir in ("input", "output"):
        p = DATA_DIR / subdir
        if not p.exists():
            continue
        files = sorted(f for f in p.iterdir() if f.is_file())
        if not files:
            continue
        file_rows += f'<tr><td colspan="4" style="color:#58a6ff; padding-top:1rem; font-weight:bold;">/{subdir}/</td></tr>\n'
        for f in files:
            sz = _human_size(f.stat().st_size)
            fname = f.name
            fname_esc = html.escape(fname)
            fname_js = fname.replace("\\", "\\\\").replace("'", "\\'")
            file_rows += (
                f'<tr>'
                f'<td class="name">{fname_esc}</td>'
                f'<td class="size">{sz}</td>'
                f'<td class="actions">'
                f'<a href="/download/{subdir}/{urllib.parse.quote(fname)}">download</a>'
                f'<a href="/delete/{subdir}/{urllib.parse.quote(fname)}" class="del" '
                f'onclick="return confirm(\'Delete {fname_js}?\')">delete</a>'
                f'</td></tr>\n'
            )

    if not file_rows:
        file_rows = '<tr><td colspan="4" class="empty">No files yet</td></tr>'

    msg_html = ""
    if message:
        msg_html = f'<div class="msg {msg_class}">{message}</div>'

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pmetal-midi</title>
<style>{CSS}</style>
</head>
<body>
<h1>pmetal-midi</h1>
<p class="sub">File manager for MIDI / WAV processing</p>

<div class="card">
  <h2>Upload files</h2>
  <form method="POST" action="/upload" enctype="multipart/form-data">
    <input type="file" name="file" accept=".mid,.midi,.wav,.flac,.mp3,.xml,.mxl" required><br>
    <label>To:</label>
    <select name="subdir">
      <option value="input" selected>input</option>
      <option value="output">output</option>
    </select>
    <input type="submit" value="Upload">
  </form>
  {msg_html}
</div>

<div class="card">
  <h2>Files on server</h2>
  <table>
    <thead><tr><th>Name</th><th>Size</th><th>Actions</th></tr></thead>
    <tbody>
{file_rows}
    </tbody>
  </table>
</div>
</body>
</html>"""


def _extract_boundary(content_type: str) -> bytes | None:
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            b = part[len("boundary="):].strip().strip('"')
            return b.encode("ascii")
    return None


def _parse_multipart(body: bytes, boundary: bytes) -> tuple[bytes | None, str | None, str]:
    """Parse multipart form data, return (file_data, filename, subdir)."""
    sep = b"--" + boundary
    parts = body.split(sep)
    file_data = None
    filename = None
    subdir = "input"

    for part in parts:
        if not part or part == b"--\r\n" or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        header_block, payload = part.split(b"\r\n\r\n", 1)
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]

        header_str = header_block.decode("utf-8", errors="replace")
        if 'name="file"' in header_str:
            fn_match = re.search(r'filename="([^"]*)"', header_str)
            if fn_match:
                filename = fn_match.group(1)
            file_data = payload
        elif 'name="subdir"' in header_str:
            subdir = payload.decode("utf-8", errors="replace").strip()

    return file_data, filename, subdir


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/status":
            self._send_json(self._status())
        elif self.path == "/files":
            self._send_json(self._list_files())
        elif self.path == "/files-detail":
            self._send_json(self._list_files_detail())
        elif self.path == "/health":
            self._send_json({"status": "ok"})
        elif self.path == "/" or self.path == "/upload":
            self._send_html(_build_page())
        elif self.path.startswith("/download/"):
            self._handle_download()
        elif self.path.startswith("/delete/"):
            self._handle_delete_get()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/upload":
            self._handle_upload()
        else:
            self.send_error(404)

    def do_DELETE(self) -> None:
        if self.path.startswith("/delete/"):
            self._handle_delete_api()
        else:
            self.send_error(404)

    def _handle_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_html(_build_page("Expected multipart/form-data", "err"))
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_UPLOAD_BYTES:
            self._send_html(_build_page(f"File too large ({content_length} bytes)", "err"))
            return

        body = self.rfile.read(content_length)
        boundary = _extract_boundary(content_type)
        if not boundary:
            self._send_html(_build_page("Missing multipart boundary", "err"))
            return

        file_data, filename, subdir = _parse_multipart(body, boundary)
        if not file_data or not filename:
            self._send_html(_build_page("No file selected", "err"))
            return

        if subdir not in ("input", "output"):
            subdir = "input"

        safe_name = _sanitize(filename)
        target_dir = DATA_DIR / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / safe_name
        target_path.write_bytes(file_data)

        size_str = _human_size(len(file_data))
        self._send_html(_build_page(
            f"Uploaded: {safe_name} ({size_str}) &rarr; /{subdir}/", "ok"
        ))

    def _handle_delete_get(self) -> None:
        """Delete via GET link (from browser onclick confirm)."""
        rel = self.path.split("/delete/", 1)[-1]
        rel = urllib.parse.unquote(rel)
        if ".." in rel:
            self._send_html(_build_page("Invalid path", "err"))
            return
        fpath = DATA_DIR / rel
        if not fpath.is_file():
            self._send_html(_build_page(f"File not found: {rel}", "err"))
            return
        allowed_dirs = {DATA_DIR / "input", DATA_DIR / "output"}
        if fpath.parent not in allowed_dirs:
            self._send_html(_build_page("Delete only allowed in input/ and output/", "err"))
            return
        fname = fpath.name
        fpath.unlink()
        self._send_html(_build_page(f"Deleted: {fname}", "ok"))

    def _handle_delete_api(self) -> None:
        """Delete via DELETE method (API/JS)."""
        rel = self.path.split("/delete/", 1)[-1]
        rel = urllib.parse.unquote(rel)
        if ".." in rel:
            self._send_json({"error": "Invalid path"}, code=403)
            return
        fpath = DATA_DIR / rel
        if not fpath.is_file():
            self._send_json({"error": f"File not found: {rel}"}, code=404)
            return
        allowed_dirs = {DATA_DIR / "input", DATA_DIR / "output"}
        if fpath.parent not in allowed_dirs:
            self._send_json({"error": "Delete only allowed in input/ and output/"}, code=403)
            return
        fpath.unlink()
        self._send_json({"success": True, "deleted": rel})

    def _handle_download(self) -> None:
        rel = self.path.split("/download/", 1)[-1]
        rel = urllib.parse.unquote(rel)
        if ".." in rel:
            self.send_error(403)
            return
        fpath = DATA_DIR / rel
        if not fpath.is_file():
            self.send_error(404)
            return
        data = fpath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{fpath.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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

    def _list_files_detail(self) -> dict:
        result: dict[str, list[dict]] = {}
        for subdir in ("input", "output"):
            p = DATA_DIR / subdir
            if p.exists():
                files = []
                for f in sorted(p.iterdir()):
                    if f.is_file():
                        files.append({"name": f.name, "size": _human_size(f.stat().st_size)})
                result[subdir] = files
            else:
                result[subdir] = []
        return result

    def _list_files(self) -> dict:
        result: dict[str, list[str]] = {}
        for subdir in ("input", "output", "logs"):
            p = DATA_DIR / subdir
            if p.exists():
                result[subdir] = sorted(f.name for f in p.iterdir() if f.is_file())
            else:
                result[subdir] = []
        return result

    def _send_json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: ARG002
        pass


def main() -> None:
    port = int(os.environ.get("STATUS_PORT", "8100"))
    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    print(f"Status server listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
