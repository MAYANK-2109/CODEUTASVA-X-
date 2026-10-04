"""Inputs the suggestion engine needs beyond prices: company fundamentals
from Yahoo Finance, cached for a day. A field Yahoo does not return stays
None and is reported as insufficient data, never filled in."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import yfinance as yf

CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "fundamentals.json"
TTL_SECONDS = 24 * 3600
LOOKUP_SECONDS = 12
FIELDS = {"market_cap": "marketCap", "average_volume": "averageVolume", "roe": "returnOnEquity",
          "margin": "profitMargins", "debt_to_equity": "debtToEquity", "pe": "trailingPE"}

_lock = threading.Lock()
_cache: dict[str, dict] | None = None


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


def fundamentals(tickers: list[str]) -> dict[str, dict]:
    """Per ticker: market cap, average volume, return on equity, profit margin,
    debt to equity and trailing P/E. A ticker Yahoo knows nothing about is absent."""
    cache = _load()
    now = time.time()
    due = [t for t in dict.fromkeys(tickers) if now - cache.get(t, {}).get("fetched_at", 0) > TTL_SECONDS]
    if due:
        pool = ThreadPoolExecutor(max_workers=min(8, len(due)))
        wait([pool.submit(_lookup, ticker) for ticker in due], timeout=LOOKUP_SECONDS)
        pool.shutdown(wait=False)
        with _lock:
            try:
                CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                CACHE_FILE.write_text(json.dumps(cache))
            except OSError:
                pass
    return {t: cache[t] for t in tickers if t in cache}
