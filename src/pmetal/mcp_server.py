"""MCP server — exposes pmetal-midi tools to Claude Desktop via HTTP transport."""

from __future__ import annotations

import base64
import json
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from .config import AppConfig
from .merger import MidiMerger
from .orchestrator import MidiOrchestrator, ProcessingState, WorkflowConfig
from .quality_analyzer import QualityAnalyzer
from .security import RateLimiter, ResourceManager, WorkflowLimiter, sanitize_filename, validate_path
from .utils import get_track_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("PMETAL_DATA_DIR", "/data"))
GUIDE_PATH = Path("/app/docs/GUIDE_RU.md")

_rate_limiter = RateLimiter(max_calls=30, period_seconds=60)
_resource_mgr = ResourceManager()

# ── Comprehensive instructions for Claude ────────────────────────────
# This text is sent to Claude on every MCP init and serves as the
# system-level context the model uses to understand the project.

INSTRUCTIONS = """\
Ты — ассистент системы pmetal-midi (Power Metal MIDI Hybridization System).
Твоя задача — помогать музыканту обрабатывать power metal баллады, созданные в Suno.

== ЧТО ДЕЛАЕТ СИСТЕМА ==
Объединяет два MIDI-файла:
• Flat MIDI (из Guitar Pro 8) — правильные ноты, но без динамики ("робот").
• Expressive MIDI (из Neural Note / транскрипции аудио) — реальная динамика, pitch bend, velocity, но ноты неточные.
Результат — Hybrid MIDI: правильные ноты + живая динамика. Готов для VST (Shreddage 3.5 Hydra, Darkwall).

== РАБОЧИЙ ПРОЦЕСС (всё через чат!) ==
1. Пользователь присылает MIDI-файлы прямо в чат (drag & drop или кнопка Attach)
2. Ты вызываешь upload_file чтобы сохранить каждый файл на сервер
3. Вызываешь midi_info для анализа структуры MIDI
4. Вызываешь merge_midi для объединения flat + expressive
5. Вызываешь analyze_quality для проверки результата
6. При необходимости — повторяешь merge с adjusted параметрами
7. Вызываешь download_file чтобы получить результат и отдать пользователю

== КЛЮЧЕВЫЕ ТЕРМИНЫ ==
• Velocity — громкость ноты (1-127)
• Pitch Bend — подтяжка высоты ноты (-8192..+8191)
• TPB (Ticks Per Beat) — разрешение MIDI (обычно 480)
• Match Rate — % нот flat, для которых найдена пара в expressive
• Quality Score — оценка результата 0.0-1.0 (>0.70 = хорошо)
• Self-correction — автоматическая повторная обработка с улучшенными параметрами

== ДОСТУПНЫЕ ИНСТРУМЕНТЫ ==
• upload_file — ЗАГРУЗКА файла на сервер (base64 из чата → /data/input/)
• download_file — СКАЧИВАНИЕ файла с сервера (возвращает base64)
• merge_midi — слияние flat + expressive MIDI (основной инструмент)
• analyze_quality — анализ качества по 5 метрикам
• analyze_audio — анализ WAV (темп, транзиенты, спектральные пики)
• run_workflow — пакетная обработка всех пар *_flat.mid + *_neural.mid
• midi_info — информация о MIDI файле (дорожки, ноты)
• list_files — список файлов в /data/input и /data/output
• get_status — статус сервера (CPU, RAM, версия)
• get_processing_log — последние строки лога обработки

== КАК НАЧАТЬ ==
Если пользователь спрашивает "с чего начать" или не знает что делать:
1. Вызови get_status — убедись что сервер работает
2. Вызови list_files — покажи что уже есть на сервере
3. Объясни что нужны два файла: *_flat.mid и *_neural.mid
4. Попроси пользователя прикрепить файлы прямо в чат (кнопка + или drag & drop)
5. Когда файлы получены — вызови upload_file для каждого
6. После загрузки — предложи запустить merge_midi

ВАЖНО: Пользователь хочет работать ТОЛЬКО через чат. Не предлагай ему команды терминала.
Используй upload_file / download_file для передачи файлов.

== СОВЕТЫ ПО ПАРАМЕТРАМ ==
• Низкий match rate (<50%): увеличь matching_window_ticks (до 240) и pitch_tolerance (до 6)
• Плоская velocity: увеличь velocity_boost (до 1.5)
• Скачки pitch bend: увеличь smoothing_window
• Плохой тайминг: уменьши humanize_max_ticks

== ЯЗЫК ==
Отвечай на том языке, на котором пишет пользователь (обычно русский).
Используй техническую терминологию MIDI, но объясняй простым языком.
"""

