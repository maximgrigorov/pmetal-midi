"""Custom exception hierarchy for pmetal-midi."""

from __future__ import annotations


class PmetalError(Exception):
    """Base exception for all pmetal-midi errors."""


class MergerError(PmetalError):
    """Error during MIDI merge operation."""


class InvalidMidiError(MergerError):
    """MIDI file is invalid or cannot be parsed."""


class NoMatchesError(MergerError):
    """No note matches found between flat and expressive MIDI."""


class TrackNotFoundError(MergerError):
    """Requested track index does not exist."""


class TimingMismatchError(MergerError):
    """Timing mismatch between flat and expressive MIDI that cannot be resolved."""


class AnalyzerError(PmetalError):
    """Error during audio analysis."""


class AudioLoadError(AnalyzerError):
    """Failed to load or decode audio file."""


class QualityError(PmetalError):
    """Error during quality analysis."""


class WorkflowError(PmetalError):
    """Error in workflow orchestration."""

    def __init__(self, message: str, state: str | None = None):
        super().__init__(message)
        self.state = state


class SecurityError(PmetalError):
    """Path or input validation failure."""
