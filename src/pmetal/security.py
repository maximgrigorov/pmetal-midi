"""Security: path validation, rate limiting, resource management."""

from __future__ import annotations

import gc
import logging
import os
import time
import tracemalloc
from asyncio import Semaphore
from pathlib import Path

from .exceptions import SecurityError

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("PMETAL_DATA_DIR", "/data"))

ALLOWED_DIRECTORIES: list[Path] = [
    DATA_DIR,
    DATA_DIR / "input",
    DATA_DIR / "output",
    DATA_DIR / "config",
    DATA_DIR / "logs",
    DATA_DIR / "models",
]


def validate_path(path_str: str) -> Path:
    """
    Validate that *path_str* resolves inside an allowed directory.
    Prevents path-traversal attacks.
    """
    path = Path(path_str).resolve()

    for allowed in ALLOWED_DIRECTORIES:
        try:
            path.relative_to(allowed.resolve())
            return path
        except ValueError:
            continue

    raise SecurityError(
        f"Path '{path_str}' is outside allowed directories. "
        f"Allowed roots: {[str(d) for d in ALLOWED_DIRECTORIES]}"
    )


def sanitize_filename(name: str) -> str:
    """Strip dangerous characters from a filename."""
    import re
    safe = re.sub(r'[^\w\s\-.]', '', name)
    safe = safe.strip('. ')
    return safe or "unnamed"


class ResourceManager:
    """Track and limit memory usage."""

    MAX_MEMORY_MB = 400

    def __init__(self) -> None:
        tracemalloc.start()

    def check_memory(self) -> bool:
        current, _ = tracemalloc.get_traced_memory()
        current_mb = current / 1024 / 1024
        if current_mb > self.MAX_MEMORY_MB:
            gc.collect()
            return False
        return True

    def get_usage_mb(self) -> tuple[float, float]:
        current, peak = tracemalloc.get_traced_memory()
        return current / 1024 / 1024, peak / 1024 / 1024

    def cleanup(self) -> None:
        gc.collect()
        tracemalloc.stop()


class WorkflowLimiter:
    """Limit concurrent workflow executions on the Brix."""

    MAX_CONCURRENT = 2

    def __init__(self) -> None:
        self._semaphore = Semaphore(self.MAX_CONCURRENT)
        self._active = 0

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        self._active += 1

    def release(self) -> None:
        self._active -= 1
        self._semaphore.release()

    @property
    def available_slots(self) -> int:
        return self.MAX_CONCURRENT - self._active


class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, max_calls: int = 10, period_seconds: float = 60.0):
        self._max = max_calls
        self._period = period_seconds
        self._calls: list[float] = []

    def allow(self) -> bool:
        now = time.time()
        self._calls = [t for t in self._calls if now - t < self._period]
        if len(self._calls) >= self._max:
            return False
        self._calls.append(now)
        return True
