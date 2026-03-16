"""Tests for the core merger module."""

from __future__ import annotations

from pathlib import Path

import pytest

from pmetal.config import AppConfig
from pmetal.merger import MidiMerger
from pmetal.models import Note, PitchBend


class TestNoteMatching:
    def test_exact_match(self, flat_midi_path: Path, expressive_midi_path: Path, tmp_path: Path):
        merger = MidiMerger()
        result = merger.merge(flat_midi_path, expressive_midi_path, tmp_path / "out.mid")
        assert result.success
        assert result.stats["matched_notes"] > 0

    def test_match_rate_reasonable(self, flat_midi_path: Path, expressive_midi_path: Path, tmp_path: Path):
        merger = MidiMerger()
        result = merger.merge(flat_midi_path, expressive_midi_path, tmp_path / "out.mid")
        assert result.stats["match_rate"] >= 0.5

    def test_output_file_created(self, flat_midi_path: Path, expressive_midi_path: Path, tmp_path: Path):
        out = tmp_path / "out.mid"
        merger = MidiMerger()
        result = merger.merge(flat_midi_path, expressive_midi_path, out)
        assert result.success
        assert out.exists()
        assert out.stat().st_size > 0


class TestVelocityTransfer:
    def test_velocity_boost(self):
        merger = MidiMerger()
        from pmetal.models import MatchedPair

        clean = Note(start=0, end=100, pitch=60, velocity=80)
        expr = Note(start=0, end=100, pitch=60, velocity=80)
        pair = MatchedPair(clean=clean, expressive=expr, time_offset=0, score=0)

        merger._transfer_velocities([pair])
        # 80 * 1.2 = 96
        assert pair.clean.velocity == 96

    def test_velocity_clamp_max(self):
        merger = MidiMerger()
        from pmetal.models import MatchedPair

        clean = Note(start=0, end=100, pitch=60, velocity=80)
        expr = Note(start=0, end=100, pitch=60, velocity=120)
        pair = MatchedPair(clean=clean, expressive=expr, time_offset=0, score=0)

        merger._transfer_velocities([pair])
        # 120 * 1.2 = 144 -> clamped to 127
        assert pair.clean.velocity == 127

    def test_velocity_clamp_min(self):
        merger = MidiMerger()
        from pmetal.models import MatchedPair

        clean = Note(start=0, end=100, pitch=60, velocity=80)
        expr = Note(start=0, end=100, pitch=60, velocity=10)
        pair = MatchedPair(clean=clean, expressive=expr, time_offset=0, score=0)

        merger._transfer_velocities([pair])
        # 10 * 1.2 = 12 -> clamped to 30
        assert pair.clean.velocity == 30


class TestPitchBendSmoothing:
    def test_smoothing_reduces_variation(self):
        merger = MidiMerger()
        bends = [PitchBend(time=i * 10, pitch=v) for i, v in enumerate(
            [0, 2000, 500, 3000, 100, 2500, 0]
        )]
        smoothed = merger._smooth_pitch_bends(bends)
        # smoothed should have less max variation
        orig_max_jump = max(
            abs(bends[i].pitch - bends[i - 1].pitch) for i in range(1, len(bends))
        )
        smooth_max_jump = max(
            abs(smoothed[i].pitch - smoothed[i - 1].pitch)
            for i in range(1, len(smoothed))
        ) if len(smoothed) > 1 else 0
        assert smooth_max_jump <= orig_max_jump

    def test_empty_bends(self):
        merger = MidiMerger()
        assert merger._smooth_pitch_bends([]) == []
