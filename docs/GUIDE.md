# pmetal-midi User Guide

## Who Is This For

This guide is for musicians who create power metal ballads in Suno and want to
convert them into high-quality MIDI for virtual instruments (Shreddage 3.5 Hydra,
Darkwall, etc.). No programming or server knowledge required.

---

## Part 1. Glossary

### Audio & MIDI

| Term | Meaning |
|------|---------|
| **WAV** | Uncompressed audio format. Large files, full fidelity. |
| **Stems** | Individual audio tracks of a song: vocals, guitar, bass, drums. Suno exports these as WAV files. |
| **MIDI** | Digital note instructions. Contains no sound — only "play note C3 at velocity 80 for a quarter note". |
| **Flat MIDI** | "Flat" MIDI — all notes have uniform velocity, no pitch bend, no dynamics. Produced by transcription + cleanup in Guitar Pro 8. Notes are correct but sound robotic. |
| **Expressive MIDI** | MIDI from audio transcription (Neural Note or similar). Contains real dynamics, pitch bends, micro-timing. Notes may be inaccurate. |
| **Hybrid MIDI** | Our output: correct notes from flat MIDI + live dynamics from expressive MIDI. Sounds like a real musician. |
| **Velocity** | Note loudness/force in MIDI (1–127). 40 = soft, 100 = loud, 127 = maximum. |
| **Pitch Bend** | String bend. Smooth pitch change up or down. MIDI range: -8192 to +8191. |
| **Ticks per Beat (TPB)** | MIDI resolution. Standard is 480 ticks per quarter note. Higher = more timing precision. |
| **Track** | One instrument line in a MIDI file. E.g. "Solo Guitar", "Rhythm Guitar L", "Electric Bass". |

### Processing

| Term | Meaning |
|------|---------|
| **Transcription** | Converting audio (WAV) to MIDI. Use ONLY Neural Note (85-95% accuracy). Klang.io is forbidden (60-70% with artifacts). |
| **Fuzzy Matching** | Algorithm that pairs notes between flat and expressive MIDI, allowing small timing/pitch differences. |
| **Match Rate** | Percentage of flat MIDI notes matched to expressive MIDI. 78% = 78 of 100 notes paired. |
| **Quantization** | Snapping notes to a rhythmic grid (quarters, eighths, sixteenths). |
| **Humanization** | Small random deviations from the grid to sound more natural. |
| **Savitzky-Golay** | Mathematical filter for smoothing pitch bend curves. Removes sharp jumps while preserving shape. |
| **Quality Score** | Result rating from 0.0 to 1.0. Above 0.70 = good result. |
| **Self-correction** | Automatic re-processing with adjusted parameters when quality check fails. |

### Infrastructure

| Term | Meaning |
|------|---------|
| **Alma** | Server (Brix mini-PC) running the processing system. |
| **Container** | Isolated environment running pmetal-midi. Like a lightweight virtual machine. |
| **MCP (Model Context Protocol)** | Protocol allowing Claude Desktop to call external programs as "tools". Claude sends a command → server executes → result returns to chat. |
| **MCP Server** | Program that receives and executes commands from Claude Desktop. In our case — pmetal-midi on the alma server. |

---

## Part 2. Using Claude Desktop

### Use the Chat Tab

The **Chat** tab is where Claude can invoke MCP tools for MIDI processing.

| Tab | Purpose | Use for pmetal-midi? |
|-----|---------|---------------------|
| **Chat** | Conversation + tool invocation (MCP) | **Yes, use this** |
| **Cowork** | Background tasks in folders | No |
| **Code** | Code autocomplete in editor | No |

### Choosing a Model

Select the model in the bottom-right corner of the Chat window.

| Model | Cost | When to use |
|-------|------|-------------|
| **Haiku 4.5** | Cheapest | Routine operations: uploading files, running merges, viewing results |
| **Sonnet** | Medium | Analyzing results, tuning parameters, when Haiku struggles |
| **Opus** | Expensive | Not needed for this task |

**Recommendation**: Start with **Haiku 4.5** to save costs. It handles tool
invocation perfectly. Switch to Sonnet only if Haiku gives poor parameter advice.

---

## Part 3. Verifying the Integration

### Step 1. Restart Claude Desktop

After configuring the MCP server, **fully quit and reopen Claude Desktop**
(Cmd+Q, then reopen). Simply closing the window is not enough.

