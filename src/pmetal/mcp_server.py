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

import librosa
import numpy as np
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
Ты помогаешь музыканту обрабатывать power metal баллады из Suno.

== СТРОГИЙ ПРИНЦИП ==
НЕ предлагай оптимистичные решения или обходные пути.
Указывай ТОЛЬКО проверенные подходы. Если метод не гарантирован — скажи это явно.
НЕ используй фразы "наверное", "должно сработать", "попробуй". Говори "сработает" или "не сработает".
Если не знаешь — скажи "У меня нет проверенных данных по этому вопросу".

== ЧТО ДЕЛАЕТ СИСТЕМА ==
Объединяет два MIDI-файла:
• Flat MIDI (из Guitar Pro 8) — правильные ноты, без динамики.
• Expressive MIDI (из Neural Note) — реальная динамика, pitch bend, velocity, но ноты неточные.
Результат — Hybrid MIDI: правильные ноты + живая динамика. Для VST (Shreddage 3.5 Hydra, Darkwall).

== ИСТОЧНИКИ ПРАВДЫ ==

АУДИО: Использовать ТОЛЬКО гитарный стем (guitar stem) из Suno, НЕ полный MP3.
- Suno отдаёт стемы: vocals, drums, bass, guitar (нужен Pro/Plus)
- Полный MP3 ЗАПРЕЩЁН — Neural Note распознает удары барабанов как высоту тона
- Если стемы недоступны → СТОП. Сообщи: "Нужен Suno Pro/Plus для экспорта стемов"

ТРАНСКРИПЦИЯ: Neural Note — ЕДИНСТВЕННЫЙ рекомендуемый инструмент для гитарных стемов.
| Инструмент | Точность | Полифония | Бенды | Статус |
|------------|----------|-----------|-------|--------|
| Neural Note | 85-95% | Моно | Отличные | РЕКОМЕНДОВАН |
| Klang.io | 60-70% | Поли | Грубые | ЗАПРЕЩЁН (верифицированный мусор на выходе) |
| Melodyne | 90-98% | Моно/поли | Отличные | Альтернатива если бюджет позволяет |

Параметры Neural Note (СТРОГИЕ):
- instrument: "Electric Guitar"
- polyphony: "Monophonic" ← ОБЯЗАТЕЛЬНО для power metal / single-voice
- sensitivity: "Medium" (default; "High" только по подтверждению пользователя)

Если пользователь спрашивает про Klang → ответь:
"Klang даёт 60-70% точности с артефактами (ошибки октав, двойные ноты). Не подходит. Используй Neural Note."

EQ ПОДГОТОВКА: Если соло и ритм гитара объединены в одном стеме:
- Предложи пользователю прогнать через eq_filter ПЕРЕД Neural Note
- Для соло гитары: highpass 800 Hz + boost 2-4 kHz
- Для ритм гитары: lowpass 2000 Hz + boost 200-500 Hz
- Это повышает точность Neural Note при распознавании

МУЛЬТИТРЕК: Пользователь может дать мультидорожечный MIDI.
- Всегда вызывай midi_info ПЕРВЫМ чтобы увидеть все дорожки
- Если пользователь указал одну дорожку — обрабатывай ТОЛЬКО её через target_tracks
- Используй extract_track если нужно извлечь одну дорожку из мультитрек-файла
- На выходе: мерж только указанной дорожки, остальные без изменений

== РАБОЧИЙ ПРОЦЕСС (всё через чат) ==
1. Пользователь присылает файлы в чат → upload_file
2. midi_info для анализа структуры (дорожки, ноты)
3. Если нужна EQ подготовка WAV → eq_filter
4. merge_midi для объединения flat + expressive (с указанием target_tracks)
5. analyze_quality для проверки
6. download_file для выдачи результата

ВАЖНО: Всё через чат. НЕ предлагай команды терминала. Используй upload_file / download_file.

== ВАЛИДАЦИЯ ВХОДНЫХ ФАЙЛОВ ==
Перед merge_midi ОБЯЗАТЕЛЬНО проверь:
1. Оба файла существуют и парсятся (вызови midi_info для каждого)
2. Оба файла имеют совпадающий темп
3. Оба файла в одном контексте строя
Если валидация не прошла → СТОП. Сообщи об ошибке явно. НЕ пытайся "починить" несовпадающие входные файлы.

== ПАРАМЕТРЫ MERGE (точные значения) ==
- merger.note_snap_tolerance: 0.15 (дефолт; не меняй без данных)
- merger.pitch_correction: true
- merger.bend_smooth: true
- merger.velocity_preserve: 0.8

