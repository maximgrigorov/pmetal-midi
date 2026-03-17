"""Tests for the quality analyser."""

from __future__ import annotations

from pathlib import Path

from pmetal.quality_analyzer import QualityAnalyzer


class TestQualityAnalyzer:
    def test_analyze_overlay_mode(self, flat_midi_path: Path):
        qa = QualityAnalyzer()
        report = qa.analyze(flat_midi_path, mode="overlay")
        assert 0.0 <= report.overall_score <= 1.0
        assert "velocity_range" in report.metrics
        assert "articulation_coverage" in report.metrics
        assert report.mode == "overlay"

    def test_analyze_replacer_mode(self, flat_midi_path: Path):
        qa = QualityAnalyzer()
        report = qa.analyze(flat_midi_path, mode="replacer")
        assert 0.0 <= report.overall_score <= 1.0
        assert "density" in report.metrics
        assert "match_rate" in report.metrics
        assert report.mode == "replacer"

    def test_report_to_json(self, flat_midi_path: Path):
        qa = QualityAnalyzer()
        report = qa.analyze(flat_midi_path, mode="overlay")
        j = report.to_json()
        import json
        data = json.loads(j)
        assert "overall_score" in data
        assert "articulation_coverage" in data

    def test_report_summary(self, flat_midi_path: Path):
        qa = QualityAnalyzer()
        report = qa.analyze(flat_midi_path)
        summary = report.to_summary()
        assert "Quality Analysis" in summary
