#!/bin/bash
set -e

echo "=== pmetal-midi container starting ==="
echo "Time: $(date -Iseconds)"
echo "Python: $(python --version)"
echo "Data dir: /data"
echo "Contents:"
ls -la /data/ 2>/dev/null || echo "  (empty)"
echo "========================================="

# Start the lightweight HTTP status server in background
python -m pmetal.status_server &
STATUS_PID=$!
echo "Status server started (PID $STATUS_PID) on port 8100"

# MCP server runs on-demand via SSH stdio (invoked by Claude Desktop per session)
# Also start HTTP MCP for optional direct access on port 8200
export FASTMCP_PORT=8200
export FASTMCP_HOST=0.0.0.0
MCP_TRANSPORT=streamable-http python -m pmetal.mcp_server &
MCP_HTTP_PID=$!
echo "MCP HTTP server started (PID $MCP_HTTP_PID) on port 8200"

# Keep container alive — wait for the status server
wait $STATUS_PID
