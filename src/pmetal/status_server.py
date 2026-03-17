"""Lightweight HTTP status + upload server (runs inside the container on port 8100)."""

from __future__ import annotations

import io
import json
import os
import platform
import re
import time
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DATA_DIR = Path(os.environ.get("PMETAL_DATA_DIR", "/data"))
START_TIME = time.time()

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB

UPLOAD_HTML = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pmetal-midi — Upload</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #c9d1d9; min-height: 100vh;
         display: flex; flex-direction: column; align-items: center; padding: 2rem; }
  h1 { color: #58a6ff; margin-bottom: 0.5rem; }
  .subtitle { color: #8b949e; margin-bottom: 2rem; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
          padding: 2rem; width: 100%%; max-width: 600px; }
  .drop-zone { border: 2px dashed #30363d; border-radius: 8px; padding: 3rem 1rem;
               text-align: center; cursor: pointer; transition: all 0.2s;
               margin-bottom: 1rem; }
  .drop-zone:hover, .drop-zone.dragover { border-color: #58a6ff; background: #1c2333; }
  .drop-zone p { color: #8b949e; margin-bottom: 0.5rem; }
  .drop-zone .big { font-size: 1.2rem; color: #c9d1d9; }
  input[type="file"] { display: none; }
  select { background: #0d1117; color: #c9d1d9; border: 1px solid #30363d;
           border-radius: 6px; padding: 0.5rem; width: 100%%; margin-bottom: 1rem; }
  label { display: block; color: #8b949e; margin-bottom: 0.3rem; font-size: 0.9rem; }
  button { background: #238636; color: #fff; border: none; border-radius: 6px;
           padding: 0.7rem 1.5rem; font-size: 1rem; cursor: pointer; width: 100%%;
           transition: background 0.2s; }
  button:hover { background: #2ea043; }
  button:disabled { background: #21262d; color: #484f58; cursor: not-allowed; }
  .result { margin-top: 1rem; padding: 1rem; border-radius: 6px; }
  .result.ok { background: #0d2818; border: 1px solid #238636; }
  .result.err { background: #2d1117; border: 1px solid #da3633; }
  .files { margin-top: 2rem; width: 100%%; max-width: 600px; }
  .files h2 { color: #58a6ff; margin-bottom: 0.5rem; font-size: 1.1rem; }
  .file-list { list-style: none; }
  .file-list li { padding: 0.3rem 0; color: #8b949e; font-family: monospace; font-size: 0.9rem; }
  .file-list li .name { color: #c9d1d9; }
  .file-list li a { color: #58a6ff; text-decoration: none; margin-left: 0.5rem; }
  .file-list li a:hover { text-decoration: underline; }
  .progress { display: none; margin-top: 0.5rem; }
  .progress-bar { height: 4px; background: #30363d; border-radius: 2px; overflow: hidden; }
  .progress-fill { height: 100%%; background: #58a6ff; width: 0%%; transition: width 0.3s; }
</style>
</head>
<body>
<h1>pmetal-midi</h1>
<p class="subtitle">Upload MIDI / WAV files for processing</p>

<div class="card">
  <form id="uploadForm" method="POST" action="/upload" enctype="multipart/form-data">
    <div class="drop-zone" id="dropZone">
      <p class="big">Drop files here</p>
      <p>or click to browse (.mid, .midi, .wav, .flac, .mp3)</p>
      <input type="file" id="fileInput" name="file" accept=".mid,.midi,.wav,.flac,.mp3" multiple>
    </div>
    <div id="fileNames" style="margin-bottom:1rem; color:#58a6ff;"></div>
    <label for="subdir">Destination</label>
    <select id="subdir" name="subdir">
      <option value="input" selected>input (for processing)</option>
      <option value="output">output</option>
    </select>
    <button type="submit" id="submitBtn" disabled>Upload</button>
    <div class="progress" id="progress">
      <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
    </div>
  </form>
  <div id="result"></div>
</div>

<div class="files" id="filesSection"></div>

<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const form = document.getElementById('uploadForm');
const submitBtn = document.getElementById('submitBtn');
const fileNames = document.getElementById('fileNames');
const resultDiv = document.getElementById('result');
const progress = document.getElementById('progress');
const progressFill = document.getElementById('progressFill');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  fileInput.files = e.dataTransfer.files;
  updateFileNames();
});
fileInput.addEventListener('change', updateFileNames);

function updateFileNames() {
  const files = fileInput.files;
  if (files.length > 0) {
    fileNames.textContent = Array.from(files).map(f => f.name + ' (' + (f.size/1024).toFixed(1) + ' KB)').join(', ');
    submitBtn.disabled = false;
  } else {
    fileNames.textContent = '';
    submitBtn.disabled = true;
  }
}

form.addEventListener('submit', async e => {
  e.preventDefault();
  const files = fileInput.files;
  if (!files.length) return;

  submitBtn.disabled = true;
  submitBtn.textContent = 'Uploading...';
  progress.style.display = 'block';
  resultDiv.innerHTML = '';

  const results = [];
  for (let i = 0; i < files.length; i++) {
    const fd = new FormData();
    fd.append('file', files[i]);
    fd.append('subdir', document.getElementById('subdir').value);
    progressFill.style.width = ((i / files.length) * 100) + '%%';
    try {
      const resp = await fetch('/upload', { method: 'POST', body: fd });
      const data = await resp.json();
      results.push(data);
    } catch (err) {
      results.push({ error: err.message, filename: files[i].name });
    }
  }
  progressFill.style.width = '100%%';

  const allOk = results.every(r => r.success);
  resultDiv.innerHTML = '<div class="result ' + (allOk ? 'ok' : 'err') + '">' +
    results.map(r => r.success
      ? '&#10004; ' + r.filename + ' (' + r.size_kb + ' KB) → ' + r.path
      : '&#10008; ' + (r.filename || '?') + ': ' + r.error
    ).join('<br>') + '</div>';

  submitBtn.textContent = 'Upload';
  submitBtn.disabled = false;
  fileInput.value = '';
  fileNames.textContent = '';
  setTimeout(() => { progress.style.display = 'none'; progressFill.style.width = '0%%'; }, 2000);
  loadFiles();
});

async function loadFiles() {
  try {
    const resp = await fetch('/files');
    const data = await resp.json();
    let html = '';
    for (const [dir, files] of Object.entries(data)) {
      if (!files.length) continue;
      html += '<h2>/' + dir + '</h2><ul class="file-list">';
      files.forEach(f => {
        html += '<li><span class="name">' + f + '</span>';
        html += ' <a href="/download/' + dir + '/' + f + '" title="Download">&#8595;</a>';
        html += '</li>';
      });
      html += '</ul>';
    }
    document.getElementById('filesSection').innerHTML = html || '<p style="color:#8b949e">No files yet</p>';
  } catch(e) {}
}
loadFiles();
</script>
</body>
</html>
"""

_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._\-()]")


def _sanitize(name: str) -> str:
    name = Path(name).name
    name = _SAFE_FILENAME_RE.sub("_", name)
    return name[:200] if name else "upload"


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
        elif self.path == "/health":
            self._send_json({"status": "ok"})
        elif self.path == "/" or self.path == "/upload":
            self._send_html(UPLOAD_HTML)
        elif self.path.startswith("/download/"):
            self._handle_download()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/upload":
            self._handle_upload()
        else:
            self.send_error(404)

    def _handle_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_json({"error": "Expected multipart/form-data"}, code=400)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_UPLOAD_BYTES:
            self._send_json({"error": f"File too large ({content_length} bytes, max {MAX_UPLOAD_BYTES})"}, code=413)
            return

        body = self.rfile.read(content_length)
        boundary = _extract_boundary(content_type)
        if not boundary:
            self._send_json({"error": "Missing multipart boundary"}, code=400)
            return

        file_data, filename, subdir = _parse_multipart(body, boundary)
        if not file_data or not filename:
            self._send_json({"error": "No file provided"}, code=400)
            return

        if subdir not in ("input", "output"):
            subdir = "input"

        safe_name = _sanitize(filename)
        target_dir = DATA_DIR / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / safe_name
        target_path.write_bytes(file_data)

        self._send_json({
            "success": True,
            "filename": safe_name,
            "path": str(target_path),
            "size_kb": round(len(file_data) / 1024, 1),
        })

    def _handle_download(self) -> None:
        parts = self.path.split("/download/", 1)
        if len(parts) < 2:
            self.send_error(404)
            return
        rel = parts[1]
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
