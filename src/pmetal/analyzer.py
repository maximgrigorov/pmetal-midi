"""Audio analysis with librosa — transient detection, spectral peaks, tempo, velocity guidance."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import librosa
import numpy as np

from .exceptions import AnalyzerError, AudioLoadError
from .models import Note, PitchBend
from .utils import seconds_to_ticks, ticks_to_seconds

logger = logging.getLogger(__name__)


@dataclass
class TransientEvent:
    time_seconds: float
    time_ticks: int = 0
    strength: float = 0.0
    frequency_hz: float | None = None


@dataclass
class SpectralPeak:
    time_seconds: float
    frequency_hz: float
    magnitude_db: float
    midi_note: int


@dataclass
class AudioFeatures:
    transients: list[TransientEvent] = field(default_factory=list)
    spectral_peaks: list[SpectralPeak] = field(default_factory=list)
    tempo_bpm: float = 0.0
    beat_times: np.ndarray = field(default_factory=lambda: np.array([]))
    rms_envelope: np.ndarray = field(default_factory=lambda: np.array([]))
    sample_rate: int = 22050
    duration_seconds: float = 0.0

    def summary(self) -> dict:
        return {
            "tempo_bpm": round(self.tempo_bpm, 1),
            "duration_seconds": round(self.duration_seconds, 1),
            "transient_count": len(self.transients),
            "spectral_peak_count": len(self.spectral_peaks),
            "beat_count": len(self.beat_times),
        }


class AudioAnalyzer:
    """Analyse WAV audio to extract features that guide MIDI merging."""

    def __init__(self, sample_rate: int = 22050, ticks_per_beat: int = 480):
        self.sample_rate = sample_rate
        self.ticks_per_beat = ticks_per_beat
        self._features: AudioFeatures | None = None

    def analyze(
        self, audio_path: Path, tempo_bpm: Optional[float] = None
    ) -> AudioFeatures:
        logger.info("Analysing audio: %s (sr=%d)", audio_path, self.sample_rate)

        if not audio_path.exists():
            raise AudioLoadError(f"Audio file not found: {audio_path}")

        try:
            y, sr = librosa.load(str(audio_path), sr=self.sample_rate)
        except Exception as e:
            raise AudioLoadError(f"Failed to load audio: {e}") from e

        duration = librosa.get_duration(y=y, sr=sr)
        logger.info("  Duration: %.1fs, samples: %d", duration, len(y))

        # Tempo & beats
        if tempo_bpm is None:
            detected_tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
            tempo_bpm = float(np.atleast_1d(detected_tempo)[0])
        else:
            _, beat_frames = librosa.beat.beat_track(y=y, sr=sr, bpm=tempo_bpm)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        logger.info("  Tempo: %.1f BPM, %d beats detected", tempo_bpm, len(beat_times))

        # Transient / onset detection
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env, sr=sr, backtrack=True, units="frames",
        )
        onset_times = librosa.frames_to_time(onset_frames, sr=sr)
        onset_strengths = onset_env[onset_frames] if len(onset_frames) > 0 else np.array([])
        max_s = np.max(onset_strengths) if len(onset_strengths) > 0 else 0
        if max_s > 0:
            onset_strengths = onset_strengths / max_s
        else:
            onset_strengths = np.zeros_like(onset_strengths)

        transients = []
        for t, s in zip(onset_times, onset_strengths):
            transients.append(TransientEvent(
                time_seconds=float(t),
                time_ticks=seconds_to_ticks(float(t), tempo_bpm, self.ticks_per_beat),
                strength=float(s),
            ))
        logger.info("  Transients: %d detected", len(transients))

        # Spectral peak detection
        spectral_peaks = self._detect_spectral_peaks(y, sr)
        logger.info("  Spectral peaks: %d detected", len(spectral_peaks))

        # RMS dynamics envelope
        rms = librosa.feature.rms(y=y)[0]

        self._features = AudioFeatures(
            transients=transients,
            spectral_peaks=spectral_peaks,
            tempo_bpm=tempo_bpm,
            beat_times=beat_times,
            rms_envelope=rms,
            sample_rate=sr,
            duration_seconds=duration,
        )
        logger.info("  Analysis complete: %s", self._features.summary())
        return self._features

    def _detect_spectral_peaks(self, y: np.ndarray, sr: int) -> list[SpectralPeak]:
        """Detect prominent spectral peaks using STFT."""
        S = np.abs(librosa.stft(y))
        S_db = librosa.amplitude_to_db(S, ref=np.max)
        freqs = librosa.fft_frequencies(sr=sr)
        times = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr)

        peaks: list[SpectralPeak] = []
        threshold_db = -30
        hop = max(1, S.shape[1] // 100)  # sample ~100 time frames

        for frame_idx in range(0, S.shape[1], hop):
            col = S_db[:, frame_idx]
            peak_bins = np.where(col > threshold_db)[0]
            if len(peak_bins) == 0:
                continue
            top_bin = peak_bins[np.argmax(col[peak_bins])]
            freq = freqs[top_bin]
            if freq < 60 or freq > 4200:
                continue
            midi_note = int(round(librosa.hz_to_midi(freq)))
            peaks.append(SpectralPeak(
                time_seconds=float(times[frame_idx]),
                frequency_hz=float(freq),
                magnitude_db=float(col[top_bin]),
                midi_note=midi_note,
            ))
        return peaks

    # ── Guidance methods ──────────────────────────────────────────────

    def get_attack_strength(self, time_ticks: int) -> float:
        """Return the transient strength closest to *time_ticks* (0.0 if none nearby)."""
        if not self._features or not self._features.transients:
            return 0.0
        best: TransientEvent | None = None
        best_dist = float("inf")
        for t in self._features.transients:
            d = abs(t.time_ticks - time_ticks)
            if d < best_dist:
                best_dist = d
                best = t
        window = self.ticks_per_beat // 4  # within a 16th note
        if best is not None and best_dist <= window:
            return best.strength
        return 0.0

    def guide_velocity(self, notes: list[Note]) -> list[Note]:
        """Adjust note velocities based on detected audio dynamics."""
        if not self._features or not self._features.transients:
            return notes
        for note in notes:
            strength = self.get_attack_strength(note.start)
            if strength > 0.0:
                target = int(30 + strength * 97)  # map 0-1 → 30-127
                note.velocity = max(note.velocity, target)
        return notes

    def validate_timing(self, notes: list[Note]) -> list[dict]:
        """Check note timing against detected beats. Return list of timing issues."""
        if not self._features:
            return []
        issues: list[dict] = []
        beat_ticks = [
            seconds_to_ticks(float(bt), self._features.tempo_bpm, self.ticks_per_beat)
            for bt in self._features.beat_times
        ]
        for note in notes:
            min_dist = min((abs(note.start - bt) for bt in beat_ticks), default=999999)
            grid = self.ticks_per_beat // 4
            if min_dist > grid:
                issues.append({
                    "note_start": note.start,
                    "pitch": note.pitch,
                    "nearest_beat_distance": min_dist,
                })
        return issues

    def validate_pitch_bends(self, bends: list[PitchBend]) -> list[dict]:
        """Check pitch bends against spectral peaks for validity."""
        if not self._features or not bends:
            return []
        issues: list[dict] = []
        for b in bends:
            if abs(b.pitch) > 4000:
                t_sec = ticks_to_seconds(
                    b.time,
                    self._features.tempo_bpm if self._features else 120,
                    self.ticks_per_beat,
                )
                has_spectral_support = any(
                    abs(sp.time_seconds - t_sec) < 0.1
                    for sp in self._features.spectral_peaks
                )
                if not has_spectral_support:
                    issues.append({
                        "time": b.time,
                        "pitch_value": b.pitch,
                        "issue": "large bend without spectral support",
                    })
        return issues
