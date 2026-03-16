# Usage Guide

## Overview

The pmetal-midi system processes Power Metal ballads from Suno by merging:
- **Flat MIDI** (from Guitar Pro 8, via Klang.io transcription) — clean notes, no expression
- **Expressive MIDI** (from Neural Note audio transcription) — velocity dynamics, pitch bends

The result is a **hybrid MIDI** with correct notes AND natural expression, ready for
Shreddage 3.5 Hydra / Darkwall or similar VSTs.

## Workflow

### Step 1: Prepare Input Files

1. **From Suno**: Download the song and stems (WAV)
2. **Klang.io / Basic Pitch**: Transcribe the full mix to get structure → import into Guitar Pro 8
3. **Guitar Pro 8**: Clean up the transcription, fix notes → export as MIDI (`song_flat.mid`)
4. **Neural Note / audio-to-midi**: Transcribe the guitar stem → get `song_neural.mid`

### Step 2: Upload to Server

```bash
make upload SRC=./song_flat.mid
make upload SRC=./song_neural.mid
```

Files go to `/home/mgrigorov/pmetal-data/input/` on alma.

### Step 3: Process with Claude Desktop

Open Claude Desktop and ask:

> "List the MIDI files available for processing"

Claude will use the `list_files` tool and show what's in the input directory.

> "Merge song_flat.mid with song_neural.mid"

Claude will call `merge_midi` and show progress:
```
[INIT] Loading configuration
[ANALYZE] Validating input files
  song_flat.mid (12.3 KB)
  song_neural.mid (8.7 KB)
[MERGE] Starting merge
  Track 6 (Solo Guitar): 342 notes — matching...
    Matched 267 / 342 (78.1%), unmatched 75
    Velocity transfer: range 34–127, mean 87.2
    Pitch bends: 183 raw → 96 smoothed
  Track 7 (Rhythm Guitar L): 890 notes — matching...
    ...
[QUALITY] Score 0.79 — PASS
[DONE] Output: /data/output/song_hybrid.mid
```

### Step 4: Review Quality

> "Analyze the quality of the output file"

Claude will show a detailed quality report with metrics:
- Density score
- Pitch bend continuity
- Velocity range
- Timing consistency

If quality is low, Claude will suggest parameter adjustments.

### Step 5: Download Result

```bash
make download FILE=output/song_hybrid.mid
```

Import the hybrid MIDI into your DAW (Logic Pro, etc.) with Shreddage 3.5.

## Claude Desktop Commands

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `merge_midi` | Merge flat + expressive MIDI files |
| `analyze_quality` | Analyze MIDI quality metrics |
| `midi_info` | Show MIDI file details (tracks, notes) |
| `list_files` | Browse files on the server |
| `get_status` | Server status and health |

### Example Conversations

**Basic merge:**
> "Merge /data/input/princess_flat.mid with /data/input/princess_neural.mid"

**Custom config:**
> "Merge the files but use a wider matching window of 180 ticks and velocity boost of 1.3"

**Inspect before merge:**
> "Show me the track info for /data/input/princess_flat.mid"

**Re-process with adjustments:**
> "The match rate was low. Re-merge with matching_window_ticks=200 and pitch_tolerance=6"

## CLI Usage (Alternative)

The system also provides a CLI for direct use:

```bash
# Inside the container
pmetal merge /data/input/flat.mid /data/input/expr.mid -o /data/output/

# Analyze quality
pmetal analyze /data/output/hybrid.mid

# Show MIDI info
pmetal info /data/input/flat.mid
```

## Configuration

Default config is at `config/default.yaml`. Key parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `matching_window_ticks` | 120 | Time window for note matching (60–240) |
| `pitch_tolerance` | 4 | Semitone tolerance for matching (1–12) |
| `velocity_boost` | 1.2 | Multiplier for expressive velocities |
| `velocity_min` | 30 | Minimum output velocity |
| `quantize_division` | 16 | Grid resolution (4=quarter, 16=sixteenth) |
| `humanize_max_ticks` | 20 | Max micro-timing offset preserved |
| `smoothing_window` | 5 | Pitch bend smoothing window size |

## Processing Tips

1. **Low match rate (<50%)**:
   - Check that both files are from the same song/section
   - Try increasing `matching_window_ticks` to 180–240
   - Increase `pitch_tolerance` to 5–6 if Neural Note has octave errors

2. **Flat velocities**:
   - Increase `velocity_boost` to 1.3–1.5
   - Lower `velocity_min` to 20

3. **Glitchy pitch bends**:
   - Increase `smoothing_window` to 7–9
   - Lower `redundancy_threshold` to 60

4. **Timing feels off**:
   - Lower `humanize_max_ticks` to 10 for tighter grid
   - Try `quantize_division=8` for eighth-note grid
