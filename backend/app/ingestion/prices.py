import math
import time
from datetime import datetime, timezone

import yfinance as yf

from app.tools import health

FRESH_SECONDS = 60
# A quote this recent is still shown if a refresh fails, so one bad response
# from the data source does not blank a price that was known a minute ago.
STALE_OK_SECONDS = 900

_cache: dict[str, tuple[float, float]] = {}


def _download(tickers: list[str]) -> dict[str, float]:
    closes = yf.download(tickers, period="5d", progress=False, auto_adjust=True, timeout=8)["Close"]
    if not hasattr(closes, "columns"):
        closes = closes.to_frame(tickers[0])
    latest = closes.ffill().iloc[-1]
    prices = {}
    for ticker in tickers:
        value = float(latest.get(ticker, math.nan))
        if not math.isnan(value):
            prices[ticker] = round(value, 2)
    return prices


def get_latest_prices(tickers: list[str]) -> tuple[dict[str, float], str | None]:
    """Latest price per ticker and the fetch time (ISO, UTC).

    Tickers whose price could not be fetched are absent from the dict; callers
    must treat a missing price as unknown rather than substituting a value.
    """
    now = time.time()
    wanted = list(dict.fromkeys(tickers))
    due = [t for t in wanted if t not in _cache or now - _cache[t][0] >= FRESH_SECONDS]

    remaining = due
    started = time.perf_counter()
    for _ in range(2):  # a batch often comes back with a few tickers blank; ask once more
        if not remaining:
            break
        try:
            fetched = _download(remaining)
        except Exception:
            fetched = {}
        for ticker, price in fetched.items():
            _cache[ticker] = (now, price)
        remaining = [t for t in remaining if t not in fetched]
    if due:
        got = len(due) - len(remaining)
        health.record("quotes", got > 0, ms=(time.perf_counter() - started) * 1000, items=got,
                      detail=f"{got} of {len(due)} quotes")

    prices = {t: _cache[t][1] for t in wanted if t in _cache and now - _cache[t][0] < STALE_OK_SECONDS}
    as_of = None
    if prices:
        oldest = min(_cache[t][0] for t in prices)
        as_of = datetime.fromtimestamp(oldest, timezone.utc).isoformat()
    return prices, as_of
