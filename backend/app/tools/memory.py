"""Keeps the server inside its memory limit.

A small host kills the process when it goes over, and every request in flight
then fails. Three things prevent that: heavy computations run one at a time,
memory they free is handed back to the system when they finish, and when the
process is still close to the limit the sentiment model is unloaded, because
headlines can be scored with the lexicon until there is room again."""

import ctypes
import gc
import os
import threading
from contextlib import contextmanager
from pathlib import Path

M_ARENA_MAX = -8          # glibc mallopt parameter
MAX_ARENAS = 2
TIGHT_SHARE = 0.80        # above this share of the limit, shed the sentiment model
HEAVY_WAIT_SECONDS = 90   # a queued computation proceeds anyway after this long
RENDER_FREE_MB = 512
CGROUP_FILES = ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes")
UNLIMITED_MB = 1 << 20    # a control-group "limit" this large means no limit

_heavy = threading.Lock()


def _libc():
    try:
        return ctypes.CDLL("libc.so.6")
    except OSError:  # not Linux with glibc: nothing to tune
        return None


def cap_arenas() -> None:
    """glibc gives each thread its own heap arena and none of them shrinks, so
    a threaded server's memory only grows. Two arenas are enough here."""
    libc = _libc()
    if libc is not None:
        try:
            libc.mallopt(M_ARENA_MAX, MAX_ARENAS)
        except (AttributeError, OSError):
            pass


def trim() -> None:
    """Collect garbage and return freed heap pages to the system."""
    gc.collect()
    libc = _libc()
    if libc is not None:
        try:
            libc.malloc_trim(0)
        except (AttributeError, OSError):
            pass


def used_mb() -> float | None:
    """Resident memory of this process. None where /proc is not available."""
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, IndexError):
        return None


def limit_mb() -> float | None:
    """The memory this process may use: MEMORY_LIMIT_MB, else the container's
    limit, else the free plan's size when running on Render."""
    configured = os.getenv("MEMORY_LIMIT_MB")
    if configured:
        try:
            return float(configured)
        except ValueError:
            pass
    for name in CGROUP_FILES:
        try:
            limit = int(Path(name).read_text().strip()) / 1e6
        except (OSError, ValueError):
            continue
        if limit < UNLIMITED_MB:
            return limit
    return RENDER_FREE_MB if os.getenv("RENDER") else None


def room_for(megabytes: float) -> bool:
    """Whether this much more can be allocated and still stay under the tight mark."""
    used, limit = used_mb(), limit_mb()
    return used is None or limit is None or used + megabytes <= limit * TIGHT_SHARE


def relieve() -> None:
    """Hand back what can be handed back; if that is not enough, unload the sentiment model."""
    trim()
    if not room_for(0):
        from app.tools import sentiment

        sentiment.shed()
        trim()


@contextmanager
def heavy():
    """Run one memory-hungry computation at a time, and tidy up after it."""
    held = _heavy.acquire(timeout=HEAVY_WAIT_SECONDS)
    try:
        relieve()
        yield
    finally:
        trim()
        if held:
            _heavy.release()


def status() -> dict:
    used, limit = used_mb(), limit_mb()
    return {"used_mb": None if used is None else round(used), "limit_mb": None if limit is None else round(limit)}
