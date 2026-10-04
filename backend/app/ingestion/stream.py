"""Continuous ingestion. Every few minutes the market and sector headlines and
the weather outlook are fetched, scored, embedded and indexed, whether or not
anyone is asking a question. Each batch is timed from the moment the data
arrives to the moment it is searchable, so the latency shown on the terminal
is measured, not assumed."""

import threading
import time
from collections import deque
from datetime import datetime, timezone

from app.ingestion.news import SECTOR_TOPICS, get_news
from app.tools import sentiment, vector_store
from app.tools.weather import get_weather_outlook

CYCLE_SECONDS = 300
TOPICS_PER_CYCLE = 4            # sector searches per cycle; all sectors are covered in turn
HEADLINES_PER_QUERY = 15
MARKET_QUERY = "Nifty Sensex stock market when:1d"
WEATHER_EVERY_SECONDS = 1800
BATCHES_KEPT = 200
INDEX_WAIT_SECONDS = 60

_lock = threading.Lock()
_batches: deque = deque(maxlen=BATCHES_KEPT)   # milliseconds from arrival to indexed, per batch
_state: dict = {"started": False, "cycles": 0, "last_cycle_at": None, "headlines": 0, "weather_records": 0,
                "weather_at": 0.0, "error": None}


def ingest_news(query: str) -> dict | None:
    """Fetch one search, then score and index what is new. Returns the batch's timings."""
    items = get_news(query, HEADLINES_PER_QUERY)
    fresh = vector_store.new_items(items)
    if not fresh:
        return None
    arrived = time.perf_counter()
    sentiment.score_many([item["title"] for item in fresh])
    scored_ms = round((time.perf_counter() - arrived) * 1000)
    vector_store.upsert_news(fresh)
    total_ms = round((time.perf_counter() - arrived) * 1000)
    batch = {"items": len(fresh), "score_ms": scored_ms, "total_ms": total_ms}
    with _lock:
        _batches.append(total_ms)
        _state["headlines"] += len(fresh)
    return batch


def weather_records(outlook: list[dict]) -> list[dict]:
    """One searchable sentence per site and forecast day."""
    records = []
    for site in outlook:
        for day in site.get("days", []):
            if day.get("date") is None:
                continue
            parts = [f"{label} {value} {unit}" for label, value, unit in (
                ("rain", day.get("rain_mm"), "mm"), ("gusts", day.get("gust_kmh"), "km/h"),
                ("maximum temperature", day.get("temp_c"), "C")) if value is not None]
            records.append({
                "_id": f'weather-{site["name"]}-{day["date"]}'.replace(" ", "-").lower(),
                vector_store.TEXT_FIELD: f'Weather forecast for {site["name"]} on {day["date"]}: ' + ", ".join(parts),
                "site": site["name"], "date": day["date"],
            })
    return records


def ingest_weather() -> None:
    if time.time() - _state["weather_at"] < WEATHER_EVERY_SECONDS:
        return
    outlook = get_weather_outlook()
    if not outlook:
        return
    arrived = time.perf_counter()
    records = weather_records(outlook)
    if vector_store.upsert_weather(records) is not None:
        with _lock:
            _batches.append(round((time.perf_counter() - arrived) * 1000))
            _state.update(weather_records=len(records), weather_at=time.time())


def run_cycle(cycle: int) -> None:
    topics = list(SECTOR_TOPICS)
    chosen = [topics[(cycle * TOPICS_PER_CYCLE + i) % len(topics)] for i in range(TOPICS_PER_CYCLE)]
    for query in [MARKET_QUERY, *(f"{SECTOR_TOPICS[topic][1]} India when:2d" for topic in chosen)]:
        ingest_news(query)
    ingest_weather()
    with _lock:
        _state.update(cycles=cycle + 1, last_cycle_at=datetime.now(timezone.utc).isoformat(), error=None)


def _run_forever() -> None:
    waited = 0
    while vector_store.enabled() and not vector_store.status()["backend"] == vector_store.PINECONE \
            and waited < INDEX_WAIT_SECONDS:
        time.sleep(2)    # the index is still connecting; headlines sent now would only be held back
        waited += 2
    cycle = 0
    while True:
        try:
            run_cycle(cycle)
        except Exception as exc:  # one bad cycle must not end the stream
            _state["error"] = f"{type(exc).__name__}: {exc}"
        cycle += 1
        time.sleep(CYCLE_SECONDS)


def start_in_background() -> None:
    with _lock:
        if _state["started"]:
            return
        _state["started"] = True
    threading.Thread(target=_run_forever, daemon=True, name="ingestion").start()


def status() -> dict:
    with _lock:
        return {**{k: _state[k] for k in ("started", "cycles", "last_cycle_at", "headlines", "weather_records", "error")},
                "every_seconds": CYCLE_SECONDS, "arrival_to_indexed_ms": vector_store.percentiles(_batches)}
