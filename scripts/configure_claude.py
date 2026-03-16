#!/usr/bin/env python3
"""Configure Claude Desktop to use the pmetal-midi MCP server via HTTP (mcp-remote bridge)."""

import json
import sys
from pathlib import Path

CLAUDE_CONFIG_PATH = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"

ALMA_IP = "192.168.50.103"
MCP_PORT = 8200

MCP_SERVER_CONFIG = {
    "command": "npx",
    "args": [
        "-y",
        "mcp-remote",
        f"http://{ALMA_IP}:{MCP_PORT}/mcp",
        "--allow-http",
    ],
}


def main():
    if not CLAUDE_CONFIG_PATH.exists():
        print(f"Claude Desktop config not found at {CLAUDE_CONFIG_PATH}")
        sys.exit(1)

    config = json.loads(CLAUDE_CONFIG_PATH.read_text())

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    existing = config["mcpServers"].get("pmetal-midi")
    if existing:
        print(f"Existing pmetal-midi config:\n{json.dumps(existing, indent=2)}")
        answer = input("Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    config["mcpServers"]["pmetal-midi"] = MCP_SERVER_CONFIG
    CLAUDE_CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    print(f"Updated: {CLAUDE_CONFIG_PATH}")
    print(f"Transport: HTTP via mcp-remote → http://{ALMA_IP}:{MCP_PORT}/mcp")
    print("\nRestart Claude Desktop (Cmd+Q then reopen) to apply.")


if __name__ == "__main__":
    main()