mcp = FastMCP("pmetal-midi", instructions=INSTRUCTIONS)


def _check_rate_limit() -> None:
    if not _rate_limiter.allow():
        raise RuntimeError("Rate limit exceeded. Try again in a minute.")


# ── MCP Resource: full guide ─────────────────────────────────────────

@mcp.resource("guide://pmetal-midi/ru")
def get_guide_resource() -> str:
    """Полное руководство пользователя pmetal-midi на русском языке."""
    if GUIDE_PATH.exists():
        return GUIDE_PATH.read_text(encoding="utf-8")
    return "Guide not found at " + str(GUIDE_PATH)


# ── MCP Prompts ──────────────────────────────────────────────────────

@mcp.prompt()
def getting_started() -> str:
    """Начать работу с pmetal-midi — пошаговая инструкция для новичка."""
    return """\
Пользователь хочет начать работу с pmetal-midi.

Действуй по шагам:
1. Вызови get_status — покажи что сервер работает
2. Вызови list_files — покажи какие файлы уже есть
3. Объясни что нужно:
   - Flat MIDI (*_flat.mid) — из Guitar Pro 8 после "чистки" нот
   - Expressive MIDI (*_neural.mid) — из Neural Note или audio-to-midi
4. Попроси прикрепить оба файла прямо в чат (кнопка + или drag & drop)
5. Когда получишь файлы — вызови upload_file для каждого
6. Запусти merge_midi
7. Вызови download_file для результата и отдай пользователю

Всё через чат — никаких команд терминала. Отвечай на русском.
"""


@mcp.prompt()
def troubleshoot() -> str:
    """Диагностика проблем с результатом обработки."""
    return """\
Пользователь недоволен результатом обработки.

Действуй по шагам:
1. Вызови get_processing_log — прочитай лог последней обработки
2. Вызови list_files — найди выходной файл
3. Вызови analyze_quality на выходном файле
4. На основе метрик предложи конкретные корректировки:
   - match_rate < 0.5 → matching_window_ticks=200, pitch_tolerance=5
   - velocity_range < 0.6 → velocity_boost=1.4
   - pitch_bend_continuity < 0.6 → увеличить smoothing
5. Предложи повторить merge с новыми параметрами

Отвечай на русском языке.
"""


# ── Tool: upload_file ────────────────────────────────────────────────

MAX_UPLOAD_SIZE_MB = 50

@mcp.tool()
def upload_file(filename: str, content_base64: str, subdirectory: str = "input") -> str:
    """
    Upload a file to the server from the chat.

    When the user sends a MIDI or audio file in the chat, read its binary
    content, encode it as base64, and call this tool to save it on the server.

    Args:
        filename: Name for the file (e.g. "song_flat.mid")
        content_base64: Base64-encoded file content
        subdirectory: Target subdirectory under /data/ — "input" (default) or "output"

    Returns:
        JSON with saved file path and size.
    """
    _check_rate_limit()
    safe_name = sanitize_filename(filename)
    if not safe_name:
        return json.dumps({"error": "Invalid filename"})

    if subdirectory not in ("input", "output", "config"):
        return json.dumps({"error": f"Invalid subdirectory: {subdirectory}. Use input, output, or config."})

    try:
        raw = base64.b64decode(content_base64)
    except Exception as e:
        return json.dumps({"error": f"Invalid base64 content: {e}"})

    size_mb = len(raw) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_SIZE_MB:
        return json.dumps({"error": f"File too large: {size_mb:.1f} MB (max {MAX_UPLOAD_SIZE_MB} MB)"})

    target_dir = DATA_DIR / subdirectory
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name
    target_path.write_bytes(raw)

    logger.info("upload_file: saved %s (%d bytes)", target_path, len(raw))
    return json.dumps({
        "success": True,
        "path": str(target_path),
        "filename": safe_name,
        "size_kb": round(len(raw) / 1024, 1),
    }, indent=2)