### Step 2. Open the Chat Tab

Switch to **Chat** if you're on another tab.

### Step 3. Check for Tools

In the chat window, near the input field, you should see a **hammer icon** or a
label showing the number of available tools (e.g. "10 tools").

Click it to see all 12 tools:
- `upload_file` — upload a file from chat to the server
- `download_file` — download a file from server to chat
- `eq_filter` — **EQ pre-processing of WAV** (solo/rhythm guitar isolation before Neural Note)
- `extract_track` — **extract single track** from multi-track MIDI
- `merge_midi` — merge MIDI files
- `analyze_quality` — quality analysis of MIDI output
- `analyze_audio` — audio analysis (tempo, transients, spectrum)
- `run_workflow` — batch-process all MIDI pairs in a directory
- `midi_info` — MIDI file information (tracks, notes, TPB)
- `list_files` — list files on the server
- `get_status` — server status (CPU, RAM, version)
- `get_processing_log` — recent processing log lines

If the icon is missing, MCP is not connected. Check:
1. Did you fully quit Claude Desktop (**Cmd+Q**, not just close the window)?
2. Is the container running? `make status` in the project folder
3. Is HTTP MCP reachable? `curl -s http://192.168.50.103:8200/mcp`
4. Is Node.js installed? `npx --version` (required for mcp-remote)

Claude Desktop config is located at:
`~/Library/Application Support/Claude/claude_desktop_config.json`

It should contain:
```json
{
  "mcpServers": {
    "pmetal-midi": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://192.168.50.103:8200/mcp", "--allow-http"]
    }
  }
}
```

The MCP server runs over HTTP (Streamable HTTP) on port 8200 of the alma server.
The `mcp-remote` npm package acts as a bridge: Claude Desktop launches it as a
stdio process, and it connects to the HTTP MCP server over the network. No SSH
needed for MCP. The connection is more stable than SSH — no idle timeouts.

To auto-configure:
```bash
make configure-claude
```

---

## Part 4. Strict Workflow

### Overview (No Variations)

```
1. Suno → Request stems (guitar only, NOT full MP3)
2. Guitar stem → Neural Note (Monophonic, Electric Guitar, Medium sensitivity)
3. If solo+rhythm combined → eq_filter to isolate BEFORE Neural Note
4. Neural output + Guitar Pro 8 manual transcription → *_flat.mid + *_neural.mid
5. If multi-track MIDI → extract_track to isolate target track
6. merge_midi(flat, neural) with strict config
7. Output → DAW + VST
```

### FORBIDDEN

| What | Why |
|------|-----|
| Full MP3 from Suno | Neural Note detects drum hits as pitch |
| Klang.io | 60-70% accuracy with artifacts (octave errors, double notes) |
| Stems without Pro/Plus | Suno requires Pro/Plus for stem export |

### Recommended Transcription Tools

| Tool | Accuracy | Polyphony | Bends | Status |
|------|----------|-----------|-------|--------|
| Neural Note | 85-95% | Mono | Excellent | **RECOMMENDED** |
| Klang.io | 60-70% | Poly | Rough | **FORBIDDEN** |
| Melodyne | 90-98% | Mono/poly | Excellent | Alternative (if budget allows) |

### Step 1. Prepare Your Files

For each song you need two files:

1. **Flat MIDI** (`song_flat.mid`):
   - Take guitar WAV stem from Suno (Pro/Plus)
   - Transcribe via Neural Note (Monophonic, Electric Guitar, Medium)
   - Open result in Guitar Pro 8
   - Clean up notes, fix errors
   - Export as MIDI

2. **Expressive MIDI** (`song_neural.mid`):
   - Same WAV stem → Neural Note
   - Save MIDI output directly (no editing)

### Step 1.5. EQ Pre-Processing (if solo and rhythm guitar in one stem)

If the stem mixes solo and rhythm guitar, run through EQ filter
**BEFORE** Neural Note transcription:

Upload WAV to server (`make upload SRC=./guitar_stem.wav`), then in chat:

> Apply solo_guitar filter to guitar_stem.wav

Available presets:
| Preset | Effect |
|--------|--------|
| `solo_guitar` | Highpass 800 Hz + boost 2-4 kHz (isolates lead guitar) |
| `rhythm_guitar` | Lowpass 2000 Hz + boost 200-500 Hz (isolates rhythm) |
| `bass` | Lowpass 800 Hz + boost 60-250 Hz (isolates bass guitar) |
| `custom` | All parameters user-specified |

