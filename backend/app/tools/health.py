"""When each live data source last answered and how long it took, so the
terminal can show the state of its feeds instead of assuming they are up."""

import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_streams: dict[str, dict] = {}


def record(name: str, ok: bool, *, detail: str = "", ms: float | None = None, items: int | None = None) -> None:
    """Note the outcome of one real request to a source (never a cache hit)."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        entry = _streams.setdefault(name, {"last_ok_at": None})
        entry.update(ok=ok, checked_at=now, detail=detail, items=items,
                     latency_ms=None if ms is None else round(ms))
        if ok:
            entry["last_ok_at"] = now


def snapshot() -> dict[str, dict]:
    with _lock:
        return {name: dict(entry) for name, entry in _streams.items()}
