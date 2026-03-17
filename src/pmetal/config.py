"""Configuration management — YAML loading and pydantic validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class MergerConfig(BaseModel):
    matching_window_ticks: int = Field(120, ge=60, le=240)
    pitch_tolerance: int = Field(4, ge=1, le=12)
    velocity_boost: float = Field(1.2, ge=0.5, le=2.0)
    velocity_min: int = Field(30, ge=1, le=80)
    quantize_division: int = Field(16, ge=4, le=64)
    humanize_max_ticks: int = Field(20, ge=0, le=60)
    scoring_pitch_weight: int = Field(50, ge=10, le=200)


class PitchBendConfig(BaseModel):
    smoothing_algorithm: str = "savgol"
    window_size: int = Field(5, ge=3, le=21)
    polynomial_order: int = Field(2, ge=1, le=5)
    redundancy_threshold: int = Field(100, ge=20, le=500)
    time_gap_threshold: int = Field(48, ge=12, le=240)
    # Only keep bends that fall inside a matched note's duration (avoids "detuned" sound
    # when expressive has many more notes and bends from wrong notes get applied).
    only_inside_matched_notes: bool = True
    # Clamp consecutive bend difference to this (raw pitch wheel units) to avoid huge jumps.
    max_jump_clamp: int = Field(2500, ge=500, le=8192)


class QualityConfig(BaseModel):
    min_overall_score: float = Field(0.70, ge=0.0, le=1.0)
    min_match_rate: float = Field(0.50, ge=0.0, le=1.0)
    min_velocity_range: int = Field(40, ge=10, le=100)
    max_stuck_notes: int = Field(0, ge=0)
    max_pitch_bend_jump: int = Field(1000, ge=100, le=8000)
    max_retries: int = Field(3, ge=0, le=10)


class TrackPreset(BaseModel):
    pattern: str = ""
    low_cutoff: int | None = None
    velocity_range: list[int] = Field(default_factory=lambda: [60, 100])


class AppConfig(BaseModel):
    """Top-level application configuration."""

    version: str = "2.0"
    merger: MergerConfig = MergerConfig()
    pitch_bend: PitchBendConfig = PitchBendConfig()
    quality: QualityConfig = QualityConfig()
    tracks: dict[str, TrackPreset] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        tracks_raw = data.pop("tracks", {})
        tracks = {k: TrackPreset(**v) for k, v in tracks_raw.items()}
        return cls(tracks=tracks, **data)

    @classmethod
    def default(cls) -> "AppConfig":
        """Load the bundled default config."""
        default_path = Path(__file__).resolve().parent.parent.parent / "config" / "default.yaml"
        if default_path.exists():
            return cls.load(default_path)
        return cls()

    def dump_yaml(self) -> str:
        return yaml.dump(self.model_dump(), default_flow_style=False, sort_keys=False)

    def merged_with(self, overrides: dict[str, Any]) -> "AppConfig":
        """Return a copy with selected fields overridden."""
        data = self.model_dump()
        for key, value in overrides.items():
            parts = key.split(".")
            d = data
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = value
        return AppConfig(**data)