Download the filtered WAV → run through Neural Note.

### Step 2. Upload Files

**MIDI files** (<1 MB): Drag & drop directly into Claude Desktop chat or
click **+ button** → "Add files or photos".

**WAV files** (10-100+ MB): Too large for chat. Upload via terminal:
```bash
make upload SRC=./guitar_stem.wav
```

Then in chat:
> Show files on the server

### Step 2.5. Multi-Track MIDI

If flat MIDI contains multiple tracks (multi-track export from Guitar Pro):

1. Call `midi_info` to see all tracks:
   > Show info for song_flat.mid

2. Extract the target track:
   > Extract track 3 from song_flat.mid

3. Use the extracted file for merge.

### Step 3. Run the Merge

> Merge song_flat.mid and song_neural.mid

For multi-track files, specify the target track:
> Merge song_flat.mid and song_neural.mid, process only track 3

### Step 4. Evaluate the Result

> Analyze the quality of the output file

If result < 0.70, Claude states **exact values** for retry (not "try"):
```
  match_rate < 0.5 → matching_window_ticks=200, pitch_tolerance=5
  velocity_range < 0.6 → velocity_boost=1.4
  pitch_bend_continuity < 0.6 → smoothing_window=15
```

### Step 5. Retry with Specified Parameters

> Retry the merge with matching_window_ticks=200 and velocity_boost=1.4

### Step 6. Download the Result

> Download the result

Claude calls `download_file` and delivers `song_hybrid.mid` in chat.

---

## Part 5. Chat Examples

### Basic Commands

| What to say | What happens |
|-------------|--------------|
| (attach MIDI) "Upload this as song_flat.mid" | `upload_file` — saves on server |
| "Download the result song_hybrid.mid" | `download_file` — delivers in chat |
| "Show server status" | `get_status` — version, CPU, RAM |
| "Show files" | `list_files` — file listing |
| "Show info for song_flat.mid" | `midi_info` — tracks, notes, TPB |
| "Merge flat.mid and neural.mid" | `merge_midi` — full pipeline |
| "Process only track 3" | `merge_midi` with target_tracks=[3] |
| "Analyze quality of output.mid" | `analyze_quality` — 5 metrics |
| "Analyze audio guitar.wav" | `analyze_audio` — tempo, transients |
| "Show processing log" | `get_processing_log` |

### EQ Filtering & Multi-Track

| What to say | What happens |
|-------------|--------------|
| "Apply solo_guitar filter to guitar_stem.wav" | `eq_filter` — highpass 800 Hz + boost 2-4 kHz |
| "Apply rhythm_guitar filter to guitar_stem.wav" | `eq_filter` — lowpass 2000 Hz + boost 200-500 Hz |
| "Apply bass filter to bass_stem.wav" | `eq_filter` — lowpass 800 Hz + boost 60-250 Hz |
| "What tracks are in song_flat.mid?" | `midi_info` — all tracks listed |
| "Extract track 3 from song_flat.mid" | `extract_track` — separate MIDI with one track |
| "Process all files in /data/input" | `run_workflow` — batch processing |

### Fine-Tuning

| What to say | What happens |
|-------------|--------------|
| "Match rate is low. Widen window to 200 ticks" | Re-merge with config_overrides |
| "Velocity is flat, boost dynamics" | Increases velocity_boost |
| "Pitch bend is jittery" | Increases smoothing window |
| "Quantize to eighth notes" | quantize_division=8 |

### Getting Help

| What to say | What happens |
|-------------|--------------|
| "What tracks are in the file?" | Shows midi_info with track list |
| "What does score 0.65 mean?" | Claude explains quality metrics |
| "Result is bad, what should I do?" | Claude suggests parameters for re-processing |

---

## Part 6. Troubleshooting

### Claude Doesn't See Tools (no hammer icon)

1. Fully quit Claude Desktop: **Cmd+Q**
2. Open a terminal:
   ```bash
   cd ~/cursor-eval/pmetal-midi
   make status
   ```
3. If the container is not running:
   ```bash
   make start
   ```
4. Test MCP connectivity:
   ```bash
   make test-mcp
   ```
