"""Inputs the suggestion engine needs beyond prices: company fundamentals
from Yahoo Finance, cached for a day. A field Yahoo does not return stays
None and is reported as insufficient data, never filled in.

Yahoo refuses this lookup from some hosting providers. A snapshot saved in the
repository stands in then, and the answer says which date it is from.

Refresh the snapshot with:  uv run python -m app.advisor.data
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import yfinance as yf

CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "fundamentals.json"
SNAPSHOT_FILE = CACHE_FILE.parent.parent / "trained" / "fundamentals_snapshot.json"
TTL_SECONDS = 24 * 3600
RETRY_AFTER_SECONDS = 1800   # after a round in which the live source gave nothing, wait this long before asking again
LOOKUP_SECONDS = 12
LOOKUP_WORKERS = 4   # each lookup holds a large response in memory while it is parsed
FIELDS = {"market_cap": "marketCap", "average_volume": "averageVolume", "roe": "returnOnEquity",
          "margin": "profitMargins", "debt_to_equity": "debtToEquity", "pe": "trailingPE"}

_lock = threading.Lock()
_cache: dict[str, dict] | None = None
_snapshot: dict | None = None
_state = {"blocked_until": 0.0, "snapshot_used": False}


def _load() -> dict[str, dict]:
    global _cache
    with _lock:
        if _cache is None:
            try:
                _cache = json.loads(CACHE_FILE.read_text())
            except (OSError, ValueError):
                _cache = {}
        return _cache


def _lookup(ticker: str) -> None:
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return
    row = {name: info.get(key) for name, key in FIELDS.items()}
    row = {name: float(value) if isinstance(value, (int, float)) else None for name, value in row.items()}
    if any(value is not None for value in row.values()):
        with _lock:
            _cache[ticker] = {**row, "fetched_at": time.time()}


def snapshot() -> dict:
    """The saved fundamentals: {"saved_on": date, "tickers": {...}}. Empty when there is no file."""
    global _snapshot
    if _snapshot is None:
        try:
            _snapshot = json.loads(SNAPSHOT_FILE.read_text())
        except (OSError, ValueError):
            _snapshot = {"saved_on": None, "tickers": {}}
    return _snapshot


def fundamentals(tickers: list[str]) -> dict[str, dict]:
    """Per ticker: market cap, average volume, return on equity, profit margin,
    debt to equity and trailing P/E. A ticker neither Yahoo nor the saved
    snapshot knows is absent."""
    cache = _load()
    now = time.time()
    due = [t for t in dict.fromkeys(tickers) if now - cache.get(t, {}).get("fetched_at", 0) > TTL_SECONDS]
    if due and now >= _state["blocked_until"]:
        before = len(cache)
        pool = ThreadPoolExecutor(max_workers=min(LOOKUP_WORKERS, len(due)))
        wait([pool.submit(_lookup, ticker) for ticker in due], timeout=LOOKUP_SECONDS)
        pool.shutdown(wait=False)
        if len(cache) == before and not any(now - cache.get(t, {}).get("fetched_at", 0) <= TTL_SECONDS for t in due):
            _state["blocked_until"] = now + RETRY_AFTER_SECONDS   # the source is refusing us; do not wait on it again yet
        with _lock:
            try:
                CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                CACHE_FILE.write_text(json.dumps(cache))
            except OSError:
                pass
    saved = snapshot()["tickers"]
    out = {}
    for ticker in tickers:
        if ticker in cache:
            out[ticker] = cache[ticker]
        elif ticker in saved:
            out[ticker] = {**saved[ticker], "snapshot": True}
            _state["snapshot_used"] = True
    return out


def source_note(facts: dict[str, dict]) -> str | None:
    """Says so when any of these fundamentals came from the saved snapshot."""
    used = sum(1 for fact in facts.values() if fact.get("snapshot"))
    if not used:
        return None
    return (f"Company fundamentals for {used} stock(s) are a snapshot saved on {snapshot()['saved_on']}; "
            "the live source did not answer.")


def save_snapshot(tickers: list[str]) -> dict:
    """Look every ticker up now and save what is known, for hosts the source refuses."""
    from datetime import date

    for start in range(0, len(tickers), 8):
        _state["blocked_until"] = 0.0
        fundamentals(tickers[start:start + 8])
    cache = _load()
    rows = {t: {k: v for k, v in cache[t].items() if k != "fetched_at"} for t in tickers if t in cache}
    out = {"saved_on": date.today().isoformat(), "source": "Yahoo Finance", "tickers": rows}
    SNAPSHOT_FILE.write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    from app.advisor.suggest import sector_universe

    universe = sorted({ticker for group in sector_universe().values() for ticker in group})
    saved = save_snapshot(universe)
    complete = [t for t, row in saved["tickers"].items() if row.get("market_cap") and row.get("average_volume")]
    print(f"{len(complete)} of {len(universe)} stocks saved with size and volume; "
          f"missing: {sorted(set(universe) - set(complete))}")
