"""Articulation overlay — transfer velocity, pitch bend, and micro-timing
from expressive MIDI onto GP8 notes WITHOUT changing note structure.

Key invariant: output note count == flat_midi note count. No notes added or removed.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import mido
import numpy as np

from .config import AppConfig, MergerConfig, PitchBendConfig
from .models import MergeResult, Note, PitchBend
from .postprocess import fix_stuck_notes, smooth_bend_transitions
from .utils import clamp, extract_notes, extract_pitch_bends, get_track_name, normalize_ticks

logger = logging.getLogger(__name__)


class ArticulationOverlay:
    """Overlay expression from Neural Note MIDI onto Guitar Pro 8 notes.

    GP8 notes are the source of truth: their pitch, duration, and count
    are never altered. From the NN transcription we take velocity, pitch
    bend events, and micro-timing of the attack.
    """

    def __init__(self, config: AppConfig | None = None):
        self.config = config or AppConfig.default()
        self.mc: MergerConfig = self.config.merger
        self.pbc: PitchBendConfig = self.config.pitch_bend
        self.stats: dict[str, Any] = {}

    def merge(
        self,
        flat_midi_path: Path,
        expressive_midi_path: Path,
        output_path: Path,
        target_tracks: list[int] | None = None,
    ) -> MergeResult:
        t0 = time.time()
        self.stats = {}

        try:
            flat_midi = mido.MidiFile(str(flat_midi_path))
            expr_midi = mido.MidiFile(str(expressive_midi_path))
        except Exception as e:
            return MergeResult(output_path=output_path, error_message=f"Cannot parse MIDI: {e}")

        flat_tpb = flat_midi.ticks_per_beat
        expr_tpb = expr_midi.ticks_per_beat

        expr_notes: list[Note] = []
        expr_bends: list[PitchBend] = []
        for track in expr_midi.tracks:
            expr_notes.extend(extract_notes(track))
            expr_bends.extend(extract_pitch_bends(track))

        for n in expr_notes:
            n.start = normalize_ticks(n.start, expr_tpb, flat_tpb)
            n.end = normalize_ticks(n.end, expr_tpb, flat_tpb)
        for b in expr_bends:
            b.time = normalize_ticks(b.time, expr_tpb, flat_tpb)

        expr_notes_sorted = sorted(expr_notes, key=lambda n: n.start)
        expr_bends_sorted = sorted(expr_bends, key=lambda b: b.time)

        logger.info(
            "Overlay: flat=%d tracks/%d tpb, expr=%d notes/%d bends",
            len(flat_midi.tracks), flat_tpb, len(expr_notes), len(expr_bends),
        )

        if target_tracks is None:
            target_tracks = self._auto_detect_tracks(flat_midi)
        for tidx in target_tracks:
            if tidx >= len(flat_midi.tracks):
                return MergeResult(
                    output_path=output_path,
                    error_message=f"Track {tidx} not found (file has {len(flat_midi.tracks)} tracks)",
                )

        total_gp8 = 0
        total_articulated = 0
        total_bends_out = 0

        for tidx in target_tracks:
            track = flat_midi.tracks[tidx]
            tname = get_track_name(track)
            gp8_notes = extract_notes(track)
            if not gp8_notes:
                logger.info("Track %d (%s): no notes, skip", tidx, tname)
                continue

            total_gp8 += len(gp8_notes)
            logger.info("Track %d (%s): %d GP8 notes", tidx, tname, len(gp8_notes))

            articulated_notes: list[Note] = []
            note_bends: list[PitchBend] = []
            articulated_count = 0

            for gp_note in gp8_notes:
                match = self._find_nearest(gp_note, expr_notes_sorted)

                if match is not None:
                    articulated_count += 1

                    new_vel = int(round(match.velocity * self.mc.velocity_boost))
                    new_vel = int(clamp(new_vel, self.mc.velocity_min, 127))

                    attack_delta = match.start - gp_note.start
                    humanize = int(clamp(attack_delta, -self.mc.humanize_max_ticks, self.mc.humanize_max_ticks))
                    new_start = max(0, gp_note.start + humanize)

                    articulated_notes.append(Note(
                        start=new_start,
                        end=new_start + gp_note.duration,
                        pitch=gp_note.pitch,
                        velocity=new_vel,
                        channel=gp_note.channel,
                    ))

                    bends_in_note = [
                        b for b in expr_bends_sorted
                        if gp_note.start - 10 <= b.time <= gp_note.end + 10
                    ]
                    note_bends.extend(bends_in_note)
                else:
                    articulated_notes.append(Note(
                        start=gp_note.start,
                        end=gp_note.end,
                        pitch=gp_note.pitch,
                        velocity=64,
                        channel=gp_note.channel,
                    ))

            assert len(articulated_notes) == len(gp8_notes), "Invariant violated: note count changed"
            total_articulated += articulated_count

            unique_bends = list({(b.time, b.pitch, b.channel): b for b in note_bends}.values())
            unique_bends.sort(key=lambda b: b.time)
            smoothed_bends = self._smooth_and_clamp(unique_bends)
            smoothed_bends = smooth_bend_transitions(smoothed_bends, articulated_notes, flat_tpb)
            total_bends_out += len(smoothed_bends)

            logger.info(
                "  Articulated %d/%d (%.1f%%), bends: %d",
                articulated_count, len(gp8_notes),
                articulated_count / len(gp8_notes) * 100 if gp8_notes else 0,
                len(smoothed_bends),
            )

            self._rebuild_track(track, articulated_notes, smoothed_bends, flat_tpb)
            fix_stuck_notes(track)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        flat_midi.save(str(output_path))

        coverage = total_articulated / total_gp8 if total_gp8 else 0
        elapsed = time.time() - t0

        self.stats = {
            "mode": "overlay",
            "gp8_notes": total_gp8,
            "articulated_notes": total_articulated,
            "articulation_coverage": round(coverage, 4),
            "pitch_bends_output": total_bends_out,
            "tracks_processed": len(target_tracks),
            "processing_time_s": round(elapsed, 2),
            "clean_notes": total_gp8,
            "matched_notes": total_articulated,
            "match_rate": round(coverage, 4),
        }
        logger.info(
            "Overlay complete in %.2fs — %d/%d articulated (%.1f%%), output: %s",
            elapsed, total_articulated, total_gp8, coverage * 100, output_path,
        )
        return MergeResult(output_path=output_path, stats=self.stats, success=True)

    # ── matching ──────────────────────────────────────────────────────

    def _find_nearest(self, gp_note: Note, expr_sorted: list[Note]) -> Note | None:
        """Find nearest expressive note within time and pitch tolerance."""
        window = self.mc.matching_window_ticks
        pitch_tol = self.mc.pitch_tolerance
        best: Note | None = None
        best_dist = float("inf")

        for en in expr_sorted:
            if en.start > gp_note.start + window:
                break
            td = abs(gp_note.start - en.start)
            if td > window:
                continue
            pd = abs(gp_note.pitch - en.pitch)
            if pd > pitch_tol:
                continue
            dist = td + pd * 50
            if dist < best_dist:
                best_dist = dist
                best = en
        return best

    # ── pitch bend processing ─────────────────────────────────────────

    def _smooth_and_clamp(self, bends: list[PitchBend]) -> list[PitchBend]:
        """Savitzky-Golay smoothing + consecutive-jump clamping + redundancy filter."""
        from scipy.signal import savgol_filter

        if len(bends) < max(3, self.pbc.window_size):
            return bends

        values = np.array([b.pitch for b in bends], dtype=float)
        ws = self.pbc.window_size if self.pbc.window_size % 2 == 1 else self.pbc.window_size + 1
        ws = min(ws, len(values))
        if ws % 2 == 0:
            ws -= 1
        po = min(self.pbc.polynomial_order, ws - 1)
        smoothed = savgol_filter(values, window_length=ws, polyorder=po, mode="nearest")

        cap = self.pbc.max_jump_clamp
        result: list[PitchBend] = []
        for i, b in enumerate(bends):
            val = int(np.clip(smoothed[i], -8192, 8191))
            if result:
                delta = val - result[-1].pitch
                if abs(delta) > cap:
                    val = result[-1].pitch + (cap if delta > 0 else -cap)
                    val = int(np.clip(val, -8192, 8191))
            result.append(PitchBend(time=b.time, pitch=val, channel=b.channel))

        threshold = self.pbc.redundancy_threshold
        gap_thresh = self.pbc.time_gap_threshold
        filtered = [result[0]] if result else []
        for b in result[1:]:
            if abs(b.pitch - filtered[-1].pitch) >= threshold or (b.time - filtered[-1].time) >= gap_thresh:
                filtered.append(b)
        return filtered

    # ── auto-detect & rebuild ─────────────────────────────────────────

    def _auto_detect_tracks(self, midi: mido.MidiFile) -> list[int]:
        patterns = []
        for preset in self.config.tracks.values():
            if preset.pattern:
                patterns.append(re.compile(preset.pattern, re.IGNORECASE))
        if not patterns:
            patterns = [re.compile(r"guitar|bass|lead|rhythm|solo|steel", re.IGNORECASE)]
        indices: list[int] = []
        for i, track in enumerate(midi.tracks):
            name = get_track_name(track)
            if any(p.search(name) for p in patterns):
                indices.append(i)
        if not indices:
            for i, track in enumerate(midi.tracks):
                if extract_notes(track):
                    indices.append(i)
        return indices

    @staticmethod
    def _rebuild_track(
        track: mido.MidiTrack,
        notes: list[Note],
        bends: list[PitchBend],
        tpb: int,
    ) -> None:
        meta_events: list[mido.Message] = []
        for msg in track:
            if msg.is_meta or msg.type in ("control_change", "program_change"):
                meta_events.append(msg)

        events: list[tuple[int, mido.Message]] = []
        for n in notes:
            events.append((n.start, mido.Message(
                "note_on", note=n.pitch, velocity=n.velocity, channel=n.channel, time=0,
            )))
            events.append((n.end, mido.Message(
                "note_off", note=n.pitch, velocity=0, channel=n.channel, time=0,
            )))
        for b in bends:
            events.append((b.time, mido.Message(
                "pitchwheel", pitch=b.pitch, channel=b.channel, time=0,
            )))
        events.sort(key=lambda x: x[0])

        track.clear()
        abs_time = 0
        for m in meta_events:
            track.append(m)
        for abs_t, msg in events:
            msg.time = abs_t - abs_time
            abs_time = abs_t
            track.append(msg)
