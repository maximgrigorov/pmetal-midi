"""Core data structures for MIDI processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Note:
    """A single MIDI note with absolute tick timing."""

    start: int
    end: int
    pitch: int
    velocity: int
    channel: int = 0

    @property
    def duration(self) -> int:
        return self.end - self.start

    def __repr__(self) -> str:
        return (
            f"Note(pitch={self.pitch}, vel={self.velocity}, "
            f"start={self.start}, dur={self.duration}, ch={self.channel})"
        )


@dataclass
class PitchBend:
    """A pitch-wheel event."""

    time: int
    pitch: int  # -8192 .. 8191
    channel: int = 0


@dataclass
class MatchedPair:
    """A clean note matched with its expressive counterpart."""

    clean: Note
    expressive: Note
    time_offset: int  # expressive.start - clean.start (after normalisation)
    score: float


@dataclass
class MergeResult:
    """Outcome of a single merge operation."""

    output_path: Path | None
    stats: dict[str, Any] = field(default_factory=dict)
    quality_metrics: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error_message: str | None = None


@dataclass
class TrackInfo:
    """Metadata about a MIDI track."""

    index: int
    name: str
    note_count: int
    channel: int | None = None
