"""Tests for the audio analyzer module."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from pmetal.analyzer import AudioAnalyzer, AudioFeatures, SpectralPeak, TransientEvent
from pmetal.exceptions import AudioLoadError
from pmetal.models import Note, PitchBend


@pytest.fixture()
def guitar_audio(tmp_path: Path) -> Path:
    """Generate a synthetic audio file with distinct onsets."""
    sr = 22050
    duration = 3.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)

    signal = np.zeros_like(t)
    onset_times = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    for onset in onset_times:
        idx = int(onset * sr)
        env = np.exp(-np.arange(0, sr // 4) / (sr * 0.05))
        chunk = np.sin(2 * np.pi * 220 * np.arange(len(env)) / sr) * env
        end = min(idx + len(chunk), len(signal))
        signal[idx:end] = chunk[:end - idx]

    p = tmp_path / "guitar_sample.wav"
    sf.write(str(p), signal, sr)
    return p


class TestTransientDetection:
    def test_detects_onsets(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        features = analyzer.analyze(guitar_audio)
        assert len(features.transients) > 0

    def test_transient_times_are_positive(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        features = analyzer.analyze(guitar_audio)
        for t in features.transients:
            assert t.time_seconds >= 0

    def test_transient_strength_normalized(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        features = analyzer.analyze(guitar_audio)
        for t in features.transients:
            assert 0.0 <= t.strength <= 1.0


class TestTempoDetection:
    def test_tempo_in_valid_range(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        features = analyzer.analyze(guitar_audio)
        assert 30 <= features.tempo_bpm <= 300

    def test_tempo_override(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        features = analyzer.analyze(guitar_audio, tempo_bpm=120.0)
        assert features.tempo_bpm == 120.0


class TestSpectralPeaks:
    def test_spectral_peaks_detected(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        features = analyzer.analyze(guitar_audio)
        assert len(features.spectral_peaks) > 0

    def test_spectral_peak_has_midi_note(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        features = analyzer.analyze(guitar_audio)
        for sp in features.spectral_peaks:
            assert 0 <= sp.midi_note <= 127


class TestGuidanceMethods:
    def test_get_attack_strength(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        features = analyzer.analyze(guitar_audio)
        strength = analyzer.get_attack_strength(0)
        assert isinstance(strength, float)

    def test_guide_velocity(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        analyzer.analyze(guitar_audio)
        notes = [Note(start=0, end=480, pitch=60, velocity=50)]
        result = analyzer.guide_velocity(notes)
        assert len(result) == 1

    def test_validate_timing(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        analyzer.analyze(guitar_audio)
        notes = [Note(start=0, end=480, pitch=60, velocity=80)]
        issues = analyzer.validate_timing(notes)
        assert isinstance(issues, list)

    def test_validate_pitch_bends(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        analyzer.analyze(guitar_audio)
        bends = [PitchBend(time=100, pitch=5000)]
        issues = analyzer.validate_pitch_bends(bends)
        assert isinstance(issues, list)


class TestErrorHandling:
    def test_missing_file(self, tmp_path: Path):
        analyzer = AudioAnalyzer()
        with pytest.raises(AudioLoadError):
            analyzer.analyze(tmp_path / "nonexistent.wav")


class TestAudioFeaturesSummary:
    def test_summary_keys(self, guitar_audio: Path):
        analyzer = AudioAnalyzer()
        features = analyzer.analyze(guitar_audio)
        s = features.summary()
        assert "tempo_bpm" in s
        assert "transient_count" in s
        assert "spectral_peak_count" in s
        assert "duration_seconds" in s
