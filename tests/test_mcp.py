"""Tests for MCP server tools (unit-level, no actual MCP transport)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _bypass_validate_path(path_str: str) -> Path:
    """Bypass security for tests running in tmp dirs."""
    return Path(path_str).resolve()


class TestMcpTools:
    """Test MCP tool functions directly (no MCP protocol)."""

    def test_get_status(self):
        from pmetal.mcp_server import get_status
        result = json.loads(get_status())
        assert result["service"] == "pmetal-midi"
        assert result["version"] == "2.0.0"
        assert "python" in result

    def test_list_files_empty(self, tmp_path: Path):
        with patch("pmetal.mcp_server.DATA_DIR", tmp_path):
            (tmp_path / "input").mkdir()
            (tmp_path / "output").mkdir()
            from pmetal.mcp_server import list_files
            result = json.loads(list_files())
            assert "input" in result
            assert "output" in result

    def test_midi_info(self, flat_midi_path: Path):
        with patch("pmetal.mcp_server.validate_path", _bypass_validate_path):
            from pmetal.mcp_server import midi_info
            result = json.loads(midi_info(str(flat_midi_path)))
            assert result["num_tracks"] >= 1
            assert len(result["tracks"]) >= 1

    def test_analyze_quality(self, flat_midi_path: Path):
        with patch("pmetal.mcp_server.validate_path", _bypass_validate_path):
            from pmetal.mcp_server import analyze_quality
            result = json.loads(analyze_quality(str(flat_midi_path)))
            assert "overall_score" in result
            assert "metrics" in result

    def test_merge_midi(self, flat_midi_path: Path, expressive_midi_path: Path, tmp_path: Path):
        with patch("pmetal.mcp_server.validate_path", _bypass_validate_path):
            from pmetal.mcp_server import merge_midi
            out = tmp_path / "output"
            out.mkdir()
            result = json.loads(merge_midi(
                str(flat_midi_path), str(expressive_midi_path), str(out),
            ))
            assert result["success"] is True
            assert result["quality_score"] > 0

    def test_extract_track(self, flat_midi_path: Path, tmp_path: Path):
        with (
            patch("pmetal.mcp_server.validate_path", _bypass_validate_path),
            patch("pmetal.mcp_server.DATA_DIR", tmp_path),
        ):
            (tmp_path / "input").mkdir(exist_ok=True)
            from pmetal.mcp_server import extract_track
            result = json.loads(extract_track(str(flat_midi_path), 0))
            assert result["success"] is True
            assert result["note_count"] >= 0
            assert Path(result["output_path"]).exists()

    def test_extract_track_invalid_index(self, flat_midi_path: Path, tmp_path: Path):
        with patch("pmetal.mcp_server.validate_path", _bypass_validate_path):
            from pmetal.mcp_server import extract_track
            result = json.loads(extract_track(str(flat_midi_path), 999))
            assert "error" in result

    def test_eq_filter(self, tmp_path: Path):
        import numpy as np
        import soundfile as sf

        wav_path = tmp_path / "test_guitar.wav"
        sr = 22050
        duration = 2.0
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        signal = np.sin(2 * np.pi * 440 * t) * 0.5
        sf.write(str(wav_path), signal, sr)

        with (
            patch("pmetal.mcp_server.validate_path", _bypass_validate_path),
            patch("pmetal.mcp_server.DATA_DIR", tmp_path),
        ):
            (tmp_path / "input").mkdir(exist_ok=True)
            from pmetal.mcp_server import eq_filter
            result = json.loads(eq_filter(str(wav_path), preset="solo_guitar"))
            assert result["success"] is True
            assert result["preset"] == "solo_guitar"
            assert Path(result["output_path"]).exists()

    def test_eq_filter_invalid_preset(self, tmp_path: Path):
        import numpy as np
        import soundfile as sf

        wav_path = tmp_path / "test.wav"
        sf.write(str(wav_path), np.zeros(1000), 22050)

        with patch("pmetal.mcp_server.validate_path", _bypass_validate_path):
            from pmetal.mcp_server import eq_filter
            result = json.loads(eq_filter(str(wav_path), preset="nonexistent"))
            assert "error" in result

    def test_get_processing_log_no_file(self, tmp_path: Path):
        with patch("pmetal.mcp_server.DATA_DIR", tmp_path):
            from pmetal.mcp_server import get_processing_log
            result = get_processing_log()
            assert "No processing log" in result


class TestSecurity:
    def test_validate_path_allowed(self):
        from pmetal.security import validate_path
        p = validate_path("/data/input/test.mid")
        assert str(p).endswith("test.mid")

    def test_validate_path_blocked(self):
        from pmetal.security import SecurityError, validate_path
        with pytest.raises(SecurityError):
            validate_path("/etc/passwd")

    def test_rate_limiter(self):
        from pmetal.security import RateLimiter
        rl = RateLimiter(max_calls=3, period_seconds=60)
        assert rl.allow()
        assert rl.allow()
        assert rl.allow()
        assert not rl.allow()