# ── Tool: download_file ─────────────────────────────────────────────

@mcp.tool()
def download_file(file_path: str) -> str:
    """
    Download a file from the server to return to the user in the chat.

    Reads the file and returns its content as base64. Use this to send
    the processed MIDI result back to the user.

    Args:
        file_path: Path to the file on the server (e.g. /data/output/song_hybrid.mid)

    Returns:
        JSON with filename, size, and base64-encoded content.
    """
    _check_rate_limit()
    path = validate_path(file_path)

    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"})
    if not path.is_file():
        return json.dumps({"error": f"Not a file: {file_path}"})

    raw = path.read_bytes()
    size_mb = len(raw) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_SIZE_MB:
        return json.dumps({"error": f"File too large to download: {size_mb:.1f} MB"})

    encoded = base64.b64encode(raw).decode("ascii")
    logger.info("download_file: read %s (%d bytes)", path, len(raw))
    return json.dumps({
        "success": True,
        "filename": path.name,
        "path": str(path),
        "size_kb": round(len(raw) / 1024, 1),
        "content_base64": encoded,
    }, indent=2)


# ── Tool: merge_midi ─────────────────────────────────────────────────

@mcp.tool()
def merge_midi(
    flat_midi_path: str,
    expressive_midi_path: str,
    output_dir: str | None = None,
    target_tracks: list[int] | None = None,
    auto_retry: bool = True,
    config_overrides: dict[str, Any] | None = None,
) -> str:
    """
    Merge a flat MIDI file with an expressive MIDI file to create a hybrid output.

    The flat MIDI (from Guitar Pro 8) provides correct note structure.
    The expressive MIDI (from Neural Note / audio transcription) provides
    velocity dynamics, pitch bends, and micro-timing.

    The result is a production-ready MIDI optimised for Shreddage 3.5 or similar VSTs.

    Args:
        flat_midi_path: Path to flat MIDI (e.g. /data/input/song_flat.mid)
        expressive_midi_path: Path to expressive MIDI (e.g. /data/input/song_neural.mid)
        output_dir: Output directory (default: /data/output)
        target_tracks: Track indices to process (default: auto-detect guitar/bass)
        auto_retry: Retry with adjusted params if quality is low
        config_overrides: Dict of config overrides (e.g. {"merger.velocity_boost": 1.3})

    Returns:
        JSON with processing results, quality score, stats, and suggestions.
    """
    _check_rate_limit()
    logger.info("merge_midi called: %s + %s", flat_midi_path, expressive_midi_path)

    flat_path = validate_path(flat_midi_path)
    expr_path = validate_path(expressive_midi_path)
    out_dir = Path(output_dir) if output_dir else DATA_DIR / "output"

    app_config = AppConfig.default()
    if config_overrides:
        app_config = app_config.merged_with(config_overrides)

    wf_config = WorkflowConfig(
        flat_midi_path=flat_path,
        expressive_midi_path=expr_path,
        output_dir=out_dir,
        target_tracks=target_tracks,
        auto_retry=auto_retry,
    )

    orchestrator = MidiOrchestrator(wf_config, app_config)
    result = orchestrator.run()

    response = {
        "success": result.state == ProcessingState.DONE,
        "output_path": str(result.output_path) if result.output_path else None,
        "quality_score": result.quality_score,
        "retry_count": result.retry_count,
        "stats": result.stats,
        "suggestions": result.suggestions,
        "errors": [str(e) for e in result.errors] if result.errors else [],
        "log": result.log_lines,
    }
    if result.processing_stats:
        response["processing_stats"] = result.processing_stats.to_dict()
    return json.dumps(response, indent=2, default=str)


# ── Tool: analyze_quality ────────────────────────────────────────────

