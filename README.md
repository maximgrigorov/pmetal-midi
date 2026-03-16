# pmetal-midi — Power Metal MIDI Hybridization System

Merges **flat MIDI** (from Guitar Pro 8, correct notes, no dynamics) with
**expressive MIDI** (from Neural Note / audio transcription, real dynamics but
imprecise notes) to produce **hybrid MIDI** ready for virtual instruments like
Shreddage 3.5 Hydra and Darkwall.

Designed for power metal ballads created in [Suno](https://suno.com).

## Architecture

```
Suno  →  WAV stems  →  Transcription  →  Guitar Pro 8  →  Flat MIDI
                                                              ↓
Neural Note  →  Expressive MIDI  ──────────────────→  pmetal-midi
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

Everything happens in the **Chat** tab. Attach MIDI files directly in the chat.

| Say this | Tool called |
|----------|-------------|
| (attach files) "Upload and merge these" | `upload_file` → `merge_midi` |
| "Show server status" | `get_status` |
| "List files" | `list_files` |
| "Analyze quality of output.mid" | `analyze_quality` |
| "Download the result" | `download_file` |

See [docs/GUIDE.md](docs/GUIDE.md) for the full English guide or
[docs/GUIDE_RU.md](docs/GUIDE_RU.md) for Russian.

## MCP Tools (10)

| Tool | Description |
|------|-------------|
| `upload_file` | Upload file from chat to server |
| `download_file` | Download file from server to chat |
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
