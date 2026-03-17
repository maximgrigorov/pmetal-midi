"""Post-processing passes applied after the merge: stuck-note fixer and pitch-bend smoother."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import mido

if TYPE_CHECKING:
    from .models import Note, PitchBend

logger = logging.getLogger(__name__)


# ── Task 3: Stuck-note fixer ─────────────────────────────────────────

def fix_stuck_notes(track: mido.MidiTrack) -> int:
    """Scan *track* for unclosed note_on events and insert note_off at track end.

    Returns the number of stuck notes fixed.
    """
    abs_time = 0
    track_end = 0
    open_notes: dict[tuple[int, int], int] = {}  # (pitch, channel) -> tick

    for msg in track:
        abs_time += msg.time
        track_end = max(track_end, abs_time)

        if msg.type == "note_on" and msg.velocity > 0:
            open_notes[(msg.note, msg.channel)] = abs_time
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            open_notes.pop((msg.note, msg.channel), None)

    if not open_notes:
        return 0

    logger.warning("Fixing %d stuck notes at tick %d", len(open_notes), track_end)
    for (pitch, channel) in sorted(open_notes):
        track.append(mido.Message(
            "note_off", note=pitch, velocity=0, channel=channel, time=0,
        ))

    return len(open_notes)


# ── Task 4: Pitch-bend transition smoother ───────────────────────────

DEFAULT_FADE_TICKS = 40
DEFAULT_SMOOTH_THRESHOLD = 500


def smooth_bend_transitions(
    bends: list["PitchBend"],
    notes: list["Note"],
    tpb: int,
    fade_ticks: int | None = None,
    threshold: int | None = None,
) -> list["PitchBend"]:
    """Insert linear fade-to-zero between notes when consecutive bends jump too far.

    For each gap between note_i.end and note_{i+1}.start, if the last bend
    before the gap differs from 0 by more than *threshold*, insert a short
    linear ramp back to 0 over *fade_ticks*.

    Parameters
    ----------
    bends : sorted list of PitchBend events
    notes : sorted list of notes (by start time)
    tpb : ticks per beat (used to compute default fade length)
    fade_ticks : duration of the fade ramp; defaults to ~20 ms at 120 BPM
    threshold : minimum |bend| to trigger a fade; defaults to 500

    Returns a new list of PitchBend events including the inserted fades.
    """
    from .models import PitchBend as PB

    if not bends or len(notes) < 2:
        return bends

    if fade_ticks is None:
        fade_ticks = max(10, tpb // 12)
    if threshold is None:
        threshold = DEFAULT_SMOOTH_THRESHOLD

    sorted_notes = sorted(notes, key=lambda n: n.start)
    ch = bends[0].channel if bends else 0

    gaps: list[tuple[int, int]] = []
    for i in range(len(sorted_notes) - 1):
        gap_start = sorted_notes[i].end
        gap_end = sorted_notes[i + 1].start
        if gap_end > gap_start:
            gaps.append((gap_start, gap_end))

    extra_bends: list[PB] = []
    bend_by_time = {b.time: b.pitch for b in bends}

    for gap_start, gap_end in gaps:
        last_val = 0
        for b in reversed(bends):
            if b.time <= gap_start:
                last_val = b.pitch
                break

        if abs(last_val) < threshold:
            continue

        actual_fade = min(fade_ticks, gap_end - gap_start - 1)
        if actual_fade < 2:
            continue

        steps = min(4, actual_fade)
        for s in range(1, steps + 1):
            t = gap_start + (actual_fade * s // steps)
            val = int(last_val * (1 - s / steps))
            if t not in bend_by_time:
                extra_bends.append(PB(time=t, pitch=val, channel=ch))

    if not extra_bends:
        return bends

    merged = list(bends) + extra_bends
    merged.sort(key=lambda b: b.time)
    logger.debug("Inserted %d fade-to-zero bends in note gaps", len(extra_bends))
    return merged