== ЯЗЫК ==
Отвечай на том языке, на котором пишет пользователь (обычно русский).
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
    """Начать работу с pmetal-midi — пошаговая инструкция."""
    return """\
Пользователь хочет начать работу с pmetal-midi.

СТРОГИЙ ПОРЯДОК:
1. get_status → убедись что сервер работает
2. list_files → покажи что есть
3. Объясни что нужно:
   - Гитарный стем WAV из Suno (НЕ полный MP3, НЕ Klang.io)
   - Flat MIDI (*_flat.mid) — из Guitar Pro 8 после ручной чистки нот
   - Expressive MIDI (*_neural.mid) — ТОЛЬКО из Neural Note (Monophonic, Electric Guitar)
4. Если соло и ритм гитара в одном стеме → предложи eq_filter для разделения
5. Попроси прикрепить файлы в чат
6. upload_file → midi_info → merge_midi (с target_tracks если мультитрек) → download_file

НЕ предлагай Klang.io, полный MP3, команды терминала. Отвечай на русском.
"""


@mcp.prompt()
def troubleshoot() -> str:
    """Диагностика проблем с результатом обработки."""
    return """\
Пользователь недоволен результатом обработки.

1. get_processing_log → лог последней обработки
2. analyze_quality на выходном файле
3. На основе метрик — КОНКРЕТНЫЕ значения параметров:
   - match_rate < 0.5 → matching_window_ticks=200, pitch_tolerance=5
   - velocity_range < 0.6 → velocity_boost=1.4
   - pitch_bend_continuity < 0.6 → smoothing_window=15
4. Повтори merge с новыми параметрами

НЕ говори "попробуй" или "может помочь". Укажи точные значения и причины.
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


# ── Tool: eq_filter ──────────────────────────────────────────────────

EQ_PRESETS = {
    "solo_guitar": {
        "description": "Solo guitar isolation — highpass 800 Hz, boost 2-4 kHz",
        "highpass_hz": 800,
        "lowpass_hz": None,
        "boost_low_hz": 2000,
        "boost_high_hz": 4000,
        "boost_db": 6.0,
    },
    "rhythm_guitar": {
        "description": "Rhythm guitar isolation — lowpass 2000 Hz, boost 200-500 Hz",
        "highpass_hz": None,
        "lowpass_hz": 2000,
        "boost_low_hz": 200,
        "boost_high_hz": 500,
        "boost_db": 4.0,
    },
    "bass": {
        "description": "Bass guitar isolation — lowpass 800 Hz, boost 60-250 Hz",
        "highpass_hz": None,
        "lowpass_hz": 800,
        "boost_low_hz": 60,
        "boost_high_hz": 250,
        "boost_db": 4.0,
    },
    "custom": {
        "description": "Custom EQ — all parameters user-specified",
    },
}


@mcp.tool()
def eq_filter(
    wav_path: str,
    preset: str = "solo_guitar",
    output_filename: str | None = None,
    highpass_hz: float | None = None,
    lowpass_hz: float | None = None,
    boost_low_hz: float | None = None,
    boost_high_hz: float | None = None,
    boost_db: float | None = None,
) -> str:
    """
    Apply EQ filtering to a WAV audio file before Neural Note transcription.

    Use this when solo and rhythm guitar are combined in one stem.
    Filter the stem to isolate the target guitar part for better Neural Note accuracy.

    Presets:
    - "solo_guitar": highpass 800 Hz + boost 2-4 kHz (isolates lead guitar)
    - "rhythm_guitar": lowpass 2000 Hz + boost 200-500 Hz (isolates rhythm)
    - "bass": lowpass 800 Hz + boost 60-250 Hz (isolates bass guitar)
    - "custom": all parameters user-specified

    Args:
        wav_path: Path to input WAV file (e.g. /data/input/guitar_stem.wav)
        preset: EQ preset name — "solo_guitar", "rhythm_guitar", "bass", or "custom"
        output_filename: Output filename (default: input_name + "_eq_preset.wav")
        highpass_hz: Custom highpass cutoff (overrides preset)
        lowpass_hz: Custom lowpass cutoff (overrides preset)
        boost_low_hz: Low edge of boost band (overrides preset)
        boost_high_hz: High edge of boost band (overrides preset)
        boost_db: Boost amount in dB (overrides preset)

    Returns:
        JSON with output path, applied settings, and duration.
    """
    _check_rate_limit()
    import soundfile as sf

    logger.info("eq_filter: %s preset=%s", wav_path, preset)
    path = validate_path(wav_path)

    if preset not in EQ_PRESETS:
        return json.dumps({"error": f"Unknown preset: {preset}. Available: {list(EQ_PRESETS.keys())}"})

    y, sr = librosa.load(str(path), sr=None, mono=False)
    is_stereo = y.ndim == 2
    if is_stereo:
        y_mono = librosa.to_mono(y)
    else:
        y_mono = y

    params = dict(EQ_PRESETS[preset])
    params.pop("description", None)
    if highpass_hz is not None:
        params["highpass_hz"] = highpass_hz
    if lowpass_hz is not None:
        params["lowpass_hz"] = lowpass_hz
    if boost_low_hz is not None:
        params["boost_low_hz"] = boost_low_hz
    if boost_high_hz is not None:
        params["boost_high_hz"] = boost_high_hz
    if boost_db is not None:
        params["boost_db"] = boost_db

    from scipy.signal import butter, sosfilt

    result = y_mono.copy()

    hp = params.get("highpass_hz")
    if hp and hp > 0:
        sos = butter(4, hp, btype="highpass", fs=sr, output="sos")
        result = sosfilt(sos, result)

    lp = params.get("lowpass_hz")
    if lp and lp > 0:
        sos = butter(4, lp, btype="lowpass", fs=sr, output="sos")
        result = sosfilt(sos, result)

    bl = params.get("boost_low_hz")
    bh = params.get("boost_high_hz")
    bdb = params.get("boost_db", 0)
    if bl and bh and bdb and bdb > 0:
        sos = butter(2, [bl, bh], btype="bandpass", fs=sr, output="sos")
        boosted = sosfilt(sos, y_mono)
        gain = 10 ** (bdb / 20)
        result = result + boosted * (gain - 1.0)

    peak = np.max(np.abs(result))
    if peak > 0:
        result = result / peak * 0.95

    if not output_filename:
        stem = path.stem
        output_filename = f"{stem}_eq_{preset}.wav"
    safe_name = sanitize_filename(output_filename)
    out_path = DATA_DIR / "input" / safe_name
    sf.write(str(out_path), result, sr)

    duration = len(result) / sr
    logger.info("eq_filter: saved %s (%.1fs)", out_path, duration)
    return json.dumps({
        "success": True,
        "output_path": str(out_path),
        "filename": safe_name,
        "duration_sec": round(duration, 1),
        "sample_rate": sr,
        "preset": preset,
        "applied_params": {k: v for k, v in params.items() if v is not None},
    }, indent=2)


# ── Tool: extract_track ─────────────────────────────────────────────

@mcp.tool()
def extract_track(
    midi_path: str,
    track_index: int,
    output_filename: str | None = None,
) -> str:
    """
    Extract a single track from a multi-track MIDI file.

    Use this when the user provides a multi-track MIDI and wants to process
    only one track. This creates a new MIDI file containing only the specified track.

    Args:
        midi_path: Path to the multi-track MIDI file
        track_index: Index of the track to extract (0-based, use midi_info to see indices)
        output_filename: Output filename (default: input_name + "_track_N.mid")

    Returns:
        JSON with output path, track name, and note count.
    """
    _check_rate_limit()
    import mido
    from .utils import extract_notes

    logger.info("extract_track: %s track=%d", midi_path, track_index)
    path = validate_path(midi_path)
    midi = mido.MidiFile(str(path))

    if track_index < 0 or track_index >= len(midi.tracks):
        return json.dumps({
            "error": f"Track index {track_index} out of range. File has {len(midi.tracks)} tracks (0-{len(midi.tracks)-1})."
        })

    track = midi.tracks[track_index]
    track_name = get_track_name(track) or f"Track {track_index}"
    notes = extract_notes(track)

    out_midi = mido.MidiFile(ticks_per_beat=midi.ticks_per_beat)
    if midi.tracks:
        tempo_track = mido.MidiTrack()
        for msg in midi.tracks[0]:
            if msg.is_meta and msg.type in ("set_tempo", "time_signature", "key_signature"):
                tempo_track.append(msg.copy())
        if tempo_track:
            out_midi.tracks.append(tempo_track)

    out_midi.tracks.append(track.copy() if track_index != 0 else track)

    if not output_filename:
        output_filename = f"{path.stem}_track_{track_index}.mid"
    safe_name = sanitize_filename(output_filename)
    out_path = DATA_DIR / "input" / safe_name
    out_midi.save(str(out_path))

    logger.info("extract_track: saved %s (%d notes)", out_path, len(notes))
    return json.dumps({
        "success": True,
        "output_path": str(out_path),
        "filename": safe_name,
        "track_index": track_index,
        "track_name": track_name,
        "note_count": len(notes),
        "ticks_per_beat": midi.ticks_per_beat,
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
