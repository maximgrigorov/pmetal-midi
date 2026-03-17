# pmetal-midi — Power Metal MIDI Hybridization System

Merges **flat MIDI** (from Guitar Pro 8, correct notes, no dynamics) with
**expressive MIDI** (from Neural Note, real dynamics but imprecise notes) to
produce **hybrid MIDI** ready for virtual instruments like Shreddage 3.5 Hydra
and Darkwall.

Designed for power metal ballads created in [Suno](https://suno.com).

## Strict Workflow

```
1. Suno → Request stems (guitar only, NOT full MP3)
2. Guitar stem → Neural Note (Monophonic, Electric Guitar, Medium sensitivity)
3. If solo+rhythm combined → eq_filter to isolate before Neural Note
4. Neural output + Guitar Pro 8 manual transcription → *_flat.mid + *_neural.mid
5. If multi-track MIDI → extract_track to isolate target track
6. merge_midi(flat, neural) with strict config
7. Output → DAW + VST
```

**Klang.io is forbidden** — 60-70% accuracy with heavy artifacts. Use Neural Note only.

## Architecture

```
Suno  →  WAV stems  →  Neural Note  →  Expressive MIDI
                                              ↓
Guitar Pro 8 transcription  →  Flat MIDI  →  pmetal-midi
                                              ↓
                                         Hybrid MIDI
                                              ↓
                                        DAW + Shreddage
```

## Quick Start

```bash
# Deploy to alma server
make deploy

# Configure Claude Desktop (auto-patches config)
make configure-claude

# Restart Claude Desktop (Cmd+Q → reopen)
# Then chat: "Покажи статус сервера"
```

## Usage via Claude Desktop

Everything happens in the **Chat** tab.

- **MIDI files** (<1 MB): Drag & drop directly into chat. Claude uploads via `upload_file`.
- **WAV files** (10-100+ MB): Upload via `make upload SRC=./guitar.wav`, then process in chat.

| Say this | Tool called |
|----------|-------------|
| (attach MIDI) "Upload and merge these" | `upload_file` → `merge_midi` |
| "Apply solo guitar EQ to guitar_stem.wav" | `eq_filter` |
| "Extract track 3 from multitrack.mid" | `extract_track` |
| "Show server status" | `get_status` |
| "Analyze quality of output.mid" | `analyze_quality` |
| "Download the result" | `download_file` |

See [docs/GUIDE.md](docs/GUIDE.md) for the full English guide or
[docs/GUIDE_RU.md](docs/GUIDE_RU.md) for Russian.

## MCP Tools (12)

| Tool | Description |
|------|-------------|
| `upload_file` | Upload file from chat to server (base64) |
| `download_file` | Download file from server to chat (base64) |
| `eq_filter` | EQ pre-processing of WAV (solo/rhythm guitar isolation) |
| `extract_track` | Extract single track from multi-track MIDI |
| `merge_midi` | Merge flat + expressive MIDI |
| `analyze_quality` | Quality analysis (5 metrics + score) |
| `analyze_audio` | Audio analysis (tempo, transients, spectrum) |
| `run_workflow` | Batch-process all MIDI pairs in a directory |
| `midi_info` | MIDI file metadata (tracks, notes, TPB) |
| `list_files` | List input/output files on server |
| `get_status` | Server status (CPU, RAM, version) |
| `get_processing_log` | Recent processing log lines |

## Project Structure

```
src/pmetal/
  mcp_server.py      MCP tool definitions (HTTP + stdio)
  merger.py           Core MIDI merging (fuzzy matching, velocity transfer)
  analyzer.py         Audio analysis with librosa
  quality_analyzer.py Quality scoring and self-correction
  orchestrator.py     Workflow state machine with checkpointing
  config.py           Pydantic config models + YAML loading
  security.py         Path validation, rate limiting
  cli.py              CLI interface (pmetal merge/analyze/info/...)
  models.py           Data models (Note, PitchBend, MatchedPair)
  exceptions.py       Custom exception hierarchy
  status_server.py    Lightweight HTTP health endpoint
```

## Deployment

Runs in a container (podman/docker) on the `alma` server.
MCP server exposed via HTTP on port 8200.
Claude Desktop connects via `mcp-remote` (npm package).

```bash
make help       # all available commands
make deploy     # full deploy (sync → build → start → configure)
make redeploy   # update code and restart
make status     # container status and logs
make test-mcp   # verify MCP HTTP connectivity
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for detailed instructions.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/ tests/
```

## License

MIT
