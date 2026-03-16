"""Quality analysis — metrics, scoring, self-correction loop, RetryStrategy, FeedbackLoop."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import mido
import numpy as np

from .config import AppConfig, MergerConfig, QualityConfig
from .exceptions import QualityError
from .models import Note, PitchBend
from .utils import extract_notes, extract_pitch_bends

logger = logging.getLogger(__name__)

METRIC_WEIGHTS = {
    "density": 0.20,
    "pitch_bend_continuity": 0.25,
    "velocity_range": 0.25,
    "timing_consistency": 0.20,
    "match_rate": 0.10,
}


@dataclass
class QualityReport:
    overall_score: float = 0.0
    passed: bool = False
    hard_failures: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    metric_details: dict[str, dict] = field(default_factory=dict)
    match_rate: float = 0.0
    velocity_range: int = 0
    pitch_bend_max_jump: int = 0
    stuck_notes_count: int = 0
    suggestions: list[str] = field(default_factory=list)
    parameter_adjustments: dict[str, Any] = field(default_factory=dict)
    analysis_timestamp: str = ""
    processing_duration_ms: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    def to_summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"Quality Analysis — {status} (score {self.overall_score:.2f})",
            "=" * 50,
        ]
        for metric, score in self.metrics.items():
            mark = "+" if score >= 0.70 else "-"
            lines.append(f"  [{mark}] {metric}: {score:.2f}")
        if self.hard_failures:
            lines.append("\nHard failures:")
            for f in self.hard_failures:
                lines.append(f"  !! {f}")
        if self.suggestions:
            lines.append("\nSuggestions:")
            for i, s in enumerate(self.suggestions, 1):
                lines.append(f"  {i}. {s}")
        return "\n".join(lines)


class QualityAnalyzer:
    def __init__(self, config: AppConfig | None = None):
        self.config = config or AppConfig.default()
        self.qc: QualityConfig = self.config.quality

    def analyze(
        self,
        midi_path: Path,
        merge_stats: dict[str, Any] | None = None,
    ) -> QualityReport:
        t0 = time.time()
        logger.info("Quality analysis: %s", midi_path)

        midi = mido.MidiFile(str(midi_path))
        tpb = midi.ticks_per_beat

        all_notes: list[Note] = []
        all_bends: list[PitchBend] = []
        for track in midi.tracks:
            all_notes.extend(extract_notes(track))
            all_bends.extend(extract_pitch_bends(track))

        metrics: dict[str, float] = {}
        details: dict[str, dict] = {}

        # density
        s, d = _density_score(all_notes, tpb)
        metrics["density"] = s
        details["density"] = d
        logger.info("  density: %.2f", s)

        # pitch bend continuity
        s, d = _pitch_bend_score(all_bends, self.qc.max_pitch_bend_jump)
        metrics["pitch_bend_continuity"] = s
        details["pitch_bend_continuity"] = d
        logger.info("  pitch_bend_continuity: %.2f", s)

        # velocity range
        s, d = _velocity_score(all_notes, self.qc.min_velocity_range)
        metrics["velocity_range"] = s
        details["velocity_range"] = d
        logger.info("  velocity_range: %.2f", s)

        # timing
        s, d = _timing_score(all_notes, tpb, self.config.merger.quantize_division)
        metrics["timing_consistency"] = s
        details["timing_consistency"] = d
        logger.info("  timing_consistency: %.2f", s)

        # match rate (from merge_stats if available)
        mr = (merge_stats or {}).get("match_rate", 1.0)
        metrics["match_rate"] = min(1.0, mr / self.qc.min_match_rate) if self.qc.min_match_rate else 1.0
        details["match_rate"] = {"actual": mr, "threshold": self.qc.min_match_rate}

        overall = sum(metrics.get(m, 0) * w for m, w in METRIC_WEIGHTS.items())
        hard_failures = self._hard_failures(details)
        passed = overall >= self.qc.min_overall_score and not hard_failures

        suggestions: list[str] = []
        adjustments: dict[str, Any] = {}
        if not passed:
            adjustments, suggestions = _suggest_adjustments(
                metrics, details, self.config.merger,
            )

        elapsed_ms = int((time.time() - t0) * 1000)
        report = QualityReport(
            overall_score=round(overall, 4),
            passed=passed,
            hard_failures=hard_failures,
            metrics=metrics,
            metric_details=details,
            match_rate=mr,
            velocity_range=details.get("velocity_range", {}).get("range", 0),
            pitch_bend_max_jump=details.get("pitch_bend_continuity", {}).get("max_jump", 0),
            stuck_notes_count=details.get("timing_consistency", {}).get("stuck_notes", 0),
            suggestions=suggestions,
            parameter_adjustments=adjustments,
            analysis_timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            processing_duration_ms=elapsed_ms,
        )
        logger.info("Quality result: %s (%.2f)", "PASS" if passed else "FAIL", overall)
        return report

    def _hard_failures(self, details: dict[str, dict]) -> list[str]:
        fails: list[str] = []
        stuck = details.get("timing_consistency", {}).get("stuck_notes", 0)
        if stuck > self.qc.max_stuck_notes:
            fails.append(f"Stuck notes: {stuck}")
        mj = details.get("pitch_bend_continuity", {}).get("max_jump", 0)
        if mj > self.qc.max_pitch_bend_jump * 2:
            fails.append(f"Extreme pitch bend jump: {mj}")
        too_quiet = details.get("velocity_range", {}).get("too_quiet_count", 0)
        if too_quiet > 5:
            fails.append(f"Too many quiet notes (velocity < 30): {too_quiet}")
        return fails


# ── metric helpers ────────────────────────────────────────────────────

def _density_score(notes: list[Note], tpb: int) -> tuple[float, dict]:
    if not notes:
        return 0.0, {"error": "no notes"}
    total_dur = max(n.end for n in notes) - min(n.start for n in notes)
    beats = total_dur / tpb if tpb else 1
    density = len(notes) / max(beats, 1)
    ratio = density / 2.0  # expected ~2 notes/beat for guitar
    score = min(1.0, ratio) if ratio <= 1 else max(0.0, 1 - (ratio - 1) * 0.5)
    return round(score, 4), {"actual_density": round(density, 2), "beats": round(beats, 1)}


def _pitch_bend_score(bends: list[PitchBend], max_jump: int) -> tuple[float, dict]:
    if len(bends) < 2:
        return 1.0, {"status": "insufficient_data"}
    sorted_b = sorted(bends, key=lambda b: b.time)
    jumps = [abs(sorted_b[i].pitch - sorted_b[i - 1].pitch) for i in range(1, len(sorted_b))]
    avg_j = float(np.mean(jumps))
    max_j = max(jumps)
    violations = sum(1 for j in jumps if j > max_jump)
    jump_score = max(0.0, 1 - avg_j / max_jump)
    penalty = min(violations * 0.1, 0.5)
    score = max(0.0, jump_score - penalty)
    return round(score, 4), {
        "avg_jump": round(avg_j, 1), "max_jump": max_j,
        "violations": violations, "total_bends": len(bends),
    }


def _velocity_score(notes: list[Note], min_range: int) -> tuple[float, dict]:
    if not notes:
        return 0.0, {"error": "no notes"}
    vels = [n.velocity for n in notes]
    vmin, vmax = min(vels), max(vels)
    vrange = vmax - vmin
    vstd = float(np.std(vels))
    too_quiet = sum(1 for v in vels if v < 30)
    range_score = min(1.0, vrange / min_range) if min_range else 1.0
    dist_score = min(1.0, vstd / (vrange / 3)) if vrange > 0 else 0.0
    audibility_penalty = min(too_quiet / len(vels) * 0.5, 0.3) if too_quiet else 0.0
    score = max(0.0, range_score * 0.5 + dist_score * 0.5 - audibility_penalty)
    return round(score, 4), {
        "min": vmin, "max": vmax, "range": vrange,
        "mean": round(float(np.mean(vels)), 1), "std": round(vstd, 1),
        "too_quiet_count": too_quiet,
    }


def _timing_score(notes: list[Note], tpb: int, q_div: int) -> tuple[float, dict]:
    if not notes:
        return 0.0, {"error": "no notes"}
    grid = tpb * 4 // q_div
    max_dur = tpb * 4
    stuck = sum(1 for n in notes if n.duration > max_dur)
    off_grid = 0
    for n in notes:
        offset = n.start % grid
        if grid * 0.25 < offset < grid * 0.75:
            off_grid += 1
    off_ratio = off_grid / len(notes)
    score = max(0.0, 1 - stuck * 0.2 - off_ratio * 0.3)
    return round(score, 4), {
        "stuck_notes": stuck, "off_grid_count": off_grid,
        "off_grid_ratio": round(off_ratio, 3),
    }


def _suggest_adjustments(
    metrics: dict[str, float],
    details: dict[str, dict],
    mc: MergerConfig,
) -> tuple[dict[str, Any], list[str]]:
    adj: dict[str, Any] = {}
    sugg: list[str] = []

    if metrics.get("match_rate", 1) < 0.5:
        if mc.matching_window_ticks < 240:
            nw = min(240, mc.matching_window_ticks + 30)
            adj["merger.matching_window_ticks"] = nw
            sugg.append(f"Widen matching window → {nw} ticks (was {mc.matching_window_ticks})")
        if mc.pitch_tolerance < 6:
            nt = min(6, mc.pitch_tolerance + 1)
            adj["merger.pitch_tolerance"] = nt
            sugg.append(f"Increase pitch tolerance → {nt} (was {mc.pitch_tolerance})")

    vr = details.get("velocity_range", {}).get("range", 100)
    if vr < 40:
        if mc.velocity_boost < 1.5:
            nb = round(mc.velocity_boost + 0.1, 2)
            adj["merger.velocity_boost"] = nb
            sugg.append(f"Boost velocity multiplier → {nb}x (was {mc.velocity_boost}x)")

    vmin_count = details.get("velocity_range", {}).get("too_quiet_count", 0)
    if vmin_count > 5:
        if mc.velocity_min > 20:
            new_min = max(20, mc.velocity_min - 5)
            adj["merger.velocity_min"] = new_min
            sugg.append(f"Lower min velocity → {new_min} (was {mc.velocity_min})")

    mj = details.get("pitch_bend_continuity", {}).get("max_jump", 0)
    if mj > 1500:
        sugg.append("Consider increasing pitch bend smoothing window")

    offgrid = details.get("timing_consistency", {}).get("off_grid_ratio", 0)
    if offgrid > 0.20:
        if mc.humanize_max_ticks > 10:
            nh = max(10, mc.humanize_max_ticks - 5)
            adj["merger.humanize_max_ticks"] = nh
            sugg.append(f"Reduce humanize → {nh} ticks (was {mc.humanize_max_ticks})")

    return adj, sugg


# ── RetryStrategy ─────────────────────────────────────────────────────

@dataclass
class RetryStrategy:
    """Encapsulates retry logic with escalating adjustments."""

    max_retries: int = 3
    escalation_factor: float = 1.5

    def should_retry(self, attempt: int, report: QualityReport) -> bool:
        if attempt >= self.max_retries:
            return False
        if report.passed:
            return False
        if report.hard_failures:
            return False  # hard failures need manual fix
        return True

    def get_adjustments(self, attempt: int, report: QualityReport) -> dict[str, Any]:
        base = report.parameter_adjustments
        if attempt > 1:
            for k, v in base.items():
                if isinstance(v, (int, float)):
                    base[k] = type(v)(v * self.escalation_factor)
        return base


class FeedbackLoop:
    """Track quality across retries and detect convergence / divergence."""

    def __init__(self) -> None:
        self.history: list[QualityReport] = []

    def record(self, report: QualityReport) -> None:
        self.history.append(report)

    @property
    def is_improving(self) -> bool:
        if len(self.history) < 2:
            return True
        return self.history[-1].overall_score > self.history[-2].overall_score

    @property
    def is_converged(self) -> bool:
        if len(self.history) < 2:
            return False
        delta = abs(self.history[-1].overall_score - self.history[-2].overall_score)
        return delta < 0.02

    def best_report(self) -> QualityReport | None:
        if not self.history:
            return None
        return max(self.history, key=lambda r: r.overall_score)

    def summary(self) -> dict[str, Any]:
        return {
            "iterations": len(self.history),
            "scores": [round(r.overall_score, 4) for r in self.history],
            "improving": self.is_improving,
            "converged": self.is_converged,
        }
