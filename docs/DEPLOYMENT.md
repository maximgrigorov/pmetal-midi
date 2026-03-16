# Deployment Guide

## Prerequisites

- **Mac M1** with SSH access to `alma` (default key, user `mgrigorov`)
- **Alma server**: AlmaLinux 9.7, Podman 5.6.0
- **Claude Desktop** installed on Mac

## Quick Deploy

```bash
# Full deployment (sync + build + start + configure Claude Desktop)
make deploy
```

This will:
1. Sync project files to `alma:~/pmetal-midi`
2. Build the container image (~2-3 min on Brix)
3. Create data directories at `/home/mgrigorov/pmetal-data/`
4. Start the `pmetal-midi` container (port 8100 for status API)
5. Patch Claude Desktop config with the MCP server

**After deployment, restart Claude Desktop** to pick up the MCP config.

## Step-by-Step Deployment

### 1. Sync code to alma

```bash
make sync
```

### 2. Build container image

```bash
make build
```

Takes ~2-3 minutes. Uses `python:3.12-slim` as base with:
- librosa, scipy, numpy (audio analysis)
- mido (MIDI processing)
- mcp, fastmcp (MCP server)
- rich, click (CLI)

### 3. Create data directories

```bash
make init-data
```

Creates `/home/mgrigorov/pmetal-data/{input,output,config,logs,models}`.

### 4. Start container

```bash
make start
```

### 5. Configure Claude Desktop

```bash
make configure-claude
```

Or manually edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pmetal-midi": {
      "command": "ssh",
      "args": ["alma", "podman", "exec", "-i", "pmetal-midi", "python", "-m", "pmetal.mcp_server"]
    }
  }
}
```

## Operations

| Command | Description |
|---------|-------------|
| `make status` | Container status and recent logs |
| `make logs` | Follow container logs (Ctrl-C to stop) |
| `make shell` | Open bash inside the container |
| `make restart` | Restart the container |
| `make stop` | Stop the container |
| `make redeploy` | Sync + rebuild + restart (keeps data) |
| `make clean` | Remove container and image |

## Data Management

```bash
# Upload files to alma
make upload SRC=./my_song_flat.mid
make upload SRC=./my_song_neural.mid

# List files on alma
make ls-data

# Download results
make download FILE=output/my_song_hybrid.mid
```

## Port Usage on Alma

| Port | Service |
|------|---------|
| 8100 | pmetal-midi status API (internal) |
| 4000 | LiteLLM (already running) |
| 8000 | lyrics-engine (existing) |
| 7860 | audio-to-midi renderer (existing) |
| 8080 | midi-frontend (existing) |

## Troubleshooting

### Container won't start
```bash
# Check logs
make logs

# Check if port 8100 is already in use
ssh alma "ss -tlnp | grep 8100"
```

### MCP not connecting from Claude Desktop
```bash
# Test MCP manually
make test-mcp

# Verify SSH works
ssh alma "podman ps | grep pmetal-midi"

# Check Claude Desktop config
cat ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

### Rebuild after code changes
```bash
make redeploy
```
