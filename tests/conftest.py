"""Shared pytest fixtures for pmetal-midi tests."""

from __future__ import annotations

from pathlib import Path

import mido
import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_simple_midi(tpb: int = 480, notes: list[tuple[int, int, int, int]] | None = None) -> mido.MidiFile:
    """
    Create a minimal MIDI file in memory.

    notes: list of (pitch, velocity, start_tick, duration_ticks)
    """
    midi = mido.MidiFile(ticks_per_beat=tpb)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Test Guitar", time=0))
    midi.tracks.append(track)

    if notes is None:
        notes = [
            (60, 80, 0, 480),
            (64, 90, 480, 480),
            (67, 100, 960, 480),
            (72, 85, 1440, 480),
        ]

    events: list[tuple[int, mido.Message]] = []
    for pitch, vel, start, dur in notes:
        events.append((start, mido.Message("note_on", note=pitch, velocity=vel, time=0)))
        events.append((start + dur, mido.Message("note_off", note=pitch, velocity=0, time=0)))

    events.sort(key=lambda x: x[0])
    abs_time = 0
    for t, msg in events:
        msg.time = t - abs_time
        abs_time = t
        track.append(msg)

    return midi


@pytest.fixture()
def flat_midi_path(tmp_path: Path) -> Path:
    """Create a flat MIDI file with clean notes."""
    midi = _make_simple_midi(480, [
        (60, 80, 0, 480),
        (64, 80, 480, 480),
        (67, 80, 960, 480),
        (72, 80, 1440, 480),
        (60, 80, 1920, 480),
        (64, 80, 2400, 480),
    ])
    p = tmp_path / "flat.mid"
    midi.save(str(p))
    return p


@pytest.fixture()
def expressive_midi_path(tmp_path: Path) -> Path:
    """Create an expressive MIDI file with varying velocities and slight timing shifts."""
    midi = _make_simple_midi(480, [
        (60, 95, 5, 470),
        (64, 110, 490, 460),
        (67, 75, 955, 490),
        (72, 120, 1435, 485),
        (60, 65, 1925, 475),
        (64, 88, 2410, 470),
    ])
    # add some pitch bends
    track = midi.tracks[0]
    track.append(mido.Message("pitchwheel", pitch=0, channel=0, time=0))
    track.append(mido.Message("pitchwheel", pitch=500, channel=0, time=100))
    track.append(mido.Message("pitchwheel", pitch=1000, channel=0, time=100))
    track.append(mido.Message("pitchwheel", pitch=500, channel=0, time=100))
    track.append(mido.Message("pitchwheel", pitch=0, channel=0, time=100))
    p = tmp_path / "expressive.mid"
    midi.save(str(p))
    return p
