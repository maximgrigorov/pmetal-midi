"""Tests for the quality analyser."""

from __future__ import annotations

from pathlib import Path

from pmetal.quality_analyzer import QualityAnalyzer


class TestQualityAnalyzer:
    def test_analyze_valid_midi(self, flat_midi_path: Path):
        qa = QualityAnalyzer()
        report = qa.analyze(flat_midi_path)
        assert 0.0 <= report.overall_score <= 1.0
        assert "density" in report.metrics
        assert "velocity_range" in report.metrics

    def test_report_to_json(self, flat_midi_path: Path):
        qa = QualityAnalyzer()
        report = qa.analyze(flat_midi_path)
        j = report.to_json()
        import json
        data = json.loads(j)
        assert "overall_score" in data

    def test_report_summary(self, flat_midi_path: Path):
        qa = QualityAnalyzer()
        report = qa.analyze(flat_midi_path)
        summary = report.to_summary()
        assert "Quality Analysis" in summary
