"""Workflow orchestrator — state machine driving the merge pipeline."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig
from .exceptions import WorkflowError
from .merger import MidiMerger
from .models import MergeResult
from .quality_analyzer import QualityAnalyzer, QualityReport

logger = logging.getLogger(__name__)


class ProcessingState(Enum):
    INIT = auto()
    ANALYZE = auto()
    MERGE = auto()
    OUTPUT = auto()
    QUALITY_CHECK = auto()
    RETRY = auto()
    DONE = auto()
    ERROR = auto()


@dataclass
class WorkflowConfig:
    flat_midi_path: Path
    expressive_midi_path: Path
    output_dir: Path
    audio_path: Path | None = None
    config_path: Path | None = None
    target_tracks: list[int] | None = None
    max_retries: int = 3
    auto_retry: bool = True
    verbose: bool = False


@dataclass
class ProcessingStats:
    """Timing statistics for each processing state."""

    state_durations: dict[str, float] = field(default_factory=dict)
    total_duration: float = 0.0
    clean_notes: int = 0
    matched_notes: int = 0
    match_rate: float = 0.0
    quality_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_durations": self.state_durations,
            "total_duration_s": round(self.total_duration, 2),
            "clean_notes": self.clean_notes,
            "matched_notes": self.matched_notes,
            "match_rate": round(self.match_rate, 4),
            "quality_score": round(self.quality_score, 4),
        }


class CheckpointManager:
    """Save and restore workflow state for crash recovery."""

    def __init__(self, checkpoint_dir: Path):
        self._dir = checkpoint_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def checkpoint_path(self) -> Path:
        return self._dir / "checkpoint.json"

    def save(self, state: ProcessingState, data: dict[str, Any]) -> None:
        payload = {"state": state.name, "timestamp": time.time(), **data}
        self.checkpoint_path.write_text(json.dumps(payload, default=str))
        logger.debug("Checkpoint saved: %s", state.name)

    def load(self) -> dict[str, Any] | None:
        if not self.checkpoint_path.exists():
            return None
        try:
            return json.loads(self.checkpoint_path.read_text())
        except Exception:
            logger.warning("Corrupt checkpoint, ignoring")
            return None

    def clear(self) -> None:
        if self.checkpoint_path.exists():
            self.checkpoint_path.unlink()


@dataclass
class WorkflowResult:
    state: ProcessingState
    output_path: Path | None = None
    quality_score: float = 0.0
    retry_count: int = 0
    stats: dict[str, Any] = field(default_factory=dict)
    processing_stats: ProcessingStats | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)


class MidiOrchestrator:
    """State-machine orchestrator for the full merge + quality workflow."""

    def __init__(self, wf_config: WorkflowConfig, app_config: AppConfig | None = None):
        self.wf = wf_config
        self.app_config = app_config or AppConfig.default()
        self.state = ProcessingState.INIT
        self.retry_count = 0
        self.errors: list[dict[str, Any]] = []
        self.suggestions: list[str] = []
        self._log_lines: list[str] = []
        self._callbacks: dict[ProcessingState | None, list[Callable]] = {}
        self._pstats = ProcessingStats()
        self._state_start: float = 0.0

        self.merger: MidiMerger | None = None
        self.quality_analyzer: QualityAnalyzer | None = None
        self.merge_result: MergeResult | None = None
        self.quality_report: QualityReport | None = None
        self.audio_features = None

        self._checkpoint: CheckpointManager | None = None
        if wf_config.output_dir:
            self._checkpoint = CheckpointManager(wf_config.output_dir / ".checkpoint")

        self._setup_file_logging()

    def _setup_file_logging(self) -> None:
        log_dir = self.wf.output_dir / "logs" if self.wf.output_dir else None
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_dir / "processing.log", maxBytes=5 * 1024 * 1024, backupCount=3,
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s")
            )
            logging.getLogger("pmetal").addHandler(handler)

    def on_state_change(self, callback: Callable[[ProcessingState, str], None]) -> None:
        """Register a callback invoked on every state transition."""
        self._callbacks.setdefault(None, []).append(callback)

    def register_callback(
        self, state: ProcessingState, callback: Callable[[ProcessingState, str], None]
    ) -> None:
        """Register a callback for a specific state transition."""
        self._callbacks.setdefault(state, []).append(callback)

    def get_state(self) -> ProcessingState:
        return self.state

    def run(self) -> WorkflowResult:
        self._log("Workflow starting")
        start = time.time()

        while self.state not in (ProcessingState.DONE, ProcessingState.ERROR):
            self._state_start = time.time()
            old_state = self.state
            try:
                self._transition()
            except Exception as e:
                logger.exception("Workflow error in state %s", self.state.name)
                self.errors.append({"state": self.state.name, "error": str(e)})
                self.state = ProcessingState.ERROR

            # record timing
            elapsed_state = time.time() - self._state_start
            self._pstats.state_durations[old_state.name] = round(elapsed_state, 3)

            # fire callbacks
            msg = self._log_lines[-1] if self._log_lines else ""
            for cb in self._callbacks.get(None, []):
                cb(self.state, msg)
            for cb in self._callbacks.get(self.state, []):
                cb(self.state, msg)

            # checkpoint
            if self._checkpoint:
                self._checkpoint.save(self.state, {"retry": self.retry_count})

        self._pstats.total_duration = time.time() - start
        if self.merge_result and self.merge_result.stats:
            self._pstats.clean_notes = self.merge_result.stats.get("clean_notes", 0)
            self._pstats.matched_notes = self.merge_result.stats.get("matched_notes", 0)
            self._pstats.match_rate = self.merge_result.stats.get("match_rate", 0)
        if self.quality_report:
            self._pstats.quality_score = self.quality_report.overall_score

        self._log(f"Workflow finished in {self._pstats.total_duration:.1f}s — state={self.state.name}")
        if self._checkpoint:
            self._checkpoint.clear()

        return WorkflowResult(
            state=self.state,
            output_path=self.merge_result.output_path if self.merge_result else None,
            quality_score=self.quality_report.overall_score if self.quality_report else 0,
            retry_count=self.retry_count,
            stats=self.merge_result.stats if self.merge_result else {},
            processing_stats=self._pstats,
            errors=self.errors,
            suggestions=self.suggestions,
            log_lines=list(self._log_lines),
        )

    # ── state handlers ────────────────────────────────────────────────

    @contextmanager
    def _error_context(self, state_name: str):
        try:
            yield
        except Exception as e:
            raise WorkflowError(str(e), state=state_name) from e

    def _transition(self) -> None:
        handler = {
            ProcessingState.INIT: self._do_init,
            ProcessingState.ANALYZE: self._do_analyze,
            ProcessingState.MERGE: self._do_merge,
            ProcessingState.OUTPUT: self._do_output,
            ProcessingState.QUALITY_CHECK: self._do_quality,
            ProcessingState.RETRY: self._do_retry,
        }.get(self.state)
        if handler is None:
            raise WorkflowError(f"No handler for state {self.state}", state=self.state.name)
        self.state = handler()

    def _do_init(self) -> ProcessingState:
        with self._error_context("INIT"):
            self._log("[INIT] Loading configuration")
            if self.wf.config_path and self.wf.config_path.exists():
                self.app_config = AppConfig.load(self.wf.config_path)
                self._log(f"  Loaded config from {self.wf.config_path}")
            self.merger = MidiMerger(self.app_config)
            self.quality_analyzer = QualityAnalyzer(self.app_config)
            self.wf.output_dir.mkdir(parents=True, exist_ok=True)
            self._log("[INIT] Ready")
        return ProcessingState.ANALYZE

    def _do_analyze(self) -> ProcessingState:
        with self._error_context("ANALYZE"):
            self._log("[ANALYZE] Validating input files")
            for p in (self.wf.flat_midi_path, self.wf.expressive_midi_path):
                if not p.exists():
                    raise FileNotFoundError(f"Input file not found: {p}")
                self._log(f"  {p.name} ({p.stat().st_size / 1024:.1f} KB)")

            # optional audio analysis
            if self.wf.audio_path and self.wf.audio_path.exists():
                self._log(f"[ANALYZE] Analysing audio: {self.wf.audio_path.name}")
                from .analyzer import AudioAnalyzer
                analyzer = AudioAnalyzer()
                self.audio_features = analyzer.analyze(self.wf.audio_path)
                self._log(
                    f"  Tempo: {self.audio_features.tempo_bpm:.1f} BPM, "
                    f"{len(self.audio_features.transients)} transients"
                )
        return ProcessingState.MERGE

    def _do_merge(self) -> ProcessingState:
        with self._error_context("MERGE"):
            self._log("[MERGE] Starting merge")
            stem = self.wf.flat_midi_path.stem.replace("_flat", "").replace("_clean", "")
            output_path = self.wf.output_dir / f"{stem}_hybrid.mid"

            assert self.merger is not None
            self.merge_result = self.merger.merge(
                flat_midi_path=self.wf.flat_midi_path,
                expressive_midi_path=self.wf.expressive_midi_path,
                output_path=output_path,
                target_tracks=self.wf.target_tracks,
                audio_features=self.audio_features,
            )

            if not self.merge_result.success:
                raise WorkflowError(
                    self.merge_result.error_message or "Merge failed", state="MERGE"
                )

            stats = self.merge_result.stats
            self._log(
                f"[MERGE] Done — matched {stats.get('matched_notes', '?')}"
                f"/{stats.get('clean_notes', '?')}"
                f" ({stats.get('match_rate', 0) * 100:.1f}%)"
            )
        return ProcessingState.OUTPUT

    def _do_output(self) -> ProcessingState:
        assert self.merge_result is not None
        self._log(f"[OUTPUT] Written to {self.merge_result.output_path}")

        # write processing report
        report_path = self.wf.output_dir / "processing_report.txt"
        lines = [
            f"Processing Report — {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
            f"Flat MIDI:       {self.wf.flat_midi_path}",
            f"Expressive MIDI: {self.wf.expressive_midi_path}",
            f"Output:          {self.merge_result.output_path}",
            "",
            "Stats:",
        ]
        for k, v in self.merge_result.stats.items():
            lines.append(f"  {k}: {v}")
        report_path.write_text("\n".join(lines) + "\n")
        self._log(f"[OUTPUT] Report written to {report_path}")

        return ProcessingState.QUALITY_CHECK

    def _do_quality(self) -> ProcessingState:
        with self._error_context("QUALITY_CHECK"):
            assert self.quality_analyzer is not None and self.merge_result is not None
            self._log("[QUALITY] Running analysis")
            self.quality_report = self.quality_analyzer.analyze(
                self.merge_result.output_path,  # type: ignore[arg-type]
                merge_stats=self.merge_result.stats,
            )
            self.suggestions = self.quality_report.suggestions
            score = self.quality_report.overall_score
            self._log(
                f"[QUALITY] Score {score:.2f} — "
                + ("PASS" if self.quality_report.passed else "FAIL")
            )

        if self.quality_report.passed:
            return ProcessingState.DONE
        if self.wf.auto_retry and self.retry_count < self.wf.max_retries:
            return ProcessingState.RETRY
        self._log("[QUALITY] Accepting sub-optimal result (no more retries)")
        return ProcessingState.DONE

    def _do_retry(self) -> ProcessingState:
        self.retry_count += 1
        assert self.quality_report is not None
        adjustments = self.quality_report.parameter_adjustments
        self._log(f"[RETRY #{self.retry_count}] Adjusting: {adjustments}")

        if adjustments:
            self.app_config = self.app_config.merged_with(adjustments)
            self.merger = MidiMerger(self.app_config)

        if self.retry_count >= self.wf.max_retries:
            self._log("[RETRY] Max retries reached")
            return ProcessingState.DONE
        return ProcessingState.ANALYZE

    # ── helpers ───────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        logger.info(msg)
        self._log_lines.append(msg)
