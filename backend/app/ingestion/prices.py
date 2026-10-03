import math
import time
from datetime import datetime, timezone

import yfinance as yf

CACHE_TTL_SECONDS = 60

_cache: dict[str, object] = {"key": None, "at": 0.0, "value": None}


def get_latest_prices(tickers: list[str]) -> tuple[dict[str, float], str | None]:
    """Latest close per ticker and the fetch time (ISO, UTC).

    Tickers whose price could not be fetched are absent from the dict; callers
    must treat a missing price as unknown rather than substituting a value.
    """
    key = ",".join(sorted(tickers))
    if _cache["key"] == key and time.time() - _cache["at"] < CACHE_TTL_SECONDS:
        return _cache["value"]

    prices: dict[str, float] = {}
    try:
        closes = yf.download(
            tickers, period="5d", progress=False, auto_adjust=True, timeout=8
        )["Close"]
        latest = closes.ffill().iloc[-1]
        for ticker in tickers:
            value = float(latest.get(ticker, math.nan))
            if not math.isnan(value):
                prices[ticker] = round(value, 2)
    except Exception:
        prices = {}

    as_of = datetime.now(timezone.utc).isoformat() if prices else None
    result = (prices, as_of)
    if prices:
        _cache.update(key=key, at=time.time(), value=result)
    return result
