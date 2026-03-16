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
| **Transcription** | Converting audio (WAV) to MIDI. Done by Klang.io, Neural Note, or Basic Pitch. |
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

Click it to see all 10 tools:
- `upload_file` — upload a file from chat to the server
- `download_file` — download a file from server to chat
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

## Part 4. Step-by-Step Workflow

### Overview

```
Suno  →  WAV stems  →  Transcription  →  Guitar Pro 8  →  Flat MIDI
                                                              ↓
Neural Note  →  Expressive MIDI  ──────────────────→  pmetal-midi
                                                              ↓
                                                        Hybrid MIDI
                                                              ↓
                                                       DAW + Shreddage
```

### Step 1. Prepare Your Files

For each song you need two files:

1. **Flat MIDI** (`song_flat.mid`):
   - Download the song from Suno
   - Transcribe via Klang.io or Basic Pitch
   - Open in Guitar Pro 8
   - Clean up notes, fix errors
   - Export as MIDI

2. **Expressive MIDI** (`song_neural.mid`):
   - Take the guitar WAV stem from Suno
   - Run through Neural Note or audio-to-midi
   - Get MIDI with real dynamics

### Step 2. Upload Files via Chat

Drag and drop both MIDI files directly into the Claude Desktop chat window,
or click the **+ button** → "Add files or photos".

Then type:

> Here are two files: song_flat.mid (flat MIDI from Guitar Pro) and
> song_neural.mid (expressive from Neural Note). Upload them to the server
> and show me the info.

Claude will automatically:
1. Call `upload_file` for each file
2. Call `midi_info` for analysis
3. Show the track structure

### Step 3. Run the Merge

Type:

> Merge song_flat.mid and song_neural.mid

Claude will call `merge_midi` and show a detailed log:

```
[INIT] Loading configuration
[ANALYZE] Validating input files
  song_flat.mid (15.2 KB)
  song_neural.mid (8.4 KB)
[MERGE] Starting merge
  Track 6 (Solo Guitar): 342 notes
    Matched 267 / 342 (78.1%)
    Velocity range: 34–127
    Pitch bends: 183 → 96 (smoothed)
[QUALITY] Score 0.79 — PASS
Output: /data/output/song_hybrid.mid
```

### Step 4. Evaluate the Result

Ask Claude:

> Analyze the quality of the output file

Claude will show a detailed report:
```
  [+] density: 0.85
  [+] pitch_bend_continuity: 0.91
  [+] velocity_range: 0.78
  [+] timing_consistency: 0.82
  [-] match_rate: 0.62

  Overall: 0.79 — PASS
```

If the result is poor (<0.70), Claude will suggest corrections:
```
  1. Widen matching window → 180 ticks
  2. Increase pitch tolerance → 5
```

### Step 5. Retry with Different Parameters (if needed)

> Retry the merge with matching_window_ticks=180 and velocity_boost=1.3

Claude will re-run merge_midi with the new parameters.

### Step 6. Download the Result

Type:

> Download the result

Claude will call `download_file` and deliver `song_hybrid.mid` right in the chat.
Open it in your DAW.

---

## Part 5. Chat Examples

### Basic Commands

| What to say | What happens |
|-------------|--------------|
| (attach file) "Upload this as song_flat.mid" | Calls `upload_file` — saves file on server |
| "Download the result song_hybrid.mid" | Calls `download_file` — delivers file in chat |
| "Show server status" | Calls `get_status` — version, CPU, RAM, file counts |
| "Show files" | Calls `list_files` — input and output file listing |
| "Show info for song_flat.mid" | Calls `midi_info` — tracks, note counts, TPB |
| "Merge flat.mid and neural.mid" | Calls `merge_midi` — full pipeline with auto-retry |
| "Process all files in /data/input" | Calls `run_workflow` — batch processing of all pairs |
| "Process only tracks 3 and 5" | Calls `merge_midi` with target_tracks=[3, 5] |
| "Analyze quality of output.mid" | Calls `analyze_quality` — 5 metrics + suggestions |
| "Analyze audio guitar.wav" | Calls `analyze_audio` — tempo, transients, spectrum |
| "Show processing log" | Calls `get_processing_log` — recent log lines |

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

### Audio Analysis

If you have a guitar WAV stem from Suno, upload it and say:
"Analyze audio guitar.wav"

The system detects:
- **Tempo** (BPM) — automatic or manually specified
- **Transients** (note attacks) — for velocity correction
- **Spectral peaks** — for pitch bend validation
- **Beats** — for timing verification

These features are then used for **audio-guided velocity** during merge.

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