@mcp.tool()
def analyze_quality(midi_path: str) -> str:
    """
    Analyse the quality of a MIDI file and provide improvement suggestions.

    Checks note density, velocity range, pitch bend continuity,
    timing consistency, and provides an overall quality score.

    Args:
        midi_path: Path to the MIDI file to analyse

    Returns:
        JSON quality report with scores and suggestions.
    """
    _check_rate_limit()
    logger.info("analyze_quality: %s", midi_path)
    path = validate_path(midi_path)
    qa = QualityAnalyzer()
    report = qa.analyze(path)
    return report.to_json()


# ── Tool: analyze_audio ──────────────────────────────────────────────

@mcp.tool()
def analyze_audio(wav_path: str, tempo_bpm: float | None = None) -> str:
    """
    Analyse an audio file (WAV/FLAC/MP3) and extract musical features.

    Detects tempo, beat positions, transients (note attacks), spectral peaks,
    and dynamics. Use this to understand the audio before merging.

    Args:
        wav_path: Path to audio file (e.g. /data/input/song_guitar.wav)
        tempo_bpm: Known tempo (optional — auto-detected if not provided)

    Returns:
        JSON with tempo, transient count, spectral peaks, duration, and beat positions.
    """
    _check_rate_limit()
    from .analyzer import AudioAnalyzer

    logger.info("analyze_audio: %s", wav_path)
    path = validate_path(wav_path)
    analyzer = AudioAnalyzer()
    features = analyzer.analyze(path, tempo_bpm=tempo_bpm)
    return json.dumps({
        **features.summary(),
        "sample_rate": features.sample_rate,
        "first_10_transients": [
            {"time_s": round(t.time_seconds, 3), "strength": round(t.strength, 2)}
            for t in features.transients[:10]
        ],
        "first_10_spectral_peaks": [
            {"time_s": round(sp.time_seconds, 3), "freq_hz": round(sp.frequency_hz, 1),
             "midi_note": sp.midi_note, "db": round(sp.magnitude_db, 1)}
            for sp in features.spectral_peaks[:10]
        ],
    }, indent=2)


# ── Tool: run_workflow ───────────────────────────────────────────────

@mcp.tool()
def run_workflow(
    project_dir: str,
    workflow_name: str = "default",
) -> str:
    """
    Run a complete merge workflow for a project directory.

    Scans the directory for *_flat.mid and *_neural.mid / *_expressive.mid pairs,
    then merges each pair using the specified config preset.

    Args:
        project_dir: Directory containing input MIDI pairs (e.g. /data/input)
        workflow_name: Config preset name — 'default' or 'shreddage'

    Returns:
        JSON with results for each processed pair.
    """
    _check_rate_limit()
    logger.info("run_workflow: dir=%s preset=%s", project_dir, workflow_name)

    base = validate_path(project_dir)
    if not base.is_dir():
        return json.dumps({"error": f"Not a directory: {project_dir}"})

    config_map = {
        "default": Path("/app/config/default.yaml"),
        "shreddage": Path("/app/config/shreddage.yaml"),
    }
    config_path = config_map.get(workflow_name)
    app_config = AppConfig.load(config_path) if config_path and config_path.exists() else AppConfig.default()

    flat_files = sorted(base.glob("*_flat.mid")) + sorted(base.glob("*_flat.midi"))
    results: list[dict] = []

    for flat_path in flat_files:
        stem = flat_path.stem.replace("_flat", "")
        candidates = [
            base / f"{stem}_neural.mid",
            base / f"{stem}_expressive.mid",
            base / f"{stem}_neural.midi",
            base / f"{stem}_expressive.midi",
        ]
        expr_path = next((c for c in candidates if c.exists()), None)
        if not expr_path:
            results.append({"flat": flat_path.name, "error": "No matching expressive MIDI found"})
            continue

        out_dir = DATA_DIR / "output"
        wf = WorkflowConfig(
            flat_midi_path=flat_path, expressive_midi_path=expr_path,
            output_dir=out_dir, auto_retry=True,
        )
        orch = MidiOrchestrator(wf, app_config)
        result = orch.run()
        results.append({
            "flat": flat_path.name,
            "expressive": expr_path.name,
            "success": result.state == ProcessingState.DONE,
            "output": str(result.output_path) if result.output_path else None,
            "quality_score": result.quality_score,
            "stats": result.stats,
        })

    return json.dumps({"workflow": workflow_name, "pairs_found": len(flat_files),
                        "results": results}, indent=2, default=str)


