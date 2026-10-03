"""News-volume signals from the GDELT Project: how much the world's press is
writing about a risk theme today compared with the past month.

GDELT allows one request every five seconds, so themes are scanned in the
background and the alert code only ever reads the cached result.
"""

import json
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "gdelt.json"
REQUEST_GAP_SECONDS = 12
SCAN_EVERY_SECONDS = 1800
RATE_LIMIT_BACKOFF_SECONDS = 600
STALE_AFTER_SECONDS = 6 * 3600
SPIKE_Z = 2.0

# Each theme maps to the kind of past event its impact is estimated from.
THEMES = {
    "india_pakistan": {
        "label": "India-Pakistan military tension",
        "query": "India Pakistan (military OR border OR strike OR ceasefire)",
        "event_type": "geopolitical",
    },
    "middle_east_oil": {
        "label": "Middle East conflict threatening oil supply",
        "query": '(Iran OR Hormuz OR "Red Sea") (oil OR tanker OR crude)',
        "event_type": "oil_up",
    },
    "trade_tariffs": {
        "label": "Tariffs on Indian exports",
        "query": "India (tariff OR tariffs) (exports OR trade) (Washington OR Trump)",
        "event_type": "geopolitical",
    },
    "india_china_border": {
        "label": "India-China border tension",
        "query": 'India China (border OR Ladakh OR "Line of Actual Control") (troops OR clash OR standoff)',
        "event_type": "geopolitical",
    },
    "cyclone_india": {
        "label": "Cyclone threatening the Indian coast",
        "query": "cyclone landfall (Gujarat OR Odisha OR Andhra OR Tamil OR Bengal OR Maharashtra)",
        "event_type": "cyclone",
    },
}

_lock = threading.Lock()
_state: dict = {"themes": {}, "scanned_at": None, "error": None}
_started = False


def daily_share(timeline: list[dict]) -> list[tuple[str, float, int]]:
    """(day, share of all monitored articles in permille, article count) per day."""
    return [
        (point["date"][:8], 1000 * point["value"] / point["norm"] if point.get("norm") else 0.0, point["value"])
        for point in timeline
    ]


def spike(days: list[tuple[str, float, int]]) -> dict | None:
    """Compare the latest full day with the days before it."""
    if len(days) < 10:
        return None
    *history, latest = days
    shares = [share for _, share, _ in history]
    spread = statistics.pstdev(shares)
    mean = statistics.mean(shares)
    z = (latest[1] - mean) / spread if spread else 0.0
    return {"day": latest[0], "articles": latest[2], "z": round(z, 2), "share_permille": round(latest[1], 3),
            "usual_share_permille": round(mean, 3), "elevated": z >= SPIKE_Z}


def _fetch(query: str) -> list[dict]:
    response = httpx.get(
        API_URL,
        params={"query": query, "mode": "timelinevolraw", "timespan": "30d", "format": "json"},
        headers={"User-Agent": "Mozilla/5.0 (portfolio-risk-monitor)"},
        timeout=40,
    )
    response.raise_for_status()
    return response.json()["timeline"][0]["data"]  # raises on the plain-text rate-limit reply


class RateLimited(Exception):
    """GDELT refused the request; the scan stops and tries again later."""


def _save() -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({k: _state[k] for k in ("themes", "scanned_at")}))
    except OSError:
        pass


def scan() -> dict:
    """Refresh each theme in turn, one request at a time. A theme that fails
    keeps its previous reading; a rate-limit reply ends the scan early."""
    for index, (key, theme) in enumerate(THEMES.items()):
        if index:
            time.sleep(REQUEST_GAP_SECONDS)
        try:
            result = spike(daily_share(_fetch(theme["query"])))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                with _lock:
                    _state["error"] = "rate limited by GDELT"
                raise RateLimited from exc
            with _lock:
                _state["error"] = f"{key}: HTTP {exc.response.status_code}"
            continue
        except Exception as exc:  # includes the plain-text rate-limit reply
            with _lock:
                _state["error"] = f"{key}: {type(exc).__name__}"
            continue
        if result:
            with _lock:
                _state["themes"][key] = {**result, "label": theme["label"], "event_type": theme["event_type"]}
                _state["scanned_at"] = datetime.now(timezone.utc).isoformat()
                _state["error"] = None
                _save()
    return _state


def _age_seconds() -> float | None:
    if not _state["scanned_at"]:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(_state["scanned_at"])).total_seconds()


def _run_forever() -> None:
    # A restart within the scan interval reuses the saved reading and waits its turn.
    age = _age_seconds()
    if age is not None and age < SCAN_EVERY_SECONDS:
        time.sleep(SCAN_EVERY_SECONDS - age)
    while True:
        try:
            scan()
            time.sleep(SCAN_EVERY_SECONDS)
        except RateLimited:
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
        except Exception:
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS)


def start_background_scan() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
        try:
            saved = json.loads(CACHE_FILE.read_text())
            _state.update(themes=saved.get("themes", {}), scanned_at=saved.get("scanned_at"))
        except (OSError, ValueError):
            pass
    threading.Thread(target=_run_forever, daemon=True).start()


def signals() -> dict:
    """The latest scan: {themes: {key: {...}}, scanned_at, stale}."""
    with _lock:
        age = _age_seconds()
        return {"themes": dict(_state["themes"]), "scanned_at": _state["scanned_at"],
                "stale": age is None or age > STALE_AFTER_SECONDS, "error": _state["error"]}