5. Relaunch Claude Desktop

### Processing Failed

Ask Claude: "What went wrong?" — it will show the error log.

Common causes:
- File not found → check `make ls-data`
- Empty tracks → check midi_info
- No matches → files are from different songs or sections

### Low Match Rate (<30%)

- Make sure flat and expressive MIDI are from the same song
- Expressive MIDI should be a transcription of the same guitar part
- Try increasing matching_window_ticks to 200–240

### Container Crashed

```bash
make logs     # see what happened
make restart  # restart the container
```

---

## Part 7. System Capabilities

### Automatic Self-Correction Loop

During processing, the system automatically:
1. **Analyzes quality** across 5 metrics (note density, pitch bend, velocity, timing, match rate)
2. If score < 0.70, **auto-adjusts parameters** (widens matching window, boosts velocity, etc.)
3. Retries up to 3 times with new parameters
4. **FeedbackLoop** tracks progress: if score worsens, processing stops

### Batch Processing (run_workflow)

For multiple songs, place them all in `/data/input/` with suffixes:
- `song1_flat.mid` + `song1_neural.mid`
- `song2_flat.mid` + `song2_neural.mid`

Tell Claude: "Process all files in /data/input" — the system finds all pairs
and processes each one.

### EQ Filtering (eq_filter)

If solo and rhythm guitar are combined in one Suno stem, run the WAV through
an EQ filter **before** Neural Note transcription:

- **solo_guitar**: highpass 800 Hz + boost 2-4 kHz → isolates lead guitar
- **rhythm_guitar**: lowpass 2000 Hz + boost 200-500 Hz → isolates rhythm
- **bass**: lowpass 800 Hz + boost 60-250 Hz → isolates bass
- **custom**: all parameters user-specified

This improves Neural Note accuracy for transcription.

### Multi-Track MIDI (extract_track)

If the user provides a multi-track MIDI from Guitar Pro (multiple tracks):
1. `midi_info` shows all tracks with indices
2. `extract_track` extracts one track into a separate file
3. `merge_midi` with `target_tracks` processes only specified tracks

Each run processes **one track at a time**.

### Audio Analysis

If you have a guitar WAV stem from Suno, upload to server (`make upload SRC=./guitar.wav`)
and say in chat: "Analyze audio guitar.wav"

The system detects:
- **Tempo** (BPM) — automatic or manually specified
- **Transients** (note attacks) — for velocity correction
- **Spectral peaks** — for pitch bend validation
- **Beats** — for timing verification

### Security & Monitoring

- All file paths are validated: access restricted to `/data/*`
- Rate limiting: max 30 calls per minute
- Resource monitoring: CPU and RAM tracked via `get_status`

---

## Part 8. Administration

### Useful Make Commands

```bash
make help          # list all commands
make status        # container status
make logs          # real-time logs
make shell         # shell inside the container
make ls-data       # list all data files
make redeploy      # update code and restart
make clean         # remove container and image
```

### Updating After Code Changes

```bash
make redeploy
```

This runs sync → rebuild → restart. Data in `/data/` is preserved.

### CLI Commands (inside the container)

```bash
make shell
# Inside the container:
pmetal info /data/input/song_flat.mid
pmetal merge /data/input/flat.mid /data/input/neural.mid -o /data/output/ -v
pmetal analyze /data/output/song_hybrid.mid
pmetal extract /data/input/guitar.wav
pmetal config show
pmetal config validate /data/config/custom.yaml
```

| Command | Description |
|---------|-------------|
| `pmetal merge` | Merge flat + expressive MIDI with Rich progress bar |
| `pmetal analyze` | Quality analysis (metrics table) |
| `pmetal extract` | Audio analysis (tempo, transients, spectrum) |
| `pmetal info` | MIDI file information (tracks, notes) |
| `pmetal config show` | Show current configuration |
| `pmetal config validate` | Validate a YAML configuration file |

### LiteLLM on alma

LiteLLM runs on the alma server (port 4000) with these models:
- `claude-haiku` — cheap Claude requests
- `qwen3-coder-30b-q8` — local model via llama-server
- `gpt-5-mini` — Azure OpenAI
- `claude-sonnet-4.5` — Anthropic (for complex tasks)

To restart:
```bash
ssh alma "sudo systemctl restart litellm"
ssh alma "sudo systemctl status litellm"
```