# ── Tool: midi_info ──────────────────────────────────────────────────

@mcp.tool()
def midi_info(midi_path: str) -> str:
    """
    Show detailed information about a MIDI file — tracks, note counts,
    ticks per beat, etc.

    Args:
        midi_path: Path to the MIDI file

    Returns:
        JSON with file metadata and per-track info.
    """
    import mido
    from .utils import extract_notes

    path = validate_path(midi_path)
    midi = mido.MidiFile(str(path))
    tracks_info = []
    for i, track in enumerate(midi.tracks):
        name = get_track_name(track)
        notes = extract_notes(track)
        tracks_info.append({
            "index": i,
            "name": name or "(unnamed)",
            "note_count": len(notes),
            "event_count": len(track),
        })

    return json.dumps({
        "file": str(midi_path),
        "type": midi.type,
        "ticks_per_beat": midi.ticks_per_beat,
        "num_tracks": len(midi.tracks),
        "tracks": tracks_info,
    }, indent=2)


# ── Tool: list_files ─────────────────────────────────────────────────

@mcp.tool()
def list_files(directory: str | None = None) -> str:
    """
    List MIDI and audio files in the data directory.

    Args:
        directory: Subdirectory to list (default: lists input/ and output/)

    Returns:
        JSON with file listings.
    """
    base = Path(directory) if directory else DATA_DIR
    result: dict[str, list[dict[str, Any]]] = {}

    for subdir in ("input", "output") if directory is None else (".",):
        p = base / subdir if directory is None else base
        if not p.exists():
            continue
        files = []
        for f in sorted(p.iterdir()):
            if f.is_file() and f.suffix.lower() in (".mid", ".midi", ".wav", ".flac", ".mp3"):
                files.append({
                    "name": f.name,
                    "path": str(f),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "type": f.suffix.lower(),
                })
        result[subdir] = files

    return json.dumps(result, indent=2)


# ── Tool: get_status ─────────────────────────────────────────────────

@mcp.tool()
def get_status() -> str:
    """
    Get server status — version, data directory contents, system info,
    memory usage, CPU.

    Returns:
        JSON with server status information.
    """
    import psutil

    input_dir = DATA_DIR / "input"
    output_dir = DATA_DIR / "output"

    mem_current, mem_peak = _resource_mgr.get_usage_mb()

    cpu_pct = psutil.cpu_percent(interval=0.1)
    vm = psutil.virtual_memory()

    return json.dumps({
        "service": "pmetal-midi",
        "version": "2.0.0",
        "python": platform.python_version(),
        "arch": platform.machine(),
        "data_dir": str(DATA_DIR),
        "input_files": len(list(input_dir.glob("*"))) if input_dir.exists() else 0,
        "output_files": len(list(output_dir.glob("*"))) if output_dir.exists() else 0,
        "memory_mb": round(mem_current, 1),
        "memory_peak_mb": round(mem_peak, 1),
        "system_cpu_pct": cpu_pct,
        "system_ram_total_mb": round(vm.total / 1024 / 1024),
        "system_ram_available_mb": round(vm.available / 1024 / 1024),
    }, indent=2)


# ── Tool: get_processing_log ─────────────────────────────────────────

@mcp.tool()
def get_processing_log(lines: int = 50) -> str:
    """
    Get the latest processing log lines.

    Args:
        lines: Number of recent lines to return (default 50)

    Returns:
        Plain text with log content.
    """
    log_path = DATA_DIR / "output" / "logs" / "processing.log"
    if not log_path.exists():
        log_path = DATA_DIR / "logs" / "processing.log"
    if not log_path.exists():
        return "No processing log found."

    all_lines = log_path.read_text().splitlines()
    return "\n".join(all_lines[-lines:])


# ── Entry point ──────────────────────────────────────────────────────

def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        logger.info("Starting pmetal-midi MCP server (stdio)")
        mcp.run(transport="stdio")
    else:
        host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
        port = os.environ.get("FASTMCP_PORT", "8200")
        logger.info("Starting pmetal-midi MCP server (HTTP %s:%s)", host, port)
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
