"""Shared utility functions for timing conversion, clamping, and MIDI helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import mido

if TYPE_CHECKING:
    from .models import Note, PitchBend

logger = logging.getLogger(__name__)


def clamp(value: int | float, lo: int | float, hi: int | float) -> int | float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def normalize_ticks(tick: int, source_tpb: int, target_tpb: int) -> int:
    """Convert a tick value from one ticks-per-beat resolution to another."""
    if source_tpb == target_tpb:
        return tick
    return int(round(tick * target_tpb / source_tpb))


def seconds_to_ticks(seconds: float, tempo_bpm: float, tpb: int) -> int:
    """Convert wall-clock seconds to MIDI ticks."""
    beats_per_second = tempo_bpm / 60.0
    return int(round(seconds * beats_per_second * tpb))


def ticks_to_seconds(ticks: int, tempo_bpm: float, tpb: int) -> float:
    """Convert MIDI ticks to wall-clock seconds."""
    beats_per_second = tempo_bpm / 60.0
    return ticks / (beats_per_second * tpb)


def extract_notes(track: mido.MidiTrack, channel_filter: int | None = None) -> list["Note"]:
    """
    Extract Note objects with absolute timing from a MIDI track.

    Handles velocity-0 note_on as note_off.  Orphan note_on events
    (no matching note_off) are logged and discarded.
    """
    from .models import Note

    notes: list[Note] = []
    pending: dict[tuple[int, int], tuple[int, int]] = {}  # (pitch, ch) -> (start, vel)
    abs_time = 0

    for msg in track:
        abs_time += msg.time

        if msg.type == "note_on" and msg.velocity > 0:
            if channel_filter is not None and msg.channel != channel_filter:
                continue
            pending[(msg.note, msg.channel)] = (abs_time, msg.velocity)

        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            key = (msg.note, msg.channel)
            if key in pending:
                start, vel = pending.pop(key)
                notes.append(
                    Note(
                        start=start,
                        end=abs_time,
                        pitch=msg.note,
                        velocity=vel,
                        channel=msg.channel,
                    )
                )

    if pending:
        logger.warning("Orphan note_on events (no note_off): %d", len(pending))

    return sorted(notes, key=lambda n: n.start)


def extract_pitch_bends(
    track: mido.MidiTrack, channel_filter: int | None = None
) -> list["PitchBend"]:
    """Extract pitchwheel events with absolute timing."""
    from .models import PitchBend

    bends: list[PitchBend] = []
    abs_time = 0

    for msg in track:
        abs_time += msg.time
        if msg.type == "pitchwheel":
            if channel_filter is not None and msg.channel != channel_filter:
                continue
            bends.append(PitchBend(time=abs_time, pitch=msg.pitch, channel=msg.channel))

    return bends


def get_track_name(track: mido.MidiTrack) -> str:
    """Return the first track_name meta-message value, or ''."""
    for msg in track:
        if msg.type == "track_name":
            return msg.name
    return ""


def note_name(midi_note: int) -> str:
    """Convert MIDI note number to name, e.g. 60 -> 'C4'."""
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    octave = (midi_note // 12) - 1
    return f"{names[midi_note % 12]}{octave}"
