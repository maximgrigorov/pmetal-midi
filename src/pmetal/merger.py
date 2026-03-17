"""Core MIDI merging engine — fuzzy matching, velocity transfer, pitch bend smoothing."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mido
import numpy as np
from scipy.signal import savgol_filter

from .config import AppConfig, MergerConfig, PitchBendConfig
from .exceptions import InvalidMidiError, MergerError, NoMatchesError, TrackNotFoundError
from .models import MatchedPair, MergeResult, Note, PitchBend
from .utils import clamp, extract_notes, extract_pitch_bends, get_track_name, normalize_ticks

if TYPE_CHECKING:
    from .analyzer import AudioFeatures

logger = logging.getLogger(__name__)


class MidiMerger:
    """Merge flat MIDI with expressive MIDI to produce a hybrid output."""

    def __init__(self, config: AppConfig | None = None):
        self.config = config or AppConfig.default()
        self.mc: MergerConfig = self.config.merger
        self.pbc: PitchBendConfig = self.config.pitch_bend
        self.stats: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def merge(
        self,
        flat_midi_path: Path,
        expressive_midi_path: Path,
        output_path: Path,
        target_tracks: list[int] | None = None,
        audio_features: "AudioFeatures | None" = None,
    ) -> MergeResult:
        """
        Run the full merge pipeline.

        Parameters
        ----------
        flat_midi_path : path to the flat (Guitar Pro) MIDI
        expressive_midi_path : path to the expressive (Neural Note) MIDI
        output_path : where to write the hybrid MIDI
        target_tracks : track indices in flat MIDI to process (None = auto-detect guitars)
        audio_features : optional AudioFeatures from analyzer for velocity guidance
        """
        t0 = time.time()
        self.stats = {}

        try:
            # --- load ---
            try:
                flat_midi = mido.MidiFile(str(flat_midi_path))
                expr_midi = mido.MidiFile(str(expressive_midi_path))
            except Exception as e:
                raise InvalidMidiError(f"Cannot parse MIDI: {e}") from e
            flat_tpb = flat_midi.ticks_per_beat
            expr_tpb = expr_midi.ticks_per_beat
            logger.info(
                "Loaded flat MIDI: %d tracks, %d tpb | expressive MIDI: %d tracks, %d tpb",
                len(flat_midi.tracks), flat_tpb, len(expr_midi.tracks), expr_tpb,
            )

            # --- extract expressive notes & bends (usually single-track) ---
            expr_notes: list[Note] = []
            expr_bends: list[PitchBend] = []
            for track in expr_midi.tracks:
                expr_notes.extend(extract_notes(track))
                expr_bends.extend(extract_pitch_bends(track))
            logger.info(
                "Expressive data: %d notes, %d pitch bends", len(expr_notes), len(expr_bends),
            )

            # --- normalise expressive timing to flat tpb ---
            for n in expr_notes:
                n.start = normalize_ticks(n.start, expr_tpb, flat_tpb)
                n.end = normalize_ticks(n.end, expr_tpb, flat_tpb)
            for b in expr_bends:
                b.time = normalize_ticks(b.time, expr_tpb, flat_tpb)

            # --- determine which flat tracks to process ---
            if target_tracks is None:
                target_tracks = self._auto_detect_tracks(flat_midi)
            for tidx in target_tracks:
                if tidx >= len(flat_midi.tracks):
                    raise TrackNotFoundError(
                        f"Track index {tidx} not found (file has {len(flat_midi.tracks)} tracks)"
                    )
            logger.info("Target tracks: %s", target_tracks)

            # --- process each target track ---
            total_matched = 0
            total_clean = 0
            total_bends_out = 0

            for tidx in target_tracks:
                track = flat_midi.tracks[tidx]
                tname = get_track_name(track)
                clean_notes = extract_notes(track)
                if not clean_notes:
                    logger.info("Track %d (%s): no notes, skipping", tidx, tname)
                    continue

                logger.info(
                    "Track %d (%s): %d notes — matching...", tidx, tname, len(clean_notes),
                )

                matched, unmatched = self._match_notes(clean_notes, expr_notes)
                match_rate = len(matched) / len(clean_notes) if clean_notes else 0
                logger.info(
                    "  Matched %d / %d (%.1f%%), unmatched %d",
                    len(matched), len(clean_notes), match_rate * 100, len(unmatched),
                )
                total_matched += len(matched)
                total_clean += len(clean_notes)

                # velocity transfer with per-track range
                track_preset = self._get_track_preset(tname)
                self._transfer_velocities(matched, track_preset)

                # audio-guided velocity if features are available
                if audio_features:
                    from .analyzer import AudioAnalyzer
                    analyzer = AudioAnalyzer(ticks_per_beat=flat_tpb)
                    analyzer._features = audio_features
                    all_clean = [p.clean for p in matched]
                    analyzer.guide_velocity(all_clean)
                    logger.info("  Audio-guided velocity applied")

                vels = [p.clean.velocity for p in matched] if matched else [0]
                logger.info(
                    "  Velocity transfer: range %d–%d, mean %.1f",
                    min(vels), max(vels), np.mean(vels),
                )

                # quantise with humanisation
                processed_notes = self._quantize_with_humanization(
                    clean_notes, matched, flat_tpb,
                )

                # pitch bends — only inside matched notes' duration to avoid "detuned" sound
                # (expressive may have many more notes; bends from wrong notes must not apply)
                track_start = min(n.start for n in clean_notes)
                track_end = max(n.end for n in clean_notes)
                track_bends = [
                    b for b in expr_bends if track_start <= b.time <= track_end
                ]
                if self.pbc.only_inside_matched_notes and matched:
                    matched_spans = [(p.clean.start, p.clean.end) for p in matched]
                    def _inside_matched(t: int) -> bool:
                        return any(s <= t <= e for s, e in matched_spans)
                    track_bends = [b for b in track_bends if _inside_matched(b.time)]
                    logger.info("  Pitch bends inside matched notes: %d", len(track_bends))
                smoothed = self._smooth_pitch_bends(track_bends)
                smoothed = self._clamp_bend_jumps(smoothed)
                logger.info(
                    "  Pitch bends: %d raw → %d smoothed", len(track_bends), len(smoothed),
                )
                total_bends_out += len(smoothed)

                # rebuild the track in-place
                self._rebuild_track(track, processed_notes, smoothed, flat_tpb)

            # --- save ---
            output_path.parent.mkdir(parents=True, exist_ok=True)
            flat_midi.save(str(output_path))

            if total_clean > 0 and total_matched == 0:
                raise NoMatchesError(
                    f"No note matches found across {len(target_tracks)} tracks "
                    f"({total_clean} clean notes, 0 matched)"
                )

            overall_rate = total_matched / total_clean if total_clean else 0
            elapsed = time.time() - t0

            self.stats = {
                "clean_notes": total_clean,
                "matched_notes": total_matched,
                "match_rate": round(overall_rate, 4),
                "pitch_bends_output": total_bends_out,
                "tracks_processed": len(target_tracks),
                "processing_time_s": round(elapsed, 2),
            }
            logger.info(
                "Merge complete in %.2fs — %d/%d matched (%.1f%%), output: %s",
                elapsed, total_matched, total_clean, overall_rate * 100, output_path,
            )

            return MergeResult(output_path=output_path, stats=self.stats, success=True)

        except Exception as e:
            logger.exception("Merge failed")
            return MergeResult(
                output_path=output_path,
                stats=self.stats,
                success=False,
                error_message=str(e),
            )

    # ------------------------------------------------------------------
    # Note matching
    # ------------------------------------------------------------------

    def _match_notes(
        self, clean_notes: list[Note], expr_notes: list[Note],
    ) -> tuple[list[MatchedPair], list[Note]]:
        """Greedy fuzzy matching: for each clean note find the best expressive match."""
        window = self.mc.matching_window_ticks
        pitch_tol = self.mc.pitch_tolerance
        pw = self.mc.scoring_pitch_weight

        expr_sorted = sorted(expr_notes, key=lambda n: n.start)
        used: set[int] = set()
        matched: list[MatchedPair] = []
        unmatched: list[Note] = []

        for cn in clean_notes:
            best: Note | None = None
            best_score = float("inf")
            best_idx = -1

            for idx, en in enumerate(expr_sorted):
                if idx in used:
                    continue
                if en.start > cn.start + window:
                    break
                td = abs(cn.start - en.start)
                if td > window:
                    continue
                pd = abs(cn.pitch - en.pitch)
                if pd > pitch_tol:
                    continue
                score = td + pd * pw
                if score < best_score:
                    best_score = score
                    best = en
                    best_idx = idx

            if best is not None:
                matched.append(
                    MatchedPair(
                        clean=cn,
                        expressive=best,
                        time_offset=best.start - cn.start,
                        score=best_score,
                    )
                )
                used.add(best_idx)
            else:
                unmatched.append(cn)

        return matched, unmatched

    # ------------------------------------------------------------------
    # Velocity
    # ------------------------------------------------------------------

    def _transfer_velocities(
        self, matched: list[MatchedPair], track_preset: dict | None = None
    ) -> None:
        boost = self.mc.velocity_boost
        vmin = self.mc.velocity_min
        vel_lo, vel_hi = 1, 127
        if track_preset and "velocity_range" in track_preset:
            vel_lo, vel_hi = track_preset["velocity_range"]
        for pair in matched:
            new_vel = int(round(pair.expressive.velocity * boost))
            new_vel = int(clamp(new_vel, max(vmin, vel_lo), vel_hi))
            pair.clean.velocity = new_vel

    def _get_track_preset(self, track_name: str) -> dict | None:
        """Return the matching track preset for *track_name*, or None."""
        for preset in self.config.tracks.values():
            if preset.pattern and re.search(preset.pattern, track_name, re.IGNORECASE):
                return {"velocity_range": preset.velocity_range}
        return None

    # ------------------------------------------------------------------
    # Pitch bend smoothing
    # ------------------------------------------------------------------

    def _smooth_pitch_bends(self, bends: list[PitchBend]) -> list[PitchBend]:
        if len(bends) < max(3, self.pbc.window_size):
            return bends

        values = np.array([b.pitch for b in bends], dtype=float)

        if self.pbc.smoothing_algorithm == "savgol":
            ws = self.pbc.window_size if self.pbc.window_size % 2 == 1 else self.pbc.window_size + 1
            ws = min(ws, len(values))
            if ws % 2 == 0:
                ws -= 1
            po = min(self.pbc.polynomial_order, ws - 1)
            smoothed = savgol_filter(values, window_length=ws, polyorder=po, mode="nearest")
        else:
            kernel = np.ones(self.pbc.window_size) / self.pbc.window_size
            smoothed = np.convolve(values, kernel, mode="same")

        # rebuild with clamped values
        result: list[PitchBend] = []
        for i, b in enumerate(bends):
            result.append(PitchBend(
                time=b.time,
                pitch=int(np.clip(smoothed[i], -8192, 8191)),
                channel=b.channel,
            ))

        # redundancy filter
        return self._filter_redundant(result)

    def _filter_redundant(self, bends: list[PitchBend]) -> list[PitchBend]:
        if not bends:
            return bends
        threshold = self.pbc.redundancy_threshold
        gap_thresh = self.pbc.time_gap_threshold
        filtered = [bends[0]]
        for b in bends[1:]:
            dp = abs(b.pitch - filtered[-1].pitch)
            dt = b.time - filtered[-1].time
            if dp >= threshold or dt >= gap_thresh:
                filtered.append(b)
        return filtered

    def _clamp_bend_jumps(self, bends: list[PitchBend]) -> list[PitchBend]:
        """Limit consecutive pitch bend difference to avoid 'detuned' spikes."""
        if len(bends) < 2:
            return bends
        cap = getattr(self.pbc, "max_jump_clamp", 2500)
        out: list[PitchBend] = [bends[0]]
        for b in bends[1:]:
            prev = out[-1].pitch
            delta = b.pitch - prev
            if abs(delta) > cap:
                new_pitch = prev + (cap if delta > 0 else -cap)
                new_pitch = int(np.clip(new_pitch, -8192, 8191))
                b = PitchBend(time=b.time, pitch=new_pitch, channel=b.channel)
            out.append(b)
        return out

    # ------------------------------------------------------------------
    # Quantisation + humanisation
    # ------------------------------------------------------------------

    def _quantize_with_humanization(
        self,
        clean_notes: list[Note],
        matched: list[MatchedPair],
        tpb: int,
    ) -> list[Note]:
        grid = tpb * 4 // self.mc.quantize_division
        hmax = self.mc.humanize_max_ticks

        offset_map: dict[tuple[int, int], int] = {}
        for pair in matched:
            offset_map[(pair.clean.pitch, pair.clean.start)] = pair.time_offset

        out: list[Note] = []
        min_dur = max(1, grid // 4)  # at least 1 tick, or 1/4 grid
        for n in clean_notes:
            quantized = round(n.start / grid) * grid
            offset = offset_map.get((n.pitch, n.start), 0)
            humanize = int(clamp(offset, -hmax, hmax))
            new_start = max(0, int(quantized + humanize))
            dur = max(min_dur, n.duration)
            out.append(Note(
                start=new_start,
                end=new_start + dur,
                pitch=n.pitch,
                velocity=n.velocity,
                channel=n.channel,
            ))
        return out

    # ------------------------------------------------------------------
    # Track rebuild
    # ------------------------------------------------------------------

    @staticmethod
    def _rebuild_track(
        track: mido.MidiTrack,
        notes: list[Note],
        bends: list[PitchBend],
        tpb: int,
    ) -> None:
        """Replace note and pitchwheel events in *track*, keeping meta events."""
        meta_events: list[mido.Message] = []
        for msg in track:
            if msg.is_meta or msg.type in ("control_change", "program_change"):
                meta_events.append(msg)

        # build absolute-time event list
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

        # convert to delta time
        track.clear()
        abs_time = 0
        # re-add meta events first (they usually sit at tick 0)
        for m in meta_events:
            track.append(m)

        for abs_t, msg in events:
            msg.time = abs_t - abs_time
            abs_time = abs_t
            track.append(msg)

    # ------------------------------------------------------------------
    # Track auto-detection
    # ------------------------------------------------------------------

    def _auto_detect_tracks(self, midi: mido.MidiFile) -> list[int]:
        """Find guitar / bass tracks by name pattern."""
        import re

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
                logger.info("Auto-detected track %d: %s", i, name)

        if not indices:
            logger.warning("No guitar/bass tracks detected — processing all tracks with notes")
            for i, track in enumerate(midi.tracks):
                if extract_notes(track):
                    indices.append(i)
        return indices
