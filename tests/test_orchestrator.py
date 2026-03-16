"""Tests for the orchestrator workflow."""

from __future__ import annotations

from pathlib import Path

from pmetal.orchestrator import MidiOrchestrator, ProcessingState, WorkflowConfig


class TestOrchestrator:
    def test_full_workflow(self, flat_midi_path: Path, expressive_midi_path: Path, tmp_path: Path):
        wf = WorkflowConfig(
            flat_midi_path=flat_midi_path,
            expressive_midi_path=expressive_midi_path,
            output_dir=tmp_path / "out",
            auto_retry=False,
        )
        orch = MidiOrchestrator(wf)
        result = orch.run()
        assert result.state == ProcessingState.DONE
        assert result.output_path is not None
        assert result.output_path.exists()
        assert result.quality_score > 0

    def test_missing_file_error(self, tmp_path: Path):
        wf = WorkflowConfig(
            flat_midi_path=tmp_path / "nonexistent.mid",
            expressive_midi_path=tmp_path / "also_missing.mid",
            output_dir=tmp_path / "out",
        )
        orch = MidiOrchestrator(wf)
        result = orch.run()
        assert result.state == ProcessingState.ERROR
        assert len(result.errors) > 0

    def test_log_lines_populated(self, flat_midi_path: Path, expressive_midi_path: Path, tmp_path: Path):
        wf = WorkflowConfig(
            flat_midi_path=flat_midi_path,
            expressive_midi_path=expressive_midi_path,
            output_dir=tmp_path / "out",
            auto_retry=False,
        )
        orch = MidiOrchestrator(wf)
        result = orch.run()
        assert len(result.log_lines) >= 4  # at least INIT, ANALYZE, MERGE, QUALITY
