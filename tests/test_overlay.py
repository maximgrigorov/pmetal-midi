"""Tests for the articulation overlay merge mode."""

from __future__ import annotations

from pathlib import Path

import mido
import pytest

from pmetal.articulation_overlay import ArticulationOverlay
from pmetal.postprocess import fix_stuck_notes, smooth_bend_transitions
from pmetal.models import Note, PitchBend
from pmetal.utils import extract_notes


class TestArticulationOverlay:
    def test_note_count_preserved(self, flat_midi_path: Path, expressive_midi_path: Path, tmp_path: Path):
        """Key invariant: output note count == flat_midi note count."""
        flat_midi = mido.MidiFile(str(flat_midi_path))
        flat_notes = []
        for t in flat_midi.tracks:
            flat_notes.extend(extract_notes(t))

        overlay = ArticulationOverlay()
        result = overlay.merge(flat_midi_path, expressive_midi_path, tmp_path / "out.mid")
        assert result.success

        out_midi = mido.MidiFile(str(tmp_path / "out.mid"))
        out_notes = []
        for t in out_midi.tracks:
            out_notes.extend(extract_notes(t))

        assert len(out_notes) == len(flat_notes)

    def test_articulation_coverage(self, flat_midi_path: Path, expressive_midi_path: Path, tmp_path: Path):
        overlay = ArticulationOverlay()
        result = overlay.merge(flat_midi_path, expressive_midi_path, tmp_path / "out.mid")
        assert result.success
        assert result.stats["mode"] == "overlay"
        assert result.stats["articulation_coverage"] > 0

    def test_velocity_transferred(self, flat_midi_path: Path, expressive_midi_path: Path, tmp_path: Path):
        overlay = ArticulationOverlay()
        result = overlay.merge(flat_midi_path, expressive_midi_path, tmp_path / "out.mid")
        assert result.success

        out_midi = mido.MidiFile(str(tmp_path / "out.mid"))
        out_notes = []
        for t in out_midi.tracks:
            out_notes.extend(extract_notes(t))

        velocities = [n.velocity for n in out_notes]
        assert max(velocities) != min(velocities), "Velocities should vary after overlay"

    def test_unmatched_notes_get_default_velocity(self, tmp_path: Path):
        """Notes with no expressive match get velocity=64."""
        flat = mido.MidiFile(ticks_per_beat=480)
        t1 = mido.MidiTrack()
        t1.append(mido.MetaMessage("track_name", name="Lead Guitar", time=0))
        t1.append(mido.Message("note_on", note=60, velocity=80, time=0))
        t1.append(mido.Message("note_off", note=60, velocity=0, time=480))
        t1.append(mido.Message("note_on", note=90, velocity=80, time=0))
        t1.append(mido.Message("note_off", note=90, velocity=0, time=480))
        flat.tracks.append(t1)
        flat_path = tmp_path / "flat.mid"
        flat.save(str(flat_path))

        expr = mido.MidiFile(ticks_per_beat=480)
        t2 = mido.MidiTrack()
        t2.append(mido.Message("note_on", note=60, velocity=100, time=0))
        t2.append(mido.Message("note_off", note=60, velocity=0, time=480))
        expr.tracks.append(t2)
        expr_path = tmp_path / "expr.mid"
        expr.save(str(expr_path))

        overlay = ArticulationOverlay()
        result = overlay.merge(flat_path, expr_path, tmp_path / "out.mid")
        assert result.success
        assert result.stats["gp8_notes"] == 2
        assert result.stats["articulated_notes"] == 1

        out_midi = mido.MidiFile(str(tmp_path / "out.mid"))
        out_notes = extract_notes(out_midi.tracks[0])
        assert len(out_notes) == 2
        unmatched = [n for n in out_notes if n.pitch == 90]
        assert len(unmatched) == 1
        assert unmatched[0].velocity == 64

    def test_output_file_created(self, flat_midi_path: Path, expressive_midi_path: Path, tmp_path: Path):
        out = tmp_path / "out.mid"
        overlay = ArticulationOverlay()
        result = overlay.merge(flat_midi_path, expressive_midi_path, out)
        assert result.success
        assert out.exists()
        assert out.stat().st_size > 0


class TestFixStuckNotes:
    def test_no_stuck_notes(self):
        track = mido.MidiTrack()
        track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
        count = fix_stuck_notes(track)
        assert count == 0

    def test_fixes_stuck_note(self):
        track = mido.MidiTrack()
        track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        track.append(mido.Message("note_on", note=64, velocity=90, time=480))
        track.append(mido.Message("note_off", note=64, velocity=0, time=480))
        count = fix_stuck_notes(track)
        assert count == 1
        note_offs = [m for m in track if m.type == "note_off" and m.note == 60]
        assert len(note_offs) == 1


class TestSmoothBendTransitions:
    def test_inserts_fade(self):
        notes = [
            Note(start=0, end=400, pitch=60, velocity=80),
            Note(start=600, end=1000, pitch=64, velocity=90),
        ]
        bends = [
            PitchBend(time=200, pitch=2000, channel=0),
            PitchBend(time=350, pitch=3000, channel=0),
        ]
        result = smooth_bend_transitions(bends, notes, tpb=480, threshold=500)
        assert len(result) > len(bends), "Should insert fade bends in the gap"
        fade_bends = [b for b in result if 400 < b.time < 600]
        assert len(fade_bends) > 0
        assert all(abs(b.pitch) < 3000 for b in fade_bends)

    def test_no_fade_when_below_threshold(self):
        notes = [
            Note(start=0, end=400, pitch=60, velocity=80),
            Note(start=600, end=1000, pitch=64, velocity=90),
        ]
        bends = [
            PitchBend(time=200, pitch=100, channel=0),
        ]
        result = smooth_bend_transitions(bends, notes, tpb=480, threshold=500)
        assert len(result) == len(bends)
