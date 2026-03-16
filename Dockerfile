FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src/ /app/src/

# Install project with all MCP dependencies
RUN pip install --no-cache-dir -e ".[mcp]"

COPY config/ /app/config/
COPY scripts/ /app/scripts/
COPY docs/ /app/docs/

RUN chmod +x /app/scripts/*.sh

RUN mkdir -p /data/input /data/output /data/config /data/logs /data/models

# Verify critical imports
RUN python -c "import mido, numpy, scipy, librosa, fastmcp, mcp, psutil, click, rich; print('All imports OK')"

EXPOSE 8100 8200

COPY scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
